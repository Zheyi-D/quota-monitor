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
| `FEISHU_CHAT_ID` | 目标群聊 chat_id | 是（飞书） |
| `SMTP_USERNAME` | QQ 邮箱地址 | 是（邮件） |
| `SMTP_PASSWORD` | QQ 邮箱 SMTP 授权码 | 是（邮件） |
| `ENCRYPTION_KEY` | AES-256 加密密钥 | 是（加密） |
| `EMAIL_SUBSCRIBERS` | 手动订阅者 JSON 数组 | 否 |

3. 部署 Cloudflare Worker（`workers/subscribe.js`），用于网页自助订阅 + 飞书 DM 订阅 API
4. Settings → Pages → Source: GitHub Actions
5. 配置 cron-job.org 定时触发：
   - `fetch-quota`：每 2 分钟 POST（08:00-22:00）
   - `feishu-ws`：每 5 小时 POST（维持长连接）

### Cloudflare Worker

Worker 承担**邮箱订阅/退订**、**飞书 DM 订阅 API**、**管理员群发**三项功能，需配置以下环境变量：

| 变量 | 说明 |
|------|------|
| `GITHUB_TOKEN` | GitHub Personal Access Token（repo scope） |
| `GITHUB_REPO` | `Zheyi-D/quota-monitor` |
| `ENCRYPTION_KEY` | 与 GitHub Secrets 中相同的 AES 密钥 |
| `ADMIN_PASSWORD` | 管理员密码，用于 admin.html 验证 |
| `FEISHU_APP_ID` | 飞书自建应用 App ID（管理员群发用） |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret（管理员群发用） |
| `FEISHU_CHAT_ID` | 目标群聊 chat_id（管理员群发用） |

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
├── workers/subscribe.js    # Cloudflare Worker（邮件订阅 + 飞书 DM API + 管理员群发）
├── feishu-ws/              # 飞书长连接客户端
│   ├── feishu-ws-client.js # Node.js SDK WebSocket 客户端
│   └── package.json        # 依赖：@larksuiteoapi/node-sdk
├── monitor.py              # 快速启动脚本
├── web/                    # GitHub Pages 前端看板
├── .github/workflows/      # CI 工作流
│   ├── fetch.yml           # 配额检测 + 群聊/邮件/DM 通知
│   ├── feishu-ws.yml       # 飞书长连接客户端（每 5 小时）
│   ├── pages.yml           # Pages 部署
│   └── welcome.yml         # 欢迎邮件
├── data/                   # 自动生成的数据
│   ├── quota.json           # 配额快照
│   ├── run.log              # CI 运行日志（放号规律数据源）
│   ├── subscribers.json     # 邮箱订阅者（加密）
│   ├── feishu_subs.json     # 飞书 DM 订阅者（加密）
│   └── welcomed.json        # 已发欢迎邮件的订阅者（加密）
└── config.example.json     # 配置模板
```

---

## 🏗 数据流

```
cron-job.org
  ├── 每 2 分钟 POST fetch-quota（08:00-22:00）
  │     ↓
  │   GitHub Actions (ci_run.py)
  │     ├── fetch_snapshot() → 入境处公开 API
  │     ├── detect_changes() — 对比快照，检测 newly_available
  │     ├── 飞书群聊广播 → send_feishu_api() (receive_id_type=chat_id)
  │     ├── 邮件通知 → QQ SMTP (每人一封，含个性化退订链接)
  │     ├── 飞书私聊 DM → send_feishu_dm() (receive_id_type=open_id)
  │     │     └── 读 feishu_subs.json → 匹配日期 → 逐人发送
  │     ├── _append_run_log() → data/run.log (放号规律数据源)
  │     └── git push → Pages 自动部署
  │
  └── 每 5 小时 POST feishu-ws
        ↓
      GitHub Actions (feishu-ws-client.js)
        ├── @larksuiteoapi/node-sdk WSClient → 飞书 WebSocket 长连接
        ├── 收 im.message.receive_v1 → 解析文字命令/日期
        ├── 收 card.action.trigger → 处理按钮点击
        ├── Worker API → 读写 data/feishu_subs.json
        └── SDK Client → 发私聊卡片回复
```

---

## 前端

| 组件 | 技术 | 说明 |
|------|------|------|
| 看板页面 | 纯 HTML/CSS/JS | 零框架、零依赖，GitHub Pages 托管 |
| Tab 切换 | CSS class 切换 | 📊实时配额 / 📈放号规律 两个视图 |
| 配额表格 | 原生 DOM 渲染 | 96天 × 6 办事处全量渲染，CSS Grid 固定首列 |
| 放号热力图 | 原生 DOM 渲染 | 日 × 小时网格，颜色深浅 = 放号频率，8:00-22:00 |
| 数据加载 | Fetch API | 从 `data/quota.json` 读取，切 tab 时懒加载 |
| 放号规律数据 | 解析 `data/run.log` | regex: `ALERT \| 新配额放出: (\d+) 个` |
| 订阅/退订 | Fetch POST → Worker | 前端表单 → Cloudflare Worker → GitHub API |
| 管理员群发 | Fetch POST → Worker | admin.html 密码验证 → Worker 调飞书 API 发群消息 |
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
| `ci_run.py` | `_append_run_log()` | 通过 GitHub API 追加 CI 日志到 `data/run.log` |

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
- **加密范围**：`data/subscribers.json`、`data/welcomed.json`、`data/feishu_subs.json`
- **格式**：`{"enc": true, "data": "<base64(iv + ciphertext)>"}`
- **向后兼容**：读取时自动识别明文/密文格式，切换加密无需数据迁移

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
| 📊 热力图 | 日 × 小时（8:00-22:00）网格，颜色深浅 = 放号频率 |

所有渲染纯前端，零 API 依赖，切 tab 时懒加载。`run.log` 保留最近 200 行。

## 管理员群发（Admin Messaging）

`web/admin.html` — 密码保护的独立页面，管理员登录后可通过飞书机器人向群聊发送消息。密码验证基于 Cloudflare Worker `ADMIN_PASSWORD` 环境变量，24 小时内无需重复输入。

---

## 运行环境

| 环境 | 用途 |
|------|------|
| GitHub Actions (Ubuntu) | fetch-quota: 配额检测 + 通知 + 数据导出 + Pages 部署 |
| GitHub Actions (Ubuntu) | feishu-ws: 飞书长连接客户端，每 5 小时启动，接收私聊消息 |
| Cloudflare Workers | 订阅/退订/飞书 DM 订阅 API：接收前端请求 → 调 GitHub API 读写文件 |
| cron-job.org | 外部定时触发器，每 2 分钟 POST fetch-quota（08:00-22:00）+ 每 5 小时 POST feishu-ws |
| 本地 Python CLI | 开发者调试：`python monitor.py --once` |
