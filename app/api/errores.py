"""Manejo consistente de errores: todas las respuestas de error usan el modelo ErrorRespuesta."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from openai import OpenAIError

from app.models.api import ErrorRespuesta

log = logging.getLogger(__name__)


class ErrorAPI(Exception):
    def __init__(self, status: int, codigo: str, mensaje: str, detalle=None):
        self.status, self.codigo, self.mensaje, self.detalle = status, codigo, mensaje, detalle


def _json(status: int, codigo: str, mensaje: str, detalle=None) -> JSONResponse:
    return JSONResponse(status_code=status, content=ErrorRespuesta(codigo=codigo, mensaje=mensaje, detalle=detalle).model_dump())


def registrar_manejadores(app: FastAPI) -> None:
    @app.exception_handler(ErrorAPI)
    async def _api(_: Request, exc: ErrorAPI) -> JSONResponse:
        return _json(exc.status, exc.codigo, exc.mensaje, exc.detalle)

    @app.exception_handler(RequestValidationError)
    async def _validacion(_: Request, exc: RequestValidationError) -> JSONResponse:
        detalle = [{"campo": ".".join(str(p) for p in e["loc"]), "mensaje": e["msg"]} for e in exc.errors()]
        return _json(422, "solicitud_invalida", "La solicitud no cumple el formato esperado.", detalle)

    @app.exception_handler(OpenAIError)
    async def _openai(_: Request, exc: OpenAIError) -> JSONResponse:
        log.exception("Error del proveedor de LLM")
        return _json(502, "proveedor_llm_error", "El proveedor de LLM devolvió un error o no está disponible.")

    @app.exception_handler(Exception)
    async def _inesperado(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Error no controlado")
        return _json(500, "error_interno", "Ocurrió un error interno al procesar la solicitud.")
