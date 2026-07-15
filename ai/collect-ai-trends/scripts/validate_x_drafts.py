#!/usr/bin/env python3
"""Validate the stable JSON contract for X-ready drafts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


URL_RE = re.compile(r"https?://[^\s]+")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def validate(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["root must be an object"]
    if payload.get("language") != "zh-CN":
        errors.append("language must be zh-CN")
    max_characters = payload.get("max_characters")
    if not isinstance(max_characters, int) or not 100 <= max_characters <= 280:
        errors.append("max_characters must be an integer between 100 and 280")
        max_characters = 280
    drafts = payload.get("drafts")
    if not isinstance(drafts, list):
        return errors + ["drafts must be an array"]
    seen_ids: set[str] = set()
    seen_texts: set[str] = set()
    for index, draft in enumerate(drafts):
        prefix = f"drafts[{index}]"
        if not isinstance(draft, dict):
            errors.append(f"{prefix} must be an object")
            continue
        draft_id = draft.get("id")
        if not isinstance(draft_id, str) or not draft_id:
            errors.append(f"{prefix}.id must be a non-empty string")
        elif draft_id in seen_ids:
            errors.append(f"{prefix}.id is duplicated: {draft_id}")
        else:
            seen_ids.add(draft_id)
        text = draft.get("text")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"{prefix}.text must be a non-empty string")
            continue
        normalized = re.sub(r"\s+", " ", text).strip().lower()
        if normalized in seen_texts:
            errors.append(f"{prefix}.text duplicates another draft")
        seen_texts.add(normalized)
        if len(text) > max_characters:
            errors.append(f"{prefix}.text has {len(text)} characters; limit is {max_characters}")
        if not CJK_RE.search(text):
            errors.append(f"{prefix}.text must contain Simplified Chinese prose")
        if draft.get("character_count") != len(text):
            errors.append(f"{prefix}.character_count must equal {len(text)}")
        text_urls = set(URL_RE.findall(text))
        if not text_urls:
            errors.append(f"{prefix}.text must contain an HTTP source URL")
        sources = draft.get("sources")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{prefix}.sources must be a non-empty array")
            continue
        source_urls = {
            source.get("url") for source in sources
            if isinstance(source, dict) and isinstance(source.get("url"), str)
        }
        if not source_urls:
            errors.append(f"{prefix}.sources must contain at least one URL")
        elif text_urls.isdisjoint(source_urls):
            errors.append(f"{prefix}.text URL must match one of its source URLs")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drafts_json", type=Path)
    args = parser.parse_args()
    try:
        with args.drafts_json.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"unable to read drafts: {exc}", file=sys.stderr)
        return 2
    errors = validate(payload)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"OK: {len(payload['drafts'])} drafts are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
