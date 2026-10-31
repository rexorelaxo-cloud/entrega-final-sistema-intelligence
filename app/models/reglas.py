"""Modelos de la base de conocimiento: fragmentos, fuentes y reglas aplicables."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Aplicabilidad(StrEnum):
    OBLIGATORIA = "obligatoria"
    OPCIONAL = "opcional"


class Verificacion(StrEnum):
    """Qué comprobación determinista habilita una regla."""

    SINTAXIS = "sintaxis"
    ESQUEMA = "esquema"
    COMPARACION_ESTRUCTURAL = "comparacion_estructural"
    CONSISTENCIA_IDIOMAS = "consistencia_idiomas"
    NINGUNA = "ninguna"  # buena práctica informativa


class FuenteRef(BaseModel):
    doc_id: str
    ruta: str = Field(description="Ruta relativa del documento dentro de knowledge/")
    chunk_id: str
    seccion: str | None = None


class Fragmento(BaseModel):
    """Fragmento de documentación listo para almacenar o recuperado del vector store."""

    chunk_id: str
    texto: str
    metadatos: dict[str, str | int | float | bool]


class FragmentoRecuperado(BaseModel):
    fragmento: Fragmento
    score: float = Field(description="Similitud en [0,1]; mayor es más relevante")


class ReglaAplicable(BaseModel):
    rule_id: str
    titulo: str
    cms: str = Field(description="sitecore | umbraco | generico")
    aplicabilidad: Aplicabilidad
    severidad: str = Field(description="Severidad con la que se reporta una infracción")
    verificacion: Verificacion
    descripcion: str
    esquema: dict[str, Any] | None = None
    fuente: FuenteRef
    score: float = 0.0

    @property
    def es_demostracion(self) -> bool:
        return True  # toda la base incluida en el repositorio es material educativo
