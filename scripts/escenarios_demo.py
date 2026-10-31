"""Ejecuta los 5 escenarios funcionales contra el sistema en ejecución (API real).

Cada escenario genera una traza en Phoenix (proyecto `localization-qa`), identificable por su thread_id
`escenario-N-<sufijo>`. Verifica el veredicto y un hallazgo esperado de cada uno.

Uso (con la API levantada):
    python -m scripts.escenarios_demo --url http://localhost:8000
En Docker:
    docker compose cp tests api:/srv/app/tests
    docker compose exec api python -m scripts.escenarios_demo --url http://localhost:8000
"""

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

# (nombre, archivos, parámetros, veredicto esperado, (código, ruta) esperado o None)
ESCENARIOS = [
    ("1. JSON válido", ["sitecore_valido.es-AR.json"], {"cms_destino": "sitecore"}, "APTO", None),
    ("2. JSON inválido (sintaxis)", ["json_invalido.json"], {"cms_destino": "sitecore"}, "NO_APTO", ("QA-SYN-001", None)),
    ("3. Propiedades faltantes", ["sitecore_faltantes.json"], {"cms_destino": "sitecore"}, "NO_APTO", ("QA-REQ-001", "$.itemId")),
    (
        "4. Inconsistencia estructural entre idiomas",
        ["sitecore_valido.es-AR.json", "sitecore_fr_inconsistente.fr-FR.json"],
        {"cms_destino": "sitecore", "idioma_origen": "es-AR"},
        "NO_APTO",
        ("QA-STR-001", "$.fields.MetaDescription"),
    ),
    (
        "5. RAG contextual: mismo archivo del 1, reglas de Umbraco",
        ["sitecore_valido.es-AR.json"],
        {"cms_destino": "umbraco"},
        "NO_APTO",
        ("QA-REQ-001", "$.key"),
    ),
]


async def ejecutar(cliente: httpx.AsyncClient, n: int, esc: tuple, sufijo: str) -> dict:
    nombre, archivos, params, esperado, hallazgo = esc
    files = [("archivos", (a, (FIXTURES / a).read_bytes(), "application/json")) for a in archivos]
    data = {**params, "aplicar_reglas_opcionales": "true", "thread_id": f"escenario-{n}-{sufijo}"}
    t0 = time.perf_counter()
    r = await cliente.post("/analyze", files=files, data=data)
    segundos = time.perf_counter() - t0
    cuerpo = r.json()
    informe = cuerpo.get("informe") or {}
    hallazgos = informe.get("bloqueantes", []) + informe.get("advertencias", [])
    encontrado = hallazgo is None or any(
        h["codigo"] == hallazgo[0] and (hallazgo[1] is None or h["ruta"] == hallazgo[1]) for h in hallazgos
    )
    ok = r.status_code == 200 and informe.get("estado") == esperado and encontrado
    return {
        "nombre": nombre,
        "ok": ok,
        "veredicto": informe.get("estado"),
        "esperado": esperado,
        "segundos": segundos,
        "thread": data["thread_id"],
    }


async def main(url: str) -> int:
    sufijo = uuid.uuid4().hex[:6]
    async with httpx.AsyncClient(base_url=url, timeout=180) as cliente:
        resultados = [await ejecutar(cliente, i, e, sufijo) for i, e in enumerate(ESCENARIOS, 1)]
    for r in resultados:
        print(
            f"[{'OK' if r['ok'] else 'FALLO'}] {r['nombre']}: {r['veredicto']} (esperado {r['esperado']}) en {r['segundos']:.1f}s  thread={r['thread']}"
        )
    fallos = sum(not r["ok"] for r in resultados)
    print(f"\n{len(resultados) - fallos}/{len(resultados)} escenarios correctos. Las trazas están en Phoenix (proyecto localization-qa).")
    return 1 if fallos else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    sys.exit(asyncio.run(main(parser.parse_args().url)))
