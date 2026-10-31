"""Los cinco escenarios funcionales exigidos y pruebas del grafo completo (checkpointer en memoria, sin OpenAI)."""

import shutil

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.config.settings import Settings
from app.graph.builder import configuracion_hilo, construir_grafo, diagrama_mermaid
from app.graph.entrada import entrada_analisis, entrada_chat
from app.graph.state import Dependencias
from app.models.ejecucion import (
    InformeQA,
    Interpretacion,
    PlanAnalisis,
    RedaccionInforme,
    SeleccionReglas,
    SupervisorDecision,
)
from app.models.solicitud import ArchivoEntrada, SolicitudAnalisis
from app.rag.embeddings import HashEmbedder
from app.rag.ingestion import ingestar
from app.rag.retriever import Retriever
from app.rag.store import MemoryVectorStore
from tests.conftest import RAIZ, leer_fixture
from tests.unit.fakes import LLMFalso, supervisor_razonable


def archivo(nombre: str) -> ArchivoEntrada:
    return ArchivoEntrada(nombre=nombre, contenido=leer_fixture(nombre))


async def analizar(grafo, nombres, thread="t", **solicitud):
    estado = await grafo.ainvoke(
        entrada_analisis(SolicitudAnalisis(**solicitud), [archivo(n) for n in nombres]), configuracion_hilo(thread)
    )
    return estado


def informe(estado) -> InformeQA:
    return InformeQA.model_validate(estado["informe"])


def codigos(inf: InformeQA) -> set[tuple[str, str | None]]:
    return {(h.codigo, h.ruta) for h in inf.bloqueantes + inf.advertencias + inf.informativos}


SC = dict(cms_destino="sitecore", aplicar_reglas_opcionales=True)


# ===================== Los cinco escenarios obligatorios ============================================
async def test_prueba_1_json_valido_cumple_las_reglas_aplicables(grafo):
    estado = await analizar(grafo, ["sitecore_valido.es-AR.json"], **SC)
    inf = informe(estado)
    assert inf.estado == "APTO" and inf.estadisticas.errores == 0
    assert {"SC-001", "SC-002", "SC-003", "SC-004", "GEN-001"} <= set(inf.reglas_aplicadas)
    assert inf.cms_aplicado == "sitecore"


async def test_prueba_2_json_invalido_devuelve_hallazgo_con_ubicacion(grafo):
    estado = await analizar(grafo, ["json_invalido.json"], cms_destino="sitecore")
    inf = informe(estado)
    assert inf.estado == "NO_APTO"
    h = inf.bloqueantes[0]
    assert h.codigo == "QA-SYN-001" and h.linea in {7, 8} and h.columna >= 1  # 3.13 señala la coma (7), 3.12 la llave (8)
    assert h.archivo == "json_invalido.json" and h.regla == "GEN-001" and h.fuente == "reglas/reglas-generales.md"
    # no se consultó al usuario por reglas opcionales: no había nada válido que validar
    assert estado["revision_pendiente"] is None and "human_review" not in " ".join(estado["pasos"])


async def test_prueba_3_propiedades_obligatorias_faltantes(grafo):
    inf = informe(await analizar(grafo, ["sitecore_faltantes.json"], **SC))
    faltantes = {h.ruta for h in inf.bloqueantes if h.codigo == "QA-REQ-001"}
    assert faltantes == {"$.itemId", "$.fields.Body"}
    h = next(h for h in inf.bloqueantes if h.ruta == "$.itemId")
    assert h.regla == "SC-001" and h.fuente == "cms/sitecore-demo.md" and h.recomendacion
    assert inf.acciones_correctivas


async def test_prueba_4_inconsistencia_estructural_entre_archivos(grafo):
    inf = informe(
        await analizar(grafo, ["sitecore_valido.es-AR.json", "sitecore_fr_inconsistente.fr-FR.json"], idioma_origen="es-AR", **SC)
    )
    c = codigos(inf)
    assert ("QA-STR-001", "$.fields.MetaDescription") in c  # falta en francés
    assert ("QA-STR-002", "$.fields.Subtitle") in c  # sobra en francés
    assert ("QA-STR-003", "$.version") in c  # número vs cadena
    assert ("QA-LANG-003", "$.fields.Body") in c  # marcador {1} perdido en la traducción
    assert inf.estado == "NO_APTO"
    assert all(h.regla in {"GEN-002", "GEN-003", "SC-004", "SC-001", "SC-002", "SC-003"} for h in inf.bloqueantes + inf.advertencias)


