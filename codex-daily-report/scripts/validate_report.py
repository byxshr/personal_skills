#!/usr/bin/env python3
"""Validate a Codex daily report against collector output."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


SENSITIVE_PATTERNS = [
    re.compile(r"(?i)\bauthorization\b\s*[:=]\s*['\"]?[^'\"\s`，,}]+"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*['\"]?[^'\"\s`，,}]+"),
    re.compile(r"(?i)\bpassword\b\s*[:=]\s*['\"]?[^'\"\s`，,}]+"),
    re.compile(r"(?i)\bsecret\b\s*[:=]\s*['\"]?[^'\"\s`，,}]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
]

REQUIRED_SECTION_KEYWORDS = [
    "报告日期",
    "统计时间范围",
    "信息来源",
    "工作概览",
    "验证",
    "风险",
    "待办",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collector_json", help="Path to collect_codex_activity.py JSON output")
    parser.add_argument("report_md", help="Path to generated Markdown report")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"collector JSON must be an object: {path}")
    return value


def has_heading(report: str, keyword: str) -> bool:
    pattern = rf"^##+\s+.*{re.escape(keyword)}.*$"
    return re.search(pattern, report, flags=re.MULTILINE) is not None


def find_workspace_heading(report: str, workspace: str) -> re.Match[str] | None:
    escaped = re.escape(workspace)
    return re.search(rf"^###(?!#)\s+.*{escaped}.*$", report, flags=re.MULTILINE)


def nearby_context(report: str, needle: str, radius: int = 240) -> str:
    index = report.find(needle)
    if index < 0:
        return ""
    start = max(0, index - radius)
    end = min(len(report), index + len(needle) + radius)
    return report[start:end]


def has_merge_or_uninspectable_note(report: str, workspace: str) -> bool:
    context = nearby_context(report, workspace)
    if not context:
        return False
    return re.search(
        r"合并|归入|同一|无法|不存在|不能检查|无法检查|not found|not a git repo|uninspectable|merged",
        context,
        flags=re.IGNORECASE,
    ) is not None


def workspace_section(report: str, workspace: str) -> str:
    match = find_workspace_heading(report, workspace)
    if not match:
        context = nearby_context(report, workspace, radius=600)
        return context
    start = match.start()
    following = report[match.end() :]
    next_same_level = re.search(r"^###(?!#)\s+", following, flags=re.MULTILINE)
    next_parent = re.search(r"^##(?!#)\s+", following, flags=re.MULTILINE)
    candidates = [m.start() for m in (next_same_level, next_parent) if m]
    if not candidates:
        return report[start:]
    return report[start : match.end() + min(candidates)]


def workspace_event_count(group: dict[str, Any]) -> int:
    total = 0
    for session in group.get("sessions") or []:
        try:
            total += int(session.get("events_in_window") or 0)
        except Exception:
            pass
    return total


def is_substantial_workspace(group: dict[str, Any]) -> bool:
    summary = group.get("evidence_summary") or {}
    command_count = int(summary.get("command_count") or len(group.get("commands") or []))
    result_count = int(summary.get("command_result_count") or len(group.get("command_results") or []))
    return command_count > 0 or result_count > 0 or workspace_event_count(group) >= 30


def check_required_sections(report: str, errors: list[str]) -> None:
    for keyword in REQUIRED_SECTION_KEYWORDS:
        if not has_heading(report, keyword):
            errors.append(f"missing required section containing: {keyword}")


def check_workspace_coverage(data: dict[str, Any], report: str, errors: list[str], warnings: list[str]) -> None:
    workspaces = data.get("workspaces") or {}
    if not isinstance(workspaces, dict):
        errors.append("collector JSON has no workspaces object")
        return
    for workspace, group in workspaces.items():
        if workspace not in report:
            errors.append(f"workspace missing from report: {workspace}")
            continue
        if not find_workspace_heading(report, workspace) and not has_merge_or_uninspectable_note(report, workspace):
            warnings.append(f"workspace appears outside a dedicated section without merge/uninspectable note: {workspace}")
        if isinstance(group, dict) and is_substantial_workspace(group):
            section = workspace_section(report, workspace)
            if not re.search(r"关键文件|文件变化|changed files?|modified|git diff", section, flags=re.IGNORECASE):
                warnings.append(f"substantial workspace may lack key file changes: {workspace}")
            if not re.search(r"验证|测试|运行|命令|passed|failed|exit_code", section, flags=re.IGNORECASE):
                warnings.append(f"substantial workspace may lack validation evidence: {workspace}")
            todo_context = section + "\n" + nearby_context(report, workspace, radius=900)
            if not re.search(r"待办|下一步|风险|遗留|todo|next", todo_context, flags=re.IGNORECASE):
                warnings.append(f"substantial workspace may lack risk/todo follow-up: {workspace}")


def check_sensitive_content(report: str, errors: list[str]) -> None:
    for pattern in SENSITIVE_PATTERNS:
        match = pattern.search(report)
        if match:
            excerpt = " ".join(match.group(0).split())
            errors.append(f"possible sensitive value in report: {excerpt[:120]}")


def main() -> int:
    args = parse_args()
    data = load_json(Path(args.collector_json))
    report = Path(args.report_md).read_text(encoding="utf-8", errors="ignore")

    errors: list[str] = []
    warnings: list[str] = []
    check_required_sections(report, errors)
    check_workspace_coverage(data, report, errors, warnings)
    check_sensitive_content(report, errors)

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"Report validation passed with {len(warnings)} warning(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
