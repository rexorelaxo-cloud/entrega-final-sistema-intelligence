"""Supervisor: interpreta la solicitud, decide qué agente interviene y cuándo terminar.

La decisión es estructurada (`SupervisorDecision`). Con LLM, el modelo propone la decisión y el código la
valida contra el estado: si propone una transición imposible se reemplaza por la decisión por reglas. Sin
LLM (modo determinista) se usa directamente la lógica por reglas.
"""

import json
import logging

from app.graph.state import Dependencias, EstadoQA
from app.models.ejecucion import Intencion, SupervisorDecision
from app.observability.tracing import span
from app.services.llm import LLMError

log = logging.getLogger(__name__)

_PALABRAS_RESULTADO = (
    "informe",
    "resultado",
    "hallazgo",
    "error",
    "advertencia",
    "por qué",
    "porque",
    "explic",
    "resum",
    "arreglar",
    "corregir",
    "bloque",
)
_PALABRAS_SOLO_SINTAXIS = ("solo sintaxis", "sólo sintaxis", "solo la sintaxis", "solamente sintaxis", "solo validar sintaxis")


def inferir_intencion(estado: EstadoQA) -> Intencion:
    if estado.get("tipo_turno") == "analisis":
        return Intencion.ANALIZAR_ARCHIVOS
    consulta = (estado.get("consulta") or "").lower()
    if estado.get("informe") and any(p in consulta for p in _PALABRAS_RESULTADO):
        return Intencion.EXPLICAR_RESULTADO
    return Intencion.CONSULTAR_REGLAS


def inferir_alcance(estado: EstadoQA) -> str:
    consulta = (estado.get("consulta") or "").lower()
    if estado.get("tipo_turno") == "analisis" and any(p in consulta for p in _PALABRAS_SOLO_SINTAXIS):
        return "solo_sintaxis"
    return "completo"


def decidir_por_reglas(estado: EstadoQA, max_pasos: int) -> SupervisorDecision:
    intencion = Intencion(estado.get("intencion") or inferir_intencion(estado))
    alcance = estado.get("alcance") or inferir_alcance(estado)

    def d(siguiente: str, razon: str) -> SupervisorDecision:
        return SupervisorDecision(intencion=intencion, alcance=alcance, siguiente=siguiente, razon=razon)

    if estado.get("sintesis_hecha"):
        return d("FINISH", "La síntesis ya fue generada; no hay más trabajo.")
    if estado.get("n_pasos", 0) >= max_pasos:
        return d("synthesis", f"Se alcanzó el máximo de {max_pasos} pasos; se cierra con la información disponible.")
    if estado.get("revision_pendiente"):
        return d("human_review", "El análisis encontró una ambigüedad que requiere decisión humana.")

    if intencion == Intencion.ANALIZAR_ARCHIVOS:
        if alcance == "completo" and not estado.get("research_hecho"):
            return d("research", "Se necesitan las reglas documentadas aplicables antes de validar.")
        if not estado.get("analisis_hecho"):
            return d("analysis", "Hay archivos por validar con las herramientas de QA.")
        return d("synthesis", "El análisis terminó; se consolida el informe.")
    if intencion == Intencion.CONSULTAR_REGLAS:
        if not estado.get("research_hecho"):
            return d("research", "La consulta se responde con la base de conocimiento.")
        return d("synthesis", "Ya se recuperó documentación; se redacta la respuesta.")
    return d("synthesis", "La pregunta se responde con el informe ya existente en el thread.")


def validar_decision(dec: SupervisorDecision, estado: EstadoQA, max_pasos: int) -> str | None:
    """Devuelve el motivo de rechazo si la decisión no es coherente con el estado; None si es válida."""
    tipo = estado.get("tipo_turno")
    if tipo == "analisis" and dec.intencion != Intencion.ANALIZAR_ARCHIVOS:
        return "en un turno de análisis la intención debe ser analizar_archivos"
    if tipo == "chat" and dec.intencion == Intencion.ANALIZAR_ARCHIVOS:
        return "un turno de chat no puede iniciar un análisis; use /analyze"
    if estado.get("intencion") and dec.intencion != estado["intencion"]:
        return "la intención no puede cambiar a mitad del turno"
    if estado.get("sintesis_hecha"):
        return None if dec.siguiente == "FINISH" else "ya se sintetizó: solo corresponde FINISH"
    if estado.get("revision_pendiente"):
        return None if dec.siguiente == "human_review" else "hay una revisión pendiente: corresponde human_review"
    if dec.siguiente == "human_review":
        return "no hay revisión pendiente"
    if dec.siguiente == "FINISH":
        return "no se puede finalizar sin síntesis"
    if estado.get("n_pasos", 0) >= max_pasos:
        return None if dec.siguiente == "synthesis" else "se agotaron los pasos: solo corresponde synthesis"
    if dec.siguiente == "research" and estado.get("research_hecho"):
        return "research ya se ejecutó"
    if dec.siguiente == "research" and dec.intencion == Intencion.EXPLICAR_RESULTADO:
        return "explicar un resultado no requiere research"
    if dec.siguiente == "analysis":
        if dec.intencion != Intencion.ANALIZAR_ARCHIVOS:
            return "analysis solo corresponde al analizar archivos"
        if estado.get("analisis_hecho"):
            return "analysis ya se ejecutó"
        if dec.alcance == "completo" and not estado.get("research_hecho"):
            return "faltan las reglas: primero research"
    con_margen = estado.get("n_pasos", 0) < max_pasos
    if dec.siguiente == "synthesis" and dec.intencion == Intencion.ANALIZAR_ARCHIVOS and not estado.get("analisis_hecho") and con_margen:
        return "synthesis requiere el análisis completo"
    if dec.siguiente == "synthesis" and dec.intencion == Intencion.CONSULTAR_REGLAS and not estado.get("research_hecho") and con_margen:
        return "synthesis requiere haber consultado la base de conocimiento"
    return None


