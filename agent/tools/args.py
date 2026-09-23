"""Checking a model's tool arguments against the tool's JSON Schema.

Models get arguments wrong: a missing field, a string for a number, a value
outside an enum. This covers the small subset of JSON Schema our tools use
(string, integer, enum, minimum, maximum, required, no extra keys)
and returns every problem as text, so the loop can hand the list back to
the model instead of crashing or calling AWS with nonsense.
"""

from __future__ import annotations

from typing import Any

TYPES = {"string": str, "integer": int}


def problems(schema: dict[str, Any], args: dict[str, Any]) -> list[str]:
    if "_unparsed" in args:
        return [f"arguments were not a JSON object: {args['_unparsed']!r:.200}"]
    found = []
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in args:
            found.append(f"missing required argument {name!r}")
    for name, value in args.items():
        if name not in properties:
            found.append(f"unknown argument {name!r}; allowed: {sorted(properties)}")
            continue
        rule = properties[name]
        expected = TYPES[rule["type"]]
        # bool is a subclass of int in Python; a model sending true for a
        # number of minutes is still wrong.
        if not isinstance(value, expected) or isinstance(value, bool):
            found.append(f"{name!r} must be {rule['type']}, got {type(value).__name__}")
            continue
        if "enum" in rule and value not in rule["enum"]:
            found.append(f"{name!r} must be one of {rule['enum']}, got {value!r}")
        if "minimum" in rule and value < rule["minimum"]:
            found.append(f"{name!r} must be at least {rule['minimum']}")
        if "maximum" in rule and value > rule["maximum"]:
            found.append(f"{name!r} must be at most {rule['maximum']}")
    return found
