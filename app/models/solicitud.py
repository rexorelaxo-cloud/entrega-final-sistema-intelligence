"""Modelos de entrada: archivos y parámetros de análisis."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArchivoEntrada(BaseModel):
    model_config = ConfigDict(frozen=True)

    nombre: str = Field(min_length=1, max_length=255)
    contenido: str = Field(description="Contenido textual (UTF-8) del archivo")

    @field_validator("nombre")
    @classmethod
    def _sin_rutas(cls, v: str) -> str:
        # solo se usa el nombre base; evita rutas con separadores en informes y claves
        return v.replace("\\", "/").rsplit("/", 1)[-1]


class SolicitudAnalisis(BaseModel):
    """Parámetros de un análisis QA."""

    instruccion: str | None = Field(default=None, max_length=2000, description="Indicación libre del usuario")
    cms_destino: str | None = Field(default=None, description="sitecore | umbraco; si falta puede pedirse al usuario")
    idioma_origen: str | None = Field(default=None, description="Código del idioma de origen, p. ej. es-AR")
    aplicar_reglas_opcionales: bool | None = Field(default=None, description="None: preguntar si hay reglas opcionales aplicables")

    @field_validator("cms_destino")
    @classmethod
    def _normalizar_cms(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        v = v.strip().lower()
        if v not in {"sitecore", "umbraco"}:
            raise ValueError("cms_destino debe ser 'sitecore' o 'umbraco'")
        return v