async def test_prueba_5_rag_contextual_la_misma_estructura_se_evalua_con_reglas_distintas(grafo):
    """Una regla recuperada de la base de conocimiento decide el resultado: el mismo archivo es APTO para
    Sitecore y NO_APTO para Umbraco, y cada hallazgo cita la regla y el documento de origen."""
    como_sitecore = informe(await analizar(grafo, ["sitecore_valido.es-AR.json"], thread="a", **SC))
    como_umbraco = informe(
        await analizar(grafo, ["sitecore_valido.es-AR.json"], thread="b", cms_destino="umbraco", aplicar_reglas_opcionales=True)
    )
    assert como_sitecore.estado == "APTO"
    assert como_umbraco.estado == "NO_APTO"
    faltantes = {h.ruta for h in como_umbraco.bloqueantes if h.codigo == "QA-REQ-001"}
    assert faltantes == {"$.key", "$.culture", "$.contentType", "$.properties"}
    assert {h.ruta for h in como_umbraco.advertencias if h.codigo == "QA-PROP-001"} >= {"$.itemId", "$.language"}
    assert {h.fuente for h in como_umbraco.bloqueantes} == {"cms/umbraco-demo.md"}
    assert {f.doc_id for f in como_umbraco.fuentes} >= {"umbraco-demo", "reglas-generales"}


async def test_la_documentacion_cambia_la_validacion_sin_tocar_el_codigo(settings, tmp_path):
    """Si se edita la regla en knowledge/, el resultado cambia: la validación depende de lo recuperado."""
    shutil.copytree(RAIZ / "knowledge", tmp_path / "k")
    doc = tmp_path / "k" / "cms" / "sitecore-demo.md"
    doc.write_text(
        doc.read_text(encoding="utf-8").replace('"required": ["Title", "Body"]', '"required": ["Title", "Body", "Summary"]'),
        encoding="utf-8",
    )
    store, emb = MemoryVectorStore(), HashEmbedder()
    await ingestar(tmp_path / "k", store, emb, 1200)
    g = construir_grafo(Dependencias(settings, Retriever(store, emb, 12, 0.15), None), InMemorySaver())
    inf = informe(await analizar(g, ["sitecore_valido.es-AR.json"], **SC))
    assert ("QA-REQ-001", "$.fields.Summary") in codigos(inf)


# ===================== Grafo, estado y flujos ===================================================================
async def test_la_ruta_depende_de_la_solicitud(grafo):
    solo_sintaxis = await grafo.ainvoke(
        entrada_analisis(SolicitudAnalisis(instruccion="solo validar sintaxis"), [archivo("sitecore_valido.es-AR.json")]),
        configuracion_hilo("s1"),
    )
    assert [p.split("(")[0] for p in solo_sintaxis["pasos"] if not p.startswith("supervisor")] == ["analysis", "synthesis"]
    completo = await analizar(grafo, ["sitecore_valido.es-AR.json"], thread="s2", **SC)
    assert [p.split("(")[0] for p in completo["pasos"] if not p.startswith("supervisor")] == ["research", "analysis", "synthesis"]


async def test_consulta_de_reglas_usa_research_y_no_analisis(grafo):
    e = await grafo.ainvoke(entrada_chat("¿Qué propiedades obligatorias exige Umbraco?"), configuracion_hilo("c1"))
    assert [p.split("(")[0] for p in e["pasos"] if not p.startswith("supervisor")] == ["research", "synthesis"]
    # la búsqueda es semántica (en pruebas, léxica por hashing): se verifica la familia de reglas, no el orden exacto
    assert "(umbraco," in e["respuesta"] and "cms/umbraco-demo.md" in e["respuesta"] and "SC-" not in e["respuesta"]
    assert e["fuentes"] and e["fuentes"][0]["ruta"].endswith(".md")


