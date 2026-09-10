"""flows-api as a compose service, and the assembler wired to it (offline — no stack, no docker).

The live proof is a real boot on bbb: bring up identity + meetings + flows + mcp + gateway with the
agent domain ABSENT, and the MCP assembly at the gateway's `/mcp` must show flows' four tools
beside the 14. These assertions pin the WIRING that proof depends on, so it cannot rot between runs
of an expensive test — and so a reviewer can see, without a stack, that the interim was actually
replaced.

THE INTERIM THIS REPLACES: the flows lanes run on the host out of `~/.storm/flows-up.sh` on
loopback :18200/:18201, and the compose-network `mcp` service was pointed at the docker BRIDGE
ADDRESS of that host lane so it could fetch `/.well-known/mcp-tools.json`. A host-specific IP,
written into a deployment, for a service the deployment does not run.
"""
from __future__ import annotations

import json
import pytest
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"


def _service(name: str) -> str:
    """One service block, as text — the same line-wise read `gate:config-contract` uses."""
    lines = COMPOSE.read_text().split("\n")
    start = next(i for i, l in enumerate(lines) if l.rstrip() == f"  {name}:")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i][:3].strip() and not lines[i].startswith("   ") and lines[i].strip():
            end = i
            break
    return "\n".join(lines[start:end])


def test_the_stack_defines_a_flows_api_service():
    block = _service("flows-api")
    assert "context: ../.." in block, "flows' image builds from the repo root (it COPYs behavior/)"
    assert "core/flows/Dockerfile" in block
    assert "flows_integrations.flows_api" in block, "the api entrypoint is a command override"


def test_flows_api_listens_on_the_network_not_its_own_loopback():
    """The single reason the bridge-address hack existed. Said out loud in the service's own
    environment rather than flipped as a default, so the exposure is visible where the port is."""
    block = _service("flows-api")
    assert "VEXA_FLOWS_API_HOST=0.0.0.0" in block
    assert re.search(r"VEXA_FLOWS_API_PORT=\$\{VEXA_FLOWS_API_PORT:-8200\}", block), block


def test_flows_api_has_a_healthcheck_and_depends_on_identity_only():
    """`depends_on` names what flows-api cannot start without: its database and the identity it
    authenticates against. NOT meeting-api, NOT agent-api — flows reaches those at step time and
    the whole point of decision 40's assembly is that a deployment may not run them at all. A
    depends_on there would make an optional domain a boot requirement."""
    block = _service("flows-api")
    assert "healthcheck:" in block and "/health" in block

    deps = block.split("depends_on:")[1]
    assert "postgres:" in deps and "admin-api:" in deps, deps
    for optional in ("meeting-api", "agent-api", "mcp", "gateway"):
        assert f"{optional}:" not in deps, f"{optional} is not something flows-api needs to BOOT"


def test_the_doors_are_service_names():
    """#1453 made the doors required-explicit: unnamed is a loud refusal at the moment of use.
    On the compose network they are service names — never a host IP, never a bridge address."""
    block = _service("flows-api")
    assert "VEXA_FLOWS_ADMIN_API_URL=http://admin-api:8001" in block
    assert "VEXA_FLOWS_GATEWAY_URL=http://gateway:8000" in block

    # The ENVIRONMENT only. A published port is `127.0.0.1:<port>` by design (loopback on the host,
    # not the box's public interfaces) and the healthcheck's `localhost` is the container's own —
    # both correct. What must never appear is a host address as a DOOR.
    env = block.split("environment:")[1].split("healthcheck:")[0]
    for banned in ("172.17.", "172.18.", "host.docker.internal", "127.0.0.1", "localhost"):
        assert banned not in env, f"a host-specific address survives as a door: {banned}"


def test_the_assembler_is_pointed_at_the_service():
    """THE POINT OF THE BRANCH. `mcp` asked for flows' manifest at whatever `VEXA_FLOWS_API_URL`
    said, which is how a bridge address ended up being the answer. It now defaults to the service."""
    block = _service("mcp")
    assert re.search(r"FLOWS_API_URL=\$\{VEXA_FLOWS_API_URL:-http://flows-api:8200\}", block), \
        "mcp still has no default route to the flows service"


def test_the_mailbox_is_a_lane_of_its_own_and_off_by_default():
    """Mail is an OPTIONAL INTAKE, and a profile is how compose says so.

    The no-agents MCP product boots identity + meetings + flows + mcp + gateway and never touches a
    mailbox. A lane that needs a real IMAP credential must therefore not be in the default
    `docker compose up`: it would restart-loop on a stock stack and read as a broken deployment,
    which is the same defect as a capability shipped dark, pointing the other way."""
    block = _service("flows-mailbox")
    assert 'profiles: ["mailbox"]' in block, "the mail lane must not start on a stock stack"
    assert "flows_integrations.mailbox" in block, "the mailbox entrypoint is a command override"
    assert "core/flows/Dockerfile" in block, "same image as flows-api — one image, three entrypoints"


