"""Import OpenAPI into Bruno and append schema-derived request scenarios."""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
SCHEMA_KEYS = {
    "type", "required", "enum", "format", "minimum", "maximum", "minLength",
    "maxLength", "minItems", "maxItems", "properties", "items", "additionalProperties",
}


def _yaml_module():
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError("PyYAML is required. Install it with `python3 -m pip install -r requirements.txt`.") from exc
    return yaml


def load_spec(source: Path) -> dict[str, Any]:
    spec = _yaml_module().safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict):
        raise ValueError("The input does not contain a valid OpenAPI `paths` object.")
    return spec


def resolve_schema(schema: Any, spec: dict[str, Any], seen: set[str] | None = None) -> Any:
    """Resolve local JSON pointers and recursively dereference schema nodes."""
    if not isinstance(schema, dict):
        return schema
    seen = set() if seen is None else seen
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/") and ref not in seen:
        target: Any = spec
        for part in ref[2:].split("/"):
            target = target.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(target, dict) else {}
        merged = dict(target) if isinstance(target, dict) else {}
        merged.update({key: value for key, value in schema.items() if key != "$ref"})
        return resolve_schema(merged, spec, seen | {ref})
    result = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            result[key] = {name: resolve_schema(child, spec, seen) for name, child in value.items()}
        elif key in {"items", "additionalProperties"} and isinstance(value, dict):
            result[key] = resolve_schema(value, spec, seen)
        elif key in {"allOf", "oneOf", "anyOf"} and isinstance(value, list):
            result[key] = [resolve_schema(child, spec, seen) for child in value]
        else:
            result[key] = value
    return result


