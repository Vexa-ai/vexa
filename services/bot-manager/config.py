import os

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
BOT_IMAGE_NAME = os.environ.get("BOT_IMAGE_NAME", "vexa-bot:dev")
DOCKER_NETWORK = os.environ.get("DOCKER_NETWORK", "vexa_default")

# Lock settings
LOCK_TIMEOUT_SECONDS = 300 # 5 minutes
LOCK_PREFIX = "bot_lock:"
MAP_PREFIX = "bot_map:"
STATUS_PREFIX = "bot_status:" 