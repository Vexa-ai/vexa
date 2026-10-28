"""The MCP tool call waits longer than the gateway's forward (Vexa-ai/vexa#1783).

When the two were equal (30 s each) the MCP client gave up a few milliseconds before the gateway
answered, so a slow service surfaced as this client's own anonymous timeout instead of the
gateway's answer naming the hop that was slow. The outer hop must outlast the inner one.
"""
from vexa_mcp import register

GATEWAY_FORWARD_TIMEOUT_S = 30.0  # gateway.adapters.FORWARD_TIMEOUT_S


def test_the_tool_call_outlasts_the_gateway_forward():
    assert register.TOOL_TIMEOUT_S >= GATEWAY_FORWARD_TIMEOUT_S + 5
