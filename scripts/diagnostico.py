"""Diagnóstico: ejecuta el escenario de inconsistencia entre idiomas y muestra qué reglas y comprobaciones se aplicaron.

Uso (en Docker):  docker compose exec api python -m scripts.diagnostico
"""

import asyncio
import inspect
import json
import sys
import uuid
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


async def main(url: str) -> None:
    try:
        from app.agents import research

        print("Código de research con protección de reglas obligatorias:", "OBLIGATORIA" in inspect.getsource(research))
    except Exception as exc:  # fuera del contenedor puede no estar el paquete
        print("No se pudo inspeccionar el código:", exc)

    archivos = ["sitecore_valido.es-AR.json", "sitecore_fr_inconsistente.fr-FR.json"]
    files = [("archivos", (a, (FIXTURES / a).read_bytes(), "application/json")) for a in archivos]
    data = {
        "cms_destino": "sitecore",
        "idioma_origen": "es-AR",
        "aplicar_reglas_opcionales": "true",
        "thread_id": f"diag-{uuid.uuid4().hex[:6]}",
    }
    async with httpx.AsyncClient(base_url=url, timeout=180) as c:
        r = await c.post("/analyze", files=files, data=data)
        cuerpo = r.json()
        informe = cuerpo.get("informe") or {}
        print("HTTP:", r.status_code, "| estado:", informe.get("estado"), "| estadísticas:", informe.get("estadisticas"))
        print("reglas_aplicadas:", informe.get("reglas_aplicadas"))
        print("observaciones:", json.dumps(informe.get("observaciones"), ensure_ascii=False))
        print("pasos:", cuerpo.get("pasos"))
        for h in informe.get("bloqueantes", []) + informe.get("advertencias", []) + informe.get("informativos", []):
            print(f"  {h['severidad']:8} {h['codigo']:12} {h['archivo']} {h['ruta']} [{h['regla']}]")
        hist = (await c.get(f"/threads/{data['thread_id']}/history")).json()
        print("thread:", data["thread_id"], "| checkpoints:", len(hist.get("checkpoints", [])))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"))
