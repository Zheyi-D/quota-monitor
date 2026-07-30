# 🏗 技术架构 · Architecture

> 面向开发者和自部署用户。普通用户请查看 [README.md](README.md)。

---

## 🔧 开发者自部署

### 前置要求

- Python 3.8+
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
| `FEISHU_APP_ID` | 飞书自建应用 App ID | 否 |
| `FEISHU_APP_SECRET` | 飞书自建应用 App Secret | 否 |
| `FEISHU_CHAT_ID` | 目标群聊 chat_id | 否 |
| `SMTP_USERNAME` | QQ 邮箱地址 | 是（邮件） |
| `SMTP_PASSWORD` | QQ 邮箱 SMTP 授权码 | 是（邮件） |
| `ENCRYPTION_KEY` | AES-256 加密密钥 | 是（加密） |
| `EMAIL_SUBSCRIBERS` | 手动订阅者 JSON 数组 | 否 |

3. 部署 Cloudflare Worker（`workers/subscribe.js`），用于网页自助订阅
4. Settings → Pages → Source: GitHub Actions
5. 配置外部定时服务（如 cron-job.org）每 5 分钟触发 `repository_dispatch`

### Cloudflare Worker

Worker 同时承担 **邮箱自助订阅** 和 **管理员群发消息** 功能，需配置以下环境变量：

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
│   ├── notify.py           # 飞书 + 邮件通知
│   ├── state.py            # 状态持久化
│   └── monitor.py          # CLI 入口
├── ci_run.py               # CI 入口（配额检测 + 欢迎邮件）
├── workers/subscribe.js    # Cloudflare Worker（订阅/退订）
├── monitor.py              # 快速启动脚本
├── web/                    # GitHub Pages 前端看板
├── .github/workflows/      # CI 工作流
│   ├── fetch.yml           # 配额检测 + 通知
│   ├── pages.yml           # Pages 部署
│   └── welcome.yml         # 欢迎邮件
├── data/                   # 自动生成的数据
│   ├── quota.json           # 配额快照
│   ├── release_log.json     # 放号记录（60 天）
│   ├── subscribers.json     # 邮箱订阅者（加密）
│   └── welcomed.json        # 已发欢迎邮件的订阅者（加密）
└── config.example.json     # 配置模板
```

---

## 🏗 数据流

```
cron-job.org (每5分钟 POST)
       │
       ▼
GitHub API (repository_dispatch)
       │
       ▼
GitHub Actions (ci_run.py)
       │
       ├──► requests → 入境处公开 API
       │
       ├──► detect_changes() — 对比两次快照，检测变化：
       │       ① 已满/no-quota → 有名额/少量  (newly_available，触发通知)
       │       ② 有名额 → 已满  (newly_full)
       │       ③ 新日期进入窗口  (newly_added，不计入通知)
       │
       ├──► SHA256 去重检查 — 仅基于 newly_available 指纹
       │
       ├──► 飞书群通知 — 自建应用 API (tenant_access_token + IM message)
       ├──► 邮件通知 — QQ SMTP (smtplib, TLS 587)
       ├──► 追加放号记录 — data/release_log.json，保留 60 天
       └──► 导出 quota.json + 部署 GitHub Pages
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
| `notify.py` | `send_email_smtp()` | Python `smtplib` → QQ SMTP，TLS 加密发送 |
| `notify.py` | `_can_send()` / `_record_sent()` | 频率控制：飞书 10 分钟、邮件 30 分钟最小间隔 |
| `state.py` | `load_state()` / `save_state()` | 快照持久化，原子写入防损坏 |
| `ci_run.py` | `_read_notify_marker()` / `_write_notify_marker()` | SHA256 去重标记，通过 GitHub API 读写，避开 git push 不可靠问题 |

## 邮件系统

- **发送方**：QQ 邮箱 SMTP（`smtp.qq.com:587`），使用授权码认证，非明文密码
- **渠道分流**：
  - 订阅确认：CI 首次检测到新订户 → 发 HTML 欢迎邮件 → 标记 `welcomed.json`
  - 配额通知：CI 检测到变化 → 发 HTML 通知邮件（含变化详情 + 二维码）
  - 本地运行：`monitor.py` 使用 agently-cli 发送，CI 使用 SMTP
- **格式**：HTML 邮件，含看板/预约/飞书群入口链接、飞书群二维码、退订链接
- **隐私保护**：每封邮件末尾附带个性化退订链接，无需登录即可退订

## 加密存储

- **算法**：AES-256-GCM（Web Crypto API / Python `cryptography` 库）
- **密钥**：32 字节随机 base64 密钥，分别存入 GitHub Secrets 和 Cloudflare Worker Variables
- **加密范围**：`data/subscribers.json`、`data/welcomed.json`
- **格式**：`{"enc": true, "data": "<base64(iv + ciphertext)>"}`
- **向后兼容**：读取时自动识别明文/密文格式，切换加密无需数据迁移

## 去重机制

为避免同一变化被重复发送多次，系统使用 **SHA256 内容指纹 + GitHub API 标记文件** 实现去重：

```
检测到配额变化 → 提取变化数据本体 → SHA256("变化内容") → 得到 16 位指纹
                                                          │
                              比对 .github/notify_marker ─┤
                                                          │
                                相同                       不同
                                 ↓                         ↓
                            跳过通知                   发送通知
                                                    写入新指纹到 notify_marker
```

**关键设计**：
- **指纹仅基于 `newly_available`**，而非完整通知消息，避免时间戳导致每次 hash 不同。`newly_added`（新日期）不触发通知
- **使用 GitHub REST API 读写标记**（`GET/PUT repos/:owner/:repo/contents/.github/notify_marker`），不依赖 `git push`，彻底避开 SSL 连接不稳定导致的推送失败
- 标记文件存储在 `.github/` 目录下，内容为单行 16 位 hex 指纹

## 放号规律（Release Trend）

每次 CI 检测到 `newly_available` 变化时，追加一条记录到 `data/release_log.json`：

```json
{"t":"2026-07-30T09:31:00+08:00","count":3,"dates":["08/05/2026","08/06/2026","08/07/2026"]}
```

- 仅记录「已满 → 有名额/少量」的真正放号，每日零点自动滚动的新日期不计入
- 同一次 CI 的所有 `newly_available` 算作一批（`count` = 该批放出日期数）
- 保留 60 天，预估大小 ~90 KB

前端「📈 放号规律」tab 读取此文件，渲染三个视图：

| 视图 | 说明 |
|------|------|
| ⏱️ 上次放号时间 | 最近一次放号的具体时间 |
| 🔥 TOP 3 时段 | 累计放号日期数最多的三个时段 |
| 📊 热力图 | 日 × 小时（8:00-22:00）网格，颜色深浅 = 放号频率 |

所有渲染纯前端，零 API 依赖，切 tab 时懒加载。

## 管理员群发（Admin Messaging）

`web/admin.html` — 密码保护的独立页面，管理员登录后可通过飞书机器人向群聊发送消息。密码验证基于 Cloudflare Worker `ADMIN_PASSWORD` 环境变量，24 小时内无需重复输入。

---

## 运行环境

| 环境 | 用途 |
|------|------|
| GitHub Actions (Ubuntu) | 主 CI：配额检测 + 通知 + 数据导出 + Pages 部署 |
| Cloudflare Workers | 订阅/退订 API：接收前端请求 → 调 GitHub API 读写文件 |
| cron-job.org | 外部定时触发器，每 2 分钟 POST → `repository_dispatch`（08:00-22:00） |
| 本地 Python CLI | 开发者调试：`python monitor.py --once` |
