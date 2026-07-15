# Shared gtest filters — SSD→HBM direct (focused, no full ST sweep)

# Gate 0: binmock MSetD2H/MGetH2D baseline only (5 cases).
# Excludes HeteroD2HTestEvcit (spill) and HeteroD2HThroughTcpTest (2-worker TCP).
export GATE0_GTEST_FILTER="${GATE0_GTEST_FILTER:-\
HeteroD2HTest.Perf:\
HeteroD2HTest.TestNoExist:\
HeteroD2HTest.TestAllExist:\
HeteroD2HTest.TestPartExist:\
HeteroD2HTest.TestMSetD2HMsgWithInvalidDeviceId}"

# Track① UT: Tasks 1–3
export NDS_UT_GTEST_FILTER="${NDS_UT_GTEST_FILTER:-\
AlignmentGateTest.*:\
MockIpcHbmBackendTest.*:\
FakeNdsSpillReaderTest.*:\
HbmMappingTableTest.*:\
NdsDirectPathTest.*}"

# Task 6 e2e (when NdsBinmockFlow lands) + minimal Hetero regression
export BINMOCK_FLOW_GTEST_FILTER="${BINMOCK_FLOW_GTEST_FILTER:-\
NdsBinmockFlow*:\
NdsClusterSpillRwTest.*:\
HeteroD2HTest.TestAllExist}"
