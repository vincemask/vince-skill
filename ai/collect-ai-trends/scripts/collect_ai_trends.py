#!/usr/bin/env python3
"""Collect, normalize, rank, and render AI trend signals with bounded failure modes."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SCHEMA_VERSION = "1.0"
SOURCE_NAMES = ("reddit", "x", "github")
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TOKEN_RE = re.compile(
    r"(?i)(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|bearer\s+[A-Za-z0-9._~+/=-]{16,})"
)
WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#.-]{1,}", re.IGNORECASE)
RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z(?:-[a-z0-9-]+)?$")
VAULT_NAME_RE = re.compile(r"^[^/\\]+$")
STOP_WORDS = {
    "about", "after", "again", "also", "and", "are", "but", "for", "from", "has", "have",
    "into", "its", "new", "not", "now", "our", "the", "their", "this", "that", "with", "your",
    "发布", "一个", "以及", "最新", "模型", "人工智能", "这个", "来自", "正在",
}
STATUS_ZH = {
    "complete": "完整",
    "partial": "部分可用",
    "failed": "失败",
    "fresh": "最新",
    "cached": "缓存",
    "disabled": "未启用",
}
SOURCE_ZH = {"reddit": "Reddit", "x": "X", "github": "GitHub"}
METRIC_ZH = {
    "score": "赞同数",
    "comments": "评论数",
    "likes": "点赞数",
    "retweets": "转发数",
    "replies": "回复数",
    "views": "浏览数",
    "stars": "星标数",
    "forks": "复刻数",
    "open_issues": "未关闭问题数",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_error(value: str, limit: int = 700) -> str:
    text = ANSI_RE.sub("", value or "")
    text = TOKEN_RE.sub("[REDACTED]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def write_json(path: Path, payload: Any) -> None:
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n")


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def default_config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "references" / "default-config.json"


def validate_relative_vault_path(value: Any, field: str, *, directory: bool = False) -> str:
    text = str(value or "").strip().replace("\\", "/")
    path = Path(text)
    if (
        not text
        or text in (".", "..")
        or path.is_absolute()
        or "//" in text
        or any(part in (".", "..") for part in path.parts)
    ):
        raise ValueError(f"{field} must be a relative Vault path without '.' or '..'")
    if directory and text.endswith("/"):
        text = text.rstrip("/")
    return text


def validate_vault_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text or text in (".", "..") or not VAULT_NAME_RE.fullmatch(text):
        raise ValueError("config.obsidian.vault must be a Vault name, not a path")
    return text


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != 1:
        raise ValueError("config.schema_version must be 1")
    if config.get("document_language", "zh-CN") != "zh-CN":
        raise ValueError("config.document_language must be zh-CN")
    if not isinstance(config.get("window_hours"), (int, float)) or config["window_hours"] <= 0:
        raise ValueError("config.window_hours must be positive")
    for source in SOURCE_NAMES:
        if not isinstance(config.get(source), dict):
            raise ValueError(f"config.{source} must be an object")
    if config["reddit"].get("enabled") and not config["reddit"].get("subreddits"):
        raise ValueError("config.reddit.subreddits must not be empty when enabled")
    if config["x"].get("enabled") and not config["x"].get("accounts"):
        raise ValueError("config.x.accounts must not be empty when enabled")
    if config["github"].get("enabled") and not config["github"].get("queries"):
        raise ValueError("config.github.queries must not be empty when enabled")
    drafts = config.get("drafts", {})
    if drafts.get("language") != "zh-CN":
        raise ValueError("config.drafts.language must be zh-CN")
    if not 1 <= int(drafts.get("count", 0)) <= 20:
        raise ValueError("config.drafts.count must be between 1 and 20")
    if not 100 <= int(drafts.get("max_characters", 0)) <= 280:
        raise ValueError("config.drafts.max_characters must be between 100 and 280")
    try:
        ZoneInfo(str(config.get("timezone", "Asia/Shanghai")))
    except ZoneInfoNotFoundError as exc:
        raise ValueError("config.timezone must name an installed IANA timezone") from exc
    obsidian = config.get("obsidian")
    if not isinstance(obsidian, dict):
        raise ValueError("config.obsidian must be an object")
    if obsidian.get("enabled") is not True:
        raise ValueError("config.obsidian.enabled must be true because publishing is mandatory")
    if obsidian.get("strict") is not True:
        raise ValueError("config.obsidian.strict must be true because publishing failures are fatal")
    validate_vault_name(obsidian.get("vault"))
    validate_relative_vault_path(obsidian.get("target_directory"), "config.obsidian.target_directory", directory=True)
    validate_relative_vault_path(obsidian.get("index_path"), "config.obsidian.index_path")
    validate_relative_vault_path(obsidian.get("log_path"), "config.obsidian.log_path")


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("items", "results", "data", "tweets", "posts", "repositories"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                nested = extract_rows(value)
                if nested:
                    return nested
    return []


def parse_json_stdout(stdout: str) -> Any:
    clean = ANSI_RE.sub("", stdout).strip()
    if not clean:
        raise ValueError("command returned empty stdout")
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        starts = [index for index in (clean.find("["), clean.find("{")) if index >= 0]
        if not starts:
            raise ValueError("command stdout did not contain JSON")
        decoder = json.JSONDecoder()
        payload, _ = decoder.raw_decode(clean[min(starts):])
        return payload


def run_command(command: list[str], timeout: int, retries: int) -> tuple[Any, dict[str, Any]]:
    last_error = "unknown command error"
    started = time.monotonic()
    for attempt in range(retries + 1):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=os.environ.copy(),
            )
            if result.returncode == 0:
                payload = parse_json_stdout(result.stdout)
                return payload, {
                    "attempts": attempt + 1,
                    "duration_seconds": round(time.monotonic() - started, 3),
                }
            last_error = safe_error(result.stderr or result.stdout or f"exit code {result.returncode}")
        except subprocess.TimeoutExpired:
            last_error = f"timed out after {timeout}s"
        except (OSError, ValueError) as exc:
            last_error = safe_error(str(exc))
        if attempt < retries:
            time.sleep(min(2 ** attempt, 4))
    raise RuntimeError(last_error)


def cache_path(root: Path, request_id: str) -> Path:
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", request_id).strip("-")[:100]
    digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:10]
    return root / ".cache" / f"{safe_id}-{digest}.json"


def fetch_request(
    source: str,
    request_id: str,
    command: list[str],
    output_root: Path,
    timeout: int,
    retries: int,
    cache_max_age_hours: float,
    use_cache: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now = utc_now()
    record: dict[str, Any] = {
        "source": source,
        "request_id": request_id,
        "status": "failed",
        "fetched_at": iso_z(now),
        "attempts": 0,
        "duration_seconds": 0.0,
        "item_count": 0,
    }
    path = cache_path(output_root, request_id)
    try:
        payload, metadata = run_command(command, timeout, retries)
        rows = extract_rows(payload)
        record.update(metadata)
        record.update({"status": "fresh", "item_count": len(rows)})
        if use_cache:
            write_json(path, {"cached_at": iso_z(now), "source": source, "rows": rows})
        return rows, record
    except RuntimeError as exc:
        record["error"] = safe_error(str(exc))
    if use_cache and path.exists():
        try:
            cached = load_json(path)
            cached_at = parse_datetime(cached.get("cached_at"))
            if cached_at is not None:
                age = max(0.0, (now - cached_at).total_seconds() / 3600)
                if age <= cache_max_age_hours:
                    rows = extract_rows(cached.get("rows", []))
                    record.update({
                        "status": "cached",
                        "cache_age_hours": round(age, 2),
                        "item_count": len(rows),
                    })
                    return rows, record
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            record["cache_error"] = safe_error(str(exc))
    return [], record


def parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        return parse_datetime(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError):
            return None


def canonical_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text.startswith(("http://", "https://")):
        return ""
    try:
        parts = urlsplit(text)
        host = parts.netloc.lower().removeprefix("www.")
        if host == "twitter.com":
            host = "x.com"
        path = parts.path.rstrip("/") or "/"
        return urlunsplit(("https", host, path, "", ""))
    except ValueError:
        return ""


def as_number(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return 0.0
    text = str(value).strip().replace(",", "")
    multiplier = 1.0
    if text.lower().endswith("k"):
        multiplier, text = 1_000.0, text[:-1]
    elif text.lower().endswith("m"):
        multiplier, text = 1_000_000.0, text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return 0.0


def author_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("username") or value.get("screen_name") or value.get("name") or "")
    return str(value or "")


def stable_id(source: str, url: str, title: str) -> str:
    basis = f"{source}\0{url or title.strip().lower()}"
    return f"{source}-{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:12]}"


def normalize_reddit(row: dict[str, Any]) -> dict[str, Any] | None:
    title = str(row.get("title") or row.get("text") or "").strip()
    if not title:
        return None
    url = canonical_url(row.get("url") or row.get("permalink") or row.get("url_overridden_by_dest"))
    published = parse_datetime(row.get("created_utc") or row.get("created_at"))
    score = as_number(row.get("score", row.get("upvotes")))
    comments = as_number(row.get("comments", row.get("num_comments")))
    return {
        "id": stable_id("reddit", url, title),
        "source": "reddit",
        "title": title,
        "summary": str(row.get("selftext") or "").strip()[:500],
        "url": url,
        "published_at": iso_z(published) if published else None,
        "author": author_text(row.get("author")),
        "channel": str(row.get("subreddit") or ""),
        "metrics": {"score": score, "comments": comments},
        "raw_engagement": math.log1p(max(score, 0)) + 0.7 * math.log1p(max(comments, 0)),
    }


def normalize_x(row: dict[str, Any]) -> dict[str, Any] | None:
    text = str(row.get("text") or row.get("title") or "").strip()
    if not text:
        return None
    url = canonical_url(row.get("url"))
    published = parse_datetime(row.get("created_at") or row.get("published_at"))
    likes = as_number(row.get("likes", row.get("favorite_count")))
    retweets = as_number(row.get("retweets", row.get("retweet_count")))
    replies = as_number(row.get("replies", row.get("reply_count")))
    views = as_number(row.get("views", row.get("view_count")))
    author = author_text(row.get("author") or row.get("username"))
    return {
        "id": stable_id("x", url, text),
        "source": "x",
        "title": re.sub(r"\s+", " ", text)[:220],
        "summary": "",
        "url": url,
        "published_at": iso_z(published) if published else None,
        "author": author,
        "channel": f"@{author.lstrip('@')}" if author else "",
        "metrics": {"likes": likes, "retweets": retweets, "replies": replies, "views": views},
        "raw_engagement": (
            math.log1p(max(likes, 0))
            + 1.5 * math.log1p(max(retweets, 0))
            + 1.2 * math.log1p(max(replies, 0))
            + 0.2 * math.log1p(max(views, 0))
        ),
    }


def normalize_github(row: dict[str, Any]) -> dict[str, Any] | None:
    name = str(row.get("full_name") or row.get("name") or "").strip()
    if not name:
        return None
    description = str(row.get("description") or "").strip()
    title = f"{name}: {description}" if description else name
    url = canonical_url(row.get("html_url") or row.get("url"))
    published = parse_datetime(row.get("created_at") or row.get("pushed_at") or row.get("updated_at"))
    stars = as_number(row.get("stargazers_count", row.get("stars")))
    forks = as_number(row.get("forks_count", row.get("forks")))
    return {
        "id": stable_id("github", url, title),
        "source": "github",
        "title": title[:300],
        "summary": description[:500],
        "url": url,
        "published_at": iso_z(published) if published else None,
        "author": str((row.get("owner") or {}).get("login") if isinstance(row.get("owner"), dict) else ""),
        "channel": str(row.get("language") or ""),
        "metrics": {"stars": stars, "forks": forks, "open_issues": as_number(row.get("open_issues_count"))},
        "raw_engagement": math.log1p(max(stars, 0)) + 1.5 * math.log1p(max(forks, 0)),
    }


def normalize_rows(source: str, rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    function = {"reddit": normalize_reddit, "x": normalize_x, "github": normalize_github}[source]
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        item = function(row)
        if not item or item["id"] in seen:
            continue
        seen.add(item["id"])
        output.append(item)
    return output


def within_window(item: dict[str, Any], cutoff: datetime, include_older: bool) -> bool:
    if include_older or not item.get("published_at"):
        return True
    published = parse_datetime(item["published_at"])
    return published is None or published >= cutoff


def score_items(items: list[dict[str, Any]], config: dict[str, Any], now: datetime) -> None:
    weights = config["ranking"].get("source_weights", {})
    window_hours = float(config["window_hours"])
    by_source = {source: [item for item in items if item["source"] == source] for source in SOURCE_NAMES}
    for source, source_items in by_source.items():
        ordered = sorted(source_items, key=lambda item: (item["raw_engagement"], item["id"]))
        count = len(ordered)
        percentiles = {item["id"]: (index + 1) / max(count, 1) for index, item in enumerate(ordered)}
        for item in source_items:
            published = parse_datetime(item.get("published_at"))
            age_hours = max(0.0, (now - published).total_seconds() / 3600) if published else window_hours
            recency = math.exp(-age_hours / max(window_hours, 1.0))
            base = 65.0 * percentiles[item["id"]] + 35.0 * recency
            item["score"] = round(max(0.0, min(100.0, base * float(weights.get(source, 1.0)))), 2)
            item["age_hours"] = round(age_hours, 2) if published else None


def title_tokens(title: str) -> set[str]:
    return {
        token.lower().strip(".-")
        for token in WORD_RE.findall(title)
        if len(token) > 2 and token.lower() not in STOP_WORDS
    }


def similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    overlap = len(left & right)
    jaccard = overlap / len(left | right)
    containment = overlap / min(len(left), len(right))
    return max(jaccard, containment)


def cluster_items(items: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    threshold = float(config["ranking"].get("cluster_similarity", 0.52))
    bonus = float(config["ranking"].get("cross_source_bonus", 8.0))
    clusters: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda value: (-value["score"], value["id"])):
        tokens = title_tokens(item["title"])
        match = None
        for cluster in clusters:
            same_url = bool(item["url"] and item["url"] in cluster["urls"])
            lexical = similarity(tokens, cluster["tokens"])
            if same_url or (len(tokens & cluster["tokens"]) >= 2 and lexical >= threshold):
                match = cluster
                break
        if match is None:
            clusters.append({"items": [item], "tokens": set(tokens), "urls": {item["url"]} if item["url"] else set()})
        else:
            match["items"].append(item)
            match["tokens"].update(tokens)
            if item["url"]:
                match["urls"].add(item["url"])

    topics: list[dict[str, Any]] = []
    for cluster in clusters:
        cluster_items_list = sorted(cluster["items"], key=lambda value: (-value["score"], value["id"]))
        sources = sorted({item["source"] for item in cluster_items_list})
        score = min(100.0, cluster_items_list[0]["score"] + bonus * (len(sources) - 1))
        topic_basis = "\0".join(item["id"] for item in cluster_items_list)
        topics.append({
            "id": f"topic-{hashlib.sha256(topic_basis.encode('utf-8')).hexdigest()[:12]}",
            "title": cluster_items_list[0]["title"],
            "score": round(score, 2),
            "sources": sources,
            "cross_source": len(sources) > 1,
            "items": cluster_items_list,
        })
    topics.sort(key=lambda value: (-value["score"], value["id"]))
    limit = int(config["ranking"].get("report_topic_count", 15))
    return topics[:limit]


def chunks(values: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(values), max(1, size)):
        yield values[index:index + max(1, size)]


def collect_live(config: dict[str, Any], output_root: Path, since: datetime, use_cache: bool) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    timeout = int(config.get("request_timeout_seconds", 45))
    retries = int(config.get("request_retries", 1))
    cache_hours = float(config.get("cache_max_age_hours", 24))
    rows: dict[str, list[dict[str, Any]]] = {source: [] for source in SOURCE_NAMES}
    source_runs: list[dict[str, Any]] = []

    reddit = config["reddit"]
    if reddit.get("enabled"):
        for subreddit in reddit["subreddits"]:
            request_id = f"reddit-subreddit-{subreddit}"
            command = [
                "opencli", "reddit", "subreddit", str(subreddit),
                "--sort", str(reddit.get("sort", "hot")),
                "--time", str(reddit.get("time", "week")),
                "--limit", str(int(reddit.get("limit_per_subreddit", 20))),
                "--window", "background", "--site-session", "persistent", "-f", "json",
            ]
            result_rows, record = fetch_request("reddit", request_id, command, output_root, timeout, retries, cache_hours, use_cache)
            rows["reddit"].extend(result_rows)
            source_runs.append(record)

    x_config = config["x"]
    if x_config.get("enabled"):
        accounts = [str(value).lstrip("@") for value in x_config["accounts"]]
        for index, batch in enumerate(chunks(accounts, int(x_config.get("batch_size", 3))), start=1):
            query = "(" + " OR ".join(f"from:{account}" for account in batch) + ")"
            query += f" since:{since.date().isoformat()}"
            if x_config.get("exclude_replies", True):
                query += " -filter:replies"
            if x_config.get("exclude_retweets", True):
                query += " -filter:nativeretweets"
            request_id = f"x-watchlist-batch-{index}-{'-'.join(batch)}"
            command = [
                "opencli", "twitter", "search", query,
                "--product", "live",
                "--limit", str(int(x_config.get("limit_per_batch", 30))),
                "--top-by-engagement", str(int(x_config.get("top_by_engagement", 20))),
                "--window", "background", "--site-session", "persistent", "-f", "json",
            ]
            result_rows, record = fetch_request("x", request_id, command, output_root, timeout, retries, cache_hours, use_cache)
            rows["x"].extend(result_rows)
            source_runs.append(record)

    github = config["github"]
    if github.get("enabled"):
        since_date = since.date().isoformat()
        for index, query_template in enumerate(github["queries"], start=1):
            query = str(query_template).format(since_date=since_date)
            request_id = f"github-query-{index}-{query}"
            command = [
                "gh", "api", "-X", "GET", "search/repositories",
                "-f", f"q={query}", "-f", "sort=stars", "-f", "order=desc",
                "-F", f"per_page={int(github.get('limit_per_query', 20))}",
            ]
            result_rows, record = fetch_request("github", request_id, command, output_root, timeout, retries, cache_hours, use_cache)
            rows["github"].extend(result_rows)
            source_runs.append(record)
    return rows, source_runs


def collect_fixtures(fixture_dir: Path, config: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    rows: dict[str, list[dict[str, Any]]] = {source: [] for source in SOURCE_NAMES}
    source_runs: list[dict[str, Any]] = []
    now = iso_z(utc_now())
    for source in SOURCE_NAMES:
        if not config[source].get("enabled"):
            continue
        path = fixture_dir / f"{source}.json"
        record = {
            "source": source,
            "request_id": f"fixture-{source}",
            "status": "failed",
            "fetched_at": now,
            "attempts": 1,
            "duration_seconds": 0.0,
            "item_count": 0,
        }
        try:
            rows[source] = extract_rows(load_json(path))
            record.update({"status": "fresh", "item_count": len(rows[source])})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            record["error"] = safe_error(str(exc))
        source_runs.append(record)
    return rows, source_runs


def source_summary(source_runs: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for source in SOURCE_NAMES:
        records = [record for record in source_runs if record["source"] == source]
        counts = {state: sum(record["status"] == state for record in records) for state in ("fresh", "cached", "failed")}
        if not records:
            state = "disabled"
        elif counts["failed"]:
            state = "failed" if counts["fresh"] + counts["cached"] == 0 else "partial"
        elif counts["cached"]:
            state = "cached" if counts["fresh"] == 0 else "partial"
        else:
            state = "fresh"
        summary[source] = {"status": state, "requests": len(records), **counts}
    return summary


def build_health(source_runs: list[dict[str, Any]], item_count: int) -> dict[str, Any]:
    fresh = sum(record["status"] == "fresh" for record in source_runs)
    cached = sum(record["status"] == "cached" for record in source_runs)
    failed = sum(record["status"] == "failed" for record in source_runs)
    if item_count == 0:
        status = "failed"
    elif cached or failed:
        status = "partial"
    else:
        status = "complete"
    return {
        "status": status,
        "item_count": item_count,
        "requests": {"total": len(source_runs), "fresh": fresh, "cached": cached, "failed": failed},
        "sources": source_summary(source_runs),
    }


def compact_title(value: str, limit: int = 150) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build_drafts(topics: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    draft_config = config["drafts"]
    max_chars = int(draft_config.get("max_characters", 280))
    language = str(draft_config.get("language", "zh-CN"))
    output = []
    for index, topic in enumerate(topics[: int(draft_config.get("count", 5))], start=1):
        lead = next((item for item in topic["items"] if item.get("url")), topic["items"][0])
        url = lead.get("url", "")
        source_label = "、".join(SOURCE_ZH.get(source, source) for source in topic["sources"])
        if language.lower().startswith("zh"):
            prefix = (
                f"AI 热点观察 #{index}：检测到来自 {source_label} 的共同信号，综合热度 {topic['score']:.0f}。"
                "建议先核对原始信息，再判断它解决了什么问题、影响哪些用户，以及是否具备可复现证据。"
            )
        else:
            raise ValueError("draft language must be zh-CN")
        separator = "\n" if url else ""
        allowed_prefix = max_chars - len(separator) - len(url)
        if len(prefix) > allowed_prefix:
            prefix = prefix[: max(0, allowed_prefix - 1)].rstrip() + "…"
        text = prefix + separator + url
        sources = [
            {"source": item["source"], "url": item["url"], "title": compact_title(item["title"], 180)}
            for item in topic["items"] if item.get("url")
        ]
        output.append({
            "id": f"draft-{index:02d}",
            "topic_id": topic["id"],
            "text": text,
            "character_count": len(text),
            "sources": sources,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "language": language,
        "max_characters": max_chars,
        "drafts": output,
    }


def metric_text(item: dict[str, Any]) -> str:
    metrics = item.get("metrics", {})
    parts = []
    for key, value in metrics.items():
        if value:
            label = METRIC_ZH.get(key, key)
            parts.append(f"{label}={int(value) if float(value).is_integer() else value}")
    return "，".join(parts) or "暂无互动指标"


def md_escape(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def render_report(report: dict[str, Any]) -> str:
    run = report["run"]
    health = report["health"]
    lines = [
        "# AI 趋势采集报告",
        "",
        f"- 运行编号：`{run['id']}`",
        f"- 生成时间：`{run['generated_at']}`",
        f"- 采集时间窗：`{run['window_start']}` → `{run['window_end']}`",
        f"- 整体状态：**{STATUS_ZH.get(health['status'], health['status'])}**",
        f"- 标准化信号数：**{health['item_count']}**",
        "",
        "## 来源状态",
        "",
        "| 来源 | 状态 | 请求数 | 最新 | 缓存 | 失败 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for source in SOURCE_NAMES:
        info = health["sources"][source]
        source_label = SOURCE_ZH.get(source, source)
        status_label = STATUS_ZH.get(info["status"], info["status"])
        lines.append(f"| {source_label} | {status_label} | {info['requests']} | {info['fresh']} | {info['cached']} | {info['failed']} |")
    lines.extend(["", "## 热点话题", ""])
    if not report["topics"]:
        lines.append("未采集到有依据的话题。请检查来源状态和请求诊断，不要生成无来源草稿。")
    for index, topic in enumerate(report["topics"], start=1):
        lines.extend([
            f"### {index}. 热点主题",
            "",
            f"综合热度：**{topic['score']}** · 来源：`{'、'.join(SOURCE_ZH.get(source, source) for source in topic['sources'])}` · 跨来源印证：`{'是' if topic['cross_source'] else '否'}`",
            "",
            f"原始主标题：{md_escape(topic['title'])}",
            "",
        ])
        for item in topic["items"]:
            label = md_escape(compact_title(item["title"], 170))
            link = f"[{label}]({item['url']})" if item.get("url") else label
            source_label = SOURCE_ZH.get(item["source"], item["source"])
            lines.append(f"- **{source_label}** · {link} · {metric_text(item)} · {item.get('published_at') or '时间未知'}")
        lines.append("")
    lines.extend(["## 采集诊断", ""])
    for record in report["source_runs"]:
        detail = f" · 缓存年龄 {record['cache_age_hours']} 小时" if "cache_age_hours" in record else ""
        error = f" · `{md_escape(record['error'])}`" if record.get("error") else ""
        source_label = SOURCE_ZH.get(record["source"], record["source"])
        status_label = STATUS_ZH.get(record["status"], record["status"])
        lines.append(f"- `{source_label}` / `{md_escape(record['request_id'])}`：**{status_label}**（{record['item_count']} 条）{detail}{error}")
    lines.extend(["", "## 局限说明", ""])
    lines.extend(f"- {value}" for value in report["limitations"])
    lines.extend(["", "X 草稿位于 `x-drafts.md`。修改结构化草稿后，请使用 `validate_x_drafts.py` 校验。", ""])
    return "\n".join(lines)


def render_drafts(payload: dict[str, Any]) -> str:
    lines = [
        "# X 话题草稿",
        "",
        f"语言：`简体中文` · 单条上限：`{payload['max_characters']}` 字",
        "",
        "草稿基于采集标题和原始链接自动生成。发布前应人工复核原文、语气和时效。",
        "",
    ]
    for index, draft in enumerate(payload["drafts"], start=1):
        lines.extend([
            f"## 草稿 {index:02d}（{draft['character_count']} 字）",
            "",
            "```text",
            draft["text"],
            "```",
            "",
            "证据来源：",
            "",
        ])
        lines.extend(
            f"- [{SOURCE_ZH.get(source['source'], source['source'])}：{md_escape(source['title'])}]({source['url']})"
            for source in draft["sources"]
        )
        lines.append("")
    return "\n".join(lines)


def yaml_quote(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def render_obsidian_note(
    report: dict[str, Any],
    drafts: dict[str, Any],
    local_now: datetime,
) -> str:
    run = report["run"]
    health = report["health"]
    sources = sorted({
        item["url"]
        for source_items in report["items"].values()
        for item in source_items
        if item.get("url")
    })
    title = f"AI 趋势采集报告 {local_now.strftime('%Y-%m-%d %H:%M')}"
    lines = [
        "---",
        f"title: {yaml_quote(title)}",
        f"created: {local_now.date().isoformat()}",
        f"updated: {local_now.date().isoformat()}",
        "type: summary",
        "tags:",
        "  - ai",
        "  - monitoring",
        "  - news",
        "  - x",
        "  - github",
    ]
    if sources:
        lines.append("sources:")
        lines.extend(f"  - {yaml_quote(url)}" for url in sources)
    else:
        lines.append("sources: []")
    lines.extend([
        f"run_id: {yaml_quote(run['id'])}",
        f"health: {health['status']}",
        f"window_start: {yaml_quote(run['window_start'])}",
        f"window_end: {yaml_quote(run['window_end'])}",
        f"signal_count: {health['item_count']}",
        f"topic_count: {len(report['topics'])}",
        f"draft_count: {len(drafts['drafts'])}",
        "---",
        "",
        "[[concepts/news-monitoring-and-growth]]",
        "",
        f"# {title}",
        "",
        "> [!info] 采集概况",
        f"> - 运行编号：`{run['id']}`",
        f"> - 状态：**{STATUS_ZH.get(health['status'], health['status'])}**",
        f"> - 时间窗：`{run['window_start']}` → `{run['window_end']}`",
        f"> - 信号 / 热点 / 草稿：**{health['item_count']} / {len(report['topics'])} / {len(drafts['drafts'])}**",
    ])
    if health["status"] != "complete":
        lines.extend([
            ">",
            "> [!warning] 数据不完整",
            "> 本次结果包含失败或缓存来源，请结合下方采集诊断审阅，不要把缺失来源解释为没有讨论。",
        ])

    lines.extend(["", "## 热点话题与跨来源证据", ""])
    if not report["topics"]:
        lines.append("未采集到有依据的话题。本页仅保留来源状态和失败诊断。")
    for index, topic in enumerate(report["topics"], start=1):
        lines.extend([
            f"### {index}. 热点主题",
            "",
            f"综合热度：**{topic['score']}** · 来源：`{'、'.join(SOURCE_ZH.get(source, source) for source in topic['sources'])}` · 跨来源印证：`{'是' if topic['cross_source'] else '否'}`",
            "",
            f"原始主标题：{md_escape(topic['title'])}",
            "",
        ])
        for item in topic["items"]:
            label = md_escape(compact_title(item["title"], 170))
            link = f"[{label}]({item['url']})" if item.get("url") else label
            source_label = SOURCE_ZH.get(item["source"], item["source"])
            lines.append(f"- **{source_label}** · {link} · {metric_text(item)} · {item.get('published_at') or '时间未知'}")
        lines.append("")

    lines.extend([
        "## 中文 X 草稿",
        "",
        "以下内容仅为待人工复核的中文草稿，不会自动发布到 X。",
        "",
    ])
    if not drafts["drafts"]:
        lines.append("没有足够证据生成草稿。")
        lines.append("")
    for index, draft in enumerate(drafts["drafts"], start=1):
        lines.extend([
            f"### 草稿 {index:02d}（{draft['character_count']} 字）",
            "",
            "```text",
            draft["text"],
            "```",
            "",
            "证据来源：",
            "",
        ])
        lines.extend(
            f"- [{SOURCE_ZH.get(source['source'], source['source'])}：{md_escape(source['title'])}]({source['url']})"
            for source in draft["sources"]
        )
        lines.append("")

    lines.extend(["## 采集诊断与局限说明", "", "### 来源状态", ""])
    lines.extend([
        "| 来源 | 状态 | 请求数 | 最新 | 缓存 | 失败 |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ])
    for source in SOURCE_NAMES:
        info = health["sources"][source]
        lines.append(
            f"| {SOURCE_ZH.get(source, source)} | {STATUS_ZH.get(info['status'], info['status'])} | "
            f"{info['requests']} | {info['fresh']} | {info['cached']} | {info['failed']} |"
        )
    lines.extend(["", "### 请求诊断", ""])
    for record in report["source_runs"]:
        detail = f" · 缓存年龄 {record['cache_age_hours']} 小时" if "cache_age_hours" in record else ""
        error = f" · `{md_escape(record['error'])}`" if record.get("error") else ""
        lines.append(
            f"- `{SOURCE_ZH.get(record['source'], record['source'])}` / `{md_escape(record['request_id'])}`："
            f"**{STATUS_ZH.get(record['status'], record['status'])}**（{record['item_count']} 条）{detail}{error}"
        )
    lines.extend(["", "### 局限说明", ""])
    lines.extend(f"- {value}" for value in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def build_obsidian_artifacts(
    config: dict[str, Any],
    report: dict[str, Any],
    drafts: dict[str, Any],
    run_dir: Path,
    now: datetime,
) -> tuple[str, dict[str, Any]]:
    obsidian = config["obsidian"]
    local_now = now.astimezone(ZoneInfo(str(config.get("timezone", "Asia/Shanghai"))))
    filename = f"trend-{local_now.strftime('%Y-%m-%d-%H%M%S')}.md"
    target_directory = validate_relative_vault_path(
        obsidian["target_directory"], "config.obsidian.target_directory", directory=True
    )
    note_path = f"{target_directory}/{filename}"
    wikilink = f"[[{note_path.removesuffix('.md')}]]"
    note = render_obsidian_note(report, drafts, local_now)
    signal_count = int(report["health"]["item_count"])
    topic_count = len(report["topics"])
    draft_count = len(drafts["drafts"])
    run_id = report["run"]["id"]
    index_entry = (
        f"- {wikilink} — AI 趋势采集：{topic_count} 个热点，包含 Reddit、GitHub、X，并附 X 草稿。"
    )
    log_marker = f"ai-trend-run:{run_id}"
    log_entry = (
        f"- {local_now.strftime('%Y-%m-%d %H:%M:%S')} | run_id={run_id} | path={wikilink} | "
        f"signals={signal_count} | topics={topic_count} | drafts={draft_count} | status=published "
        f"<!-- {log_marker} -->"
    )
    plan = {
        "schema_version": 1,
        "run_id": run_id,
        "vault": validate_vault_name(obsidian["vault"]),
        "target_directory": target_directory,
        "note_path": note_path,
        "note_wikilink": wikilink,
        "note_file": str((run_dir / "obsidian-note.md").resolve()),
        "note_sha256": hashlib.sha256(note.encode("utf-8")).hexdigest(),
        "index_path": validate_relative_vault_path(obsidian["index_path"], "config.obsidian.index_path"),
        "log_path": validate_relative_vault_path(obsidian["log_path"], "config.obsidian.log_path"),
        "strict": bool(obsidian["strict"]),
        "created_date": local_now.date().isoformat(),
        "signal_count": signal_count,
        "topic_count": topic_count,
        "draft_count": draft_count,
        "index_entry": index_entry,
        "log_entry": log_entry,
        "log_marker": log_marker,
        "required_note_markers": [
            f"run_id: {yaml_quote(run_id)}",
            "[[concepts/news-monitoring-and-growth]]",
            "## 热点话题与跨来源证据",
            "## 中文 X 草稿",
            "## 采集诊断与局限说明",
        ],
        "result_file": str((run_dir / "obsidian-publish-result.json").resolve()),
    }
    return note, plan


def command_check(
    command: list[str],
    timeout: int = 20,
    failure_markers: tuple[str, ...] = (),
) -> dict[str, Any]:
    binary = command[0]
    if shutil.which(binary) is None:
        return {"command": " ".join(command), "status": "missing", "detail": f"{binary} not found"}
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        detail = safe_error(result.stdout or result.stderr)
        marker_failed = any(marker in detail for marker in failure_markers)
        status = "ok" if result.returncode == 0 and not marker_failed else "failed"
        return {"command": " ".join(command), "status": status, "detail": detail}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": " ".join(command), "status": "failed", "detail": safe_error(str(exc))}


def run_preflight() -> int:
    checks = [
        command_check(["opencli", "--version"]),
        command_check(["opencli", "doctor"], timeout=30, failure_markers=("[FAIL]", "[MISSING]")),
        command_check(["gh", "--version"]),
        command_check(["gh", "auth", "status"]),
    ]
    payload = {"status": "ok" if all(check["status"] == "ok" for check in checks) else "failed", "checks": checks}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "ok" else 2


def copy_latest(
    output_root: Path,
    run_dir: Path,
    report: dict[str, Any],
    drafts: dict[str, Any],
    obsidian_plan: dict[str, Any],
) -> None:
    mapping = {
        "latest-report.json": json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        "latest-report.md": (run_dir / "report.md").read_text(encoding="utf-8"),
        "latest-drafts.json": json.dumps(drafts, ensure_ascii=False, indent=2) + "\n",
        "latest-x-drafts.md": (run_dir / "x-drafts.md").read_text(encoding="utf-8"),
        "latest-obsidian-note.md": (run_dir / "obsidian-note.md").read_text(encoding="utf-8"),
        "latest-obsidian-publish.json": json.dumps(obsidian_plan, ensure_ascii=False, indent=2) + "\n",
    }
    for name, content in mapping.items():
        atomic_write(output_root / name, content)
    write_json(output_root / "latest.json", {
        "schema_version": SCHEMA_VERSION,
        "run_id": report["run"]["id"],
        "run_dir": str(run_dir.resolve()),
        "generated_at": report["run"]["generated_at"],
        "health": report["health"],
        "obsidian": {
            "vault": obsidian_plan["vault"],
            "note_path": obsidian_plan["note_path"],
            "publish_plan": str((run_dir / "obsidian-publish.json").resolve()),
        },
    })


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_config_path(), help="JSON configuration path")
    parser.add_argument("--output-dir", type=Path, default=Path("ai-trend-output"), help="Output root")
    parser.add_argument("--fixture-dir", type=Path, help="Read reddit.json, x.json, and github.json instead of calling CLIs")
    parser.add_argument("--run-id", help="Deterministic run id in YYYYMMDDTHHMMSSZ[-suffix] form")
    parser.add_argument("--no-cache", action="store_true", help="Disable cache reads and writes")
    parser.add_argument("--strict", action="store_true", help="Return nonzero unless health is complete")
    parser.add_argument("--obsidian-vault", help="Override config.obsidian.vault with a Vault name")
    parser.add_argument("--obsidian-dir", help="Override config.obsidian.target_directory with a relative path")
    parser.add_argument("--preflight", action="store_true", help="Check opencli bridge and gh authentication, then exit")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.preflight:
        return run_preflight()
    try:
        config = load_json(args.config.resolve())
        if args.obsidian_vault:
            config.setdefault("obsidian", {})["vault"] = args.obsidian_vault
        if args.obsidian_dir:
            config.setdefault("obsidian", {})["target_directory"] = args.obsidian_dir
        validate_config(config)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        print(f"configuration error: {safe_error(str(exc))}", file=sys.stderr)
        return 2

    now = utc_now()
    since = now - timedelta(hours=float(config["window_hours"]))
    run_id = args.run_id or now.strftime("%Y%m%dT%H%M%SZ")
    if not RUN_ID_RE.fullmatch(run_id):
        print("run-id must match YYYYMMDDTHHMMSSZ[-suffix]", file=sys.stderr)
        return 2
    output_root = args.output_dir.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / run_id
    if run_dir.exists():
        print(f"run directory already exists: {run_dir}", file=sys.stderr)
        return 2
    temp_dir = output_root / f".tmp-{run_id}-{os.getpid()}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    try:
        if args.fixture_dir:
            raw_rows, source_runs = collect_fixtures(args.fixture_dir.resolve(), config)
        else:
            raw_rows, source_runs = collect_live(config, output_root, since, not args.no_cache)
        normalized: dict[str, list[dict[str, Any]]] = {}
        all_items: list[dict[str, Any]] = []
        for source in SOURCE_NAMES:
            source_items = normalize_rows(source, raw_rows[source])
            source_items = [item for item in source_items if within_window(item, since, bool(config.get("include_older_items")))]
            normalized[source] = source_items
            all_items.extend(source_items)
        score_items(all_items, config, now)
        for source in SOURCE_NAMES:
            normalized[source].sort(key=lambda item: (-item["score"], item["id"]))
        topics = cluster_items(all_items, config)
        health = build_health(source_runs, len(all_items))
        limitations = [
            "GitHub 没有公开的官方 Trending API，因此相关结果是基于仓库搜索构建的趋势代理指标。",
            "Reddit 和 X 数据依赖用户现有的 opencli 浏览器会话，以及网站当时可见的结果。",
            "词法聚类可能遗漏相关话题，也可能合并措辞相似但实际不同的讨论；必须核对原始链接。",
            "自动生成的 X 草稿是带来源的编辑起点，不代表已经独立核实全部事实。",
        ]
        report = {
            "schema_version": SCHEMA_VERSION,
            "run": {
                "id": run_id,
                "generated_at": iso_z(now),
                "window_start": iso_z(since),
                "window_end": iso_z(now),
                "config_path": str(args.config.resolve()),
                "fixture_mode": bool(args.fixture_dir),
                "document_language": "zh-CN",
            },
            "health": health,
            "source_runs": source_runs,
            "topics": topics,
            "items": normalized,
            "limitations": limitations,
        }
        drafts = build_drafts(topics, config)
        obsidian_note, obsidian_plan = build_obsidian_artifacts(config, report, drafts, run_dir, now)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "generated_at": iso_z(now),
            "health_status": health["status"],
            "files": [
                "report.json", "report.md", "drafts.json", "x-drafts.md",
                "obsidian-note.md", "obsidian-publish.json",
                "raw/reddit.json", "raw/x.json", "raw/github.json",
            ],
        }
        (temp_dir / "raw").mkdir(parents=True, exist_ok=False)
        for source in SOURCE_NAMES:
            write_json(temp_dir / "raw" / f"{source}.json", raw_rows[source])
        write_json(temp_dir / "manifest.json", manifest)
        write_json(temp_dir / "report.json", report)
        atomic_write(temp_dir / "report.md", render_report(report))
        write_json(temp_dir / "drafts.json", drafts)
        atomic_write(temp_dir / "x-drafts.md", render_drafts(drafts))
        atomic_write(temp_dir / "obsidian-note.md", obsidian_note)
        write_json(temp_dir / "obsidian-publish.json", obsidian_plan)
        os.replace(temp_dir, run_dir)
        copy_latest(output_root, run_dir, report, drafts, obsidian_plan)
    except Exception as exc:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        print(f"collection failed: {safe_error(str(exc))}", file=sys.stderr)
        return 2

    result = {
        "run_id": run_id,
        "health": health["status"],
        "report": str((run_dir / "report.md").resolve()),
        "drafts": str((run_dir / "x-drafts.md").resolve()),
        "obsidian_note": str((run_dir / "obsidian-note.md").resolve()),
        "obsidian_publish_plan": str((run_dir / "obsidian-publish.json").resolve()),
        "obsidian_target": f"{obsidian_plan['vault']}:{obsidian_plan['note_path']}",
    }
    print(json.dumps(result, ensure_ascii=False))
    if args.strict and health["status"] != "complete":
        return 3
    return 2 if health["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