_SISTEMA = """Eres el Supervisor de un sistema de control de calidad (QA) de localización de contenido.
Decides qué agente debe intervenir a continuación. Agentes: research (consulta la base de conocimiento de reglas),
analysis (ejecuta validaciones JSON deterministas), human_review (pausa para una decisión humana, solo si hay una
revisión pendiente), synthesis (redacta el informe o la respuesta) y FINISH (solo cuando la síntesis ya se hizo).
Intenciones: analizar_archivos (turnos de análisis con archivos), consultar_reglas (preguntas sobre reglas o
requisitos documentados) y explicar_resultado (preguntas sobre un informe ya generado).
alcance: "solo_sintaxis" si el usuario pide únicamente validar la sintaxis; si no, "completo".
Para analizar archivos el orden lógico es research -> analysis -> synthesis (con alcance solo_sintaxis se omite research).
Responde únicamente con la decisión estructurada solicitada; la razón debe ser breve y en español."""


def _contexto_para_llm(estado: EstadoQA) -> str:
    return json.dumps(
        {
            "tipo_turno": estado.get("tipo_turno"),
            "consulta_usuario": estado.get("consulta"),
            "cantidad_archivos": len(estado.get("archivos") or []) if estado.get("tipo_turno") == "analisis" else 0,
            "hay_informe_previo": bool(estado.get("informe")),
            "intencion_actual": estado.get("intencion"),
            "research_hecho": estado.get("research_hecho", False),
            "analisis_hecho": estado.get("analisis_hecho", False),
            "sintesis_hecha": estado.get("sintesis_hecha", False),
            "revision_pendiente": bool(estado.get("revision_pendiente")),
            "pasos_ejecutados": estado.get("pasos", []),
        },
        ensure_ascii=False,
    )


async def nodo_supervisor(estado: EstadoQA, deps: Dependencias) -> dict:
    max_pasos = deps.settings.graph_max_steps
    correcciones = estado.get("correcciones_supervisor", 0)
    with span("agente.supervisor", "AGENT", **{"input.value": estado.get("consulta") or ""}) as sp:
        decision: SupervisorDecision
        origen = "reglas"
        if deps.llm is None:
            decision = decidir_por_reglas(estado, max_pasos)
        else:
            try:
                propuesta = await deps.llm.estructurado(SupervisorDecision, _SISTEMA, _contexto_para_llm(estado))
                motivo = validar_decision(propuesta, estado, max_pasos)
                if motivo is None:
                    decision, origen = propuesta, "llm"
                else:
                    log.warning("Decisión del supervisor corregida: %s", motivo)
                    base = decidir_por_reglas(estado, max_pasos)
                    decision = base.model_copy(update={"razon": f"Corregido ({motivo}). {base.razon}"[:400]})
                    origen, correcciones = "reglas (corregido)", correcciones + 1
            except LLMError as exc:
                log.warning("LLM no disponible para el supervisor, se usan reglas: %s", exc)
                decision = decidir_por_reglas(estado, max_pasos)
                origen = "reglas (LLM no disponible)"
        sp.set_attribute("supervisor.siguiente", decision.siguiente)
        sp.set_attribute("supervisor.intencion", decision.intencion.value)
        sp.set_attribute("supervisor.origen", origen)

    return {
        "intencion": decision.intencion.value,
        "alcance": decision.alcance,
        "siguiente": decision.siguiente,
        "razon_supervisor": f"[{origen}] {decision.razon}",
        "correcciones_supervisor": correcciones,
        "n_pasos": estado.get("n_pasos", 0) + 1,
        "pasos": [*estado.get("pasos", []), f"supervisor→{decision.siguiente}"],
    }
