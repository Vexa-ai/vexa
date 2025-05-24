# Quick start: Local Deployment and Testing

Instructions for setting up, running, and testing the Vexa system locally using Docker Compose and Make.

[3 min video tutorial](https://www.youtube.com/watch?v=bHMIByieVek)

### Quick Start with Make


1.  **For CPU (Tiny Model, Slower Performance - Good for local tests/development):**
   this will use 'whisper tiny' model, which can run on CPU.
    ```bash
    git clone https://github.com/Vexa-ai/vexa
    cd vexa
    make all
    ```
    This command (among other things) uses `env-example.cpu` defaults for `.env` if not present.

2.  **For GPU (Medium Model, Faster Performance - Requires NVIDIA GPU & Toolkit):**
    this will use 'whisper medium' model, which is good enough to run on GPU.
    ```bash
    git clone https://github.com/Vexa-ai/vexa
    cd vexa
    make all TARGET=gpu
    ```
    This uses `env-example.gpu` defaults for `.env` if not present.


### Testing the deployment

```bash
make test
```

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

