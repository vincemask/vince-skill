from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "tooling" / "generate_skills.py"


class GenerateSkillsTests(unittest.TestCase):
    def test_committed_skill_scripts_match_canonical_sources(self) -> None:
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_each_generated_skill_is_self_contained(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir)
            result = subprocess.run(
                [sys.executable, str(GENERATOR), "--output-root", str(destination)],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            for skill_name in (
                "collect-ai-trends",
                "collect-github-ai-trends",
                "collect-reddit-ai-trends",
                "collect-x-ai-trends",
            ):
                scripts = destination / "ai" / skill_name / "scripts"
                self.assertTrue((scripts / "finalize_ai_trends.py").is_file())
                self.assertTrue((scripts / "validate_x_drafts.py").is_file())
                collectors = list(scripts.glob("collect*_ai_trends.py"))
                self.assertEqual(len(collectors), 1)
                for script in (
                    collectors[0],
                    scripts / "finalize_ai_trends.py",
                    scripts / "validate_x_drafts.py",
                ):
                    smoke = subprocess.run(
                        [sys.executable, str(script), "--help"],
                        cwd=scripts,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(smoke.returncode, 0, smoke.stdout + smoke.stderr)


if __name__ == "__main__":
    unittest.main()
