"""OpenAPI contract tests: versioned paths, exact DTO schemas, docs version."""

from __future__ import annotations

import json


def test_openapi_contains_contract_paths_and_schemas(client):
    spec = client.get("/openapi.json").json()

    assert spec["openapi"].startswith("3.")
    assert spec["info"]["version"] == "0.1.0"
    assert spec["info"]["title"] == "Procedural Detective API"

    paths = spec["paths"]
    assert "/api/v1/health" in paths
    assert "/api/v1/readiness" in paths

    # Health 200 -> HealthResponse
    health_get = paths["/api/v1/health"]["get"]
    health_ref = health_get["responses"]["200"]["content"]["application/json"]["schema"]
    assert health_ref in ({"$ref": "#/components/schemas/HealthResponse"},)

    # Readiness 200 -> ReadinessResponse, 503 -> ErrorResponse
    readiness_get = paths["/api/v1/readiness"]["get"]
    ready_ref = readiness_get["responses"]["200"]["content"]["application/json"]["schema"]
    assert ready_ref in ({"$ref": "#/components/schemas/ReadinessResponse"},)
    assert "ErrorResponse" in json.dumps(readiness_get["responses"]["503"])

    components = spec["components"]["schemas"]
    assert "HealthResponse" in components
    assert "ReadinessResponse" in components
    assert "ErrorResponse" in components

    health_schema = components["HealthResponse"]
    assert health_schema["properties"]["status"]["default"] == "ok"
    assert health_schema["properties"]["service"]["default"] == "procedural-detective"
    assert health_schema["properties"]["version"]["default"] == "0.1.0"

    error_schema = components["ErrorResponse"]
    assert "error" in error_schema["properties"]
    # FastAPI components factor nested models into their own schema entries.
    assert error_schema["properties"]["error"] == {"$ref": "#/components/schemas/ErrorBody"}
    error_body = components["ErrorBody"]
    assert set(error_body["properties"].keys()) == {"code", "message", "details"}
    ed = error_body["properties"]["details"]
    assert ed.get("anyOf") is not None or ed.get("type") in ("object", "null")

    ready_schema = components["ReadinessResponse"]
    assert ready_schema["properties"]["database"]["default"] == "ok"
    assert ready_schema["properties"]["migrations"]["default"] == "ok"


def test_docs_page_served_with_version(client):
    res = client.get("/docs")
    assert res.status_code == 200
    assert "swagger" in res.text.lower()


def test_health_response_model_matches_contract_shape(client):
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert set(res.json().keys()) == {"status", "service", "version"}
    assert all(isinstance(v, str) for v in res.json().values())