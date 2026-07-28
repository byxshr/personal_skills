#!/usr/bin/env python3
"""Collect normalized Claude Code activities for a daily report window."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from activity_common import (
    command_category,
    command_directory_hints,
    empty_activity,
    finalize_activity,
    flatten_content,
    generic_result,
    merge_activity,
    parse_event_time,
    touch_activity,
)
from collect_codex_activity import clip, parse_json_line, unique


IGNORED_TYPES = {
    "mode", "permission-mode", "file-history-snapshot", "file-history-delta",
    "last-prompt", "queue-operation",
}


def session_files(claude_home: Path) -> list[Path]:
    root = claude_home / "projects"
    return sorted(root.glob("**/*.jsonl")) if root.exists() else []


def user_text(event: dict[str, Any]) -> str:
    if event.get("isMeta") or event.get("isCompactSummary"):
        return ""
    message = event.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        text = content
    else:
        parts: list[str] = []
        for item in content or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        text = " ".join(parts)
    text = text.strip()
    if not text:
        return ""
    if text.startswith(("[Request interrupted", "<task-notification>", "<local-command-stdout>")):
        return ""
    if text.startswith("/compact"):
        return ""
    if text.startswith("<command-name>"):
        args_match = re.search(r"<command-args>(.*?)</command-args>", text, flags=re.DOTALL)
        text = args_match.group(1).strip() if args_match else ""
        if not text:
            return ""
    return clip(text, 1200)


def assistant_text_items(event: dict[str, Any]) -> list[str]:
    message = event.get("message") or {}
    parts: list[str] = []
    for item in message.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
            parts.append(clip(str(item["text"]), 1200))
    return parts


def tool_uses(event: dict[str, Any], cwd: str) -> list[dict[str, Any]]:
    message = event.get("message") or {}
    tools: list[dict[str, Any]] = []
    for item in message.get("content") or []:
        if not isinstance(item, dict) or item.get("type") != "tool_use":
            continue
        name = str(item.get("name") or "tool")
        args = item.get("input") if isinstance(item.get("input"), dict) else {}
        command = str(args.get("command") or "")
        record: dict[str, Any] = {
            "call_id": str(item.get("id") or ""),
            "name": name,
            "category": command_category(name, command),
        }
        if command:
            record["cmd"] = clip(command, 1600)
            record["workspace_hints"] = command_directory_hints(command, cwd)
        file_path = args.get("file_path")
        if file_path:
            record["file_path"] = str(file_path)
        if args:
            safe_args = {
                key: value
                for key, value in args.items()
                if key not in {"content", "new_string", "old_string", "prompt"}
            }
            if safe_args:
                record["arguments"] = clip(json.dumps(safe_args, ensure_ascii=False), 1000)
        tools.append(record)
    return tools


def tool_results(event: dict[str, Any]) -> list[tuple[str, str, bool | None]]:
    message = event.get("message") or {}
    results: list[tuple[str, str, bool | None]] = []
    for item in message.get("content") or []:
        if not isinstance(item, dict) or item.get("type") != "tool_result":
            continue
        results.append(
            (
                str(item.get("tool_use_id") or ""),
                flatten_content(item.get("content")),
                item.get("is_error") if isinstance(item.get("is_error"), bool) else None,
            )
        )
    return results


def parse_file(
    path: Path,
    start: datetime,
    end: datetime,
    seen_events: set[tuple[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    calls: dict[str, dict[str, Any]] = {}
    session_id = path.stem
    cwd = "<unknown>"
    actor = "subagent" if "/subagents/" in str(path) else "main"
    matched_events = 0
    parse_errors = 0
    unknown_events = 0

    try:
        handle = path.open(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return [], {"matched_events": 0, "parse_errors": 1, "warnings": [f"{path}: {exc}"]}

    with handle:
        for line in handle:
            event = parse_json_line(line)
            if not event:
                parse_errors += 1
                continue
            session_id = str(event.get("sessionId") or event.get("session_id") or session_id)
            cwd = str(event.get("cwd") or cwd)
            event_uuid = str(event.get("uuid") or "")
            event_key = (session_id, event_uuid)
            if event_uuid and event_key in seen_events:
                continue
            if event_uuid:
                seen_events.add(event_key)

            timestamp = parse_event_time(event.get("timestamp"))
            if timestamp is None or not (start <= timestamp < end):
                continue
            matched_events += 1
            event_type = str(event.get("type") or "")

            if event_type == "user":
                results = tool_results(event)
                if results:
                    if current is None:
                        current = empty_activity(
                            "claude_code", session_id, actor, cwd, timestamp, event_uuid or line[:80]
                        )
                    touch_activity(current, timestamp)
                    for call_id, output, is_error in results:
                        current["command_results"].append(
                            {
                                "time": timestamp.isoformat(),
                                **generic_result(output, calls.get(call_id), call_id, is_error),
                            }
                        )
                    continue

                text = user_text(event)
                if text:
                    current_end = (
                        parse_event_time(current.get("end_utc"))
                        if current is not None
                        else None
                    )
                    if (
                        current is not None
                        and current_end is not None
                        and timestamp - current_end <= timedelta(hours=4)
                    ):
                        touch_activity(current, timestamp)
                        current["user_requests"].append(
                            {"time": timestamp.isoformat(), "text": text}
                        )
                        continue
                    if current is not None:
                        activities.append(finalize_activity(current))
                    current = empty_activity(
                        "claude_code", session_id, actor, cwd, timestamp, event_uuid or text, text
                    )
                continue

            if event_type == "assistant":
                if current is None:
                    current = empty_activity(
                        "claude_code", session_id, actor, cwd, timestamp, event_uuid or line[:80]
                    )
                touch_activity(current, timestamp)
                for text in assistant_text_items(event):
                    current["completions"].append({"time": timestamp.isoformat(), "text": text})
                for tool in tool_uses(event, cwd):
                    calls[str(tool.get("call_id") or "")] = tool
                    current["commands"].append({"time": timestamp.isoformat(), **tool})
                    if tool.get("file_path"):
                        current["files"].append(str(tool["file_path"]))
                        if tool.get("category") == "edit":
                            current["changed_files"].append(str(tool["file_path"]))
                continue

            if event_type not in IGNORED_TYPES and event_type not in {
                "system", "attachment", "progress", "summary",
            }:
                unknown_events += 1

    if current is not None:
        activities.append(finalize_activity(current))
    return activities, {
        "matched_events": matched_events,
        "parse_errors": parse_errors,
        "unknown_events": unknown_events,
        "warnings": [],
    }


def roll_up_subagents(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    main_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    side: list[dict[str, Any]] = []
    for activity in activities:
        if "subagent" in (activity.get("actors") or []):
            side.append(activity)
        else:
            main_by_session[str(activity.get("session_id") or "")].append(activity)

    retained: list[dict[str, Any]] = [
        activity for group in main_by_session.values() for activity in group
    ]
    for activity in side:
        candidates = main_by_session.get(str(activity.get("session_id") or ""), [])
        side_start = parse_event_time(activity.get("start_utc"))
        best: dict[str, Any] | None = None
        best_distance: float | None = None
        for candidate in candidates:
            candidate_start = parse_event_time(candidate.get("start_utc"))
            candidate_end = parse_event_time(candidate.get("end_utc"))
            if side_start is None or candidate_start is None:
                continue
            if candidate_end and candidate_start <= side_start <= candidate_end + timedelta(hours=8):
                distance = max(0.0, (side_start - candidate_end).total_seconds())
            else:
                distance = abs((side_start - candidate_start).total_seconds())
            if distance <= 8 * 3600 and (best_distance is None or distance < best_distance):
                best = candidate
                best_distance = distance
        if best is None:
            retained.append(activity)
            continue
        best["subagent_ids"] = unique(
            list(best.get("subagent_ids") or []) + [activity["id"]], 80
        )
        merge_activity(best, activity)

    return dedupe_activities(
        [finalize_activity(activity) for activity in retained if activity.get("material")]
    )


def dedupe_activities(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for activity in activities:
        first_request = ""
        if activity.get("user_requests"):
            first_request = str(activity["user_requests"][0].get("text") or "")
        key = (
            str(activity.get("workspace") or activity.get("cwd") or ""),
            str(activity.get("start_utc") or ""),
            first_request,
        )
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = activity
        else:
            merge_activity(existing, activity)
    return [finalize_activity(activity) for activity in deduped.values()]


def collect(claude_home: Path, start: datetime, end: datetime) -> dict[str, Any]:
    files = session_files(claude_home)
    if not (claude_home / "projects").exists():
        return {
            "status": "unavailable",
            "scanned_session_files": 0,
            "matched_sessions": 0,
            "matched_events": 0,
            "warnings": [f"Claude Code projects directory not found: {claude_home / 'projects'}"],
            "activities": [],
        }

    activities: list[dict[str, Any]] = []
    seen_events: set[tuple[str, str]] = set()
    matched_sessions: set[str] = set()
    matched_events = 0
    parse_errors = 0
    unknown_events = 0
    warnings: list[str] = []
    for path in files:
        parsed, stats = parse_file(path, start, end, seen_events)
        activities.extend(parsed)
        matched_events += int(stats.get("matched_events") or 0)
        parse_errors += int(stats.get("parse_errors") or 0)
        unknown_events += int(stats.get("unknown_events") or 0)
        warnings.extend(stats.get("warnings") or [])
        for activity in parsed:
            matched_sessions.add(str(activity.get("session_id") or path.stem))

    if parse_errors:
        warnings.append(f"ignored {parse_errors} malformed Claude Code JSONL line(s)")
    if unknown_events:
        warnings.append(f"ignored {unknown_events} unknown timestamped Claude Code event(s)")
    return {
        "status": "ok",
        "scanned_session_files": len(files),
        "matched_sessions": len(matched_sessions),
        "matched_events": matched_events,
        "warnings": unique(warnings, 50),
        "activities": roll_up_subagents(activities),
    }
