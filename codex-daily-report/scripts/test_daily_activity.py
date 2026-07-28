#!/usr/bin/env python3
"""Regression tests for unified daily-report activity collection."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from activity_common import empty_activity, finalize_activity
from collect_codex_activity import redact
from collect_claude_activity import collect as collect_claude
from collect_daily_activity import build_task_groups, relation_for


START = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
END = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def event(
    event_type: str,
    timestamp: str,
    session_id: str,
    cwd: str,
    content: list[dict] | None = None,
    **extra: object,
) -> dict:
    value = {
        "type": event_type,
        "timestamp": timestamp,
        "sessionId": session_id,
        "cwd": cwd,
        "uuid": extra.pop("uuid", f"{event_type}-{timestamp}"),
        **extra,
    }
    if content is not None:
        value["message"] = {
            "role": "assistant" if event_type == "assistant" else "user",
            "content": content,
        }
    return value


class ClaudeCollectorTests(unittest.TestCase):
    def test_boundary_tools_workspace_and_subagent_rollup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            claude_home = root / ".claude"
            project = claude_home / "projects" / "fixture"
            subagents = project / "session-1" / "subagents"
            subagents.mkdir(parents=True)
            repo = root / "repo"
            (repo / ".git").mkdir(parents=True)
            (repo / "src").mkdir()
            target = repo / "src" / "app.py"

            main_events = [
                event(
                    "user",
                    "2026-07-27T12:00:00Z",
                    "session-1",
                    str(root),
                    [{"type": "text", "text": "Implement TASK-42"}],
                    uuid="main-user",
                ),
                event(
                    "assistant",
                    "2026-07-27T12:01:00Z",
                    "session-1",
                    str(root),
                    [
                        {"type": "thinking", "thinking": "not collected"},
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "Edit",
                            "input": {
                                "file_path": str(target),
                                "old_string": "x",
                                "new_string": "y",
                            },
                        },
                    ],
                    uuid="main-tool",
                ),
                event(
                    "user",
                    "2026-07-27T12:02:00Z",
                    "session-1",
                    str(root),
                    [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": "Updated successfully",
                            "is_error": False,
                        }
                    ],
                    uuid="main-result",
                ),
                event(
                    "user",
                    "2026-07-28T12:00:00Z",
                    "session-1",
                    str(root),
                    [{"type": "text", "text": "outside end"}],
                    uuid="outside-end",
                ),
            ]
            main_path = project / "session-1.jsonl"
            main_path.write_text(
                "\n".join(json.dumps(item) for item in main_events) + "\n",
                encoding="utf-8",
            )

            side_events = [
                event(
                    "user",
                    "2026-07-27T12:01:15Z",
                    "session-1",
                    str(root),
                    [{"type": "text", "text": "Review TASK-42"}],
                    isSidechain=True,
                    agentId="agent-1",
                    uuid="side-user",
                ),
                event(
                    "assistant",
                    "2026-07-27T12:01:30Z",
                    "session-1",
                    str(root),
                    [
                        {
                            "type": "tool_use",
                            "id": "tool-2",
                            "name": "Read",
                            "input": {"file_path": str(target)},
                        }
                    ],
                    isSidechain=True,
                    agentId="agent-1",
                    uuid="side-tool",
                ),
            ]
            (subagents / "agent-1.jsonl").write_text(
                "\n".join(json.dumps(item) for item in side_events) + "\n",
                encoding="utf-8",
            )

            result = collect_claude(claude_home, START, END)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(result["activities"]), 1)
            activity = result["activities"][0]
            self.assertEqual(activity["workspace"], str(repo.resolve()))
            self.assertEqual(activity["changed_files"], [str(target)])
            self.assertEqual(len(activity["subagent_ids"]), 1)
            self.assertNotIn("not collected", json.dumps(activity))


class AssociationTests(unittest.TestCase):
    def make_activity(
        self,
        source: str,
        seed: str,
        text: str,
        *,
        workspace: str = "/repo",
        roles: list[str] | None = None,
        commits: list[str] | None = None,
        files: list[str] | None = None,
    ) -> dict:
        activity = empty_activity(
            source,
            f"session-{seed}",
            "main",
            workspace,
            START,
            seed,
            text,
        )
        activity["workspace"] = workspace
        activity["roles"] = roles or []
        activity["commits"] = commits or []
        activity["files"] = files or []
        return finalize_activity(activity)

    def test_strong_commit_relation(self) -> None:
        left = self.make_activity("codex", "a", "Implement login", commits=["abcdef1"])
        right = self.make_activity(
            "claude_code", "b", "Review login", commits=["abcdef1"]
        )
        relation = relation_for(left, right)
        self.assertIsNotNone(relation)
        self.assertEqual(relation["confidence"], "strong")

    def test_medium_review_relation_and_weak_non_relation(self) -> None:
        left = self.make_activity(
            "codex", "a", "Implement login validation", files=["/repo/src/login.py"]
        )
        right = self.make_activity(
            "claude_code",
            "b",
            "Code review login validation",
            files=["/repo/src/login.py"],
        )
        relation = relation_for(left, right)
        self.assertIsNotNone(relation)
        self.assertEqual(relation["confidence"], "medium")

        unrelated = self.make_activity("claude_code", "c", "Discuss deployment budget")
        self.assertIsNone(relation_for(left, unrelated))

    def test_task_groups_merge_linked_activities(self) -> None:
        left = self.make_activity("codex", "a", "Implement TASK-42")
        right = self.make_activity("claude_code", "b", "Review TASK-42")
        relation = relation_for(left, right)
        groups = build_task_groups([left, right], [relation])
        self.assertEqual(len(groups), 1)
        self.assertEqual(set(groups[0]["sources"]), {"codex", "claude_code"})

    def test_redaction(self) -> None:
        value = redact("api_key=sk-abcdefghijklmnopqrstuvwxyz123456")
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", value)


class ValidatorTests(unittest.TestCase):
    def validator_data(self) -> dict:
        return {
            "schema_version": 2,
            "sources": {
                "codex": {"status": "ok", "warnings": []},
                "claude_code": {"status": "ok", "warnings": []},
            },
            "activities": [
                {"id": "codex-a", "material": True},
                {"id": "claude-code-b", "material": True},
            ],
            "relations": [
                {
                    "left_activity_id": "codex-a",
                    "right_activity_id": "claude-code-b",
                    "decision": "merge",
                }
            ],
            "workspaces": {
                "/repo": {
                    "sessions": [{"events_in_window": 40}],
                    "commands": [{"category": "edit"}],
                    "command_results": [],
                    "evidence_summary": {"command_count": 1},
                }
            },
        }

    def report(self, split: bool = False, include_claude: bool = True) -> str:
        second = ""
        marker = "<!-- activities: codex-a claude-code-b -->"
        if split:
            marker = "<!-- activities: codex-a -->"
            second = """
