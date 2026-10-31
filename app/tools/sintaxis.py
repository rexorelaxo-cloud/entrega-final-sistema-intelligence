"""Herramienta 1: validación de sintaxis JSON (con línea y columna)."""

import json

from app.models.hallazgos import Hallazgo, Severidad
from app.tools.entradas import EntradaSintaxis, ResultadoSintaxis

_HERRAMIENTA = "validar_sintaxis_json"


def validar_sintaxis_json(entrada: EntradaSintaxis) -> ResultadoSintaxis:
    """Parsea el contenido y devuelve hallazgos de sintaxis. No modifica el contenido.

    Es una función pura y segura para ejecutarse en hilos concurrentes: no usa estado global.
    """
    texto = entrada.contenido.lstrip("﻿")

    if not texto.strip():
        return ResultadoSintaxis(
            herramienta=_HERRAMIENTA,
            archivos=[entrada.nombre],
            hallazgos=[
                Hallazgo(
                    codigo="QA-SYN-002",
                    severidad=Severidad.ERROR,
                    archivo=entrada.nombre,
                    descripcion="El archivo está vacío.",
                    regla="GEN-001",
                    recomendacion="Verificar la exportación: un archivo vacío no puede importarse.",
                )
            ],
        )

    duplicadas: set[str] = set()

    def hook(pares: list[tuple[str, object]]) -> dict:
        vistas: set[str] = set()
        for clave, _ in pares:
            if clave in vistas:
                duplicadas.add(clave)
            vistas.add(clave)
        return dict(pares)  # se conserva el último valor, igual que json.loads por defecto

    try:
        datos = json.loads(texto, object_pairs_hook=hook)
    except json.JSONDecodeError as exc:
        return ResultadoSintaxis(
            herramienta=_HERRAMIENTA,
            archivos=[entrada.nombre],
            hallazgos=[
                Hallazgo(
                    codigo="QA-SYN-001",
                    severidad=Severidad.ERROR,
                    archivo=entrada.nombre,
                    ruta=f"línea {exc.lineno}, columna {exc.colno}",
                    descripcion=f"JSON inválido: {exc.msg}.",
                    regla="GEN-001",
                    recomendacion="Corregir la sintaxis en la posición indicada (comas, comillas o llaves).",
                    linea=exc.lineno,
                    columna=exc.colno,
                )
            ],
        )

    hallazgos = [
        Hallazgo(
            codigo="QA-SYN-003",
            severidad=Severidad.WARNING,
            archivo=entrada.nombre,
            ruta=clave,
            descripcion=f"La clave '{clave}' aparece duplicada en un mismo objeto; solo se conserva el último valor.",
            regla="GEN-001",
            recomendacion="Eliminar o renombrar la clave duplicada.",
        )
        for clave in sorted(duplicadas)
    ]
    return ResultadoSintaxis(herramienta=_HERRAMIENTA, archivos=[entrada.nombre], hallazgos=hallazgos, datos=datos, valido=True)
