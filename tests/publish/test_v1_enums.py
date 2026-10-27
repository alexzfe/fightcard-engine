"""Enum values are closed within /v1/ (docs/CONTRACT.md, Versioning): consumers such as the site validate them
strictly, so a new value in an existing enum breaks them and ships as /v2/. This pins every enum in the published
/v1/schema.json. A new enum on a new field is additive: add it here with the field."""

from fightcard.publish.build import schema

V1_ENUMS = {
    "Event.status": ["upcoming", "completed"],
    "Fight.finish_tier": ["high", "medium", "low"],
    "Fight.segment": ["main", "prelims", "early_prelims"],
    "ModelInfo.tiers": ["low", "medium", "high"],  # its keys
    "PastFight.result": ["win", "loss", "draw", "nc"],
    "Result.outcome": ["win", "draw", "nc"],
}


def published_enums() -> dict[str, list]:
    found = {}

    def walk(node, where):
        if isinstance(node, dict):
            if "enum" in node:
                found[where] = node["enum"]
            for value in node.values():
                walk(value, where)
        elif isinstance(node, list):
            for value in node:
                walk(value, where)

    for name, definition in schema()["$defs"].items():
        for field, prop in definition.get("properties", {}).items():
            walk(prop, f"{name}.{field}")
    return found


def test_v1_enums_are_closed():
    assert published_enums() == V1_ENUMS
