"""
Observatorio BOB/USD - exportador de datos.

Lee la base del recolector P2P (SQLite) y los archivos del BCB (repositorio
acamperob/bcb-tco), calcula las estadisticas de la pagina y escribe:
    data/obs.json                 todo lo que dibuja la pagina
    data/csv/p2p_YYYY-MM-DD.csv   precios P2P por minuto (un archivo por dia)
    data/csv/tco.csv              serie del TCO

Uso:
    python export.py --db /var/lib/bobusd/p2p_bob_usdt.sqlite \
                     --bcb /var/lib/bobusd/bcb-tco/data --out data

Convenciones
- Horas en UTC dentro del JSON; la pagina las muestra en hora de Bolivia (UTC-4).
- "Base" (basis) = mid P2P / TCO vigente - 1, en %. Positiva = el dolar P2P es
  mas caro que el oficial.
- El TCO de un dia de corte rige desde `vigencia` hasta `vigencia_hasta`.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

BOT = timezone(timedelta(hours=-4))
SIZES = [100, 500, 1000, 5000, 10000, 50000]          # USDT
TICKET_BINS = [0, 1e3, 1e4, 1e5, 1e6, np.inf]
TICKET_LABELS = ["< 1 mil", "1 – 10 mil", "10 – 100 mil", "100 mil – 1 M", "> 1 M"]
MIN_DAYS_TAR = 14                                      # antes de esto, "preliminar"


# --------------------------------------------------------------------------- utilidades
def r(x, nd=4):
    """Redondea y convierte NaN/inf en None (JSON valido)."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(x) or math.isinf(x)) else round(x, nd)


def rl(arr, nd=4):
    return [r(v, nd) for v in arr]


def iso(ts) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def wquantile(values, weights, q):
    o = np.argsort(values)
    v, w = np.asarray(values)[o], np.asarray(weights)[o]
    c = np.cumsum(w) / w.sum()
    return float(v[np.searchsorted(c, q)])


# --------------------------------------------------------------------------- BCB
def load_bcb(path: Path):
    tot = pd.read_csv(path / "total.csv", parse_dates=["fecha_corte", "vigencia", "vigencia_hasta"])
    op = pd.read_csv(path / "operaciones.csv", parse_dates=["fecha_corte"])
    tot = tot.sort_values("fecha_corte").reset_index(drop=True)
    return tot, op


def tco_daily(tot: pd.DataFrame) -> pd.Series:
    """TCO vigente por fecha calendario (hora de Bolivia)."""
    rows = []
    for _, x in tot.iterrows():
        for d in pd.date_range(x.vigencia, x.vigencia_hasta, freq="D"):
            rows.append((d.date(), x.tco))
    s = pd.Series(dict(rows)).sort_index()
    # rellena huecos (feriados no cubiertos) con el ultimo valor conocido
    full = pd.date_range(min(s.index), max(s.index), freq="D").date
    return s.reindex(full).ffill()


