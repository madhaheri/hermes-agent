#!/usr/bin/env python3
"""Validate the review-only custom skill sources under docs/."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = ROOT / "docs" / "hermes-custom-skills"
REQUIRED_FIELDS = {"name", "description", "version", "author", "license", "platforms", "metadata"}
REQUIRED_SECTIONS = {
    "When to Use",
    "Prerequisites",
    "How to Run",
    "Quick Reference",
    "Procedure",
    "Safety and Read-Back",
    "Pitfalls",
    "Verification",
}
EXPECTED_NAMES = {
    "z840-model-benchmarking",
    "pair-capability-router",
    "zimaos-ollama-health",
    "hermes-gateway-maintenance",
    "safe-model-archive-management",
    "verified-coding-delegation",
}


def validate_skill(path: Path) -> tuple[str | None, list[str]]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return None, ["missing delimited YAML frontmatter"]
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return None, [f"invalid YAML frontmatter: {exc}"]
    if not isinstance(data, dict):
        return None, ["frontmatter must be a mapping"]

    missing = sorted(REQUIRED_FIELDS - data.keys())
    if missing:
        errors.append(f"missing frontmatter fields: {', '.join(missing)}")
    name = data.get("name")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        errors.append("name must be lowercase kebab-case")
        name = None
    elif name != path.parent.name:
        errors.append("frontmatter name must match directory name")

    description = data.get("description")
    if not isinstance(description, str) or len(description) > 60 or not description.endswith("."):
        errors.append("description must be one sentence, end in '.', and be at most 60 characters")
    when = re.search(r"^## When to Use\n+(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if not when or len(when.group(1).strip()) < 30:
        errors.append("When to Use must contain an operational trigger description")

    sections = set(re.findall(r"^## (.+?)\s*$", text, re.MULTILINE))
    absent = sorted(REQUIRED_SECTIONS - sections)
    if absent:
        errors.append(f"missing sections: {', '.join(absent)}")
    for heading in ("Safety and Read-Back", "Verification"):
        body = re.search(rf"^## {re.escape(heading)}\n+(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
        if not body or len(body.group(1).strip()) < 80:
            errors.append(f"{heading} must contain actionable checks")
    return name, errors


def main() -> int:
    skill_files = sorted(SKILLS_ROOT.glob("*/SKILL.md"))
    errors: list[str] = []
    names: list[str] = []
    for path in skill_files:
        name, skill_errors = validate_skill(path)
        if name:
            names.append(name)
        errors.extend(f"{path.relative_to(ROOT)}: {error}" for error in skill_errors)

    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        errors.append(f"duplicate skill names: {', '.join(duplicates)}")
    missing = sorted(EXPECTED_NAMES - set(names))
    unexpected = sorted(set(names) - EXPECTED_NAMES)
    if missing:
        errors.append(f"missing expected skills: {', '.join(missing)}")
    if unexpected:
        errors.append(f"unexpected custom skills: {', '.join(unexpected)}")
    if len(skill_files) != len(EXPECTED_NAMES):
        errors.append(f"expected {len(EXPECTED_NAMES)} SKILL.md files, found {len(skill_files)}")

    if errors:
        print("Custom skill validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Validated {len(skill_files)} custom skills: frontmatter, triggers, safety, verification, unique names.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
