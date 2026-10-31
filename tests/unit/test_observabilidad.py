"""Pruebas de los spans manuales: errores reales se registran; la pausa HITL no es un error."""

import pytest
from langgraph.errors import GraphInterrupt
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app.observability import tracing


@pytest.fixture
def exportador(monkeypatch):
    exp = InMemorySpanExporter()
    proveedor = TracerProvider()
    proveedor.add_span_processor(SimpleSpanProcessor(exp))
    monkeypatch.setattr(tracing, "_tracer", proveedor.get_tracer("prueba"))
    return exp


def test_span_registra_atributos_y_tipo(exportador):
    with tracing.span("rag.recuperar", "RETRIEVER", consulta="x", n=3, objeto={"a": 1}):
        pass
    s = exportador.get_finished_spans()[0]
    assert s.attributes["openinference.span.kind"] == "RETRIEVER" and s.attributes["n"] == 3
    assert s.attributes["objeto"] == '{"a": 1}' and s.status.status_code != StatusCode.ERROR


def test_una_excepcion_real_queda_registrada_como_error_y_se_propaga(exportador):
    with pytest.raises(ValueError), tracing.span("herramienta.x", "TOOL"):
        raise ValueError("falló")
    s = exportador.get_finished_spans()[0]
    assert s.status.status_code == StatusCode.ERROR and "falló" in s.status.description
    assert any(e.name == "exception" for e in s.events)


def test_la_pausa_hitl_no_se_marca_como_error(exportador):
    with pytest.raises(GraphInterrupt), tracing.span("agente.human_review", "CHAIN"):
        raise GraphInterrupt()
    s = exportador.get_finished_spans()[0]
    assert s.status.status_code == StatusCode.OK and s.attributes["hitl.pausado"] is True
    assert not s.events


def test_sin_tracing_configurado_span_no_falla():
    with tracing.span("x"):
        assert trace.get_current_span() is not None
