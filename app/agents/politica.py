"""Política determinista para decidir qué reglas aplican y cuándo hace falta una persona.

Si una decisión puede resolverse con los datos disponibles (parámetros de la solicitud, estructura de los
archivos), se resuelve aquí sin molestar al usuario. Solo se pide intervención humana ante ambigüedad real.
"""

from dataclasses import dataclass, field
from typing import Any

from app.models.ejecucion import DecisionHumana, RevisionHumana
from app.models.reglas import Aplicabilidad, ReglaAplicable, Verificacion
from app.models.solicitud import SolicitudAnalisis


@dataclass
class ResolucionReglas:
    efectivas: list[ReglaAplicable] = field(default_factory=list)
    cms_aplicado: str | None = None
    revision: RevisionHumana | None = None
    notas: list[str] = field(default_factory=list)


def _claves_raiz_requeridas(regla: ReglaAplicable) -> set[str]:
    return set((regla.esquema or {}).get("required", []))


def inferir_cms(reglas: list[ReglaAplicable], documentos: list[Any]) -> str | None:
    """Devuelve el único CMS cuyas propiedades raíz obligatorias están en todos los archivos.

    Devuelve None si ninguno o más de uno cumple (ambigüedad real).
    """
    por_cms: dict[str, set[str]] = {}
    for r in reglas:
        if r.verificacion == Verificacion.ESQUEMA and r.cms != "generico":
            por_cms.setdefault(r.cms, set()).update(_claves_raiz_requeridas(r))
    objetos = [d for d in documentos if isinstance(d, dict)]
    if not objetos:
        return None
    candidatos = [cms for cms, req in por_cms.items() if req and all(req <= set(obj) for obj in objetos)]
    return candidatos[0] if len(candidatos) == 1 else None


def resolver_reglas(
    reglas: list[ReglaAplicable],
    solicitud: SolicitudAnalisis,
    decisiones: dict,
    documentos: list[Any],
) -> ResolucionReglas:
    res = ResolucionReglas()
    cms = solicitud.cms_destino or decisiones.get("cms")
    cms_con_esquema = sorted({r.cms for r in reglas if r.verificacion == Verificacion.ESQUEMA and r.cms != "generico"})

    if cms is None:
        if len(cms_con_esquema) == 1:
            cms = cms_con_esquema[0]
            res.notas.append(f"CMS destino deducido de las reglas recuperadas: {cms}.")
        elif len(cms_con_esquema) > 1:
            inferido = inferir_cms(reglas, documentos)
            if inferido:
                cms = inferido
                res.notas.append(f"CMS destino inferido por la estructura de los archivos: {cms}.")
            else:
                res.revision = RevisionHumana(
                    tipo="cms_ambiguo",
                    pregunta=(
                        "No se indicó el CMS de destino y la estructura de los archivos no permite deducirlo. "
                        "¿Con qué reglas de CMS debe validarse el contenido?"
                    ),
                    opciones=cms_con_esquema,
                    contexto={"cms_candidatos": cms_con_esquema},
                )
                return res

    res.cms_aplicado = cms
    efectivas = [r for r in reglas if r.cms in {"generico", cms}] if cms else [r for r in reglas if r.cms == "generico"]

    opcionales = [r for r in efectivas if r.aplicabilidad == Aplicabilidad.OPCIONAL and r.verificacion != Verificacion.NINGUNA]
    aplicar = solicitud.aplicar_reglas_opcionales
    if aplicar is None:
        aplicar = decisiones.get("aplicar_opcionales")
    if opcionales and aplicar is None:
        res.revision = RevisionHumana(
            tipo="regla_opcional",
            pregunta="Hay reglas opcionales aplicables. ¿Desea aplicarlas (se reportan como advertencias)?",
            opciones=["aplicar", "omitir"],
            contexto={"reglas_opcionales": [{"rule_id": r.rule_id, "titulo": r.titulo} for r in opcionales]},
        )
        return res
    if aplicar is False:
        ids_opcionales = {r.rule_id for r in opcionales}
        efectivas = [r for r in efectivas if r.rule_id not in ids_opcionales]
        res.notas.append("Reglas opcionales omitidas por decisión del usuario.")
    res.efectivas = efectivas
    return res


def decision_responde_a(revision: RevisionHumana, decision: DecisionHumana) -> str | None:
    """Devuelve un mensaje de error si la decisión no contesta la pregunta pendiente; None si es válida."""
    if revision.tipo == "cms_ambiguo":
        if decision.cms is None:
            return "La decisión debe indicar 'cms' (sitecore o umbraco)."
        if decision.cms not in revision.opciones:
            return f"El CMS '{decision.cms}' no está entre las opciones: {', '.join(revision.opciones)}."
    if revision.tipo == "regla_opcional" and decision.aplicar_opcionales is None:
        return "La decisión debe indicar 'aplicar_opcionales' (true o false)."
    return None
