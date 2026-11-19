"""The ADR-0046 decision-12 flows routes — routine claim and timers, concern stages, the event-agent
kill switch, sinks — spelled as flows-api publishes them, for every suite that stubs flows' OpenAPI.
`core/flows/tests/test_manifest_routes_served.py` holds the real flows-api app to the same routes."""

ARGS = {"Claim": ["thread"], "TriggerAdd": ["thread", "name", "trigger", "instruction"],
        "StageMove": ["thread", "stage", "note"],
        "Subscribe": ["thread", "name", "event", "filter", "wake", "instruction"],
        # ADR-0050: a person's sensors
        "SensorBody": ["name", "kind", "filter", "connection_id", "interval_s", "scope"],
        "SensorChange": ["filter", "connection_id", "interval_s"],
        "SensorConnect": ["thread", "wake", "instruction"]}


def _body(ref):
    return {"content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{ref}"}}}}


def _path(name):
    return [{"name": name, "in": "path", "required": True, "schema": {"type": "string"}}]


PATHS = {
    "/flows/{name}/claim": {"post": {"summary": "Claim a routine for this chat", "parameters": _path("name"),
                                     "requestBody": _body("Claim")}},
    "/routines/triggers": {"post": {"summary": "Give a chat another timer", "requestBody": _body("TriggerAdd")}},
    "/concerns/stage": {"post": {"summary": "Move a concern chat along its lifecycle",
                                 "requestBody": _body("StageMove")}},
    "/event-agents/pause": {"post": {"summary": "Pause every running event agent", "parameters": [
        {"name": "everyone", "in": "query", "schema": {"type": "boolean"}}]}},
    "/sinks": {"post": {"summary": "Subscribe a chat to an event stream", "requestBody": _body("Subscribe")}},
    "/sinks/{name}/unsubscribe": {"post": {"summary": "Stop a chat's subscription", "parameters": _path("name")}},
    "/sensors": {"get": {"summary": "Your sensors", "parameters": [
                     {"name": "scope", "in": "query", "schema": {"type": "string"}}]},
                 "post": {"summary": "Make a sensor of your own", "requestBody": _body("SensorBody")}},
    "/sensors/{name}/test": {"post": {"summary": "Dry-run a sensor", "parameters": _path("name") + [
        {"name": "scope", "in": "query", "schema": {"type": "string"}}]}},
    "/sensors/{name}/connect": {"post": {"summary": "Connect a sensor to a chat", "parameters": _path("name") + [
        {"name": "scope", "in": "query", "schema": {"type": "string"}}], "requestBody": _body("SensorConnect")}},
    "/sensors/{name}/{action}": {"post": {"summary": "Enable, pause, update or delete a sensor",
                                          "parameters": _path("name") + _path("action") + [
        {"name": "scope", "in": "query", "schema": {"type": "string"}}], "requestBody": _body("SensorChange")}},
}
SCHEMAS = {k: {"type": "object", "title": k,
               "properties": {a: {"type": "object" if a in ("trigger",) or (a == "filter" and k == "Subscribe")
                                  else "number" if a == "interval_s" else "string"} for a in v}}
           for k, v in ARGS.items()}


def extend(openapi: dict) -> dict:
    openapi.setdefault("paths", {}).update(PATHS)
    openapi.setdefault("components", {}).setdefault("schemas", {}).update(SCHEMAS)
    return openapi
