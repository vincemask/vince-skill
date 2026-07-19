#!/usr/bin/env python3
"""Small filesystem-backed Obsidian CLI double used only by publisher tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def parameter(arguments: list[str], name: str) -> str | None:
    prefix = name + "="
    return next((value[len(prefix):] for value in arguments if value.startswith(prefix)), None)


def fail(message: str, code: int = 1) -> int:
    print(message, file=sys.stderr)
    return code


def main() -> int:
    mode = os.environ.get("FAKE_OBSIDIAN_FAIL", "")
    if mode == "exit134":
        return 134
    arguments = sys.argv[1:]
    vault_argument = next((value for value in arguments if value.startswith("vault=")), None)
    if vault_argument:
        arguments.remove(vault_argument)
    if not arguments:
        return fail("missing command")
    command, command_arguments = arguments[0], arguments[1:]
    command_log = os.environ.get("FAKE_OBSIDIAN_COMMAND_LOG")
    if command_log:
        with Path(command_log).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"command": command, "arguments": command_arguments}) + "\n")
    if command == "version":
        print("1.0.0-fake")
        return 0
    root_value = os.environ.get("FAKE_OBSIDIAN_ROOT")
    if not root_value:
        return fail("FAKE_OBSIDIAN_ROOT missing")
    vault_name = vault_argument.split("=", 1)[1] if vault_argument else ""
    vault = (Path(root_value) / vault_name).resolve()
    if not vault.is_dir():
        return fail("vault not found")
    if command == "vault":
        print(vault_name)
        return 0
    relative = parameter(command_arguments, "path")
    if relative is None:
        return fail("path parameter missing")
    target = (vault / relative).resolve()
    try:
        target.relative_to(vault)
    except ValueError:
        return fail("unsafe path")
    if command == "folder":
        if not target.is_dir():
            return fail(f'Error: Folder "{relative}" not found.', 0)
        print(relative)
        return 0
    if command == "file":
        if not target.is_file():
            return fail(f'Error: File "{relative}" not found.', 0)
        print(relative)
        return 0
    if command == "read":
        if not target.is_file():
            return fail(f'Error: File "{relative}" not found.', 0)
        content = target.read_text(encoding="utf-8")
        if mode == "read-mismatch" and target.name.startswith("trend-"):
            content = content.replace("**X 成稿：**", "**成稿内容已损坏：**")
        sys.stdout.write(content)
        return 0
    if command == "create":
        content = parameter(command_arguments, "content")
        if content is None:
            return fail("content parameter missing")
        overwrite = "overwrite" in command_arguments
        if target.exists() and not overwrite:
            return fail("file exists")
        if mode == "index-write" and target.name == "index.md":
            return fail("injected index failure")
        if mode == "note-create" and target.name.startswith("trend-"):
            return fail("injected note failure")
        target.write_text(content, encoding="utf-8")
        print(relative)
        return 0
    if command == "append":
        content = parameter(command_arguments, "content")
        if content is None or not target.is_file():
            return fail("append target missing")
        if mode == "log-append" and target.name == "log.md":
            return fail("injected log failure")
        existing = target.read_text(encoding="utf-8")
        if "inline" in command_arguments:
            target.write_text(existing + content, encoding="utf-8")
            print(relative)
            return 0
        separator = "" if not existing or existing.endswith("\n") else "\n"
        target.write_text(existing + separator + content + "\n", encoding="utf-8")
        print(relative)
        return 0
    return fail(f"unsupported command: {command}")


if __name__ == "__main__":
    raise SystemExit(main())
