"""``python -m chat_door`` — run the door with uvicorn, config from env.

The startup line states the signing-key **fingerprint** and whether it was generated for this
process, so an operator can tell at a glance whether links will survive a restart. The key
itself is never emitted.
"""
from __future__ import annotations

import os

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
    config = load_config()
    app = create_app(config)
    if config.signing_key.generated:
        print(
            f"chat-door: EPHEMERAL signing key (fingerprint {config.signing_key.fingerprint}) — "
            "links die on restart. Set CHAT_DOOR_SIGNING_KEY for anything but a quick demo."
        )
    else:
        print(f"chat-door: signing key fingerprint {config.signing_key.fingerprint}")
    print(f"chat-door: base url {config.base_url} · meetings api {config.meetings_url}")
    uvicorn.run(app, host=os.getenv("CHAT_DOOR_HOST", "0.0.0.0"),
                port=int(os.getenv("CHAT_DOOR_PORT", "8080")))


if __name__ == "__main__":  # pragma: no cover
    main()
