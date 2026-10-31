"""Synthesis / Reporting Agent: consolida hallazgos y redacta el informe o la respuesta en español.

Los conteos, la clasificación (bloqueante / advertencia / informativo) y el veredicto APTO/NO_APTO se calculan en
código. El LLM, si existe, solo redacta el resumen y las acciones correctivas a partir de esos datos.
"""

import json
import logging

from app.graph.state import Dependencias, EstadoQA
from app.models.ejecucion import InformeQA, Intencion, RedaccionInforme
from app.models.hallazgos import Hallazgo, Severidad, consolidar_hallazgos, resumir
from app.models.reglas import FuenteRef, ReglaAplicable
from app.observability.tracing import span
from app.services.llm import LLMError

log = logging.getLogger(__name__)


def _fuentes_unicas(fuentes: list[FuenteRef]) -> list[FuenteRef]:
    vistas: dict[str, FuenteRef] = {}
    for f in fuentes:
        vistas.setdefault(f.chunk_id, f)
    return list(vistas.values())


def _acciones_deterministas(hallazgos: list[Hallazgo], maximo: int = 10) -> list[str]:
    acciones: list[str] = []
    for h in hallazgos:
        if h.severidad == Severidad.INFO or not h.recomendacion:
            continue
        texto = f"{h.archivo}{' ' + h.ruta if h.ruta else ''}: {h.recomendacion}"
        if texto not in acciones:
            acciones.append(texto)
        if len(acciones) >= maximo:
            break
    return acciones


def _resumen_determinista(informe_parcial: dict) -> str:
    e = informe_parcial["estadisticas"]
    n = len(informe_parcial["archivos_analizados"])
    if e.apto_para_importar:
        cola = f" Se registraron {e.advertencias} advertencia(s) para revisión." if e.advertencias else ""
        return f"Los {n} archivo(s) analizados no tienen errores bloqueantes y pueden importarse.{cola}"
    return (
        f"Se encontraron {e.errores} error(es) bloqueante(s) y {e.advertencias} advertencia(s) en {n} archivo(s). "
        "No se recomienda importar hasta corregir los errores bloqueantes."
    )


async def _informe(estado: EstadoQA, deps: Dependencias) -> InformeQA:
    hallazgos = consolidar_hallazgos([Hallazgo.model_validate(h) for h in estado.get("hallazgos") or []])
    reglas = [ReglaAplicable.model_validate(r) for r in estado.get("reglas") or []]
    aplicadas = set(estado.get("reglas_aplicadas") or [])
    fuentes = _fuentes_unicas([r.fuente for r in reglas if r.rule_id in aplicadas])
    resumen_qa = resumir(hallazgos)
    base = dict(
        archivos_analizados=[a["nombre"] for a in estado.get("archivos") or []],
        estadisticas=resumen_qa,
    )
    resumen, acciones = _resumen_determinista(base), _acciones_deterministas(hallazgos)

    if not estado.get("analisis_hecho"):
        # Cierre por límite de pasos u otra causa antes de validar: nunca se declara APTO sin análisis.
        return InformeQA(
            estado="INCOMPLETO",
            resumen="El análisis no se completó (se alcanzó el límite de pasos de ejecución); no se puede afirmar que los archivos sean aptos.",
            observaciones=estado.get("observaciones") or [],
            **base,
        )

    if deps.llm is not None:
        try:
            redaccion = await deps.llm.estructurado(
                RedaccionInforme,
                "Eres el agente de Reporte de un sistema de QA de localización. Redacta en español técnico claro, para un "
                "Localization Engineer, un resumen breve (2-4 frases) y una lista de acciones correctivas priorizadas. "
                "Usa SOLO los hallazgos entregados; no inventes errores, reglas ni cifras.",
                json.dumps(
                    {
                        "estadisticas": resumen_qa.model_dump(),
                        "cms": estado.get("cms_aplicado"),
                        "hallazgos": [h.model_dump(mode="json") for h in hallazgos[:50]],
                    },
                    ensure_ascii=False,
                ),
            )
            resumen = redaccion.resumen or resumen
            acciones = redaccion.acciones_correctivas or acciones
        except LLMError as exc:
            log.warning("Redacción por LLM no disponible, se usa el texto determinista: %s", exc)

    return InformeQA(
        estado="APTO" if resumen_qa.apto_para_importar else "NO_APTO",
        resumen=resumen,
        cms_aplicado=estado.get("cms_aplicado"),
        reglas_aplicadas=sorted(aplicadas),
        fuentes=fuentes,
        bloqueantes=[h for h in hallazgos if h.severidad == Severidad.ERROR],
        advertencias=[h for h in hallazgos if h.severidad == Severidad.WARNING],
        informativos=[h for h in hallazgos if h.severidad == Severidad.INFO],
        acciones_correctivas=acciones,
        observaciones=estado.get("observaciones") or [],
        decisiones_humanas=estado.get("decisiones_humanas") or {},
        **base,
    )


