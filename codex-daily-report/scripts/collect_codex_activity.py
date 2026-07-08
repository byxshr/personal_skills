#!/usr/bin/env python3
"""Collect Codex activity events for a daily report window."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


SECRET_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*)(['\"]?)[^,'\"\s}]+"),
    re.compile(r"(?i)(token\s*[:=]\s*)(['\"]?)[^,'\"\s}]+"),
    re.compile(r"(?i)(password\s*[:=]\s*)(['\"]?)[^,'\"\s}]+"),
    re.compile(r"(?i)(secret\s*[:=]\s*)(['\"]?)[^,'\"\s}]+"),
    re.compile(r"(?i)(app[_-]?id\s*[:=]\s*)(['\"]?)[^,'\"\s}]+"),
]
EXIT_CODE_RE = re.compile(r"Process exited with code (-?\d+)")
COMMIT_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$")
GIT_STATUS_FILE_RE = re.compile(r"^([ MADRCU?!]{2})\s+(.+)$")
DIFF_STAT_FILE_RE = re.compile(r"^\s*(.+?)\s+\|\s+\d+")
BRANCH_RE = re.compile(r"^(?:##\s+|On branch\s+)(.+)$")
RISK_LINE_RE = re.compile(
    r"(?i)\b(error|errors|failed|failure|traceback|exception|timeout|timed out|"
    r"no such file|cannot|denied)\b|错误|失败|异常|超时"
)
TEST_WORDS = ("passed", "failed", "skipped", "error", "errors", "xfailed", "xpassed", "deselected")
METRIC_LINE_RE = re.compile(
    r"(\b\d+(?:\.\d+)?%|\b\d+\s*/\s*\d+\b|"
    r"\b[A-Za-z_][\w.-]*\s*[=:]\s*-?\d+(?:\.\d+)?%?)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Report date in local timezone, YYYY-MM-DD")
    parser.add_argument("--tz", default="Asia/Singapore", help="Report timezone")
    parser.add_argument("--cutoff-hour", type=int, default=20, help="Local report cutoff hour, 0-23")
    parser.add_argument("--window-start-utc", help="Inclusive UTC start, ISO format")
    parser.add_argument("--window-end-utc", help="Exclusive UTC end, ISO format")
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def parse_dt(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_window(args: argparse.Namespace) -> tuple[datetime, datetime, str, str]:
    if args.window_start_utc and args.window_end_utc:
        start = parse_dt(args.window_start_utc)
        end = parse_dt(args.window_end_utc)
        return start, end, start.isoformat(), end.isoformat()

    if ZoneInfo is None:
        raise RuntimeError("zoneinfo is required for timezone-aware date windows")
    if not 0 <= args.cutoff_hour <= 23:
        raise ValueError("--cutoff-hour must be between 0 and 23")
    tz = ZoneInfo(args.tz)
    if args.date:
        report_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        report_date = datetime.now(tz).date()
    local_end = datetime.combine(report_date, time(args.cutoff_hour, 0), tzinfo=tz)
    local_start = local_end - timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc),
        local_end.astimezone(timezone.utc),
        local_start.isoformat(),
        local_end.isoformat(),
    )


def redact(text: str) -> str:
    out = text
    for pattern in SECRET_PATTERNS:
        out = pattern.sub(r"\1\2<redacted>", out)
    return out


def clip(text: str, limit: int = 600) -> str:
    text = redact(" ".join(str(text).split()))
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def unique(items: list[Any], limit: int = 50) -> list[Any]:
    seen = set()
    out = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= limit:
            break
    return out


def parse_json_line(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def event_timestamp(event: dict[str, Any]) -> datetime | None:
    ts = event.get("timestamp")
    if not ts:
        return None
    try:
        return parse_dt(str(ts))
    except Exception:
        return None


def text_from_message(payload: dict[str, Any]) -> str:
    parts = []
    for item in payload.get("content") or []:
        if isinstance(item, dict):
            parts.append(item.get("text") or item.get("input_text") or "")
    return clip(" ".join(parts), 900)


def command_category(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "")
    cmd = str(item.get("cmd") or "")
    args = str(item.get("arguments") or "")
    text = f"{name} {cmd} {args}".lower()
    if name == "apply_patch" or "*** begin patch" in text:
        return "edit"
    if name != "exec_command":
        return name or "function_call"
    if re.search(r"(^|\s)git\s+", cmd):
        return "git"
    if "pytest" in text or re.search(r"\b(go test|cargo test|npm test|pnpm test|yarn test)\b", text):
        return "test"
    if re.search(r"\b(py_compile|tsc\s+--noemit|mypy|ruff|eslint|bash -n|shellcheck)\b", text):
        return "static-check"
    if re.search(r"(^|\s)(ssh|scp|rsync)\b", cmd):
        return "remote"
    if re.search(r"(^|\s)(rg|sed|cat|ls|find|wc|nl|head|tail)\b", cmd):
        return "inspect"
    return "command"


def command_from_call(payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name") or payload.get("namespace") or "function_call"
    raw_args = payload.get("arguments")
    parsed: Any = None
    if isinstance(raw_args, str):
        try:
            parsed = json.loads(raw_args)
        except Exception:
            parsed = raw_args
    else:
        parsed = raw_args
    item: dict[str, Any] = {"name": name}
    if payload.get("call_id"):
        item["call_id"] = payload.get("call_id")
    if isinstance(parsed, dict):
        if "cmd" in parsed:
            item["cmd"] = clip(parsed.get("cmd"), 1200)
        if "workdir" in parsed:
            item["workdir"] = parsed.get("workdir")
        if name != "exec_command":
            item["arguments"] = clip(json.dumps(parsed, ensure_ascii=False), 1200)
    else:
        item["arguments"] = clip(str(parsed), 1200)
    item["category"] = command_category(item)
    return item


def output_body(output: str) -> str:
    marker = "\nOutput:\n"
    if marker in output:
        return output.split(marker, 1)[1]
    return output


def parse_exit_code(output: str) -> int | None:
    match = EXIT_CODE_RE.search(output)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def extract_test_results(text: str) -> list[str]:
    results: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        lower = line.lower()
        if not line or re.search(r"^(create|delete|rename) mode \d+\s+", lower):
            continue
        if any(word in lower for word in TEST_WORDS):
            parts = re.findall(
                r"\b\d+\s+(?:passed|failed|skipped|errors?|xfailed|xpassed|deselected)\b",
                line,
                flags=re.IGNORECASE,
            )
            if parts:
                results.append(", ".join(parts))
        elif re.search(r"\b\d+\s+tests?\s+(?:passed|failed|run|completed)\b", line, flags=re.IGNORECASE):
            results.append(clip(line, 180))
    return unique(results, 20)


def extract_changed_files(text: str) -> list[str]:
    files: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        patch_match = PATCH_FILE_RE.match(stripped)
        if patch_match:
            files.append(patch_match.group(1).strip())
            continue
        status_match = GIT_STATUS_FILE_RE.match(line)
        if status_match and not stripped.startswith(("Chunk ID:", "Wall time:", "Output:")):
            status = status_match.group(1)
            if not status.strip():
                continue
            path = status_match.group(2).strip()
            if " -> " in path:
                files.extend(part.strip() for part in path.split(" -> ") if part.strip())
            else:
                files.append(path)
            continue
        stat_match = DIFF_STAT_FILE_RE.match(line)
        if stat_match and " file changed" not in stripped and " files changed" not in stripped:
            files.append(stat_match.group(1).strip())
    return unique([item.strip("'\"") for item in files if item], 80)


def extract_commits(text: str) -> list[str]:
    return unique(COMMIT_RE.findall(text), 40)


def extract_branches(text: str) -> list[str]:
    branches: list[str] = []
    for line in text.splitlines():
        match = BRANCH_RE.match(line.strip())
        if match:
            branch = match.group(1).strip()
            if re.match(r"^[A-Z][A-Za-z ]+$", branch):
                continue
            branches.append(branch)
    return unique(branches, 20)


def extract_metrics(text: str) -> list[str]:
    metrics: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or len(stripped) > 500:
            continue
        if METRIC_LINE_RE.search(stripped):
            metrics.append(clip(stripped, 220))
    return unique(metrics, 30)


def extract_risk_signals(text: str, exit_code: int | None) -> list[str]:
    risks: list[str] = []
    if exit_code is not None and exit_code != 0:
        risks.append(f"nonzero exit code: {exit_code}")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if re.search(r"\b0\s+(failed|failures|errors?)\b", lower) and not re.search(
            r"\b[1-9]\d*\s+(failed|failures|errors?)\b", lower
        ):
            continue
        if RISK_LINE_RE.search(stripped):
            risks.append(clip(stripped, 220))
    return unique(risks, 20)


def command_result_from_output(payload: dict[str, Any], command: dict[str, Any] | None) -> dict[str, Any]:
    output = str(payload.get("output") or "")
    body = output_body(output)
    exit_code = parse_exit_code(output)
    category = command_category(command or {})
    cmd = str((command or {}).get("cmd") or "")
    item: dict[str, Any] = {
        "call_id": payload.get("call_id"),
        "name": (command or {}).get("name", "function_call_output"),
        "category": category,
    }
    if command:
        for key in ("cmd", "workdir", "arguments"):
            if key in command:
                item[key] = command[key]
    if exit_code is not None:
        item["exit_code"] = exit_code
    if body.strip():
        item["output_excerpt"] = clip(body, 1000)
    test_results = extract_test_results(body) if category in {"test", "static-check"} else []
    file_listing_git = (
        re.search(r"\bgit\s+status\b", cmd)
        or re.search(r"\bgit\s+diff\b", cmd)
        or re.search(r"\bgit\s+log\b", cmd)
        or (re.search(r"\bgit\s+show\b", cmd) and re.search(r"--stat|--name-status|--name-only", cmd))
    )
    changed_files = []
    if category == "edit" or (category == "git" and file_listing_git):
        changed_files = extract_changed_files(body + "\n" + str((command or {}).get("arguments") or ""))
    commits = extract_commits(body) if category in {"git", "edit"} else []
    branches = (
        extract_branches(body)
        if category == "git" and re.search(r"\bgit\s+(status|branch)\b", cmd)
        else []
    )
    metrics = extract_metrics(body)
    risks = extract_risk_signals(body, exit_code)
    if test_results:
        item["test_results"] = test_results
    if changed_files:
        item["changed_files"] = changed_files
    if commits:
        item["commits"] = commits
    if branches:
        item["branches"] = branches
    if metrics:
        item["metrics"] = metrics
    if risks:
        item["risk_signals"] = risks
    return item


def finalize_workspace(group: dict[str, Any]) -> dict[str, Any]:
    categories = Counter(item.get("category", "unknown") for item in group.get("commands", []))
    exit_codes = Counter(str(item.get("exit_code")) for item in group.get("command_results", []) if "exit_code" in item)
    changed_files: list[str] = []
    test_results: list[str] = []
    risk_signals: list[str] = []
    commits: list[str] = []
    branches: list[str] = []
    metrics: list[str] = []

    for result in group.get("command_results", []):
        changed_files.extend(result.get("changed_files") or [])
        test_results.extend(result.get("test_results") or [])
        risk_signals.extend(result.get("risk_signals") or [])
        commits.extend(result.get("commits") or [])
        branches.extend(result.get("branches") or [])
        metrics.extend(result.get("metrics") or [])

    group["changed_files"] = unique(changed_files, 120)
    group["test_results"] = unique(test_results, 50)
    group["risk_signals"] = unique(risk_signals, 50)
    group["evidence_summary"] = {
        "command_count": len(group.get("commands", [])),
        "command_result_count": len(group.get("command_results", [])),
        "command_categories": dict(sorted(categories.items())),
        "exit_codes": dict(sorted(exit_codes.items())),
        "commits": unique(commits, 40),
        "branches": unique(branches, 20),
        "metrics": unique(metrics, 40),
    }
    return group


def session_files(codex_home: Path) -> list[Path]:
    files: list[Path] = []
    for root in [codex_home / "sessions", codex_home / "archived_sessions"]:
        if root.exists():
            files.extend(sorted(root.glob("**/*.jsonl")))
    return files


def collect(codex_home: Path, start: datetime, end: datetime) -> dict[str, Any]:
    workspaces: dict[str, Any] = defaultdict(lambda: {
        "sessions": [],
        "user_messages": [],
        "task_completions": [],
        "commands": [],
        "command_results": [],
        "changed_files": [],
        "test_results": [],
        "risk_signals": [],
        "evidence_summary": {},
    })
    scanned = 0
    matched_sessions = 0

    for path in session_files(codex_home):
        scanned += 1
        cwd = None
        session_id = None
        events_in_window = 0
        first = None
        last = None
        session_users = []
        session_dones = []
        session_commands = []
        session_command_results = []
        calls_by_id: dict[str, dict[str, Any]] = {}

        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue

        for line in lines:
            event = parse_json_line(line)
            if not event:
                continue
            payload = event.get("payload") or {}
            if event.get("type") == "session_meta" and isinstance(payload, dict):
                cwd = payload.get("cwd") or cwd
                session_id = payload.get("session_id") or payload.get("id") or session_id
            dt = event_timestamp(event)
            in_window = dt is not None and start <= dt < end

            if event.get("type") == "response_item" and isinstance(payload, dict):
                if payload.get("type") == "function_call":
                    command = command_from_call(payload)
                    call_id = command.get("call_id")
                    if call_id:
                        calls_by_id[str(call_id)] = command
                    if in_window:
                        session_commands.append({"time": dt.isoformat(), **command})

            if not in_window:
                continue

            events_in_window += 1
            first = dt if first is None or dt < first else first
            last = dt if last is None or dt > last else last

            if event.get("type") == "response_item" and isinstance(payload, dict):
                if payload.get("type") == "message" and payload.get("role") == "user":
                    text = text_from_message(payload)
                    if text and not text.startswith("<environment_context>") and not text.startswith("# AGENTS.md instructions"):
                        session_users.append({"time": dt.isoformat(), "text": text})
                elif payload.get("type") == "function_call_output":
                    call_id = payload.get("call_id")
                    command = calls_by_id.get(str(call_id)) if call_id else None
                    result = command_result_from_output(payload, command)
                    session_command_results.append({"time": dt.isoformat(), **result})

            if event.get("type") == "event_msg" and isinstance(payload, dict):
                if payload.get("type") == "task_complete":
                    msg = clip(payload.get("last_agent_message") or "", 1200)
                    if msg:
                        session_dones.append({"time": dt.isoformat(), "text": msg})

        if events_in_window == 0:
            continue

        matched_sessions += 1
        workspace = cwd or "<unknown>"
        group = workspaces[workspace]
        group["sessions"].append({
            "session_id": session_id,
            "file": str(path),
            "first_event_utc": first.isoformat() if first else None,
            "last_event_utc": last.isoformat() if last else None,
            "events_in_window": events_in_window,
        })
        group["user_messages"].extend(session_users[:20])
        group["task_completions"].extend(session_dones[-12:])
        group["commands"].extend(session_commands[:80])
        group["command_results"].extend(session_command_results[:80])

    finalized = {
        workspace: finalize_workspace(group)
        for workspace, group in sorted(workspaces.items())
    }
    return {
        "scanned_session_files": scanned,
        "matched_sessions": matched_sessions,
        "workspaces": finalized,
    }


def main() -> int:
    args = parse_args()
    start, end, local_start, local_end = compute_window(args)
    data = collect(Path(args.codex_home).expanduser(), start, end)
    data["window"] = {
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "local_start": local_start,
        "local_end": local_end,
        "timezone": args.tz,
    }
    print(json.dumps(data, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
