# 🏗 技术架构 · Architecture

> 面向开发者和自部署用户。普通用户请查看 [README.md](README.md)。

---

## 🔧 开发者自部署

### 前置要求

- Python 3.8+
- Node.js 20+
- GitHub 账号

### 安装

```bash
git clone https://github.com/Zheyi-D/quota-monitor.git
cd quota-monitor
pip install -e .
```

### 配置

```bash
cp config.example.json config.json
# 编辑 config.json，填入飞书应用凭据或 webhook URL
```

### 运行

```bash
# 本地持续监控
python monitor.py --interval 600

# 单次测试
python monitor.py --once

# CI 模式
python ci_run.py
```

### GitHub Actions 部署

1. Fork 本仓库
2. Settings → Secrets and variables → Actions 中添加：

| Secret | 说明 | 必填 |
|--------|------|------|
| `FEISHU_APP_ID` | 飞书自建应用 App ID | 是（飞书） |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret | 是（飞书） |
| `FEISHU_CHAT_ID` | 目标群聊 chat_id（支持逗号分隔多群） | 是（飞书） |
| `ENCRYPTION_KEY` | AES-256 加密密钥 | 是（加密） |

3. 部署 Cloudflare Worker（`workers/subscribe.js`），用于飞书 DM 订阅 API + 管理后台 API + 管理员群发
4. Settings → Pages → Source: GitHub Actions
5. 配置 cron-job.org 定时触发：
   - `fetch-quota`：每 2 分钟 POST（08:00-24:00）
   - `feishu-ws`：每 5 小时 POST（维持长连接）

### Cloudflare Worker

Worker 承担**飞书 DM 订阅 API**、**管理后台 API**、**管理员群发**三项功能，需配置以下环境变量：

| 变量 | 说明 |
|------|------|
| `GITHUB_TOKEN` | **Fine-grained PAT**：仅本仓库、Contents Read+Write（不含 Workflows），详见下方安全配置指引 |
| `GITHUB_REPO` | `Zheyi-D/quota-monitor` |
| `ENCRYPTION_KEY` | 与 GitHub Secrets 中相同的 AES 密钥 |
| `ADMIN_PASSWORD` | 管理后台密码（仅在 `/api/admin/login` 使用） |
| `ADMIN_TOKEN_SECRET` | HMAC-SHA256 签名密钥（32 字节随机字符串，与 `ADMIN_PASSWORD` 独立配置） |
| `FEISHU_APP_ID` | 飞书自建应用 App ID |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret |
| `FEISHU_CHAT_ID` | 目标群聊 chat_id（支持逗号分隔多群） |

### GitHub Token 安全配置指引

**必须使用 GitHub Fine-grained Personal Access Token**，不要使用 classic PAT（`repo` scope 权限过宽）。

创建方式：
1. GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token
2. Repository access：仅选中本仓库（`Zheyi-D/quota-monitor`）
3. Permissions：只勾选 **Contents: Read and write**
4. **明确不要勾选 Workflows 权限** — 这样即使 token 泄露，GitHub 也会拒绝对 `.github/workflows/*.yml` 的写入（403），阻断"Worker 被攻破 → 篡改 CI 工作流 → 供应链攻击"路径

> 如果未来订阅者数量增长、希望进一步降低风险，可以将 `data/` 目录迁移到独立仓库（可设为 private），Worker 的 token 只指向数据仓库，与存放代码和 CI 工作流的仓库完全隔离。

### Admin 认证机制

Admin 后台已改为 **HMAC-SHA256 Token 认证**：
- 用户凭密码调用 `POST /api/admin/login` 获取 2 小时有效期的 signed token
- 后续所有 admin 接口通过 `Authorization: Bearer <token>` 鉴权
- 密码不再随每次请求明文传输，也不存储在 localStorage 中

---

## 📁 项目结构

```
quota-monitor/
├── quota_monitor/          # Python 核心库
│   ├── core.py             # API 拉取 + 变化检测
│   ├── notify.py           # 飞书群聊 + 私聊通知
│   ├── state.py            # 状态持久化
│   └── monitor.py          # CLI 入口
├── ci_run.py               # CI 入口（配额检测 + 群聊/DM 通知）
├── workers/subscribe.js    # Cloudflare Worker（DM API + 管理后台 API + 群发）
├── feishu-ws/              # 飞书长连接客户端
│   ├── feishu-ws-client.js # Node.js SDK WebSocket 客户端
│   └── package.json        # 依赖：@larksuiteoapi/node-sdk
├── monitor.py              # 快速启动脚本
├── web/                    # GitHub Pages 前端
│   ├── index.html          # 看板（配额表格 + 热力图 + 飞书 CTA）
│   ├── admin.html          # 管理后台（概览 + DM 订阅者 + 群发）
│   ├── app.js              # 看板逻辑
│   └── style.css           # 样式（暗色模式自适应）
├── .github/workflows/      # CI 工作流
│   ├── fetch.yml           # 配额检测 + 群聊/DM 通知 + Pages 后备部署
│   ├── feishu-ws.yml       # 飞书长连接客户端（每 5 小时）
│   └── pages.yml           # Pages 部署（push 触发）
├── data/                   # 自动生成的数据
│   ├── quota.json           # 配额快照
│   ├── run.log              # CI 运行日志（放号规律数据源，上限 10000 行）
│   ├── feishu_subs.json     # 飞书 DM 订阅者（加密）
│   └── feishu_subs_log.json # 飞书 DM 事件日志（加密）
└── config.example.json     # 配置模板
```

