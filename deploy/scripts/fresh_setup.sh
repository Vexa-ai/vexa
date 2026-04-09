#!/bin/bash
set -e

# Fresh setup: install prereqs, clone repo, configure, and deploy Vexa.
#
# Usage:
#   ./fresh_setup.sh [--cpu|--gpu]
#   TRANSCRIPTION_TOKEN=xxx ./fresh_setup.sh    # fully automated
#
# If TRANSCRIPTION_TOKEN is set, deploys automatically. Otherwise prompts.

MODE="cpu"
if [ "${1:-}" = "--gpu" ]; then MODE="gpu"; fi

if [ "$(id -u)" != "0" ]; then
  echo "Please run as root (sudo -i)." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "[1/7] Updating system packages..."
apt-get update
apt-get -y upgrade

echo "[2/7] Installing prerequisites..."
apt-get install -y \
  python3 python3-pip python-is-python3 python3-venv \
  make git curl jq ca-certificates gnupg

echo "[3/7] Installing Docker Engine + Compose v2..."
apt-get remove -y docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc || true
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

if [ "$MODE" = "gpu" ]; then
  echo "[4/7] GPU mode selected. Installing NVIDIA Container Toolkit (if drivers present)..."
  if command -v nvidia-smi >/dev/null 2>&1; then
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
      sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update
    apt-get install -y nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker || true
    systemctl restart docker || true
  else
    echo "nvidia-smi not found. Skipping NVIDIA Container Toolkit. Install GPU drivers first if needed." >&2
  fi
else
  echo "[4/7] CPU mode selected. Skipping NVIDIA setup."
fi

echo "[5/7] Cloning or updating repository..."
if [ -d "/root/vexa" ]; then
  cd /root/vexa && git pull
else
  git clone https://github.com/Vexa-ai/vexa.git /root/vexa
fi

echo "[6/7] Configuring .env..."
cd /root/vexa
if [ ! -f .env ]; then
  cp deploy/env-example .env
  echo "Created .env from template."
fi

# Get transcription token: env var → interactive prompt → skip
TOKEN="${TRANSCRIPTION_TOKEN:-}"
if [ -z "$TOKEN" ]; then
  echo ""
  echo "A transcription token is required to run Vexa."
  echo "Get one at: https://staging.vexa.ai/dashboard/transcription"
  echo ""
  if [ -t 0 ] || [ -e /dev/tty ]; then
    read -rp "Paste your transcription token (or press Enter to skip): " TOKEN < /dev/tty
  fi
fi

if [ -n "$TOKEN" ]; then
  sed -i "s|^TRANSCRIPTION_SERVICE_TOKEN=.*|TRANSCRIPTION_SERVICE_TOKEN=$TOKEN|" .env
  echo "Transcription token set in .env."
fi

echo "[7/7] Deploying..."
if [ -n "$TOKEN" ]; then
  cd /root/vexa/deploy/compose && make all
  echo ""
  echo "Vexa is running."
  echo "  Dashboard: http://$(hostname -I | awk '{print $1}'):3001"
  echo "  API docs:  http://$(hostname -I | awk '{print $1}'):8056/docs"
else
  echo ""
  echo "Skipped deploy (no transcription token). Next steps:"
  echo "  cd /root/vexa"
  echo "  nano .env                            # set TRANSCRIPTION_SERVICE_TOKEN"
  echo "  cd deploy/compose && make all"
fi
