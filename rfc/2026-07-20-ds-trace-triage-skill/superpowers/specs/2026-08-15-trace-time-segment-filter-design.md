# Trace Latency-Band Filter Design

## Goal

Add a five-band Client-total-latency view to the generated TopN bottleneck report so a reviewer can see how the dominant problem mix changes with tail latency and filter the main overview to one band.

## Segmentation contract

- Use five explicit Client-total-latency bands: `[5,6)ms`, `[6,7)ms`, `[7,10)ms`, `[10,20]ms`, and `>20ms`.
- Keep rows below 5ms outside the five bands and show their count; do not silently force them into a band.
- Each band records its one-based index, Trace count, failed count, Client p50/p90, primary-problem counts, dominant primary problem, and member Trace IDs.
- A band describes only the current TopN input. It must not be presented as the full-run latency distribution.
- Worker-local URMA and correlation timestamps are not merged into this Client-latency grouping.

## User interface

Add a new overview panel, “图 1-4 Client 总时延五档问题分布”, after the existing stage-share chart.

- Render one stacked bar per segment, split by the six mutually exclusive primary-problem categories.
- The tooltip and a compact summary row show the latency band, Trace count, failed count, Client p50/p90, and dominant problem.
- Add filter buttons for “全部” and each available segment above the overview charts.
- The selected button is visually distinct and the overview states the current scope.
- Clicking a stacked bar selects the corresponding segment; clicking “全部” restores the complete TopN.

## Filter scope

The selected latency band filters:

- overview problem-count chart;
- overview problem-latency chart;
- overview stage-share chart;
- TopN stacked timeline;
- Trace table and its existing category/status/location/Worker/search filters.

The URMA, same-Worker correlation, non-transport, and Worker deep-dive chapters remain full-TopN views. Their headings must state that they are not affected by the overview time filter. This avoids comparing or clipping independent Worker-local clocks as though they were one global clock.

## Data flow

Python computes deterministic `aggregate.latency_segments` entries from the final ranked TopN rows. The page keeps `activeTimeSegment` in JavaScript. A single `scopeRows()` function returns either all rows or the selected band rows. Overview renderers and `applyFilters()` consume that scope. Deep-dive renderers continue consuming their existing full-row collections.

Changing the latency band resets the Trace-table page to one, preserves the existing filters, selects the first matching Trace when necessary, and re-renders only the affected overview/timeline/table components.

## Empty and edge cases

- Render all five reference bands even when a band has zero Trace, so different reports remain comparable.
- Rows below 5ms remain visible in “全部” and are explicitly counted as outside the five bands.
- If existing filters leave no Trace in the selected band, the table uses its existing empty state and the selected band remains active.
- Stage sums and primary-problem classification are unchanged; the bands only group and filter existing rows.

## Validation

- Unit-test all boundary values, especially exact 20ms versus greater than 20ms and the below-5ms exclusion.
- Assert each in-range Trace belongs to exactly one band.
- Browser-test the five buttons, stacked segment chart, selected-state styling, overview/timeline/table filtering, reset-to-all behavior, and unaffected deep-dive row counts.
- Re-run the existing triage and bottleneck regression suites, generated-page browser contract, skill validation, and package checksums.
