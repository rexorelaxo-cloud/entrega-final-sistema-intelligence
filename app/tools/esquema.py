"""Herramientas 2-5: validación contra JSON Schema, propiedades faltantes, inesperadas y tipos.

Las cuatro comparten el mismo núcleo (jsonschema) y se diferencian por el tipo de error que reportan.
"""

import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from app.models.hallazgos import ORDEN_SEVERIDAD, Hallazgo, ResultadoHerramienta, Severidad
from app.tools.entradas import EntradaEsquema

COD_FALTANTE = "QA-REQ-001"
COD_INESPERADA = "QA-PROP-001"
COD_TIPO = "QA-TYPE-001"
COD_OTRO = "QA-SCH-001"
COD_ESQUEMA_INVALIDO = "QA-SCH-000"

_TIPOS_JSON = {
    bool: "boolean",
    int: "integer",
    float: "number",
    str: "string",
    list: "array",
    dict: "object",
    type(None): "null",
}


def _tipo_json(valor: Any) -> str:
    return _TIPOS_JSON.get(type(valor), type(valor).__name__)


def formatear_ruta(partes: list[Any]) -> str:
    ruta = "$"
    for p in partes:
        ruta += f"[{p}]" if isinstance(p, int) else f".{p}"
    return ruta


def _propiedades_inesperadas(error: ValidationError) -> list[str]:
    """Calcula qué claves del objeto no están declaradas en el esquema que lo restringe."""
    instancia = error.instance
    if not isinstance(instancia, dict):
        return []
    schema = error.schema if isinstance(error.schema, dict) else {}
    declaradas = set(schema.get("properties", {}))
    patrones = list(schema.get("patternProperties", {}))
    extras = []
    for clave in instancia:
        if clave in declaradas:
            continue
        if any(re.search(p, clave) for p in patrones):
            continue
        extras.append(clave)
    return sorted(extras)


def _convertir_error(error: ValidationError, e: EntradaEsquema, nombre: str) -> list[Hallazgo]:
    base = list(error.absolute_path)
    comunes = dict(archivo=nombre, regla=e.regla, fuente=e.fuente)
    validador = error.validator

    if validador == "required":
        faltantes = [k for k in error.validator_value if isinstance(error.instance, dict) and k not in error.instance]
        return [
            Hallazgo(
                codigo=COD_FALTANTE,
                severidad=Severidad.ERROR,
                ruta=formatear_ruta(base + [k]),
                descripcion=f"Falta la propiedad obligatoria '{k}'.",
                recomendacion=f"Agregar la propiedad '{k}' en {formatear_ruta(base)} según la regla documentada.",
                **comunes,
            )
            for k in faltantes
        ]

    if validador == "additionalProperties":
        return [
            Hallazgo(
                codigo=COD_INESPERADA,
                severidad=e.severidad_inesperadas,
                ruta=formatear_ruta(base + [k]),
                descripcion=f"Propiedad inesperada '{k}': no está declarada en el esquema.",
                recomendacion=f"Eliminar '{k}' o confirmar que el CMS destino la admite.",
                **comunes,
            )
            for k in _propiedades_inesperadas(error)
        ]

    if validador == "type":
        esperado = error.validator_value
        esperado_txt = " o ".join(esperado) if isinstance(esperado, list) else str(esperado)
        return [
            Hallazgo(
                codigo=COD_TIPO,
                severidad=Severidad.ERROR,
                ruta=formatear_ruta(base),
                descripcion=f"Tipo de dato incorrecto: se esperaba {esperado_txt} y se encontró {_tipo_json(error.instance)}.",
                recomendacion=f"Convertir el valor a {esperado_txt}.",
                **comunes,
            )
        ]

    return [
        Hallazgo(
            codigo=COD_OTRO,
            severidad=Severidad.ERROR,
            ruta=formatear_ruta(base),
            descripcion=f"Incumple la restricción '{validador}': {error.message}",
            recomendacion="Ajustar el valor para cumplir la restricción documentada.",
            **comunes,
        )
    ]


def _limitar(h: Hallazgo, maxima: Severidad) -> Hallazgo:
    """Baja la severidad de un hallazgo si supera la máxima permitida por la regla."""
    if ORDEN_SEVERIDAD[h.severidad] < ORDEN_SEVERIDAD[maxima]:
        return h.model_copy(update={"severidad": maxima})
    return h


def _validar(entrada: EntradaEsquema, herramienta: str) -> ResultadoHerramienta:
    try:
        Draft202012Validator.check_schema(entrada.esquema)
    except SchemaError as exc:
        return ResultadoHerramienta(
            herramienta=herramienta,
            archivos=[entrada.nombre],
            hallazgos=[
                Hallazgo(
                    codigo=COD_ESQUEMA_INVALIDO,
                    severidad=Severidad.WARNING,
                    archivo=entrada.nombre,
                    descripcion=f"El esquema de la regla {entrada.regla or '(sin id)'} no es válido: {exc.message}",
                    regla=entrada.regla,
                    fuente=entrada.fuente,
                    recomendacion="Corregir el bloque de esquema en la documentación de la regla.",
                )
            ],
        )
    validador = Draft202012Validator(entrada.esquema, format_checker=FormatChecker())
    hallazgos: list[Hallazgo] = []
    for error in sorted(validador.iter_errors(entrada.datos), key=lambda x: [str(p) for p in x.absolute_path]):
        hallazgos.extend(_convertir_error(error, entrada, entrada.nombre))
    hallazgos = [_limitar(h, entrada.severidad_maxima) for h in hallazgos]
    return ResultadoHerramienta(herramienta=herramienta, archivos=[entrada.nombre], hallazgos=hallazgos)


def validar_contra_esquema(entrada: EntradaEsquema) -> ResultadoHerramienta:
    """Validación completa contra JSON Schema (faltantes, inesperadas, tipos y restricciones)."""
    return _validar(entrada, "validar_contra_esquema")


def _filtrar(entrada: EntradaEsquema, herramienta: str, codigo: str) -> ResultadoHerramienta:
    completo = _validar(entrada, herramienta)
    return completo.model_copy(update={"hallazgos": [h for h in completo.hallazgos if h.codigo == codigo]})


def detectar_propiedades_faltantes(entrada: EntradaEsquema) -> ResultadoHerramienta:
    return _filtrar(entrada, "detectar_propiedades_faltantes", COD_FALTANTE)


def detectar_propiedades_inesperadas(entrada: EntradaEsquema) -> ResultadoHerramienta:
    return _filtrar(entrada, "detectar_propiedades_inesperadas", COD_INESPERADA)


def validar_tipos(entrada: EntradaEsquema) -> ResultadoHerramienta:
    return _filtrar(entrada, "validar_tipos", COD_TIPO)
