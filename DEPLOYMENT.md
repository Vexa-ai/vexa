# Quick start: Local Deployment and Testing

Instructions for setting up, running, and testing the Vexa system locally using Docker Compose and Make.

[3 min video tutorial](https://www.youtube.com/watch?v=bHMIByieVek)

## Prerequisites (Ubuntu/Debian)

Before running Vexa, ensure you have the following installed on your Ubuntu system:

### Required Dependencies
```bash
# Update package list
sudo apt update

# Install Python (ensure "python" command works) and venv support
sudo apt install -y python3 python3-pip python-is-python3 python3-venv

# Install Make and Git
sudo apt install -y make git

# Install the latest Docker Engine (includes docker compose v2)
# Remove older packages (safe if not present)
sudo apt remove -y docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc || true

# Dependencies for Docker repo
sudo apt install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update

# Install Docker Engine + Compose V2 plugin
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Start and enable Docker service
sudo systemctl enable --now docker

# Optional: allow current user to run docker without sudo (relogin required)
sudo usermod -aG docker $USER

# Verify versions
docker --version
docker compose version
```


### Verify installation
Run the following to verify all prerequisites are present:
```bash
python --version
make --version
docker --version
docker compose version
```

### Quick Start with Make


1.  **For CPU (Tiny Model, Slower Performance - Good for local tests/development):**
   this will use 'whisper tiny' model, which can run on CPU.
    ```bash
    git clone https://github.com/Vexa-ai/vexa
    cd vexa
    make all
    ```
    This command (among other things) uses `env-example.cpu` defaults for `.env` if not present.
    It also creates a local Python virtual environment (`.venv`) to download the Whisper model
    into `./hub` so Docker can reuse the cache. No system-wide Python packages are installed.

2.  **For GPU (Medium Model, Faster Performance - Requires NVIDIA GPU & Toolkit):**
    this will use 'whisper medium' model, which is good enough to run on GPU.
    ```bash
    git clone https://github.com/Vexa-ai/vexa
    cd vexa
    make all TARGET=gpu
    ```
    This uses `env-example.gpu` defaults for `.env` if not present.
    Like CPU mode, model artifacts are cached under `./hub` via a local `.venv`.


What to expect during testing:
1. Test user and its token are created
2. You will be asked for a meeting ID
3. Provide the `xxx-xxxx-xxx` from your running meeting (`https://meet.google.com/xxx-xxxx-xxx`)
4. Bot is sent to the meeting you provided 
5. Wait about 10 sec for the bot to join the meeting
6. Let the bot into the conference
7. Start speaking
8. Wait for the transcripts to appear. 

Did it work? Tell us! 💬 [Join Discord Community!](https://discord.gg/Ga9duGkVz9)
 



The transcription latency can is higher and quality might be lower  when running locally in CPU mode, since you don't have a device to run bigger model quickly. But this is usually enough for development and testing





### API Documentation that is running behind the hood

API docs (Swagger/OpenAPI) are available at (ports are configurable in `.env`):

```
Main API docs:  http://localhost:8056/docs
Admin API docs: http://localhost:8057/docs
```

**Managing Services:**
- `make ps`: Show container status.
- `make logs`: Tail logs (or `make logs SERVICE=<service_name>`).
- `make down`: Stop all services.
- `make clean`: Stop services and remove volumes.

