"""Pruebas del sistema RAG: ingestión, fragmentación, metadatos, recuperación y fuentes."""

import json
import shutil
from types import SimpleNamespace

import pytest

from app.models.reglas import Aplicabilidad, Verificacion
from app.rag.embeddings import HashEmbedder, OpenAIEmbedder
from app.rag.ingestion import DocumentoInvalido, cargar_fragmentos, fragmentar_documento, ingestar
from app.rag.retriever import Retriever, a_regla
from app.rag.store import MemoryVectorStore
from tests.conftest import RAIZ

IDS_REGLAS = {"GEN-001", "GEN-002", "GEN-003", "GEN-004", "SC-001", "SC-002", "SC-003", "SC-004", "UM-001", "UM-002", "UM-003", "UM-004"}


def test_ingestion_separa_reglas_y_guias_con_metadatos():
    fragmentos = cargar_fragmentos(RAIZ / "knowledge", 1200)
    reglas = {f.metadatos["rule_id"]: f for f in fragmentos if f.metadatos["tipo"] == "regla"}
    assert set(reglas) == IDS_REGLAS
    assert any(f.metadatos["tipo"] == "guia" for f in fragmentos)
    sc1 = reglas["SC-001"].metadatos
    assert sc1["cms"] == "sitecore" and sc1["aplicabilidad"] == "obligatoria" and sc1["verificacion"] == "esquema"
    assert sc1["ruta"] == "cms/sitecore-demo.md"
    assert json.loads(sc1["esquema_json"])["required"] == ["itemId", "language", "template", "fields"]


def test_todo_el_material_esta_marcado_como_demostracion_educativa():
    assert {f.metadatos["origen"] for f in cargar_fragmentos(RAIZ / "knowledge", 1200)} == {"demostracion_educativa"}


def test_los_ids_de_fragmento_son_unicos():
    ids = [f.chunk_id for f in cargar_fragmentos(RAIZ / "knowledge", 1200)]
    assert len(ids) == len(set(ids))


def test_las_guias_largas_se_dividen_por_tamano(tmp_path):
    (tmp_path / "g.md").write_text(
        "---\ndoc_id: g\ncms: generico\norigen: demostracion_educativa\n---\n## Guía\n\n"
        + "\n\n".join(f"Párrafo {i} " + "x" * 80 for i in range(10)),
        encoding="utf-8",
    )
    fr = fragmentar_documento(tmp_path / "g.md", tmp_path, 300)
    assert len(fr) > 1 and all(len(f.texto) <= 300 + 20 for f in fr)


@pytest.mark.parametrize(
    "contenido,mensaje",
    [
        ("## Sin encabezado", "encabezado"),
        ("---\ndoc_id: x\n---\n## A", "cms"),
        ("---\ndoc_id: x\ncms: c\norigen: o\n---\n## REGLA X-1 — T\n- aplicabilidad: obligatoria\n", "severidad"),
        (
            "---\ndoc_id: x\ncms: c\norigen: o\n---\n## REGLA X-1 — T\n- aplicabilidad: obligatoria\n- severidad: ERROR\n- verificacion: esquema\n```json-schema\n{no es json}\n```",
            "JSON válido",
        ),
    ],
)
def test_documentos_mal_formados_fallan_con_mensaje_claro(tmp_path, contenido, mensaje):
    (tmp_path / "d.md").write_text(contenido, encoding="utf-8")
    with pytest.raises(DocumentoInvalido, match=mensaje):
        cargar_fragmentos(tmp_path, 1200)


async def test_directorio_vacio_falla(tmp_path):
    with pytest.raises(DocumentoInvalido):
        await ingestar(tmp_path, MemoryVectorStore(), HashEmbedder(), 1200)


