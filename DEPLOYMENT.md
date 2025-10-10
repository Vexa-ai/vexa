# Vexa Deployment Guide

Real-time Google Meet and Microsoft Teams transcription API. Get up and running in minutes.

## Quick Start

**TL;DR - Try these in order:**

### 1. If you have an established development machine
Try running directly - this might work instantly:
```bash
git clone https://github.com/Vexa-ai/vexa.git && cd vexa
make all  # CPU laptop (whisper tiny model - good for development)
```
or 

```bash
git clone https://github.com/Vexa-ai/vexa.git && cd vexa
make all TARGET=gpu # GPU machine (whisper medium model - much better quality)
```

**What `make all` does:**
- Builds all Docker images (takes some time at the first run)
- Spins up all containers
- Runs database migrations (if nesessary)
- Starts a simple test to verify everything works

If you change code later, just run `make all` again - it rebuilds what's needed and skips the rest.

### 2. If you're on a fresh GPU VM in the cloud
**Automated setup** - Tested on Vultr `vcg-a16-6c-64g-16vram`

Sets up everything for you on a fresh VM:
```bash
git clone https://github.com/Vexa-ai/vexa.git && cd vexa
sudo ./fresh_setup.sh --gpu    # or --cpu for CPU-only hosts
make all TARGET=gpu             # or make all for CPU
```


### 3. Manual setup (if the above don't work)
**For fresh GPU virtual machines or custom setups:**

**Ubuntu/Debian:**
```bash
# Prerequisites
sudo apt update && sudo apt install -y \
  python3 python3-pip python-is-python3 python3-venv \
  make git curl jq ca-certificates gnupg

# Docker Engine + Compose v2
sudo apt remove -y docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc || true
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update && sudo apt install -y \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker

# GPU only (requires NVIDIA drivers: nvidia-smi must work)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt update && sudo apt install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# Deploy
git clone https://github.com/Vexa-ai/vexa.git && cd vexa
# make all              # CPU (tiny model)
make all TARGET=gpu # GPU (medium model)
```

**macOS (CPU only):**
Install Docker Desktop, then:
```bash
git clone https://github.com/Vexa-ai/vexa.git && cd vexa
make all
```

## Testing

Once deployed, services are available at:
- **API docs:** http://localhost:18056/docs
- **Admin API:** http://localhost:18057/docs

**Live meeting test:**
```bash
make test MEETING_ID=abc-defg-hij  # Use your Google Meet ID (xxx-xxxx-xxx format)
```

What to expect:
1. Bot joins your Google Meet
2. Admit the bot when prompted
3. Start speaking to see real-time transcripts

## Tested Environments

- **CPU:** Mac Pro (Docker Desktop)
- **GPU:** Fresh Vultr A16 VM (vcg-a16-6c-64g-16vram)

## Management Commands

```bash
make ps        # Show container status
make logs      # View logs
make down      # Stop services
make test-api  # Quick API connectivity test
```

## Managing Self-Hosted Vexa

For detailed guidance on managing users and API tokens in your self-hosted deployment, see:

**[Self-Hosted Management Guide](docs/self-hosted-management.md)**

This guide covers:
- Creating and managing users
- Generating and revoking API tokens
- Updating user settings (bot limits, etc.)
- Complete workflow examples with curl and Python

## Troubleshooting

**GPU Issues:**
- **"unknown device" error:** Ensure NVIDIA drivers work (`nvidia-smi`) and Container Toolkit is configured
- **Bot creation fails:** Check `docker-compose.yml` has correct `device_ids` (usually `"0"`)

**Test Issues:**
- **JSON parsing errors:** Use valid Google Meet ID format (`xxx-xxxx-xxx`) and admit bot to meeting
- **Bot doesn't join:** Check firewall settings and meeting permissions

---

**Need help?** Join our [Discord Community](https://discord.gg/Ga9duGkVz9) | **Video tutorial:** [3-minute setup guide](https://www.youtube.com/watch?v=bHMIByieVek)
