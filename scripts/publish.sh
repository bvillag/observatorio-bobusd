#!/usr/bin/env bash
# Corre en el VPS cada 10 minutos (systemd timer bobusd-publish), como usuario bobusd.
# Recalcula las estadisticas y sube data/ a GitHub.
#
# Auto-reparable: cada corrida parte de una copia limpia de origin/main
# (data/ se regenera completo cada vez), asi un corte a medias no bloquea las siguientes.
# Datos del BCB: carpeta bcb/ del propio repo (GitHub Actions, diario 23:50 BOT);
# si todavia no existe, usa el repositorio publico acamperob/bcb-tco como respaldo.
set -Eeuo pipefail
export HOME=/var/lib/bobusd
SITE="$HOME/site"; BCB="$HOME/bcb-tco"
DB=/var/lib/bobusd/p2p_bob_usdt.sqlite
ERRF="$HOME/publish_last_error.txt"
LIVE_COLLECTOR=/opt/bobusd/p2p_collector.py
GIT=(git -c user.name="observatorio-bot" -c user.email="observatorio-bot@users.noreply.github.com")

fail() {
  { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) linea $1: $2"; } >> "$ERRF"
  tail -n 20 "$ERRF" > "$ERRF.tmp" && mv "$ERRF.tmp" "$ERRF"
}

sync_repo() {
  cd "$SITE"
  git rebase --abort >/dev/null 2>&1 || true
  git merge --abort >/dev/null 2>&1 || true
  git fetch -q origin
  git reset -q --hard origin/main
}

push_retry() {
  local i
  for i in 1 2 3; do
    if git push -q origin HEAD:main; then return 0; fi
    echo "aviso: push fallo (intento $i), reintento"
    sleep $((i * 5))
    git fetch -q origin
    git rebase -q origin/main || { git rebase --abort || true; return 1; }
  done
  return 1
}

# Colector remoto: el codigo vive en collector/p2p_collector.py del repo.
# - Si el repo aun no lo tiene, se sube la version que corre hoy en el VPS (una sola vez).
# - Si el repo tiene una version distinta y valida, se instala y se reinicia el servicio
#   (requiere el permiso sudo de scripts/vps_bootstrap.sh; si no existe, solo avisa).
deploy_collector() {
  local src="$SITE/collector/p2p_collector.py"
  if [ ! -f "$src" ]; then
    [ -r "$LIVE_COLLECTOR" ] || return 0
    mkdir -p "$SITE/collector"
    cp "$LIVE_COLLECTOR" "$src"
    git add collector/p2p_collector.py
    "${GIT[@]}" commit -q -m "colector: copia de la version en produccion"
    push_retry && echo "colector subido al repo"
    return 0
  fi
  cmp -s "$src" "$LIVE_COLLECTOR" && return 0
  if [ ! -w "$LIVE_COLLECTOR" ] || ! sudo -n -l /bin/systemctl restart bobusd-collector >/dev/null 2>&1; then
    echo "aviso: colector del repo distinto al del VPS, falta correr scripts/vps_bootstrap.sh"
    return 0
  fi
  /opt/bobusd/venv/bin/python -c "import ast,sys; ast.parse(open(sys.argv[1]).read())" "$src" || { echo "aviso: colector del repo no compila, no se instala"; return 0; }
  cp "$LIVE_COLLECTOR" "$HOME/p2p_collector.prev.py"
  cp "$src" "$LIVE_COLLECTOR"
  sudo -n /bin/systemctl restart bobusd-collector
  sleep 5
  if ! systemctl is-active -q bobusd-collector; then
    echo "aviso: el colector nuevo no arranco, vuelvo a la version anterior"
    cp "$HOME/p2p_collector.prev.py" "$LIVE_COLLECTOR"
    sudo -n /bin/systemctl restart bobusd-collector
    return 1
  fi
  echo "colector actualizado y reiniciado"
}

main() {
  sync_repo

  if [ -s "$SITE/bcb/total.csv" ] && [ -s "$SITE/bcb/operaciones.csv" ]; then
    BCBDATA="$SITE/bcb"
  else
    if [ ! -d "$BCB/.git" ]; then git clone -q --depth 1 https://github.com/acamperob/bcb-tco "$BCB"
    else git -C "$BCB" pull -q --ff-only || echo "aviso: no se pudo actualizar bcb-tco"; fi
    BCBDATA="$BCB/data"
  fi

  deploy_collector || echo "aviso: deploy del colector fallo"

  /opt/bobusd/venv/bin/python scripts/export.py --db "$DB" --bcb "$BCBDATA" --out data
  /opt/bobusd/venv/bin/python scripts/health.py data || echo "aviso: health.py fallo"

  git add -A data
  if git diff --cached --quiet; then echo "sin cambios"; rm -f "$ERRF"; return 0; fi
  "${GIT[@]}" commit -q -m "datos $(date -u +%Y-%m-%dT%H:%MZ)"
  push_retry
  rm -f "$ERRF"
  echo "publicado $(date -u +%H:%M) (bcb: $BCBDATA)"
}

trap 'fail $LINENO "$BASH_COMMAND"' ERR
main "$@"
