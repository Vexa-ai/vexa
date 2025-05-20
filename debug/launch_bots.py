# Requirements: Install the docker sdk: pip install docker

import docker
import json
import uuid
import argparse
import sys

# --- Configuration (Match these with your docker-compose.yml) ---
BOT_IMAGE = "vexa-bot:latest"
# Check 'docker network ls | grep vexa' if unsure about the network name
DOCKER_NETWORK = "vexa_vexa_default"
REDIS_URL = "redis://redis:6379/0" # Assumes 'redis' is the service name in the network
# --------------------------------------------------------------

# --- Bot Defaults (Adjust as needed) ---
DEFAULT_PLATFORM = "google_meet"
DEFAULT_BOT_NAME_PREFIX = "DirectBot"
DEFAULT_LANGUAGE = None # Or set a default like 'en'
DEFAULT_TASK = "transcribe"
DEFAULT_TOKEN = "dummy-token" # Provide a dummy token if needed
DEFAULT_WAITING_ROOM_TIMEOUT = 300000
DEFAULT_NO_ONE_JOINED_TIMEOUT = 300000
DEFAULT_EVERYONE_LEFT_TIMEOUT = 300000
# --------------------------------------

def generate_bot_config(meeting_url, native_meeting_id, platform, bot_name, language, task, token):
    """Generates the BOT_CONFIG dictionary."""
    connection_id = str(uuid.uuid4())
    config = {
        # "meeting_id": None, # Removed: Set explicitly to null, but schema expects optional number (not null)
        "platform": platform,
        "meetingUrl": meeting_url,
        "botName": bot_name,
        "token": token,
        "nativeMeetingId": native_meeting_id,
        "connectionId": connection_id,
        "language": language,
        "task": task,
        "redisUrl": REDIS_URL,
        "automaticLeave": {
            "waitingRoomTimeout": DEFAULT_WAITING_ROOM_TIMEOUT,
            "noOneJoinedTimeout": DEFAULT_NO_ONE_JOINED_TIMEOUT,
            "everyoneLeftTimeout": DEFAULT_EVERYONE_LEFT_TIMEOUT
        }
    }
    # Remove keys with None values if bot schema expects them to be potentially undefined
    if language is None:
        del config["language"]
    if task is None:
        del config["task"]

    return config, connection_id

def extract_native_id(url, platform):
    """Basic extraction of native ID from URL (adjust if needed)."""
    if platform == "google_meet":
        # Example: https://meet.google.com/xyz-abc-pdq -> xyz-abc-pdq
        try:
            # Handle potential trailing slash and query parameters
            path_part = url.split('?')[0]
            if path_part.endswith('/'):
                path_part = path_part[:-1]
            return path_part.split('/')[-1]
        except Exception:
            return None
    # Add logic for other platforms (Zoom, Teams) if necessary
    print(f"Warning: Native ID extraction not implemented for platform '{platform}'. Returning None.", file=sys.stderr)
    return None

def start_bot(client, bot_config_dict, bot_number):
    """Starts a single vexa-bot container."""
    container_name = f"direct-vexa-bot-{bot_config_dict['nativeMeetingId'] or 'unknownid'}-{bot_number}-{uuid.uuid4().hex[:6]}"
    bot_config_json = json.dumps(bot_config_dict)

    print(f"Attempting to start container: {container_name}")
    print(f"  Platform: {bot_config_dict['platform']}")
    print(f"  Meeting URL: {bot_config_dict['meetingUrl']}")
    print(f"  Native ID: {bot_config_dict['nativeMeetingId']}")
    print(f"  Bot Name: {bot_config_dict['botName']}")
    print(f"  Connection ID: {bot_config_dict['connectionId']}")
    # print(f"  BOT_CONFIG: {bot_config_json}") # Uncomment to debug the full JSON

    try:
        container = client.containers.run(
            image=BOT_IMAGE,
            name=container_name,
            environment={
                "BOT_CONFIG": bot_config_json,
                "DISPLAY": ":99" # Assuming the entrypoint.sh sets up Xvfb on :99
            },
            network=DOCKER_NETWORK,
            detach=True,  # Run in the background
            # remove=True,  # Automatically remove container when it stops/exits
            # Add any other necessary options like volumes if vexa-bot needs them
            # volumes={'/path/on/host': {'bind': '/path/in/container', 'mode': 'rw'}},
            # Add labels if needed for tracking (mimicking bot-manager)
            # labels={
            #     "vexa.direct_launch": "true",
            #     "vexa.connection_id": bot_config_dict['connectionId'],
            #     "vexa.native_meeting_id": bot_config_dict['nativeMeetingId']
            # }
        )
        print(f"Successfully started container {container.short_id} ({container_name}) with Connection ID: {bot_config_dict['connectionId']}")
        return container.short_id
    except docker.errors.ImageNotFound:
        print(f"Error: Bot image '{BOT_IMAGE}' not found. Ensure it's built.", file=sys.stderr)
        return None
    except docker.errors.APIError as e:
        print(f"Error starting container {container_name}: {e}", file=sys.stderr)
        # Check for network not found error
        if "network" in str(e).lower() and ("not found" in str(e).lower() or "could not find" in str(e).lower()):
             print(f"Hint: Ensure Docker network '{DOCKER_NETWORK}' exists and is attachable. It might be created by 'docker compose up'. Check 'docker network ls'.", file=sys.stderr)
        elif "port is already allocated" in str(e).lower():
             print(f"Hint: A required port might be in use by another container or process.", file=sys.stderr)
        elif "driver failed programming external connectivity" in str(e).lower():
             print(f"Hint: This often relates to port conflicts or Docker networking issues.", file=sys.stderr)
        else:
             print(f"Hint: Check Docker daemon logs for more details.", file=sys.stderr)
        return None
    except Exception as e:
        print(f"An unexpected error occurred starting container {container_name}: {e}", file=sys.stderr)
        return None

