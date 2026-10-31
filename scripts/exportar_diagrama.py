"""Imprime el diagrama Mermaid del grafo realmente compilado (el que se copia al README).

Uso:  python -m scripts.exportar_diagrama
"""

from langgraph.checkpoint.memory import InMemorySaver

from app.config.settings import Settings
from app.graph.builder import construir_grafo, diagrama_mermaid
from app.graph.state import Dependencias
from app.rag.embeddings import HashEmbedder
from app.rag.retriever import Retriever
from app.rag.store import MemoryVectorStore

if __name__ == "__main__":
    settings = Settings(_env_file=None, llm_mode="deterministic", tracing_enabled=False)
    retriever = Retriever(MemoryVectorStore(), HashEmbedder(), settings.rag_top_k, settings.rag_min_score)
    print(diagrama_mermaid(construir_grafo(Dependencias(settings, retriever, None), InMemorySaver())))
