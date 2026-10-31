"""Generación de embeddings. OpenAI en producción; hashing local solo para el modo determinista de pruebas."""

import hashlib
import math
import re
from typing import Protocol

from openai import AsyncOpenAI

from app.config.settings import Settings


class Embedder(Protocol):
    nombre: str

    async def embed(self, textos: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    def __init__(self, cliente: AsyncOpenAI, modelo: str):
        self._cliente = cliente
        self._modelo = modelo
        self.nombre = modelo.replace("/", "-")

    async def embed(self, textos: list[str]) -> list[list[float]]:
        if not textos:
            return []
        resp = await self._cliente.embeddings.create(model=self._modelo, input=textos)
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]


_TOKEN = re.compile(r"[a-záéíóúüñ0-9_]{2,}", re.IGNORECASE)


class HashEmbedder:
    """Embeddings léxicos deterministas (bolsa de palabras hasheada). No es semántico.

    Existe únicamente para poder ejecutar RAG y pruebas sin OpenAI. El nombre queda en el nombre de la
    colección para no mezclarlos con embeddings reales.
    """

    nombre = "hash256"
    dim = 256

    def _vector(self, texto: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in _TOKEN.findall(texto.lower()):
            h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    async def embed(self, textos: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in textos]


def crear_embedder(settings: Settings, cliente_openai: AsyncOpenAI | None = None) -> Embedder:
    if settings.usa_openai:
        cliente = cliente_openai or AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=settings.openai_timeout_seconds)
        return OpenAIEmbedder(cliente, settings.openai_embedding_model)
    return HashEmbedder()
