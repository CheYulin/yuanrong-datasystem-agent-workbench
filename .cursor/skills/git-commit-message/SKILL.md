---
name: git-commit-message
description: >-
  Produces a one-line git commit subject in the form [tag] summary from staged
  or requested changes. Use when the user asks for a commit message, git message,
  or "[chore]"-style subjects; when preparing a commit; or when summarizing
  git diff / staged files for a concise title.
---

# Git commit message (`[tag] summary`)

## Output format (required)

Emit **exactly one line** (subject only, no body unless the user asks):

```text
[tag] Imperative short description in English
```

- **`[tag]`**: lowercase, one word, common values below. Pick the **single best** fit.
- **Description**: imperative mood (*Add*, *Fix*, *Update*, *Align*, *Remove*), **no trailing period**, aim **≤ 72 characters** for the whole line.

## Tag reference

| Tag | Use when |
|-----|----------|
| `feat` | New user-facing capability, API, or behavior |
| `fix` | Bug fix or incorrect behavior correction |
| `docs` | Documentation only |
| `chore` | Maintenance: tooling, config, Cursor rules/skills, `.gitignore`, repo layout, scripts that do not change product behavior |
| `refactor` | Code/doc structure change without intended behavior change |
| `test` | Tests only |
| `perf` | Performance improvement |
| `ci` | CI / pipeline config |
| `revert` | Reverting a prior commit |

If unsure between `chore` and `feat`, default to **`chore`** when the change is meta/tooling/docs layout; use **`feat`** when behavior or public surface changes.

## Workflow

1. **Gather scope** (unless the user pasted a summary already):
   - `git diff --cached --stat` for staged work, or `git diff --stat` for unstaged if they want a message before staging.
   - Skim `git diff --cached --name-only` to infer area (e.g. `.cursor/`, `scripts/`, `docs/`).
2. **Choose `tag`** using the table.
3. **Write the summary**: concrete nouns (what changed), not vague ("update files").
4. **Return** the single line in a fenced `text` block so the user can copy-paste: `git commit -m '...'`.

## Examples

**Input (staged):** new Cursor rules + skills, no product code.  
**Output:**

```text
[chore] Align repo layout with Cursor rules and skills
```

**Input:** staged fix for off-by-one in metrics counter.  
**Output:**

```text
[fix] Correct off-by-one in metrics counter increment
```

**Input:** only `README.md` and `docs/agent/*.md` updated.  
**Output:**

```text
[docs] Expand agent maintenance and decision-tree guides
```

## Optional (only if asked)

- **Body**: blank line after subject, then bullets; wrap at ~72 cols.
- **Scope** inside summary: acceptable as nouns — e.g. `[fix] Resolve null deref in ZMQ reconnect path` (no need for `fix(zmq):` unless the team uses that hybrid; this project’s convention is **`[tag]` first**).

## Anti-patterns

- Do not output only `chore:` or `feat(scope):` without **brackets** unless the user switches convention.
- Do not use vague subjects: "Update", "Fix stuff", "Changes".
