"""Integración real con OpenAI. Se omite si no hay OPENAI_API_KEY o no hay salida a la API.

Ejecutar:  OPENAI_API_KEY=sk-... pytest -m openai -v
Consume una cantidad pequeña de tokens (embeddings de la base y unas pocas llamadas al modelo)."""

import os

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.config.settings import Settings
from app.graph.builder import configuracion_hilo, construir_grafo
from app.graph.entrada import entrada_analisis
from app.graph.state import Dependencias
from app.models.ejecucion import InformeQA
from app.models.solicitud import ArchivoEntrada, SolicitudAnalisis
from app.rag.embeddings import OpenAIEmbedder
from app.rag.ingestion import ingestar
from app.rag.retriever import Retriever
from app.rag.store import MemoryVectorStore
from app.services.llm import OpenAILLM, crear_cliente_openai
from tests.conftest import RAIZ, leer_fixture

pytestmark = pytest.mark.openai


class LLMContador:
    def __init__(self, real):
        self.real, self.llamadas = real, 0

    async def estructurado(self, esquema, sistema, usuario):
        self.llamadas += 1
        return await self.real.estructurado(esquema, sistema, usuario)

    async def texto(self, sistema, usuario):
        self.llamadas += 1
        return await self.real.texto(sistema, usuario)


@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="falta OPENAI_API_KEY")
async def test_analisis_completo_con_openai_real():
    settings = Settings(_env_file=None, llm_mode="openai", tracing_enabled=False)
    cliente = crear_cliente_openai(settings)
    embedder = OpenAIEmbedder(cliente, settings.openai_embedding_model)
    store = MemoryVectorStore()
    await ingestar(RAIZ / "knowledge", store, embedder, settings.rag_chunk_max_chars)  # embeddings reales
    llm = LLMContador(OpenAILLM(cliente, settings.openai_model))
    grafo = construir_grafo(Dependencias(settings, Retriever(store, embedder, 12, 0.0), llm), InMemorySaver())

    entrada = entrada_analisis(
        SolicitudAnalisis(cms_destino="sitecore", aplicar_reglas_opcionales=True),
        [ArchivoEntrada(nombre="sitecore_faltantes.json", contenido=leer_fixture("sitecore_faltantes.json"))],
    )
    estado = await grafo.ainvoke(entrada, configuracion_hilo("openai-real"))
    inf = InformeQA.model_validate(estado["informe"])

    assert llm.llamadas >= 2, "el LLM real no fue invocado"
    assert "LLM no disponible" not in estado["razon_supervisor"]
    # los hallazgos son deterministas: el LLM no los altera
    assert inf.estado == "NO_APTO" and {h.ruta for h in inf.bloqueantes} == {"$.itemId", "$.fields.Body"}
    assert inf.resumen
