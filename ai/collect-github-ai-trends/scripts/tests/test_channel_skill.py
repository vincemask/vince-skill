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
SCRIPT = SKILL_DIR / "scripts" / "collect_github_ai_trends.py"
FINALIZER = SKILL_DIR / "scripts" / "finalize_ai_trends.py"
CONFIG = SKILL_DIR / "references" / "default-config.json"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SOURCE = "github"


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
    spec = importlib.util.spec_from_file_location("collect_github_ai_trends", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class GitHubChannelSkillTests(unittest.TestCase):
    def test_fixture_run_emits_only_github_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = json.loads(CONFIG.read_text(encoding="utf-8"))
            config["include_older_items"] = True
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--config", str(config_path), "--fixture-dir", str(FIXTURES),
                 "--output-dir", str(root / "out"), "--run-id", "20260717T000000Z-github"],
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
            self.assertIn("raw/github.json", manifest["files"])
            self.assertFalse((run_dir / "raw" / "x.json").exists())
            self.assertFalse((run_dir / "raw" / "reddit.json").exists())
            write_valid_editorial(run_dir)
            finalized = subprocess.run([sys.executable, str(FINALIZER), "--run-dir", str(run_dir)], capture_output=True, text=True, check=False)
            self.assertEqual(finalized.returncode, 0, finalized.stderr)
            final_payload = json.loads(finalized.stdout)
            self.assertEqual(final_payload["content"], (run_dir / "report.md").read_text(encoding="utf-8"))
            self.assertFalse((run_dir / "obsidian-publish.json").exists())
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual({path for path in manifest["files"] if path.startswith("raw/")}, {"raw/github.json"})

    def test_config_rejects_enabling_another_channel(self) -> None:
        module = load_module()
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        config["reddit"] = {"enabled": True, "subreddits": ["MachineLearning"]}
        with self.assertRaisesRegex(ValueError, "requires only"):
            module.validate_config(config)

    def test_defaults_cover_coding_agent_topics(self) -> None:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        queries = " ".join(config["github"]["queries"])
        self.assertIn("topic:coding-agents", queries)
        self.assertIn("topic:ai-coding-agent", queries)
        self.assertIn("topic:ai-coding-assistant", queries)
        self.assertTrue(config["include_older_items"])
        self.assertIn("{active_since_date}", queries)
        self.assertIn("{emerging_since_date}", queries)

    def test_ranking_favors_hot_rising_projects_over_creation_recency(self) -> None:
        module = load_module()
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        now = module.parse_datetime("2026-07-19T00:00:00Z")
        rows = [
            {
                "full_name": "example/hot-established",
                "html_url": "https://github.com/example/hot-established",
                "created_at": "2023-01-01T00:00:00Z",
                "pushed_at": "2026-07-18T00:00:00Z",
                "stargazers_count": 50000,
                "forks_count": 6000,
            },
            {
                "full_name": "example/brand-new",
                "html_url": "https://github.com/example/brand-new",
                "created_at": "2026-07-18T00:00:00Z",
                "pushed_at": "2026-07-18T00:00:00Z",
                "stargazers_count": 20,
                "forks_count": 2,
            },
        ]
        items = module.normalize_rows("github", rows)
        module.score_items(items, config, now)
        scores = {item["title"]: item["score"] for item in items}
        self.assertGreater(scores["example/hot-established"], scores["example/brand-new"])
        self.assertIn("stars_per_day_proxy", items[0]["metrics"])
        self.assertEqual(
            items[0]["ranking_signals"]["momentum_basis"],
            "lifetime-stars-and-forks-per-day-proxy",
        )

    def test_ranking_can_put_fast_growth_ahead_of_slightly_higher_total_stars(self) -> None:
        module = load_module()
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        now = module.parse_datetime("2026-07-19T00:00:00Z")
        rows = [
            {
                "full_name": "example/mature-popular",
                "html_url": "https://github.com/example/mature-popular",
                "created_at": "2021-07-19T00:00:00Z",
                "pushed_at": "2026-07-18T00:00:00Z",
                "stargazers_count": 30000,
                "forks_count": 3000,
            },
            {
                "full_name": "example/fast-rising",
                "html_url": "https://github.com/example/fast-rising",
                "created_at": "2026-05-20T00:00:00Z",
                "pushed_at": "2026-07-18T00:00:00Z",
                "stargazers_count": 20000,
                "forks_count": 2000,
            },
        ]
        items = module.normalize_rows("github", rows)
        module.score_items(items, config, now)
        scores = {item["title"]: item["score"] for item in items}
        self.assertGreater(scores["example/fast-rising"], scores["example/mature-popular"])

    def test_preflight_checks_only_gh(self) -> None:
        module = load_module()
        calls = []
        with patch.object(module, "command_check", side_effect=lambda command, **_: calls.append(command) or {"status": "ok"}), redirect_stdout(StringIO()):
            self.assertEqual(module.run_preflight(), 0)
        self.assertTrue(calls)
        self.assertEqual({command[0] for command in calls}, {"gh"})


if __name__ == "__main__":
    unittest.main()