def banks_block(tot: pd.DataFrame, op: pd.DataFrame) -> dict:
    tco_cut = tot.set_index("fecha_corte")
    out = {"date": [], "tco": [], "usd_m": [], "hhi": [], "top3": [], "p10": [], "p90": [], "above_cap": []}
    rd88_end = pd.Timestamp("2026-09-24")
    for d, g in op.groupby("fecha_corte"):
        if d not in tco_cut.index:
            continue
        x = tco_cut.loc[d]
        by_bank = g.groupby("banco").monto_usd.sum()
        sh = by_bank / by_bank.sum()
        dev = (g.tc / x.tco - 1) * 100
        out["date"].append(d.strftime("%Y-%m-%d"))
        out["tco"].append(r(x.tco, 2))
        out["usd_m"].append(r(g.monto_usd.sum() / 1e6, 2))
        out["hhi"].append(r((sh ** 2).sum(), 3))
        out["top3"].append(r(sh.nlargest(3).sum() * 100, 1))
        out["p10"].append(r(wquantile(dev.values, g.monto_usd.values, 0.10), 2))
        out["p90"].append(r(wquantile(dev.values, g.monto_usd.values, 0.90), 2))
        if d <= rd88_end:
            # tope de venta RD 88: TCO vigente ese dia + 0,10. Aproximacion: TCO del corte anterior.
            prev = tot[tot.fecha_corte < d].tco
            cap = (prev.iloc[-1] if len(prev) else x.tco) + 0.10
            out["above_cap"].append(r(g.loc[g.tc > cap, "monto_usd"].sum() / g.monto_usd.sum() * 100, 1))
        else:
            out["above_cap"].append(None)

    # concentracion en toda la muestra
    by_bank = op.groupby("banco").monto_usd.sum().sort_values(ascending=False)
    share = (by_bank / by_bank.sum() * 100).round(1)

    # descuento por tamano de operacion (promedio del nivel de precio)
    o = op.merge(tot[["fecha_corte", "tco"]], on="fecha_corte")
    o["ticket"] = o.monto_usd / o.n_ops
    o["dev"] = (o.tc / o.tco - 1) * 100
    o["bin"] = pd.cut(o.ticket, TICKET_BINS, labels=TICKET_LABELS, right=False)
    tk = []
    for lab, g in o.groupby("bin", observed=False):
        if len(g) == 0:
            continue
        tk.append({"bin": lab, "dev_pct": r(np.average(g.dev, weights=g.n_ops), 2),
                   "ops_pct": r(g.n_ops.sum() / o.n_ops.sum() * 100, 1),
                   "usd_pct": r(g.monto_usd.sum() / o.monto_usd.sum() * 100, 1)})

    return {
        "daily": out,
        "share": [{"bank": b.title(), "pct": float(p)} for b, p in share.head(6).items()],
        "share_other": float(round(100 - share.head(6).sum(), 1)),
        "hhi_mean": r(np.nanmean([v for v in out["hhi"] if v is not None]), 3),
        "top3_mean": r(np.nanmean([v for v in out["top3"] if v is not None]), 1),
        "dispersion_median": r(np.nanmedian(np.array(out["p90"], float) - np.array(out["p10"], float)), 2),
        "tickets": tk,
        "n_days": len(out["date"]),
        "total_usd_m": r(op.monto_usd.sum() / 1e6, 0),
    }


# --------------------------------------------------------------------------- P2P
def load_rates(con) -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM rates ORDER BY ts", con)
    df["ts"] = pd.to_datetime(df.ts, utc=True, format="ISO8601")
    return df.set_index("ts")


def vwap_for_size(prices, qty, min_bob, max_bob, q, ascending):
    o = np.argsort(prices)
    if not ascending:
        o = o[::-1]
    remaining, cost = q, 0.0
    for i in o:
        if remaining <= 1e-9:
            break
        p = prices[i]
        cap = min(qty[i], max_bob[i] / p if max_bob[i] else qty[i])
        take = min(remaining, cap)
        if take * p < min_bob[i]:
            continue
        cost += take * p
        remaining -= take
    return cost / (q - remaining) if remaining <= 1e-9 else np.nan


def book_stats(ads: pd.DataFrame, mid: float) -> dict:
    a, b = ads[ads.side == "ask"], ads[ads.side == "bid"]
    res = {"depth1_ask": a.loc[a.price <= mid * 1.01, "qty_usdt"].sum(),
           "depth1_bid": b.loc[b.price >= mid * 0.99, "qty_usdt"].sum()}
    for q in SIZES:
        pa = vwap_for_size(a.price.values, a.qty_usdt.values, a.min_bob.values, a.max_bob.values, q, True)
        pb = vwap_for_size(b.price.values, b.qty_usdt.values, b.min_bob.values, b.max_bob.values, q, False)
        res[f"ask_{q}"] = (pa / mid - 1) * 100
        res[f"bid_{q}"] = (1 - pb / mid) * 100
    return res