---

## 🏗 数据流

```
cron-job.org
  ├── 每 2 分钟 POST fetch-quota（08:00-24:00）
  │     ↓
  │   GitHub Actions (ci_run.py)
  │     ├── fetch_snapshot() → 入境处公开 API
  │     ├── detect_changes() — 对比快照，检测 newly_available
  │     ├── 飞书群聊广播 → ThreadPoolExecutor 并行（多群逗号分隔）
  │     ├── 飞书私聊 DM → ThreadPoolExecutor 并行（最多 5 并发）
  │     │     └── 读 feishu_subs.json → 匹配日期 → 逐人发送
  │     ├── state.json → GitHub API 直写
  │     ├── _append_run_log() → data/run.log（上限 10000 行）
  │     ├── Pages 后备部署（push 失败时仍能推送最新数据）
  │     └── git push → 触发 pages.yml → Pages 部署
  │
  └── 每 5 小时 POST feishu-ws
        ↓
      GitHub Actions (feishu-ws-client.js)
        ├── @larksuiteoapi/node-sdk WSClient → 飞书 WebSocket 长连接
        ├── 收 im.message.receive_v1 → 解析文字命令/日期
        ├── 收 card.action.trigger → 处理按钮点击
        ├── Worker API → 读写 data/feishu_subs.json + 追加 feishu_subs_log.json
        └── SDK Client → 发私聊卡片回复
```

---

## 前端

| 组件 | 技术 | 说明 |
|------|------|------|
| 看板页面 | 纯 HTML/CSS/JS | 零框架、零依赖，GitHub Pages 托管 |
| Tab 切换 | CSS class 切换 | 📊实时配额 / 📈放号规律 两个视图 |
| 配额表格 | 原生 DOM 渲染 | 96天 × 6 办事处全量渲染，CSS Grid 固定首列 |
| 放号热力图 | 原生 DOM 渲染 | 日 × 小时网格，颜色深浅 = 放号频率，8:00-23:00 |
| 数据加载 | Fetch API | 从 `data/quota.json` 读取，切 tab 时懒加载 |
| 放号规律数据 | 解析 `data/run.log` | regex: `ALERT \| 新配额放出: (\d+) 个` |
| 飞书 CTA | 醒目公告栏 | 引导用户加入飞书群或私聊机器人 |
| 管理后台 | admin.html | 三 Tab 管理后台，Worker API 拉取解密数据 |
| 暗色模式 | `prefers-color-scheme` | CSS 变量自动适配，零 JS |
| 更新时间 | `data/last_update.json` | 显示 CI 最后一次抓取的北京时间 |

## 后端 (Python CI)

| 模块 | 核心函数 | 说明 |
|------|---------|------|
| `core.py` | `fetch_snapshot()` | 拉取入境处 API，返回 `{(date, office, type): status}` 字典 |
| `core.py` | `detect_changes()` | 等级值比较：quota-g=1, quota-y=2, quota-r=3, no-quota=4 |
| `core.py` | `export_web_data()` | 导出 `data/quota.json` 供前端读取 |
| `notify.py` | `send_feishu_api()` | 飞书 Open API：获取 token → POST 消息卡片到群聊 |
| `notify.py` | `send_feishu_dm()` | 飞书 Open API：获取 token → POST 消息卡片到私聊 (receive_id_type=open_id) |
| `state.py` | `load_state()` / `save_state()` | 快照持久化，原子写入防损坏 |
| `ci_run.py` | `_save_state_remote()` | GitHub API 直写 state.json，避免 push 不可靠 |
| `ci_run.py` | `_append_run_log()` | GitHub API 追加 CI 日志到 `data/run.log`（上限 10000 行） |

## 通知推送流程

CI 检测到 `newly_available` 后，按以下顺序并行化推送：