async def test_chat_con_contexto_explica_el_informe_previo_del_thread(grafo):
    await analizar(grafo, ["sitecore_faltantes.json"], thread="ctx", **SC)
    e = await grafo.ainvoke(entrada_chat("¿Por qué falló el análisis? Explicame el informe"), configuracion_hilo("ctx"))
    assert e["intencion"] == "explicar_resultado"
    assert "NO_APTO" in e["respuesta"] and "$.itemId" in e["respuesta"]
    assert [p.split("(")[0] for p in e["pasos"] if not p.startswith("supervisor")] == ["synthesis"]


async def test_chat_sin_informe_previo_no_inventa_resultados(grafo):
    e = await grafo.ainvoke(entrada_chat("explicame el informe"), configuracion_hilo("nuevo"))
    assert "Todavía no hay un informe" in e["respuesta"] or e["intencion"] == "consultar_reglas"


async def test_estado_recuperable_desde_el_checkpointer_por_thread_id(grafo):
    await analizar(grafo, ["sitecore_valido.es-AR.json"], thread="rec", **SC)
    snap = await grafo.aget_state(configuracion_hilo("rec"))
    assert snap.values["informe"]["estado"] == "APTO" and snap.next == ()
    assert [m["rol"] for m in snap.values["mensajes"]] == ["usuario", "asistente"]
    otro = await grafo.aget_state(configuracion_hilo("otro-thread"))
    assert not otro.values  # los threads están aislados
    historial = [s async for s in grafo.aget_state_history(configuracion_hilo("rec"))]
    assert len(historial) > 3


async def test_los_mensajes_se_acumulan_entre_turnos(grafo):
    await analizar(grafo, ["sitecore_valido.es-AR.json"], thread="m", **SC)
    await grafo.ainvoke(entrada_chat("¿Qué es SC-004?"), configuracion_hilo("m"))
    snap = await grafo.aget_state(configuracion_hilo("m"))
    assert len(snap.values["mensajes"]) == 4


async def test_los_archivos_originales_no_se_modifican(grafo):
    antes = leer_fixture("sitecore_faltantes.json")
    entrada = entrada_analisis(SolicitudAnalisis(**SC), [archivo("sitecore_faltantes.json")])
    contenido_enviado = entrada["archivos"][0]["contenido"]
    await grafo.ainvoke(entrada, configuracion_hilo("inm"))
    assert contenido_enviado == antes and leer_fixture("sitecore_faltantes.json") == antes
    snap = await grafo.aget_state(configuracion_hilo("inm"))
    assert snap.values["archivos"][0]["contenido"] == antes


# ===================== Human-in-the-Loop ======================================================================
async def test_hitl_pausa_por_ambiguedad_y_reanuda_con_la_decision(grafo):
    cfg = configuracion_hilo("hitl")
    primera = await grafo.ainvoke(
        entrada_analisis(SolicitudAnalisis(aplicar_reglas_opcionales=True), [archivo("estructura_ambigua.json")]), cfg
    )
    assert primera["__interrupt__"][0].value["tipo"] == "cms_ambiguo"
    snap = await grafo.aget_state(cfg)
    assert snap.next == ("human_review",) and not snap.values.get("informe")

    final = await grafo.ainvoke(Command(resume={"cms": "sitecore"}), cfg)
    assert "__interrupt__" not in final
    inf = informe(final)
    assert inf.cms_aplicado == "sitecore" and inf.decisiones_humanas == {"cms": "sitecore"}
    assert any(h.regla == "SC-001" for h in inf.bloqueantes)
    assert "human_review(cms_ambiguo)" in final["pasos"]


async def test_hitl_dos_pausas_consecutivas_cms_y_regla_opcional(grafo):
    cfg = configuracion_hilo("hitl2")
    await grafo.ainvoke(entrada_analisis(SolicitudAnalisis(), [archivo("estructura_ambigua.json")]), cfg)
    segunda = await grafo.ainvoke(Command(resume={"cms": "umbraco"}), cfg)
    assert segunda["__interrupt__"][0].value["tipo"] == "regla_opcional"
    final = await grafo.ainvoke(Command(resume={"aplicar_opcionales": False}), cfg)
    inf = informe(final)
    assert inf.cms_aplicado == "umbraco" and "UM-004" not in inf.reglas_aplicadas
    assert inf.decisiones_humanas == {"cms": "umbraco", "aplicar_opcionales": False}