def test_both_flows_lanes_carry_the_same_configuration():
    """ONE DECLARATION, TWO LANES. `config.v1` is declared for the flows DOMAIN, and
    `gate:config-contract` reads the flows-api block; a mail policy that held in one lane and not
    the other would pass that gate and still be wrong. Same keys, both lanes."""
    def keys(name):
        lines = _service(name).split("\n")
        start = lines.index("    environment:") + 1
        out = set()
        for line in lines[start:]:
            if line.strip() and not line.startswith("      "):
                break
            m = re.match(r"\s*-\s*([A-Z][A-Z0-9_]*)=", line)
            if m:
                out.add(m.group(1))
        return out

    api, mailbox = keys("flows-api"), keys("flows-mailbox")
    assert api == mailbox, {"only flows-api": sorted(api - mailbox),
                            "only flows-mailbox": sorted(mailbox - api)}
    assert "VEXA_FLOWS_MAIL_DOMAINS" in mailbox, "the intake allow-list must reach the intake"


def test_the_link_port_is_declared_and_may_be_empty():
    """PRD decision 4: flows owns a link port, the terminal is one adapter, and the no-agents
    product has none. Empty is a deployment with no terminal — not a deployment that cannot boot,
    which is what a required-explicit door made it."""
    for lane in ("flows-api", "flows-mailbox"):
        assert "VEXA_UI_URL=${VEXA_UI_URL:-}" in _service(lane), lane


def test_the_host_port_is_overridable_and_bound_to_loopback():
    """Published for debugging like every other service here, on 127.0.0.1 so it is not on the
    box's public interfaces, and overridable so two stacks can share a host."""
    block = _service("flows-api")
    assert re.search(r'"127\.0\.0\.1:\$\{FLOWS_API_HOST_PORT:-\d+\}:8200"', block), block


def _agent_carriers_are_private() -> bool:
    """The OSS cut carries no producer for the two desk carriers: `core/flows/contracts/flows.v1/
    carriers.json` marks `desk.unscaffolded` and `claim.proposed` as `published_by: "private"`
    (the producer lives in the private tree). While that flag stands, the honest assertion is the
    OPPOSITE of the one below: agent-api declares NO publish edge and compose sets none. The moment
    a producer lands here and the flag comes off, the original assertions apply unchanged."""
    census = json.loads((ROOT / "core/flows/contracts/flows.v1/carriers.json").read_text())
    entries = census.get("carriers", census)
    items = entries.values() if isinstance(entries, dict) else entries
    agent = [c for c in items if isinstance(c, dict) and c.get("owner") == "agent"]
    return bool(agent) and all(c.get("published_by") == "private" for c in agent)


def test_the_agent_domain_carries_the_publish_edge_it_declares():
    """THE DESK CARDS EXIST ON THE WIRE, or they exist only in the tests.

    agent-api declares `desk.unscaffolded` and `claim.proposed` as a `publish-edge`, and a publish
    edge that no deployment configures is a publisher that never publishes. `targets: []` is how
    that passes every gate: gate:config-contract checks a key against the surfaces the key's own
    `targets` names, so an empty list asks it to check nothing — the edge was declared, gated and
    unit-tested, and set in no deployment we ship.

    The KEYS ARE READ FROM THE DECLARATION rather than named here, so a third key added to the edge
    is covered the moment it is declared."""
    decl = json.loads((ROOT / "core/agent/control_plane/config.v1.json").read_text())
    edge = [k["key"] for k in decl["keys"] if k.get("class") == "publish-edge"]
    block = _service("agent-api")
    if _agent_carriers_are_private():
        assert not edge, (
            "the census says the desk carriers are published privately, yet agent-api declares a "
            "publish edge here — take the flag off carriers.json or drop the declaration")
        assert "VEXA_FLOWS_API_URL=" not in block, (
            "compose sets a flows publish door on agent-api although the OSS cut has no producer")
        return
    assert edge, "agent-api declares no publish edge — the desk cards have no producer"
    for key in edge:
        assert f"- {key}=" in block, (
            f"agent-api declares {key} as part of its publish edge and the compose service sets "
            f"it nowhere; the two desk facts are dropped in the stack we actually ship")


def test_the_agent_publish_edge_points_at_the_flows_service_by_default():
    """Same rule as the assembler above, and the same reason: flows-api is a service in THIS FILE.

    admin-api's edge defaults to EMPTY, which is right there — identity onboards people in
    deployments that carry no flows at all. Here an empty default would mean a plain `up -d` of
    the full stack publishes nothing, the queue shows no desk cards, and every part of the
    mechanism reports itself healthy. Set it empty to run a deployment that genuinely has no
    flows domain."""
    if _agent_carriers_are_private():
        pytest.skip("desk carriers are published privately in this cut — no agent publish edge to point anywhere")
    block = _service("agent-api")
    assert re.search(r"VEXA_FLOWS_API_URL=\$\{VEXA_FLOWS_API_URL:-http://flows-api:8200\}", block), \
        "agent-api has no default route to the flows service, so the desk facts go nowhere"
    # The credential travels as the SAME variable flows-api itself reads — one operator key, passed
    # through, never re-defaulted to a literal (F95). A target without its credential 401s, which
    # looks exactly like a deployment running no flows and is not one.
    assert "VEXA_FLOWS_API_KEY=${VEXA_FLOWS_API_KEY:-}" in block
