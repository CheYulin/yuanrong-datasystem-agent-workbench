# UB Data Plane Quarantine

**Status**: Draft  
**Date**: 2026-07-12  
**Source repo**: `yuanrong-datasystem`  
**Workbench**: `yuanrong-datasystem-agent-workbench`

This RFC records the design direction for isolating UB data-plane failures so
that silent success-rate degradation does not continue after UB becomes
unhealthy.

## Documents

| Document | Purpose |
|----------|---------|
| [design-and-story.md](./design-and-story.md) | Story, scenarios, use cases, and acceptance criteria |
| [design.md](./design.md) | Initial design, semantics, state machine, and integration points |
| [flow-analysis.md](./flow-analysis.md) | CodeGraph-backed read/write, migration, and rebalance flow analysis on `main/master` |
| [phased-implementation-spec.md](./phased-implementation-spec.md) | Phased TDD/SDD implementation plan mapped to acceptance features |

## Current Decision

When UB failures isolate a destination worker, the default behavior is:

- Do not write new data to that destination worker.
- Do not silently fall back to TCP.
- Allow TCP write fallback only behind an explicit opt-in policy.
- Resume writes only after recovery probing marks the destination healthy.

The original flow analysis indexed `main/master` at
`ddba645424a857bbbd14d256cb0b97d3c155ac4f`. The phased implementation spec is
now recorded and should be executed from a freshly fetched `main/master`; the
latest observed implementation baseline after URMA Mock merge is `e5d7178ac`.
