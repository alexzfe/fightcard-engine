"""Enum values are closed within /v1/ (docs/CONTRACT.md, Versioning): consumers such as the site validate them
strictly, so a new value in an existing enum breaks them and ships as /v2/. This pins every enum in the published
/v1/schema.json. A new enum on a new field is additive: add it here with the field."""

from fightcard.publish.build import schema

# Keyed by where the enum sits: "<Def>.<field>" then the JSON path inside the field ("<Def>" alone for a def that is
# an enum). Values compare as sets: reordering a Literal is no change for a client.
V1_ENUMS = {
    "Event.status": ["upcoming", "completed"],
    "Fight.finish_tier": ["high", "medium", "low"],
    "Fight.segment": ["main", "prelims", "early_prelims"],
    "ModelInfo.tiers": ["low", "medium", "high"],  # its keys
    "PastFight.result": ["win", "loss", "draw", "nc"],
    "Result.outcome": ["win", "draw", "nc"],
}


def enum_field(where: str) -> str:
    """The field an enum belongs to: "ModelInfo.tiers" for "ModelInfo.tiers/anyOf/0/propertyNames"."""
    return where.split("/")[0]


def enums_in(tree: dict) -> dict[str, set]:
    """Every enum under the schema's $defs, wherever it sits in a def."""
    found = {}

    def walk(node, path):
        if isinstance(node, dict):
            if "enum" in node:
                if len(path) >= 3 and path[1] == "properties":
                    head, rest = f"{path[0]}.{path[2]}", path[3:]
                else:
                    head, rest = path[0], path[1:]
                found["/".join([head, *rest])] = set(node["enum"])
            for key, value in node.items():
                walk(value, [*path, key])
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, [*path, str(i)])

    for name, definition in tree["$defs"].items():
        walk(definition, [name])
    return found


def published_enums() -> dict[str, set]:
    return enums_in(schema())


def count_enums(node) -> int:
    if isinstance(node, dict):
        return ("enum" in node) + sum(count_enums(value) for value in node.values())
    if isinstance(node, list):
        return sum(count_enums(value) for value in node)
    return 0


def test_the_walk_finds_every_enum_node():
    """Two enums under one field (dict[Literal, Literal]) and a def that is itself an enum (an enum.Enum class)."""
    tree = {"$defs": {
        "Grade": {"enum": ["a", "b"], "type": "string"},
        "Box": {"properties": {"marks": {"additionalProperties": {"enum": ["x"]}, "propertyNames": {"enum": ["y"]}}}},
    }}
    assert enums_in(tree) == {
        "Grade": {"a", "b"},
        "Box.marks/additionalProperties": {"x"},
        "Box.marks/propertyNames": {"y"},
    }
    assert count_enums(tree) == 3


def test_v1_enums_are_closed():
    assert published_enums() == V1_ENUMS


def test_every_enum_in_the_schema_is_pinned():
    assert count_enums(schema()) == len(published_enums())
