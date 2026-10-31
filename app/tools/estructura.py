"""Herramienta 6: comparación de estructuras entre dos archivos JSON."""

from typing import Any

from app.models.hallazgos import Hallazgo, ResultadoHerramienta, Severidad
from app.tools.entradas import EntradaComparacion
from app.tools.esquema import _tipo_json

COD_SOLO_REFERENCIA = "QA-STR-001"
COD_SOLO_OBJETIVO = "QA-STR-002"
COD_TIPO_DISTINTO = "QA-STR-003"
COD_LONGITUD = "QA-STR-004"


def aplanar_estructura(datos: Any, ruta: str = "$") -> dict[str, set[str]]:
    """Devuelve {ruta: {tipos}} recorriendo objetos y listas.

    Los elementos de una lista se unifican bajo la ruta `...[]`, de modo que dos listas con
    distinta cantidad de elementos pero igual forma no generan diferencias estructurales.
    """
    resultado: dict[str, set[str]] = {ruta: {_tipo_json(datos)}}
    if isinstance(datos, dict):
        for clave, valor in datos.items():
            for r, t in aplanar_estructura(valor, f"{ruta}.{clave}").items():
                resultado.setdefault(r, set()).update(t)
    elif isinstance(datos, list):
        for valor in datos:
            for r, t in aplanar_estructura(valor, f"{ruta}[]").items():
                resultado.setdefault(r, set()).update(t)
    return resultado


def _longitudes_listas(datos: Any, ruta: str = "$") -> dict[str, int]:
    res: dict[str, int] = {}
    if isinstance(datos, dict):
        for k, v in datos.items():
            res.update(_longitudes_listas(v, f"{ruta}.{k}"))
    elif isinstance(datos, list):
        res[ruta] = len(datos)
    return res


def comparar_estructuras(entrada: EntradaComparacion) -> ResultadoHerramienta:
    ref, obj = entrada.referencia, entrada.objetivo
    estr_ref = aplanar_estructura(ref.datos)
    estr_obj = aplanar_estructura(obj.datos)
    comunes = dict(regla=entrada.regla, fuente=entrada.fuente)
    hallazgos: list[Hallazgo] = []

    for ruta in sorted(set(estr_ref) - set(estr_obj)):
        hallazgos.append(
            Hallazgo(
                codigo=COD_SOLO_REFERENCIA,
                severidad=entrada.severidad_faltantes,
                archivo=obj.nombre,
                ruta=ruta,
                descripcion=f"La propiedad {ruta} existe en '{ref.nombre}' pero falta en '{obj.nombre}'.",
                recomendacion="Agregar la propiedad al archivo destino o confirmar que es intencional.",
                **comunes,
            )
        )
    for ruta in sorted(set(estr_obj) - set(estr_ref)):
        hallazgos.append(
            Hallazgo(
                codigo=COD_SOLO_OBJETIVO,
                severidad=entrada.severidad_sobrantes,
                archivo=obj.nombre,
                ruta=ruta,
                descripcion=f"La propiedad {ruta} existe en '{obj.nombre}' pero no en '{ref.nombre}'.",
                recomendacion="Eliminarla del archivo destino o incorporarla al archivo de referencia.",
                **comunes,
            )
        )
    for ruta in sorted(set(estr_ref) & set(estr_obj)):
        if estr_ref[ruta] != estr_obj[ruta]:
            hallazgos.append(
                Hallazgo(
                    codigo=COD_TIPO_DISTINTO,
                    severidad=Severidad.ERROR,
                    archivo=obj.nombre,
                    ruta=ruta,
                    descripcion=(
                        f"Tipo distinto en {ruta}: '{ref.nombre}' tiene {sorted(estr_ref[ruta])} "
                        f"y '{obj.nombre}' tiene {sorted(estr_obj[ruta])}."
                    ),
                    recomendacion="Unificar el tipo de dato entre ambos archivos.",
                    **comunes,
                )
            )
    long_ref, long_obj = _longitudes_listas(ref.datos), _longitudes_listas(obj.datos)
    for ruta in sorted(set(long_ref) & set(long_obj)):
        if long_ref[ruta] != long_obj[ruta]:
            hallazgos.append(
                Hallazgo(
                    codigo=COD_LONGITUD,
                    severidad=Severidad.INFO,
                    archivo=obj.nombre,
                    ruta=ruta,
                    descripcion=f"La lista {ruta} tiene {long_ref[ruta]} elementos en '{ref.nombre}' y {long_obj[ruta]} en '{obj.nombre}'.",
                    recomendacion="Verificar que no falten ni sobren elementos traducidos.",
                    **comunes,
                )
            )
    return ResultadoHerramienta(herramienta="comparar_estructuras", archivos=[ref.nombre, obj.nombre], hallazgos=hallazgos)
