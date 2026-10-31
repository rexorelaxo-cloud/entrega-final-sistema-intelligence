"""Modelos de decisiones de agentes, revisión humana e informe final."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.hallazgos import Hallazgo, ResumenQA
from app.models.reglas import FuenteRef


class Intencion(StrEnum):
    ANALIZAR_ARCHIVOS = "analizar_archivos"
    CONSULTAR_REGLAS = "consultar_reglas"
    EXPLICAR_RESULTADO = "explicar_resultado"


Destino = Literal["research", "analysis", "human_review", "synthesis", "FINISH"]


class SupervisorDecision(BaseModel):
    """Decisión estructurada del Supervisor (salida validada, no texto libre)."""

    intencion: Intencion
    alcance: Literal["completo", "solo_sintaxis"] = "completo"
    siguiente: Destino
    razon: str = Field(max_length=400)


class SeleccionReglas(BaseModel):
    """Selección del Research Agent: solo IDs que existan en lo recuperado."""

    rule_ids: list[str]
    razon: str = Field(max_length=400)


class CheckEjecutable(StrEnum):
    SINTAXIS = "sintaxis"
    ESQUEMA = "esquema"
    FALTANTES = "faltantes"
    INESPERADAS = "inesperadas"
    TIPOS = "tipos"
    ESTRUCTURA = "estructura"
    IDIOMAS = "idiomas"


class PlanAnalisis(BaseModel):
    checks: list[CheckEjecutable]
    razon: str = Field(max_length=400)


class Interpretacion(BaseModel):
    observaciones: list[str] = Field(default_factory=list, max_length=10)


class RedaccionInforme(BaseModel):
    resumen: str
    acciones_correctivas: list[str] = Field(default_factory=list)


class RevisionHumana(BaseModel):
    """Situación que requiere decisión de una persona."""

    tipo: Literal["cms_ambiguo", "regla_opcional"]
    pregunta: str
    opciones: list[str]
    contexto: dict = Field(default_factory=dict)


class DecisionHumana(BaseModel):
    """Respuesta que se entrega a /resume."""

    cms: Literal["sitecore", "umbraco"] | None = None
    aplicar_opcionales: bool | None = None
    comentario: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _algo_decidido(self):
        if self.cms is None and self.aplicar_opcionales is None:
            raise ValueError("La decisión debe incluir 'cms' o 'aplicar_opcionales'")
        return self


class InformeQA(BaseModel):
    estado: Literal["APTO", "NO_APTO", "INCOMPLETO"]
    resumen: str
    estadisticas: ResumenQA
    archivos_analizados: list[str]
    cms_aplicado: str | None = None
    reglas_aplicadas: list[str] = Field(default_factory=list)
    fuentes: list[FuenteRef] = Field(default_factory=list)
    bloqueantes: list[Hallazgo] = Field(default_factory=list)
    advertencias: list[Hallazgo] = Field(default_factory=list)
    informativos: list[Hallazgo] = Field(default_factory=list)
    acciones_correctivas: list[str] = Field(default_factory=list)
    observaciones: list[str] = Field(default_factory=list)
    decisiones_humanas: dict = Field(default_factory=dict)
    nota_origen_reglas: str = (
        "Las reglas provienen de documentación de demostración con fines educativos; "
        "no representan la documentación oficial de Sitecore ni de Umbraco."
    )
