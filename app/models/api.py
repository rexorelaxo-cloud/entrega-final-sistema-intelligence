"""Modelos de la API HTTP (entradas y salidas)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.ejecucion import DecisionHumana, InformeQA, RevisionHumana
from app.models.reglas import FuenteRef

EstadoEjecucion = Literal["completado", "pendiente_revision"]


class RespuestaAnalisis(BaseModel):
    thread_id: str
    estado: EstadoEjecucion
    intencion: str | None = None
    informe: InformeQA | None = None
    revision: RevisionHumana | None = None
    pasos: list[str] = Field(default_factory=list, description="Nodos ejecutados en este turno")


class ChatRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    mensaje: str = Field(min_length=1, max_length=4000)


class RespuestaChat(BaseModel):
    thread_id: str
    estado: EstadoEjecucion
    intencion: str | None = None
    respuesta: str | None = None
    fuentes: list[FuenteRef] = Field(default_factory=list)
    revision: RevisionHumana | None = None
    pasos: list[str] = Field(default_factory=list)


class ResumeRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=128)
    decision: DecisionHumana


class CheckpointResumen(BaseModel):
    checkpoint_id: str
    paso: int | None = None
    origen: str | None = None
    nodos_siguientes: list[str] = Field(default_factory=list)
    creado: datetime | str | None = None


class HistorialThread(BaseModel):
    thread_id: str
    estado: EstadoEjecucion
    mensajes: list[dict[str, Any]]
    informe: InformeQA | None = None
    revision: RevisionHumana | None = None
    checkpoints: list[CheckpointResumen]


class ComponenteSalud(BaseModel):
    estado: Literal["ok", "error", "deshabilitado"]
    detalle: str | None = None


class RespuestaSalud(BaseModel):
    estado: Literal["ok", "degradado"]
    modo_llm: str
    componentes: dict[str, ComponenteSalud]


class ErrorRespuesta(BaseModel):
    codigo: str
    mensaje: str
    detalle: Any | None = None
