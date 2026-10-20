"""Pure pinned-source worktree policy; cleanliness and allowed generated paths differ."""


def source_state(product, status, tracked_status):
    lines = status.splitlines() if isinstance(status, str) else []
    untracked = [line for line in lines if line.startswith("?? ")]
    allowed = [line for line in untracked if product == "datasystem" and line == "?? .venv/"]
    unexpected = [line for line in untracked if line not in allowed]
    tracked_clean = tracked_status == "" and not any(not line.startswith("?? ") for line in lines)
    return {"source_worktree_clean": status == "", "source_status_porcelain": status,
            "source_tracked_worktree_clean": tracked_clean, "source_tracked_status_porcelain": tracked_status,
            "source_untracked_status_porcelain": "\n".join(untracked) if isinstance(status, str) else None,
            "source_allowed_untracked": allowed, "source_unexpected_untracked": unexpected,
            "source_worktree_policy_valid": isinstance(status, str) and tracked_clean and not unexpected,
            "source_worktree_policy": "Tracked source must be clean; only DataSystem's recipe-created untracked .venv/ directory is allowed. Full worktree cleanliness remains separately recorded."}
