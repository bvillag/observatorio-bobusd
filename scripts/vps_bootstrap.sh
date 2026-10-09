#!/usr/bin/env bash
# Preparacion unica del VPS para operarlo 100% desde el repo. Correr UNA vez como root:
#   curl -fsSL https://raw.githubusercontent.com/bvillag/observatorio-bobusd/main/scripts/vps_bootstrap.sh | bash
#
# Que hace (y nada mas):
#  1. Envoltorio de publicacion robusto: ya no hace "git pull" (que se trababa con cambios a medias);
#     solo limpia el repo y pasa el control a scripts/publish.sh del repo.
#  2. Da al usuario bobusd la propiedad del archivo del colector, para que publish.sh
#     instale la version del repo (collector/p2p_collector.py).
#  3. Permiso sudo para UN solo comando: reiniciar el servicio del colector.
set -euo pipefail
U=bobusd; H=/var/lib/bobusd
COL=/opt/bobusd/p2p_collector.py

echo "== 1/3 Envoltorio de publicacion"
cat > /opt/bobusd/publish_wrapper.sh <<WRAP
#!/usr/bin/env bash
export HOME=$H
cd $H/site || exit 1
git rebase --abort >/dev/null 2>&1 || true
git merge --abort >/dev/null 2>&1 || true
git fetch -q origin && git reset -q --hard origin/main
exec bash $H/site/scripts/publish.sh
WRAP
chmod 755 /opt/bobusd/publish_wrapper.sh

echo "== 2/3 Archivo del colector"
if ! systemctl cat bobusd-collector | grep -q "$COL"; then
  echo "AVISO: el servicio no usa $COL; revisa 'systemctl cat bobusd-collector'"
fi
chown $U:$U "$COL"

echo "== 3/3 Permiso para reiniciar solo el colector"
cat > /etc/sudoers.d/bobusd-collector <<SUDO
$U ALL=(root) NOPASSWD: /bin/systemctl restart bobusd-collector, /usr/bin/systemctl restart bobusd-collector
SUDO
chmod 440 /etc/sudoers.d/bobusd-collector
visudo -cf /etc/sudoers.d/bobusd-collector >/dev/null

systemctl start bobusd-publish.service || true
echo
echo "Listo. Ultima publicacion:"
journalctl -u bobusd-publish -n 5 --no-pager | tail -n 5
