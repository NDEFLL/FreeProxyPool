# 免费代理 IP 池

![Python](https://img.shields.io/badge/python-3.10%2B-blue)

从多个公开免费代理源采集代理，自动去重、并发校验、评分淘汰，并通过 HTTP API 提供可用代理。

> 免费代理存活时间短、成功率有限。本项目适合爬虫调试、低频采集和学习代理池原理，不建议用于生产环境。

## 功能

- 多源采集：内置 11 个免费代理源
- 并发校验：验证 HTTP / HTTPS 可用性并记录延迟
- 评分淘汰：成功加分，失败扣分，分数耗尽自动移出
- 加权取用：分数越高越容易被选中
- 自动刷新：启动后立即刷新，之后按固定间隔重复
- JSON 持久化：重启后保留代理、评分和失败记录
- 只读 API：`/get`、`/all`、`/stats`、`/health`
- 可作为 Python 库直接使用

## 项目结构

```text
proxy_pool/
├── run.py                 # 入口：启动刷新线程和 HTTP 服务
├── core/
│   ├── __init__.py        # 对外导出 ProxyPool / check_proxy
│   ├── config.py          # 配置
│   ├── sources.py         # 免费代理源采集
│   ├── validator.py       # 代理可用性校验
│   ├── pool.py            # 评分、淘汰、持久化、取用
│   ├── api.py             # 只读 HTTP API
│   └── utils.py           # 代理地址规范化
└── data/
    └── proxies.json       # 运行时生成
```

## 环境要求

- Python 3.10+
- requests

```bash
pip install requests
```

## 快速开始

```bash
cd proxy_pool
python run.py
```

启动后：

- API 地址：<http://127.0.0.1:5010>
- 后台线程立即执行一轮采集和校验
- 之后每隔 `REFRESH_INTERVAL` 秒刷新一次
- `Ctrl+C` 退出

首次启动时池子为空，需要等一轮刷新完成。期间 `/health` 可用，`/get` 会返回 `503`。

也可以从上一级目录运行：

```bash
python proxy_pool/run.py
```

Python 会自动把脚本所在目录加入 `sys.path`，因此 `run.py` 中的 `from core import ...` 可以正常工作。

## HTTP API

默认只监听 `127.0.0.1:5010`，只提供 GET，不做鉴权。

| 接口 | 说明 |
|---|---|
| `GET /` | 接口索引 |
| `GET /get` | 随机取代理，支持 `https=1`、`count=N`（最多 20） |
| `GET /all` | 按评分列出代理，支持 `limit=N`（最多 500） |
| `GET /stats` | 池统计：总数、HTTPS 数、平均延迟、更新时间 |
| `GET /health` | 健康检查 |

取 3 个支持 HTTPS 的代理：

```bash
curl "http://127.0.0.1:5010/get?https=1&count=3"
```

返回示例：

```json
{
  "count": 2,
  "proxies": [
    {
      "proxy": "1.2.3.4:8080",
      "url": "http://1.2.3.4:8080",
      "https": true,
      "latency": 320,
      "score": 84
    }
  ]
}
```

查看评分最高的 20 个：

```bash
curl "http://127.0.0.1:5010/all?limit=20"
```

查看统计：

```bash
curl "http://127.0.0.1:5010/stats"
```

```json
{
  "total": 394,
  "https": 47,
  "avg_latency_ms": 637,
  "updated_at": 1764000000
}
```

跨项目使用时，建议通过 HTTP API 取代理，避免处理 Python 包路径。

## 工作流程

```text
采集：sources.fetch_all()
  → 多源并发抓取
  → 单源限量、跨源去重
  → 排除池内已有和近期失败过的代理
  → validator.check_many() 并发校验
  → 成功：新增或加分
  → 失败：扣分，分数耗尽则淘汰
  → 按容量截断
  → 写入 data/proxies.json
```

取用：

```text
pool.pick()
  → 按 score 加权随机
  → 请求失败时 report_failure()
  → 扣分，分数耗尽则移出池
```

## 评分规则

| 事件 | 分数变化 |
|---|---|
| 新代理校验通过 | 基础分 60 + 成功 12 = 72 |
| 池内代理校验通过 | +12，最高 100 |
| 池内代理校验失败或运行中失败 | -30 |
| 新代理校验失败 | 不入池，记入 `rejected` |
| 分数 ≤ 0 | 移出代理池 |
| 取用 | 按分数加权随机 |

## 配置

配置文件：`core/config.py`

| 配置 | 默认值 | 说明 |
|---|---|---|
| `FETCH_TIMEOUT` | 15 | 单个代理源请求超时（秒） |
| `FETCH_WORKERS` | 6 | 同时抓取的源数量 |
| `MAX_PER_SOURCE` | 150 | 单个源最多保留多少条 |
| `MAX_NEW_PER_CYCLE` | 1500 | 每轮最多校验多少条新代理 |
| `VALIDATE_TIMEOUT` | 5 | 单个代理校验超时（秒） |
| `CHECK_WORKERS` | 100 | 并发校验线程数 |
| `HTTPS_CHECK_URL` | 百度 robots | HTTPS 代理校验地址 |
| `HTTP_CHECK_URL` | 百度 robots | HTTP 代理校验地址 |
| `CHECK_MARKER` | `Baiduspider` | 响应中必须包含的特征串 |
| `INIT_SCORE` | 60 | 基础分，新代理校验通过后为 72 |
| `MAX_SCORE` | 100 | 分数上限 |
| `SUCCESS_GAIN` | 12 | 每次校验成功加分 |
| `FAIL_PENALTY` | 30 | 每次失败扣分 |
| `MAX_POOL_SIZE` | 500 | 池容量 |
| `REJECT_TTL` | 1800 | 失败代理多久内不重复校验（秒） |
| `REFRESH_INTERVAL` | 600 | 自动刷新间隔（秒） |

默认端口和监听地址写在 `run.py` 的 `run_server(pool, host="127.0.0.1", port=5010)` 中，按需修改。

## 数据文件

`data/proxies.json` 首次刷新后自动生成，结构如下：

```json
{
  "updated_at": 1764000000,
  "proxies": [
    {
      "proxy": "1.2.3.4:8080",
      "https": true,
      "http": true,
      "latency": 320,
      "score": 84,
      "success": 5,
      "fail": 0,
      "last_checked": 1764000000
    }
  ],
  "rejected": {
    "5.6.7.8:9999": 1764000000
  }
}
```

- `score`：质量分，决定取用概率和容量截断优先级
- `latency`：最近一次校验延迟（毫秒）
- `success` / `fail`：累计成功、失败次数
- `rejected`：近期校验失败的代理及时间戳，避免重复校验

## 内置源

89ip、ip3366、站大爷、free-proxy-list、geonode、proxyscrape、openproxylist、proxyspace、speedx、monosans、scrape-center。

单个源失败只记录日志，不影响其他源。

## 常见问题

**`/get` 一直返回 503**

池子还没有可用代理。首次启动需要等一轮采集校验完成；也可以查看 `/stats` 确认数量。

**端口被占用**

修改 `run.py` 中的 `port=5010`，或者停止占用该端口的进程。

**代理很快失效**

免费代理本身存活时间短，尤其是 HTTPS 代理。可以缩短 `REFRESH_INTERVAL`，或改用付费代理。



## 已知限制

- 免费代理来源公开、质量参差不齐，成功率有限
- HTTPS 代理数量明显少于 HTTP 代理
- 校验结果是采集时的快照，代理可能在两次刷新之间失效
- 免费代理不可信，不要通过它传输账号密码等敏感信息
- 请遵守目标网站的服务条款和 robots 协议，控制请求频率

## 注意事项

本项目仅用于技术学习和交流。代理均来自公开免费源，请勿用于非法用途
免费代理IP稳定性和安全性较差，建议仅用于非敏感场景；生产环境推荐使用商业代理服务


