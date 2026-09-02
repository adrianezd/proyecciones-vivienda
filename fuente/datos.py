"""
Descarga de la serie del INE.

Se ejecuta en la GitHub Action, no en el navegador del visitante: cuando
el usuario abre la pagina, el dato ya esta dentro del HTML.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import httpx

CACHE = Path(__file__).parent.parent / "cache"
CACHE.mkdir(exist_ok=True)

CABECERAS = {
    "User-Agent": "proyecciones-vivienda/1.0 (+https://github.com/adrianezd/proyecciones-vivienda)",
    "Accept": "application/json, text/plain, */*",
}

INE = "https://servicios.ine.es/wstempus/js/ES"

# El codigo de serie NO esta escrito a mano: el INE renumera las series
# cada vez que cambia de base, y un codigo inventado devuelve datos de
# otra cosa sin avisar. Se busca por nombre dentro de su operacion, y el
# generador imprime cual ha elegido.
#
# "precios de la vivienda" (con "la") es a proposito: sin ella, el texto
# tambien encaja dentro de "Indice de Precios de Vivienda en ALQUILER",
# que es una operacion distinta.
BUSQUEDA = {"operacion": ["precios de la vivienda"],
            "serie": ["nacional", "general", "variación anual"]}

ANIO_MINIMO = dt.date.today().year - 2   # una serie sin datos recientes esta muerta


def _descargar(url: str, clave: str, params: dict | None = None) -> Any:
    fichero = CACHE / f"{clave}.json"
    try:
        r = httpx.get(url, params=params, headers=CABECERAS,
                      timeout=40.0, follow_redirects=True)
        r.raise_for_status()
        datos = r.json()
        fichero.write_text(json.dumps(datos), encoding="utf-8")
        print(f"  descargado  {clave}")
        return datos
    except Exception as e:
        if fichero.exists():
            print(f"  CACHE       {clave}  ({type(e).__name__})")
            return json.loads(fichero.read_text(encoding="utf-8"))
        print(f"  FALLO       {clave}  ({e})")
        return None


def listar_operaciones() -> list[dict]:
    datos = _descargar(f"{INE}/OPERACIONES_DISPONIBLES", "ine-operaciones")
    if not isinstance(datos, list):
        return []
    return [{"cod": o.get("Codigo") or o.get("Id"), "nombre": o.get("Nombre") or ""}
            for o in datos if o.get("Nombre")]


def listar_series(operacion: str) -> list[dict]:
    datos = _descargar(f"{INE}/SERIES_OPERACION/{operacion}",
                       f"series-{operacion}", {"page": 1})
    if not isinstance(datos, list):
        return []
    return [{"codigo": s.get("COD"), "nombre": s.get("Nombre") or ""}
            for s in datos if s.get("COD")]


def tasas_anuales(codigo: str) -> list[dict]:
    """Serie del INE convertida en una tasa por año.

    Prefiere diciembre, que cierra el año. Si la serie es trimestral usa el
    cuarto trimestre, y si no promedia lo que haya de cada año.
    """
    datos = _descargar(f"{INE}/DATOS_SERIE/{codigo}", f"ine-{codigo}", {"nult": 400})
    if not isinstance(datos, dict):
        return []

    puntos = datos.get("Data", [])
    if not puntos:
        return []

    def recoger(periodos: tuple[str, ...]) -> dict[int, float]:
        return {int(p["Anyo"]): float(p["Valor"]) for p in puntos
                if p.get("T3_Periodo") in periodos
                and isinstance(p.get("Valor"), (int, float))}

    por_anio = recoger(("M12",)) or recoger(("T4", "Q4"))

    if not por_anio:
        acumulado: dict[int, list[float]] = {}
        for p in puntos:
            if isinstance(p.get("Valor"), (int, float)):
                acumulado.setdefault(int(p["Anyo"]), []).append(float(p["Valor"]))
        por_anio = {a: sum(v) / len(v) for a, v in acumulado.items()}

    return [{"anio": a, "tasa": round(por_anio[a], 2)} for a in sorted(por_anio)]


def _ultimo_anio(codigo: str) -> int:
    filas = tasas_anuales(codigo)
    return filas[-1]["anio"] if filas else 0


def localizar_serie() -> str | None:
    """Encuentra el codigo de serie del INE para el precio de la vivienda.

    Tres pasos:
      1. Busca la operacion estadistica por su nombre, no por un codigo fijo.
      2. Dentro de ella, busca las series cuyo nombre encaje.
      3. Comprueba que la serie sigue viva: el INE conserva publicadas las
         series de bases antiguas, congeladas hace años, sin avisar.
    """
    operaciones = [o for o in listar_operaciones()
                   if all(p.lower() in o["nombre"].lower() for p in BUSQUEDA["operacion"])]

    if not operaciones:
        print(f"  SIN OPERACION  vivienda: ninguna con {BUSQUEDA['operacion']}")
        return None

    candidatas = []
    for op in operaciones:
        for s in listar_series(str(op["cod"])):
            nombre = (s.get("nombre") or "").lower()
            if all(p.lower() in nombre for p in BUSQUEDA["serie"]):
                candidatas.append(s)

    if not candidatas:
        print(f"  SIN SERIE   vivienda: nada con {BUSQUEDA['serie']} en {[o['nombre'][:40] for o in operaciones]}")
        return None

    # El INE anade las series nuevas al final, asi que se prueban del reves.
    for s in reversed(candidatas[-8:]):
        anio = _ultimo_anio(s["codigo"])
        if anio >= ANIO_MINIMO:
            print(f"  serie       vivienda -> {s['codigo']}  hasta {anio}  ({s['nombre'][:55]})")
            return s["codigo"]
        print(f"  descartada  {s['codigo']} ({s['nombre'][:40]}): sin datos desde {anio or 'nunca'}")

    print("  SIN SERIE   vivienda: todas las candidatas estan desactualizadas")
    return None
