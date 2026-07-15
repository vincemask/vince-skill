from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TEST_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = TEST_DIR.parent
COLLECTOR = SCRIPT_DIR / "collect_ai_trends.py"
VALIDATOR = SCRIPT_DIR / "validate_x_drafts.py"
CONFIG = TEST_DIR / "fixture-config.json"
FIXTURES = TEST_DIR / "fixtures"


def load_collector_module():
    spec = importlib.util.spec_from_file_location("collect_ai_trends", COLLECTOR)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load collector module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CollectorIntegrationTests(unittest.TestCase):
    def run_collector(self, output: Path, fixture_dir: Path = FIXTURES, strict: bool = True) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(COLLECTOR),
            "--config", str(CONFIG),
            "--fixture-dir", str(fixture_dir),
            "--output-dir", str(output),
            "--run-id", "20260715T000000Z-test",
        ]
        if strict:
            command.append("--strict")
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def test_fixture_run_emits_stable_editorial_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            result = self.run_collector(output)
            self.assertEqual(result.returncode, 0, result.stderr)
            run_dir = output / "20260715T000000Z-test"
            for relative in (
                "manifest.json", "report.json", "editorial-input.json", "run-config.json",
                "raw/reddit.json", "raw/x.json", "raw/github.json",
            ):
                self.assertTrue((run_dir / relative).is_file(), relative)
            for relative in ("report.md", "drafts.json", "x-drafts.md", "obsidian-note.md", "obsidian-publish.json"):
                self.assertFalse((run_dir / relative).exists(), relative)
            self.assertTrue((output / "latest-collection.json").is_file())
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["health"]["status"], "complete")
            self.assertEqual(report["health"]["item_count"], 6)
            self.assertEqual(len(report["topics"]), 2)
            self.assertTrue(all(topic["cross_source"] for topic in report["topics"]))
            self.assertTrue(all(set(topic["sources"]) == {"reddit", "x", "github"} for topic in report["topics"]))
            self.assertEqual(report["run"]["document_language"], "zh-CN")
            editorial_input = json.loads((run_dir / "editorial-input.json").read_text(encoding="utf-8"))
            self.assertEqual(editorial_input["topic_limit"], 10)
            self.assertEqual(editorial_input["required_topic_count"], 2)
            self.assertEqual(editorial_input["post_policy"]["mode"], "long")
            self.assertTrue(all(1 <= len(topic["evidence"]) <= 3 for topic in editorial_input["topics"]))
            self.assertTrue(all(len({evidence["source"] for evidence in topic["evidence"]}) == 3 for topic in editorial_input["topics"]))
            stdout = json.loads(result.stdout)
            self.assertTrue(stdout["needs_editorial"])
            self.assertEqual(stdout["editorial_input"], str((run_dir / "editorial-input.json").resolve()))

    def test_missing_fixture_yields_partial_report_and_strict_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            fixtures = temp_path / "fixtures"
            fixtures.mkdir()
            (fixtures / "reddit.json").write_text((FIXTURES / "reddit.json").read_text(encoding="utf-8"), encoding="utf-8")
            (fixtures / "github.json").write_text((FIXTURES / "github.json").read_text(encoding="utf-8"), encoding="utf-8")
            output = temp_path / "output"
            result = self.run_collector(output, fixtures)
            self.assertEqual(result.returncode, 3, result.stderr)
            report = json.loads((output / "20260715T000000Z-test" / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["health"]["status"], "partial")
            self.assertEqual(report["health"]["sources"]["x"]["status"], "failed")
            editorial_input = json.loads((output / "20260715T000000Z-test" / "editorial-input.json").read_text(encoding="utf-8"))
            self.assertEqual(editorial_input["health"], "partial")

    def test_all_sources_failed_still_emits_health_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            fixtures = temp_path / "fixtures"
            fixtures.mkdir()
            output = temp_path / "output"
            result = self.run_collector(output, fixtures, strict=False)
            self.assertEqual(result.returncode, 2, result.stderr)
            run_dir = output / "20260715T000000Z-test"
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            editorial_input = json.loads((run_dir / "editorial-input.json").read_text(encoding="utf-8"))
            self.assertEqual(report["health"]["status"], "failed")
            self.assertEqual(report["health"]["item_count"], 0)
            self.assertEqual(editorial_input["required_topic_count"], 0)
            self.assertEqual(editorial_input["topics"], [])

    def test_invalid_run_id_fails_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                [
                    sys.executable, str(COLLECTOR), "--config", str(CONFIG),
                    "--fixture-dir", str(FIXTURES), "--output-dir", temp,
                    "--run-id", "../../unsafe",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("run-id must match", result.stderr)

    def test_preflight_failure_marker_overrides_zero_exit(self) -> None:
        collector = load_collector_module()
        check = collector.command_check(
            [sys.executable, "-c", "print('[FAIL] browser bridge unavailable')"],
            failure_markers=("[FAIL]",),
        )
        self.assertEqual(check["status"], "failed")

    def test_obsidian_path_overrides_reject_unsafe_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            for value in ("../raw", "/absolute/raw", "raw/../other"):
                with self.subTest(value=value):
                    result = subprocess.run(
                        [
                            sys.executable, str(COLLECTOR), "--config", str(CONFIG),
                            "--fixture-dir", str(FIXTURES), "--output-dir", temp,
                            "--run-id", "20260715T000000Z-test", "--obsidian-dir", value,
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("config.obsidian.target_directory", result.stderr)

    def test_title_similarity_recognizes_containment(self) -> None:
        collector = load_collector_module()
        short = collector.title_tokens("new reasoning model for developers")
        long = collector.title_tokens("OpenAI releases a new reasoning model for developers with API access")
        self.assertGreaterEqual(collector.similarity(short, long), 0.7)

    def test_twelve_distinct_candidates_are_capped_at_ten(self) -> None:
        collector = load_collector_module()
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        items = [
            {
                "id": f"github-{index:02d}",
                "source": "github",
                "title": f"uniqueproject{index:02d} specialized capability{index:02d}",
                "url": f"https://github.com/example/project-{index:02d}",
                "score": float(100 - index),
            }
            for index in range(12)
        ]
        topics = collector.cluster_items(items, config)
        self.assertEqual(len(topics), 10)
        self.assertEqual([topic["items"][0]["id"] for topic in topics], [f"github-{index:02d}" for index in range(10)])

    def test_draft_validator_rejects_non_chinese_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "drafts.json"
            text = "AI trend to watch https://example.com/topic"
            payload = {
                "schema_version": "1.0",
                "language": "en",
                "mode": "long",
                "min_prose_characters": 120,
                "max_prose_characters": 180,
                "min_recommendation_characters": 20,
                "max_recommendation_characters": 50,
                "max_hashtags": 1,
                "drafts": [{
                    "id": "draft-01",
                    "rank": 1,
                    "topic_id": "topic-01",
                    "title_zh": "这是一个用于测试的中文标题",
                    "recommendation_reason": "多个来源同时出现明显采用信号，因此值得及时向开发者说明实际影响。",
                    "text": text,
                    "primary_url": "https://example.com/topic",
                    "character_count": len(text),
                    "prose_character_count": len("AItrendtowatch"),
                    "sources": [{"source": "x", "url": "https://example.com/topic", "title": "Topic"}],
                }],
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(VALIDATOR), str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("language must be zh-CN", result.stderr)
            self.assertIn("must contain Simplified Chinese prose", result.stderr)


if __name__ == "__main__":
    unittest.main()
