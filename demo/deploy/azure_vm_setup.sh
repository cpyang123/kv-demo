#!/bin/bash
# Bootstrap an Ubuntu 24.04 Azure VM for the full demo (replay + live runs through the GoodVision router).
# Run as root once:  sudo bash azure_vm_setup.sh <git clone url or /path/to/bundle> <dns name>
# Afterwards: put GOODVISION_API_KEY in /opt/kv/demo.env, copy the data bundle (demo/deploy/pack_data.sh)
# into /opt/kv/bundle, edit /etc/caddy/Caddyfile (domain + password hash), then `systemctl restart kv-demo caddy`.
set -euo pipefail
REPO="${1:?git url of knowledge-vault-project}"; DOMAIN="${2:-}"
apt-get update -y
apt-get install -y git curl python3.12 python3.12-venv python3-pip uidmap squashfs-tools fuse2fs
# Apptainer (the harness runs every agent command inside the task image)
APPT_VER=1.5.4
curl -sL -o /tmp/apptainer.deb "https://github.com/apptainer/apptainer/releases/download/v${APPT_VER}/apptainer_${APPT_VER}_amd64.deb"
apt-get install -y /tmp/apptainer.deb
# Caddy (TLS + reverse proxy)
apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
apt-get update -y && apt-get install -y caddy
# service user + checkout + venv
id kvdemo &>/dev/null || useradd -r -m -d /opt/kv -s /bin/bash kvdemo
mkdir -p /opt/kv && chown kvdemo /opt/kv
sudo -u kvdemo bash -c "cd /opt/kv && ([ -d knowledge-vault-project ] || git clone '$REPO' knowledge-vault-project) && cd knowledge-vault-project && git checkout share/inline-ledger-0922"
sudo -u kvdemo bash -c "python3.12 -m venv /opt/kv/venv && /opt/kv/venv/bin/pip install -q -r /opt/kv/knowledge-vault-project/demo/requirements.txt"
mkdir -p /opt/kv/sifs /opt/kv/custom && chown -R kvdemo /opt/kv/sifs /opt/kv/custom
[ -f /opt/kv/demo.env ] || cat > /opt/kv/demo.env <<ENV
# environment for kv-demo.service — keep this file root-readable only (chmod 600)
DEMO_HOST=127.0.0.1
DEMO_PORT=8765
RSM_SIF_DIR=/opt/kv/sifs
DEMO_CUSTOM_WORK=/opt/kv/custom
RSM_HF_HOME=/opt/kv/hf_home
GOODVISION_API_KEY=
# set DEMO_DISABLE_LIVE=1 for a replay-only deployment (no Apptainer runs, no key needed)
ENV
chmod 600 /opt/kv/demo.env
install -m 644 /opt/kv/knowledge-vault-project/demo/deploy/kv-demo.service /etc/systemd/system/kv-demo.service
[ -n "$DOMAIN" ] && sed "s/demo.example.edu/$DOMAIN/" /opt/kv/knowledge-vault-project/demo/deploy/Caddyfile > /etc/caddy/Caddyfile
systemctl daemon-reload && systemctl enable --now kv-demo
echo "done. Next: GOODVISION_API_KEY in /opt/kv/demo.env; data bundle -> /opt/kv (tar -xzf runs_extra.tgz in the repo, sifs -> /opt/kv/sifs);"
echo "      caddy hash-password -> /etc/caddy/Caddyfile; systemctl restart kv-demo caddy; open NSG ports 80/443."
