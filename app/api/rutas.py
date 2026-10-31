"""Endpoints HTTP. Todos son async y validan entradas y salidas con Pydantic."""

import re
import uuid

import httpx
from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from langgraph.types import Command
from pydantic import ValidationError

from app.agents.politica import decision_responde_a
from app.api import servicio
from app.api.errores import ErrorAPI
from app.api.recursos import Recursos
from app.graph.entrada import entrada_analisis, entrada_chat
from app.models.api import (
    ChatRequest,
    ComponenteSalud,
    HistorialThread,
    RespuestaAnalisis,
    RespuestaChat,
    RespuestaSalud,
    ResumeRequest,
)
from app.models.solicitud import ArchivoEntrada, SolicitudAnalisis

router = APIRouter()
_THREAD_VALIDO = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
_ERRORES = {
    404: {"description": "El thread no existe"},
    409: {"description": "Conflicto con el estado del thread (revisión pendiente o ausente)"},
    422: {"description": "Solicitud inválida"},
}


def _recursos(request: Request) -> Recursos:
    return request.app.state.recursos


def _validar_thread(thread_id: str) -> str:
    if not _THREAD_VALIDO.match(thread_id):
        raise ErrorAPI(422, "thread_id_invalido", "thread_id solo admite letras, números y los símbolos _ . : - (máx. 128).")
    return thread_id


@router.post("/analyze", response_model=RespuestaAnalisis, tags=["Análisis"], summary="Analizar archivos JSON", responses=_ERRORES)
async def analizar(
    request: Request,
    archivos: list[UploadFile] = File(description="Uno o varios archivos JSON (UTF-8)"),
    thread_id: str | None = Form(default=None, description="Si se omite, se genera uno nuevo"),
    instruccion: str | None = Form(default=None, description="Indicación libre, p. ej. 'solo validar sintaxis'"),
    cms_destino: str | None = Form(default=None, description="sitecore | umbraco"),
    idioma_origen: str | None = Form(default=None, description="Idioma de referencia, p. ej. es-AR"),
    aplicar_reglas_opcionales: bool | None = Form(default=None, description="Si se omite y hay reglas opcionales, se pide decisión"),
) -> RespuestaAnalisis:
    r = _recursos(request)
    if not archivos:
        raise ErrorAPI(422, "sin_archivos", "Debe enviar al menos un archivo.")
    if len(archivos) > r.settings.max_files_per_request:
        raise ErrorAPI(422, "demasiados_archivos", f"Máximo {r.settings.max_files_per_request} archivos por solicitud.")

    entradas: list[ArchivoEntrada] = []
    for f in archivos:
        contenido = await f.read()
        if len(contenido) > r.settings.max_file_size_bytes:
            raise ErrorAPI(422, "archivo_demasiado_grande", f"'{f.filename}' supera {r.settings.max_file_size_bytes} bytes.")
        try:
            texto = contenido.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ErrorAPI(422, "codificacion_invalida", f"'{f.filename}' no está codificado en UTF-8.") from exc
        entradas.append(ArchivoEntrada(nombre=f.filename or "sin_nombre.json", contenido=texto))
    nombres = [e.nombre for e in entradas]
    if len(set(nombres)) != len(nombres):
        raise ErrorAPI(422, "nombres_duplicados", "Los archivos deben tener nombres distintos.")

    try:
        solicitud = SolicitudAnalisis(
            instruccion=instruccion or None,
            cms_destino=cms_destino,
            idioma_origen=idioma_origen or None,
            aplicar_reglas_opcionales=aplicar_reglas_opcionales,
        )
    except ValidationError as exc:
        raise ErrorAPI(422, "parametros_invalidos", "Parámetros de análisis inválidos.", [e["msg"] for e in exc.errors()]) from exc

    tid = _validar_thread(thread_id) if thread_id else uuid.uuid4().hex
    previo = await servicio.estado_thread(r.grafo, tid)
    if servicio.pendiente_revision(previo):
        raise ErrorAPI(409, "revision_pendiente", "El thread tiene una revisión pendiente: use /resume antes de enviar otro análisis.")

    snap = await servicio.ejecutar(r.grafo, tid, entrada_analisis(solicitud, entradas))
    return servicio.respuesta_analisis(tid, snap)


