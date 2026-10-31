"""Nodo de Human-in-the-Loop: pausa la ejecución con interrupt() y la reanuda con la decisión humana.

El estado queda en el checkpointer (Redis en producción), por lo que la pausa sobrevive a reinicios del servidor.
Todo lo que está antes de interrupt() se vuelve a ejecutar al reanudar, por eso no tiene efectos secundarios.
Si la respuesta no contesta la pregunta pendiente, el nodo vuelve a preguntar (patrón de validación de LangGraph).
"""

from langgraph.types import interrupt

from app.agents.politica import decision_responde_a
from app.graph.state import EstadoQA
from app.models.ejecucion import DecisionHumana, RevisionHumana
from app.observability.tracing import span


async def nodo_human_review(estado: EstadoQA) -> dict:
    revision = RevisionHumana.model_validate(estado["revision_pendiente"])
    with span("agente.human_review", "CHAIN", **{"hitl.tipo": revision.tipo}):
        payload = revision.model_dump(mode="json")
        while True:
            respuesta = interrupt(payload)  # se suspende hasta recibir Command(resume=...)
            try:
                decision = DecisionHumana.model_validate(respuesta)
            except ValueError as exc:
                error = f"Decisión inválida: {exc}"
            else:
                error = decision_responde_a(revision, decision)
                if error is None:
                    break
            # Si la respuesta no contesta la pregunta pendiente (p. ej. un valor de reanudación arrastrado de una
            # pausa anterior), se vuelve a preguntar en lugar de continuar con un estado inconsistente.
            payload = {**payload, "error": error}

    decisiones = dict(estado.get("decisiones_humanas") or {})
    if decision.cms is not None:
        decisiones["cms"] = decision.cms
    if decision.aplicar_opcionales is not None:
        decisiones["aplicar_opcionales"] = decision.aplicar_opcionales
    if decision.comentario:
        decisiones.setdefault("comentarios", []).append(decision.comentario)
    return {
        "decisiones_humanas": decisiones,
        "revision_pendiente": None,
        "mensajes": [{"rol": "humano", "contenido": f"Decisión sobre '{revision.tipo}': {decision.model_dump(exclude_none=True)}"}],
        "pasos": [*estado.get("pasos", []), f"human_review({revision.tipo})"],
    }
