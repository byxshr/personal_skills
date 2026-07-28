---
name: codex-daily-report
description: Generate, regenerate, improve, or automate Chinese daily work reports from Codex and Claude Code session history plus git/workspace evidence. Use when a report must cover all active AI coding workspaces in a time window, merge related implementation and code-review activity across tools into actual tasks, review previous todos, or save Markdown under a YYYY-MM/YYYY-MM-DD.md structure.
---

# Daily Work Report

## Workflow

1. Determine the report date and window in `Asia/Singapore`.
   - Use the latest completed cutoff when the task runs before the cutoff.
   - Default window: previous day 20:00 to report date 20:00.
   - Convert to UTC only for event filtering.
2. Run `scripts/collect_daily_activity.py` with both sources and save its schema-v2 JSON beside the report.
3. Check `sources` warnings. Treat an unavailable source as an evidence limitation, not as proof of no work.
4. Locate the previous calendar day's report and review its `下一个工作日待办` against evidence from both sources.
5. Read `references/report-sop.md` before drafting.
6. For every canonical workspace in the collector:
   - inspect same-window Git history and current tracked status when available;
   - use normalized activities, task groups, changed files, tests, risks, commits, branches, and source roles;
   - retain uninspectable workspaces with a limitation note.
7. Draft by project/workspace, then by actual task. Merge linked Codex implementation, Claude Code review, fixes, and validation into one task.
8. Add `<!-- activities: ID... -->` immediately below each task heading, listing every activity consumed by that task exactly once.
9. Write `<report-root>/YYYY-MM/YYYY-MM-DD.md`, then run `scripts/validate_report.py`. Fix errors and evidence-backed warnings before finishing.

## Collector

Run:

```bash
python3 /Users/bianyuxin/.codex/skills/codex-daily-report/scripts/collect_daily_activity.py \
  --date YYYY-MM-DD \
  --tz Asia/Singapore \
  --cutoff-hour 20 \
  --sources codex,claude-code \
  --pretty
```

Useful options:

- `--codex-home /path/to/.codex`
- `--claude-home /path/to/.claude`
- `--window-start-utc 2026-07-26T12:00:00Z`
- `--window-end-utc 2026-07-27T12:00:00Z`

The unified collector scans Codex sessions and Claude Code `projects/**/*.jsonl`, filters each event by timestamp, rolls Claude subagents into their parent activity, resolves workspace evidence, and emits:

- `sources`: availability, scanned files, matched sessions/events, and warnings
- `activities`: normalized user-task activity with stable IDs and source roles
- `relations`: evidence-based cross-source merge decisions
- `task_groups`: connected activities that must be reported as one task
- `workspaces`: backward-compatible grouped evidence

Keep `scripts/collect_codex_activity.py` available for Codex-only compatibility.

## Output

Write Chinese Markdown headed `# 工作日报` and include:

- 报告日期
- 统计时间范围
- 信息来源说明 for Codex and Claude Code
- 昨日待办完成情况, when previous todos exist
- 按项目/工作区分组的工作概览
- task-level goals, source stages, outcomes, file changes, validation, review findings, risks, and next actions
- global key changes, validation, risks, and next-workday todos
- 工作和个人成长建议 only when concrete evidence supports them

Use task sections like:

```markdown
### `/path/to/workspace`

#### 任务：完成某项交付并闭环审查
<!-- activities: codex-abc claude-code-def -->

来源及阶段：

- Codex：实施
- Claude Code：Code Review、验证
```

Validate with:

```bash
python3 /Users/bianyuxin/.codex/skills/codex-daily-report/scripts/validate_report.py \
  <report-root>/YYYY-MM/YYYY-MM-DD.collector.json \
  <report-root>/YYYY-MM/YYYY-MM-DD.md \
  --previous-report <report-root>/PREVIOUS-YYYY-MM/PREVIOUS-YYYY-MM-DD.md
```

If both sources are readable and contain no confirmed work, still write the report and state `无工作进展`. If either source is unavailable, state the evidence limitation instead of making that conclusion.
