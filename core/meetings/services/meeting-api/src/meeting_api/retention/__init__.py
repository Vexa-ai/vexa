"""Public front door for Minutes erasure and clock-controlled expiry policy."""

from .service import ErasureFailed, ErasureReceipt, erase_meeting
from .ttl import (
    MAX_TTL_BATCH,
    DueScope,
    ScopeExpiries,
    TtlBatchFailed,
    TtlBatchReceipt,
    materialize_scope_expiries,
    run_ttl_batch,
)
from .ttl_adapters import run_production_ttl_once

__all__ = [
    "ErasureFailed",
    "ErasureReceipt",
    "erase_meeting",
    "MAX_TTL_BATCH",
    "DueScope",
    "ScopeExpiries",
    "TtlBatchFailed",
    "TtlBatchReceipt",
    "materialize_scope_expiries",
    "run_ttl_batch",
    "run_production_ttl_once",
]
