"""Analysis / QA Agent: decide qué comprobaciones corresponden y ejecuta las herramientas deterministas.

Las validaciones son código Python (app/tools). El LLM, si existe, solo ayuda a acotar el plan cuando el
usuario da una instrucción libre y a redactar observaciones; nunca produce ni altera hallazgos.
"""

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from app.agents.politica import resolver_reglas
from app.graph.state import Dependencias, EstadoQA
from app.models.ejecucion import CheckEjecutable, Interpretacion, PlanAnalisis
from app.models.hallazgos import Hallazgo, ResultadoHerramienta, Severidad
from app.models.reglas import ReglaAplicable, Verificacion
from app.models.solicitud import ArchivoEntrada, SolicitudAnalisis
from app.observability.tracing import span
from app.services.llm import LLMError
from app.tools.entradas import (
    ArchivoParseado,
    EntradaComparacion,
    EntradaEsquema,
    EntradaIdiomas,
    EntradaSintaxis,
)
from app.tools.esquema import (
    detectar_propiedades_faltantes,
    detectar_propiedades_inesperadas,
    validar_contra_esquema,
    validar_tipos,
)
from app.tools.estructura import comparar_estructuras
from app.tools.idiomas import detectar_inconsistencias_idiomas, idioma_declarado, idioma_en_nombre
from app.tools.sintaxis import validar_sintaxis_json

log = logging.getLogger(__name__)
C = CheckEjecutable
_CHECKS_ESQUEMA_PARCIALES: dict[CheckEjecutable, Callable[[EntradaEsquema], ResultadoHerramienta]] = {
    C.FALTANTES: detectar_propiedades_faltantes,
    C.INESPERADAS: detectar_propiedades_inesperadas,
    C.TIPOS: validar_tipos,
}


async def _ejecutar(nombre: str, fn: Callable[[Any], ResultadoHerramienta], entrada: BaseModel, **attrs: Any) -> ResultadoHerramienta:
    """Ejecuta una herramienta síncrona y CPU-bound fuera del event loop, dentro de un span TOOL."""
    with span(f"herramienta.{nombre}", "TOOL", **{"tool.name": nombre}, **attrs) as sp:
        resultado = await asyncio.to_thread(fn, entrada)
        sp.set_attribute("tool.hallazgos", len(resultado.hallazgos))
        return resultado


async def _plan(deps: Dependencias, solicitud: SolicitudAnalisis, alcance: str, n_validos: int) -> tuple[set[CheckEjecutable], str]:
    por_defecto = {C.SINTAXIS, C.ESQUEMA}
    if n_validos >= 2:
        por_defecto |= {C.ESTRUCTURA, C.IDIOMAS}
    if alcance == "solo_sintaxis":
        return {C.SINTAXIS}, "alcance solo_sintaxis"
    if deps.llm is None or not solicitud.instruccion:
        return por_defecto, "plan por defecto"
    sistema = (
        "Eres el agente de Análisis de un sistema de QA de localización. Según la instrucción del usuario elige las "
        "comprobaciones a ejecutar: sintaxis, esquema (incluye faltantes, inesperadas y tipos), faltantes, inesperadas, "
        "tipos, estructura (comparación entre archivos) e idiomas (consistencia entre idiomas). "
        "Si la instrucción no restringe el alcance, elige todas."
    )
    try:
        plan = await deps.llm.estructurado(
            PlanAnalisis, sistema, json.dumps({"instruccion": solicitud.instruccion, "archivos_validos": n_validos}, ensure_ascii=False)
        )
    except LLMError as exc:
        log.warning("Plan por LLM no disponible, se usa el plan por defecto: %s", exc)
        return por_defecto, "plan por defecto (LLM no disponible)"
    checks = set(plan.checks) | {C.SINTAXIS}
    if n_validos < 2:
        checks -= {C.ESTRUCTURA, C.IDIOMAS}
    return checks, f"plan del LLM: {plan.razon}"


