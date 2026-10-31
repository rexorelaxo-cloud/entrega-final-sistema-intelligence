"""Pruebas de los modelos Pydantic."""

import pytest
from pydantic import ValidationError

from app.models.ejecucion import DecisionHumana, SupervisorDecision
from app.models.hallazgos import Hallazgo, Severidad, consolidar_hallazgos, resumir
from app.models.solicitud import ArchivoEntrada, SolicitudAnalisis


def hallazgo(sev=Severidad.ERROR, codigo="X-1", ruta="$.a", archivo="a.json", regla="R-1") -> Hallazgo:
    return Hallazgo(codigo=codigo, severidad=sev, archivo=archivo, ruta=ruta, descripcion="d", regla=regla)


def test_solicitud_normaliza_cms():
    assert SolicitudAnalisis(cms_destino="  Sitecore ").cms_destino == "sitecore"
    assert SolicitudAnalisis(cms_destino="").cms_destino is None


def test_solicitud_rechaza_cms_desconocido():
    with pytest.raises(ValidationError):
        SolicitudAnalisis(cms_destino="wordpress")


def test_archivo_entrada_usa_solo_nombre_base():
    assert ArchivoEntrada(nombre="../../etc/passwd.json", contenido="{}").nombre == "passwd.json"
    assert ArchivoEntrada(nombre="C:\\datos\\a.json", contenido="{}").nombre == "a.json"


def test_decision_humana_exige_algo_decidido():
    with pytest.raises(ValidationError):
        DecisionHumana()
    assert DecisionHumana(aplicar_opcionales=False).aplicar_opcionales is False


def test_decision_humana_rechaza_cms_invalido():
    with pytest.raises(ValidationError):
        DecisionHumana(cms="drupal")


def test_supervisor_decision_rechaza_destino_inexistente():
    with pytest.raises(ValidationError):
        SupervisorDecision(intencion="analizar_archivos", siguiente="inventado", razon="x")


def test_hallazgo_es_inmutable():
    h = hallazgo()
    with pytest.raises(ValidationError):
        h.codigo = "otro"


def test_consolidar_elimina_duplicados_y_conserva_la_mayor_severidad():
    lista = [hallazgo(Severidad.WARNING), hallazgo(Severidad.ERROR), hallazgo(Severidad.INFO, codigo="X-2")]
    res = consolidar_hallazgos(lista)
    assert len(res) == 2
    assert res[0].severidad == Severidad.ERROR and res[0].codigo == "X-1"
    assert res[1].severidad == Severidad.INFO


def test_resumir_cuenta_y_decide_aptitud():
    r = resumir([hallazgo(Severidad.WARNING), hallazgo(Severidad.INFO, codigo="X-2")])
    assert (r.errores, r.advertencias, r.informativos, r.apto_para_importar) == (0, 1, 1, True)
    assert resumir([hallazgo()]).apto_para_importar is False
