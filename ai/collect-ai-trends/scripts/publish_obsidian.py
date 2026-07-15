#!/usr/bin/env python3
"""Publish a staged AI trend note through Obsidian CLI with idempotent verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HEADER_RE = re.compile(r"(?m)^> Last updated:\s*([^|\r\n]+?)\s*\|\s*Total pages:\s*(\d+)\s*$")
RAW_HEADING_RE = re.compile(r"(?m)^## Raw\s*$")
VAULT_NAME_RE = re.compile(r"^[^/\\]+$")


class PublishError(RuntimeError):
    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def write_json(path: Path, payload: Any) -> None:
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def safe_detail(value: str, limit: int = 900) -> str:
    return re.sub(r"\s+", " ", value or "").strip()[:limit]


def normalize_content(value: str) -> str:
    return value.replace("\r\n", "\n").rstrip("\n") + "\n"


def sha256_text(value: str) -> str:
    return hashlib.sha256(normalize_content(value).encode("utf-8")).hexdigest()


def validate_relative_path(value: Any, field: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = Path(text)
    if (
        not text
        or text in (".", "..")
        or path.is_absolute()
        or "//" in text
        or any(part in (".", "..") for part in path.parts)
    ):
        raise PublishError("plan", f"{field} 必须是 Vault 内不含 . 或 .. 的相对路径")
    return text.rstrip("/")


def validate_plan(plan: dict[str, Any], plan_path: Path) -> dict[str, Any]:
    required = {
        "run_id", "vault", "target_directory", "note_path", "note_wikilink", "note_file",
        "note_sha256", "index_path", "log_path", "created_date", "signal_count",
        "topic_count", "draft_count", "index_entry", "log_entry", "log_marker",
        "required_note_markers",
    }
    missing = sorted(required - set(plan))
    if missing:
        raise PublishError("plan", f"发布计划缺少字段：{', '.join(missing)}")
    if plan.get("schema_version") != 1:
        raise PublishError("plan", "不支持的发布计划 schema_version")
    vault = str(plan["vault"]).strip()
    if not vault or vault in (".", "..") or not VAULT_NAME_RE.fullmatch(vault):
        raise PublishError("plan", "vault 必须是名称，不能是文件系统路径")
    target = validate_relative_path(plan["target_directory"], "target_directory")
    note_path = validate_relative_path(plan["note_path"], "note_path")
    index_path = validate_relative_path(plan["index_path"], "index_path")
    log_path = validate_relative_path(plan["log_path"], "log_path")
    if not note_path.startswith(target + "/") or not note_path.endswith(".md"):
        raise PublishError("plan", "note_path 必须位于 target_directory 且以 .md 结尾")
    expected_wikilink = f"[[{note_path[:-3]}]]"
    if plan["note_wikilink"] != expected_wikilink:
        raise PublishError("plan", "note_wikilink 与 note_path 不一致")
    note_file = Path(str(plan["note_file"])).resolve()
    if not note_file.is_file():
        raise PublishError("plan", f"本地笔记不存在：{note_file}")
    if note_file.parent != plan_path.parent.resolve():
        raise PublishError("plan", "note_file 必须与 obsidian-publish.json 位于同一运行目录")
    note = note_file.read_text(encoding="utf-8")
    if sha256_text(note) != str(plan["note_sha256"]):
        raise PublishError("plan", "本地笔记摘要与发布计划不一致")
    markers = plan["required_note_markers"]
    if not isinstance(markers, list) or not markers or any(not isinstance(value, str) for value in markers):
        raise PublishError("plan", "required_note_markers 必须是非空字符串数组")
    for marker in markers:
        if marker not in note:
            raise PublishError("plan", f"本地笔记缺少必要标记：{marker}")
    normalized = dict(plan)
    normalized.update({
        "vault": vault,
        "target_directory": target,
        "note_path": note_path,
        "index_path": index_path,
        "log_path": log_path,
        "note_file": str(note_file),
        "note_content": note,
    })
    return normalized


class ObsidianCLI:
    def __init__(self, vault: str):
        self.binary = os.environ.get("OBSIDIAN_BIN", "obsidian")
        self.binary_argv = shlex.split(self.binary)
        if not self.binary_argv:
            raise PublishError("preflight", "OBSIDIAN_BIN 不能为空")
        self.vault = vault
        self.timeout = int(os.environ.get("OBSIDIAN_TIMEOUT_SECONDS", "45"))

    def run(self, command: str, *arguments: str, use_vault: bool = True) -> subprocess.CompletedProcess[str]:
        argv = list(self.binary_argv)
        if use_vault:
            argv.append(f"vault={self.vault}")
        argv.extend([command, *arguments])
        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise PublishError("preflight", f"找不到 Obsidian CLI：{self.binary}") from exc
        except subprocess.TimeoutExpired as exc:
            raise PublishError("preflight", f"Obsidian CLI 超时：{command}") from exc
        if result.returncode in (134, -6):
            raise PublishError("preflight", "Obsidian CLI 异常退出 134；若处于 Codex 沙箱，请批准在沙箱外执行发布脚本")
        return result

    def require(self, stage: str, command: str, *arguments: str, use_vault: bool = True) -> str:
        result = self.run(command, *arguments, use_vault=use_vault)
        if self.failed(result):
            detail = safe_detail(result.stderr or result.stdout or f"exit {result.returncode}")
            raise PublishError(stage, f"Obsidian CLI 命令失败：{command}（{detail}）")
        return result.stdout

    @staticmethod
    def failed(result: subprocess.CompletedProcess[str]) -> bool:
        output = (result.stderr or "") + "\n" + (result.stdout or "")
        return result.returncode != 0 or re.search(r"(?mi)^\s*Error:\s*", output) is not None

    @staticmethod
    def missing(result: subprocess.CompletedProcess[str]) -> bool:
        output = (result.stderr or "") + "\n" + (result.stdout or "")
        return re.search(r"(?i)\b(?:not found|does not exist|no such file)\b", output) is not None

    def read(self, stage: str, path: str) -> str:
        return self.require(stage, "read", f"path={path}")


def verify_note(plan: dict[str, Any], content: str) -> None:
    for marker in plan["required_note_markers"]:
        if marker not in content:
            raise PublishError("note", f"Obsidian 回读笔记缺少标记：{marker}")
    if sha256_text(content) != plan["note_sha256"]:
        raise PublishError("note", "Obsidian 回读笔记与本地摘要不一致")


def preflight(cli: ObsidianCLI, plan: dict[str, Any]) -> tuple[str, str, str | None]:
    cli.require("preflight", "version", use_vault=False)
    cli.require("preflight", "vault")
    cli.require("preflight", "folder", f"path={plan['target_directory']}")
    index_content = cli.read("preflight", plan["index_path"])
    log_content = cli.read("preflight", plan["log_path"])
    note_info = cli.run("file", f"path={plan['note_path']}")
    if cli.failed(note_info) and not cli.missing(note_info):
        detail = safe_detail(note_info.stderr or note_info.stdout or f"exit {note_info.returncode}")
        raise PublishError("preflight", f"无法检查目标笔记：{detail}")
    note_content = None if cli.missing(note_info) else cli.read("preflight", plan["note_path"])
    if note_content is not None:
        run_marker = f"run_id: {json.dumps(str(plan['run_id']), ensure_ascii=False)}"
        if run_marker not in note_content:
            raise PublishError("note", f"目标路径已被其他运行占用：{plan['note_path']}")
        verify_note(plan, note_content)
    return index_content, log_content, note_content


def create_or_verify_note(cli: ObsidianCLI, plan: dict[str, Any], existing: str | None, actions: list[str]) -> None:
    if existing is None:
        result = cli.run("create", f"path={plan['note_path']}", f"content={plan['note_content']}")
        if cli.failed(result):
            check = cli.run("file", f"path={plan['note_path']}")
            if cli.failed(check):
                detail = safe_detail(result.stderr or result.stdout or f"exit {result.returncode}")
                raise PublishError("note", f"创建趋势笔记失败：{detail}")
        actions.append("note-created")
    else:
        actions.append("note-reused")
    verify_note(plan, cli.read("note", plan["note_path"]))


def updated_index(content: str, plan: dict[str, Any]) -> tuple[str, bool, int]:
    header = HEADER_RE.search(content)
    if header is None:
        raise PublishError("index", "index.md 缺少 Last updated / Total pages 标头")
    current_total = int(header.group(2))
    link_present = plan["note_wikilink"] in content
    if link_present:
        return content, False, current_total
    if RAW_HEADING_RE.search(content) is None:
        raise PublishError("index", "index.md 缺少 Raw 区域")
    new_total = current_total + 1
    header_replacement = f"> Last updated: {plan['created_date']} | Total pages: {new_total}"
    output = HEADER_RE.sub(header_replacement, content, count=1)
    raw = RAW_HEADING_RE.search(output)
    if raw is None:
        raise PublishError("index", "index.md Raw 区域定位失败")
    insert_at = raw.end()
    output = output[:insert_at] + "\n\n" + plan["index_entry"] + output[insert_at:]
    return normalize_content(output), True, new_total


def update_index(cli: ObsidianCLI, plan: dict[str, Any], content: str, actions: list[str]) -> int:
    expected, changed, total = updated_index(content, plan)
    if changed:
        result = cli.run("create", f"path={plan['index_path']}", f"content={expected}", "overwrite")
        readback = cli.read("index", plan["index_path"])
        if cli.failed(result) and plan["note_wikilink"] not in readback:
            detail = safe_detail(result.stderr or result.stdout or f"exit {result.returncode}")
            raise PublishError("index", f"更新 index.md 失败：{detail}")
        actions.append("index-updated")
    else:
        readback = content
        actions.append("index-reused")
    if plan["note_wikilink"] not in readback or plan["index_entry"] not in readback:
        raise PublishError("index", "index.md 回读未找到预期 Raw 条目")
    header = HEADER_RE.search(readback)
    if header is None or int(header.group(2)) != total:
        raise PublishError("index", "index.md 回读页数不一致")
    return total


def update_log(cli: ObsidianCLI, plan: dict[str, Any], content: str, actions: list[str]) -> None:
    marker = f"<!-- {plan['log_marker']} -->"
    if marker not in content:
        result = cli.run("append", f"path={plan['log_path']}", f"content={plan['log_entry']}")
        readback = cli.read("log", plan["log_path"])
        if cli.failed(result) and marker not in readback:
            detail = safe_detail(result.stderr or result.stdout or f"exit {result.returncode}")
            raise PublishError("log", f"追加 log.md 失败：{detail}")
        actions.append("log-appended")
    else:
        readback = content
        actions.append("log-reused")
    if marker not in readback or f"run_id={plan['run_id']}" not in readback:
        raise PublishError("log", "log.md 回读未找到运行记录")


def final_verify(cli: ObsidianCLI, plan: dict[str, Any], expected_total: int) -> None:
    verify_note(plan, cli.read("final", plan["note_path"]))
    index_content = cli.read("final", plan["index_path"])
    log_content = cli.read("final", plan["log_path"])
    if plan["index_entry"] not in index_content:
        raise PublishError("final", "最终回读未找到 index.md 条目")
    header = HEADER_RE.search(index_content)
    if header is None or int(header.group(2)) != expected_total:
        raise PublishError("final", "最终回读 index.md 页数不一致")
    if f"<!-- {plan['log_marker']} -->" not in log_content:
        raise PublishError("final", "最终回读未找到 log.md 记录")


def result_payload(plan: dict[str, Any], status: str, stage: str, actions: list[str], **extra: Any) -> dict[str, Any]:
    payload = {
        "schema_version": 1,
        "status": status,
        "stage": stage,
        "run_id": plan.get("run_id"),
        "vault": plan.get("vault"),
        "note_path": plan.get("note_path"),
        "note_wikilink": plan.get("note_wikilink"),
        "actions": actions,
        "verified_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    payload.update(extra)
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path, help="Path to obsidian-publish.json")
    parser.add_argument("--preflight", action="store_true", help="Only perform read-only CLI checks")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    plan_path = args.plan.resolve()
    result_path = plan_path.parent / "obsidian-publish-result.json"
    actions: list[str] = []
    plan: dict[str, Any] = {}
    try:
        with plan_path.open("r", encoding="utf-8") as handle:
            raw_plan = json.load(handle)
        if not isinstance(raw_plan, dict):
            raise PublishError("plan", "发布计划必须是 JSON 对象")
        plan = validate_plan(raw_plan, plan_path)
        cli = ObsidianCLI(plan["vault"])
        index_content, log_content, note_content = preflight(cli, plan)
        actions.append("preflight-ok")
        if args.preflight:
            payload = result_payload(plan, "preflight-ok", "preflight", actions, note_exists=note_content is not None)
            print(json.dumps(payload, ensure_ascii=False))
            return 0
        create_or_verify_note(cli, plan, note_content, actions)
        total = update_index(cli, plan, index_content, actions)
        current_log = cli.read("log", plan["log_path"])
        update_log(cli, plan, current_log, actions)
        final_verify(cli, plan, total)
        actions.append("final-verified")
        payload = result_payload(plan, "published", "complete", actions, total_pages=total)
        write_json(result_path, payload)
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, json.JSONDecodeError, PublishError) as exc:
        stage = exc.stage if isinstance(exc, PublishError) else "plan"
        message = safe_detail(str(exc))
        payload = result_payload(plan, "failed", stage, actions, error=message)
        try:
            write_json(result_path, payload)
        except OSError:
            pass
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
