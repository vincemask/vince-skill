#!/usr/bin/env python3
"""Validate Codex editorial copy and render direct-response AI trend content."""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any

from collect_ai_trends import (
    SCHEMA_VERSION,
    STATUS_ZH,
    atomic_write,
    validate_config,
    write_json,
)
from validate_x_drafts import validate as validate_drafts


URL_RE = re.compile(r"https?://[^\s]+")
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
HASHTAG_RE = re.compile(r"(?<!\w)#[\w\u3400-\u4dbf\u4e00-\u9fff]+")
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF]")
MARKDOWN_RE = re.compile(r"```|^#{1,6}\s|\[[^\]]+\]\(https?://", re.MULTILINE)
BANNED_PHRASES = ("AI 热点观察 #", "检测到来自", "共同信号", "建议先核对原始信息")


class FinalizeError(RuntimeError):
    pass


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalized_text(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def compact_count(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


def cjk_ratio(value: str) -> float:
    compact = re.sub(r"\s+", "", value)
    return len(CJK_RE.findall(compact)) / max(len(compact), 1)


def prose_without_urls(value: str) -> str:
    return URL_RE.sub("", value).strip()


def validate_editorial(
    editorial: Any,
    editorial_input: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(editorial, dict):
        raise FinalizeError("editorial.json 根节点必须是对象")
    if editorial.get("schema_version") != 1:
        raise FinalizeError("editorial.json schema_version 必须为 1")
    if editorial.get("run_id") != editorial_input["run_id"]:
        raise FinalizeError("editorial.json run_id 与采集运行不一致")
    if editorial.get("language") != "zh-CN":
        raise FinalizeError("editorial.json language 必须为 zh-CN")
    items = editorial.get("items")
    if not isinstance(items, list):
        raise FinalizeError("editorial.json items 必须是数组")
    topics = editorial_input["topics"]
    expected_count = int(editorial_input["required_topic_count"])
    if len(items) != expected_count:
        raise FinalizeError(f"editorial.json 必须包含 {expected_count} 个话题，当前为 {len(items)} 个")
    policy = editorial_input["post_policy"]
    normalized_items: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    seen_posts: set[str] = set()
    for index, (item, topic) in enumerate(zip(items, topics), start=1):
        prefix = f"items[{index - 1}]"
        if not isinstance(item, dict):
            raise FinalizeError(f"{prefix} 必须是对象")
        if item.get("rank") != index:
            raise FinalizeError(f"{prefix}.rank 必须为 {index}")
        if item.get("topic_id") != topic["topic_id"]:
            raise FinalizeError(f"{prefix}.topic_id 缺失、未知或顺序错误")

        title = normalized_text(item.get("title_zh"))
        title_length = compact_count(title)
        if not 8 <= title_length <= 28 or len(CJK_RE.findall(title)) < 4:
            raise FinalizeError(f"{prefix}.title_zh 必须是 8–28 字的中文标题")
        title_key = re.sub(r"\s+", "", title).lower()
        if title_key in seen_titles:
            raise FinalizeError(f"{prefix}.title_zh 与其他话题重复")
        seen_titles.add(title_key)

        reason = normalized_text(item.get("recommendation_reason"))
        reason_length = compact_count(reason)
        if not int(policy["min_recommendation_characters"]) <= reason_length <= int(policy["max_recommendation_characters"]):
            raise FinalizeError(f"{prefix}.recommendation_reason 长度必须符合配置")
        if URL_RE.search(reason) or cjk_ratio(reason) < 0.5:
            raise FinalizeError(f"{prefix}.recommendation_reason 必须是无链接的中文推荐理由")

        primary_url = normalized_text(item.get("primary_url"))
        evidence_by_url = {
            evidence["url"]: evidence
            for evidence in topic["evidence"]
            if isinstance(evidence, dict) and evidence.get("url")
        }
        if primary_url not in evidence_by_url:
            raise FinalizeError(f"{prefix}.primary_url 不属于该话题的证据集合")

        post = normalized_text(item.get("x_post"))
        urls = URL_RE.findall(post)
        if urls != [primary_url]:
            raise FinalizeError(f"{prefix}.x_post 必须且只能包含 primary_url 一次")
        nonempty_lines = [line.strip() for line in post.splitlines() if line.strip()]
        if not nonempty_lines or nonempty_lines[-1] != primary_url:
            raise FinalizeError(f"{prefix}.x_post 必须以独立一行的 primary_url 结尾")
        prose = prose_without_urls(post)
        prose_length = compact_count(prose)
        if not int(policy["min_prose_characters"]) <= prose_length <= int(policy["max_prose_characters"]):
            raise FinalizeError(f"{prefix}.x_post 正文长度必须符合配置")
        if cjk_ratio(prose) < 0.5:
            raise FinalizeError(f"{prefix}.x_post 必须以简体中文为主要语言")
        if MARKDOWN_RE.search(post):
            raise FinalizeError(f"{prefix}.x_post 不得包含 Markdown")
        if len(HASHTAG_RE.findall(post)) > int(policy["max_hashtags"]):
            raise FinalizeError(f"{prefix}.x_post hashtag 数量超过限制")
        if len(EMOJI_RE.findall(post)) > 1:
            raise FinalizeError(f"{prefix}.x_post 不得堆叠 emoji")
        banned = next((phrase for phrase in BANNED_PHRASES if phrase in post), None)
        if banned:
            raise FinalizeError(f"{prefix}.x_post 包含禁止的泛化模板：{banned}")
        post_key = re.sub(r"\s+", " ", prose).strip().lower()
        if post_key in seen_posts:
            raise FinalizeError(f"{prefix}.x_post 与其他成稿重复")
        seen_posts.add(post_key)

        evidence = evidence_by_url[primary_url]
        normalized_items.append({
            "rank": index,
            "topic_id": topic["topic_id"],
            "title_zh": title,
            "recommendation_reason": reason,
            "x_post": post,
            "primary_url": primary_url,
            "primary_source": {
                "source": evidence["source"],
                "url": primary_url,
                "title": evidence["title"],
            },
        })
    return normalized_items


def build_drafts_payload(
    items: list[dict[str, Any]],
    editorial_input: dict[str, Any],
) -> dict[str, Any]:
    policy = editorial_input["post_policy"]
    drafts = []
    for item in items:
        text = item["x_post"]
        prose = prose_without_urls(text)
        drafts.append({
            "id": f"draft-{item['rank']:02d}",
            "rank": item["rank"],
            "topic_id": item["topic_id"],
            "title_zh": item["title_zh"],
            "recommendation_reason": item["recommendation_reason"],
            "text": text,
            "primary_url": item["primary_url"],
            "character_count": len(text),
            "prose_character_count": compact_count(prose),
            "sources": [item["primary_source"]],
        })
    payload = {
        "schema_version": SCHEMA_VERSION,
        "language": "zh-CN",
        "mode": policy["mode"],
        "min_prose_characters": int(policy["min_prose_characters"]),
        "max_prose_characters": int(policy["max_prose_characters"]),
        "min_recommendation_characters": int(policy["min_recommendation_characters"]),
        "max_recommendation_characters": int(policy["max_recommendation_characters"]),
        "max_hashtags": int(policy["max_hashtags"]),
        "drafts": drafts,
    }
    errors = validate_drafts(payload)
    if errors:
        raise FinalizeError("drafts.json 校验失败：" + "; ".join(errors))
    return payload


def render_compact_report(report: dict[str, Any], drafts: dict[str, Any]) -> str:
    health = report["health"]["status"]
    count = len(drafts["drafts"])
    lines = [
        "# AI 热点与 X 成稿",
        "",
        f"> 本次共 {count} 个可靠话题 · 数据状态：{STATUS_ZH.get(health, health)}",
        "",
    ]
    warnings = []
    if health != "complete":
        warnings.append("本次包含缓存或失败来源；完整诊断保留在本地 `report.json`。")
    if count < 10:
        warnings.append(f"本次仅有 {count} 个可靠话题，未使用低质量信号补足。")
    if warnings:
        lines.append("> [!warning] 输出提示")
        lines.extend(f"> {warning}" for warning in warnings)
        lines.append("")
    if count == 0:
        lines.extend(["没有足够证据生成 X 成稿。", ""])
    for draft in drafts["drafts"]:
        lines.extend([
            f"## {draft['rank']:02d}｜{draft['title_zh']}",
            "",
            f"**推荐理由：** {draft['recommendation_reason']}",
            "",
            "**X 成稿：**",
            "",
            "```text",
            draft["text"],
            "```",
            "",
        ])
    return "\n".join(lines)


def copy_latest(
    output_root: Path,
    run_dir: Path,
    report: dict[str, Any],
    drafts: dict[str, Any],
    editorial: dict[str, Any],
) -> None:
    mapping = {
        "latest-report.json": json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        "latest-report.md": (run_dir / "report.md").read_text(encoding="utf-8"),
        "latest-editorial.json": json.dumps(editorial, ensure_ascii=False, indent=2) + "\n",
        "latest-drafts.json": json.dumps(drafts, ensure_ascii=False, indent=2) + "\n",
        "latest-x-drafts.md": (run_dir / "x-drafts.md").read_text(encoding="utf-8"),
    }
    for name, content in mapping.items():
        atomic_write(output_root / name, content)
    write_json(output_root / "latest.json", {
        "schema_version": SCHEMA_VERSION,
        "run_id": report["run"]["id"],
        "run_dir": str(run_dir.resolve()),
        "generated_at": report["run"]["generated_at"],
        "health": report["health"],
        "topic_count": len(drafts["drafts"]),
        "content": (run_dir / "report.md").read_text(encoding="utf-8"),
    })


def finalize(run_dir: Path, editorial_path: Path) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    if not run_dir.is_dir():
        raise FinalizeError(f"运行目录不存在：{run_dir}")
    report = load_json(run_dir / "report.json")
    editorial_input = load_json(run_dir / "editorial-input.json")
    config = load_json(run_dir / "run-config.json")
    validate_config(config)
    if report["run"]["id"] != editorial_input.get("run_id"):
        raise FinalizeError("report.json 与 editorial-input.json 的 run_id 不一致")
    editorial = load_json(editorial_path.resolve())
    items = validate_editorial(editorial, editorial_input)
    normalized_editorial = {
        "schema_version": 1,
        "run_id": editorial_input["run_id"],
        "language": "zh-CN",
        "items": items,
    }
    drafts = build_drafts_payload(items, editorial_input)
    compact_report = render_compact_report(report, drafts)
    manifest = load_json(run_dir / "manifest.json")
    manifest.update({
        "stage": "finalized",
        "topic_count": len(drafts["drafts"]),
        "files": [
            "report.json", "report.md", "editorial-input.json", "editorial.json",
            "drafts.json", "x-drafts.md",
            "run-config.json", "raw/reddit.json", "raw/x.json", "raw/github.json",
        ],
    })
    write_json(run_dir / "editorial.json", normalized_editorial)
    write_json(run_dir / "drafts.json", drafts)
    atomic_write(run_dir / "report.md", compact_report)
    atomic_write(run_dir / "x-drafts.md", compact_report)
    write_json(run_dir / "manifest.json", manifest)
    write_json(run_dir / "finalized.json", {
        "schema_version": 1,
        "run_id": report["run"]["id"],
        "status": "finalized",
        "topic_count": len(drafts["drafts"]),
    })
    copy_latest(run_dir.parent, run_dir, report, drafts, normalized_editorial)
    return {
        "run_id": report["run"]["id"],
        "status": "finalized",
        "health": report["health"]["status"],
        "topic_count": len(drafts["drafts"]),
        "report": str((run_dir / "report.md").resolve()),
        "drafts": str((run_dir / "x-drafts.md").resolve()),
        "content": compact_report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--editorial", type=Path, help="Defaults to <run-dir>/editorial.json")
    args = parser.parse_args()
    editorial_path = args.editorial or args.run_dir / "editorial.json"
    try:
        result = finalize(args.run_dir, editorial_path)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError, FinalizeError) as exc:
        print(f"finalization failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
