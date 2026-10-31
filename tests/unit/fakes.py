"""LLM falso para probar los agentes sin consumir tokens."""

import json
from collections.abc import Callable
from typing import Any

from app.models.ejecucion import SupervisorDecision
from app.services.llm import LLMError


class LLMFalso:
    """Responde según el tipo de esquema solicitado. Un valor puede ser una instancia, una excepción o
    una función que recibe el texto del usuario."""

    def __init__(self, respuestas: dict[type, Any] | None = None, texto: str | Exception = "Respuesta del LLM falso."):
        self.respuestas = respuestas or {}
        self.texto_respuesta = texto
        self.llamadas: list[tuple[str, str]] = []

    async def estructurado(self, esquema, sistema: str, usuario: str):
        self.llamadas.append((esquema.__name__, usuario))
        r = self.respuestas.get(esquema)
        if r is None:
            raise LLMError(f"sin respuesta configurada para {esquema.__name__}")
        if isinstance(r, Exception):
            raise r
        return r(usuario) if isinstance(r, Callable) else r

    async def texto(self, sistema: str, usuario: str) -> str:
        self.llamadas.append(("texto", usuario))
        if isinstance(self.texto_respuesta, Exception):
            raise self.texto_respuesta
        return self.texto_respuesta


def supervisor_razonable(usuario: str) -> SupervisorDecision:
    """Simula un LLM que decide bien a partir del contexto que recibe el Supervisor."""
    c = json.loads(usuario)
    if c["sintesis_hecha"]:
        siguiente = "FINISH"
    elif c["revision_pendiente"]:
        siguiente = "human_review"
    elif c["tipo_turno"] == "analisis":
        siguiente = "synthesis" if c["analisis_hecho"] else ("analysis" if c["research_hecho"] else "research")
    else:
        siguiente = "synthesis" if c["research_hecho"] else "research"
    intencion = "analizar_archivos" if c["tipo_turno"] == "analisis" else "consultar_reglas"
    return SupervisorDecision(intencion=intencion, siguiente=siguiente, razon="decisión del LLM falso")
