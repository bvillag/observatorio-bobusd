#!/usr/bin/env python3
"""
Descarga diaria de la base de calculo del Tipo de Cambio Oficial (TCO) del BCB.

Fuentes publicas del BCB:
  - Lista de fechas de corte publicadas:
      https://www.bcb.gob.bo/bcb_tco_publico_detalle_historico.php  (atributo data-fechas)
  - CSV de una fecha de corte (el mismo del boton "Descargar CSV"):
      https://www.bcb.gob.bo/bcb_tco_publico_descargar_csv.php?desde=AAAA-MM-DD&hasta=AAAA-MM-DD

Salidas (carpeta bcb/ del repositorio):
  raw/AAAA-MM-DD.csv  copia sin modificar de cada fecha de corte
  operaciones.csv     una fila por (fecha_corte, banco, tipo de cambio)
  total.csv           una fila por fecha de corte (TCO publicado, operaciones, monto)
  tco_bancos.csv      TCO de cada banco por fecha de corte (fila "TCO" del BCB)

Idempotente: solo descarga las fechas que faltan en raw/ y reconstruye las tablas.
Solo usa la biblioteca estandar de Python.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE = "https://www.bcb.gob.bo"
URL_FECHAS = BASE + "/bcb_tco_publico_detalle_historico.php"
URL_CSV = BASE + "/bcb_tco_publico_descargar_csv.php?desde={d}&hasta={d}"
UA = "Mozilla/5.0 (X11; Linux x86_64) observatorio-bobusd (+https://github.com/bvillag/observatorio-bobusd)"
CAMBIO_METODO = "2026-09-25"  # RD 142/2026: mediana ponderada desde este corte
FECHA_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def get(url: str, intentos: int = 4) -> str:
    err = None
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "es-BO,es"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8-sig", errors="replace")
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(5 * (i + 1))
    raise RuntimeError(f"no se pudo descargar {url}: {err}")


def fechas_publicadas() -> list[str]:
    html = get(URL_FECHAS)
    m = re.search(r'data-fechas=(["\'])(.*?)\1', html, re.S)
    if not m:
        raise RuntimeError("no aparece data-fechas en la pagina del BCB (cambio el HTML?)")
    return sorted(f for f in json.loads(m.group(2).replace("&quot;", '"')) if FECHA_RE.fullmatch(f))


# ------------------------------------------------------------------ parseo
def num(s: str | None) -> float | None:
    """'="8.952.427"' -> 8952427 ; '10,0000' -> 10.0 ; '-' o '' -> None"""
    if s is None:
        return None
    s = s.strip().lstrip("=").strip('"').strip()
    if s in ("", "-"):
        return None
    return float(s.replace(".", "").replace(",", "."))


def vigencia(s: str) -> tuple[str, str]:
    f = FECHA_RE.findall(s)
    return (f[0], f[-1]) if f else ("", "")


def parse(texto: str) -> tuple[list[dict], list[dict], list[dict]]:
    filas = list(csv.reader(io.StringIO(texto.lstrip("﻿")), delimiter=";"))
    h = next(i for i, f in enumerate(filas) if f and f[0].strip() == "Fecha de corte")
    cab = filas[h]
    bancos = [(cab[j].strip(), j) for j in range(3, len(cab)) if cab[j].strip()]
    ops, tot, tcob = [], {}, []
    for f in filas[h + 2:]:
        if len(f) < 4 or not FECHA_RE.fullmatch(f[0].strip()):
            continue
        fc = f[0].strip()
        v0, v1 = vigencia(f[1])
        tipo = f[2].strip().upper()
        if tipo == "TCO":
            for b, j in bancos:
                val = num(f[j]) if j < len(f) else None
                if b == "TOTAL BANCOS":
                    tot.setdefault(fc, {})["tco"] = val
                elif val is not None:
                    tcob.append({"fecha_corte": fc, "banco": b, "tco_banco": val})
            tot[fc].update(vigencia=v0, vigencia_hasta=v1)
        elif tipo == "TOTAL":
            j = dict(bancos)["TOTAL BANCOS"]
            tot.setdefault(fc, {}).update(n_ops=num(f[j]), monto_usd=num(f[j + 1]))
        else:
            tc = num(f[2])
            for b, j in bancos:
                if b == "TOTAL BANCOS":
                    continue
                n, m = num(f[j]), num(f[j + 1])
                if n or m:
                    ops.append({"fecha_corte": fc, "vigencia": v0, "vigencia_hasta": v1, "banco": b,
                                "tc": tc, "n_ops": int(n or 0), "monto_usd": m or 0.0})
    total = []
    for fc, x in sorted(tot.items()):
        total.append({"fecha_corte": fc, "vigencia": x.get("vigencia", ""), "vigencia_hasta": x.get("vigencia_hasta", ""),
                      "metodo": "mediana" if fc >= CAMBIO_METODO else "promedio", "tco": x.get("tco"),
                      "n_ops": int(x.get("n_ops") or 0), "monto_usd": x.get("monto_usd")})
    return ops, total, tcob


def escribir(path: Path, filas: list[dict]) -> None:
    if not filas:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(filas[0]))
        w.writeheader()
        w.writerows(filas)


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "bcb"))
    ap.add_argument("--sin-descarga", action="store_true", help="solo reconstruye tablas desde raw/")
    a = ap.parse_args()
    out = Path(a.out)
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)

    if not a.sin_descarga:
        nuevas = 0
        for d in fechas_publicadas():
            dest = raw / f"{d}.csv"
            if dest.exists() and dest.stat().st_size > 500:
                continue
            texto = get(URL_CSV.format(d=d))
            if "Fecha de corte" not in texto or ";TCO;" not in texto:
                print(f"aviso: {d} sin datos completos todavia, se reintenta en la proxima corrida", file=sys.stderr)
                continue
            dest.write_text(texto, encoding="utf-8")
            nuevas += 1
            print(f"descargado {d}")
            time.sleep(1)
        print(f"fechas nuevas: {nuevas}")

    ops, total, tcob = [], [], []
    for p in sorted(raw.glob("*.csv")):
        o, t, b = parse(p.read_text(encoding="utf-8"))
        ops += o
        total += t
        tcob += b
    escribir(out / "operaciones.csv", ops)
    escribir(out / "total.csv", total)
    escribir(out / "tco_bancos.csv", tcob)
    print(f"cortes: {len(total)} | filas de operaciones: {len(ops)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
