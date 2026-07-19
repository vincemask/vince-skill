#!/usr/bin/env python3
"""Collect, normalize, and rank GitHub AI trend signals with bounded failure modes."""

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
SOURCE_NAMES = ("github",)
EXPECTED_SOURCE = "github"
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TOKEN_RE = re.compile(
    r"(?i)(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|bearer\s+[A-Za-z0-9._~+/=-]{16,})"
)
WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#.-]{1,}", re.IGNORECASE)
RUN_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z(?:-[a-z0-9-]+)?$")
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


def validate_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != 1:
        raise ValueError("config.schema_version must be 1")
    if config.get("document_language", "zh-CN") != "zh-CN":
        raise ValueError("config.document_language must be zh-CN")
    if not isinstance(config.get("window_hours"), (int, float)) or config["window_hours"] <= 0:
        raise ValueError("config.window_hours must be positive")
    for source in ("reddit", "x", "github"):
        if not isinstance(config.get(source), dict):
            raise ValueError(f"config.{source} must be an object")
    enabled_sources = [source for source in ("reddit", "x", "github") if config[source].get("enabled")]
    if enabled_sources != [EXPECTED_SOURCE]:
        raise ValueError(f"this skill requires only config.{EXPECTED_SOURCE}.enabled=true")
    if config["reddit"].get("enabled") and not config["reddit"].get("subreddits"):
        raise ValueError("config.reddit.subreddits must not be empty when enabled")
    if config["x"].get("enabled") and not config["x"].get("accounts"):
        raise ValueError("config.x.accounts must not be empty when enabled")
    topic_queries = config["x"].get("topic_queries", [])
    if not isinstance(topic_queries, list) or any(not isinstance(query, str) or not query.strip() for query in topic_queries):
        raise ValueError("config.x.topic_queries must be a list of non-empty strings")
    if config["github"].get("enabled") and not config["github"].get("queries"):
        raise ValueError("config.github.queries must not be empty when enabled")
    for field in ("active_window_days", "emerging_window_days"):
        if float(config["github"].get(field, 0)) <= 0:
            raise ValueError(f"config.github.{field} must be positive")
    ranking = config.get("ranking", {})
    ranking_weights = [
        float(ranking.get("popularity_weight", 0.4)),
        float(ranking.get("momentum_weight", 0.5)),
        float(ranking.get("activity_weight", 0.1)),
    ]
    if any(weight < 0 for weight in ranking_weights) or not math.isclose(sum(ranking_weights), 1.0, abs_tol=1e-9):
        raise ValueError("config.ranking popularity/momentum/activity weights must be non-negative and sum to 1")
    if float(ranking.get("momentum_min_age_days", 7)) <= 0:
        raise ValueError("config.ranking.momentum_min_age_days must be positive")
    if float(ranking.get("activity_half_life_days", 45)) <= 0:
        raise ValueError("config.ranking.activity_half_life_days must be positive")
    drafts = config.get("drafts", {})
    if drafts.get("language") != "zh-CN":
        raise ValueError("config.drafts.language must be zh-CN")
    if int(config.get("ranking", {}).get("report_topic_count", 0)) != 10:
        raise ValueError("config.ranking.report_topic_count must be 10")
    if int(drafts.get("count", 0)) != 10:
        raise ValueError("config.drafts.count must be 10")
    if drafts.get("mode") != "long":
        raise ValueError("config.drafts.mode must be long")
    minimum = int(drafts.get("min_prose_characters", 0))
    maximum = int(drafts.get("max_prose_characters", 0))
    if not 1 <= minimum <= maximum <= 500:
        raise ValueError("config.drafts prose character limits are invalid")
    reason_minimum = int(drafts.get("min_recommendation_characters", 0))
    reason_maximum = int(drafts.get("max_recommendation_characters", 0))
    if not 1 <= reason_minimum <= reason_maximum <= 100:
        raise ValueError("config.drafts recommendation character limits are invalid")
    if int(drafts.get("max_hashtags", -1)) != 1:
        raise ValueError("config.drafts.max_hashtags must be 1")
    try:
        ZoneInfo(str(config.get("timezone", "Asia/Shanghai")))
    except ZoneInfoNotFoundError as exc:
        raise ValueError("config.timezone must name an installed IANA timezone") from exc


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
    created = parse_datetime(row.get("created_at"))
    pushed = parse_datetime(row.get("pushed_at") or row.get("updated_at"))
    published = pushed or created
    stars = as_number(row.get("stargazers_count", row.get("stars")))
    forks = as_number(row.get("forks_count", row.get("forks")))
    return {
        "id": stable_id("github", url, title),
        "source": "github",
        "title": title[:300],
        "summary": description[:500],
        "url": url,
        "published_at": iso_z(published) if published else None,
        "created_at": iso_z(created) if created else None,
        "pushed_at": iso_z(pushed) if pushed else None,
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


def percentile_ranks(items: list[dict[str, Any]], field: str) -> dict[str, float]:
    values = sorted(float(item.get(field, 0.0)) for item in items)
    count = len(values)
    return {
        item["id"]: sum(value <= float(item.get(field, 0.0)) for value in values) / max(count, 1)
        for item in items
    }


def score_items(items: list[dict[str, Any]], config: dict[str, Any], now: datetime) -> None:
    ranking = config["ranking"]
    source_weights = ranking.get("source_weights", {})
    popularity_weight = float(ranking.get("popularity_weight", 0.4))
    momentum_weight = float(ranking.get("momentum_weight", 0.5))
    activity_weight = float(ranking.get("activity_weight", 0.1))
    minimum_age_days = float(ranking.get("momentum_min_age_days", 7))
    activity_half_life_days = float(ranking.get("activity_half_life_days", 45))
    by_source = {source: [item for item in items if item["source"] == source] for source in SOURCE_NAMES}
    for source, source_items in by_source.items():
        for item in source_items:
            created = parse_datetime(item.get("created_at"))
            pushed = parse_datetime(item.get("pushed_at") or item.get("published_at"))
            age_days = max(minimum_age_days, (now - created).total_seconds() / 86400) if created else minimum_age_days
            push_age_days = max(0.0, (now - pushed).total_seconds() / 86400) if pushed else activity_half_life_days * 4
            stars = max(float(item.get("metrics", {}).get("stars", 0.0)), 0.0)
            forks = max(float(item.get("metrics", {}).get("forks", 0.0)), 0.0)
            stars_per_day = stars / age_days
            forks_per_day = forks / age_days
            item["raw_momentum"] = math.log1p(stars_per_day) + 1.5 * math.log1p(forks_per_day)
            item["age_days"] = round(age_days, 2)
            item["age_hours"] = round(push_age_days * 24, 2) if pushed else None
            item["metrics"]["stars_per_day_proxy"] = round(stars_per_day, 2)
            item["metrics"]["forks_per_day_proxy"] = round(forks_per_day, 2)
        popularity_percentiles = percentile_ranks(source_items, "raw_engagement")
        momentum_percentiles = percentile_ranks(source_items, "raw_momentum")
        for item in source_items:
            push_age_days = float(item["age_hours"]) / 24 if item.get("age_hours") is not None else activity_half_life_days * 4
            activity = math.pow(0.5, push_age_days / activity_half_life_days)
            popularity = popularity_percentiles[item["id"]]
            momentum = momentum_percentiles[item["id"]]
            base = 100.0 * (
                popularity_weight * popularity
                + momentum_weight * momentum
                + activity_weight * activity
            )
            item["score"] = round(max(0.0, min(100.0, base * float(source_weights.get(source, 1.0)))), 2)
            item["ranking_signals"] = {
                "popularity_percentile": round(popularity, 4),
                "momentum_percentile": round(momentum, 4),
                "activity_score": round(activity, 4),
                "momentum_basis": "lifetime-stars-and-forks-per-day-proxy",
            }


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
    topics = [topic for topic in topics if any(item.get("url") for item in topic["items"])]
    limit = int(config["ranking"].get("report_topic_count", 10))
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

        for index, topic_query in enumerate(x_config.get("topic_queries", []), start=1):
            query = f"({str(topic_query).strip()}) since:{since.date().isoformat()}"
            if x_config.get("exclude_replies", True):
                query += " -filter:replies"
            if x_config.get("exclude_retweets", True):
                query += " -filter:nativeretweets"
            request_id = f"x-topic-query-{index}-{topic_query}"
            command = [
                "opencli", "twitter", "search", query,
                "--product", "live",
                "--limit", str(int(x_config.get("limit_per_topic_query", x_config.get("limit_per_batch", 30)))),
                "--top-by-engagement", str(int(x_config.get("top_by_engagement", 20))),
                "--window", "background", "--site-session", "persistent", "-f", "json",
            ]
            result_rows, record = fetch_request("x", request_id, command, output_root, timeout, retries, cache_hours, use_cache)
            rows["x"].extend(result_rows)
            source_runs.append(record)

    github = config["github"]
    if github.get("enabled"):
        since_date = since.date().isoformat()
        active_since_date = (utc_now() - timedelta(days=float(github.get("active_window_days", 180)))).date().isoformat()
        emerging_since_date = (utc_now() - timedelta(days=float(github.get("emerging_window_days", 365)))).date().isoformat()
        for index, query_template in enumerate(github["queries"], start=1):
            query = str(query_template).format(
                since_date=since_date,
                active_since_date=active_since_date,
                emerging_since_date=emerging_since_date,
            )
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


def select_editorial_evidence(topic: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    for item in topic["items"]:
        if item["source"] in seen_sources or not item.get("url"):
            continue
        selected.append(item)
        seen_sources.add(item["source"])
        if len(selected) == limit:
            break
    if len(selected) < limit:
        selected_ids = {item["id"] for item in selected}
        for item in topic["items"]:
            if item["id"] in selected_ids or not item.get("url"):
                continue
            selected.append(item)
            if len(selected) == limit:
                break
    return selected


def build_editorial_input(
    report: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    topics = []
    for rank, topic in enumerate(report["topics"], start=1):
        evidence = select_editorial_evidence(topic)
        topics.append({
            "rank": rank,
            "topic_id": topic["id"],
            "original_title": topic["title"],
            "score": topic["score"],
            "sources": topic["sources"],
            "cross_source": topic["cross_source"],
            "evidence": [
                {
                    "source": item["source"],
                    "title": item["title"],
                    "summary": item.get("summary", ""),
                    "url": item["url"],
                    "published_at": item.get("published_at"),
                    "created_at": item.get("created_at"),
                    "pushed_at": item.get("pushed_at"),
                    "author": item.get("author", ""),
                    "channel": item.get("channel", ""),
                    "metrics": item.get("metrics", {}),
                    "ranking_signals": item.get("ranking_signals", {}),
                    "score": item.get("score"),
                }
                for item in evidence
            ],
        })
    drafts = config["drafts"]
    return {
        "schema_version": 1,
        "run_id": report["run"]["id"],
        "language": "zh-CN",
        "health": report["health"]["status"],
        "topic_limit": 10,
        "required_topic_count": len(topics),
        "post_policy": {
            "mode": drafts["mode"],
            "min_prose_characters": int(drafts["min_prose_characters"]),
            "max_prose_characters": int(drafts["max_prose_characters"]),
            "min_recommendation_characters": int(drafts["min_recommendation_characters"]),
            "max_recommendation_characters": int(drafts["max_recommendation_characters"]),
            "max_hashtags": int(drafts["max_hashtags"]),
            "primary_url_count": 1,
            "style": "professional-concise",
        },
        "topics": topics,
    }


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
        command_check(["gh", "--version"]),
        command_check(["gh", "auth", "status"]),
    ]
    payload = {"status": "ok" if all(check["status"] == "ok" for check in checks) else "failed", "checks": checks}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "ok" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=default_config_path(), help="JSON configuration path")
    parser.add_argument("--output-dir", type=Path, default=Path("github-ai-trend-output"), help="Output root")
    parser.add_argument("--fixture-dir", type=Path, help="Read github.json instead of calling gh")
    parser.add_argument("--run-id", help="Deterministic run id in YYYYMMDDTHHMMSSZ[-suffix] form")
    parser.add_argument("--no-cache", action="store_true", help="Disable cache reads and writes")
    parser.add_argument("--strict", action="store_true", help="Return nonzero unless health is complete")
    parser.add_argument("--preflight", action="store_true", help="Check gh CLI and authentication, then exit")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.preflight:
        return run_preflight()
    try:
        config = load_json(args.config.resolve())
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
            "增长动量由当前 stars/forks 按项目年龄折算，不是 GitHub 官方近期增星数据。",
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
                "channel": EXPECTED_SOURCE,
                "active_window_days": float(config["github"].get("active_window_days", 180)),
                "emerging_window_days": float(config["github"].get("emerging_window_days", 365)),
                "include_older_items": bool(config.get("include_older_items")),
            },
            "health": health,
            "source_runs": source_runs,
            "topics": topics,
            "items": normalized,
            "limitations": limitations,
        }
        editorial_input = build_editorial_input(report, config)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "generated_at": iso_z(now),
            "health_status": health["status"],
            "stage": "collected",
            "files": ["report.json", "editorial-input.json", "run-config.json", "raw/github.json"],
        }
        (temp_dir / "raw").mkdir(parents=True, exist_ok=False)
        for source in SOURCE_NAMES:
            write_json(temp_dir / "raw" / f"{source}.json", raw_rows[source])
        write_json(temp_dir / "manifest.json", manifest)
        write_json(temp_dir / "report.json", report)
        write_json(temp_dir / "editorial-input.json", editorial_input)
        write_json(temp_dir / "run-config.json", config)
        os.replace(temp_dir, run_dir)
        write_json(output_root / "latest-collection.json", {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "run_dir": str(run_dir.resolve()),
            "generated_at": report["run"]["generated_at"],
            "health": health,
            "editorial_input": str((run_dir / "editorial-input.json").resolve()),
            "editorial_output": str((run_dir / "editorial.json").resolve()),
        })
    except Exception as exc:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        print(f"collection failed: {safe_error(str(exc))}", file=sys.stderr)
        return 2

    result = {
        "run_id": run_id,
        "health": health["status"],
        "run_dir": str(run_dir.resolve()),
        "editorial_input": str((run_dir / "editorial-input.json").resolve()),
        "editorial_output": str((run_dir / "editorial.json").resolve()),
        "needs_editorial": True,
    }
    print(json.dumps(result, ensure_ascii=False))
    if args.strict and health["status"] != "complete":
        return 3
    return 2 if health["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
