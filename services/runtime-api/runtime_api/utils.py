"""Shared utilities for runtime_api."""


def parse_memory(mem_str: str) -> int:
    """Parse memory string like '2Gi', '512m' to bytes."""
    mem_str = mem_str.strip()
    multipliers = {
        "k": 1024, "ki": 1024,
        "m": 1024**2, "mi": 1024**2,
        "g": 1024**3, "gi": 1024**3,
    }
    for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
        if mem_str.lower().endswith(suffix):
            return int(float(mem_str[:-len(suffix)]) * mult)
    return int(mem_str)
