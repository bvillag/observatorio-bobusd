#!/usr/bin/env bash
# Corre en el VPS cada 10 minutos (systemd timer bobusd-publish).
# Recalcula las estadisticas y sube data/ a GitHub.
# Datos del BCB: carpeta bcb/ del propio repo (GitHub Actions, diario 23:50 BOT);
# si todavia no existe, usa el repositorio publico acamperob/bcb-tco como respaldo.
set -euo pipefail
export HOME=/var/lib/bobusd
SITE="$HOME/site"; BCB="$HOME/bcb-tco"
DB=/var/lib/bobusd/p2p_bob_usdt.sqlite

cd "$SITE"
git pull -q --rebase

if [ -s "$SITE/bcb/total.csv" ] && [ -s "$SITE/bcb/operaciones.csv" ]; then
  BCBDATA="$SITE/bcb"
else
  if [ ! -d "$BCB/.git" ]; then git clone -q --depth 1 https://github.com/acamperob/bcb-tco "$BCB"
  else git -C "$BCB" pull -q --ff-only || echo "aviso: no se pudo actualizar bcb-tco"; fi
  BCBDATA="$BCB/data"
fi

/opt/bobusd/venv/bin/python scripts/export.py --db "$DB" --bcb "$BCBDATA" --out data
git add -A data
if git diff --cached --quiet; then echo "sin cambios"; exit 0; fi
git -c user.name="observatorio-bot" -c user.email="observatorio-bot@users.noreply.github.com" \
    commit -q -m "datos $(date -u +%Y-%m-%dT%H:%MZ)"
git push -q
echo "publicado $(date -u +%H:%M) (bcb: $BCBDATA)"
