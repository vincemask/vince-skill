from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = REPO_ROOT / "tooling" / "validate_repo.py"


class ValidateRepoTests(unittest.TestCase):
    def run_validator(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR), str(root)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_current_repository_is_valid(self) -> None:
        result = self.run_validator(REPO_ROOT)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reports_directory_name_and_default_prompt_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill = Path(temp_dir) / "nested" / "ai" / "wrong-directory"
            (skill / "agents").mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: actual-name\ndescription: Test skill.\n---\n\n# Test\n",
                encoding="utf-8",
            )
            (skill / "agents" / "openai.yaml").write_text(
                'interface:\n  default_prompt: "Run this skill."\n',
                encoding="utf-8",
            )

            result = self.run_validator(Path(temp_dir))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("directory name", result.stderr)
        self.assertIn("$actual-name", result.stderr)

    def test_rejects_invalid_openai_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            skill = Path(temp_dir) / "ai" / "test-skill"
            (skill / "agents").mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: test-skill\ndescription: Test skill.\n---\n\n# Test\n",
                encoding="utf-8",
            )
            (skill / "agents" / "openai.yaml").write_text("interface: [\n", encoding="utf-8")

            result = self.run_validator(Path(temp_dir))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid YAML", result.stderr)


if __name__ == "__main__":
    unittest.main()
