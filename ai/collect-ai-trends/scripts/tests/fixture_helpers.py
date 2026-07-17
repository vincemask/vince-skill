from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent.parent
FINALIZER = SCRIPT_DIR / "finalize_ai_trends.py"


def compact_count(value: str) -> int:
    return len(re.sub(r"\s+", "", value))


def editorial_for(editorial_input: dict[str, Any]) -> dict[str, Any]:
    titles = {
        1: "推理模型开放开发者接口",
        2: "消费级显卡本地推理降内存",
    }
    openings = {
        1: "OpenAI 新推理模型开放开发者接口，并同步给出更新后的评测信息。",
        2: "新的开源本地推理工具把消费级显卡的内存占用作为主要优化目标。",
    }
    items = []
    for topic in editorial_input["topics"]:
        rank = topic["rank"]
        primary = sorted(
            topic["evidence"],
            key=lambda evidence: {"github": 0, "x": 1, "reddit": 2}.get(evidence["source"], 3),
        )[0]
        title = titles.get(rank, f"第{rank:02d}项人工智能工具进展")
        reason = "多类来源在短时间内同时升温，并且已有明确产品或开发者采用信号。"
        prose = openings.get(rank, f"第{rank:02d}项人工智能进展已经出现明确的产品变化和开发者反馈。")
        addition = "它值得关注的不只是发布本身，还包括实际使用门槛、可复现证据和对现有工作流的影响。"
        while compact_count(prose) < 130:
            prose += addition
        prose = re.sub(r"\s+", "", prose)[:129] + "。"
        items.append({
            "rank": rank,
            "topic_id": topic["topic_id"],
            "title_zh": title,
            "recommendation_reason": reason,
            "x_post": prose + "\n\n" + primary["url"],
            "primary_url": primary["url"],
        })
    return {
        "schema_version": 1,
        "run_id": editorial_input["run_id"],
        "language": "zh-CN",
        "items": items,
    }


def write_editorial(run_dir: Path) -> Path:
    editorial_input = json.loads((run_dir / "editorial-input.json").read_text(encoding="utf-8"))
    path = run_dir / "editorial.json"
    path.write_text(json.dumps(editorial_for(editorial_input), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def finalize_run(run_dir: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(FINALIZER), "--run-dir", str(run_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
