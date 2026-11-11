# vince-skill

Public repo of installable Codex skills. Base branch: `main`.

- Layout: `<category>/<skill-name>/SKILL.md`, plus optional `agents/openai.yaml`, `scripts/`, `references/`, `assets/`. Start from `category-name/example-skill`.
- `SKILL.md` front matter has only `name` and `description`; `name` matches the directory name.
- Validate a skill: `python3 "${CODEX_HOME:-$HOME/.codex}/skills/.system/skill-creator/scripts/quick_validate.py" <category>/<skill-name>`
- Run a skill's tests: `python3 -m unittest discover -s <category>/<skill-name>/scripts/tests`
- This repo is public: no secrets, tokens, personal config or generated output.
