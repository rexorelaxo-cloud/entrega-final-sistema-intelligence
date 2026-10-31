"""Pruebas de las herramientas de QA deterministas (sin LLM, sin red, sin Redis)."""

import json

from app.models.hallazgos import Severidad
from app.tools.entradas import ArchivoParseado, EntradaComparacion, EntradaEsquema, EntradaIdiomas, EntradaSintaxis
from app.tools.esquema import (
    detectar_propiedades_faltantes,
    detectar_propiedades_inesperadas,
    validar_contra_esquema,
    validar_tipos,
)
from app.tools.estructura import comparar_estructuras
from app.tools.idiomas import detectar_inconsistencias_idiomas
from app.tools.sintaxis import validar_sintaxis_json

ESQUEMA = {
    "type": "object",
    "required": ["id", "fields"],
    "properties": {
        "id": {"type": "string", "pattern": "^[0-9]+$"},
        "fields": {"type": "object", "required": ["Title"], "properties": {"Title": {"type": "string"}}},
    },
    "additionalProperties": False,
}


def esquema(datos, **kw) -> EntradaEsquema:
    return EntradaEsquema(nombre="a.json", datos=datos, esquema=ESQUEMA, regla="R-1", fuente="doc.md", **kw)


# --- sintaxis -----------------------------------------------------------------------------------
def test_sintaxis_valida_devuelve_datos():
    r = validar_sintaxis_json(EntradaSintaxis(nombre="a.json", contenido='{"a": 1}'))
    assert r.valido and r.datos == {"a": 1} and not r.hallazgos


def test_sintaxis_invalida_informa_linea_y_columna():
    r = validar_sintaxis_json(EntradaSintaxis(nombre="a.json", contenido='{\n  "a": 1,\n}'))
    assert not r.valido
    h = r.hallazgos[0]
    assert h.codigo == "QA-SYN-001" and h.severidad == Severidad.ERROR
    # la posición exacta depende de la versión de Python: 3.13 señala la coma sobrante (línea 2) y 3.12 la llave (línea 3)
    assert h.linea in {2, 3} and h.columna >= 1 and f"línea {h.linea}, columna {h.columna}" == h.ruta


def test_sintaxis_archivo_vacio():
    r = validar_sintaxis_json(EntradaSintaxis(nombre="a.json", contenido="  \n"))
    assert r.hallazgos[0].codigo == "QA-SYN-002"


def test_sintaxis_acepta_bom_y_advierte_claves_duplicadas():
    r = validar_sintaxis_json(EntradaSintaxis(nombre="a.json", contenido='\ufeff{"a": 1, "a": 2}'))
    assert r.valido and r.datos == {"a": 2}
    assert [h.codigo for h in r.hallazgos] == ["QA-SYN-003"] and r.hallazgos[0].severidad == Severidad.WARNING


# --- esquema, faltantes, inesperadas y tipos -----------------------------------------------------
def test_esquema_datos_validos_sin_hallazgos():
    assert validar_contra_esquema(esquema({"id": "123", "fields": {"Title": "x"}})).hallazgos == []


def test_faltantes_reporta_ruta_regla_y_fuente():
    r = detectar_propiedades_faltantes(esquema({"fields": {}}))
    rutas = {h.ruta for h in r.hallazgos}
    assert rutas == {"$.id", "$.fields.Title"}
    h = r.hallazgos[0]
    assert h.codigo == "QA-REQ-001" and h.severidad == Severidad.ERROR
    assert h.regla == "R-1" and h.fuente == "doc.md" and h.recomendacion


def test_inesperadas_reporta_cada_propiedad_extra():
    r = detectar_propiedades_inesperadas(esquema({"id": "1", "fields": {"Title": "x"}, "z": 1, "y": 2}))
    assert sorted(h.ruta for h in r.hallazgos) == ["$.y", "$.z"]
    assert all(h.codigo == "QA-PROP-001" and h.severidad == Severidad.WARNING for h in r.hallazgos)


def test_tipos_reporta_tipo_esperado_y_encontrado():
    r = validar_tipos(esquema({"id": "1", "fields": {"Title": 5}}))
    assert len(r.hallazgos) == 1
    h = r.hallazgos[0]
    assert h.codigo == "QA-TYPE-001" and h.ruta == "$.fields.Title"
    assert "string" in h.descripcion and "integer" in h.descripcion


