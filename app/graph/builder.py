"""Construcción del grafo de LangGraph (Supervisor + agentes) con checkpointer.

Topología hub-and-spoke: todos los agentes vuelven al Supervisor, que decide el siguiente paso con base en el
estado. Las transiciones desde el Supervisor son condicionales.
"""

from functools import partial
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agents.analysis import nodo_analysis
from app.agents.human_review import nodo_human_review
from app.agents.research import nodo_research
from app.agents.supervisor import nodo_supervisor
from app.agents.synthesis import nodo_synthesis
from app.graph.state import Dependencias, EstadoQA

TRABAJADORES = ("research", "analysis", "human_review", "synthesis")


def enrutar(estado: EstadoQA) -> str:
    """Arista condicional: lee la decisión que dejó el Supervisor en el estado."""
    destino = estado.get("siguiente") or "FINISH"
    return destino if destino in (*TRABAJADORES, "FINISH") else "FINISH"


def construir_grafo(deps: Dependencias, checkpointer: BaseCheckpointSaver | None) -> CompiledStateGraph:
    g = StateGraph(EstadoQA)
    g.add_node("supervisor", partial(nodo_supervisor, deps=deps))
    g.add_node("research", partial(nodo_research, deps=deps))
    g.add_node("analysis", partial(nodo_analysis, deps=deps))
    g.add_node("human_review", nodo_human_review)
    g.add_node("synthesis", partial(nodo_synthesis, deps=deps))

    g.add_edge(START, "supervisor")
    g.add_conditional_edges(
        "supervisor",
        enrutar,
        {"research": "research", "analysis": "analysis", "human_review": "human_review", "synthesis": "synthesis", "FINISH": END},
    )
    for nodo in TRABAJADORES:
        g.add_edge(nodo, "supervisor")
    return g.compile(checkpointer=checkpointer)


def diagrama_mermaid(grafo: CompiledStateGraph) -> str:
    """Diagrama Mermaid generado a partir del grafo realmente compilado."""
    return grafo.get_graph().draw_mermaid()


def configuracion_hilo(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}
