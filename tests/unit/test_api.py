"""Pruebas de la API HTTP (ASGI en memoria, checkpointer en memoria, modo determinista)."""

import asyncio

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.api import servicio
from app.main import create_app
from tests.conftest import archivos_multipart

SC = {"cms_destino": "sitecore", "aplicar_reglas_opcionales": "true"}


async def test_health_informa_componentes(cliente):
    r = await cliente.get("/health")
    cuerpo = r.json()
    assert r.status_code == 200 and cuerpo["estado"] == "ok" and cuerpo["modo_llm"] == "deterministic"
    assert cuerpo["componentes"]["vector_store"]["estado"] == "ok"
    assert cuerpo["componentes"]["openai"]["estado"] == "deshabilitado"


async def test_health_degradado_devuelve_503_si_un_componente_falla(settings, store):
    async def ping_roto():
        raise ConnectionError("chroma caído")

    store.ping = ping_roto
    app = create_app(settings, checkpointer=InMemorySaver(), store=store, llm=None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/health")
    assert r.status_code == 503
    assert r.json()["estado"] == "degradado" and "chroma caído" in r.json()["componentes"]["vector_store"]["detalle"]


async def test_openapi_documenta_los_endpoints(cliente):
    spec = (await cliente.get("/openapi.json")).json()
    assert {"/analyze", "/chat", "/resume", "/threads/{thread_id}/history", "/health"} <= set(spec["paths"])
    assert (await cliente.get("/docs")).status_code == 200


async def test_analyze_un_archivo_devuelve_informe_estructurado(cliente):
    r = await cliente.post("/analyze", files=archivos_multipart("sitecore_faltantes.json"), data={**SC, "thread_id": "api-1"})
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["thread_id"] == "api-1" and cuerpo["estado"] == "completado"
    inf = cuerpo["informe"]
    assert inf["estado"] == "NO_APTO" and inf["estadisticas"]["errores"] == 2
    assert {h["ruta"] for h in inf["bloqueantes"]} == {"$.itemId", "$.fields.Body"}
    assert {"codigo", "severidad", "archivo", "ruta", "descripcion", "regla", "recomendacion"} <= set(inf["bloqueantes"][0])


async def test_analyze_genera_thread_id_si_se_omite(cliente):
    r = await cliente.post("/analyze", files=archivos_multipart("sitecore_valido.es-AR.json"), data=SC)
    assert len(r.json()["thread_id"]) == 32


async def test_analyze_varios_archivos_compara_estructuras(cliente):
    r = await cliente.post(
        "/analyze",
        files=archivos_multipart("sitecore_valido.es-AR.json", "sitecore_fr_inconsistente.fr-FR.json"),
        data={**SC, "idioma_origen": "es-AR"},
    )
    codigos = {h["codigo"] for h in r.json()["informe"]["bloqueantes"]}
    assert {"QA-STR-001", "QA-STR-003", "QA-LANG-003"} <= codigos


async def test_analyze_pausa_por_hitl_y_resume_completa(cliente):
    r = await cliente.post(
        "/analyze", files=archivos_multipart("estructura_ambigua.json"), data={"thread_id": "api-hitl", "aplicar_reglas_opcionales": "true"}
    )
    cuerpo = r.json()
    assert cuerpo["estado"] == "pendiente_revision" and cuerpo["informe"] is None
    assert cuerpo["revision"]["tipo"] == "cms_ambiguo" and cuerpo["revision"]["opciones"] == ["sitecore", "umbraco"]

    # mientras está pausado no se aceptan nuevos análisis ni chat
    r2 = await cliente.post("/analyze", files=archivos_multipart("estructura_ambigua.json"), data={"thread_id": "api-hitl"})
    assert r2.status_code == 409 and r2.json()["codigo"] == "revision_pendiente"
    assert (await cliente.post("/chat", json={"thread_id": "api-hitl", "mensaje": "hola"})).status_code == 409

    h = await cliente.get("/threads/api-hitl/history")
    assert h.json()["estado"] == "pendiente_revision" and h.json()["revision"]["tipo"] == "cms_ambiguo"

    ok = await cliente.post("/resume", json={"thread_id": "api-hitl", "decision": {"cms": "umbraco"}})
    assert ok.status_code == 200
    assert ok.json()["estado"] == "completado" and ok.json()["informe"]["cms_aplicado"] == "umbraco"
    assert ok.json()["informe"]["decisiones_humanas"] == {"cms": "umbraco"}


async def test_resume_validaciones(cliente):
    r = await cliente.post("/resume", json={"thread_id": "no-existe", "decision": {"cms": "sitecore"}})
    assert r.status_code == 404 and r.json()["codigo"] == "thread_inexistente"

    await cliente.post("/analyze", files=archivos_multipart("sitecore_valido.es-AR.json"), data={**SC, "thread_id": "completo"})
    r = await cliente.post("/resume", json={"thread_id": "completo", "decision": {"cms": "sitecore"}})
    assert r.status_code == 409 and r.json()["codigo"] == "sin_revision_pendiente"

    await cliente.post("/analyze", files=archivos_multipart("estructura_ambigua.json"), data={"thread_id": "p"})
    r = await cliente.post("/resume", json={"thread_id": "p", "decision": {"aplicar_opcionales": True}})
    assert r.status_code == 422 and r.json()["codigo"] == "decision_invalida"
    assert (await cliente.post("/resume", json={"thread_id": "p", "decision": {}})).status_code == 422
    assert (await cliente.post("/resume", json={"thread_id": "p", "decision": {"cms": "drupal"}})).status_code == 422


async def test_chat_usa_el_contexto_del_thread(cliente):
    await cliente.post("/analyze", files=archivos_multipart("sitecore_faltantes.json"), data={**SC, "thread_id": "chat-1"})
    r = await cliente.post("/chat", json={"thread_id": "chat-1", "mensaje": "Explicame el informe, ¿por qué falló?"})
    cuerpo = r.json()
    assert r.status_code == 200 and cuerpo["intencion"] == "explicar_resultado" and "$.itemId" in cuerpo["respuesta"]


async def test_chat_consulta_reglas_y_devuelve_fuentes(cliente):
    r = await cliente.post("/chat", json={"thread_id": "chat-2", "mensaje": "¿Qué exige Sitecore para el itemId?"})
    cuerpo = r.json()
    assert cuerpo["intencion"] == "consultar_reglas" and cuerpo["fuentes"]
    assert all(f["ruta"].endswith(".md") and f["chunk_id"] for f in cuerpo["fuentes"])


async def test_historial_devuelve_mensajes_informe_y_checkpoints(cliente):
    await cliente.post("/analyze", files=archivos_multipart("sitecore_valido.es-AR.json"), data={**SC, "thread_id": "hist-1"})
    await cliente.post("/chat", json={"thread_id": "hist-1", "mensaje": "¿Qué es SC-004?"})
    h = (await cliente.get("/threads/hist-1/history")).json()
    assert h["estado"] == "completado" and h["informe"]["estado"] == "APTO"
    assert [m["rol"] for m in h["mensajes"]] == ["usuario", "asistente", "usuario", "asistente"]
    assert len(h["checkpoints"]) > 5 and h["checkpoints"][0]["checkpoint_id"]


async def test_historial_de_thread_inexistente_es_404(cliente):
    r = await cliente.get("/threads/fantasma/history")
    assert r.status_code == 404 and r.json()["codigo"] == "thread_inexistente"


@pytest.mark.parametrize(
    "kwargs,codigo",
    [
        (dict(files=[]), "solicitud_invalida"),  # sin archivos (validación de FastAPI)
        (dict(files=archivos_multipart("sitecore_valido.es-AR.json"), data={"cms_destino": "wordpress"}), "parametros_invalidos"),
        (dict(files=archivos_multipart("sitecore_valido.es-AR.json"), data={"thread_id": "con espacios!"}), "thread_id_invalido"),
        (dict(files=archivos_multipart("sitecore_valido.es-AR.json", "sitecore_valido.es-AR.json")), "nombres_duplicados"),
        (dict(files=[("archivos", ("x.json", b"\xff\xfe\x00no-utf8", "application/json"))]), "codificacion_invalida"),
    ],
)
async def test_analyze_rechaza_solicitudes_invalidas_con_error_estructurado(cliente, kwargs, codigo):
    r = await cliente.post("/analyze", **kwargs)
    assert r.status_code == 422
    assert r.json()["codigo"] == codigo and r.json()["mensaje"]


async def test_analyze_limita_cantidad_y_tamano(settings, store):
    chico = settings.model_copy(update={"max_files_per_request": 1, "max_file_size_bytes": 50})
    app = create_app(chico, checkpointer=InMemorySaver(), store=store, llm=None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/analyze", files=archivos_multipart("sitecore_valido.es-AR.json", "umbraco_valido.es-AR.json"))
            assert r.json()["codigo"] == "demasiados_archivos"
            r = await c.post("/analyze", files=archivos_multipart("sitecore_valido.es-AR.json"))
            assert r.json()["codigo"] == "archivo_demasiado_grande"


async def test_error_interno_se_devuelve_estructurado_sin_filtrar_detalles(settings, store, monkeypatch):
    async def roto(*a, **k):
        raise RuntimeError("secreto interno")

    monkeypatch.setattr(servicio, "ejecutar", roto)
    app = create_app(settings, checkpointer=InMemorySaver(), store=store, llm=None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as c:
            r = await c.post("/analyze", files=archivos_multipart("sitecore_valido.es-AR.json"), data=SC)
    assert r.status_code == 500 and r.json()["codigo"] == "error_interno"
    assert "secreto interno" not in r.text


async def test_error_del_proveedor_llm_se_traduce_a_502(settings, store, monkeypatch):
    from openai import OpenAIError

    async def roto(*a, **k):
        raise OpenAIError("cuota agotada")

    monkeypatch.setattr(servicio, "ejecutar", roto)
    app = create_app(settings, checkpointer=InMemorySaver(), store=store, llm=None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://t") as c:
            r = await c.post("/chat", json={"thread_id": "x", "mensaje": "hola"})
    assert r.status_code == 502 and r.json()["codigo"] == "proveedor_llm_error"


async def test_cinco_solicitudes_simultaneas_se_resuelven_sin_cruzar_estados(cliente):
    """Concurrencia lógica: 5 análisis a la vez, cada uno con su thread, sin mezclar resultados."""
    casos = [
        ("sitecore_valido.es-AR.json", "APTO", "sitecore"),
        ("sitecore_faltantes.json", "NO_APTO", "sitecore"),
        ("json_invalido.json", "NO_APTO", "sitecore"),
        ("umbraco_valido.es-AR.json", "APTO", "umbraco"),
        ("sitecore_valido.en-US.json", "APTO", "sitecore"),
    ]
    respuestas = await asyncio.gather(
        *(
            cliente.post("/analyze", files=archivos_multipart(n), data={**SC, "cms_destino": cms, "thread_id": f"conc-{i}"})
            for i, (n, _, cms) in enumerate(casos)
        )
    )
    for i, (r, (nombre, esperado, _)) in enumerate(zip(respuestas, casos, strict=True)):
        assert r.status_code == 200, r.text
        assert r.json()["thread_id"] == f"conc-{i}" and r.json()["informe"]["estado"] == esperado
        assert r.json()["informe"]["archivos_analizados"] == [nombre]
