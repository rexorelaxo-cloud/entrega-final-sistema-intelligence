"""Aplicación FastAPI. `create_app` permite inyectar dependencias en pruebas; en producción todo se
construye desde la configuración (Redis, Chroma, OpenAI, Phoenix)."""

import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from fastapi import FastAPI
from langgraph.checkpoint.base import BaseCheckpointSaver
from redis.asyncio import Redis

from app.api.errores import registrar_manejadores
from app.api.recursos import Recursos
from app.api.rutas import router
from app.config.settings import Settings, get_settings
from app.graph.builder import construir_grafo
from app.graph.state import Dependencias
from app.observability.tracing import configurar_tracing
from app.persistence.checkpointer import checkpointer_redis
from app.rag.embeddings import crear_embedder
from app.rag.retriever import Retriever
from app.rag.store import ChromaVectorStore, VectorStore
from app.services.llm import LLMClient, crear_llm

log = logging.getLogger(__name__)
_SIN_ESPECIFICAR: Any = object()

DESCRIPCION = """
Sistema de control de calidad para contenido localizado: valida archivos JSON antes de importarlos a un CMS.

Combina validaciones deterministas (código Python), recuperación de reglas documentadas (RAG) y un grafo
multiagente de LangGraph con Supervisor, persistencia en Redis y Human-in-the-Loop.

Las reglas incluidas son **documentación de demostración con fines educativos**, no documentación oficial.
"""


def create_app(
    settings: Settings | None = None,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    store: VectorStore | None = None,
    llm: LLMClient | None = _SIN_ESPECIFICAR,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        async with AsyncExitStack() as stack:
            tracing = configurar_tracing(settings)

            saver = checkpointer
            redis: Redis | None = None
            if saver is None:
                saver = await stack.enter_async_context(checkpointer_redis(settings.redis_url))
                redis = Redis.from_url(settings.redis_url)
                stack.push_async_callback(redis.aclose)

            embedder = crear_embedder(settings)
            vector_store = store or await ChromaVectorStore.conectar(settings, embedder.nombre)
            retriever = Retriever(vector_store, embedder, settings.rag_top_k, settings.rag_min_score)
            cliente_llm = crear_llm(settings) if llm is _SIN_ESPECIFICAR else llm

            grafo = construir_grafo(Dependencias(settings=settings, retriever=retriever, llm=cliente_llm), saver)
            app.state.recursos = Recursos(settings=settings, grafo=grafo, store=vector_store, redis=redis, tracing_activo=tracing)
            log.info("Aplicación lista (LLM_MODE=%s)", settings.llm_mode)
            yield

    app = FastAPI(
        title="Localization QA Intelligence System",
        version="1.0.0",
        description=DESCRIPCION,
        lifespan=lifespan,
    )
    registrar_manejadores(app)
    app.include_router(router)
    return app


def app_produccion() -> FastAPI:
    """Fábrica para uvicorn: `uvicorn app.main:app_produccion --factory`."""
    return create_app()
