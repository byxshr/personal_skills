#!/usr/bin/env python3
"""Shared activity normalization helpers for daily-report collectors."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from collect_codex_activity import (
    clip,
    extract_branches,
    extract_changed_files,
    extract_commits,
    extract_metrics,
    extract_risk_signals,
    extract_test_results,
    parse_dt,
    unique,
)


PR_RE = re.compile(r"(?i)(?:pull(?:/|\s+request\s*#?)|pr\s*#)(\d+)")
TASK_REF_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,11}-\d+\b")
REVIEW_RE = re.compile(r"(?i)\b(?:code\s*review|review|reviewing|reviewed)\b|代码审查|审查|复核")
VERIFY_RE = re.compile(
    r"(?i)\b(?:test|tests|testing|verify|verification|validate|validation|ci|lint|"
    r"pytest|py_compile|typecheck)\b|测试|验证|校验"
)
FIX_RE = re.compile(r"(?i)\b(?:fix|fixed|repair|resolve|address(?:ed|ing)?)\b|修复|修正|整改")
IMPLEMENT_RE = re.compile(
    r"(?i)\b(?:implement|implemented|create|created|add|added|build|built|update|updated|"
    r"refactor|refactored|migrate|migrated)\b|实现|新增|创建|更新|重构|迁移"
)
STOP_WORDS = {
    "about", "after", "again", "also", "and", "before", "claude", "code", "codex",
    "current", "daily", "file", "files", "from", "have", "into", "please", "report",
    "review", "task", "that", "the", "this", "today", "using", "with", "work", "workspace",
}


def stable_id(prefix: str, *parts: Any) -> str:
    raw = "\x1f".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def parse_event_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return parse_dt(str(value))
    except Exception:
        return None


def flatten_content(value: Any) -> str:
    parts: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for key in ("text", "content", "output", "stdout", "stderr", "message"):
                if key in item:
                    visit(item[key])

    visit(value)
    return clip("\n".join(part for part in parts if part), 1600)


def command_category(name: str, command: str = "") -> str:
    lowered_name = name.lower()
    text = f"{name} {command}".lower()
    if lowered_name in {"edit", "write", "apply_patch"}:
        return "edit"
    if lowered_name in {"read", "grep", "glob", "ls", "find"}:
        return "inspect"
    if lowered_name in {"agent", "task", "taskcreate", "taskupdate", "taskoutput"}:
        return "agent"
    if lowered_name == "skill":
        return "skill"
    if lowered_name in {"webfetch", "websearch"}:
        return "web"
    if lowered_name not in {"bash", "exec_command"}:
        return lowered_name or "tool"
    if re.search(r"(^|\s)git(?:\s+-C\s+\S+)?\s+", command):
        return "git"
    if re.search(r"\b(pytest|go test|cargo test|npm test|pnpm test|yarn test)\b", text):
        return "test"
    if re.search(r"\b(py_compile|tsc\s+--noemit|mypy|ruff|eslint|bash -n|shellcheck)\b", text):
        return "static-check"
    if re.search(r"(^|\s)(ssh|scp|rsync)\b", command):
        return "remote"
    if re.search(r"(^|\s)(rg|sed|cat|ls|find|wc|nl|head|tail)\b", command):
        return "inspect"
    return "command"


def empty_activity(
    source: str,
    session_id: str,
    actor: str,
    cwd: str,
    timestamp: datetime,
    seed: str,
    user_text: str = "",
) -> dict[str, Any]:
    activity_id = stable_id(source.replace("_", "-"), session_id, actor, timestamp.isoformat(), seed)
    activity = {
        "id": activity_id,
        "source": source,
        "session_id": session_id,
        "actors": [actor],
        "start_utc": timestamp.isoformat(),
        "end_utc": timestamp.isoformat(),
        "cwd": cwd or "<unknown>",
        "workspace": cwd or "<unknown>",
        "user_requests": [],
        "completions": [],
        "commands": [],
        "command_results": [],
        "files": [],
        "changed_files": [],
        "test_results": [],
        "risk_signals": [],
        "commits": [],
        "branches": [],
        "pr_refs": [],
        "task_refs": [],
        "roles": [],
        "subagent_ids": [],
        "warnings": [],
        "material": True,
    }
    if user_text:
        activity["user_requests"].append(
            {"time": timestamp.isoformat(), "text": clip(user_text, 1200)}
        )
    return activity


def touch_activity(activity: dict[str, Any], timestamp: datetime) -> None:
    current_start = parse_event_time(activity.get("start_utc"))
    current_end = parse_event_time(activity.get("end_utc"))
    if current_start is None or timestamp < current_start:
        activity["start_utc"] = timestamp.isoformat()
    if current_end is None or timestamp > current_end:
        activity["end_utc"] = timestamp.isoformat()


def merge_activity(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    for timestamp_key in ("start_utc", "end_utc"):
        left = parse_event_time(target.get(timestamp_key))
        right = parse_event_time(source.get(timestamp_key))
        if right is None:
            continue
        if left is None or (timestamp_key == "start_utc" and right < left) or (
            timestamp_key == "end_utc" and right > left
        ):
            target[timestamp_key] = right.isoformat()

    for key in (
        "actors", "user_requests", "completions", "commands", "command_results", "files",
        "changed_files", "test_results", "risk_signals", "commits", "branches", "pr_refs",
        "task_refs", "roles", "subagent_ids", "warnings",
    ):
        values = list(target.get(key) or []) + list(source.get(key) or [])
        target[key] = unique(values, 240)
    return target


def resolve_path(value: str, cwd: str) -> Path | None:
    if not value or value.startswith("<"):
        return None
    try:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = Path(cwd).expanduser() / path
        return path.resolve(strict=False)
    except Exception:
        return None


def find_git_root(path: Path | None) -> Path | None:
    if path is None:
        return None
    candidate = path if path.is_dir() else path.parent
    if not candidate.exists() and path.suffix:
        candidate = path.parent
    for current in (candidate, *candidate.parents):
        if (current / ".git").exists():
            return current
    return None


def command_directory_hints(command: str, cwd: str) -> list[str]:
    hints: list[str] = []
    patterns = [
        re.compile(r"(?:^|[;&|]\s*)git\s+-C\s+(?P<path>'[^']+'|\"[^\"]+\"|[^\s;&|]+)"),
        re.compile(r"(?:^|[;&|]\s*)cd\s+(?P<path>'[^']+'|\"[^\"]+\"|[^\s;&|]+)\s*(?:&&|;)"),
    ]
    for pattern in patterns:
        for match in pattern.finditer(command):
            raw = match.group("path")
            try:
                parsed = shlex.split(raw)[0]
            except Exception:
                parsed = raw.strip("'\"")
            resolved = resolve_path(parsed, cwd)
            if resolved:
                hints.append(str(resolved))
    return unique(hints, 20)


def activity_text(activity: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("user_requests", "completions"):
        for item in activity.get(key) or []:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
    for item in activity.get("commands") or []:
        if isinstance(item, dict):
            parts.extend([str(item.get("cmd") or ""), str(item.get("arguments") or "")])
    return clip(" ".join(parts), 5000)


def keywords(text: str) -> set[str]:
    tokens = {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", text)
    }
    return {token for token in tokens if token not in STOP_WORDS and len(token) <= 60}


def infer_roles(text: str, commands: list[dict[str, Any]]) -> list[str]:
    roles: list[str] = []
    if REVIEW_RE.search(text):
        roles.append("Code Review")
    if FIX_RE.search(text):
        roles.append("修正")
    if VERIFY_RE.search(text) or any(
        command.get("category") in {"test", "static-check"} for command in commands
    ):
        roles.append("验证")
    if IMPLEMENT_RE.search(text) or any(command.get("category") == "edit" for command in commands):
        roles.append("实施")
    if not roles:
        roles.append("分析")
    return unique(roles, 8)


def finalize_activity(activity: dict[str, Any]) -> dict[str, Any]:
    cwd = str(activity.get("cwd") or "<unknown>")
    files = list(activity.get("files") or [])
    changed_files = list(activity.get("changed_files") or [])
    tests = list(activity.get("test_results") or [])
    risks = list(activity.get("risk_signals") or [])
    commits = list(activity.get("commits") or [])
    branches = list(activity.get("branches") or [])
    metrics: list[str] = []
    workspace_hints: list[str] = []

    for command in activity.get("commands") or []:
        if not isinstance(command, dict):
            continue
        path = command.get("file_path") or command.get("workdir")
        if path:
            files.append(str(path))
            workspace_hints.append(str(path))
        workspace_hints.extend(command.get("workspace_hints") or [])
        if command.get("category") == "edit" and path:
            changed_files.append(str(path))

    for result in activity.get("command_results") or []:
        if not isinstance(result, dict):
            continue
        files.extend(result.get("files") or [])
        changed_files.extend(result.get("changed_files") or [])
        tests.extend(result.get("test_results") or [])
        risks.extend(result.get("risk_signals") or [])
        commits.extend(result.get("commits") or [])
        branches.extend(result.get("branches") or [])
        metrics.extend(result.get("metrics") or [])

    text = activity_text(activity)
    commits.extend(extract_commits(text))
    pr_refs = list(activity.get("pr_refs") or []) + PR_RE.findall(text)
    task_refs = list(activity.get("task_refs") or []) + TASK_REF_RE.findall(text)

    roots: list[str] = []
    for hint in [*workspace_hints, *files]:
        resolved = resolve_path(str(hint), cwd)
        root = find_git_root(resolved)
        if root:
            roots.append(str(root))
    cwd_root = find_git_root(resolve_path(cwd, cwd))
    if cwd_root:
        roots.append(str(cwd_root))
    if roots:
        counts = Counter(roots)
        activity["workspace"] = counts.most_common(1)[0][0]
        if len(counts) > 1:
            activity["warnings"] = unique(
                list(activity.get("warnings") or [])
                + [f"activity touched multiple git roots: {', '.join(sorted(counts))}"],
                20,
            )
    else:
        activity["workspace"] = cwd

    activity["files"] = unique(files, 160)
    activity["changed_files"] = unique(changed_files, 120)
    activity["test_results"] = unique(tests, 50)
    activity["risk_signals"] = unique(risks, 50)
    activity["commits"] = unique(commits, 40)
    activity["branches"] = unique(branches, 20)
    activity["pr_refs"] = unique([str(item) for item in pr_refs], 30)
    activity["task_refs"] = unique(task_refs, 30)
    activity["metrics"] = unique(metrics, 40)
    activity["roles"] = infer_roles(text, activity.get("commands") or [])
    activity["keywords"] = sorted(keywords(text))[:80]
    activity["material"] = bool(
        activity.get("user_requests")
        or activity.get("completions")
        or activity.get("commands")
        or activity.get("command_results")
    )
    return activity


def generic_result(
    output: str,
    command: dict[str, Any] | None,
    call_id: str,
    is_error: bool | None = None,
) -> dict[str, Any]:
    command = command or {}
    category = str(command.get("category") or "tool")
    cmd = str(command.get("cmd") or "")
    item: dict[str, Any] = {
        "call_id": call_id,
        "name": command.get("name") or "tool_result",
        "category": category,
    }
    if cmd:
        item["cmd"] = cmd
    if command.get("file_path"):
        item["file_path"] = command["file_path"]
    if is_error is not None:
        item["exit_code"] = 1 if is_error else 0
    if output.strip():
        item["output_excerpt"] = clip(output, 1000)

    changed = []
    if category == "edit" and command.get("file_path"):
        changed.append(str(command["file_path"]))
    if category == "git" and re.search(r"\bgit\s+(?:-C\s+\S+\s+)?(?:status|diff|log|show)\b", cmd):
        changed.extend(extract_changed_files(output))
    tests = extract_test_results(output) if category in {"test", "static-check"} else []
    commits = extract_commits(output) if category in {"git", "edit"} else []
    branches = extract_branches(output) if category == "git" else []
    risks = extract_risk_signals(output, item.get("exit_code"))
    metrics = extract_metrics(output)
    if changed:
        item["changed_files"] = unique(changed, 80)
    if tests:
        item["test_results"] = tests
    if commits:
        item["commits"] = commits
    if branches:
        item["branches"] = branches
    if risks:
        item["risk_signals"] = risks
    if metrics:
        item["metrics"] = metrics
    return item