def ar1(x: np.ndarray):
    """x_t = c + phi x_{t-1} + e. Devuelve phi y vida media en pasos."""
    y, z = x[1:], x[:-1]
    Z = np.column_stack([np.ones_like(z), z])
    beta, *_ = np.linalg.lstsq(Z, y, rcond=None)
    phi = beta[1]
    hl = math.log(0.5) / math.log(phi) if 0 < phi < 1 else np.nan
    return phi, hl


def tar_band(x: np.ndarray, mu: float):
    """Autorregresion con umbral (estilo Balke y Fomby, 1997), banda simetrica alrededor de mu.

    dx_t = rho_in * d_{t-1}  si |d_{t-1}| <= theta   (dentro de la banda)
    dx_t = rho_out * d_{t-1} si |d_{t-1}| >  theta   (fuera: se corrige)
    con d = x - mu. theta se elige por minima suma de cuadrados en una grilla.
    """
    d = x - mu
    dy, dl = np.diff(d), d[:-1]
    best = None
    for theta in np.quantile(np.abs(dl), np.linspace(0.15, 0.85, 57)):
        inside = np.abs(dl) <= theta
        if inside.sum() < 20 or (~inside).sum() < 20:
            continue
        ssr, rho = 0.0, {}
        for k, m in (("in", inside), ("out", ~inside)):
            b = (dl[m] @ dy[m]) / (dl[m] @ dl[m])
            rho[k] = b
            ssr += ((dy[m] - b * dl[m]) ** 2).sum()
        if best is None or ssr < best[0]:
            best = (ssr, theta, rho, (~inside).mean())
    if best is None:
        return None
    _, theta, rho, out_share = best
    hl_out = math.log(0.5) / math.log(1 + rho["out"]) if -1 < rho["out"] < 0 else None
    return {"theta_pct": r(theta, 3), "center_pct": r(mu, 3), "rho_in": r(rho["in"], 4),
            "rho_out": r(rho["out"], 4), "pct_outside": r(out_share * 100, 1), "half_life_out_steps": r(hl_out, 1)}


def variance_ratio(logp: np.ndarray, q: int):
    r1 = np.diff(logp)
    n = len(r1)
    if n < 5 * q:
        return None, None
    mu = r1.mean()
    s1 = ((r1 - mu) ** 2).sum() / (n - 1)
    rq = logp[q:] - logp[:-q]
    m = q * (n - q + 1) * (1 - q / n)
    vr = ((rq - q * mu) ** 2).sum() / m / s1
    e2 = (r1 - mu) ** 2
    theta = sum((2 * (q - j) / q) ** 2 * (e2[j:] * e2[:-j]).sum() / (e2.sum() ** 2) * n for j in range(1, q))
    z = (vr - 1) / math.sqrt(theta / n) if theta > 0 else np.nan
    return vr, z


