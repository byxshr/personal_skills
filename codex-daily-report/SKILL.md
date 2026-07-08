---
name: codex-daily-report
description: Generate high-quality Codex work daily reports from Codex session history, git/workspace evidence, and scheduled-task context. Use when Codex needs to create, regenerate, improve, or automate a daily work report, especially reports that must cover all active Codex workspaces in a time window, group work by project path, produce next-workday todos, or save Markdown reports under a YYYY-MM/YYYY-MM-DD.md structure.
---

# Codex Daily Report

## Workflow

1. Determine the report date and time window in `Asia/Singapore`.
   - Default report date: the current date in `Asia/Singapore`.
   - Default window: previous day 20:00 to report date 20:00 in `Asia/Singapore`.
   - Convert the window to UTC only for filtering timestamps.
2. Run `scripts/collect_codex_activity.py` to collect Codex session events by timestamp and group them by `cwd`; save the collector JSON to a temporary or sidecar file for report drafting and validation.
3. Locate the previous calendar day's report under the same report root, if it exists. Review its `下一个工作日待办` items against the current window's evidence before drafting today's report.
4. Read `references/report-sop.md` before drafting the final report.
5. For each active workspace found by the script, add verifiable context:
   - `git log --since ... --until ... --oneline --decorate --stat`
   - `git status --short --branch --untracked-files=no`
   - collector evidence such as command results, changed files, test results, risk signals, and remote/check metrics
   - relevant file paths, remote command outputs, or test results mentioned in Codex sessions
6. Write or update the Markdown report under the requested report root. Default root:
   `/Users/bianyuxin/hope-jobs/work_report`.
7. Run `scripts/validate_report.py` with the collector JSON, Markdown report, and previous report path when available. Fix validation errors before finishing; treat warnings as prompts to add available evidence.

## Collector

Run:

```bash
python3 /Users/bianyuxin/.codex/skills/codex-daily-report/scripts/collect_codex_activity.py \
  --date YYYY-MM-DD \
  --tz Asia/Singapore \
  --cutoff-hour 20
```

Useful options:

- `--report-root /path/to/work_report`
- `--cutoff-hour 20`
- `--window-start-utc 2026-07-06T12:00:00Z`
- `--window-end-utc 2026-07-07T12:00:00Z`
- `--pretty`

The collector scans `~/.codex/sessions` and `~/.codex/archived_sessions`, filters by event timestamp, and includes sessions created before the window if they have activity inside the window.

The collector output keeps the legacy workspace fields and also includes:

- `command_results`: associated function-call outputs with exit codes and short excerpts
- `evidence_summary`: command category counts, exit code counts, commits, branches, and metrics
- `changed_files`: file paths inferred from patches, git status, or git stats
- `test_results`: parsed test result summaries
- `risk_signals`: nonzero exits and generic error/failure signals

## Output

Save the report as:

```text
<report-root>/YYYY-MM/YYYY-MM-DD.md
```

The report must be Chinese Markdown and include:

- 标题
- 报告日期
- 统计时间范围
- 信息来源说明
- 昨日待办完成情况, when a previous report with todos exists
- 按项目/工作区分组的工作概览
- 完成事项
- 关键文件变化
- 验证/运行过的命令
- 风险或遗留事项
- 下一个工作日待办
- 工作和个人成长建议, only when the window provides concrete evidence

Validate the report with:

```bash
python3 /Users/bianyuxin/.codex/skills/codex-daily-report/scripts/validate_report.py \
  /path/to/collector.json \
  <report-root>/YYYY-MM/YYYY-MM-DD.md \
  --previous-report <report-root>/PREVIOUS-YYYY-MM/PREVIOUS-YYYY-MM-DD.md
```

Use `--previous-report` only when the previous calendar day's report file exists. The validator can also infer the previous report from a standard `<report-root>/YYYY-MM/YYYY-MM-DD.md` path.

If no confirmed Codex work exists in the window, still create the file and write `无工作进展`.
