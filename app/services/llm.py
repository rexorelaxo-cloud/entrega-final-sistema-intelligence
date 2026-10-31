"""Cliente LLM desacoplado. Hoy solo OpenAI (SDK asíncrono); agregar otro proveedor implica
implementar `LLMClient` sin tocar los agentes."""

from typing import Protocol, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config.settings import Settings

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Error al invocar al LLM (red, cuota, respuesta no interpretable)."""


class LLMClient(Protocol):
    async def estructurado(self, esquema: type[T], sistema: str, usuario: str) -> T:
        """Devuelve una instancia validada de `esquema`."""
        ...

    async def texto(self, sistema: str, usuario: str) -> str: ...


class OpenAILLM:
    def __init__(self, cliente: AsyncOpenAI, modelo: str):
        self._cliente = cliente
        self._modelo = modelo

    async def estructurado(self, esquema: type[T], sistema: str, usuario: str) -> T:
        try:
            resp = await self._cliente.chat.completions.parse(
                model=self._modelo,
                messages=[{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
                response_format=esquema,
                temperature=0,
            )
        except Exception as exc:
            raise LLMError(f"Falló la llamada a OpenAI: {exc}") from exc
        mensaje = resp.choices[0].message
        if mensaje.refusal:
            raise LLMError(f"El modelo rechazó la solicitud: {mensaje.refusal}")
        if mensaje.parsed is None:
            raise LLMError("La respuesta del modelo no pudo interpretarse según el esquema")
        return mensaje.parsed

    async def texto(self, sistema: str, usuario: str) -> str:
        try:
            resp = await self._cliente.chat.completions.create(
                model=self._modelo,
                messages=[{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
                temperature=0.2,
            )
        except Exception as exc:
            raise LLMError(f"Falló la llamada a OpenAI: {exc}") from exc
        return resp.choices[0].message.content or ""


def crear_cliente_openai(settings: Settings) -> AsyncOpenAI:
    return AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=settings.openai_timeout_seconds)


def crear_llm(settings: Settings, cliente: AsyncOpenAI | None = None) -> LLMClient | None:
    """Devuelve None en modo determinista: los agentes usan entonces su lógica sin LLM."""
    if not settings.usa_openai:
        return None
    return OpenAILLM(cliente or crear_cliente_openai(settings), settings.openai_model)
