---
name: example-skill
description: Demonstrate a minimal, reusable Codex Skill structure and workflow. Use when testing this repository's Skill layout or as a starting point for a new Skill.
---

# Example Skill

Use this file as a compact template. Replace every example instruction with domain-specific guidance before publishing a real Skill.

## Workflow

1. Confirm the requested outcome and inspect the relevant local context.
2. Choose the smallest safe action that satisfies the request.
3. Execute the action with bundled scripts or references only when they add value.
4. Verify the result with a concrete check.
5. Report the outcome, changed artifacts, and any remaining limitation.

## Resource routing

- Read `references/` only when the request needs detailed domain knowledge.
- Run or update `scripts/` when the same deterministic operation would otherwise be rewritten.
- Reuse `assets/` for templates, icons, or files copied into generated output.
- Remove unused resource directories from a real Skill.

## Safety

- Preserve unrelated user changes.
- Do not expose secrets in commands, logs, or responses.
- Request confirmation before destructive or externally visible actions that exceed the user's stated scope.
