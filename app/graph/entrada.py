"""Construcción de las entradas del grafo para cada tipo de turno."""

from typing import Any

from app.graph.state import estado_turno_base
from app.models.solicitud import ArchivoEntrada, SolicitudAnalisis


def entrada_analisis(solicitud: SolicitudAnalisis, archivos: list[ArchivoEntrada]) -> dict[str, Any]:
    resumen = f"Análisis de {len(archivos)} archivo(s): {', '.join(a.nombre for a in archivos)}"
    return {
        **estado_turno_base(),
        "tipo_turno": "analisis",
        "consulta": solicitud.instruccion or "",
        "solicitud": solicitud.model_dump(mode="json"),
        "archivos": [a.model_dump() for a in archivos],
        "decisiones_humanas": {},
        "informe": None,
        "mensajes": [{"rol": "usuario", "contenido": f"{resumen}. {solicitud.instruccion or ''}".strip()}],
    }


def entrada_chat(mensaje: str) -> dict[str, Any]:
    return {
        **estado_turno_base(),
        "tipo_turno": "chat",
        "consulta": mensaje,
        "mensajes": [{"rol": "usuario", "contenido": mensaje}],
    }
