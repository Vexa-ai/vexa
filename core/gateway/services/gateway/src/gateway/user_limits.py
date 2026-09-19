"""Identity → X-User-Limits: ceiling plus optional per-account spawn ramp.

Both gateway forwarding paths use this encoder. Older identity responses without both
ramp keys keep their bare-integer header byte-for-byte.
"""
import json


def user_limits_header(user_data: dict) -> str:
    max_concurrent = user_data.get("max_concurrent", 3)
    if "ramp_bots" in user_data and "ramp_window_s" in user_data:
        return json.dumps({
            "max_concurrent": max_concurrent,
            "ramp_bots": user_data["ramp_bots"],
            "ramp_window_s": user_data["ramp_window_s"],
        })
    return str(max_concurrent)
