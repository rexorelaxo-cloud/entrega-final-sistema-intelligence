"""Pruebas de la política de reglas/HITL y de las transiciones del Supervisor."""

import pytest

from app.agents.politica import decision_responde_a, inferir_cms, resolver_reglas
from app.agents.supervisor import decidir_por_reglas, nodo_supervisor, validar_decision
from app.graph.state import Dependencias
from app.models.ejecucion import DecisionHumana, Intencion, RevisionHumana, SupervisorDecision
from app.models.solicitud import SolicitudAnalisis
from app.rag.retriever import a_regla
from app.services.llm import LLMError
from tests.unit.fakes import LLMFalso, supervisor_razonable

SITECORE = {"itemId": "x", "language": "es-AR", "template": "T", "fields": {"Title": "t", "Body": "b"}}
UMBRACO = {"key": "x", "culture": "es-AR", "contentType": "t", "properties": {"title": "t", "bodyText": "b"}}
AMBIGUO = {"id": "x", "lang": "es-AR"}


@pytest.fixture
async def todas(retriever):
    return [a_regla(f) for f in await retriever.completar_reglas(["sitecore", "umbraco", "generico"])]


# --- política ---------------------------------------------------------------------------------------
def test_inferir_cms_por_estructura(todas):
    assert inferir_cms(todas, [SITECORE]) == "sitecore"
    assert inferir_cms(todas, [UMBRACO]) == "umbraco"
    assert inferir_cms(todas, [AMBIGUO]) is None
    assert inferir_cms(todas, [SITECORE, UMBRACO]) is None  # mezcla: ambigüedad real


def test_cms_indicado_filtra_reglas_y_pide_decision_por_opcionales(todas):
    res = resolver_reglas(todas, SolicitudAnalisis(cms_destino="sitecore"), {}, [SITECORE])
    assert res.revision is not None and res.revision.tipo == "regla_opcional"
    assert res.revision.contexto["reglas_opcionales"][0]["rule_id"] == "SC-004"


def test_opcionales_aplicadas_u_omitidas_segun_decision(todas):
    aplicar = resolver_reglas(todas, SolicitudAnalisis(cms_destino="sitecore", aplicar_reglas_opcionales=True), {}, [SITECORE])
    assert aplicar.revision is None and "SC-004" in {r.rule_id for r in aplicar.efectivas}
    omitir = resolver_reglas(todas, SolicitudAnalisis(cms_destino="sitecore"), {"aplicar_opcionales": False}, [SITECORE])
    assert omitir.revision is None and "SC-004" not in {r.rule_id for r in omitir.efectivas}
    assert {r.cms for r in aplicar.efectivas} == {"sitecore", "generico"}


def test_la_regla_informativa_ninguna_no_genera_pregunta(todas):
    # GEN-004 es opcional pero informativa: no debe provocar HITL por sí sola
    solo_genericas = [r for r in todas if r.cms == "generico"]
    res = resolver_reglas(solo_genericas, SolicitudAnalisis(), {}, [AMBIGUO])
    assert res.revision is None and res.cms_aplicado is None


def test_sin_cms_se_deduce_por_estructura_sin_molestar_al_usuario(todas):
    res = resolver_reglas(todas, SolicitudAnalisis(aplicar_reglas_opcionales=False), {}, [UMBRACO])
    assert res.revision is None and res.cms_aplicado == "umbraco"
    assert any("inferido por la estructura" in n for n in res.notas)


def test_sin_cms_y_estructura_ambigua_pide_decision_humana(todas):
    res = resolver_reglas(todas, SolicitudAnalisis(), {}, [AMBIGUO])
    assert res.revision.tipo == "cms_ambiguo" and res.revision.opciones == ["sitecore", "umbraco"]
    assert res.efectivas == []


def test_la_decision_humana_resuelve_la_ambiguedad(todas):
    res = resolver_reglas(todas, SolicitudAnalisis(aplicar_reglas_opcionales=True), {"cms": "umbraco"}, [AMBIGUO])
    assert res.revision is None and res.cms_aplicado == "umbraco"


def test_decision_responde_a_la_pregunta_pendiente():
    cms = RevisionHumana(tipo="cms_ambiguo", pregunta="?", opciones=["sitecore", "umbraco"])
    opc = RevisionHumana(tipo="regla_opcional", pregunta="?", opciones=["aplicar", "omitir"])
    assert decision_responde_a(cms, DecisionHumana(cms="umbraco")) is None
    assert "cms" in decision_responde_a(cms, DecisionHumana(aplicar_opcionales=True))
    assert "opciones" in decision_responde_a(
        RevisionHumana(tipo="cms_ambiguo", pregunta="?", opciones=["sitecore"]), DecisionHumana(cms="umbraco")
    )
    assert decision_responde_a(opc, DecisionHumana(aplicar_opcionales=False)) is None
    assert "aplicar_opcionales" in decision_responde_a(opc, DecisionHumana(cms="sitecore"))


