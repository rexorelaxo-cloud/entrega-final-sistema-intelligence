"""Lógica de aplicación entre la API y el grafo: ejecutar turnos, leer estado, armar respuestas."""

from typing import Any

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot

from app.graph.builder import configuracion_hilo
from app.models.api import CheckpointResumen, HistorialThread, RespuestaAnalisis, RespuestaChat
from app.models.ejecucion import InformeQA, RevisionHumana
from app.models.reglas import FuenteRef


async def estado_thread(grafo: CompiledStateGraph, thread_id: str) -> StateSnapshot:
    return await grafo.aget_state(configuracion_hilo(thread_id))


def existe(snapshot: StateSnapshot) -> bool:
    return bool(snapshot.values)


def pendiente_revision(snapshot: StateSnapshot) -> RevisionHumana | None:
    """Devuelve la revisión humana pendiente si el thread está pausado esperando una decisión."""
    if "human_review" in snapshot.next and snapshot.values.get("revision_pendiente"):
        return RevisionHumana.model_validate(snapshot.values["revision_pendiente"])
    return None


async def ejecutar(grafo: CompiledStateGraph, thread_id: str, entrada: dict[str, Any] | Command) -> StateSnapshot:
    """Ejecuta el grafo (asíncrono) y devuelve el estado resultante del thread."""
    await grafo.ainvoke(entrada, configuracion_hilo(thread_id))
    return await estado_thread(grafo, thread_id)


def respuesta_analisis(thread_id: str, snap: StateSnapshot) -> RespuestaAnalisis:
    revision = pendiente_revision(snap)
    informe = None if revision or not snap.values.get("informe") else InformeQA.model_validate(snap.values["informe"])
    return RespuestaAnalisis(
        thread_id=thread_id,
        estado="pendiente_revision" if revision else "completado",
        intencion=snap.values.get("intencion"),
        informe=informe,
        revision=revision,
        pasos=snap.values.get("pasos", []),
    )


def respuesta_chat(thread_id: str, snap: StateSnapshot) -> RespuestaChat:
    revision = pendiente_revision(snap)
    return RespuestaChat(
        thread_id=thread_id,
        estado="pendiente_revision" if revision else "completado",
        intencion=snap.values.get("intencion"),
        respuesta=None if revision else snap.values.get("respuesta"),
        fuentes=[FuenteRef.model_validate(f) for f in snap.values.get("fuentes", [])],
        revision=revision,
        pasos=snap.values.get("pasos", []),
    )


async def historial(grafo: CompiledStateGraph, thread_id: str, snap: StateSnapshot, limite: int = 100) -> HistorialThread:
    checkpoints: list[CheckpointResumen] = []
    async for s in grafo.aget_state_history(configuracion_hilo(thread_id), limit=limite):
        meta = s.metadata or {}
        checkpoints.append(
            CheckpointResumen(
                checkpoint_id=str(s.config["configurable"].get("checkpoint_id", "")),
                paso=meta.get("step"),
                origen=meta.get("source"),
                nodos_siguientes=list(s.next),
                creado=s.created_at,
            )
        )
    revision = pendiente_revision(snap)
    informe = snap.values.get("informe")
    return HistorialThread(
        thread_id=thread_id,
        estado="pendiente_revision" if revision else "completado",
        mensajes=snap.values.get("mensajes", []),
        informe=InformeQA.model_validate(informe) if informe else None,
        revision=revision,
        checkpoints=checkpoints,
    )
