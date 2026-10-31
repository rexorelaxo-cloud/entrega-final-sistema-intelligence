"""Integración con Chroma real (cliente asíncrono sobre HTTP) y Redis real. Se omite si no están disponibles."""

import uuid

import httpx
import pytest

from app.main import create_app
from app.rag.embeddings import HashEmbedder
from app.rag.ingestion import ingestar
from app.rag.retriever import Retriever, a_regla
from app.rag.store import ChromaVectorStore
from tests.conftest import RAIZ, archivos_multipart

pytestmark = pytest.mark.stack


async def test_ingestion_y_recuperacion_sobre_chroma_real(settings, chroma_disponible):
    host, puerto = chroma_disponible
    cfg = settings.model_copy(update={"chroma_host": host, "chroma_port": puerto, "chroma_collection": f"test_{uuid.uuid4().hex[:8]}"})
    emb = HashEmbedder()
    store = await ChromaVectorStore.conectar(cfg, emb.nombre)
    try:
        n = await ingestar(RAIZ / "knowledge", store, emb, cfg.rag_chunk_max_chars)
        assert n == await store.contar() and n >= 12
        r = Retriever(store, emb, 12, 0.0)
        assert await r.cms_disponibles() == ["sitecore", "umbraco"]
        res = await r.buscar("propiedades obligatorias itemId language template fields", cms="sitecore", k=5)
        regla = next(a_regla(x) for x in res if a_regla(x))
        assert regla.cms in {"sitecore", "generico"} and regla.fuente.ruta.endswith(".md")
        todas = await r.completar_reglas(["umbraco"])
        assert {x.fragmento.metadatos["rule_id"] for x in todas} == {"UM-001", "UM-002", "UM-003", "UM-004"}
        assert await ingestar(RAIZ / "knowledge", store, emb, cfg.rag_chunk_max_chars) == n and await store.contar() == n  # idempotente
    finally:
        await store.vaciar()


@pytest.mark.redis
async def test_aplicacion_completa_con_redis_y_chroma_reales(redis_url, chroma_disponible, thread_unico):
    host, puerto = chroma_disponible
    cfg = __import__("app.config.settings", fromlist=["Settings"]).Settings(
        _env_file=None,
        llm_mode="deterministic",
        tracing_enabled=False,
        redis_url=redis_url,
        chroma_host=host,
        chroma_port=puerto,
        chroma_collection=f"test_{uuid.uuid4().hex[:8]}",
    )
    emb = HashEmbedder()
    store = await ChromaVectorStore.conectar(cfg, emb.nombre)
    await ingestar(RAIZ / "knowledge", store, emb, cfg.rag_chunk_max_chars)
    app = create_app(cfg, llm=None)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
                assert (await c.get("/health")).json()["estado"] == "ok"
                r = await c.post(
                    "/analyze",
                    files=archivos_multipart("sitecore_faltantes.json"),
                    data={"thread_id": thread_unico, "cms_destino": "sitecore", "aplicar_reglas_opcionales": "true"},
                )
                assert r.json()["informe"]["estado"] == "NO_APTO"
    finally:
        await store.vaciar()
