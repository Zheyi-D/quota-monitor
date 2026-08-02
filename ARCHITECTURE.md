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
| `SMTP_USERNAME` | QQ 邮箱地址 | 是（邮件） |
| `SMTP_PASSWORD` | QQ 邮箱 SMTP 授权码 | 是（邮件） |
| `ENCRYPTION_KEY` | AES-256 加密密钥 | 是（加密） |
| `EMAIL_SUBSCRIBERS` | 手动订阅者 JSON 数组 | 否 |

3. 部署 Cloudflare Worker（`workers/subscribe.js`），用于网页自助订阅 + 飞书 DM 订阅 API + 管理后台 API
4. Settings → Pages → Source: GitHub Actions
5. 配置 cron-job.org 定时触发：
   - `fetch-quota`：每 2 分钟 POST（08:00-24:00）
   - `feishu-ws`：每 5 小时 POST（维持长连接）

### Cloudflare Worker

Worker 承担**邮箱订阅/退订**、**飞书 DM 订阅 API**、**管理后台 API**、**管理员群发**四项功能，需配置以下环境变量：

| 变量 | 说明 |
|------|------|
| `GITHUB_TOKEN` | GitHub Personal Access Token（repo scope） |
| `GITHUB_REPO` | `Zheyi-D/quota-monitor` |
| `ENCRYPTION_KEY` | 与 GitHub Secrets 中相同的 AES 密钥 |
| `ADMIN_PASSWORD` | 管理后台密码 |
| `FEISHU_APP_ID` | 飞书自建应用 App ID |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret |
| `FEISHU_CHAT_ID` | 目标群聊 chat_id（支持逗号分隔多群） |

---

## 📁 项目结构

```
quota-monitor/
├── quota_monitor/          # Python 核心库
│   ├── core.py             # API 拉取 + 变化检测
│   ├── notify.py           # 飞书群聊/私聊 + 邮件通知
│   ├── state.py            # 状态持久化
│   └── monitor.py          # CLI 入口
├── ci_run.py               # CI 入口（配额检测 + 通知 + 欢迎邮件）
├── workers/subscribe.js    # Cloudflare Worker（邮件订阅 + DM API + 管理后台 API + 群发）
├── feishu-ws/              # 飞书长连接客户端
│   ├── feishu-ws-client.js # Node.js SDK WebSocket 客户端
│   └── package.json        # 依赖：@larksuiteoapi/node-sdk
├── monitor.py              # 快速启动脚本
├── web/                    # GitHub Pages 前端
│   ├── index.html          # 看板（配额表格 + 热力图）
│   ├── admin.html          # 管理后台（概览 + 订阅者 + 群发）
│   ├── app.js              # 看板逻辑
│   └── style.css           # 样式（暗色模式自适应）
├── .github/workflows/      # CI 工作流
│   ├── fetch.yml           # 配额检测 + 多通道通知 + Pages 后备部署
│   ├── feishu-ws.yml       # 飞书长连接客户端（每 5 小时）
│   ├── pages.yml           # Pages 部署（push 触发）
│   └── welcome.yml         # 欢迎邮件
├── data/                   # 自动生成的数据
│   ├── quota.json           # 配额快照
│   ├── run.log              # CI 运行日志（放号规律数据源，上限 10000 行）
│   ├── subscribers.json     # 邮箱订阅者（加密）
│   ├── feishu_subs.json     # 飞书 DM 订阅者（加密）
│   ├── feishu_subs_log.json # 飞书 DM 事件日志（加密）
│   └── welcomed.json        # 已发欢迎邮件的订阅者（加密）
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
  │     ├── 邮件通知 → QQ SMTP 逐封串行
  │     ├── state.json → GitHub API 直写（避免 push 不可靠导致重复通知）
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
| 订阅/退订 | Fetch POST → Worker | 前端表单 → Cloudflare Worker → GitHub API |
| 管理后台 | admin.html | 四 Tab 管理后台，Worker API 拉取解密数据 |
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
| `notify.py` | `send_email_smtp()` | Python `smtplib` → QQ SMTP，TLS 加密发送 |
| `state.py` | `load_state()` / `save_state()` | 快照持久化，原子写入防损坏 |
| `ci_run.py` | `_save_state_remote()` | GitHub API 直写 state.json，避免 push 不可靠 |
| `ci_run.py` | `_append_run_log()` | GitHub API 追加 CI 日志到 `data/run.log`（上限 10000 行） |

## 通知推送流程

CI 检测到 `newly_available` 后，按以下顺序并行化推送：

```
detect_changes() → has_significant_change() → format_changes()

  ├─ 1. 飞书群聊广播 → ThreadPoolExecutor 并行发送（多群同时，逗号分隔 FEISHU_CHAT_ID）
  │    └─ send_feishu_api() → receive_id_type=chat_id, msg_type=interactive

  ├─ 2. 飞书私聊 DM → ThreadPoolExecutor 并行发送（最多 5 并发）
  │    ├─ 读 data/feishu_subs.json → 提取 released_dates
  │    ├─ 遍历订阅者 → dates=[] 全量匹配 或 dates 与 released_dates 交集
  │    └─ send_feishu_dm() → receive_id_type=open_id, msg_type=interactive

  └─ 3. 邮件通知 → 逐封串行发送（SMTP 批量为避免被拦截，每封间隔 2s）
       └─ send_email_smtp() → QQ SMTP TLS 587