def main():
    parser = argparse.ArgumentParser(description="Launch multiple Vexa bots directly into a meeting.")
    parser.add_argument("meeting_url", help="The full URL of the meeting to join.")
    parser.add_argument("-n", "--num_bots", type=int, default=1, help="Number of bots to launch.")
    parser.add_argument("-p", "--platform", default=DEFAULT_PLATFORM, choices=["google_meet", "zoom", "teams"], help="Meeting platform.")
    parser.add_argument("--bot_name_prefix", default=DEFAULT_BOT_NAME_PREFIX, help="Prefix for bot names (will be appended with a number).")
    parser.add_argument("--lang", default=DEFAULT_LANGUAGE, help="Language code for transcription (e.g., 'en', 'es').")
    parser.add_argument("--task", default=DEFAULT_TASK, choices=["transcribe", "translate"], help="Transcription task.")
    parser.add_argument("--token", default=DEFAULT_TOKEN, help="Dummy or required token for the bot.")

    args = parser.parse_args()

    native_id = extract_native_id(args.meeting_url, args.platform)
    if not native_id and args.platform == "google_meet": # Only exit if native ID is crucial and extraction failed
        print(f"Error: Could not extract Google Meet native meeting ID from URL '{args.meeting_url}'. Please check the URL format.", file=sys.stderr)
        sys.exit(1)
    elif not native_id:
         print(f"Warning: Could not extract native meeting ID for platform '{args.platform}'. Proceeding with nativeMeetingId=null.", file=sys.stderr)


    print(f"Preparing to launch {args.num_bots} bot(s) for {args.platform} meeting: {native_id or args.meeting_url}")

    try:
        client = docker.from_env()
        # Test connection
        client.ping()
        print("Connected to Docker daemon.")
    except docker.errors.DockerException as e:
        print(f"Error connecting to Docker daemon: {e}", file=sys.stderr)
        print("Ensure Docker is running and the Docker socket is accessible (check permissions if necessary).", file=sys.stderr)
        sys.exit(1)

    started_bots = []
    for i in range(args.num_bots):
        bot_number = i + 1
        bot_name = f"{args.bot_name_prefix}-{bot_number}"
        bot_config, conn_id = generate_bot_config(
            meeting_url=args.meeting_url,
            native_meeting_id=native_id, # Will be None if extraction failed
            platform=args.platform,
            bot_name=bot_name,
            language=args.lang,
            task=args.task,
            token=args.token
        )
        container_id = start_bot(client, bot_config, bot_number)
        if container_id:
            started_bots.append((container_id, conn_id))
        else:
            print(f"Failed to start bot number {bot_number}. Stopping.", file=sys.stderr)
            # Optional: Add logic here to stop already started bots if one fails
            # for cid_to_stop, _ in started_bots:
            #    try:
            #        container_to_stop = client.containers.get(cid_to_stop)
            #        print(f"Stopping previously started bot {cid_to_stop}...")
            #        container_to_stop.stop(timeout=5)
            #    except docker.errors.NotFound:
            #        print(f"Container {cid_to_stop} already stopped or removed.")
            #    except Exception as stop_err:
            #        print(f"Error stopping container {cid_to_stop}: {stop_err}", file=sys.stderr)
            break # Stop trying to launch more bots

    print("-" * 20)
    if started_bots:
        print(f"Successfully launched {len(started_bots)} bot(s):")
        for cid, connid in started_bots:
            print(f"  - Container ID: {cid}, Connection ID: {connid}")
    elif args.num_bots > 0 :
        print("No bots were successfully launched.")
    else:
        print("Number of bots requested was 0.")

if __name__ == "__main__":
    main() 