async def test_hitl_una_respuesta_que_no_contesta_vuelve_a_preguntar(grafo):
    cfg = configuracion_hilo("hitl3")
    await grafo.ainvoke(entrada_analisis(SolicitudAnalisis(), [archivo("estructura_ambigua.json")]), cfg)
    r = await grafo.ainvoke(Command(resume={"aplicar_opcionales": True}), cfg)  # no responde a la pregunta del CMS
    pendiente = r["__interrupt__"][0].value
    assert pendiente["tipo"] == "cms_ambiguo" and "error" in pendiente
    assert (await grafo.aget_state(cfg)).next == ("human_review",)


async def test_hitl_no_se_pide_si_se_puede_resolver_de_forma_determinista(grafo):
    # CMS indicado + opcionales decididas + archivo ambiguo => no hay nada que preguntar
    e = await analizar(grafo, ["estructura_ambigua.json"], thread="det", cms_destino="sitecore", aplicar_reglas_opcionales=False)
    assert "__interrupt__" not in e and e["informe"]
    # estructura inequívoca de Umbraco => se infiere el CMS
    e2 = await analizar(grafo, ["umbraco_valido.es-AR.json"], thread="det2", aplicar_reglas_opcionales=True)
    assert "__interrupt__" not in e2 and informe(e2).cms_aplicado == "umbraco" and informe(e2).estado == "APTO"
    assert any("inferido por la estructura" in o for o in informe(e2).observaciones)


# ===================== Límites y tolerancia a fallos ==========================================================
async def test_el_limite_de_pasos_nunca_produce_un_apto_falso(retriever):
    """Si el grafo se corta por el límite de pasos antes de analizar, un archivo válido no puede salir APTO."""
    s = Settings(_env_file=None, llm_mode="deterministic", tracing_enabled=False, graph_max_steps=1)
    corto = construir_grafo(Dependencias(s, retriever, None), InMemorySaver())
    estado = await analizar(corto, ["sitecore_valido.es-AR.json"], **SC)
    inf = informe(estado)
    assert inf.estado == "INCOMPLETO" and estado["hallazgos"] == []
    assert "no se completó" in inf.resumen


# ===================== Con LLM simulado ======================================================================
def deps_con(llm, settings, retriever) -> Dependencias:
    return Dependencias(settings, retriever, llm)


async def test_con_llm_el_flujo_completa_y_el_codigo_calcula_los_conteos(settings, retriever):
    llm = LLMFalso(
        {
            SupervisorDecision: supervisor_razonable,
            SeleccionReglas: lambda u: SeleccionReglas(rule_ids=["SC-001", "GEN-001", "SC-003"], razon="aplican"),
            Interpretacion: Interpretacion(observaciones=["Faltan propiedades obligatorias."]),
            RedaccionInforme: RedaccionInforme(resumen="Resumen redactado por el LLM.", acciones_correctivas=["Agregar itemId."]),
            PlanAnalisis: PlanAnalisis(checks=["sintaxis", "esquema"], razon="completo"),
        }
    )
    g = construir_grafo(deps_con(llm, settings, retriever), InMemorySaver())
    estado = await analizar(g, ["sitecore_faltantes.json"], **SC)
    inf = informe(estado)
    assert inf.resumen == "Resumen redactado por el LLM." and inf.acciones_correctivas == ["Agregar itemId."]
    assert "Faltan propiedades obligatorias." in inf.observaciones
    assert inf.estadisticas.errores == 2 and inf.estado == "NO_APTO"  # calculado por código, no por el LLM
    assert estado["razon_supervisor"].startswith("[llm]")
    # el LLM no puede quitar reglas obligatorias: se conservan todas las obligatorias del CMS y las generales
    assert {"SC-001", "SC-002", "SC-003", "GEN-001", "GEN-002", "GEN-003"} <= set(inf.reglas_aplicadas)


