# CodeGraph DB Open Diagnosis - 2026-08-03

## Symptom

Inside the Codex sandbox, CodeGraph commands fail with:

```text
Failed to get status: unable to open database file
```

Observed with:

```bash
/home/t14s/.local/bin/codegraph status /home/t14s/workspace/git-repos/yuanrong-datasystem --json
/home/t14s/.local/bin/codegraph query -p /home/t14s/workspace/git-repos/yuanrong-datasystem RefreshPeerTopology --json
/home/t14s/.local/bin/codegraph files -p /home/t14s/workspace/git-repos/yuanrong-datasystem --format flat --json
```

## Actual CodeGraph Paths

CLI:

```text
/home/t14s/.local/bin/codegraph -> /home/t14s/.codegraph/versions/v0.9.7/bin/codegraph
```

Project index directory:

```text
/home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph
```

SQLite DB opened by CodeGraph v0.9.7:

```text
/home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph/codegraph.db
/home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph/codegraph.db-wal
/home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph/codegraph.db-shm
```

Legacy/smaller DB also present, but not used by current `status` path:

```text
/home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph/graph.db
/home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph/graph.db-wal
/home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph/graph.db-shm
```

## Evidence

Running the same command outside the sandbox succeeds:

```json
{
  "initialized": true,
  "projectPath": "/home/t14s/workspace/git-repos/yuanrong-datasystem",
  "fileCount": 2159,
  "nodeCount": 53469,
  "edgeCount": 157732,
  "dbSizeBytes": 2696896512,
  "backend": "node-sqlite",
  "journalMode": "wal",
  "pendingChanges": {
    "added": 0,
    "modified": 0,
    "removed": 0
  },
  "worktreeMismatch": null
}
```

Filtered `strace` evidence from the successful run:

```text
access("/home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph/codegraph.db", F_OK) = 0
openat(AT_FDCWD, "/home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph/codegraph.db", O_RDWR|O_CREAT|O_NOFOLLOW|O_CLOEXEC, 0644) = 22
openat(AT_FDCWD, "/home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph/codegraph.db-wal", O_RDWR|O_CREAT|O_NOFOLLOW|O_CLOEXEC, 0644) = 23
openat(AT_FDCWD, "/home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph/codegraph.db-shm", O_RDWR|O_CREAT|O_NOFOLLOW|O_CLOEXEC, 0644) = 24
```

## Current Root Cause

The CodeGraph CLI opens `codegraph.db` in read-write WAL mode and creates/opens
the matching `codegraph.db-wal` and `codegraph.db-shm` files. In the Codex
sandbox, `/home/t14s/workspace/git-repos/yuanrong-datasystem` is readable but
not writable, so SQLite cannot open the DB in the required mode and surfaces the
generic error `unable to open database file`.

## Practical Guidance

Use CodeGraph commands with host/escalated execution when reading the shared
`/home` index from Codex, or run them from an environment that has write access
to the `.codegraph` directory. Do not rebuild a per-worktree index just to work
around this; the shared index is:

```text
/home/t14s/workspace/git-repos/yuanrong-datasystem/.codegraph/codegraph.db
```
