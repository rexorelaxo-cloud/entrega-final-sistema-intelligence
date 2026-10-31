"""Fixtures compartidas. Las pruebas unitarias usan checkpointer y vector store en memoria y el modo
determinista (sin OpenAI); las de integración se omiten si falta el servicio correspondiente."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.config.settings import Settings
from app.graph.builder import construir_grafo
from app.graph.state import Dependencias
from app.main import create_app
from app.rag.embeddings import HashEmbedder
from app.rag.ingestion import ingestar
from app.rag.retriever import Retriever
from app.rag.store import MemoryVectorStore

RAIZ = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def leer_fixture(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, llm_mode="deterministic", tracing_enabled=False)


@pytest.fixture
async def store(settings) -> MemoryVectorStore:
    s = MemoryVectorStore()
    await ingestar(RAIZ / "knowledge", s, HashEmbedder(), settings.rag_chunk_max_chars)
    return s


@pytest.fixture
def retriever(store, settings) -> Retriever:
    return Retriever(store, HashEmbedder(), settings.rag_top_k, settings.rag_min_score)


@pytest.fixture
def deps(settings, retriever) -> Dependencias:
    return Dependencias(settings=settings, retriever=retriever, llm=None)


@pytest.fixture
def grafo(deps):
    return construir_grafo(deps, InMemorySaver())


@pytest.fixture
async def cliente(settings, store) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings, checkpointer=InMemorySaver(), store=store, llm=None)
    async with app.router.lifespan_context(app):
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://test") as c:
            yield c


def archivos_multipart(*nombres: str) -> list[tuple[str, tuple[str, bytes, str]]]:
    return [("archivos", (n, (FIXTURES / n).read_bytes(), "application/json")) for n in nombres]
