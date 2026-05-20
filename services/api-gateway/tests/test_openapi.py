"""Regression tests for api-gateway OpenAPI auth schemes."""
from main import app


def _fresh_openapi_schema():
    app.openapi_schema = None
    return app.openapi()


def test_openapi_security_schemes_match_runtime_headers():
    schema = _fresh_openapi_schema()

    assert schema["components"]["securitySchemes"]["ApiKeyAuth"]["name"] == "X-API-Key"
    assert (
        schema["components"]["securitySchemes"]["AdminApiKeyAuth"]["name"]
        == "X-Admin-API-Key"
    )


def test_openapi_operations_reference_defined_security_schemes():
    schema = _fresh_openapi_schema()
    defined_schemes = set(schema["components"]["securitySchemes"])
    referenced_schemes = {
        scheme_name
        for path in schema["paths"].values()
        for operation in path.values()
        for requirement in operation.get("security", [])
        for scheme_name in requirement
    }

    assert referenced_schemes <= defined_schemes
    assert "APIKeyHeader" not in referenced_schemes


def test_openapi_admin_routes_use_admin_api_key_header():
    schema = _fresh_openapi_schema()

    admin_security = schema["paths"]["/admin/{path}"]["get"]["security"]
    assert admin_security == [{"AdminApiKeyAuth": []}]


def test_openapi_client_routes_use_client_api_key_header():
    schema = _fresh_openapi_schema()

    bots_security = schema["paths"]["/bots"]["post"]["security"]
    assert bots_security == [{"ApiKeyAuth": []}]
