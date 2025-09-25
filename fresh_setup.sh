#!/bin/bash
set -e

# Simple fresh setup to prepare a VM to run: make all (CPU) or make all TARGET=gpu
# Usage: ./fresh_setup.sh [--cpu|--gpu]

MODE="cpu"
if [ "${1:-}" = "--gpu" ]; then MODE="gpu"; fi

if [ "$(id -u)" != "0" ]; then
  echo "Please run as root (sudo -i)." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

echo "[1/6] Updating system packages..."
apt-get update
apt-get -y upgrade

echo "[2/6] Installing prerequisites..."
apt-get install -y \
  python3 python3-pip python-is-python3 python3-venv \
  make git curl jq ca-certificates gnupg

echo "[3/6] Installing Docker Engine + Compose v2..."
apt-get remove -y docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc || true
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker

if [ "$MODE" = "gpu" ]; then
  echo "[4/6] GPU mode selected. Installing NVIDIA Container Toolkit (if drivers present)..."
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
  echo "[4/6] CPU mode selected. Skipping NVIDIA setup."
fi

echo "[5/6] Cloning or updating repository..."
if [ -d "/root/vexa" ]; then
  cd /root/vexa && git pull
else
  git clone https://github.com/Vexa-ai/vexa.git /root/vexa
fi

echo "[6/6] Done. Next steps:"
echo "  cd /root/vexa"
if [ "$MODE" = "gpu" ]; then
  echo "  make all TARGET=gpu"
else
  echo "  make all"
fi
echo "Then open the API docs at http://localhost:18056/docs (or the port in .env)."


