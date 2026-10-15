"""Small JSON Schema subset validator used by the offline Runtime tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class SchemaValidationError(ValueError):
    pass


TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def load_schema(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_ref(ref: str, base_dir: Path) -> tuple[dict[str, Any], Path]:
    parsed = urlparse(ref)
    if parsed.scheme or parsed.netloc:
        raise SchemaValidationError(f"Remote schema refs are unsupported: {ref}")
    target = (base_dir / parsed.path).resolve()
    return load_schema(target), target.parent


def validate(
    instance: Any,
    schema: dict[str, Any],
    base_dir: Path,
    path: str = "$",
) -> None:
    if "$ref" in schema:
        target, target_base = _resolve_ref(schema["$ref"], base_dir)
        validate(instance, target, target_base, path)
        return

    expected = schema.get("type")
    if expected is not None:
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(
            isinstance(instance, TYPE_MAP[item])
            and not (item in {"integer", "number"} and isinstance(instance, bool))
            for item in allowed
        ):
            raise SchemaValidationError(
                f"{path}: expected {allowed}, got {type(instance).__name__}"
            )

    if "const" in schema and instance != schema["const"]:
        raise SchemaValidationError(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaValidationError(f"{path}: value is not in enum")
    if isinstance(instance, str) and len(instance) < schema.get("minLength", 0):
        raise SchemaValidationError(f"{path}: string is shorter than minLength")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaValidationError(f"{path}: value is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaValidationError(f"{path}: value is above maximum")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            raise SchemaValidationError(f"{path}: missing required keys {missing}")
        properties = schema.get("properties", {})
        for key, value in instance.items():
            if key in properties:
                validate(value, properties[key], base_dir, f"{path}.{key}")
            else:
                additional = schema.get("additionalProperties", True)
                if additional is False:
                    raise SchemaValidationError(f"{path}: unexpected key {key!r}")
                if isinstance(additional, dict):
                    validate(value, additional, base_dir, f"{path}.{key}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise SchemaValidationError(f"{path}: array has fewer than {schema['minItems']} items")
        item_schema = schema.get("items")
        if item_schema:
            for index, value in enumerate(instance):
                validate(value, item_schema, base_dir, f"{path}[{index}]")
        if schema.get("uniqueItems"):
            serialized = [json.dumps(value, sort_keys=True) for value in instance]
            if len(serialized) != len(set(serialized)):
                raise SchemaValidationError(f"{path}: array items are not unique")


def validate_file(instance: Any, schema_path: Path) -> None:
    validate(instance, load_schema(schema_path), schema_path.resolve().parent)
