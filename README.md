# 🪪 香港入境处预约配额监控

实时追踪香港入境事务处**换领身份证**预约配额，新名额放出时**飞书群 + 邮件**自动通知。

> **✨ 无需懂代码** — 打开网页看板即可查询，或填写邮箱自助订阅通知。

---

## 快速开始

### 📱 方式一：加入飞书群（推荐）

> 📱 [点击加入飞书群](https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=ff3i6631-016b-40cc-989e-e4651ccd353c)

机器人自动推送配额变化，加群即用，无需任何配置。

### 📧 方式二：邮件订阅（推荐）

在看板页面输入邮箱点击「订阅」，配额放出时自动收到通知邮件，每封邮件附带一键退订链接。

> ⚠️ **中国内地用户请注意**：邮件订阅/退订功能依赖 Cloudflare Worker，在内地需科学上网才能提交。订阅成功后**接收邮件不受影响**，无需持续 VPN。飞书群通知无此限制，内地用户推荐优先使用飞书群。

### 🖥 方式三：打开网页看板

> 🖥 **[quota-monitor 看板](https://Zheyi-D.github.io/quota-monitor)**

- 📊 实时查看 6 个办事处的预约配额状态
- 📖 附无 e-visa 电话预约办理教程

---

## 📊 涵盖办事处

| 代码 | 名称 |
|------|------|
| FTO | 火炭办事处 |
| RHK | 港岛办事处 |
| RKO | 九龙办事处 |
| RTK | 将军澳办事处 |
| TMO | 屯门办事处 |
| YLO | 元朗办事处 |

---

## 📖 无 e-visa 电话预约教程

看板页面已内置完整教程（点击「📖 港硕🇭🇰无学签身份证提前预约办理教程」展开），无需学签即可通过电话预约办理。

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

网页自助订阅功能依赖 Cloudflare Worker，需额外配置以下环境变量：

| 变量 | 说明 |
|------|------|
| `GITHUB_TOKEN` | GitHub Personal Access Token（repo scope） |
| `GITHUB_REPO` | `Zheyi-D/quota-monitor` |
| `ENCRYPTION_KEY` | 与 GitHub Secrets 中相同的 AES 密钥 |

---

## 📁 项目结构

```
quota-monitor/
├── quota_monitor/          # Python 核心库
│   ├── core.py             # API 拉取 + 变化检测
│   ├── notify.py           # 飞书 + 邮件通知
│   ├── state.py            # 状态持久化
│   └── monitor.py          # CLI 入口
├── ci_run.py               # CI 入口（配额检测）
├── welcome_runner.py       # CI 入口（欢迎邮件）
├── workers/subscribe.js    # Cloudflare Worker（订阅/退订）
├── monitor.py              # 快速启动脚本
├── web/                    # GitHub Pages 前端看板
├── data/                   # 自动生成的数据
├── .github/workflows/      # CI 工作流
└── config.example.json     # 配置模板
```

---

## 🏗 技术架构

### 数据流

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
       ├──► detect_changes() — 对比两次快照，检测 2 种变化：
       │       ① 已满 → 有名额  (newly_available)
       │       ② 新日期进入窗口  (newly_added)
       │
       ├──► SHA256 去重检查 — 比对 .github/notify_marker，相同内容不重复发送
       │
       ├──► 飞书群通知 — 自建应用 API (tenant_access_token + IM message)
       ├──► 邮件通知 — QQ SMTP (smtplib, TLS 587)
       └──► 导出 quota.json + 部署 GitHub Pages
```

### 前端

| 组件 | 技术 | 说明 |
|------|------|------|
| 看板页面 | 纯 HTML/CSS/JS | 零框架、零依赖，GitHub Pages 托管 |
| 数据加载 | Fetch API | 从 `data/quota.json` 读取，无后端 |
| 配额表格 | 原生 DOM 渲染 | 96天 × 6 办事处全量渲染，CSS Grid 固定首列 |
| 无极滚动 | `overflow-x: auto` | 触屏 + 鼠标滚轮自由拖动，自动滚到今天 |
| 订阅/退订 | Fetch POST → Worker | 前端表单 → Cloudflare Worker → GitHub API |
| 暗色模式 | `prefers-color-scheme` | CSS 变量自动适配，零 JS |
| 更新时间 | `data/last_update.json` | 显示 CI 最后一次抓取的北京时间 |

### 后端 (Python CI)

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

### 邮件系统

- **发送方**：QQ 邮箱 SMTP（`smtp.qq.com:587`），使用授权码认证，非明文密码
- **订阅管理**：用户于前端提交邮箱 → Cloudflare Worker 加密写入 `data/subscribers.json`
- **欢迎邮件**：独立 workflow `welcome.yml`，检测新人 → 发确认邮件 → 标记 `welcomed.json`
- **退订**：邮件底部的退订链接点击即退，Worker 同时清理 `subscribers.json` + `welcomed.json`
- **隐私保护**：每封邮件末尾附带个性化退订链接，无需登录即可退订

### 加密存储

- **算法**：AES-256-GCM（Web Crypto API / Python `cryptography` 库）
- **密钥**：32 字节随机 base64 密钥，分别存入 GitHub Secrets 和 Cloudflare Worker Variables
- **加密范围**：`subscribers.json`、`welcomed.json`
- **格式**：`{"enc": true, "data": "<base64(iv + ciphertext)>"}`
- **向后兼容**：读取时自动识别明文/密文格式，切换加密无需数据迁移

### 去重机制

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
- **指纹基于变化数据本身**（`newly_available` + `newly_added` 的 JSON），而非完整通知消息，避免时间戳导致每次 hash 不同
- **使用 GitHub REST API 读写标记**（`GET/PUT repos/:owner/:repo/contents/.github/notify_marker`），不依赖 `git push`，彻底避开 SSL 连接不稳定导致的推送失败
- 标记文件存储在 `.github/` 目录下，内容为单行 16 位 hex 指纹

### 运行环境

| 环境 | 用途 |
|------|------|
| GitHub Actions (Ubuntu) | 主 CI：配额检测 + 通知 + 数据导出 + Pages 部署 |
| Cloudflare Workers | 订阅/退订 API：接收前端请求 → 调 GitHub API 读写文件 |
| cron-job.org | 外部定时触发器，每 5 分钟 POST → `repository_dispatch` |
| 本地 Python CLI | 开发者调试：`python monitor.py --once` |

---

## 🔒 隐私与安全

- 订阅者邮箱使用 **AES-256-GCM 加密存储**，仓库中不可读
- 仅读取入境处**公开发布**的配额数据
- 邮件退订链接支持一键退订
- ⚠️ 免责声明：本系统为第三方工具，非香港入境事务处官方服务，请以官网信息为准

---

## 📄 License

MIT © [Deng Zheyi](https://github.com/Zheyi-D)

> ⚠️ **本项目为开源项目，仅供学习交流使用，请勿用于任何商业盈利目的。**

---

## 🙏 鸣谢

- 数据来源：[香港入境事务处 — 预约配额预览](https://eservices.es2.immd.gov.hk/es/quota-enquiry-client/?l=zh-CN&appId=579)
- 电话预约教程来源：小红书博主 [@八亿捌（增肌版）](https://www.xiaohongshu.com/explore/6a3006cc000000000f004f46)
