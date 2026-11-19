"""ADR-0047 d1/d8: the CRM is an optional domain. Its tools reach the one MCP only when the
deployment names crm-api's door; without it they are absent, never present-and-failing."""
import json
import pathlib

from vexa_mcp import discover
from vexa_mcp import manifest as m

REPO = pathlib.Path(__file__).resolve().parents[5]
CRM = json.loads((REPO / "core/crm/mcp.tools.v1.json").read_text())


def test_the_crm_door_is_a_deployment_fact():
    assert discover.DOMAIN_URL_ENV["crm"] == CRM["base_url_env"] == "CRM_API_URL"


def test_crm_tools_are_absent_without_the_crm_and_present_with_it():
    without = m.assemble([CRM], deployed={"identity"})
    assert not [t for t in without.tools if t.domain == "crm"]
    with_crm = m.assemble([CRM], deployed={"identity", "crm"})
    names = {t.name for t in with_crm.tools}
    assert {"crm_describe", "crm_search", "crm_read", "crm_history", "crm_change", "crm_review",
            "crm_configure", "crm_bind", "desktop_read", "desktop_arrange", "desktop_arrange_propose"} <= names
    assert all(t.auth == "subject" for t in with_crm.tools)    # the caller's own credential, always


def test_the_desk_tools_are_three_and_review_and_undo_are_not_tools():
    """Desktop D1: an agent reads, arranges (on the person's own request) or proposes; applying a
    suggestion and Undo are the person's confirmations, so no tool reaches them."""
    desk = {t["name"]: t["route"]["path"] for t in CRM["tools"] if t["name"].startswith("desktop_")}
    assert desk == {"desktop_read": "/desk/read", "desktop_arrange": "/desk/arrange",
                    "desktop_arrange_propose": "/desk/arrange/propose"}
    assert not {"/desk/proposal", "/desk/undo"} & {t["route"]["path"] for t in CRM["tools"]}
    assert len(CRM["tools"]) == 16


def test_the_workspace_settings_tools_are_two_on_the_desks_prefix():
    """Per-person workspace settings (the CRM member_of relation): an agent reads them, and sets them
    only on the person's request in their own chat; both ride the desk's `desktop` switch."""
    settings = {t["name"]: t["route"]["path"] for t in CRM["tools"] if t["name"].startswith("workspace_settings_")}
    assert settings == {"workspace_settings_read": "/desk/workspaces/read",
                        "workspace_settings_set": "/desk/workspaces/set"}
    with_crm = {t.name for t in m.assemble([CRM], deployed={"identity", "crm"}).tools}
    assert set(settings) <= with_crm


def test_the_card_tools_are_three_and_every_person_verb_is_not_a_tool():
    """Desktop D2: an agent lists and proposes; making, moving, sharing and accepting are a person's."""
    cards = {t["name"]: t["route"]["path"] for t in CRM["tools"] if t["name"].startswith("card_")}
    assert cards == {"card_list": "/card/list", "card_propose": "/card/propose",
                     "card_update_propose": "/card/update/propose"}
    paths = {t["route"]["path"] for t in CRM["tools"]}
    assert not {"/card/create", "/card/update", "/card/members", "/card/membership", "/card/read"} & paths
