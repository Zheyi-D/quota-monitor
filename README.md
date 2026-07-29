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

## 🔒 隐私与安全

- 订阅者邮箱使用 **AES-256-GCM 加密存储**，仓库中不可读
- 仅读取入境处**公开发布**的配额数据
- 邮件退订链接支持一键退订
- ⚠️ 免责声明：本系统为第三方工具，非香港入境事务处官方服务，请以官网信息为准

---

## 📄 License

MIT © [Deng Zheyi](https://github.com/Zheyi-D)

---

## 🙏 鸣谢

- 数据来源：[香港入境事务处 — 预约配额预览](https://eservices.es2.immd.gov.hk/es/quota-enquiry-client/?l=zh-CN&appId=579)
- 电话预约教程来源：小红书博主 [@八亿捌（增肌版）](https://www.xiaohongshu.com/explore/6a3006cc000000000f004f46)
