#!/usr/bin/env bash
# Instala la publicacion automatica del Observatorio BOB/USD en el VPS.
# Uso (como root, en la consola web):
#   curl -fsSL https://raw.githubusercontent.com/bvillag/observatorio-bobusd/main/scripts/setup_vps.sh | bash
set -euo pipefail
U=bobusd; H=/var/lib/bobusd
REPO=git@github.com:bvillag/observatorio-bobusd.git

echo "== 1/4 Paquetes (git, pandas, numpy)"
apt-get update -qq && apt-get install -y -qq git >/dev/null
/opt/bobusd/venv/bin/pip install -q pandas numpy

echo "== 2/4 Llave de despliegue (la parte privada nunca sale del servidor)"
install -d -m 700 -o $U -g $U $H/.ssh
[ -f $H/.ssh/id_ed25519 ] || sudo -u $U ssh-keygen -q -t ed25519 -N "" -C "observatorio-vps" -f $H/.ssh/id_ed25519
cat > $H/.ssh/config <<CFG
Host github.com
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile $H/.ssh/id_ed25519
  StrictHostKeyChecking accept-new
CFG
chown $U:$U $H/.ssh/config; chmod 600 $H/.ssh/config

echo "== 3/4 Envoltorio y temporizador (cada 10 min)"
cat > /opt/bobusd/publish_wrapper.sh <<WRAP
#!/usr/bin/env bash
set -e
export HOME=$H
[ -d $H/site/.git ] || git clone -q $REPO $H/site
cd $H/site
git pull -q --rebase
if [ -f site.tgz ]; then
  # primer arranque: desempaca la estructura de la pagina subida como un solo archivo
  tar xzf site.tgz && git rm -q --cached site.tgz && rm -f site.tgz setup_vps.sh
  git add -A
  git -c user.name="observatorio-bot" -c user.email="observatorio-bot@users.noreply.github.com" commit -q -m "estructura inicial de la pagina"
  git push -q
fi
exec bash $H/site/scripts/publish.sh
WRAP
chmod 755 /opt/bobusd/publish_wrapper.sh
cat > /etc/systemd/system/bobusd-publish.service <<UNIT
[Unit]
Description=Observatorio BOB/USD - exportar y publicar datos
After=network-online.target
[Service]
Type=oneshot
User=$U
ExecStart=/opt/bobusd/publish_wrapper.sh
TimeoutStartSec=600
UNIT
cat > /etc/systemd/system/bobusd-publish.timer <<UNIT
[Unit]
Description=Observatorio BOB/USD - cada 10 minutos
[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now bobusd-publish.timer >/dev/null

echo "== 4/4 Listo. Copia la linea de abajo (empieza con ssh-ed25519) y pegala en el chat:"
echo
cat $H/.ssh/id_ed25519.pub
echo
echo "Ver el estado:  journalctl -u bobusd-publish -n 20 --no-pager"
