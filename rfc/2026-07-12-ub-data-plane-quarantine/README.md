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

## Current Decision

When UB failures isolate a destination worker, the default behavior is:

- Do not write new data to that destination worker.
- Do not silently fall back to TCP.
- Allow TCP write fallback only behind an explicit opt-in policy.
- Resume writes only after recovery probing marks the destination healthy.

The latest `main/master` source was indexed at
`ddba645424a857bbbd14d256cb0b97d3c155ac4f`. The next step is to review this
RFC, settle the open policy choices, and then write an implementation plan.
