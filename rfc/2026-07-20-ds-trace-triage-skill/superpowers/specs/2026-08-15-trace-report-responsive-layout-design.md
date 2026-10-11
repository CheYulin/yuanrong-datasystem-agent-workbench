# Trace Report Responsive Layout Design

## Goal

Make generated bottleneck-report tables fit the available page width without horizontal dragging, and render the four same-Worker correlation charts one per row.

## Layout contract

- All report tables use a fixed-width layout and wrap long Trace, Worker, method, evidence, and conclusion text inside cells.
- Fixed layout does not mean equal columns: compact time/count/latency columns receive narrow widths, Worker/direction columns receive medium widths, and evidence/diagnosis/conclusion columns receive the remaining space. The URMA Trace diagnosis column must occupy at least 24% of its table at desktop width.
- Table containers scroll vertically only. At widths below 1050px and 650px, table-specific secondary breakdown columns are hidden while identity, status, total latency, primary category, and conclusion columns remain visible.
- Full fields remain available in the existing Trace detail/log panels; responsive hiding does not alter the underlying rows, sorting, pagination, filters, or downloads.
- Controls shrink to the available width and text inputs stop imposing a desktop minimum width.
- The four charts in “同 Worker 时间关联分析” always use a single-column grid.

## Validation

- The browser contract checks that the first two correlation cards have different vertical positions at desktop width.
- The browser contract measures the rendered URMA diagnosis-cell share and requires at least 24%, preventing equal-column regressions that create extremely tall rows.
- At a 900px viewport, the document and every visible table wrapper must have no horizontal overflow.
- Existing report pagination, sorting, chart interaction, Trace linkage, and downloads remain covered by the browser contract.
