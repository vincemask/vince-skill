from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fixture_helpers import editorial_for, finalize_run, write_editorial


TEST_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = TEST_DIR.parent
COLLECTOR = SCRIPT_DIR / "collect_ai_trends.py"
VALIDATOR = SCRIPT_DIR / "validate_x_drafts.py"
CONFIG = TEST_DIR / "fixture-config.json"
FIXTURES = TEST_DIR / "fixtures"
RUN_ID = "20260715T000000Z-finalize"


class FinalizerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / "output"
        collection = subprocess.run(
            [
                sys.executable, str(COLLECTOR), "--config", str(CONFIG),
                "--fixture-dir", str(FIXTURES), "--output-dir", str(self.output),
                "--run-id", RUN_ID,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(collection.returncode, 0, collection.stderr)
        self.run_dir = self.output / RUN_ID
        self.editorial_input = json.loads((self.run_dir / "editorial-input.json").read_text(encoding="utf-8"))
        self.valid_editorial = editorial_for(self.editorial_input)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def save_editorial(self, payload: dict) -> None:
        (self.run_dir / "editorial.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_valid_editorial_generates_compact_human_and_obsidian_outputs(self) -> None:
        write_editorial(self.run_dir)
        result = finalize_run(self.run_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "finalized")
        self.assertEqual(payload["topic_count"], 2)
        for relative in (
            "editorial.json", "drafts.json", "report.md", "x-drafts.md",
            "obsidian-note.md", "obsidian-publish.json", "finalized.json",
        ):
            self.assertTrue((self.run_dir / relative).is_file(), relative)
        report_md = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("# AI 热点与 X 成稿", report_md)
        self.assertIn("## 01｜推理模型开放开发者接口", report_md)
        self.assertIn("**推荐理由：**", report_md)
        self.assertIn("**X 成稿：**", report_md)
        self.assertIn("本次仅有 2 个可靠话题", report_md)
        for forbidden in ("## 来源状态", "## 采集诊断", "## 局限说明", "原始主标题", "证据来源："):
            self.assertNotIn(forbidden, report_md)
        note = (self.run_dir / "obsidian-note.md").read_text(encoding="utf-8")
        self.assertIn("[[concepts/news-monitoring-and-growth]]", note)
        self.assertIn("post_mode: long", note)
        self.assertEqual(note.count("  - \"https://github.com/example/"), 2)
        self.assertNotIn("https://reddit.com/", note)
        drafts = json.loads((self.run_dir / "drafts.json").read_text(encoding="utf-8"))
        self.assertEqual(len(drafts["drafts"]), 2)
        self.assertTrue(all(len(draft["sources"]) == 1 for draft in drafts["drafts"]))
        validation = subprocess.run(
            [sys.executable, str(VALIDATOR), str(self.run_dir / "drafts.json")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(validation.returncode, 0, validation.stderr)
        plan = json.loads((self.run_dir / "obsidian-publish.json").read_text(encoding="utf-8"))
        self.assertIn("2 个推荐话题与 2 条 X 成稿", plan["index_entry"])

    def test_editorial_validation_rejects_required_failure_modes(self) -> None:
        cases = {}
        missing = copy.deepcopy(self.valid_editorial)
        missing["items"].pop()
        cases["必须包含 2 个话题"] = missing

        reordered = copy.deepcopy(self.valid_editorial)
        reordered["items"][0]["topic_id"] = reordered["items"][1]["topic_id"]
        cases["顺序错误"] = reordered

        unknown_url = copy.deepcopy(self.valid_editorial)
        unknown_url["items"][0]["primary_url"] = "https://example.com/unknown"
        unknown_url["items"][0]["x_post"] = unknown_url["items"][0]["x_post"].rsplit("\n", 1)[0] + "\nhttps://example.com/unknown"
        cases["不属于该话题"] = unknown_url

        generic = copy.deepcopy(self.valid_editorial)
        generic["items"][0]["x_post"] = "检测到来自多个平台的共同信号，" + generic["items"][0]["x_post"]
        cases["禁止的泛化模板"] = generic

        missing_reason = copy.deepcopy(self.valid_editorial)
        missing_reason["items"][0]["recommendation_reason"] = ""
        cases["recommendation_reason 长度必须符合配置"] = missing_reason

        short = copy.deepcopy(self.valid_editorial)
        short["items"][0]["x_post"] = "这是过短的中文正文。\n\n" + short["items"][0]["primary_url"]
        cases["正文长度必须符合配置"] = short

        multiple_urls = copy.deepcopy(self.valid_editorial)
        multiple_urls["items"][0]["x_post"] = "https://example.com/extra\n" + multiple_urls["items"][0]["x_post"]
        cases["必须且只能包含"] = multiple_urls

        non_chinese = copy.deepcopy(self.valid_editorial)
        non_chinese["items"][0]["title_zh"] = "English title only"
        cases["中文标题"] = non_chinese

        duplicate = copy.deepcopy(self.valid_editorial)
        first_prose = duplicate["items"][0]["x_post"].rsplit("\n", 2)[0]
        duplicate["items"][1]["x_post"] = first_prose + "\n\n" + duplicate["items"][1]["primary_url"]
        cases["与其他成稿重复"] = duplicate

        for message, payload in cases.items():
            with self.subTest(message=message):
                self.save_editorial(payload)
                result = finalize_run(self.run_dir)
                self.assertEqual(result.returncode, 2)
                self.assertIn(message, result.stderr)
                self.assertFalse((self.run_dir / "obsidian-publish.json").exists())

    def test_partial_run_combines_all_notices_into_one_warning(self) -> None:
        report_path = self.run_dir / "report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["health"]["status"] = "partial"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_editorial(self.run_dir)
        result = finalize_run(self.run_dir)
        self.assertEqual(result.returncode, 0, result.stderr)
        report_md = (self.run_dir / "report.md").read_text(encoding="utf-8")
        self.assertEqual(report_md.count("> [!warning]"), 1)
        self.assertIn("包含缓存或失败来源", report_md)
        self.assertIn("本次仅有 2 个可靠话题", report_md)
        for forbidden in ("## 来源状态", "## 采集诊断", "## 局限说明"):
            self.assertNotIn(forbidden, report_md)

    def test_successfully_published_run_cannot_be_finalized_again(self) -> None:
        write_editorial(self.run_dir)
        self.assertEqual(finalize_run(self.run_dir).returncode, 0)
        (self.run_dir / "obsidian-publish-result.json").write_text(
            json.dumps({"status": "published"}),
            encoding="utf-8",
        )
        result = finalize_run(self.run_dir)
        self.assertEqual(result.returncode, 2)
        self.assertIn("已经发布成功", result.stderr)


if __name__ == "__main__":
    unittest.main()
