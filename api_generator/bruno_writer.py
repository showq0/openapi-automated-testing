from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openapi_parser import METHODS, _body_schema, _sample, load_spec, resolve_schema, _yaml_module
from scenario_generator import generate_payload_scenarios

def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-.") or "scenario"
def _response_sample(operation: dict[str, Any], status: int, spec: dict[str, Any]) -> tuple[str, Any]:
    responses = operation.get("responses", {})
    response = responses.get(str(status), responses.get(status, {}))
    description = response.get("description", "Expected status for this generated scenario.") if isinstance(response, dict) else "Expected status for this generated scenario."
    if not isinstance(response, dict):
        return description, None
    for media in response.get("content", {}).values():
        if not isinstance(media, dict):
            continue
        if "example" in media:
            return description, media["example"]
        if media.get("examples"):
            example = next(iter(media["examples"].values()))
            if isinstance(example, dict) and "value" in example:
                return description, example["value"]
        if isinstance(media.get("schema"), dict):
            return description, _sample(media["schema"], spec)
    return description, None
def _write_scenario(collection: Path, folder: str, method: str, route: str, operation: dict[str, Any], parameters: list[dict[str, Any]], scenario: dict[str, Any], sequence: int, spec: dict[str, Any]) -> None:
    yaml = _yaml_module()
    group = _safe_name(folder)
    target_dir = collection / group
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{sequence:03d}-{_safe_name(scenario['name']).lower()}.yml"
    expected = next((int(code) for code in operation.get("responses", {}) if str(code).startswith("4")), 400) if not scenario["valid"] else next((int(code) for code in operation.get("responses", {}) if str(code).startswith("2")), 200)
    sample_url = re.sub(r"\{([^{}]+)\}", r"{{\1}}", route)
    servers = operation.get("servers") or []
    if not servers:
        # Collection imported by Bruno typically uses a baseUrl variable.
        sample_url = "{{baseUrl}}" + route
    response_description, response_body = _response_sample(operation, expected, spec)
    request: dict[str, Any] = {
        "info": {"name": scenario["name"], "type": "http", "seq": sequence, "tags": operation.get("tags", [])},
        "http": {"method": method.upper(), "url": sample_url, "auth": "inherit"},
        "runtime": {"scripts": [{"type": "tests", "code": f'test("responds with expected status {expected}", function () {{ expect(res.status).to.equal({expected}); }});'}]},
        "docs": operation.get("description") or operation.get("summary", "Generated OpenAPI schema scenario."),
    }
    request_params: dict[str, list[dict[str, Any]]] = {}
    headers = []
    for parameter in parameters:
        if not isinstance(parameter, dict):
            continue
        parameter = resolve_schema(parameter, spec)
        location = parameter.get("in")
        value = parameter.get("example", parameter.get("schema", {}).get("example", _sample(parameter.get("schema", {}), spec)))
        entry = {"name": parameter.get("name", ""), "value": value}
        if parameter.get("description"):
            entry["description"] = parameter["description"]
        if location == "header":
            headers.append(entry)
        elif location in {"path", "query"}:
            request_params.setdefault(location, []).append(entry)
    if headers:
        request["http"]["headers"] = headers
    if request_params:
        request["http"]["params"] = request_params
    if scenario.get("payload") is not None:
        request["http"]["body"] = {"type": "json", "data": json.dumps(scenario["payload"], indent=2, ensure_ascii=False)}
    example_response: dict[str, Any] = {"status": expected}
    if response_body is not None:
        example_response["body"] = {"type": "json", "data": json.dumps(response_body, indent=2, ensure_ascii=False)}
    request["examples"] = [{"name": f"Expected {expected} response", "description": response_description, "request": {"url": sample_url, "method": method.upper()}, "response": example_response}]
    (target_dir / filename).write_text(yaml.safe_dump(request, sort_keys=False, allow_unicode=True), encoding="utf-8")
def generate_collection_scenarios(source: Path, collection: Path) -> int:
    spec = load_spec(source)
    generated = 0
    servers = spec.get("servers") or [{"url": "{{baseUrl}}"}]
    for route, path_item in spec["paths"].items():
        if not isinstance(path_item, dict):
            continue
        folder = (path_item.get("tags") or ["Generated scenarios"])[0] if isinstance(path_item.get("tags"), list) else "Generated scenarios"
        path_parameters = path_item.get("parameters", [])
        for method, operation in path_item.items():
            if method.lower() not in METHODS or not isinstance(operation, dict):
                continue
            body_schema = _body_schema(operation, spec)
            if body_schema is None:
                continue
            parameter_map = {(item.get("in"), item.get("name")): item for item in path_parameters if isinstance(item, dict)}
            parameter_map.update({(item.get("in"), item.get("name")): item for item in operation.get("parameters", []) if isinstance(item, dict)})
            parameters = list(parameter_map.values())
            # Each request filename is unique across endpoints in one tag folder.
            for scenario in generate_payload_scenarios(body_schema, spec):
                generated += 1
                _write_scenario(collection, folder, method, route, operation, parameters, scenario, generated, spec)
    return generated
