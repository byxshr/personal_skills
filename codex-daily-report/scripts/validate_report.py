#!/usr/bin/env python3
"""Validate a daily report against Codex-only or unified collector output."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


SENSITIVE_PATTERNS = [
    re.compile(r"(?i)\bauthorization\b\s*[:=]\s*['\"]?[^'\"\s`，,}]+"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token)\b\s*[:=]\s*['\"]?[^'\"\s`，,}]+"),
    re.compile(r"(?i)\bpassword\b\s*[:=]\s*['\"]?[^'\"\s`，,}]+"),
    re.compile(r"(?i)\bsecret\b\s*[:=]\s*['\"]?[^'\"\s`，,}]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b"),
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

TODO_PLACEHOLDER_PATTERN = re.compile(r"暂无|无明确|无待办|没有明确|none", flags=re.IGNORECASE)
TODO_STATUS_PATTERN = re.compile(r"已完成|部分完成|未完成|无法判断")
ACTIVITY_COMMENT_PATTERN = re.compile(r"<!--\s*activities?\s*:\s*([^>]+?)\s*-->")
SOURCE_LABELS = {"codex": "Codex", "claude_code": "Claude Code"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collector_json", help="Path to collector JSON output")
    parser.add_argument("report_md", help="Path to generated Markdown report")
    parser.add_argument(
        "--previous-report",
        help="Optional path to the previous calendar day's Markdown report. If omitted, infer it from a standard report path.",
    )
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


def heading_section(report: str, keyword: str) -> str:
    pattern = rf"^(##+)\s+.*{re.escape(keyword)}.*$"
    match = re.search(pattern, report, flags=re.MULTILINE)
    if not match:
        return ""
    level = len(match.group(1))
    following = report[match.end() :]
    next_heading = re.search(rf"^#{{1,{level}}}\s+", following, flags=re.MULTILINE)
    if not next_heading:
        return following.strip()
    return following[: next_heading.start()].strip()


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

        if isinstance(group, dict):
            for source_workspace in group.get("source_workspaces") or []:
                if not isinstance(source_workspace, dict):
                    continue
                original = str(source_workspace.get("cwd") or "")
                if not original or original == workspace or original == "<unknown>":
                    continue
                if original not in report:
                    warnings.append(
                        f"source workspace alias is not explained in report: {original} -> {workspace}"
                    )
                elif not has_merge_or_uninspectable_note(report, original):
                    warnings.append(
                        f"source workspace alias lacks a merge/attribution note: {original} -> {workspace}"
                    )


def task_heading_for_position(report: str, position: int) -> tuple[int, str] | None:
    headings = list(re.finditer(r"^(#{4,})\s+(.+?)\s*$", report[:position], flags=re.MULTILINE))
    if not headings:
        return None
    heading = headings[-1]
    return heading.start(), heading.group(2).strip()


def activity_coverage(report: str) -> tuple[dict[str, list[tuple[int, str]]], list[str]]:
    coverage: dict[str, list[tuple[int, str]]] = {}
    orphaned: list[str] = []
    for comment in ACTIVITY_COMMENT_PATTERN.finditer(report):
        task = task_heading_for_position(report, comment.start())
        ids = [item for item in re.split(r"[\s,]+", comment.group(1).strip()) if item]
        if task is None:
            orphaned.extend(ids)
            continue
        for activity_id in ids:
            coverage.setdefault(activity_id, []).append(task)
    return coverage, orphaned


def check_v2_sources(
    data: dict[str, Any],
    report: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    sources = data.get("sources") or {}
    info = heading_section(report, "信息来源")
    for source, details in sources.items():
        label = SOURCE_LABELS.get(str(source), str(source))
        if label.lower() not in info.lower():
            errors.append(f"information source section does not mention: {label}")
        if not isinstance(details, dict):
            continue
        for warning in details.get("warnings") or []:
            warnings.append(f"{label} collector warning: {warning}")
        if details.get("status") != "ok" and not re.search(
            rf"{re.escape(label)}.{{0,80}}(?:无法|不可|缺失|未读取|unavailable|missing)",
            info,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            errors.append(f"unavailable source lacks an explicit limitation note: {label}")


def check_v2_activity_coverage(
    data: dict[str, Any],
    report: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    activities = [
        activity
        for activity in data.get("activities") or []
        if isinstance(activity, dict) and activity.get("material", True)
    ]
    expected = {str(activity.get("id")) for activity in activities if activity.get("id")}
    coverage, orphaned = activity_coverage(report)
    for activity_id in orphaned:
        errors.append(f"activity marker is outside a task heading: {activity_id}")
    for activity_id in sorted(expected):
        locations = coverage.get(activity_id) or []
        if not locations:
            errors.append(f"material activity missing from report: {activity_id}")
        elif len(locations) > 1:
            errors.append(f"material activity appears more than once: {activity_id}")
    for activity_id in sorted(set(coverage) - expected):
        warnings.append(f"report references unknown activity id: {activity_id}")

    for relation in data.get("relations") or []:
        if not isinstance(relation, dict) or relation.get("decision") != "merge":
            continue
        left = str(relation.get("left_activity_id") or "")
        right = str(relation.get("right_activity_id") or "")
        left_locations = coverage.get(left) or []
        right_locations = coverage.get(right) or []
        if len(left_locations) == 1 and len(right_locations) == 1:
            if left_locations[0][0] != right_locations[0][0]:
                errors.append(
                    f"linked activities must appear in the same task section: {left}, {right}"
                )

    title = re.search(r"^#\s+(.+?)\s*$", report, flags=re.MULTILINE)
    if not title or title.group(1).strip() != "工作日报":
        errors.append("schema v2 report title must be: 工作日报")


def check_sensitive_content(report: str, errors: list[str]) -> None:
    for pattern in SENSITIVE_PATTERNS:
        match = pattern.search(report)
        if match:
            excerpt = " ".join(match.group(0).split())
            errors.append(f"possible sensitive value in report: {excerpt[:120]}")


def is_concrete_todo(text: str) -> bool:
    normalized = re.sub(r"^\[[ xX]\]\s*", "", text).strip()
    if not normalized:
        return False
    if TODO_PLACEHOLDER_PATTERN.search(normalized) and len(normalized) <= 40:
        return False
    return True


def extract_bullet_items(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        match = re.match(r"\s*(?:[-*]|\d+[.)])\s+(?:\[[ xX]\]\s*)?(.+?)\s*$", line)
        if match and is_concrete_todo(match.group(1)):
            items.append(match.group(1).strip())
    return items


def dedupe_items(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        key = re.sub(r"\s+", " ", item).strip().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def extract_todo_items(report: str) -> list[str]:
    items: list[str] = []
    global_section = heading_section(report, "下一个工作日待办")
    if global_section:
        items.extend(extract_bullet_items(global_section))

    label_matches = list(re.finditer(r"(?m)^\s*下一个工作日待办[:：]\s*(.*)$", report))
    for index, match in enumerate(label_matches):
        inline = match.group(1).strip()
        if inline and is_concrete_todo(inline):
            items.append(inline)
            continue
        start = match.end()
        end = label_matches[index + 1].start() if index + 1 < len(label_matches) else len(report)
        block = report[start:end]
        block = re.split(
            r"(?m)^\s*(?:今日主线|完成事项|关键文件变化|验证/运行结果|风险或遗留事项)[:：]\s*",
            block,
            maxsplit=1,
        )[0]
        items.extend(extract_bullet_items(block))

    return dedupe_items(items)


def infer_previous_report_path(report_path: Path) -> Path | None:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})\.md", report_path.name)
    if not match or report_path.parent.parent == report_path.parent:
        return None
    try:
        current_date = datetime.strptime(report_path.stem, "%Y-%m-%d").date()
    except ValueError:
        return None
    previous_date = current_date - timedelta(days=1)
    report_root = report_path.parent.parent
    return report_root / previous_date.strftime("%Y-%m") / f"{previous_date:%Y-%m-%d}.md"


def check_previous_todo_review(previous_report: Path | None, report: str, errors: list[str], warnings: list[str]) -> None:
    if previous_report is None or not previous_report.exists():
        return
    previous_text = previous_report.read_text(encoding="utf-8", errors="ignore")
    previous_todos = extract_todo_items(previous_text)
    if not previous_todos:
        return

    review_section = heading_section(report, "昨日待办") or heading_section(report, "待办完成情况")
    if not review_section:
        errors.append(
            f"previous report has {len(previous_todos)} todo(s), but current report lacks a previous-todo review section"
        )
        return
    if not TODO_STATUS_PATTERN.search(review_section):
        warnings.append("previous-todo review section does not include explicit status labels")

    next_todo_section = heading_section(report, "下一个工作日待办")
    if re.search(r"未完成|部分完成", review_section) and not next_todo_section:
        warnings.append("previous-todo review has unfinished items but the next-workday todo section was not found")


def main() -> int:
    args = parse_args()
    data = load_json(Path(args.collector_json))
    report_path = Path(args.report_md)
    report = report_path.read_text(encoding="utf-8", errors="ignore")
    previous_report = Path(args.previous_report) if args.previous_report else infer_previous_report_path(report_path)

    errors: list[str] = []
    warnings: list[str] = []
    check_required_sections(report, errors)
    check_workspace_coverage(data, report, errors, warnings)
    if int(data.get("schema_version") or 1) >= 2:
        check_v2_sources(data, report, errors, warnings)
        check_v2_activity_coverage(data, report, errors, warnings)
    check_previous_todo_review(previous_report, report, errors, warnings)
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