def p2p_block(con, tco_by_day: pd.Series) -> dict | None:
    rates = load_rates(con)
    rates = rates[rates.mid.notna()]
    if rates.empty:
        return None
    last_ts = rates.index[-1]

    # --- serie de 10 minutos
    b10 = rates[["mid", "bid_vwap", "ask_vwap", "spread_pct", "depth_ask_usdt", "depth_bid_usdt",
                 "n_ask", "n_bid"]].resample("10min").mean().dropna(subset=["mid"])
    local_day = b10.index.tz_convert(BOT).date
    b10["tco"] = [tco_by_day.get(d, np.nan) for d in local_day]
    b10["basis"] = (b10.mid / b10.tco - 1) * 100

    # --- libro actual y curva de costo por monto
    last_ads = pd.read_sql("SELECT * FROM ads WHERE ts = ?", con, params=(rates.index[-1].strftime("%Y-%m-%dT%H:%M:%S+00:00"),))
    touch = lambda x: (x.best_ask + x.best_bid) / 2      # mid del mejor precio de cada lado
    now_book = book_stats(last_ads, touch(rates.iloc[-1])) if len(last_ads) else {}

    # promedio de las ultimas 24 h, una foto por hora
    recent = rates.loc[rates.index >= last_ts - pd.Timedelta(hours=24)]
    hourly_ts = recent.groupby(recent.index.floor("1h")).head(1).index
    curves, limits = [], []
    for t in hourly_ts:
        ads = pd.read_sql("SELECT * FROM ads WHERE ts = ?", con, params=(t.strftime("%Y-%m-%dT%H:%M:%S+00:00"),))
        if len(ads):
            curves.append(book_stats(ads, touch(rates.loc[t])))
            limits.append(ads)
    cv = pd.DataFrame(curves)
    lim = pd.concat(limits) if limits else last_ads

    # --- base P2P - TCO
    bs = b10.basis.dropna()
    days = (bs.index[-1] - bs.index[0]).total_seconds() / 86400 if len(bs) > 1 else 0
    hist_edges = np.arange(math.floor(bs.min() * 2) / 2, math.ceil(bs.max() * 2) / 2 + 0.25, 0.25) if len(bs) else []
    hist = np.histogram(bs, bins=hist_edges)[0].tolist() if len(bs) else []
    phi, hl = ar1(bs.values) if len(bs) > 30 else (np.nan, np.nan)
    tar = tar_band(bs.values, float(bs.mean())) if len(bs) > 100 else None

    # --- dinamica: volatilidad y paseo aleatorio sobre retornos de 10 min
    lp = np.log(b10.mid.values)
    ret = np.diff(lp)
    per_day = 144
    rv = pd.Series(ret, index=b10.index[1:]).pow(2).resample("1D").sum().pow(0.5) * 100
    acf = [r(pd.Series(ret).autocorr(k), 3) for k in range(1, 13)] if len(ret) > 50 else []
    vrs = {f"q{q}": dict(zip(("vr", "z"), map(lambda v: r(v, 3), variance_ratio(lp, q)))) for q in (6, 36, 144)}

    # --- mapa de calor: spread mediano por dia de la semana y hora (Bolivia)
    loc = rates.copy()
    loc.index = loc.index.tz_convert(BOT)
    heat = loc.groupby([loc.index.dayofweek, loc.index.hour]).spread_pct.median()
    heat_rows = [[int(h), int(d), r(v, 3)] for (d, h), v in heat.items()]

    last = rates.iloc[-1]
    tco_now = tco_by_day.get(last_ts.tz_convert(BOT).date(), np.nan)
    return {
        "first": iso(rates.index[0]), "last": iso(last_ts), "n": int(len(rates)), "days": r(days, 2),
        "now": {
            "mid": r(last.mid), "best_bid": r(last.best_bid), "best_ask": r(last.best_ask),
            "bid_vwap": r(last.bid_vwap), "ask_vwap": r(last.ask_vwap), "spread_pct": r(last.spread_pct, 3),
            "tco": r(tco_now, 2), "basis_pct": r((last.mid / tco_now - 1) * 100, 2),
            "depth1_ask": r(now_book.get("depth1_ask"), 0), "depth1_bid": r(now_book.get("depth1_bid"), 0),
            "depth_ask": r(last.depth_ask_usdt, 0), "depth_bid": r(last.depth_bid_usdt, 0),
            "n_ask": int(last.n_ask), "n_bid": int(last.n_bid),
        },
        "s10": {
            "t": [iso(t) for t in b10.index], "mid": rl(b10.mid), "bid": rl(b10.bid_vwap), "ask": rl(b10.ask_vwap),
            "tco": rl(b10.tco, 2), "basis": rl(b10.basis, 3), "spread": rl(b10.spread_pct, 3),
            "depth_ask": rl(b10.depth_ask_usdt, 0), "depth_bid": rl(b10.depth_bid_usdt, 0),
            "n_ask": rl(b10.n_ask, 1), "n_bid": rl(b10.n_bid, 1),
        },
        "basis": {
            "mean": r(bs.mean(), 3), "sd": r(bs.std(), 3), "p5": r(bs.quantile(.05), 3),
            "p50": r(bs.median(), 3), "p95": r(bs.quantile(.95), 3), "last": r(bs.iloc[-1], 3) if len(bs) else None,
            "hist_edges": rl(hist_edges, 2), "hist": hist,
            "ar1_phi": r(phi, 4), "half_life_min": r(hl * 10, 0) if not math.isnan(hl) else None,
            "tar": tar, "preliminary": days < MIN_DAYS_TAR, "days": r(days, 1),
        },
        "size_curve": {
            "sizes": SIZES,
            "ask_now": [r(now_book.get(f"ask_{q}"), 3) for q in SIZES],
            "bid_now": [r(now_book.get(f"bid_{q}"), 3) for q in SIZES],
            "ask_24h": [r(cv[f"ask_{q}"].median(), 3) if len(cv) else None for q in SIZES],
            "bid_24h": [r(cv[f"bid_{q}"].median(), 3) if len(cv) else None for q in SIZES],
        },
        "limits": {
            "min_bob_median": {s: r(lim[lim.side == s].min_bob.median(), 0) for s in ("ask", "bid")},
            "max_bob_median": {s: r(lim[lim.side == s].max_bob.median(), 0) for s in ("ask", "bid")},
            "finish_rate_median": r(lim.finish_rate.median() * 100, 1),
            "merchants_24h": int(lim.user_no.nunique()),
        },
        "heat": heat_rows,
        "dynamics": {
            "rv_daily": {"date": [d.strftime("%Y-%m-%d") for d in rv.index], "pct": rl(rv, 3)},
            "vol_ann_pct": r(np.std(ret) * math.sqrt(per_day * 365) * 100, 1) if len(ret) > 50 else None,
            "acf": acf, "vr": vrs, "preliminary": days < MIN_DAYS_TAR,
        },
    }


