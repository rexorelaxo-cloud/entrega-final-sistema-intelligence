"""Prueba de concurrencia: envía N solicitudes /analyze simultáneas y mide latencias reales.

Uso:
    python -m scripts.carga_concurrente --url http://localhost:8000 --solicitudes 5

Cada solicitud usa su propio thread_id. Los resultados se imprimen y se guardan en
resultados/carga_concurrente_<fecha>.json. La latencia p95 se calcula por rango más cercano
(con 5 muestras coincide con la latencia máxima).
"""

import argparse
import asyncio
import json
import math
import statistics
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
CASOS = [
    (["sitecore_valido.es-AR.json"], {"cms_destino": "sitecore"}),
    (["sitecore_faltantes.json"], {"cms_destino": "sitecore"}),
    (["sitecore_valido.es-AR.json", "sitecore_fr_inconsistente.fr-FR.json"], {"cms_destino": "sitecore", "idioma_origen": "es-AR"}),
    (["umbraco_valido.es-AR.json"], {"cms_destino": "umbraco"}),
    (["json_invalido.json"], {"cms_destino": "sitecore"}),
]


def percentil(muestras: list[float], p: float) -> float:
    ordenadas = sorted(muestras)
    rango = max(1, math.ceil(p / 100 * len(ordenadas)))
    return ordenadas[rango - 1]


async def una_solicitud(cliente: httpx.AsyncClient, i: int, prefijo: str) -> dict:
    nombres, params = CASOS[i % len(CASOS)]
    archivos = [("archivos", (n, (FIXTURES / n).read_bytes(), "application/json")) for n in nombres]
    datos = {**params, "aplicar_reglas_opcionales": "true", "thread_id": f"{prefijo}-{i}"}
    inicio = time.perf_counter()
    try:
        r = await cliente.post("/analyze", files=archivos, data=datos)
        duracion = time.perf_counter() - inicio
        cuerpo = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        completada = r.status_code == 200 and cuerpo.get("estado") == "completado"
        return {
            "i": i,
            "status": r.status_code,
            "completada": completada,
            "segundos": duracion,
            "veredicto": (cuerpo.get("informe") or {}).get("estado"),
            "error": None if r.status_code == 200 else r.text[:200],
        }
    except Exception as exc:  # error de red o timeout
        return {
            "i": i,
            "status": None,
            "completada": False,
            "segundos": time.perf_counter() - inicio,
            "veredicto": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def main(url: str, n: int, limite_s: float) -> dict:
    prefijo = f"carga-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=url, timeout=limite_s) as cliente:
        salud = (await cliente.get("/health")).json()
        inicio = time.perf_counter()
        resultados = await asyncio.gather(*(una_solicitud(cliente, i, prefijo) for i in range(n)))
        total = time.perf_counter() - inicio
    latencias = [r["segundos"] for r in resultados]
    resumen = {
        "fecha": datetime.now().isoformat(timespec="seconds"),
        "url": url,
        "modo_llm": salud.get("modo_llm"),
        "solicitudes": n,
        "completadas": sum(r["completada"] for r in resultados),
        "errores": sum(not r["completada"] for r in resultados),
        "tiempo_total_s": round(total, 3),
        "latencia_promedio_s": round(statistics.mean(latencias), 3),
        "latencia_p95_s": round(percentil(latencias, 95), 3),
        "latencia_min_s": round(min(latencias), 3),
        "latencia_max_s": round(max(latencias), 3),
        "detalle": [{**r, "segundos": round(r["segundos"], 3)} for r in resultados],
    }
    return resumen


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--solicitudes", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()
    salida = asyncio.run(main(args.url, args.solicitudes, args.timeout))
    destino = Path("resultados")
    destino.mkdir(exist_ok=True)
    archivo = destino / f"carga_concurrente_{datetime.now():%Y%m%d_%H%M%S}.json"
    archivo.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in salida.items() if k != "detalle"}, ensure_ascii=False, indent=2))
    print(f"Detalle guardado en {archivo}")
