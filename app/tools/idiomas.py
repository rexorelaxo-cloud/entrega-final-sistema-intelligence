"""Herramienta 7: detección de inconsistencias entre archivos de distintos idiomas."""

import re
from typing import Any

from app.models.hallazgos import Hallazgo, ResultadoHerramienta, Severidad
from app.tools.entradas import ArchivoParseado, EntradaComparacion, EntradaIdiomas
from app.tools.estructura import comparar_estructuras

CLAVES_IDIOMA = ("language", "culture", "lang", "locale")
_PATRON_IDIOMA_ARCHIVO = re.compile(r"(?<![A-Za-z])([a-z]{2}(?:[-_][A-Za-z]{2})?)\.json$", re.IGNORECASE)
_PATRON_MARCADORES = re.compile(r"\{\{?\w*\}?\}|%[sdif]|%\(\w+\)s")
_PATRON_ETIQUETAS = re.compile(r"</?\w+[^>]*>")


def normalizar_idioma(codigo: str) -> str:
    return codigo.replace("_", "-").strip().lower()


def idioma_declarado(datos: Any) -> str | None:
    if isinstance(datos, dict):
        for clave in CLAVES_IDIOMA:
            valor = datos.get(clave)
            if isinstance(valor, str) and valor.strip():
                return normalizar_idioma(valor)
    return None


def idioma_en_nombre(nombre: str) -> str | None:
    m = _PATRON_IDIOMA_ARCHIVO.search(nombre)
    return normalizar_idioma(m.group(1)) if m else None


def _hojas_texto(datos: Any, ruta: str = "$") -> dict[str, str]:
    res: dict[str, str] = {}
    if isinstance(datos, dict):
        for k, v in datos.items():
            if k in CLAVES_IDIOMA:
                continue
            res.update(_hojas_texto(v, f"{ruta}.{k}"))
    elif isinstance(datos, list):
        for i, v in enumerate(datos):
            res.update(_hojas_texto(v, f"{ruta}[{i}]"))
    elif isinstance(datos, str):
        res[ruta] = datos
    return res


def _h(codigo: str, sev: Severidad, archivo: str, ruta: str | None, desc: str, rec: str, e: EntradaIdiomas) -> Hallazgo:
    return Hallazgo(
        codigo=codigo,
        severidad=sev,
        archivo=archivo,
        ruta=ruta,
        descripcion=desc,
        recomendacion=rec,
        regla=e.regla,
        fuente=e.fuente,
    )


