#!/usr/bin/env bash
set -euo pipefail
# This exact host transaction installs the source build prerequisites.
dnf install -y glog-devel gflags-devel jsoncpp-devel numactl-devel rdma-core-devel \
  boost1.78-devel openssl-devel yaml-cpp-devel libcurl-devel libzstd-devel \
  msgpack-devel libbsd-devel