async def test_el_llm_no_puede_inventar_reglas(settings, retriever):
    llm = LLMFalso(
        {SupervisorDecision: supervisor_razonable, SeleccionReglas: SeleccionReglas(rule_ids=["SC-999", "XX-1"], razon="inventadas")}
    )
    g = construir_grafo(deps_con(llm, settings, retriever), InMemorySaver())
    inf = informe(await analizar(g, ["sitecore_valido.es-AR.json"], **SC))
    assert "SC-999" not in inf.reglas_aplicadas and "SC-001" in inf.reglas_aplicadas  # se descartó y se conservó lo real


async def test_si_el_llm_falla_el_sistema_sigue_en_modo_determinista(settings, retriever):
    from app.services.llm import LLMError

    llm = LLMFalso({}, texto=LLMError("sin cuota"))  # todas las llamadas estructuradas fallan por falta de respuesta
    g = construir_grafo(deps_con(llm, settings, retriever), InMemorySaver())
    estado = await analizar(g, ["sitecore_faltantes.json"], **SC)
    inf = informe(estado)
    assert inf.estado == "NO_APTO" and inf.estadisticas.errores == 2
    assert "LLM no disponible" in estado["razon_supervisor"] or estado["correcciones_supervisor"] >= 0
    assert llm.llamadas  # se intentó usar el LLM


async def test_el_llm_acota_el_plan_pero_siempre_se_ejecuta_la_sintaxis(settings, retriever):
    llm = LLMFalso({SupervisorDecision: supervisor_razonable, PlanAnalisis: PlanAnalisis(checks=["faltantes"], razon="solo faltantes")})
    g = construir_grafo(deps_con(llm, settings, retriever), InMemorySaver())
    estado = await grafo_con_instruccion(g, "revisá solo las propiedades faltantes")
    inf = informe(estado)
    assert {h.codigo for h in inf.bloqueantes} == {"QA-REQ-001"}
    assert any("faltantes" in o and "sintaxis" in o for o in inf.observaciones)


async def grafo_con_instruccion(g, instruccion):
    sol = SolicitudAnalisis(instruccion=instruccion, **SC)
    return await g.ainvoke(entrada_analisis(sol, [archivo("sitecore_faltantes.json")]), configuracion_hilo("plan"))


def test_el_diagrama_mermaid_refleja_el_grafo_compilado(grafo):
    mermaid = diagrama_mermaid(grafo)
    for nodo in ("supervisor", "research", "analysis", "human_review", "synthesis"):
        assert nodo in mermaid
    assert "-.->" in mermaid  # aristas condicionales desde el supervisor


async def test_el_llm_no_puede_descartar_reglas_obligatorias_y_causar_un_falso_apto(settings, retriever):
    """Regresión: con OpenAI real el escenario de inconsistencia estructural dio APTO porque el LLM no eligió
    GEN-002/GEN-003 y esas comprobaciones se omitieron en silencio."""
    llm = LLMFalso({SeleccionReglas: SeleccionReglas(rule_ids=["SC-001", "SC-002", "SC-003", "GEN-001"], razon="solo CMS")})
    g = construir_grafo(deps_con(llm, settings, retriever), InMemorySaver())
    inf = informe(await analizar(g, ["sitecore_valido.es-AR.json", "sitecore_fr_inconsistente.fr-FR.json"], idioma_origen="es-AR", **SC))
    assert inf.estado == "NO_APTO"
    assert ("QA-STR-001", "$.fields.MetaDescription") in codigos(inf)
    assert not any(h.codigo.startswith("QA-RAG") for h in inf.informativos)


async def test_el_llm_si_puede_descartar_reglas_opcionales(settings, retriever):
    llm = LLMFalso({SeleccionReglas: SeleccionReglas(rule_ids=["SC-001"], razon="sin opcionales")})
    g = construir_grafo(deps_con(llm, settings, retriever), InMemorySaver())
    estado = await g.ainvoke(
        entrada_analisis(SolicitudAnalisis(cms_destino="sitecore"), [archivo("sitecore_valido.es-AR.json")]), configuracion_hilo("opc")
    )
    # SC-004 es opcional y el LLM no la eligió: no se aplica ni se pregunta por ella
    assert "__interrupt__" not in estado and "SC-004" not in informe(estado).reglas_aplicadas
