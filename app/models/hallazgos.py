"""Modelos de hallazgos y resultados de las herramientas de QA."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severidad(StrEnum):
    ERROR = "ERROR"  # puede impedir la importación
    WARNING = "WARNING"  # requiere revisión
    INFO = "INFO"  # información o recomendación


ORDEN_SEVERIDAD = {Severidad.ERROR: 0, Severidad.WARNING: 1, Severidad.INFO: 2}


class Hallazgo(BaseModel):
    model_config = ConfigDict(frozen=True)

    codigo: str = Field(description="Código estable del hallazgo, p. ej. QA-REQ-001")
    severidad: Severidad
    archivo: str
    ruta: str | None = Field(default=None, description="Ruta de la propiedad (JSON Pointer o notación $.a.b)")
    descripcion: str
    regla: str | None = Field(default=None, description="ID de la regla documentada que originó el hallazgo")
    fuente: str | None = Field(default=None, description="Documento de la base de conocimiento de la regla")
    recomendacion: str | None = None
    linea: int | None = None
    columna: int | None = None

    @property
    def clave(self) -> tuple[str, str, str | None, str | None]:
        """Clave de deduplicación."""
        return (self.codigo, self.archivo, self.ruta, self.regla)


class ResultadoHerramienta(BaseModel):
    """Resultado estructurado de cualquier herramienta de QA."""

    herramienta: str
    archivos: list[str] = Field(default_factory=list)
    hallazgos: list[Hallazgo] = Field(default_factory=list)
    metadatos: dict[str, Any] = Field(default_factory=dict)

    @property
    def tiene_errores(self) -> bool:
        return any(h.severidad == Severidad.ERROR for h in self.hallazgos)


class ResumenQA(BaseModel):
    total: int
    errores: int
    advertencias: int
    informativos: int
    apto_para_importar: bool


def consolidar_hallazgos(hallazgos: list[Hallazgo]) -> list[Hallazgo]:
    """Elimina duplicados exactos y ordena por severidad, archivo y ruta."""
    vistos: dict[tuple, Hallazgo] = {}
    for h in hallazgos:
        existente = vistos.get(h.clave)
        # ante duplicados se conserva el de mayor severidad
        if existente is None or ORDEN_SEVERIDAD[h.severidad] < ORDEN_SEVERIDAD[existente.severidad]:
            vistos[h.clave] = h
    return sorted(vistos.values(), key=lambda h: (ORDEN_SEVERIDAD[h.severidad], h.archivo, h.ruta or "", h.codigo))


def resumir(hallazgos: list[Hallazgo]) -> ResumenQA:
    e = sum(h.severidad == Severidad.ERROR for h in hallazgos)
    w = sum(h.severidad == Severidad.WARNING for h in hallazgos)
    i = sum(h.severidad == Severidad.INFO for h in hallazgos)
    return ResumenQA(total=len(hallazgos), errores=e, advertencias=w, informativos=i, apto_para_importar=e == 0)