```

**设计要点**：
- DM 放在邮件**之前**，避免被 46+ 封 SMTP 邮件阻塞 20-30 秒
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

### CI 分发

`ci_run.py` 检测到 `newly_available` 后，提取放出日期集合，遍历 `feishu_subs.json`，匹配则调用 `send_feishu_dm()` 逐人推送。

## 邮件系统

- **发送方**：QQ 邮箱 SMTP（`smtp.qq.com:587`），使用授权码认证
- **渠道分流**：
  - 订阅确认：CI 首次检测到新订户 → 发 HTML 欢迎邮件 → 标记 `welcomed.json`
  - 配额通知：CI 检测到变化 → 发 HTML 通知邮件（含变化详情 + 二维码）
- **格式**：HTML 邮件，含看板/预约/飞书群入口链接、飞书群二维码、退订链接
- **隐私保护**：每封邮件末尾附带个性化退订链接，无需登录即可退订

## 加密存储

- **算法**：AES-256-GCM（Web Crypto API / Python `cryptography` 库）
- **密钥**：32 字节随机 base64 密钥，分别存入 GitHub Secrets 和 Cloudflare Worker Variables
- **加密范围**：`data/subscribers.json`、`data/welcomed.json`、`data/feishu_subs.json`、`data/feishu_subs_log.json`
- **格式**：`{"enc": true, "data": "<base64(iv + ciphertext)>"}`
- **向后兼容**：读取时自动识别明文/密文格式

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

`web/admin.html` — 四 Tab 管理后台，提供：

| Tab | 功能 |
|-----|------|
| 📊 概览 | 邮件/DM 订阅统计、今日新增/退订、历史累计（BJT 时区） |
| 📧 邮件订阅 | 明文查看/搜索/复制/导出/删除订阅者 |
| 💬 DM 订阅 | 查看/搜索/展开/删除 DM 订阅者，统计全部/特定日期分布 |
| 📢 群发 | 群聊广播（多群并发）+ 私聊群发（串行 0.5s/人） |

### 后台 API（Worker `/api/admin/*`）

| 端点 | 用途 |
|------|------|
| `POST /api/admin/stats` | 返回订阅统计（邮件数、DM 活跃/新增/退订/历史累计） |
| `POST /api/admin/subscribers` | 返回解密后的邮件列表 |
| `POST /api/admin/dm-subscribers` | 返回解密后的 DM 订阅列表 |
| `POST /api/admin/dm-send` | 私聊群发给所有 DM 订阅者 |

所有 admin API 需密码验证。统计使用 **BJT（UTC+8）** 过滤今日数据。

---

## 运行环境

| 环境 | 用途 |
|------|------|
| GitHub Actions (Ubuntu) | fetch-quota: 配额检测 + 通知 + 数据导出 + Pages 后备部署 |
| GitHub Actions (Ubuntu) | feishu-ws: 飞书长连接客户端，每 5 小时启动，接收私聊消息 |
| Cloudflare Workers | 订阅/退订/飞书 DM API/管理后台 API：接收请求 → 调 GitHub API 读写文件 |
| cron-job.org | 外部定时触发器，每 2 分钟 POST fetch-quota（08:00-24:00）+ 每 5 小时 POST feishu-ws |
| 本地 Python CLI | 开发者调试：`python monitor.py --once` |
