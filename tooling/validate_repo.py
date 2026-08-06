#!/usr/bin/env python3
"""Validate every Skill package and checked-in JSON configuration in the repository."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml


NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def parse_frontmatter(path: Path) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return {}, [f"{path}: missing opening YAML frontmatter delimiter"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}, [f"{path}: missing closing YAML frontmatter delimiter"]
    metadata: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        if ":" not in line:
            errors.append(f"{path}: invalid frontmatter line: {line}")
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = value.strip().strip('"\'')
    unexpected = sorted(set(metadata) - {"name", "description"})
    if unexpected:
        errors.append(f"{path}: unsupported frontmatter fields: {', '.join(unexpected)}")
    return metadata, errors


def validate_skill(skill_file: Path) -> list[str]:
    metadata, errors = parse_frontmatter(skill_file)
    name = metadata.get("name", "")
    description = metadata.get("description", "")
    if not NAME_RE.fullmatch(name):
        errors.append(f"{skill_file}: name must use lowercase hyphen-case")
    if not description:
        errors.append(f"{skill_file}: description is required")
    if name and skill_file.parent.name != name:
        errors.append(
            f"{skill_file}: directory name {skill_file.parent.name!r} must match Skill name {name!r}"
        )
    openai_yaml = skill_file.parent / "agents" / "openai.yaml"
    if openai_yaml.is_file():
        try:
            metadata_yaml = yaml.safe_load(openai_yaml.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            errors.append(f"{openai_yaml}: invalid YAML: {exc}")
        else:
            interface = metadata_yaml.get("interface") if isinstance(metadata_yaml, dict) else None
            default_prompt = interface.get("default_prompt") if isinstance(interface, dict) else None
            if name and (not isinstance(default_prompt, str) or f"${name}" not in default_prompt):
                errors.append(f"{openai_yaml}: interface.default_prompt must contain ${name}")
    return errors


def validate_json(path: Path) -> list[str]:
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{path}: invalid JSON: {exc}"]
    return []


def validate_repo(root: Path) -> list[str]:
    skill_files = sorted(root.rglob("SKILL.md"))
    if not skill_files:
        return [f"{root}: no Skill packages found"]
    errors: list[str] = []
    for skill_file in skill_files:
        errors.extend(validate_skill(skill_file))
    for json_file in sorted(root.rglob("references/*.json")):
        errors.extend(validate_json(json_file))
    return errors


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]).resolve()
    errors = validate_repo(root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    skill_count = len(list(root.rglob("SKILL.md")))
    print(f"validated {skill_count} Skill packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
