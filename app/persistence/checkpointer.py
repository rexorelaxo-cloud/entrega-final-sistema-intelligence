"""Checkpointer de LangGraph sobre Redis (AsyncRedisSaver, asíncrono).

Requiere un Redis con RediSearch y RedisJSON (Redis 8 o Redis Stack). El estado de cada thread_id queda en
Redis: sobrevive a reinicios del servidor y permite reanudar ejecuciones interrumpidas por HITL.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.redis.aio import AsyncRedisSaver

log = logging.getLogger(__name__)


@asynccontextmanager
async def checkpointer_redis(redis_url: str) -> AsyncIterator[AsyncRedisSaver]:
    """Abre la conexión, crea los índices si no existen y la cierra al salir."""
    async with AsyncRedisSaver.from_conn_string(redis_url) as saver:
        await saver.asetup()
        log.info("Checkpointer Redis listo en %s", redis_url)
        yield saver
