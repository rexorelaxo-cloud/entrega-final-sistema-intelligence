"""Ingestión: lectura de documentos Markdown, fragmentación, embeddings y almacenamiento.

Separada de la recuperación: se ejecuta con `python -m scripts.ingest`, no durante las consultas.

Formato de los documentos (ver knowledge/):
- Encabezado YAML simple (`clave: valor`) con doc_id, titulo, cms y origen.
- Una sección `## REGLA <ID> — <título>` por regla, con viñetas `- aplicabilidad:`, `- severidad:` y
  `- verificacion:`, un texto descriptivo y, opcionalmente, un bloque ```json-schema.
- Cualquier otra sección `## ...` se trata como guía informativa y se divide por tamaño.
Para ampliar la base basta con agregar archivos .md y volver a ejecutar la ingestión.
"""

import asyncio
import json
import re
from pathlib import Path

from app.models.reglas import Fragmento
from app.rag.embeddings import Embedder
from app.rag.store import VectorStore

_PATRON_REGLA = re.compile(r"^REGLA\s+([A-Z]+-\d+)\s*[—–-]\s*(.+)$")
_PATRON_BLOQUE_ESQUEMA = re.compile(r"```json-schema\s*\n(.*?)\n```", re.DOTALL)
_PATRON_VINETA = re.compile(r"^-\s*(aplicabilidad|severidad|verificacion):\s*(.+?)\s*$", re.MULTILINE)


class DocumentoInvalido(ValueError):
    pass


def _separar_encabezado(texto: str, ruta: Path) -> tuple[dict[str, str], str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", texto, re.DOTALL)
    if not m:
        raise DocumentoInvalido(f"{ruta}: falta el encabezado YAML (--- ... ---)")
    meta: dict[str, str] = {}
    for linea in m.group(1).splitlines():
        if ":" in linea:
            clave, valor = linea.split(":", 1)
            meta[clave.strip()] = valor.strip()
    for obligatoria in ("doc_id", "cms", "origen"):
        if obligatoria not in meta:
            raise DocumentoInvalido(f"{ruta}: el encabezado no define '{obligatoria}'")
    return meta, m.group(2)


def _secciones(cuerpo: str) -> list[tuple[str, str]]:
    """Divide el cuerpo por encabezados de nivel 2. El texto previo al primero se descarta."""
    partes = re.split(r"^##\s+(.+)$", cuerpo, flags=re.MULTILINE)
    return [(partes[i].strip(), partes[i + 1].strip()) for i in range(1, len(partes) - 1, 2)]


def _dividir_por_tamano(texto: str, maximo: int) -> list[str]:
    parrafos = [p.strip() for p in re.split(r"\n\s*\n", texto) if p.strip()]
    trozos: list[str] = []
    actual = ""
    for p in parrafos:
        if actual and len(actual) + len(p) + 2 > maximo:
            trozos.append(actual)
            actual = p
        else:
            actual = f"{actual}\n\n{p}" if actual else p
    if actual:
        trozos.append(actual)
    return trozos or [texto]


def fragmentar_documento(ruta: Path, raiz: Path, max_chars: int) -> list[Fragmento]:
    meta, cuerpo = _separar_encabezado(ruta.read_text(encoding="utf-8"), ruta)
    ruta_rel = ruta.relative_to(raiz).as_posix()
    base = {"doc_id": meta["doc_id"], "ruta": ruta_rel, "cms": meta["cms"], "origen": meta["origen"]}
    fragmentos: list[Fragmento] = []

    for titulo, contenido in _secciones(cuerpo):
        m_regla = _PATRON_REGLA.match(titulo)
        if m_regla:
            rule_id, nombre = m_regla.group(1), m_regla.group(2).strip()
            props = {k: v for k, v in _PATRON_VINETA.findall(contenido)}
            for obligatoria in ("aplicabilidad", "severidad", "verificacion"):
                if obligatoria not in props:
                    raise DocumentoInvalido(f"{ruta_rel}: la regla {rule_id} no define '{obligatoria}'")
            esquema = ""
            m_esq = _PATRON_BLOQUE_ESQUEMA.search(contenido)
            if m_esq:
                try:
                    esquema = json.dumps(json.loads(m_esq.group(1)), ensure_ascii=False, sort_keys=True)
                except json.JSONDecodeError as exc:
                    raise DocumentoInvalido(f"{ruta_rel}: el esquema de {rule_id} no es JSON válido: {exc}") from exc
            # una regla es atómica: no se divide aunque supere max_chars, para no separar el esquema
            fragmentos.append(
                Fragmento(
                    chunk_id=f"{meta['doc_id']}::{rule_id}",
                    texto=f"{titulo}\n\n{contenido}",
                    metadatos={
                        **base,
                        "tipo": "regla",
                        "seccion": titulo,
                        "rule_id": rule_id,
                        "titulo": nombre,
                        "aplicabilidad": props["aplicabilidad"],
                        "severidad": props["severidad"],
                        "verificacion": props["verificacion"],
                        "esquema_json": esquema,
                    },
                )
            )
        else:
            for i, trozo in enumerate(_dividir_por_tamano(contenido, max_chars)):
                slug = re.sub(r"[^a-z0-9]+", "-", titulo.lower()).strip("-")[:40]
                fragmentos.append(
                    Fragmento(
                        chunk_id=f"{meta['doc_id']}::{slug}::{i}",
                        texto=f"{titulo}\n\n{trozo}",
                        metadatos={**base, "tipo": "guia", "seccion": titulo},
                    )
                )
    return fragmentos


def cargar_fragmentos(directorio: Path, max_chars: int) -> list[Fragmento]:
    archivos = sorted(directorio.rglob("*.md"))
    if not archivos:
        raise DocumentoInvalido(f"No se encontraron documentos .md en {directorio}")
    fragmentos: list[Fragmento] = []
    for archivo in archivos:
        fragmentos.extend(fragmentar_documento(archivo, directorio, max_chars))
    ids = [f.chunk_id for f in fragmentos]
    if len(ids) != len(set(ids)):
        raise DocumentoInvalido("Hay identificadores de fragmento duplicados en la base de conocimiento")
    return fragmentos


async def ingestar(directorio: Path, store: VectorStore, embedder: Embedder, max_chars: int, lote: int = 64) -> int:
    """Lee, fragmenta, calcula embeddings y almacena. Devuelve la cantidad de fragmentos ingeridos."""
    # lectura de disco y parseo son síncronos: se hacen fuera del event loop
    fragmentos = await asyncio.to_thread(cargar_fragmentos, directorio, max_chars)
    for i in range(0, len(fragmentos), lote):
        parte = fragmentos[i : i + lote]
        embeddings = await embedder.embed([f.texto for f in parte])
        await store.upsert(parte, embeddings)
    return len(fragmentos)
