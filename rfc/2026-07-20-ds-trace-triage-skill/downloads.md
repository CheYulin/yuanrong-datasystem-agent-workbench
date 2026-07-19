# yche.me Downloads

This RFC uses sanitized/synthetic traces for public downloads. Real production
traces should not be published unless a separate sensitive-data review approves
the exact archive.

## Planned artifacts

- `ds-trace-triage-fixtures-20260720.tar.gz`
  - contains a minimal gzip-tar trace bundle;
  - covers access latency, worker aggregation, `ProcessGetObjectRequest`,
    rpc slow, `URMA_ELAPSED_TOTAL`, and `RPC deadline exceeded`;
  - suitable for parser self-training and manual smoke tests.

## Published URL

- https://yche.me/assets/downloads/ds-trace-triage-fixtures-20260720.tar.gz

## Validation checklist

- Archive can be downloaded over HTTPS: `HTTP/2 200`, content length `837`.
- Archive can be parsed by:

  ```bash
  python3 scripts/ds_trace_triage.py /tmp/ds-trace-triage-fixtures-20260720.live.tar.gz \
    --code-ref live-download \
    --output-json /tmp/ds-trace-triage-fixtures-20260720.live.json
  ```

- Live parse verification:

  ```text
  trace_count == 1
  errors["RPC deadline exceeded"] == 1
  breakdown_ms["ProcessGetObjectRequest"]["sum"] == 517.0
  ```
