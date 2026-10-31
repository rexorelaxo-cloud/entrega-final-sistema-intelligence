"""Persistencia real: checkpointer de LangGraph sobre Redis. Se omite si no hay Redis con RediSearch/RedisJSON.

Escenario exigido: iniciar conversación -> ejecutar el grafo -> guardar estado -> recrear la aplicación manteniendo
Redis -> recuperar la conversación con el mismo thread_id."""

import httpx
import pytest
from redis.asyncio import Redis

from app.main import create_app
from tests.conftest import archivos_multipart

pytestmark = pytest.mark.redis


def nueva_app(settings, store):
    """Aplicación nueva: lifespan propio, conexión y checkpointer nuevos (equivale a reiniciar el servidor)."""
    return create_app(settings, store=store, llm=None)


async def cliente_de(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_la_conversacion_sobrevive_al_reinicio_de_la_aplicacion(settings_redis, store_memoria, thread_unico):
    # 1) instancia A: inicia la conversación y ejecuta el grafo hasta una pausa HITL
    app_a = nueva_app(settings_redis, store_memoria)
    async with app_a.router.lifespan_context(app_a), await cliente_de(app_a) as a:
        r = await a.post("/analyze", files=archivos_multipart("estructura_ambigua.json"), data={"thread_id": thread_unico})
        assert r.json()["estado"] == "pendiente_revision"

    # 2) la instancia A ya no existe; el estado solo puede estar en Redis
    app_b = nueva_app(settings_redis, store_memoria)
    async with app_b.router.lifespan_context(app_b), await cliente_de(app_b) as b:
        h = (await b.get(f"/threads/{thread_unico}/history")).json()
        assert h["estado"] == "pendiente_revision" and h["revision"]["tipo"] == "cms_ambiguo"
        assert [m["rol"] for m in h["mensajes"]] == ["usuario"] and h["checkpoints"]

        # 3) se reanuda en la instancia B con el mismo thread_id
        r = await b.post("/resume", json={"thread_id": thread_unico, "decision": {"cms": "sitecore", "aplicar_opcionales": True}})
        assert r.status_code == 200 and r.json()["estado"] in {"completado", "pendiente_revision"}
        if r.json()["estado"] == "pendiente_revision":  # sitecore tiene una regla opcional pendiente
            r = await b.post("/resume", json={"thread_id": thread_unico, "decision": {"aplicar_opcionales": True}})
        assert r.json()["estado"] == "completado" and r.json()["informe"]["cms_aplicado"] == "sitecore"

    # 4) instancia C: el informe final también persiste
    app_c = nueva_app(settings_redis, store_memoria)
    async with app_c.router.lifespan_context(app_c), await cliente_de(app_c) as c:
        h = (await c.get(f"/threads/{thread_unico}/history")).json()
        assert h["estado"] == "completado" and h["informe"]["cms_aplicado"] == "sitecore"
        chat = await c.post("/chat", json={"thread_id": thread_unico, "mensaje": "Explicame el informe"})
        assert chat.json()["intencion"] == "explicar_resultado" and "SC-001" in chat.json()["respuesta"]


async def test_el_estado_esta_realmente_en_redis(settings_redis, store_memoria, thread_unico):
    app = nueva_app(settings_redis, store_memoria)
    async with app.router.lifespan_context(app), await cliente_de(app) as c:
        await c.post(
            "/analyze",
            files=archivos_multipart("sitecore_valido.es-AR.json"),
            data={"thread_id": thread_unico, "cms_destino": "sitecore", "aplicar_reglas_opcionales": "true"},
        )
    redis = Redis.from_url(settings_redis.redis_url)
    try:
        claves = [k async for k in redis.scan_iter(match=f"checkpoint*{thread_unico}*", count=200)]
    finally:
        await redis.aclose()
    assert claves, "no se encontraron claves de checkpoint del thread en Redis"


async def test_threads_distintos_estan_aislados(settings_redis, store_memoria, thread_unico):
    app = nueva_app(settings_redis, store_memoria)
    async with app.router.lifespan_context(app), await cliente_de(app) as c:
        await c.post(
            "/analyze",
            files=archivos_multipart("sitecore_faltantes.json"),
            data={"thread_id": thread_unico, "cms_destino": "sitecore", "aplicar_reglas_opcionales": "true"},
        )
        assert (await c.get(f"/threads/{thread_unico}-otro/history")).status_code == 404


async def test_health_con_redis_real(settings_redis, store_memoria):
    app = nueva_app(settings_redis, store_memoria)
    async with app.router.lifespan_context(app), await cliente_de(app) as c:
        cuerpo = (await c.get("/health")).json()
        assert cuerpo["componentes"]["redis"]["estado"] == "ok"