def test_esquema_completo_clasifica_y_las_vistas_filtran():
    datos = {"id": "abc", "fields": {"Title": 5}, "extra": 1}
    codigos = {h.codigo for h in validar_contra_esquema(esquema(datos)).hallazgos}
    assert codigos == {"QA-SCH-001", "QA-TYPE-001", "QA-PROP-001"}  # pattern, tipo, inesperada
    assert {h.codigo for h in validar_tipos(esquema(datos)).hallazgos} == {"QA-TYPE-001"}


def test_severidad_maxima_de_la_regla_limita_los_hallazgos():
    r = validar_contra_esquema(esquema({"fields": {}}, severidad_maxima=Severidad.WARNING))
    assert r.hallazgos and all(h.severidad == Severidad.WARNING for h in r.hallazgos)


def test_esquema_invalido_se_informa_sin_lanzar_excepcion():
    e = EntradaEsquema(nombre="a.json", datos={}, esquema={"type": "no-existe"}, regla="R-9")
    r = validar_contra_esquema(e)
    assert r.hallazgos[0].codigo == "QA-SCH-000"


def test_las_herramientas_no_modifican_los_datos():
    datos = {"id": "1", "fields": {"Title": "x"}, "z": 1}
    copia = json.loads(json.dumps(datos))
    validar_contra_esquema(esquema(datos))
    assert datos == copia


# --- estructura -----------------------------------------------------------------------------------
def comparar(a, b):
    return comparar_estructuras(
        EntradaComparacion(
            referencia=ArchivoParseado(nombre="a.json", datos=a), objetivo=ArchivoParseado(nombre="b.json", datos=b), regla="G-2"
        )
    )


def test_estructuras_iguales_sin_diferencias():
    assert not [h for h in comparar({"a": 1, "l": [{"x": 1}]}, {"a": 2, "l": [{"x": 5}, {"x": 6}]}).hallazgos if h.codigo != "QA-STR-004"]


def test_estructuras_detecta_faltantes_sobrantes_y_tipos():
    r = comparar({"a": 1, "b": {"c": "x"}}, {"a": "1", "b": {"d": "x"}})
    por_codigo = {h.codigo: h.ruta for h in r.hallazgos}
    assert por_codigo["QA-STR-001"] == "$.b.c"
    assert por_codigo["QA-STR-002"] == "$.b.d"
    assert por_codigo["QA-STR-003"] == "$.a"


def test_estructuras_informa_diferencia_de_longitud_de_listas():
    r = comparar({"l": [1, 2, 3]}, {"l": [1]})
    assert [h.codigo for h in r.hallazgos] == ["QA-STR-004"] and r.hallazgos[0].severidad == Severidad.INFO


# --- idiomas ---------------------------------------------------------------------------------------
def idiomas(*pares, **kw):
    return detectar_inconsistencias_idiomas(
        EntradaIdiomas(archivos=[ArchivoParseado(nombre=n, datos=d) for n, d in pares], regla="G-3", **kw)
    )


def test_idiomas_detecta_marcadores_vacios_y_etiquetas():
    es = {"language": "es-AR", "body": "<b>Hola {0}</b>", "t": "Texto largo aquí", "v": "algo"}
    en = {"language": "en-US", "body": "<b>Hello</b>", "t": "Text", "v": ""}
    r = idiomas(("a.es-AR.json", es), ("a.en-US.json", en))
    codigos = {(h.codigo, h.ruta) for h in r.hallazgos}
    assert ("QA-LANG-003", "$.body") in codigos
    assert ("QA-LANG-002", "$.v") in codigos


def test_idiomas_detecta_codigo_declarado_distinto_al_del_nombre():
    r = idiomas(("a.es-AR.json", {"language": "es-AR"}), ("a.en-US.json", {"language": "fr-FR"}))
    assert "QA-LANG-006" in {h.codigo for h in r.hallazgos}


def test_idiomas_detecta_dos_archivos_del_mismo_idioma():
    r = idiomas(("a.json", {"language": "es-AR"}), ("b.json", {"language": "es-AR"}))
    assert "QA-LANG-007" in {h.codigo for h in r.hallazgos}


def test_idiomas_no_marca_identificadores_tecnicos_como_sin_traducir():
    r = idiomas(
        ("a.es-AR.json", {"language": "es-AR", "id": "3f2504e0-4f89", "t": "Hola mundo cruel"}),
        ("a.en-US.json", {"language": "en-US", "id": "3f2504e0-4f89", "t": "Hola mundo cruel"}),
    )
    rutas = [h.ruta for h in r.hallazgos if h.codigo == "QA-LANG-005"]
    assert rutas == ["$.t"]
