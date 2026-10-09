#!/usr/bin/env python3
"""Estado del VPS para diagnostico remoto -> data/health.json.

Solo lectura, sin dependencias externas. Lo llama publish.sh cada 10 minutos.
Permite revisar el servidor desde raw.githubusercontent.com sin entrar al VPS.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DB = Path("/var/lib/bobusd/p2p_bob_usdt.sqlite")
ERR = Path("/var/lib/bobusd/publish_last_error.txt")
SERVICES = ["bobusd-collector", "bobusd-publish.timer"]


def sh(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout.strip()
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def service(name: str) -> dict:
    props = "ActiveState,SubState,ActiveEnterTimestamp,NRestarts,MemoryCurrent,Result,MainPID"
    raw = sh("systemctl", "show", name, f"--property={props}")
    d = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
    mem = d.get("MemoryCurrent", "")
    return {
        "active": d.get("ActiveState"),
        "sub": d.get("SubState"),
        "since": d.get("ActiveEnterTimestamp"),
        "restarts": int(d["NRestarts"]) if d.get("NRestarts", "").isdigit() else None,
        "memory_mb": round(int(mem) / 2**20, 1) if mem.isdigit() else None,
        "result": d.get("Result"),
        # memoria real del proceso (MemoryCurrent incluye la cache de disco de la BD SQLite)
        "rss_mb": rss_mb(d.get("MainPID", "")),
    }


def rss_mb(pid: str):
    if not pid.isdigit() or pid == "0":
        return None
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    except Exception:  # noqa: BLE001
        return None
    return None


def db() -> dict:
    if not DB.exists():
        return {"exists": False}
    out = {"exists": True, "size_mb": round(DB.stat().st_size / 2**20, 1)}
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
        n, last = con.execute("select count(*), max(ts) from rates").fetchone()
        con.close()
        out.update(rows=n, last_ts=last)
    except Exception as e:  # noqa: BLE001
        out["error"] = str(e)
    return out


def can_restart_collector() -> bool:
    """True si el bootstrap del VPS ya dio permiso para reiniciar el colector."""
    try:
        r = subprocess.run(["sudo", "-n", "-l", "/bin/systemctl", "restart", "bobusd-collector"],
                           capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    du = shutil.disk_usage("/")
    try:
        uptime_h = round(float(Path("/proc/uptime").read_text().split()[0]) / 3600, 1)
    except Exception:  # noqa: BLE001
        uptime_h = None
    health = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "services": {s: service(s) for s in SERVICES},
        "db": db(),
        "disk": {"free_gb": round(du.free / 2**30, 1), "used_pct": round(du.used / du.total * 100, 1)},
        "uptime_h": uptime_h,
        "load": [round(x, 2) for x in os.getloadavg()],
        "reboot_required": Path("/var/run/reboot-required").exists(),
        "last_publish_error": ERR.read_text()[-2000:] if ERR.exists() else None,
        "collector_deploy": can_restart_collector(),
        "ts_unix": int(time.time()),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "health.json").write_text(json.dumps(health, indent=1, ensure_ascii=False))
    print("health ok")


if __name__ == "__main__":
    main()
