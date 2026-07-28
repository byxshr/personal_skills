#!/usr/bin/env python3
"""Collect and associate Codex and Claude Code activity for a daily report."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

import collect_codex_activity as codex
from activity_common import (
    activity_text,
    empty_activity,
    finalize_activity,
    merge_activity,
    parse_event_time,
    stable_id,
    touch_activity,
)
from collect_claude_activity import collect as collect_claude


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Report date in local timezone, YYYY-MM-DD")
    parser.add_argument("--tz", default="Asia/Singapore", help="Report timezone")
    parser.add_argument("--cutoff-hour", type=int, default=20, help="Local cutoff hour")
    parser.add_argument("--window-start-utc", help="Inclusive UTC start")
    parser.add_argument("--window-end-utc", help="Exclusive UTC end")
    parser.add_argument(
        "--sources",
        default="codex,claude-code",
        help="Comma-separated sources: codex,claude-code",
    )
    parser.add_argument(
        "--codex-home",
        default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")),
    )
    parser.add_argument(
        "--claude-home",
        default=os.environ.get("CLAUDE_HOME", str(Path.home() / ".claude")),
    )
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def compute_window(args: argparse.Namespace) -> tuple[datetime, datetime, str, str]:
    if args.window_start_utc and args.window_end_utc:
        start = codex.parse_dt(args.window_start_utc)
        end = codex.parse_dt(args.window_end_utc)
        return start, end, start.isoformat(), end.isoformat()
    if ZoneInfo is None:
        raise RuntimeError("zoneinfo is required")
    if not 0 <= args.cutoff_hour <= 23:
        raise ValueError("--cutoff-hour must be between 0 and 23")
    tz = ZoneInfo(args.tz)
    report_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else datetime.now(tz).date()
    )
    local_end = datetime.combine(report_date, time(args.cutoff_hour), tzinfo=tz)
    local_start = local_end - timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc),
        local_end.astimezone(timezone.utc),
        local_start.isoformat(),
        local_end.isoformat(),
    )


def collect_codex_activities(
    codex_home: Path,
    start: datetime,
    end: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    activities: list[dict[str, Any]] = []
    matched_sessions: set[str] = set()
    matched_events = 0
    parse_errors = 0
    files = codex.session_files(codex_home)

    for path in files:
        cwd = "<unknown>"
        session_id = path.stem
        current: dict[str, Any] | None = None
        calls: dict[str, dict[str, Any]] = {}
        try:
            handle = path.open(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        with handle:
            for line in handle:
                event = codex.parse_json_line(line)
                if not event:
                    parse_errors += 1
                    continue
                payload = event.get("payload") or {}
                if event.get("type") == "session_meta" and isinstance(payload, dict):
                    cwd = str(payload.get("cwd") or cwd)
                    session_id = str(
                        payload.get("session_id") or payload.get("id") or session_id
                    )
                timestamp = codex.event_timestamp(event)

                if (
                    event.get("type") == "response_item"
                    and isinstance(payload, dict)
                    and payload.get("type") == "function_call"
                ):
                    command = codex.command_from_call(payload)
                    call_id = str(command.get("call_id") or "")
                    if call_id:
                        calls[call_id] = command

                if timestamp is None or not (start <= timestamp < end):
                    continue
                matched_events += 1
                matched_sessions.add(session_id)

                if event.get("type") == "response_item" and isinstance(payload, dict):
                    if payload.get("type") == "message" and payload.get("role") == "user":
                        text = codex.text_from_message(payload)
                        if not text or text.startswith(
                            (
                                "<environment_context>",
                                "# AGENTS.md instructions",
                                "<recommended_plugins>",
                                "<skill>",
                                "<app-context>",
                                "<permissions instructions>",
                            )
                        ):
                            continue
                        if current is not None:
                            activities.append(finalize_activity(current))
                        current = empty_activity(
                            "codex", session_id, "main", cwd, timestamp, text, text
                        )
                    elif payload.get("type") == "function_call":
                        if current is None:
                            current = empty_activity(
                                "codex", session_id, "main", cwd, timestamp, line[:120]
                            )
                        command = codex.command_from_call(payload)
                        touch_activity(current, timestamp)
                        current["commands"].append(
                            {"time": timestamp.isoformat(), **command}
                        )
                    elif payload.get("type") == "function_call_output":
                        if current is None:
                            current = empty_activity(
                                "codex", session_id, "main", cwd, timestamp, line[:120]
                            )
                        touch_activity(current, timestamp)
                        call_id = str(payload.get("call_id") or "")
                        current["command_results"].append(
                            {
                                "time": timestamp.isoformat(),
                                **codex.command_result_from_output(
                                    payload, calls.get(call_id)
                                ),
                            }
                        )

                if (
                    event.get("type") == "event_msg"
                    and isinstance(payload, dict)
                    and payload.get("type") == "task_complete"
                ):
                    if current is None:
                        current = empty_activity(
                            "codex", session_id, "main", cwd, timestamp, line[:120]
                        )
                    touch_activity(current, timestamp)
                    message = codex.clip(payload.get("last_agent_message") or "", 1400)
                    if message:
                        current["completions"].append(
                            {"time": timestamp.isoformat(), "text": message}
                        )
        if current is not None:
            activities.append(finalize_activity(current))

    warnings = []
    if parse_errors:
        warnings.append(f"ignored {parse_errors} malformed Codex JSONL line(s)")
    return (
        [activity for activity in activities if activity.get("material")],
        {
            "status": "ok",
            "scanned_session_files": len(files),
            "matched_sessions": len(matched_sessions),
            "matched_events": matched_events,
            "warnings": warnings,
        },
    )


def similarity(left: set[str], right: set[str]) -> float:
    if len(left & right) < 3:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def relation_for(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any] | None:
    if left.get("source") == right.get("source"):
        return None
    same_workspace = left.get("workspace") == right.get("workspace")
    commits = set(left.get("commits") or []) & set(right.get("commits") or [])
    prs = set(left.get("pr_refs") or []) & set(right.get("pr_refs") or [])
    refs = set(left.get("task_refs") or []) & set(right.get("task_refs") or [])
    signals: list[str] = []
    confidence = ""

    if commits:
        signals.append("same_commit")
    if prs:
        signals.append("same_pr")
    if refs:
        signals.append("same_task_reference")
    if signals:
        confidence = "strong"
    elif same_workspace:
        left_branches = {
            value for value in left.get("branches") or [] if value not in {"main", "master"}
        }
        right_branches = {
            value for value in right.get("branches") or [] if value not in {"main", "master"}
        }
        if left_branches & right_branches:
            signals.append("same_branch")
        if set(left.get("files") or []) & set(right.get("files") or []):
            signals.append("file_overlap")
        if similarity(
            set(left.get("keywords") or []), set(right.get("keywords") or [])
        ) >= 0.25:
            signals.append("task_similarity")
        left_time = parse_event_time(left.get("end_utc"))
        right_time = parse_event_time(right.get("start_utc"))
        if left_time and right_time and abs((right_time - left_time).total_seconds()) <= 8 * 3600:
            signals.append("temporal_handoff")
        roles = set(left.get("roles") or []) | set(right.get("roles") or [])
        if "Code Review" in roles and (
            "实施" in roles or "修正" in roles or "验证" in roles
        ):
            signals.append("review_handoff")
        combined = f"{activity_text(left)} {activity_text(right)}".lower()
        if "codex" in combined and (
            "review" in combined or "审查" in combined or "复核" in combined
        ):
            signals.append("explicit_cross_tool_reference")
        semantic = {
            "file_overlap", "task_similarity", "review_handoff",
            "explicit_cross_tool_reference",
        }
        if len(set(signals)) >= 2 and semantic & set(signals):
            confidence = "medium"

    if not confidence:
        return None
    return {
        "id": stable_id("relation", left["id"], right["id"]),
        "left_activity_id": left["id"],
        "right_activity_id": right["id"],
        "confidence": confidence,
        "decision": "merge",
        "signals": sorted(set(signals)),
    }


def build_relations(activities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    relations: list[dict[str, Any]] = []
    for index, left in enumerate(activities):
        for right in activities[index + 1 :]:
            relation = relation_for(left, right)
            if relation:
                relations.append(relation)
    return relations


def build_task_groups(
    activities: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    parent = {activity["id"]: activity["id"] for activity in activities}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for relation in relations:
        if relation.get("decision") == "merge":
            union(relation["left_activity_id"], relation["right_activity_id"])

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for activity in activities:
        groups[find(activity["id"])].append(activity)

    result: list[dict[str, Any]] = []
    for members in groups.values():
        ids = sorted(member["id"] for member in members)
        result.append(
            {
                "id": stable_id("task", *ids),
                "activity_ids": ids,
                "workspace": Counter(
                    str(member.get("workspace") or "<unknown>") for member in members
                ).most_common(1)[0][0],
                "sources": sorted({str(member.get("source")) for member in members}),
                "source_roles": {
                    source: sorted(
                        {
                            role
                            for member in members
                            if member.get("source") == source
                            for role in member.get("roles") or []
                        }
                    )
                    for source in sorted({str(member.get("source")) for member in members})
                },
                "changed_files": codex.unique(
                    [
                        item
                        for member in members
                        for item in member.get("changed_files") or []
                    ],
                    120,
                ),
                "test_results": codex.unique(
                    [
                        item
                        for member in members
                        for item in member.get("test_results") or []
                    ],
                    50,
                ),
                "risk_signals": codex.unique(
                    [
                        item
                        for member in members
                        for item in member.get("risk_signals") or []
                    ],
                    50,
                ),
            }
        )
    return sorted(result, key=lambda item: (item["workspace"], item["id"]))


def build_workspaces(activities: list[dict[str, Any]]) -> dict[str, Any]:
    workspaces: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "sessions": [],
            "user_messages": [],
            "task_completions": [],
            "commands": [],
            "command_results": [],
            "changed_files": [],
            "test_results": [],
            "risk_signals": [],
            "evidence_summary": {},
            "sources": [],
            "source_workspaces": [],
            "activity_ids": [],
        }
    )
    for activity in activities:
        workspace = str(activity.get("workspace") or activity.get("cwd") or "<unknown>")
        group = workspaces[workspace]
        group["sessions"].append(
            {
                "session_id": activity.get("session_id"),
                "source": activity.get("source"),
                "first_event_utc": activity.get("start_utc"),
                "last_event_utc": activity.get("end_utc"),
                "events_in_window": (
                    len(activity.get("commands") or [])
                    + len(activity.get("command_results") or [])
                    + len(activity.get("user_requests") or [])
                    + len(activity.get("completions") or [])
                ),
            }
        )
        group["user_messages"].extend(activity.get("user_requests") or [])
        group["task_completions"].extend(activity.get("completions") or [])
        group["commands"].extend(activity.get("commands") or [])
        group["command_results"].extend(activity.get("command_results") or [])
        group["changed_files"].extend(activity.get("changed_files") or [])
        group["test_results"].extend(activity.get("test_results") or [])
        group["risk_signals"].extend(activity.get("risk_signals") or [])
        group["sources"].append(activity.get("source"))
        group["source_workspaces"].append(
            {"source": activity.get("source"), "cwd": activity.get("cwd")}
        )
        group["activity_ids"].append(activity["id"])

    finalized: dict[str, Any] = {}
    for workspace, group in sorted(workspaces.items()):
        categories = Counter(
            str(command.get("category") or "unknown")
            for command in group["commands"]
            if isinstance(command, dict)
        )
        exits = Counter(
            str(result["exit_code"])
            for result in group["command_results"]
            if isinstance(result, dict) and "exit_code" in result
        )
        group["sessions"] = codex.unique(group["sessions"], 200)
        group["user_messages"] = codex.unique(group["user_messages"], 100)
        group["task_completions"] = codex.unique(group["task_completions"], 100)
        group["commands"] = codex.unique(group["commands"], 240)
        group["command_results"] = codex.unique(group["command_results"], 240)
        group["changed_files"] = codex.unique(group["changed_files"], 120)
        group["test_results"] = codex.unique(group["test_results"], 50)
        group["risk_signals"] = codex.unique(group["risk_signals"], 50)
        group["sources"] = sorted(set(group["sources"]))
        group["source_workspaces"] = codex.unique(group["source_workspaces"], 50)
        group["activity_ids"] = sorted(set(group["activity_ids"]))
        group["evidence_summary"] = {
            "command_count": len(group["commands"]),
            "command_result_count": len(group["command_results"]),
            "command_categories": dict(sorted(categories.items())),
            "exit_codes": dict(sorted(exits.items())),
            "commits": codex.unique(
                [
                    commit
                    for result in group["command_results"]
                    if isinstance(result, dict)
                    for commit in result.get("commits") or []
                ],
                40,
            ),
            "branches": codex.unique(
                [
                    branch
                    for result in group["command_results"]
                    if isinstance(result, dict)
                    for branch in result.get("branches") or []
                ],
                20,
            ),
            "metrics": codex.unique(
                [
                    metric
                    for result in group["command_results"]
                    if isinstance(result, dict)
                    for metric in result.get("metrics") or []
                ],
                40,
            ),
        }
        finalized[workspace] = group
    return finalized


def main() -> int:
    args = parse_args()
    start, end, local_start, local_end = compute_window(args)
    requested_sources = {
        item.strip().lower() for item in args.sources.split(",") if item.strip()
    }
    activities: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}

    if "codex" in requested_sources:
        codex_activities, codex_stats = collect_codex_activities(
            Path(args.codex_home).expanduser(), start, end
        )
        sources["codex"] = codex_stats
        activities.extend(codex_activities)
    if {"claude", "claude-code", "claude_code"} & requested_sources:
        claude_data = collect_claude(Path(args.claude_home).expanduser(), start, end)
        activities.extend(claude_data.pop("activities"))
        sources["claude_code"] = claude_data

    activities = sorted(
        [finalize_activity(activity) for activity in activities if activity.get("material")],
        key=lambda item: (item.get("start_utc") or "", item["id"]),
    )
    relations = build_relations(activities)
    task_groups = build_task_groups(activities, relations)
    workspaces = build_workspaces(activities)
    data = {
        "schema_version": 2,
        "window": {
            "start_utc": start.isoformat(),
            "end_utc": end.isoformat(),
            "local_start": local_start,
            "local_end": local_end,
            "timezone": args.tz,
        },
        "sources": sources,
        "scanned_session_files": sum(
            int(item.get("scanned_session_files") or 0) for item in sources.values()
        ),
        "matched_sessions": sum(
            int(item.get("matched_sessions") or 0) for item in sources.values()
        ),
        "workspaces": workspaces,
        "activities": activities,
        "relations": relations,
        "task_groups": task_groups,
    }
    print(json.dumps(data, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
