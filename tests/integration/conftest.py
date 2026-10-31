"""Fixtures de integración: se omiten automáticamente si falta el servicio (no fallan)."""

import os
import socket
import uuid
from urllib.parse import urlparse

import pytest
from redis.asyncio import Redis

from app.config.settings import Settings
from app.rag.embeddings import HashEmbedder
from app.rag.ingestion import ingestar
from app.rag.store import MemoryVectorStore
from tests.conftest import RAIZ

REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379")
CHROMA_HOST = os.environ.get("TEST_CHROMA_HOST", "localhost")
CHROMA_PORT = int(os.environ.get("TEST_CHROMA_PORT", "8001"))


def _puerto_abierto(host: str, puerto: int) -> bool:
    try:
        with socket.create_connection((host, puerto), timeout=1):
            return True
    except OSError:
        return False


@pytest.fixture
async def redis_url() -> str:
    u = urlparse(REDIS_URL)
    if not _puerto_abierto(u.hostname or "localhost", u.port or 6379):
        pytest.skip(f"Redis no disponible en {REDIS_URL} (defina TEST_REDIS_URL)")
    r = Redis.from_url(REDIS_URL)
    try:
        modulos = {m[b"name"].lower() if isinstance(m, dict) else m[1].lower() for m in await r.module_list()}
    finally:
        await r.aclose()
    if not ({b"search", b"searchlight"} & modulos) or not ({b"rejson"} & modulos):
        pytest.skip("El Redis de pruebas no tiene RediSearch y RedisJSON (use Redis 8 o Redis Stack)")
    return REDIS_URL


@pytest.fixture
def thread_unico() -> str:
    return f"it-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def settings_redis(redis_url) -> Settings:
    return Settings(_env_file=None, llm_mode="deterministic", tracing_enabled=False, redis_url=redis_url)


@pytest.fixture
async def store_memoria() -> MemoryVectorStore:
    s = MemoryVectorStore()
    await ingestar(RAIZ / "knowledge", s, HashEmbedder(), 1200)
    return s


@pytest.fixture
def chroma_disponible() -> tuple[str, int]:
    if not _puerto_abierto(CHROMA_HOST, CHROMA_PORT):
        pytest.skip(f"Chroma no disponible en {CHROMA_HOST}:{CHROMA_PORT} (defina TEST_CHROMA_HOST/PORT)")
    return CHROMA_HOST, CHROMA_PORT