#### 任务：审查
<!-- activities: claude-code-b -->

完成事项：完成审查。
关键文件变化：无。
验证/运行结果：通过。
风险或遗留事项：无。
下一步：无。
"""
        source_line = "- Codex 与 Claude Code 均成功采集。" if include_claude else "- Codex 成功采集。"
        return f"""# 工作日报

## 报告日期

2026-07-28

## 统计时间范围

前一天 20:00 至当天 20:00（Asia/Singapore）。

## 信息来源说明

{source_line}

## 按项目/工作区分组的工作概览

### `/repo`

#### 任务：实现并审查
{marker}

完成事项：完成实现。
关键文件变化：`src/app.py`。
验证/运行结果：测试通过。
风险或遗留事项：无。
下一步：观察。
{second}

## 关键文件变化

- `src/app.py`

## 验证/运行过的命令

- tests passed

## 风险或遗留事项

- 无。

## 下一个工作日待办

- 观察结果。
"""

    def run_validator(self, data: dict, report: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            collector = root / "collector.json"
            markdown = root / "report.md"
            collector.write_text(json.dumps(data), encoding="utf-8")
            markdown.write_text(report, encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("validate_report.py")),
                    str(collector),
                    str(markdown),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_valid_merged_report(self) -> None:
        result = self.run_validator(self.validator_data(), self.report())
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_split_linked_activity_fails(self) -> None:
        result = self.run_validator(self.validator_data(), self.report(split=True))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("same task section", result.stderr)

    def test_missing_source_and_secret_fail(self) -> None:
        report = self.report(include_claude=False) + "\napi_key=sk-abcdefghijklmnopqrstuvwxyz123456\n"
        result = self.run_validator(self.validator_data(), report)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Claude Code", result.stderr)
        self.assertIn("sensitive", result.stderr)


if __name__ == "__main__":
    unittest.main()
