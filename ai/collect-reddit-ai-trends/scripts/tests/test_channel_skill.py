from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[2]
SCRIPT = SKILL_DIR / "scripts" / "collect_reddit_ai_trends.py"
FINALIZER = SKILL_DIR / "scripts" / "finalize_ai_trends.py"
CONFIG = SKILL_DIR / "references" / "default-config.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE = "reddit"


def write_valid_editorial(run_dir: Path) -> None:
    editorial_input = json.loads((run_dir / "editorial-input.json").read_text(encoding="utf-8"))
    items = []
    for topic in editorial_input["topics"]:
        rank = topic["rank"]
        primary_url = topic["evidence"][0]["url"]
        prose = f"第{rank}项渠道信号呈现了明确的人工智能产品变化、开发者反馈和可核对的原始信息。"
        while len(re.sub(r"\s+", "", prose)) < 130:
            prose += "这项变化值得结合实际使用门槛、采用范围和对现有工作流的影响继续观察。"
        prose = re.sub(r"\s+", "", prose)[:129] + "。"
        items.append({
            "rank": rank,
            "topic_id": topic["topic_id"],
            "title_zh": f"人工智能渠道热点第{rank:02d}项",
            "recommendation_reason": "该信号近期互动明显，且包含可核对的原始链接，适合作为当前选题。",
            "x_post": prose + "\n\n" + primary_url,
            "primary_url": primary_url,
        })
    (run_dir / "editorial.json").write_text(json.dumps({
        "schema_version": 1,
        "run_id": editorial_input["run_id"],
        "language": "zh-CN",
        "items": items,
    }, ensure_ascii=False), encoding="utf-8")


def load_module():
    spec = importlib.util.spec_from_file_location("collect_reddit_ai_trends", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class RedditChannelSkillTests(unittest.TestCase):
    def test_fixture_run_emits_only_reddit_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = json.loads(CONFIG.read_text(encoding="utf-8"))
            config["include_older_items"] = True
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--config", str(config_path), "--fixture-dir", str(FIXTURES),
                 "--output-dir", str(root / "out"), "--run-id", "20260717T000000Z-reddit"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            run_dir = Path(payload["run_dir"])
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(report["run"]["channel"], SOURCE)
            self.assertEqual(set(report["items"]), {SOURCE})
            self.assertTrue(report["topics"])
            self.assertEqual({row["source"] for row in report["source_runs"]}, {SOURCE})
            self.assertIn("raw/reddit.json", manifest["files"])
            self.assertFalse((run_dir / "raw" / "x.json").exists())
            self.assertFalse((run_dir / "raw" / "github.json").exists())
            write_valid_editorial(run_dir)
            finalized = subprocess.run([sys.executable, str(FINALIZER), "--run-dir", str(run_dir)], capture_output=True, text=True, check=False)
            self.assertEqual(finalized.returncode, 0, finalized.stderr)
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual({path for path in manifest["files"] if path.startswith("raw/")}, {"raw/reddit.json"})

    def test_config_rejects_enabling_another_channel(self) -> None:
        module = load_module()
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        config["x"] = {"enabled": True, "accounts": ["OpenAI"], "topic_queries": []}
        with self.assertRaisesRegex(ValueError, "requires only"):
            module.validate_config(config)

    def test_defaults_cover_coding_agent_communities(self) -> None:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        self.assertIn("ChatGPTCoding", config["reddit"]["subreddits"])
        self.assertIn("AI_Agents", config["reddit"]["subreddits"])

    def test_preflight_checks_only_opencli(self) -> None:
        module = load_module()
        calls = []
        with patch.object(module, "command_check", side_effect=lambda command, **_: calls.append(command) or {"status": "ok"}), redirect_stdout(StringIO()):
            self.assertEqual(module.run_preflight(), 0)
        self.assertTrue(calls)
        self.assertEqual({command[0] for command in calls}, {"opencli"})


if __name__ == "__main__":
    unittest.main()
