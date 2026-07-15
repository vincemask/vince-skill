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

    def test_fixture_run_emits_stable_outputs_and_valid_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "output"
            result = self.run_collector(output)
            self.assertEqual(result.returncode, 0, result.stderr)
            run_dir = output / "20260715T000000Z-test"
            for relative in (
                "manifest.json", "report.json", "report.md", "drafts.json", "x-drafts.md",
                "obsidian-note.md", "obsidian-publish.json",
                "raw/reddit.json", "raw/x.json", "raw/github.json",
            ):
                self.assertTrue((run_dir / relative).is_file(), relative)
            self.assertTrue((output / "latest.json").is_file())
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["health"]["status"], "complete")
            self.assertEqual(report["health"]["item_count"], 6)
            self.assertEqual(len(report["topics"]), 2)
            self.assertTrue(all(topic["cross_source"] for topic in report["topics"]))
            self.assertTrue(all(set(topic["sources"]) == {"reddit", "x", "github"} for topic in report["topics"]))
            self.assertEqual(report["run"]["document_language"], "zh-CN")
            report_md = (run_dir / "report.md").read_text(encoding="utf-8")
            drafts_md = (run_dir / "x-drafts.md").read_text(encoding="utf-8")
            for heading in ("## 来源状态", "## 热点话题", "## 采集诊断", "## 局限说明"):
                self.assertIn(heading, report_md)
            for forbidden in ("## Source health", "## Ranked topics", "## Collection diagnostics", "## Limitations"):
                self.assertNotIn(forbidden, report_md)
            self.assertIn("证据来源：", drafts_md)
            self.assertIn("## 草稿 01", drafts_md)
            self.assertNotIn("Evidence:", drafts_md)
            self.assertNotIn("Language:", drafts_md)
            obsidian_note = (run_dir / "obsidian-note.md").read_text(encoding="utf-8")
            obsidian_plan = json.loads((run_dir / "obsidian-publish.json").read_text(encoding="utf-8"))
            for marker in (
                "type: summary", "  - ai", "run_id: \"20260715T000000Z-test\"",
                "[[concepts/news-monitoring-and-growth]]", "## 热点话题与跨来源证据",
                "## 中文 X 草稿", "## 采集诊断与局限说明",
            ):
                self.assertIn(marker, obsidian_note)
            self.assertEqual(obsidian_plan["vault"], "wiki")
            self.assertRegex(obsidian_plan["note_path"], r"^raw/trend-\d{4}-\d{2}-\d{2}-\d{6}\.md$")
            self.assertEqual(obsidian_plan["note_file"], str((run_dir / "obsidian-note.md").resolve()))
            stdout = json.loads(result.stdout)
            self.assertEqual(stdout["obsidian_publish_plan"], str((run_dir / "obsidian-publish.json").resolve()))
            drafts_payload = json.loads((run_dir / "drafts.json").read_text(encoding="utf-8"))
            self.assertTrue(all(draft["text"].startswith("AI 热点观察 #") for draft in drafts_payload["drafts"]))
            self.assertTrue(all("共同信号" in draft["text"] for draft in drafts_payload["drafts"]))
            self.assertTrue(all("We are releasing" not in draft["text"] for draft in drafts_payload["drafts"]))
            self.assertTrue(all("A new open source toolkit" not in draft["text"] for draft in drafts_payload["drafts"]))
            validation = subprocess.run(
                [sys.executable, str(VALIDATOR), str(run_dir / "drafts.json")],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(validation.returncode, 0, validation.stderr)

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
            note = (output / "20260715T000000Z-test" / "obsidian-note.md").read_text(encoding="utf-8")
            self.assertIn("> [!warning] 数据不完整", note)

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
            drafts = json.loads((run_dir / "drafts.json").read_text(encoding="utf-8"))
            self.assertEqual(report["health"]["status"], "failed")
            self.assertEqual(report["health"]["item_count"], 0)
            self.assertEqual(drafts["drafts"], [])

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

    def test_draft_validator_rejects_non_chinese_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "drafts.json"
            text = "AI trend to watch https://example.com/topic"
            payload = {
                "schema_version": "1.0",
                "language": "en",
                "max_characters": 280,
                "drafts": [{
                    "id": "draft-01",
                    "topic_id": "topic-01",
                    "text": text,
                    "character_count": len(text),
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
