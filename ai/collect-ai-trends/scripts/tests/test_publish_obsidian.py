from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fixture_helpers import finalize_run, write_editorial


TEST_DIR = Path(__file__).resolve().parent
SCRIPT_DIR = TEST_DIR.parent
COLLECTOR = SCRIPT_DIR / "collect_ai_trends.py"
PUBLISHER = SCRIPT_DIR / "publish_obsidian.py"
FAKE_OBSIDIAN = TEST_DIR / "fake_obsidian.py"
CONFIG = TEST_DIR / "fixture-config.json"
FIXTURES = TEST_DIR / "fixtures"
RUN_ID = "20260715T000000Z-publish"


class PublisherIntegrationTests(unittest.TestCase):
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
        write_editorial(self.run_dir)
        finalization = finalize_run(self.run_dir)
        self.assertEqual(finalization.returncode, 0, finalization.stderr)
        self.plan_path = self.run_dir / "obsidian-publish.json"
        self.plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        self.fake_root = self.root / "fake-vaults"
        self.vault = self.fake_root / "wiki"
        (self.vault / "raw").mkdir(parents=True)
        (self.vault / "index.md").write_text(
            "# Index\n\n> Last updated: 2026-07-14 | Total pages: 10\n\n## Raw\n",
            encoding="utf-8",
        )
        (self.vault / "log.md").write_text("# Log\n", encoding="utf-8")
        self.command_log = self.root / "commands.jsonl"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def environment(self, failure: str = "") -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "OBSIDIAN_BIN": f"{sys.executable} {FAKE_OBSIDIAN}",
            "FAKE_OBSIDIAN_ROOT": str(self.fake_root),
            "FAKE_OBSIDIAN_COMMAND_LOG": str(self.command_log),
            "FAKE_OBSIDIAN_FAIL": failure,
        })
        return env

    def publish(self, failure: str = "", preflight: bool = False) -> subprocess.CompletedProcess[str]:
        command = [sys.executable, str(PUBLISHER), str(self.plan_path)]
        if preflight:
            command.append("--preflight")
        return subprocess.run(command, capture_output=True, text=True, env=self.environment(failure), check=False)

    @property
    def note_path(self) -> Path:
        return self.vault / self.plan["note_path"]

    def test_first_publish_creates_note_index_log_and_verified_result(self) -> None:
        result = self.publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "published")
        self.assertEqual(
            self.note_path.read_text(encoding="utf-8"),
            (self.run_dir / "obsidian-note.md").read_text(encoding="utf-8"),
        )
        index = (self.vault / "index.md").read_text(encoding="utf-8")
        log = (self.vault / "log.md").read_text(encoding="utf-8")
        self.assertIn(self.plan["index_entry"], index)
        self.assertIn("Total pages: 11", index)
        self.assertIn(self.plan["log_marker"], log)
        saved = json.loads((self.run_dir / "obsidian-publish-result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "published")
        commands = [json.loads(line)["command"] for line in self.command_log.read_text(encoding="utf-8").splitlines()]
        self.assertTrue({"vault", "folder", "file", "read", "create", "append"}.issubset(set(commands)))

    def test_same_run_retry_is_idempotent(self) -> None:
        self.assertEqual(self.publish().returncode, 0)
        retry = self.publish()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        payload = json.loads(retry.stdout)
        self.assertIn("note-reused", payload["actions"])
        self.assertIn("index-reused", payload["actions"])
        self.assertIn("log-reused", payload["actions"])
        index = (self.vault / "index.md").read_text(encoding="utf-8")
        log = (self.vault / "log.md").read_text(encoding="utf-8")
        self.assertEqual(index.count(self.plan["note_wikilink"]), 1)
        self.assertEqual(log.count(self.plan["log_marker"]), 1)
        self.assertIn("Total pages: 11", index)

    def test_retry_completes_missing_index_entry(self) -> None:
        self.assertEqual(self.publish().returncode, 0)
        (self.vault / "index.md").write_text(
            "# Index\n\n> Last updated: 2026-07-14 | Total pages: 10\n\n## Raw\n",
            encoding="utf-8",
        )
        retry = self.publish()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        payload = json.loads(retry.stdout)
        self.assertIn("note-reused", payload["actions"])
        self.assertIn("index-updated", payload["actions"])
        self.assertIn("log-reused", payload["actions"])

    def test_retry_completes_missing_log_entry(self) -> None:
        self.assertEqual(self.publish().returncode, 0)
        (self.vault / "log.md").write_text("# Log\n", encoding="utf-8")
        retry = self.publish()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        payload = json.loads(retry.stdout)
        self.assertIn("note-reused", payload["actions"])
        self.assertIn("index-reused", payload["actions"])
        self.assertIn("log-appended", payload["actions"])
        self.assertEqual((self.vault / "log.md").read_text(encoding="utf-8").count(self.plan["log_marker"]), 1)

    def test_path_collision_fails_without_overwrite(self) -> None:
        self.note_path.write_text("---\nrun_id: \"other-run\"\n---\n", encoding="utf-8")
        result = self.publish()
        self.assertEqual(result.returncode, 4)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["stage"], "note")
        self.assertIn("其他运行占用", payload["error"])
        self.assertIn("other-run", self.note_path.read_text(encoding="utf-8"))

    def test_missing_target_directory_fails_during_read_only_preflight(self) -> None:
        (self.vault / "raw").rmdir()
        result = self.publish()
        self.assertEqual(result.returncode, 4)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["stage"], "preflight")
        self.assertFalse(self.note_path.exists())

    def test_cli_exit_134_returns_actionable_failure(self) -> None:
        result = self.publish("exit134")
        self.assertEqual(result.returncode, 4)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["stage"], "preflight")
        self.assertIn("134", payload["error"])
        self.assertIn("沙箱外", payload["error"])

    def test_note_readback_mismatch_fails_before_index_and_log(self) -> None:
        result = self.publish("read-mismatch")
        self.assertEqual(result.returncode, 4)
        payload = json.loads(result.stderr)
        self.assertEqual(payload["stage"], "note")
        self.assertNotIn(self.plan["note_wikilink"], (self.vault / "index.md").read_text(encoding="utf-8"))
        self.assertNotIn(self.plan["log_marker"], (self.vault / "log.md").read_text(encoding="utf-8"))

    def test_large_cjk_note_is_uploaded_in_safe_chunks(self) -> None:
        note = ("a" * 3400 + "开源\n") * 3 + "尾\n"
        (self.run_dir / "obsidian-note.md").write_text(note, encoding="utf-8")
        self.plan["note_sha256"] = hashlib.sha256(note.encode("utf-8")).hexdigest()
        self.plan["required_note_markers"] = ["开源", "尾"]
        self.plan_path.write_text(json.dumps(self.plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        result = self.publish()

        self.assertEqual(result.returncode, 0, result.stderr)
        writes = []
        for line in self.command_log.read_text(encoding="utf-8").splitlines():
            command = json.loads(line)
            if (
                command["command"] in {"create", "append"}
                and f"path={self.plan['note_path']}" in command["arguments"]
            ):
                content = next((value[8:] for value in command["arguments"] if value.startswith("content=")), None)
                if content is not None:
                    writes.append(content)
        self.assertEqual(len(writes), 3)
        self.assertEqual("".join(writes), note)
        self.assertTrue(all(len(content.encode("utf-8")) <= 3500 for content in writes))

    def test_large_index_is_uploaded_in_safe_chunks(self) -> None:
        index = (
            "# Index\n\n> Last updated: 2026-07-14 | Total pages: 10\n\n## Raw\n"
            + ("a" * 3400 + "开源\n") * 3
        )
        (self.vault / "index.md").write_text(index, encoding="utf-8")

        result = self.publish()

        self.assertEqual(result.returncode, 0, result.stderr)
        writes = []
        for line in self.command_log.read_text(encoding="utf-8").splitlines():
            command = json.loads(line)
            if (
                command["command"] in {"create", "append"}
                and f"path={self.plan['index_path']}" in command["arguments"]
            ):
                content = next((value[8:] for value in command["arguments"] if value.startswith("content=")), None)
                if content is not None:
                    writes.append(content)
        self.assertGreater(len(writes), 1)
        self.assertEqual("".join(writes), (self.vault / "index.md").read_text(encoding="utf-8"))
        self.assertTrue(all(len(content.encode("utf-8")) <= 3500 for content in writes))

    def test_preflight_is_read_only_and_uses_cli_for_every_vault_check(self) -> None:
        before_index = (self.vault / "index.md").read_text(encoding="utf-8")
        before_log = (self.vault / "log.md").read_text(encoding="utf-8")
        result = self.publish(preflight=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.note_path.exists())
        self.assertEqual((self.vault / "index.md").read_text(encoding="utf-8"), before_index)
        self.assertEqual((self.vault / "log.md").read_text(encoding="utf-8"), before_log)
        commands = [json.loads(line)["command"] for line in self.command_log.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(commands[:5], ["version", "vault", "folder", "read", "read"])
        self.assertIn("file", commands)
        self.assertNotIn("create", commands)
        self.assertNotIn("append", commands)


if __name__ == "__main__":
    unittest.main()