# --- supervisor: decisiones por reglas ----------------------------------------------------------------------
def est(**kw):
    return {"tipo_turno": "analisis", "n_pasos": 0, "pasos": [], **kw}


def test_el_supervisor_no_sigue_una_secuencia_fija():
    d = lambda e: decidir_por_reglas(e, 12).siguiente  # noqa: E731
    assert d(est()) == "research"
    assert d(est(research_hecho=True)) == "analysis"
    assert d(est(research_hecho=True, analisis_hecho=True)) == "synthesis"
    assert d(est(research_hecho=True, revision_pendiente={"tipo": "x"})) == "human_review"
    assert d(est(sintesis_hecha=True)) == "FINISH"


def test_alcance_solo_sintaxis_omite_research():
    e = est(consulta="solo validar sintaxis por favor")
    dec = decidir_por_reglas(e, 12)
    assert dec.alcance == "solo_sintaxis" and dec.siguiente == "analysis"


def test_consulta_de_reglas_no_ejecuta_analisis():
    e = {"tipo_turno": "chat", "consulta": "¿qué exige Umbraco?", "n_pasos": 0}
    assert decidir_por_reglas(e, 12).siguiente == "research"
    assert decidir_por_reglas({**e, "research_hecho": True}, 12).siguiente == "synthesis"


def test_pregunta_sobre_el_informe_va_directo_a_synthesis():
    e = {"tipo_turno": "chat", "consulta": "explicame los errores del informe", "informe": {"estado": "NO_APTO"}, "n_pasos": 0}
    dec = decidir_por_reglas(e, 12)
    assert dec.intencion == Intencion.EXPLICAR_RESULTADO and dec.siguiente == "synthesis"


def test_limite_de_pasos_fuerza_el_cierre():
    assert decidir_por_reglas(est(n_pasos=12), 12).siguiente == "synthesis"
    assert decidir_por_reglas(est(n_pasos=12, sintesis_hecha=True), 12).siguiente == "FINISH"


# --- supervisor: validación de decisiones propuestas por un LLM ----------------------------------------------
def dec(siguiente, intencion=Intencion.ANALIZAR_ARCHIVOS, alcance="completo"):
    return SupervisorDecision(intencion=intencion, alcance=alcance, siguiente=siguiente, razon="r")


@pytest.mark.parametrize(
    "decision,estado",
    [
        (dec("analysis"), est()),  # sin research previo
        (dec("synthesis"), est(research_hecho=True)),  # sin análisis
        (dec("FINISH"), est()),  # sin síntesis
        (dec("human_review"), est()),  # sin revisión pendiente
        (dec("research"), est(research_hecho=True)),  # research repetido
        (dec("analysis"), est(research_hecho=True, analisis_hecho=True)),  # análisis repetido
        (dec("analysis"), est(research_hecho=True, revision_pendiente={"x": 1})),  # debe ir a human_review
        (dec("research", Intencion.CONSULTAR_REGLAS), est()),  # intención incoherente en turno de análisis
        (dec("analysis", Intencion.ANALIZAR_ARCHIVOS), {"tipo_turno": "chat", "n_pasos": 0}),  # chat no analiza
        (dec("research"), est(sintesis_hecha=True)),
    ],
)
def test_transiciones_imposibles_son_rechazadas(decision, estado):
    assert validar_decision(decision, estado, 12) is not None


def test_transiciones_coherentes_son_aceptadas():
    assert validar_decision(dec("research"), est(), 12) is None
    assert validar_decision(dec("analysis", alcance="solo_sintaxis"), est(), 12) is None
    assert validar_decision(dec("human_review"), est(revision_pendiente={"x": 1}), 12) is None
    assert validar_decision(dec("FINISH"), est(sintesis_hecha=True), 12) is None


async def test_nodo_supervisor_con_llm_valido_usa_la_decision_del_llm(deps):
    d = Dependencias(deps.settings, deps.retriever, LLMFalso({SupervisorDecision: supervisor_razonable}))
    out = await nodo_supervisor(est(consulta="x"), d)
    assert out["siguiente"] == "research" and out["razon_supervisor"].startswith("[llm]")


async def test_nodo_supervisor_corrige_una_decision_invalida_del_llm(deps):
    malo = SupervisorDecision(intencion=Intencion.ANALIZAR_ARCHIVOS, siguiente="FINISH", razon="terminar ya")
    d = Dependencias(deps.settings, deps.retriever, LLMFalso({SupervisorDecision: malo}))
    out = await nodo_supervisor(est(), d)
    assert out["siguiente"] == "research" and out["correcciones_supervisor"] == 1
    assert out["razon_supervisor"].startswith("[reglas (corregido)]")


async def test_nodo_supervisor_cae_a_reglas_si_el_llm_falla(deps):
    d = Dependencias(deps.settings, deps.retriever, LLMFalso({SupervisorDecision: LLMError("sin cuota")}))
    out = await nodo_supervisor(est(), d)
    assert out["siguiente"] == "research" and "LLM no disponible" in out["razon_supervisor"]
