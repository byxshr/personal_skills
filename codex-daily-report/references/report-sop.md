# Daily Work Report SOP

## Scope

Treat the report root as an output location, not as the work scope. Cover every material Codex and Claude Code activity whose event timestamp is inside the statistical window.

Use the unified collector rather than the scheduled-task cwd or session creation time. Long-running sessions and Claude subagents can contain relevant in-window events.

## Source And Workspace Identity

- Preserve `source`, `session_id`, activity ID, original `cwd`, and canonical workspace.
- Use file paths, command workdirs, simple `cd`, `git -C`, and visible Git roots to attribute work launched from a broad directory.
- Treat different canonical workspaces as separate unless a commit, PR, task reference, or explicit user instruction proves a cross-repository task.
- If an original cwd is attributed to another canonical workspace, mention the attribution once.
- If a path no longer exists, is unmounted, or is not a Git repository, keep the activity and state the inspection limit.
- Roll Claude subagent evidence into its parent activity; never create a standalone task merely because a subagent ran.

## Activity And Task Association

Organize the report around actual tasks, not around AI tools.

- Merge activities with a strong relation: same commit, same PR, or same explicit task reference.
- Merge medium-confidence relations only when the collector records at least two corroborating signals and one is semantic or artifact-based, such as file overlap, task similarity, review handoff, or an explicit cross-tool reference.
- Do not merge based only on the same cwd, repository, branch, or nearby timestamps.
- Treat Codex implementation followed by Claude Code review, fixes, or verification as phases of one task when relation evidence supports it.
- Keep uncertain activities as separate tasks under the same project and state the limitation briefly.
- Deduplicate shared commits, files, tests, and metrics inside a merged task.
- Preserve source roles such as `实施`, `Code Review`, `修正`, and `验证`.

Each material activity must appear exactly once in an invisible marker immediately below a `####` task heading:

```markdown
#### 任务：任务名称
<!-- activities: codex-abc claude-code-def -->
```

All activities joined by a collector relation with `decision: merge` must appear in the same task section.

## Evidence Rules

- Use event timestamps for inclusion; use Git state and command output for corroboration.
- Prefer user requests, final summaries, edits, command results, commits, changed files, tests, review findings, and remote verification.
- Do not use thinking blocks, compaction metadata, UI state, or unsupported inference.
- Do not claim completion from a plan, task status label, or silence alone.
- Redact credentials, tokens, Authorization headers, API keys, passwords, secrets, and private keys.
- Cache only collector excerpts that have already been clipped and redacted.

## Previous Todo Review

- Read the previous calendar day's report from the same report root.
- Extract global and per-workspace `下一个工作日待办` items.
- Judge each item against combined Codex, Claude Code, and Git evidence.
- Use `已完成`, `部分完成`, `未完成`, or `无法判断`; include evidence for completed or partial items.
- Carry `未完成` and `部分完成` into today's todos. Carry `无法判断` only when still relevant or risky.
- Merge duplicate carried and newly discovered todos while preserving project context.
- If the previous report is missing or contains no concrete todo, do not invent a review.

## Generation Steps

1. Run the unified collector and save the JSON beside the report.
2. Inspect source status, activities, relations, task groups, and canonical workspaces.
3. Read the previous report and extract its todos.
4. Gather same-window Git evidence for locally inspectable canonical workspaces.
5. Name each task group from its concrete objective and evidence.
6. Draft by project/workspace, then task:
   - objective and reached state;
   - source and phase;
   - completed work and review findings;
   - key file changes and validation;
   - risks, leftovers, and next action.
7. Put all linked cross-tool activities in one task and add their activity marker.
8. Add global file changes, validation, risk, and next-workday todo sections.
9. Add growth suggestions only when a repeated behavior in the current window supports them.
10. Run the validator with the previous report path when present and fix all errors.

## Report Structure

```markdown
# 工作日报

## 报告日期

## 统计时间范围

## 信息来源说明

## 昨日待办完成情况

## 按项目/工作区分组的工作概览

### `/path/to/workspace`

#### 任务：任务名称
<!-- activities: activity-id-1 activity-id-2 -->

目标与状态：

来源及阶段：

完成事项：

Review 发现：

关键文件变化：

验证/运行结果：

风险或遗留事项：

下一步：

## 关键文件变化

## 验证/运行过的命令

## 风险或遗留事项

## 下一个工作日待办

## 工作和个人成长建议
```

Omit `Review 发现` and growth suggestions when there is no concrete evidence. If both collectors are readable and have no material activity, use `无工作进展`. If a source is unavailable, describe the evidence gap instead.

## Quality Checklist

- The 20:00-to-20:00 Asia/Singapore window is explicit.
- Information sources name both collectors and disclose warnings or missing data.
- Every material activity ID appears exactly once under a task heading.
- Linked implementation and review activities share one task section.
- Every canonical workspace appears or has an evidence-based merge/uninspectable note.
- Similar paths and same-repository activities are not merged without task evidence.
- Commits, files, tests, metrics, risks, and carried todos are concise and evidence-backed.
- No sensitive value is copied into the report.
- `scripts/validate_report.py` exits successfully.
