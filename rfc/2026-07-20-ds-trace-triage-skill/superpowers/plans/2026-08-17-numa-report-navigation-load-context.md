# NUMA Report Navigation and Load Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the NUMA report homepage link to the bottleneck and triage reports and display the parameterized per-node load configuration.

**Architecture:** Extend the existing NUMA post-processor contract with an optional `runtime_config` object and expose four CLI flags. Render relative offline links and configuration cards from the JSON contract; keep all analysis classifications unchanged.

**Tech Stack:** Python 3, embedded HTML/CSS/JavaScript, pytest, Playwright/Edge.

## Global Constraints

- The current case uses 315 QPS per node, 8 Clients, 16 threads per Client, and 1 Worker per node.
- Derived values are configuration arithmetic, not measured throughput.
- Missing configuration renders as unconfigured and never inherits current-case constants.
- Links are relative and work from the extracted offline share directory.

---

### Task 1: Analysis Contract and CLI

**Files:**
- Modify: `scripts/ds_trace_numa_analysis.py`
- Test: `tests/scripts/test_ds_trace_numa_analysis.py`

**Interfaces:**
- Consumes: four optional integer/float CLI values.
- Produces: `metadata.runtime_config` with `qps_per_node`, `client_count`, `threads_per_client`, `workers_per_node`, `qps_per_client`, and `client_threads_per_node`.

- [ ] **Step 1: Write the failing contract tests**

Assert that `build_analysis(..., runtime_config={...})` preserves the four inputs and computes `39.375` QPS per Client and `128` Client threads per node. Assert a missing config produces null derived values.

- [ ] **Step 2: Run the focused tests and confirm failure**

Run: `python3 -m pytest -q -s tests/scripts/test_ds_trace_numa_analysis.py -k runtime_config`

- [ ] **Step 3: Implement the runtime config normalizer and CLI flags**

Add optional `--qps-per-node`, `--client-count`, `--threads-per-client`, and `--workers-per-node`; pass them into `build_analysis` and compute derived values only when operands are present and valid.

- [ ] **Step 4: Run the focused tests and confirm pass**

Run: `python3 -m pytest -q -s tests/scripts/test_ds_trace_numa_analysis.py -k runtime_config`

### Task 2: Homepage Navigation and Rendering

**Files:**
- Modify: `scripts/ds_trace_numa_analysis.py`
- Modify: `tests/scripts/check_ds_trace_numa_analysis.js`
- Test: `tests/scripts/test_ds_trace_numa_analysis.py`

**Interfaces:**
- Consumes: `metadata.runtime_config`.
- Produces: relative `index.html`, `bottleneck.html`, and `triage.html` navigation plus visible configuration cards.

- [ ] **Step 1: Write failing renderer assertions**

Assert the HTML includes `href="bottleneck.html"`, `href="triage.html"`, a runtime configuration container, and the configured values.

- [ ] **Step 2: Run the renderer test and confirm failure**

Run: `python3 -m pytest -q -s tests/scripts/test_ds_trace_numa_analysis.py -k renderer`

- [ ] **Step 3: Implement the homepage navigation and cards**

Add three hero buttons and render 315, 8, 16, 1, 39.375, and 128 from JSON. Keep the layout responsive and use relative paths.

- [ ] **Step 4: Extend browser validation**

Verify both target links, displayed config, pagination, filtering, download, centered chart titles, and 900px no-overflow behavior.

### Task 3: Regenerate and Deliver

**Files:**
- Update generated files under `<output-dir>/trace-0817-pr2081-numa-analysis-20260817/`.
- Replace `<output-dir>/trace-0817-pr2081-numa-analysis-share-20260817.tar.gz`.

**Interfaces:**
- Consumes: the existing triage run, bottleneck JSON, source refs, and original trace archive.
- Produces: refreshed offline report directory and tarball.

- [ ] **Step 1: Run the generator with the current case configuration**

Pass `--qps-per-node 315 --client-count 8 --threads-per-client 16 --workers-per-node 1`.

- [ ] **Step 2: Run regression and browser checks**

Run the triage, bottleneck, and NUMA pytest suites, Node syntax check, and Edge browser contract.

- [ ] **Step 3: Refresh the share package and validate it**

Copy the regenerated HTML/JSON/script, recreate the tarball, run `gzip -t`, and compute SHA256.

- [ ] **Step 4: Commit implementation changes**

Commit only the script, tests, browser helper, and this plan; generated Downloads artifacts remain outside Git.