```
detect_changes() → has_significant_change() → format_changes()

  ├─ 1. 飞书群聊广播 → ThreadPoolExecutor 并行发送（多群同时，逗号分隔 FEISHU_CHAT_ID）
  │    └─ send_feishu_api() → receive_id_type=chat_id, msg_type=interactive

  └─ 2. 飞书私聊 DM → ThreadPoolExecutor 并行发送（最多 5 并发）
       ├─ 读 data/feishu_subs.json → 提取 released_dates
       ├─ 遍历订阅者 → dates=[] 全量匹配 或 dates 与 released_dates 交集
       └─ send_feishu_dm() → receive_id_type=open_id, msg_type=interactive
```

**设计要点**：
- 群聊和 DM 各自使用 `ThreadPoolExecutor` 并行，几乎同时抵达
- 单个群/DM 发送失败不中止其余发送

## 飞书 DM 按日期过滤

feishu-ws-client.js 使用飞书官方 Node.js SDK 的 `WSClient` 建立 WebSocket 长连接，在 GitHub Actions 中持续运行（每 5 小时 cron-job.org 定时重启，timeout 5.5 小时保证无缝衔接）。

### 交互方式

- **接收消息**：`im.message.receive_v1` — 解析文字命令/日期输入
- **卡片按钮**：`card.action.trigger` — 处理交互卡片按钮点击
- **发送回复**：SDK `client.im.message.create()`，`receive_id_type=open_id`

### 数据存储

订阅偏好加密存储在 `data/feishu_subs.json`（Worker REST API 读写）：

```json
[{"open_id": "ou_xxx", "dates": ["08/15/2026", ...], "subscribed_at": "..."}]
```

- `dates: []` → 全量通知
- `dates: ["08/15/2026", ...]` → 仅匹配时通知

### 事件日志

每次订阅/退订同时追加一条事件到 `data/feishu_subs_log.json`（加密），用于管理后台统计每日新增/退订/历史累计。日志保留最近 500 条。

## 放号规律（Release Trend）

CI 检测到 `newly_available` 变化时，通过 GitHub API 追加到 `data/run.log`：

```
[2026-07-31 11:24:30 BJT] ALERT | 新配额放出: 8 个
```

前端「📈 放号规律」tab fetch `data/run.log`，用 regex 解析放号批次：

| 视图 | 说明 |
|------|------|
| ⏱️ 上次放号时间 | 最近一次放号的具体时间 |
| 🔥 TOP 3 时段 | 累计放号日期数最多的三个时段 |
| 📊 热力图 | 日 × 小时（8:00-23:00）网格，颜色深浅 = 放号频率 |

所有渲染纯前端，零 API 依赖，切 tab 时懒加载。`run.log` 保留最近 10000 行。

## 管理后台（Admin v2）

`web/admin.html` — 三 Tab 管理后台，提供：

| Tab | 功能 |
|-----|------|
| 📊 概览 | DM 订阅统计、今日新增/退订、历史累计（BJT 时区） |
| 💬 DM 订阅 | 查看/搜索/展开/删除 DM 订阅者，统计全部/特定日期分布 |
| 📢 群发 | 群聊广播（多群并发）+ 私聊群发（串行 0.5s/人） |

所有 Admin API 通过 `Authorization: Bearer <token>` 鉴权（HMAC-SHA256 签名，有效期 2 小时），登录端点除外。

### 后台 API（Worker `/api/admin/*`）

| 端点 | 用途 |
|------|------|
| `POST /api/admin/login` | 密码登录，返回 signed token（2h 有效期） |
| `POST /api/admin/stats` | 返回 DM 订阅统计（活跃/新增/退订/历史累计） |
| `POST /api/admin/dm-subscribers` | 返回解密后的 DM 订阅列表 |
| `POST /api/admin/dm-send` | 私聊群发给所有 DM 订阅者（串行 0.5s/人） |

统计使用 **BJT（UTC+8）** 过滤今日数据。

## 加密存储

- **算法**：AES-256-GCM（Web Crypto API / Python `cryptography` 库）
- **密钥**：32 字节随机 base64 密钥，分别存入 GitHub Secrets 和 Cloudflare Worker Variables
- **加密范围**：`data/feishu_subs.json`、`data/feishu_subs_log.json`
- **格式**：`{"enc": true, "data": "<base64(iv + ciphertext)>"}`
- **向后兼容**：读取时自动识别明文/密文格式

---

## 运行环境

| 环境 | 用途 |
|------|------|
| GitHub Actions (Ubuntu) | fetch-quota: 配额检测 + 群聊/DM 通知 + 数据导出 + Pages 后备部署 |
| GitHub Actions (Ubuntu) | feishu-ws: 飞书长连接客户端，每 5 小时启动，接收私聊消息 |
| Cloudflare Workers | 飞书 DM API/管理后台 API/群发：接收请求 → 调 GitHub API 读写文件 |
| cron-job.org | 外部定时触发器，每 2 分钟 POST fetch-quota（08:00-24:00）+ 每 5 小时 POST feishu-ws |
| 本地 Python CLI | 开发者调试：`python monitor.py --once` |
