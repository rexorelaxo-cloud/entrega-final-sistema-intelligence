"""Almacenamiento vectorial. Chroma (servidor, cliente asíncrono) y un store en memoria para pruebas."""

import contextlib
import json
import math
from typing import Protocol

import chromadb

from app.config.settings import Settings
from app.models.reglas import Fragmento, FragmentoRecuperado


class VectorStore(Protocol):
    async def upsert(self, fragmentos: list[Fragmento], embeddings: list[list[float]]) -> None: ...

    async def buscar(self, embedding: list[float], k: int, filtro_cms: list[str] | None = None) -> list[FragmentoRecuperado]: ...

    async def contar(self) -> int: ...

    async def listar_cms(self) -> list[str]: ...

    async def obtener_reglas(self, cms: list[str]) -> list[Fragmento]: ...

    async def vaciar(self) -> None: ...

    async def ping(self) -> bool: ...


def _similitud_desde_distancia(distancia: float) -> float:
    """Chroma devuelve distancia coseno en [0, 2]; se convierte a similitud en [0, 1]."""
    return max(0.0, min(1.0, 1.0 - distancia))


class ChromaVectorStore:
    """Store sobre Chroma en modo servidor usando AsyncHttpClient (sin bloquear el event loop)."""

    def __init__(self, cliente: "chromadb.AsyncClientAPI", nombre_coleccion: str):
        self._cliente = cliente
        self._nombre = nombre_coleccion
        self._coleccion = None

    @classmethod
    async def conectar(cls, settings: Settings, sufijo_embedder: str) -> "ChromaVectorStore":
        cliente = await chromadb.AsyncHttpClient(host=settings.chroma_host, port=settings.chroma_port)
        return cls(cliente, f"{settings.chroma_collection}__{sufijo_embedder}")

    async def _col(self):
        if self._coleccion is None:
            self._coleccion = await self._cliente.get_or_create_collection(
                name=self._nombre, metadata={"hnsw:space": "cosine"}, embedding_function=None
            )
        return self._coleccion

    async def upsert(self, fragmentos: list[Fragmento], embeddings: list[list[float]]) -> None:
        if not fragmentos:
            return
        col = await self._col()
        await col.upsert(
            ids=[f.chunk_id for f in fragmentos],
            documents=[f.texto for f in fragmentos],
            metadatas=[f.metadatos for f in fragmentos],
            embeddings=embeddings,
        )

    async def buscar(self, embedding: list[float], k: int, filtro_cms: list[str] | None = None) -> list[FragmentoRecuperado]:
        col = await self._col()
        where = {"cms": {"$in": filtro_cms}} if filtro_cms else None
        res = await col.query(query_embeddings=[embedding], n_results=k, where=where)
        salida: list[FragmentoRecuperado] = []
        for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0], strict=True):
            salida.append(
                FragmentoRecuperado(
                    fragmento=Fragmento(chunk_id=cid, texto=doc, metadatos=meta),
                    score=_similitud_desde_distancia(dist),
                )
            )
        return salida

    async def contar(self) -> int:
        return await (await self._col()).count()

    async def listar_cms(self) -> list[str]:
        res = await (await self._col()).get(include=["metadatas"])
        return sorted({str(m["cms"]) for m in res["metadatas"] or [] if m and "cms" in m})

    async def obtener_reglas(self, cms: list[str]) -> list[Fragmento]:
        res = await (await self._col()).get(where={"$and": [{"tipo": "regla"}, {"cms": {"$in": cms}}]}, include=["documents", "metadatas"])
        return [Fragmento(chunk_id=i, texto=d, metadatos=m) for i, d, m in zip(res["ids"], res["documents"], res["metadatas"], strict=True)]

    async def vaciar(self) -> None:
        with contextlib.suppress(Exception):  # la colección puede no existir
            await self._cliente.delete_collection(self._nombre)
        self._coleccion = None

    async def ping(self) -> bool:
        await self._cliente.heartbeat()
        return True


class MemoryVectorStore:
    """Store en memoria con la misma interfaz. Se usa en pruebas unitarias."""

    def __init__(self) -> None:
        self._datos: dict[str, tuple[Fragmento, list[float]]] = {}

    async def upsert(self, fragmentos: list[Fragmento], embeddings: list[list[float]]) -> None:
        for f, e in zip(fragmentos, embeddings, strict=True):
            self._datos[f.chunk_id] = (f, e)

    async def buscar(self, embedding: list[float], k: int, filtro_cms: list[str] | None = None) -> list[FragmentoRecuperado]:
        def coseno(a: list[float], b: list[float]) -> float:
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(x * x for x in b)) or 1.0
            return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)

        candidatos = [
            FragmentoRecuperado(fragmento=f, score=max(0.0, min(1.0, coseno(embedding, e))))
            for f, e in self._datos.values()
            if not filtro_cms or f.metadatos.get("cms") in filtro_cms
        ]
        return sorted(candidatos, key=lambda r: r.score, reverse=True)[:k]

    async def contar(self) -> int:
        return len(self._datos)

    async def listar_cms(self) -> list[str]:
        return sorted({str(f.metadatos["cms"]) for f, _ in self._datos.values() if "cms" in f.metadatos})

    async def obtener_reglas(self, cms: list[str]) -> list[Fragmento]:
        return [f for f, _ in self._datos.values() if f.metadatos.get("tipo") == "regla" and f.metadatos.get("cms") in cms]

    async def vaciar(self) -> None:
        self._datos.clear()

    async def ping(self) -> bool:
        return True


def esquema_desde_metadatos(metadatos: dict) -> dict | None:
    bruto = metadatos.get("esquema_json")
    return json.loads(bruto) if isinstance(bruto, str) and bruto else None
