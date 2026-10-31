"""Recuperación semántica de reglas y guías. Independiente de los agentes y de la ingestión."""

from app.models.reglas import (
    Aplicabilidad,
    FragmentoRecuperado,
    FuenteRef,
    ReglaAplicable,
    Verificacion,
)
from app.observability.tracing import registrar_documentos, span
from app.rag.embeddings import Embedder
from app.rag.store import VectorStore, esquema_desde_metadatos


def fuente_de(fr: FragmentoRecuperado) -> FuenteRef:
    m = fr.fragmento.metadatos
    return FuenteRef(
        doc_id=str(m.get("doc_id", "")),
        ruta=str(m.get("ruta", "")),
        chunk_id=fr.fragmento.chunk_id,
        seccion=str(m["seccion"]) if "seccion" in m else None,
    )


def a_regla(fr: FragmentoRecuperado) -> ReglaAplicable | None:
    """Convierte un fragmento de tipo 'regla' en ReglaAplicable. Las guías devuelven None."""
    m = fr.fragmento.metadatos
    if m.get("tipo") != "regla":
        return None
    return ReglaAplicable(
        rule_id=str(m["rule_id"]),
        titulo=str(m.get("titulo", "")),
        cms=str(m.get("cms", "generico")),
        aplicabilidad=Aplicabilidad(str(m["aplicabilidad"])),
        severidad=str(m["severidad"]).upper(),
        verificacion=Verificacion(str(m["verificacion"])),
        descripcion=fr.fragmento.texto,
        esquema=esquema_desde_metadatos(m),
        fuente=fuente_de(fr),
        score=fr.score,
    )


class Retriever:
    def __init__(self, store: VectorStore, embedder: Embedder, top_k: int, min_score: float):
        self._store = store
        self._embedder = embedder
        self._top_k = top_k
        self._min_score = min_score

    async def cms_disponibles(self) -> list[str]:
        """CMS con documentación en la base de conocimiento (sin contar las reglas genéricas)."""
        return [c for c in await self._store.listar_cms() if c != "generico"]

    async def completar_reglas(self, cms: list[str]) -> list[FragmentoRecuperado]:
        """Todas las reglas documentadas de los CMS indicados, sin depender del ranking de similitud.

        Un validador debe aplicar el conjunto completo de reglas de su CMS: perder una regla obligatoria por quedar
        fuera del top-k produciría falsos "APTO". Se marcan con score 0.0 para distinguirlas de lo recuperado
        semánticamente.
        """
        with span("rag.completar_reglas", "RETRIEVER", **{"rag.cms": ",".join(cms)}) as sp:
            fragmentos = await self._store.obtener_reglas(cms)
            sp.set_attribute("rag.devueltos", len(fragmentos))
            return [FragmentoRecuperado(fragmento=f, score=0.0) for f in fragmentos]

    async def buscar(self, consulta: str, cms: str | None = None, k: int | None = None) -> list[FragmentoRecuperado]:
        """Busca fragmentos relevantes. Con `cms` se restringe a ese CMS y a las reglas genéricas."""
        k = k or self._top_k
        filtro = [cms, "generico"] if cms else None
        with span("rag.recuperar", "RETRIEVER", **{"input.value": consulta, "rag.cms": cms, "rag.top_k": k}) as sp:
            vector = (await self._embedder.embed([consulta]))[0]
            candidatos = await self._store.buscar(vector, k, filtro)
            resultado = [c for c in candidatos if c.score >= self._min_score]
            sp.set_attribute("rag.candidatos", len(candidatos))
            sp.set_attribute("rag.devueltos", len(resultado))
            registrar_documentos(
                sp,
                [
                    {
                        "id": r.fragmento.chunk_id,
                        "contenido": r.fragmento.texto,
                        "score": r.score,
                        "metadatos": {"ruta": r.fragmento.metadatos.get("ruta")},
                    }
                    for r in resultado
                ],
            )
            return resultado
