"""vexa-slim — a minimal, gateway-only client for the meeting/agent control plane.

Public surface (highest → lowest):
    from vexa_slim import listen_to_meeting, agent_on_meeting, harvest, Harvest   # cookbook: intent
    from vexa_slim import Slim                                                    # SDK: mechanism

Everything speaks api.v1 through the gateway only — no redis, no internals — so the client doubles as
a living proof of the `meetings ⊥ agent` separation.
"""
from .client import Slim
from .cookbook import (
    agent_on_meeting,
    browse_workspace,
    chat,
    connect,
    init_workspace,
    list_routines,
    listen_to_meeting,
    meeting_doc,
    mount_workspace,
    onboard,
    read_workspace_file,
    schedule_routine,
    set_routine_enabled,
    whoami,
)
from .harvest import harvest
from .models import Harvest

__all__ = [
    "Slim",
    # identity & connect (bootstrap — the inverted verb; blocked on D-F, see plan H7)
    "connect", "whoami",
    # meeting-processor agent (live Harvest; finished-file fold is a lower-level mechanism)
    "listen_to_meeting", "agent_on_meeting", "harvest", "meeting_doc", "Harvest",
    # chat over the workspace (meeting → MCP REST read; files → prompt-injected Read) + onboarding
    "chat", "onboard",
    # cadence & automate (🔌 wireable now — scheduling skill + reconciler exist)
    "schedule_routine", "list_routines", "set_routine_enabled",
    # workspace controls
    "browse_workspace", "read_workspace_file", "init_workspace", "mount_workspace",
]
