"""Argumentos (Pydantic) de las herramientas de QA. Las herramientas no dependen de los agentes."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.hallazgos import ResultadoHerramienta, Severidad


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArchivoParseado(_Base):
    nombre: str = Field(min_length=1)
    datos: Any


class ReglaRef(_Base):
    """Identificación de la regla documentada que se estampa en los hallazgos."""

    regla: str | None = None
    fuente: str | None = None


class EntradaSintaxis(_Base):
    nombre: str = Field(min_length=1)
    contenido: str


class EntradaEsquema(ReglaRef):
    nombre: str = Field(min_length=1)
    datos: Any
    esquema: dict[str, Any]
    severidad_inesperadas: Severidad = Severidad.WARNING
    severidad_maxima: Severidad = Field(
        default=Severidad.ERROR,
        description="Severidad máxima de los hallazgos de la regla (una regla WARNING nunca reporta ERROR)",
    )


class EntradaComparacion(ReglaRef):
    referencia: ArchivoParseado
    objetivo: ArchivoParseado
    severidad_faltantes: Severidad = Severidad.ERROR
    severidad_sobrantes: Severidad = Severidad.WARNING


class EntradaIdiomas(ReglaRef):
    archivos: list[ArchivoParseado] = Field(min_length=2)
    idioma_origen: str | None = None
    incluir_estructura: bool = False  # la estructura se evalúa con comparar_estructuras (regla GEN-002)


class ResultadoSintaxis(ResultadoHerramienta):
    """Resultado de sintaxis: incluye los datos parseados cuando el JSON es válido."""

    datos: Any | None = None
    valido: bool = False