async def test_la_base_se_amplia_agregando_documentos_sin_tocar_codigo(tmp_path):
    shutil.copytree(RAIZ / "knowledge", tmp_path / "k")
    (tmp_path / "k" / "cms" / "nuevo.md").write_text(
        "---\ndoc_id: nuevo-cms\ncms: nuevocms\norigen: demostracion_educativa\n---\n"
        "## REGLA NC-001 — Campo codigoSucursal obligatorio\n- aplicabilidad: obligatoria\n- severidad: ERROR\n- verificacion: esquema\n\n"
        'El campo codigoSucursal es obligatorio.\n\n```json-schema\n{"required": ["codigoSucursal"]}\n```\n',
        encoding="utf-8",
    )
    store, emb = MemoryVectorStore(), HashEmbedder()
    await ingestar(tmp_path / "k", store, emb, 1200)
    r = Retriever(store, emb, 5, 0.0)
    assert "nuevocms" in await r.cms_disponibles()
    resultados = await r.buscar("codigoSucursal obligatorio", cms="nuevocms")
    assert resultados[0].fragmento.metadatos["rule_id"] == "NC-001"


async def test_recuperacion_semantica_devuelve_la_regla_correcta_con_fuente(retriever):
    resultados = await retriever.buscar("propiedades obligatorias itemId language template fields Title Body", cms="sitecore", k=5)
    ids = [r.fragmento.metadatos.get("rule_id") for r in resultados]
    assert "SC-001" in ids[:3]
    assert not any(str(i).startswith("UM-") for i in ids)  # el filtro por CMS excluye Umbraco
    sc1 = next(r for r in resultados if r.fragmento.metadatos.get("rule_id") == "SC-001")
    regla = a_regla(sc1)
    assert regla.fuente.ruta == "cms/sitecore-demo.md" and regla.fuente.doc_id == "sitecore-demo"
    assert regla.fuente.chunk_id == "sitecore-demo::SC-001"
    assert regla.aplicabilidad == Aplicabilidad.OBLIGATORIA and regla.verificacion == Verificacion.ESQUEMA
    assert regla.esquema["required"][0] == "itemId"


async def test_el_filtro_por_cms_incluye_reglas_genericas(retriever):
    resultados = await retriever.buscar("consistencia entre idiomas marcadores", cms="umbraco", k=12)
    cms = {r.fragmento.metadatos["cms"] for r in resultados}
    assert cms <= {"umbraco", "generico"} and "generico" in cms


async def test_las_guias_no_se_convierten_en_reglas(retriever):
    resultados = await retriever.buscar("misma estructura evaluada con distintas reglas", k=12)
    guias = [r for r in resultados if r.fragmento.metadatos["tipo"] == "guia"]
    assert guias and all(a_regla(g) is None for g in guias)


async def test_umbral_minimo_de_similitud_filtra_resultados(store):
    estricto = Retriever(store, HashEmbedder(), 12, 0.99)
    assert await estricto.buscar("propiedades obligatorias de sitecore") == []


async def test_completar_reglas_devuelve_todas_las_del_cms_sin_depender_del_ranking(retriever):
    res = await retriever.completar_reglas(["umbraco"])
    assert {r.fragmento.metadatos["rule_id"] for r in res} == {"UM-001", "UM-002", "UM-003", "UM-004"}
    assert all(r.score == 0.0 for r in res)


async def test_cms_disponibles_excluye_generico(retriever):
    assert await retriever.cms_disponibles() == ["sitecore", "umbraco"]


async def test_hash_embedder_es_determinista_y_normalizado():
    e = HashEmbedder()
    a, b = await e.embed(["Propiedad obligatoria"]), await e.embed(["Propiedad obligatoria"])
    assert a == b
    assert abs(sum(x * x for x in a[0]) - 1.0) < 1e-9


async def test_openai_embedder_conserva_el_orden_por_indice():
    llamadas = []

    async def crear(model, input):
        llamadas.append((model, input))
        return SimpleNamespace(data=[SimpleNamespace(index=1, embedding=[2.0]), SimpleNamespace(index=0, embedding=[1.0])])

    cliente = SimpleNamespace(embeddings=SimpleNamespace(create=crear))
    emb = OpenAIEmbedder(cliente, "text-embedding-3-small")
    assert await emb.embed(["a", "b"]) == [[1.0], [2.0]]
    assert llamadas == [("text-embedding-3-small", ["a", "b"])]
    assert await emb.embed([]) == []
