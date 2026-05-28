# Clash Meta 部署配置

## 概述

本服务器运行 Clash Meta 代理服务，提供规则分流：
- **国内 IP** → DIRECT（直连）
- **海外流量**（Google、GitHub、OpenAI、Anthropic 等）→ ZCSSR 代理组（默认美国节点）
- TUN 模式开启后，**服务器本身所有流量**走 Clash 分流规则

## 服务架构

```
Clash Meta (mihomo)
├── 配置文件: /root/clash-dashboard/mihomo.yaml
├── TUN配置:  /root/clash-dashboard/mihomo-tun.yaml
├── 控制脚本: /root/clash-dashboard/clash-ctl
├── Web Dashboard: Flask @ :9091 (clash-dash.service)
└── 代理端口: mixed-port 7890
```

## 快速命令

```bash
# 查看 TUN 状态
/root/clash-dashboard/clash-ctl status

# 开启 TUN（服务器全局代理，用于 Claude Code/GitHub CLI）
/root/clash-dashboard/clash-ctl on

# 关闭 TUN
/root/clash-dashboard/clash-ctl off

# 重启 Clash Meta
systemctl restart clash-meta

# 查看运行状态
systemctl status clash-meta

# 查看日志
journalctl -u clash-meta -f
```

## TUN 模式说明

| 状态 | 适用场景 |
|------|----------|
| **OFF**（默认） | 仅 HTTP/SOCKS 代理，Dashboard 管理，规则分流 |
| **ON** | 服务器本身所有流量走代理（如 Claude Code、GitHub CLI） |

开启 TUN 后：
- 服务器所有出站流量按规则分流
- Claude Code / Codex 等工具直连海外 API 自动走 ZCSSR 代理组（美国节点）
- 国内网站流量走 DIRECT，不影响访问

## 配置文件说明

| 文件 | 用途 |
|------|------|
| `mihomo.yaml` | 默认配置，TUN 关闭 |
| `mihomo-tun.yaml` | TUN 开启配置（auto-route: true） |

两个文件除了 `tun.enable` 不同外，其他配置完全一致。

## Dashboard

- 地址: `http://<服务器IP>:9091`
- 登录: `cyl191836400` / `02170903C`
- 功能: 节点选择、延迟测试、模式切换（规则/全局/直连）、配置上传

## 规则分流（ZCSSR 代理组）

已配置规则示例：
- `DOMAIN-KEYWORD,google` → ZCSSR
- `DOMAIN-KEYWORD,anthropic` → ZCSSR
- `DOMAIN-KEYWORD,github` → ZCSSR
- `GEOIP,CN` → DIRECT
- `MATCH` → ZCSSR（默认海外流量走代理）

## 注意事项

1. **TUN 需重启服务**：切换 TUN 状态会执行 `systemctl restart clash-meta`，有短暂中断
2. **Dashboard 不受 TUN 影响**：Dashboard 始终在 `:9091` 可访问
3. **mixed-port 7890**：同时提供 HTTP 和 SOCKS5 代理
4. **配置更新**：通过 Dashboard 上传新配置会覆盖 `mihomo.yaml`，不会影响 `mihomo-tun.yaml`

## 故障排查

```bash
# 确认端口监听
ss -tlnp | grep mihomo

# 测试代理连通性（服务器本地）
curl -x http://127.0.0.1:7890 https://www.google.com/generate_204

# 查看 TUN 网卡是否创建
ip link show | grep clash
```