def extract_constraints(schema: Any, path: str = "$", found: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return each recognized schema constraint with its JSON-style location."""
    found = [] if found is None else found
    if not isinstance(schema, dict):
        return found
    constraints = {key: copy.deepcopy(schema[key]) for key in SCHEMA_KEYS if key in schema}
    if constraints:
        found.append({"path": path, "constraints": constraints})
    required = set(schema.get("required", []))
    for name, child in schema.get("properties", {}).items():
        child_path = f"{path}.{name}"
        extract_constraints(child, child_path, found)
        if name in required:
            # Required is a property-level constraint; record it even when the
            # child has no other validation keywords.
            child_constraints = next((item for item in found if item["path"] == child_path), None)
            if child_constraints is None:
                child_constraints = {"path": child_path, "constraints": {}}
                found.append(child_constraints)
            child_constraints["constraints"]["required"] = True
    if isinstance(schema.get("items"), dict):
        extract_constraints(schema["items"], f"{path}[]", found)
    if isinstance(schema.get("additionalProperties"), dict):
        extract_constraints(schema["additionalProperties"], f"{path}.*", found)
    for keyword in ("allOf", "oneOf", "anyOf"):
        for index, child in enumerate(schema.get(keyword, [])):
            extract_constraints(child, f"{path}.{keyword}[{index}]", found)
    return found


def _sample(schema: Any, spec: dict[str, Any]) -> Any:
    schema = resolve_schema(schema or {}, spec)
    if not isinstance(schema, dict):
        return None
    for union in ("oneOf", "anyOf"):
        if schema.get(union):
            return _sample(schema[union][0], spec)
    if schema.get("allOf"):
        combined: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        for part in schema["allOf"]:
            part = resolve_schema(part, spec)
            if isinstance(part, dict):
                combined.update({key: value for key, value in part.items() if key not in {"properties", "required"}})
                combined["properties"].update(part.get("properties", {}))
                combined["required"].extend(part.get("required", []))
        return _sample(combined, spec)
    if "example" in schema:
        return copy.deepcopy(schema["example"])
    if "default" in schema:
        return copy.deepcopy(schema["default"])
    if schema.get("enum"):
        return copy.deepcopy(schema["enum"][0])
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        required = schema.get("required", [])
        props = schema.get("properties", {})
        value = {key: _sample(props[key], spec) for key in required if key in props} or {
            key: _sample(value, spec) for key, value in props.items()
        }
        if isinstance(schema.get("additionalProperties"), dict):
            value.setdefault("example", _sample(schema["additionalProperties"], spec))
        return value
    if kind == "array":
        count = max(1, int(schema.get("minItems", 1)))
        if "maxItems" in schema:
            count = min(count, int(schema["maxItems"]))
        return [_sample(schema.get("items", {}), spec) for _ in range(min(count, 5))]
    if kind == "string":
        fmt = schema.get("format")
        examples = {"date": "2026-01-01", "date-time": "2026-01-01T00:00:00Z", "email": "user@example.com", "uuid": "123e4567-e89b-12d3-a456-426614174000", "uri": "https://example.com"}
        if fmt in examples:
            return examples[fmt]
        length = max(1, int(schema.get("minLength", 1)))
        value = "x" * min(length, 128)
        return value[:schema.get("maxLength", len(value))]
    if kind == "integer":
        return int(schema.get("minimum", 1 if schema.get("exclusiveMinimum") is True else 0))
    if kind == "number":
        return float(schema.get("minimum", 1 if schema.get("exclusiveMinimum") is True else 0))
    if kind == "boolean":
        return True
    return None


def _body_schema(operation: dict[str, Any], spec: dict[str, Any]) -> Any:
    request_body = operation.get("requestBody") or {}
    content = request_body.get("content", {}) if isinstance(request_body, dict) else {}
    for media_type in ("application/json", *content.keys()):
        media = content.get(media_type)
        if isinstance(media, dict) and isinstance(media.get("schema"), dict):
            schema = copy.deepcopy(media["schema"])
            if "example" in media and "example" not in schema:
                schema["example"] = media["example"]
            elif "examples" in media and "example" not in schema:
                example = next(iter(media["examples"].values()), {})
                if isinstance(example, dict) and "value" in example:
                    schema["example"] = example["value"]
            return resolve_schema(schema, spec)
    return None


def _mutate_at(payload: Any, path: list[str], value: Any, remove: bool = False) -> Any:
    result = copy.deepcopy(payload)
    if not path:
        return value
    parent = result
    for part in path[:-1]:
        parent = parent[int(part)] if isinstance(parent, list) else parent[part]
    last = path[-1]
    if isinstance(parent, dict):
        if remove:
            parent.pop(last, None)
        else:
            parent[last] = value
    elif isinstance(parent, list):
        parent[int(last)] = value
    return result


def generate_payload_scenarios(schema: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Create one valid sample and bounded invalid variants from constraints."""
    schema = resolve_schema(schema, spec)
    base = _sample(schema, spec)
    scenarios = [{"name": "Valid payload", "payload": base, "valid": True}]

    def walk(node: Any, path: list[str]) -> None:
        if not isinstance(node, dict):
            return
        required = node.get("required", [])
        for name in required:
            scenarios.append({"name": f"Missing required {'.'.join(path + [name])}", "payload": _mutate_at(base, path + [name], None, True), "valid": False})
        if "enum" in node and node["enum"]:
            scenarios.append({"name": f"Invalid enum at {'.'.join(path) or '$'}", "payload": _mutate_at(base, path, "__invalid_enum__"), "valid": False})
        if node.get("format") in {"date", "date-time", "email", "uuid", "uri", "url"}:
            scenarios.append({"name": f"Invalid format at {'.'.join(path) or '$'}", "payload": _mutate_at(base, path, "not-a-valid-format"), "valid": False})
        for bound in ("minimum", "maximum"):
            if bound in node:
                value = node[bound] - 1 if bound == "minimum" else node[bound] + 1
                scenarios.append({"name": f"Out of range at {'.'.join(path) or '$'}", "payload": _mutate_at(base, path, value), "valid": False})
        for bound in ("minLength", "maxLength"):
            if bound in node:
                length = max(0, node[bound] - 1) if bound == "minLength" else node[bound] + 1
                scenarios.append({"name": f"Invalid string length at {'.'.join(path) or '$'}", "payload": _mutate_at(base, path, "x" * min(length, 256)), "valid": False})
        for bound in ("minItems", "maxItems"):
            if bound in node:
                count = max(0, node[bound] - 1) if bound == "minItems" else node[bound] + 1
                scenarios.append({"name": f"Invalid item count at {'.'.join(path) or '$'}", "payload": _mutate_at(base, path, [_sample(node.get("items", {}), spec) for _ in range(min(count, 20))]), "valid": False})
        for name, child in node.get("properties", {}).items():
            walk(child, path + [name])
        if isinstance(node.get("items"), dict):
            walk(node["items"], path + ["0"])
        if isinstance(node.get("additionalProperties"), dict):
            walk(node["additionalProperties"], path + ["example"])

    walk(schema, [])
    # Constraint-driven cases can repeat at the same location; keep distinct payload/name pairs.
    unique = []
    seen = set()
    for scenario in scenarios:
        key = (scenario["name"], json.dumps(scenario["payload"], sort_keys=True, default=str))
        if key not in seen:
            seen.add(key)
            unique.append(scenario)
    return unique


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


def convert(source: Path, output: Path, collection_name: str | None = None) -> None:
    """Use Bruno's importer, then append generated request scenarios."""
    source = source.expanduser().resolve()
    output = output.expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"OpenAPI source file does not exist: {source}")
    _yaml_module()  # Fail early with an actionable dependency message.
    load_spec(source)  # Validate the document before Bruno writes the collection.
    executable = shutil.which("bru")
    if executable is None:
        raise RuntimeError("Bruno CLI was not found; install it and ensure `bru` is on PATH.")
    name = collection_name or source.stem.replace("_", " ").replace("-", " ").title()
    subprocess.run([executable, "import", "openapi", "--source", str(source), "--output", str(output), "--collection-name", name], check=True)
    count = generate_collection_scenarios(source, output)
    print(f"Generated {count} schema-based Bruno request scenarios in {output}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Path to an OpenAPI YAML or JSON specification")
    parser.add_argument("output", type=Path, help="Directory where Bruno creates the collection")
    parser.add_argument("--name", help="Bruno collection name (defaults to the source filename)")
    args = parser.parse_args(argv)

    try:
        convert(args.source, args.output, args.name)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"Bruno import failed with exit code {exc.returncode}.", file=sys.stderr)
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