@router.post(
    "/chat", response_model=RespuestaChat, tags=["Análisis"], summary="Conversar usando el contexto del thread", responses=_ERRORES
)
async def chat(body: ChatRequest, request: Request) -> RespuestaChat:
    r = _recursos(request)
    tid = _validar_thread(body.thread_id)
    previo = await servicio.estado_thread(r.grafo, tid)
    if servicio.pendiente_revision(previo):
        raise ErrorAPI(409, "revision_pendiente", "El thread tiene una revisión pendiente: use /resume.")
    snap = await servicio.ejecutar(r.grafo, tid, entrada_chat(body.mensaje))
    return servicio.respuesta_chat(tid, snap)


@router.post(
    "/resume", response_model=RespuestaAnalisis, tags=["Análisis"], summary="Reanudar una ejecución pausada (HITL)", responses=_ERRORES
)
async def reanudar(body: ResumeRequest, request: Request) -> RespuestaAnalisis:
    r = _recursos(request)
    tid = _validar_thread(body.thread_id)
    previo = await servicio.estado_thread(r.grafo, tid)
    if not servicio.existe(previo):
        raise ErrorAPI(404, "thread_inexistente", f"No existe el thread '{tid}'.")
    revision = servicio.pendiente_revision(previo)
    if revision is None:
        raise ErrorAPI(409, "sin_revision_pendiente", "El thread no tiene ninguna revisión pendiente.")
    error = decision_responde_a(revision, body.decision)
    if error:
        raise ErrorAPI(422, "decision_invalida", error, {"revision": revision.model_dump()})
    snap = await servicio.ejecutar(r.grafo, tid, Command(resume=body.decision.model_dump(exclude_none=True)))
    return servicio.respuesta_analisis(tid, snap)


@router.get(
    "/threads/{thread_id}/history", response_model=HistorialThread, tags=["Threads"], summary="Historial de un thread", responses=_ERRORES
)
async def historial(thread_id: str, request: Request) -> HistorialThread:
    r = _recursos(request)
    tid = _validar_thread(thread_id)
    snap = await servicio.estado_thread(r.grafo, tid)
    if not servicio.existe(snap):
        raise ErrorAPI(404, "thread_inexistente", f"No existe el thread '{tid}'.")
    return await servicio.historial(r.grafo, tid, snap)


@router.get("/health", response_model=RespuestaSalud, tags=["Operación"], summary="Estado de los componentes")
async def salud(request: Request) -> JSONResponse:
    r = _recursos(request)
    componentes: dict[str, ComponenteSalud] = {}

    async def comprobar(nombre: str, coro) -> None:
        try:
            await coro
            componentes[nombre] = ComponenteSalud(estado="ok")
        except Exception as exc:  # el healthcheck informa, no propaga
            componentes[nombre] = ComponenteSalud(estado="error", detalle=f"{type(exc).__name__}: {exc}"[:200])

    if r.redis is not None:
        await comprobar("redis", r.redis.ping())
    else:
        componentes["redis"] = ComponenteSalud(estado="deshabilitado", detalle="sin conexión directa (checkpointer inyectado)")
    await comprobar("vector_store", r.store.ping())

    if r.tracing_activo:
        base = r.settings.phoenix_collector_endpoint.split("/v1/")[0]

        async def phoenix() -> None:
            async with httpx.AsyncClient(timeout=2.0) as c:
                (await c.get(f"{base}/healthz")).raise_for_status()

        await comprobar("phoenix", phoenix())
    else:
        componentes["phoenix"] = ComponenteSalud(estado="deshabilitado", detalle="TRACING_ENABLED=false")

    if r.settings.usa_openai:
        tiene_clave = bool(r.settings.openai_api_key.get_secret_value())
        componentes["openai"] = ComponenteSalud(
            estado="ok" if tiene_clave else "error",
            detalle="API key configurada (no se consume la API en el healthcheck)" if tiene_clave else "falta OPENAI_API_KEY",
        )
    else:
        componentes["openai"] = ComponenteSalud(estado="deshabilitado", detalle="LLM_MODE=deterministic")

    ok = all(c.estado != "error" for c in componentes.values())
    cuerpo = RespuestaSalud(estado="ok" if ok else "degradado", modo_llm=r.settings.llm_mode, componentes=componentes)
    return JSONResponse(status_code=200 if ok else 503, content=cuerpo.model_dump())
