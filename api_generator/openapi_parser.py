from __future__ import annotations

import copy
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