def _referencia(parseados: list[ArchivoParseado], idioma_origen: str | None) -> ArchivoParseado:
    if idioma_origen:
        objetivo = idioma_origen.replace("_", "-").lower()
        for a in parseados:
            if (idioma_declarado(a.datos) or idioma_en_nombre(a.nombre)) == objetivo:
                return a
    return parseados[0]


def _aviso(codigo: str, descripcion: str, recomendacion: str) -> Hallazgo:
    return Hallazgo(codigo=codigo, severidad=Severidad.INFO, archivo="(análisis)", descripcion=descripcion, recomendacion=recomendacion)


async def nodo_analysis(estado: EstadoQA, deps: Dependencias) -> dict:
    solicitud = SolicitudAnalisis.model_validate(estado.get("solicitud") or {})
    archivos = [ArchivoEntrada.model_validate(a) for a in estado.get("archivos") or []]
    reglas = [ReglaAplicable.model_validate(r) for r in estado.get("reglas") or []]
    decisiones = estado.get("decisiones_humanas") or {}
    alcance = estado.get("alcance") or "completo"
    paso_prev = estado.get("pasos", [])

    with span("agente.analysis", "AGENT", **{"analysis.archivos": len(archivos)}) as sp:
        hallazgos: list[Hallazgo] = []
        parseados: list[ArchivoParseado] = []

        # 1) Sintaxis: siempre, sobre cada archivo.
        resultados = await asyncio.gather(
            *(
                _ejecutar(
                    "validar_sintaxis_json",
                    validar_sintaxis_json,
                    EntradaSintaxis(nombre=a.nombre, contenido=a.contenido),
                    archivo=a.nombre,
                )
                for a in archivos
            )
        )
        gen001 = next((r for r in reglas if r.rule_id == "GEN-001"), None)
        for res in resultados:
            for h in res.hallazgos:
                hallazgos.append(h.model_copy(update={"fuente": gen001.fuente.ruta}) if gen001 else h)
            if getattr(res, "valido", False):
                parseados.append(ArchivoParseado(nombre=res.archivos[0], datos=res.datos))  # type: ignore[attr-defined]

        notas: list[str] = []
        efectivas: list[ReglaAplicable] = []
        cms_aplicado: str | None = None
        if alcance != "solo_sintaxis" and not parseados:
            notas.append("Ningún archivo tiene JSON válido: no se aplicaron reglas de esquema ni se consultó al usuario.")
        if alcance != "solo_sintaxis" and parseados:
            # 2) Política: qué reglas aplican y si hace falta una persona.
            resolucion = resolver_reglas(reglas, solicitud, decisiones, [p.datos for p in parseados])
            if resolucion.revision is not None:
                sp.set_attribute("analysis.revision_pendiente", resolucion.revision.tipo)
                return {
                    "analisis_hecho": False,
                    "revision_pendiente": resolucion.revision.model_dump(mode="json"),
                    "pasos": [*paso_prev, f"analysis→revisión humana ({resolucion.revision.tipo})"],
                }
            efectivas, cms_aplicado, notas = resolucion.efectivas, resolucion.cms_aplicado, resolucion.notas

        checks, razon_plan = await _plan(deps, solicitud, alcance, len(parseados))
        notas.append(f"Comprobaciones ejecutadas: {', '.join(sorted(c.value for c in checks))} ({razon_plan}).")

        # 3) Comprobaciones dirigidas por las reglas recuperadas (RAG).
        con_esquema = [r for r in efectivas if r.verificacion == Verificacion.ESQUEMA and r.esquema]
        if alcance != "solo_sintaxis" and parseados and not con_esquema and C.ESQUEMA in checks:
            hallazgos.append(
                _aviso(
                    "QA-RAG-001",
                    "No se recuperaron reglas de esquema aplicables; solo se validó la sintaxis.",
                    "Indicar el CMS de destino o ampliar la base de conocimiento.",
                )
            )
        for r in con_esquema:
            sev_max = Severidad(r.severidad) if r.severidad in Severidad.__members__ else Severidad.ERROR
            for a in parseados:
                entrada = EntradaEsquema(
                    nombre=a.nombre, datos=a.datos, esquema=r.esquema or {}, regla=r.rule_id, fuente=r.fuente.ruta, severidad_maxima=sev_max
                )
                if C.ESQUEMA in checks:
                    res = await _ejecutar("validar_contra_esquema", validar_contra_esquema, entrada, archivo=a.nombre, regla=r.rule_id)
                    hallazgos.extend(res.hallazgos)
                else:
                    for check, fn in _CHECKS_ESQUEMA_PARCIALES.items():
                        if check in checks:
                            res = await _ejecutar(fn.__name__, fn, entrada, archivo=a.nombre, regla=r.rule_id)
                            hallazgos.extend(res.hallazgos)

        if len(parseados) >= 2 and alcance != "solo_sintaxis":
            ref = _referencia(parseados, solicitud.idioma_origen)
            regla_estr = next((r for r in efectivas if r.verificacion == Verificacion.COMPARACION_ESTRUCTURAL), None)
            if C.ESTRUCTURA in checks:
                if regla_estr is None:
                    hallazgos.append(
                        _aviso(
                            "QA-RAG-002",
                            "No se recuperó la regla de consistencia estructural; no se comparó la estructura.",
                            "Verificar la base de conocimiento (regla GEN-002).",
                        )
                    )
                else:
                    for a in parseados:
                        if a is ref:
                            continue
                        res = await _ejecutar(
                            "comparar_estructuras",
                            comparar_estructuras,
                            EntradaComparacion(referencia=ref, objetivo=a, regla=regla_estr.rule_id, fuente=regla_estr.fuente.ruta),
                            referencia=ref.nombre,
                            objetivo=a.nombre,
                        )
                        hallazgos.extend(res.hallazgos)
            regla_idi = next((r for r in efectivas if r.verificacion == Verificacion.CONSISTENCIA_IDIOMAS), None)
            if C.IDIOMAS in checks:
                if regla_idi is None:
                    hallazgos.append(
                        _aviso(
                            "QA-RAG-003",
                            "No se recuperó la regla de consistencia entre idiomas; no se revisó el contenido traducido.",
                            "Verificar la base de conocimiento (regla GEN-003).",
                        )
                    )
                else:
                    ordenados = [ref, *[a for a in parseados if a is not ref]]
                    res = await _ejecutar(
                        "detectar_inconsistencias_idiomas",
                        detectar_inconsistencias_idiomas,
                        EntradaIdiomas(
                            archivos=ordenados, idioma_origen=solicitud.idioma_origen, regla=regla_idi.rule_id, fuente=regla_idi.fuente.ruta
                        ),
                        archivos=",".join(a.nombre for a in ordenados),
                    )
                    hallazgos.extend(res.hallazgos)

        # 4) Interpretación (opcional, solo texto): no modifica hallazgos.
        observaciones = list(notas)
        if deps.llm is not None and hallazgos:
            try:
                interp = await deps.llm.estructurado(
                    Interpretacion,
                    "Eres el agente de Análisis de un sistema de QA de localización. Resume en español, en pocas frases cortas, "
                    "los patrones relevantes de estos hallazgos. No inventes hallazgos ni reglas.",
                    json.dumps([h.model_dump(mode="json") for h in hallazgos[:40]], ensure_ascii=False),
                )
                observaciones.extend(interp.observaciones)
            except LLMError as exc:
                log.warning("Interpretación por LLM no disponible: %s", exc)

        sp.set_attribute("analysis.hallazgos", len(hallazgos))

    return {
        "analisis_hecho": True,
        "hallazgos": [h.model_dump(mode="json") for h in hallazgos],
        "observaciones": observaciones,
        "cms_aplicado": cms_aplicado,
        "reglas_aplicadas": [r.rule_id for r in efectivas if r.verificacion != Verificacion.NINGUNA],
        "revision_pendiente": None,
        "pasos": [*paso_prev, f"analysis({len(hallazgos)} hallazgos)"],
    }
