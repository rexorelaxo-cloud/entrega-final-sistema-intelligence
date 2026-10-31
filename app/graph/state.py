"""Estado compartido tipado del grafo.

Todos los valores son JSON-serializables (dicts, listas, cadenas) para que el checkpointer de Redis pueda
persistirlos y restaurarlos tras un reinicio. Cada campo indica qué nodo lo escribe.
"""

import operator
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict

from app.config.settings import Settings
from app.rag.retriever import Retriever
from app.services.llm import LLMClient


class EstadoQA(TypedDict, total=False):
    # --- conversación (se acumula entre turnos) ---
    mensajes: Annotated[list[dict[str, str]], operator.add]
    informe: dict[str, Any] | None  # último informe QA del thread (synthesis)
    archivos: list[dict[str, str]]  # archivos del último análisis: {nombre, contenido}
    solicitud: dict[str, Any]  # SolicitudAnalisis serializada

    # --- turno actual (la entrada los reinicia en cada solicitud) ---
    tipo_turno: str  # "analisis" | "chat"
    consulta: str  # instrucción o mensaje del usuario
    intencion: str | None  # supervisor
    alcance: str | None  # supervisor: completo | solo_sintaxis
    siguiente: str  # supervisor
    razon_supervisor: str  # supervisor
    correcciones_supervisor: int  # supervisor: decisiones del LLM corregidas por validación
    n_pasos: int  # supervisor
    pasos: list[str]  # todos los nodos, en orden de ejecución

    research_hecho: bool  # research
    reglas: list[dict[str, Any]]  # research: ReglaAplicable serializadas
    guias: list[dict[str, Any]]  # research: fragmentos informativos con su fuente

    analisis_hecho: bool  # analysis
    hallazgos: list[dict[str, Any]]  # analysis
    observaciones: list[str]  # analysis
    cms_aplicado: str | None  # analysis
    reglas_aplicadas: list[str]  # analysis
    revision_pendiente: dict[str, Any] | None  # analysis -> human_review
    decisiones_humanas: dict[str, Any]  # human_review

    sintesis_hecha: bool  # synthesis
    respuesta: str | None  # synthesis (turnos de chat)
    fuentes: list[dict[str, Any]]  # synthesis


@dataclass
class Dependencias:
    """Servicios que reciben los nodos. `llm=None` activa la lógica determinista sin LLM."""

    settings: Settings
    retriever: Retriever
    llm: LLMClient | None = None


def estado_turno_base() -> dict[str, Any]:
    """Valores con los que se reinicia el turno. No toca informe, archivos ni mensajes."""
    return {
        "intencion": None,
        "alcance": None,
        "siguiente": "",
        "razon_supervisor": "",
        "correcciones_supervisor": 0,
        "n_pasos": 0,
        "pasos": [],
        "research_hecho": False,
        "reglas": [],
        "guias": [],
        "analisis_hecho": False,
        "hallazgos": [],
        "observaciones": [],
        "cms_aplicado": None,
        "reglas_aplicadas": [],
        "revision_pendiente": None,
        "sintesis_hecha": False,
        "respuesta": None,
        "fuentes": [],
    }
