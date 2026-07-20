# Worker Isolation Self-Healing RFC

**Status**: Draft  
**Date**: 2026-07-15  
**Source baseline**: `main/master` `911abcefb36b4ff5e4138ccc5a90f439342dcc24`

This RFC analyzes current worker self-termination paths when
`auto_del_dead_node=true`, and proposes replacing network-jitter-triggered
self-kill with explicit worker runtime state, service admission control, and
recovery/reconciliation.

Documents:

- [design-and-story.md](./design-and-story.md): story, existing exit path
  analysis, impact, module abstraction, and core logic.
- [cluster-boundary-review-20260720.md](./cluster-boundary-review-20260720.md):
  overall implementation judgment, `ICoordinationBackend` boundary correctness
  review, and required closure items before the design can be called complete.
- [refactor-plan-20260720.md](./refactor-plan-20260720.md): accepted Plan A
  cohesion refactor plan using thin worker-local abstractions and TDD gates.
- [scale-fault-overlap-followups.md](./scale-fault-overlap-followups.md):
  scale-in/scale-out plus overlapping-fault acceptance cases that still need
  active UT/ST coverage.