def write_csv(con, tot: pd.DataFrame, out: Path, full: bool):
    d = out / "csv"
    d.mkdir(parents=True, exist_ok=True)
    tot[["fecha_corte", "vigencia", "vigencia_hasta", "metodo", "tco", "n_ops", "monto_usd"]].to_csv(d / "tco.csv", index=False)
    rates = load_rates(con)
    if rates.empty:
        return []
    rates.index = rates.index.tz_convert("UTC")
    days = sorted(set(rates.index.date))
    todo = days if full else days[-2:]
    for day in todo:
        x = rates[rates.index.date == day]
        x.to_csv(d / f"p2p_{day}.csv", float_format="%.4f")
    return [str(x) for x in days]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--bcb", required=True)
    ap.add_argument("--out", default="data")
    ap.add_argument("--full-csv", action="store_true", help="reescribe todos los CSV diarios")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    tot, op = load_bcb(Path(a.bcb))
    tco_by_day = tco_daily(tot)
    con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True, timeout=60)
    full = a.full_csv or not (out / "csv").exists()
    p2p_days = write_csv(con, tot, out, full)

    obs = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tco": {"cutoff": tot.fecha_corte.dt.strftime("%Y-%m-%d").tolist(),
                "from": tot.vigencia.dt.strftime("%Y-%m-%d").tolist(),
                "to": tot.vigencia_hasta.dt.strftime("%Y-%m-%d").tolist(),
                "tco": rl(tot.tco, 2), "method": tot.metodo.tolist(),
                "usd_m": rl(tot.monto_usd / 1e6, 2)},
        "banks": banks_block(tot, op),
        "p2p": p2p_block(con, tco_by_day),
        "csv_days": p2p_days,
    }
    tco_lp = np.log(tot.tco.values)
    obs["tco"]["vr5"] = dict(zip(("vr", "z"), map(lambda v: r(v, 3), variance_ratio(tco_lp, 5))))
    obs["tco"]["vol_daily_pct"] = r(np.std(np.diff(tco_lp)) * 100, 2)
    (out / "obs.json").write_text(json.dumps(obs, ensure_ascii=False, separators=(",", ":")))
    print("ok", obs["generated"], "p2p:", obs["p2p"]["n"] if obs["p2p"] else 0)


if __name__ == "__main__":
    main()
