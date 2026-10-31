"""Recursos compartidos de la aplicación (se crean en el lifespan y se guardan en app.state)."""

from dataclasses import dataclass

from langgraph.graph.state import CompiledStateGraph
from redis.asyncio import Redis

from app.config.settings import Settings
from app.rag.store import VectorStore


@dataclass
class Recursos:
    settings: Settings
    grafo: CompiledStateGraph
    store: VectorStore
    redis: Redis | None
    tracing_activo: bool
