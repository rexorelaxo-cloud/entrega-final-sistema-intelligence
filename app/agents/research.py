"""Research / RAG Agent: recupera de la base de conocimiento las reglas aplicables y sus fuentes.

No inventa reglas: solo se devuelven reglas presentes en lo recuperado. Con LLM, el modelo selecciona cuáles
aplican a la solicitud, pero el código descarta cualquier identificador que no haya sido recuperado.
"""

import json
import logging
from typing import Any

from app.graph.state import Dependencias, EstadoQA
from app.models.ejecucion import SeleccionReglas
from app.models.reglas import Aplicabilidad, FragmentoRecuperado, Verificacion
from app.models.solicitud import SolicitudAnalisis
from app.observability.tracing import span
from app.rag.retriever import a_regla, fuente_de
from app.services.llm import LLMError

log = logging.getLogger(__name__)


def _claves_de(datos: Any, profundidad: int = 2, prefijo: str = "") -> list[str]:
    if not isinstance(datos, dict) or profundidad == 0:
        return []
    claves: list[str] = []
    for k, v in datos.items():
        claves.append(f"{prefijo}{k}")
        claves.extend(_claves_de(v, profundidad - 1, f"{prefijo}{k}."))
    return claves


def construir_consultas(estado: EstadoQA) -> tuple[list[str], str | None]:
    """Devuelve las consultas de recuperación y el CMS por el que filtrar (si se conoce)."""
    consulta_usuario = estado.get("consulta") or ""
    if estado.get("tipo_turno") != "analisis":
        texto = consulta_usuario.lower()
        cms = (
            "sitecore"
            if "sitecore" in texto and "umbraco" not in texto
            else "umbraco"
            if "umbraco" in texto and "sitecore" not in texto
            else None
        )
        return [consulta_usuario], cms

    solicitud = SolicitudAnalisis.model_validate(estado.get("solicitud") or {})
    archivos = estado.get("archivos") or []
    claves: list[str] = []
    for a in archivos:
        try:
            claves.extend(_claves_de(json.loads(a["contenido"].lstrip("﻿"))))
        except (json.JSONDecodeError, ValueError):
            continue  # un JSON inválido no aporta claves; la sintaxis la reporta la herramienta
    unicas = sorted(set(claves))[:40]
    principal = (
        f"reglas de validación y requisitos de importación del CMS {solicitud.cms_destino or ''} "
        f"para contenido JSON con propiedades obligatorias, formatos y propiedades permitidas: {' '.join(unicas)} "
        f"{solicitud.instruccion or ''}"
    ).strip()
    consultas = [principal]
    if len(archivos) > 1:
        consultas.append(
            "consistencia estructural y de contenido entre idiomas: propiedades, tipos, marcadores de posición, "
            "etiquetas HTML, valores vacíos e idioma declarado en archivos traducidos"
        )
    return consultas, solicitud.cms_destino


async def _seleccionar_con_llm(deps: Dependencias, estado: EstadoQA, ids: list[str], resumen: str) -> list[str] | None:
    sistema = (
        "Eres el agente Research de un sistema de QA de localización. Recibes reglas documentadas ya recuperadas "
        "(solo identificadores y títulos) y la solicitud del usuario. Elige las reglas que aplican. "
        "Las reglas obligatorias se aplican siempre, aunque no las elijas; tu elección sirve para descartar opcionales. "
        "Si el usuario no indicó el CMS de destino, conserva las reglas de todos los CMS recuperados: "
        "esa ambigüedad se resuelve después. No inventes identificadores."
    )
    usuario = json.dumps(
        {
            "solicitud": estado.get("consulta"),
            "cms_destino": (estado.get("solicitud") or {}).get("cms_destino"),
            "reglas_recuperadas": resumen,
        },
        ensure_ascii=False,
    )
    try:
        seleccion = await deps.llm.estructurado(SeleccionReglas, sistema, usuario)  # type: ignore[union-attr]
    except LLMError as exc:
        log.warning("Selección por LLM no disponible, se conservan todas las reglas: %s", exc)
        return None
    validos = [i for i in seleccion.rule_ids if i in ids]  # descarta identificadores inventados
    return validos or None


async def nodo_research(estado: EstadoQA, deps: Dependencias) -> dict:
    with span("agente.research", "AGENT", **{"input.value": estado.get("consulta") or ""}) as sp:
        consultas, cms = construir_consultas(estado)
        # Sin CMS indicado en un análisis se recuperan las reglas de cada CMS de la base: así la decisión posterior
        # (deducir por estructura o preguntar) no depende de qué reglas rankearon primero en una sola búsqueda.
        filtros: list[str | None] = [cms]
        if cms is None and estado.get("tipo_turno") == "analisis":
            filtros = list(await deps.retriever.cms_disponibles()) or [None]
        recuperados: dict[str, FragmentoRecuperado] = {}
        for filtro in filtros:
            for consulta in consultas:
                for fr in await deps.retriever.buscar(consulta, cms=filtro):
                    previo = recuperados.get(fr.fragmento.chunk_id)
                    if previo is None or fr.score > previo.score:
                        recuperados[fr.fragmento.chunk_id] = fr

        # Garantía de completitud: las reglas del CMS en juego (y las genéricas) siempre se incluyen.
        if estado.get("tipo_turno") == "analisis" and filtros != [None]:
            en_juego = [c for c in filtros if c] + ["generico"]
            for fr in await deps.retriever.completar_reglas(en_juego):
                recuperados.setdefault(fr.fragmento.chunk_id, fr)

        reglas = [r for fr in recuperados.values() if (r := a_regla(fr)) is not None]
        guias = [
            {"fuente": fuente_de(fr).model_dump(), "texto": fr.fragmento.texto, "score": fr.score}
            for fr in recuperados.values()
            if fr.fragmento.metadatos.get("tipo") != "regla"
        ]
        reglas.sort(key=lambda r: (-r.score, r.rule_id))

        if deps.llm is not None and reglas:
            elegidas = await _seleccionar_con_llm(
                deps, estado, [r.rule_id for r in reglas], "; ".join(f"{r.rule_id}: {r.titulo} ({r.cms})" for r in reglas)
            )
            if elegidas:
                # El LLM solo puede descartar reglas opcionales o informativas. Una regla obligatoria nunca se
                # pierde por una decisión del modelo: hacerlo produciría falsos "APTO" (comprobaciones omitidas).
                reglas = [
                    r
                    for r in reglas
                    if r.rule_id in elegidas or (r.aplicabilidad == Aplicabilidad.OBLIGATORIA and r.verificacion != Verificacion.NINGUNA)
                ]

        sp.set_attribute("research.reglas", ",".join(r.rule_id for r in reglas))
        sp.set_attribute("research.guias", len(guias))

    return {
        "research_hecho": True,
        "reglas": [r.model_dump(mode="json") for r in reglas],
        "guias": guias,
        "pasos": [*estado.get("pasos", []), f"research({len(reglas)} reglas, {len(guias)} guías)"],
    }
