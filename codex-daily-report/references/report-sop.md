# Codex Daily Report SOP

## Scope

Treat the report root as an output location, not as the work scope. Cover every Codex workspace with event activity inside the statistical window.

Do not rely only on the current working directory, the scheduled-task cwd, or `session_index.jsonl`. Long-running sessions can be created before the window and still contain relevant events inside it.

## Workspace Identity

- Treat each distinct collector `cwd` as a distinct activity source.
- Do not merge workspaces only because their paths, names, parent directories, or repository names look similar.
- If multiple `cwd` values are reported under one project group, state the evidence-based reason, such as a shared git root or an explicit user instruction.
- If a workspace path no longer exists, is not a git repo, or cannot be inspected, still mention it and briefly explain the limitation.

## Evidence Rules

- Use session/event `timestamp` to decide inclusion.
- Group by exact `cwd` unless a documented merge reason exists.
- Prefer concrete user requests, final task summaries, tool calls, command outputs, git commits, changed files, test results, and remote verification outputs.
- Do not invent work for projects that have no confirmed evidence.
- If a workspace cannot be inspected or is not a git repo, say so briefly.
- Redact or omit credentials, tokens, Authorization headers, app IDs, passwords, and secrets.

## Previous Todo Review

- For report date `D`, look for the previous calendar day's report under the same report root: `D - 1 day`.
- If the previous report exists, read its `下一个工作日待办` section and any per-workspace `下一个工作日待办：` items.
- Add a `昨日待办完成情况` section to today's report when previous todos exist.
- Classify each previous todo using current-window evidence:
  - `已完成`: there is concrete evidence that the todo was completed.
  - `部分完成`: there is evidence of progress, but the todo is not fully done.
  - `未完成`: there is evidence it remains open, or there is no completion evidence and the item is still relevant.
  - `无法判断`: the current window does not provide enough evidence to judge, and relevance is unclear.
- Include short evidence for `已完成` and `部分完成`; do not claim completion from silence.
- Carry `未完成` and `部分完成` items into today's `下一个工作日待办`, preserving the project/workspace context. Carry `无法判断` items only when they still look relevant or risky.
- Merge duplicate carried-over and newly discovered todos; keep the clearest wording and mark carried items as continuations when helpful.
- If the previous report is missing or has no concrete todos, state that briefly or omit the review section; do not invent carryover work.

## Recommended Generation Steps

1. Run the collector with the report date and window, and keep the collector JSON available for validation.
2. List all active workspaces from collector output.
3. Locate and read the previous calendar day's report if it exists, and extract its next-workday todos.
4. For every workspace that exists locally:
   - If it is a git repo, collect git log and tracked status for the same window.
   - If it is not a git repo, rely on session evidence and visible files.
5. Draft the previous-todo review using current-window evidence.
6. Draft the report by workspace:
   - Start with the most substantive business/project work.
   - Put the report automation workspace later unless it was the main work.
   - Include temporary setup workspaces only if they had direct activity.
   - For each substantive workspace, include today's main thread, completed work, key file changes, validation or command results, risks or leftovers, and next-workday todo.
7. Add global validation commands and observed results.
8. Add risks and next-workday todos based on evidence only, including unfinished previous todos that should continue.
9. Add growth suggestions only when specific patterns appear in the day's work; skip the section if there is no concrete evidence.
10. Run `scripts/validate_report.py` against the collector JSON and the Markdown report; pass the previous report path when it exists.
11. Re-read the report and verify that every workspace listed by the collector appears in the report or has an explicit merge/uninspectable note.

## Workspace Section Rubric

Each substantive workspace section should answer:

- What main workstream moved forward today?
- What state did the work reach?
- What evidence supports that status?
- What risks, failures, or unfinished items remain?
- What is the next useful action for the next workday?

## Quality Checklist

- The statistical window is shown with timezone.
- The report states what data sources were used.
- Every active workspace appears as a group, or the report states why it was merged or could not be inspected.
- Similar-looking workspaces are not merged without evidence.
- Commits are listed with short hashes and concise meanings when available.
- Key changed files are listed for substantive code/file work when available.
- Tests and remote checks include the important result numbers.
- Untracked or dirty worktrees are mentioned only when relevant.
- Previous report todos are reviewed when the previous report exists and contains concrete todos.
- Unfinished previous todos are carried into the new next-workday todo list unless there is evidence they are obsolete.
- "Next workday todo" items name the relevant project/workspace.
- No sensitive values are copied into the report.
- `scripts/validate_report.py` passes without errors. Warnings should be fixed when they indicate missing evidence that is available.

## Suggested Sections

```markdown
# Codex 工作日报

## 报告日期

## 统计时间范围

## 信息来源说明

## 昨日待办完成情况

## 按项目/工作区分组的工作概览

### `/path/to/workspace`

今日主线：

完成事项：

关键文件变化：

验证/运行结果：

风险或遗留事项：

下一个工作日待办：

## 关键文件变化

## 验证/运行过的命令

## 风险或遗留事项

## 下一个工作日待办

## 工作和个人成长建议
```

When there is no confirmed work:

```markdown
# Codex 工作日报

## 报告日期

YYYY-MM-DD

## 统计时间范围

...

## 工作概览

无工作进展

## 下一个工作日待办

暂无明确待办
```
