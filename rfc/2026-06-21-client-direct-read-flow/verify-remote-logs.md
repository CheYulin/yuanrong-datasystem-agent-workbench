# Remote full UT/ST verification logs

Background run on **tiantiyun-80c128g** (`/home/cache/`).

| Artifact | Path on remote |
|----------|----------------|
| Runner progress | `/home/cache/verify-logs/runner.out` |
| UT full log | `/home/cache/verify-logs/full_ut.log` |
| ST full log | `/home/cache/verify-logs/full_st.log` |
| UT failure names | `/home/cache/verify-logs/ut_failures.txt` |
| ST failure names | `/home/cache/verify-logs/st_failures.txt` |
| Summary (auto-updated) | `/home/cache/verify-logs/verify_summary.md` |
| CTest last failed | `/home/cache/verify-logs/ctest_LastTestsFailed.log` |

Re-run harness (records JUnit + failure lists):

```bash
bash yuanrong-datasystem-agent-workbench/scripts/testing/verify/run_full_ut_st_remote.sh --skip-rsync
```

Pull logs locally:

```bash
rsync -avz root@150.242.244.2:/home/cache/verify-logs/ ./verify-logs-$(date +%Y%m%d)/
```
