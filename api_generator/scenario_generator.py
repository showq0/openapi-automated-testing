"""Generate valid and invalid request payloads from OpenAPI schemas."""

from __future__ import annotations

import copy
import json
from typing import Any
from openapi_parser import _sample, resolve_schema

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
