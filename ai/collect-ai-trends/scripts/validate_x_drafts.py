#!/usr/bin/env python3
"""Validate the compact JSON contract for evidence-linked X long-post drafts."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


URL_RE = re.compile(r"https?://[^\s]+")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
HASHTAG_RE = re.compile(r"(?<!\w)#[\w\u3400-\u4dbf\u4e00-\u9fff]+")
MARKDOWN_RE = re.compile(r"```|^#{1,6}\s|\[[^\]]+\]\(https?://", re.MULTILINE)
BANNED_PHRASES = ("AI 热点观察 #", "检测到来自", "共同信号", "建议先核对原始信息")


def compact_count(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


def cjk_ratio(value: str) -> float:
    compact = re.sub(r"\s+", "", value)
    return len(CJK_RE.findall(compact)) / max(len(compact), 1)


def validate(payload: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["root must be an object"]
    if payload.get("language") != "zh-CN":
        errors.append("language must be zh-CN")
    if payload.get("mode") != "long":
        errors.append("mode must be long")
    minimum = payload.get("min_prose_characters")
    maximum = payload.get("max_prose_characters")
    reason_minimum = payload.get("min_recommendation_characters")
    reason_maximum = payload.get("max_recommendation_characters")
    max_hashtags = payload.get("max_hashtags")
    if not isinstance(minimum, int) or not isinstance(maximum, int) or not 1 <= minimum <= maximum <= 500:
        errors.append("prose character limits are invalid")
        minimum, maximum = 120, 180
    if (
        not isinstance(reason_minimum, int)
        or not isinstance(reason_maximum, int)
        or not 1 <= reason_minimum <= reason_maximum <= 100
    ):
        errors.append("recommendation character limits are invalid")
        reason_minimum, reason_maximum = 20, 50
    if max_hashtags != 1:
        errors.append("max_hashtags must be 1")
        max_hashtags = 1
    drafts = payload.get("drafts")
    if not isinstance(drafts, list):
        return errors + ["drafts must be an array"]
    if len(drafts) > 10:
        errors.append("drafts must contain at most 10 items")
    seen_ids: set[str] = set()
    seen_topics: set[str] = set()
    seen_texts: set[str] = set()
    for index, draft in enumerate(drafts):
        prefix = f"drafts[{index}]"
        if not isinstance(draft, dict):
            errors.append(f"{prefix} must be an object")
            continue
        expected_rank = index + 1
        if draft.get("rank") != expected_rank:
            errors.append(f"{prefix}.rank must equal {expected_rank}")
        draft_id = draft.get("id")
        if not isinstance(draft_id, str) or not draft_id:
            errors.append(f"{prefix}.id must be a non-empty string")
        elif draft_id in seen_ids:
            errors.append(f"{prefix}.id is duplicated: {draft_id}")
        else:
            seen_ids.add(draft_id)
        topic_id = draft.get("topic_id")
        if not isinstance(topic_id, str) or not topic_id:
            errors.append(f"{prefix}.topic_id must be a non-empty string")
        elif topic_id in seen_topics:
            errors.append(f"{prefix}.topic_id is duplicated: {topic_id}")
        else:
            seen_topics.add(topic_id)
        title = unicodedata.normalize("NFC", str(draft.get("title_zh") or "")).strip()
        if not 8 <= compact_count(title) <= 28 or len(CJK_RE.findall(title)) < 4:
            errors.append(f"{prefix}.title_zh must be an 8–28 character Chinese title")
        reason = unicodedata.normalize("NFC", str(draft.get("recommendation_reason") or "")).strip()
        if not reason_minimum <= compact_count(reason) <= reason_maximum:
            errors.append(f"{prefix}.recommendation_reason length is invalid")
        if URL_RE.search(reason) or cjk_ratio(reason) < 0.5:
            errors.append(f"{prefix}.recommendation_reason must be Chinese prose without URLs")
        text = unicodedata.normalize("NFC", str(draft.get("text") or "")).strip()
        if not text:
            errors.append(f"{prefix}.text must be a non-empty string")
            continue
        normalized = re.sub(r"\s+", " ", URL_RE.sub("", text)).strip().lower()
        if normalized in seen_texts:
            errors.append(f"{prefix}.text duplicates another draft")
        seen_texts.add(normalized)
        primary_url = draft.get("primary_url")
        text_urls = URL_RE.findall(text)
        if not isinstance(primary_url, str) or text_urls != [primary_url]:
            errors.append(f"{prefix}.text must contain primary_url exactly once")
        nonempty_lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not nonempty_lines or nonempty_lines[-1] != primary_url:
            errors.append(f"{prefix}.text must end with primary_url on its own line")
        prose = URL_RE.sub("", text).strip()
        prose_count = compact_count(prose)
        if not minimum <= prose_count <= maximum:
            errors.append(f"{prefix}.text prose has {prose_count} characters; expected {minimum}–{maximum}")
        if cjk_ratio(prose) < 0.5:
            errors.append(f"{prefix}.text must contain Simplified Chinese prose")
        if MARKDOWN_RE.search(text):
            errors.append(f"{prefix}.text must not contain Markdown")
        if len(HASHTAG_RE.findall(text)) > max_hashtags:
            errors.append(f"{prefix}.text has too many hashtags")
        for phrase in BANNED_PHRASES:
            if phrase in text:
                errors.append(f"{prefix}.text contains banned generic phrase: {phrase}")
        if draft.get("character_count") != len(text):
            errors.append(f"{prefix}.character_count must equal {len(text)}")
        if draft.get("prose_character_count") != prose_count:
            errors.append(f"{prefix}.prose_character_count must equal {prose_count}")
        sources = draft.get("sources")
        if not isinstance(sources, list) or len(sources) != 1 or not isinstance(sources[0], dict):
            errors.append(f"{prefix}.sources must contain exactly one source object")
        elif sources[0].get("url") != primary_url:
            errors.append(f"{prefix}.sources URL must equal primary_url")
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
