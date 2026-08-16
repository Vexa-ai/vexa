"""``chat_door`` — the magic-link entry from an artifact email into the meeting record.

Public surface (the only names other code should reach for):

* :func:`chat_door.app.create_app` — the FastAPI door
* :class:`chat_door.tokens.TokenSigner` / :func:`chat_door.tokens.build_magic_link`
* :func:`chat_door.postman.build_message` — artifact → MIME
* :class:`chat_door.store.FileIdentityStore` — lazy identity + personal instructions
"""
from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.12.0"