def _respuesta_reglas_determinista(estado: EstadoQA) -> tuple[str, list[FuenteRef]]:
    reglas = [ReglaAplicable.model_validate(r) for r in estado.get("reglas") or []]
    guias = estado.get("guias") or []
    if not reglas and not guias:
        return ("No encontré documentación relevante en la base de conocimiento para esa consulta.", [])
    lineas = ["Según la base de conocimiento (documentación de demostración con fines educativos):"]
    for r in reglas[:6]:
        lineas.append(f"- {r.rule_id} ({r.cms}, {r.aplicabilidad.value}): {r.titulo.rstrip('.')}. Fuente: {r.fuente.ruta}.")
    for g in guias[:3]:
        lineas.append(f"- Guía «{g['fuente'].get('seccion')}». Fuente: {g['fuente']['ruta']}.")
    fuentes = _fuentes_unicas([r.fuente for r in reglas[:6]] + [FuenteRef.model_validate(g["fuente"]) for g in guias[:3]])
    return "\n".join(lineas), fuentes


def _respuesta_resultado_determinista(estado: EstadoQA) -> str:
    informe = estado.get("informe")
    if not informe:
        return "Todavía no hay un informe en este thread. Envíe archivos a /analyze para generarlo."
    i = InformeQA.model_validate(informe)
    lineas = [f"Último informe: {i.estado}. {i.resumen}"]
    for h in (i.bloqueantes + i.advertencias)[:8]:
        regla = f" (regla {h.regla})" if h.regla else ""
        lineas.append(f"- [{h.severidad.value}] {h.archivo} {h.ruta or ''}{regla}: {h.descripcion}")
    return "\n".join(lineas)


async def nodo_synthesis(estado: EstadoQA, deps: Dependencias) -> dict:
    intencion = Intencion(estado.get("intencion") or Intencion.CONSULTAR_REGLAS)
    paso_prev = estado.get("pasos", [])
    with span("agente.synthesis", "AGENT", **{"synthesis.intencion": intencion.value}):
        if intencion == Intencion.ANALIZAR_ARCHIVOS:
            informe = await _informe(estado, deps)
            return {
                "sintesis_hecha": True,
                "informe": informe.model_dump(mode="json"),
                "respuesta": informe.resumen,
                "fuentes": [f.model_dump() for f in informe.fuentes],
                "mensajes": [{"rol": "asistente", "contenido": f"Informe {informe.estado}: {informe.resumen}"}],
                "pasos": [*paso_prev, "synthesis(informe)"],
            }

        if intencion == Intencion.CONSULTAR_REGLAS:
            respuesta, fuentes = _respuesta_reglas_determinista(estado)
            contexto = "\n\n".join(
                [r["descripcion"] for r in (estado.get("reglas") or [])[:6]] + [g["texto"] for g in (estado.get("guias") or [])[:3]]
            )
            sistema = (
                "Responde en español la consulta del usuario usando EXCLUSIVAMENTE los fragmentos de documentación entregados. "
                "Son documentación de demostración educativa, no oficial. Si la respuesta no está en los fragmentos, dilo. "
                "Cita los identificadores de regla (p. ej. SC-001)."
            )
            usuario = f"Consulta: {estado.get('consulta')}\n\nFragmentos:\n{contexto}"
        else:
            respuesta, fuentes = _respuesta_resultado_determinista(estado), []
            sistema = (
                "Explica en español, de forma clara, el resultado del informe QA según la pregunta del usuario. "
                "Usa SOLO el informe entregado; no inventes hallazgos."
            )
            usuario = f"Pregunta: {estado.get('consulta')}\n\nInforme:\n{json.dumps(estado.get('informe'), ensure_ascii=False)}"

        hay_material = bool(contexto.strip()) if intencion == Intencion.CONSULTAR_REGLAS else bool(estado.get("informe"))
        if deps.llm is not None and hay_material:
            try:
                respuesta = (await deps.llm.texto(sistema, usuario)).strip() or respuesta
            except LLMError as exc:
                log.warning("Respuesta por LLM no disponible, se usa la determinista: %s", exc)

    return {
        "sintesis_hecha": True,
        "respuesta": respuesta,
        "fuentes": [f.model_dump() for f in fuentes],
        "mensajes": [{"rol": "asistente", "contenido": respuesta}],
        "pasos": [*paso_prev, "synthesis(respuesta)"],
    }