def detectar_inconsistencias_idiomas(entrada: EntradaIdiomas) -> ResultadoHerramienta:
    archivos = entrada.archivos
    hallazgos: list[Hallazgo] = []

    idiomas: dict[str, str | None] = {}
    for a in archivos:
        declarado, en_nombre = idioma_declarado(a.datos), idioma_en_nombre(a.nombre)
        idiomas[a.nombre] = declarado or en_nombre
        if declarado and en_nombre and declarado != en_nombre:
            hallazgos.append(
                _h(
                    "QA-LANG-006",
                    Severidad.WARNING,
                    a.nombre,
                    "$.language",
                    f"El idioma declarado ('{declarado}') no coincide con el del nombre de archivo ('{en_nombre}').",
                    "Alinear el nombre del archivo con el idioma declarado en el contenido.",
                    entrada,
                )
            )
        if idiomas[a.nombre] is None:
            hallazgos.append(
                _h(
                    "QA-LANG-008",
                    Severidad.WARNING,
                    a.nombre,
                    None,
                    "No se pudo determinar el idioma del archivo (ni por contenido ni por nombre).",
                    "Agregar una propiedad de idioma (language/culture) o un sufijo de idioma en el nombre.",
                    entrada,
                )
            )

    # idiomas repetidos
    por_idioma: dict[str, list[str]] = {}
    for nombre, idioma in idiomas.items():
        if idioma:
            por_idioma.setdefault(idioma, []).append(nombre)
    for idioma, nombres in por_idioma.items():
        if len(nombres) > 1:
            hallazgos.append(
                _h(
                    "QA-LANG-007",
                    Severidad.WARNING,
                    nombres[1],
                    None,
                    f"Más de un archivo declara el idioma '{idioma}': {', '.join(nombres)}.",
                    "Verificar que cada idioma tenga un único archivo.",
                    entrada,
                )
            )

    # referencia: idioma de origen indicado, o el primer archivo
    origen = archivos[0]
    if entrada.idioma_origen:
        objetivo = normalizar_idioma(entrada.idioma_origen)
        origen = next((a for a in archivos if idiomas[a.nombre] == objetivo), archivos[0])
    otros: list[ArchivoParseado] = [a for a in archivos if a is not origen]

    hojas_origen = _hojas_texto(origen.datos)
    for destino in otros:
        # 1) estructura, reutilizando la herramienta de comparación
        if entrada.incluir_estructura:
            cmp = comparar_estructuras(EntradaComparacion(referencia=origen, objetivo=destino, regla=entrada.regla, fuente=entrada.fuente))
            for h in cmp.hallazgos:
                hallazgos.append(h.model_copy(update={"codigo": h.codigo.replace("QA-STR", "QA-LANG-S")}))

        # 2) contenido: vacíos, marcadores y etiquetas
        hojas_destino = _hojas_texto(destino.datos)
        for ruta, texto_origen in hojas_origen.items():
            if ruta not in hojas_destino:
                continue
            texto_destino = hojas_destino[ruta]
            if texto_origen.strip() and not texto_destino.strip():
                hallazgos.append(
                    _h(
                        "QA-LANG-002",
                        Severidad.WARNING,
                        destino.nombre,
                        ruta,
                        f"Valor vacío en '{destino.nombre}' mientras que '{origen.nombre}' tiene contenido.",
                        "Completar la traducción o confirmar que el campo debe quedar vacío.",
                        entrada,
                    )
                )
                continue
            marc_o, marc_d = sorted(_PATRON_MARCADORES.findall(texto_origen)), sorted(_PATRON_MARCADORES.findall(texto_destino))
            if marc_o != marc_d:
                hallazgos.append(
                    _h(
                        "QA-LANG-003",
                        Severidad.ERROR,
                        destino.nombre,
                        ruta,
                        f"Los marcadores de posición no coinciden: origen {marc_o}, destino {marc_d}.",
                        "Conservar exactamente los marcadores ({0}, {{nombre}}, %s) de la cadena original.",
                        entrada,
                    )
                )
            et_o, et_d = sorted(_PATRON_ETIQUETAS.findall(texto_origen)), sorted(_PATRON_ETIQUETAS.findall(texto_destino))
            if et_o != et_d:
                hallazgos.append(
                    _h(
                        "QA-LANG-004",
                        Severidad.WARNING,
                        destino.nombre,
                        ruta,
                        "Las etiquetas HTML del texto traducido no coinciden con las del texto de origen.",
                        "Revisar que las etiquetas se conserven y estén balanceadas.",
                        entrada,
                    )
                )
            if (
                texto_origen == texto_destino
                and len(texto_origen.split()) >= 2  # identificadores técnicos (GUID, plantilla) no se traducen
                and any(c.isalpha() for c in texto_origen)
                and not marc_o
            ):
                hallazgos.append(
                    _h(
                        "QA-LANG-005",
                        Severidad.INFO,
                        destino.nombre,
                        ruta,
                        "El texto es idéntico al de origen; podría estar sin traducir.",
                        "Confirmar si es un término que no se traduce.",
                        entrada,
                    )
                )

    return ResultadoHerramienta(
        herramienta="detectar_inconsistencias_idiomas",
        archivos=[a.nombre for a in archivos],
        hallazgos=hallazgos,
        metadatos={"idiomas": {k: v for k, v in idiomas.items()}, "origen": origen.nombre},
    )
