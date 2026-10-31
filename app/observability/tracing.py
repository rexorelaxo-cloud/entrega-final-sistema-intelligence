"""Observabilidad con Arize Phoenix (OpenTelemetry + OpenInference).

- `configurar_tracing` registra el proveedor de trazas hacia Phoenix e instrumenta LangChain/LangGraph
  (grafo, nodos y transiciones) y OpenAI (llamadas al LLM con tokens y latencia).
- `span` crea spans manuales para lo que ninguna librería instrumenta: RAG y herramientas de QA.

Si el tracing está deshabilitado o Phoenix no está disponible, `span` es un no-op y la aplicación sigue
funcionando: el exportador trabaja en segundo plano y no bloquea las solicitudes.
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from langgraph.errors import GraphInterrupt
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from app.config.settings import Settings

log = logging.getLogger(__name__)
_tracer = trace.get_tracer("localization_qa")
_instrumentado = False


def configurar_tracing(settings: Settings) -> bool:
    """Registra Phoenix e instrumenta LangGraph/LangChain y OpenAI. Devuelve True si quedó activo."""
    global _instrumentado
    if not settings.tracing_enabled:
        log.info("Tracing deshabilitado (TRACING_ENABLED=false)")
        return False
    if _instrumentado:
        return True
    try:
        from openinference.instrumentation.langchain import LangChainInstrumentor
        from openinference.instrumentation.openai import OpenAIInstrumentor
        from phoenix.otel import register

        proveedor = register(
            project_name=settings.phoenix_project_name,
            endpoint=settings.phoenix_collector_endpoint,
            batch=True,
            set_global_tracer_provider=True,
            auto_instrument=False,
            verbose=False,
        )
        LangChainInstrumentor().instrument(tracer_provider=proveedor)
        OpenAIInstrumentor().instrument(tracer_provider=proveedor)
    except Exception:  # la observabilidad nunca debe impedir que el servicio arranque
        log.exception("No se pudo configurar el tracing hacia Phoenix")
        return False
    _instrumentado = True
    log.info("Tracing activo hacia %s (proyecto %s)", settings.phoenix_collector_endpoint, settings.phoenix_project_name)
    return True


def _valor(v: Any) -> str | bool | int | float:
    if isinstance(v, (str, bool, int, float)):
        return v
    try:
        return json.dumps(v, ensure_ascii=False, default=str)[:4000]
    except (TypeError, ValueError):
        return str(v)[:4000]


@contextmanager
def span(nombre: str, tipo: str = "CHAIN", **atributos: Any) -> Iterator[trace.Span]:
    """Span manual con el tipo OpenInference indicado (CHAIN, RETRIEVER, TOOL, AGENT...).

    Las excepciones se registran en el span y se vuelven a lanzar. La pausa de Human-in-the-Loop
    (`GraphInterrupt`) es control de flujo y no un error: se marca con `hitl.pausado=true` y estado OK.
    """
    with _tracer.start_as_current_span(nombre, record_exception=False, set_status_on_exception=False) as sp:
        sp.set_attribute("openinference.span.kind", tipo)
        for clave, valor in atributos.items():
            if valor is not None:
                sp.set_attribute(clave, _valor(valor))
        try:
            yield sp
        except GraphInterrupt:
            sp.set_attribute("hitl.pausado", True)
            sp.set_status(Status(StatusCode.OK))
            raise
        except BaseException as exc:
            sp.record_exception(exc)
            sp.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
            raise


def registrar_documentos(sp: trace.Span, documentos: list[dict[str, Any]]) -> None:
    """Adjunta los documentos recuperados con el esquema de atributos de OpenInference."""
    for i, d in enumerate(documentos):
        prefijo = f"retrieval.documents.{i}.document"
        sp.set_attribute(f"{prefijo}.id", str(d.get("id", "")))
        sp.set_attribute(f"{prefijo}.content", str(d.get("contenido", ""))[:1000])
        sp.set_attribute(f"{prefijo}.score", float(d.get("score", 0.0)))
        sp.set_attribute(f"{prefijo}.metadata", _valor(d.get("metadatos", {})))
