"""bundle — a meeting as a portable file (meeting-bundle.v1), out of one deployment and into another.

Front door (P6): import from here, never a deep module path.

The contract is ``deploy/contracts/meeting-bundle.v1`` (schema, goldens, a Node re-derivation and a
README written for third parties). This module is its one exporter and one importer:

  * ``build_router(store, recording_repo, storage, ...)`` — ``GET /meetings/{meeting_id}/export`` and
    ``POST /meetings/import`` (the unified app mounts them);
  * ``write_bundle`` / ``read_bundle`` — the pure codec (bytes in, bytes or a validated bundle out);
  * ``export_meeting`` / ``import_bundle`` — the flows over the transcript store, the recording repo
    and object storage, the ports meeting-api already owns (no new table, no new store method);
  * ``BundleRefused`` — every refusal, carrying one contract ``Refusal`` code.
"""
from __future__ import annotations

from .codec import (
    CONTRACT,
    FORMAT_VERSION,
    BundleRefused,
    MediaBlob,
    ParsedBundle,
    read_bundle,
    write_bundle,
)
from .router import build_router
from .service import ExportError, deployment_id, export_meeting, import_bundle

__all__ = [
    "CONTRACT",
    "FORMAT_VERSION",
    "BundleRefused",
    "ExportError",
    "MediaBlob",
    "ParsedBundle",
    "build_router",
    "deployment_id",
    "export_meeting",
    "import_bundle",
    "read_bundle",
    "write_bundle",
]
