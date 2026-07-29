# 🪪 香港入境处预约配额监控

实时追踪香港入境事务处**换领身份证**预约配额，新名额放出时 **飞书群通知 + 邮件提醒**。

> **✨ 非技术用户？** 不需要懂代码——加入飞书群就能自动收到通知，或打开网页随时查看配额状态。

---

## 快速开始（选择适合你的方式）

### 🟢 方式一：加入飞书群（推荐）

加入下方飞书群，机器人会自动在群里推送最新配额变化：

> 📱 [点击加入飞书群](https://applink.feishu.cn/client/chat/chatter/add_by_link?link_token=ff3i6631-016b-40cc-989e-e4651ccd353c)

### 🌐 方式二：打开网页看板

直接访问看板页面，随时查看各办事处预约配额状态：

> 🖥 **[quota-monitor 看板](https://Zheyi-D.github.io/quota-monitor)**

### 📧 方式三：邮件通知

在网页看板上填写你的邮箱地址，或在飞书群中发送 `/subscribe your-email@example.com`，即可收到邮件通知（由 `dddzzzyyy@agent.qq.com` 发送）。

---

## 📊 看板预览

| 🟢 有名额 | 🟡 少量 | 🔴 已满 | ⬜ 不提供 |
|-----------|---------|---------|-----------|

![看板预览](web/screenshot.png)

**涵盖办事处：**
- 火炭办事处 (FTO)
- 港岛办事处 (RHK)
- 九龙办事处 (RKO)
- 将军澳办事处 (RTK)
- 屯门办事处 (TMO)
- 元朗办事处 (YLO)

---

## 🔧 开发者自部署

如果你想自己部署一套监控系统：

### 前置要求

- Python 3.8+
- GitHub 账号（用于 Actions 自动运行）

### 安装

```bash
git clone https://github.com/Zheyi-D/quota-monitor.git
cd quota-monitor
pip install -e .
```

### 配置

```bash
cp config.example.json config.json
# 编辑 config.json，填入你的飞书 webhook URL 和邮件订阅者
```

### 运行

```bash
# 本地持续监控
python monitor.py --interval 600

# 单次测试
python monitor.py --once

# CI 模式（供 GitHub Actions 使用）
python ci_run.py
```

### 配置 GitHub Actions

1. Fork 本仓库
2. 在 Settings → Secrets and variables → Actions 中添加：
   - `FEISHU_WEBHOOK_URL`：你的飞书群机器人 webhook URL
   - `EMAIL_SUBSCRIBERS`（可选）：JSON 数组 `["user@example.com"]`
3. 在 Settings → Pages 中启用 GitHub Pages（Source: Deploy from a branch，选 `main` 分支 `/ (root)` 目录）
4. Actions 会每 5 分钟自动运行

### 创建飞书群机器人

1. 打开 [飞书开发者后台](https://open.feishu.cn/app)
2. 创建自建应用 → 添加「机器人」能力
3. 在「事件与回调」中获取 Webhook 地址
4. 将机器人添加到目标群聊
5. 把 Webhook URL 填入 `config.json` 或 GitHub Secrets

---

## 📁 项目结构

```
quota-monitor/
├── quota_monitor/          # Python 核心库
│   ├── core.py             # API 拉取 + 变化检测
│   ├── notify.py           # 飞书 webhook + 邮件通知
│   ├── state.py            # 状态持久化
│   └── monitor.py          # CLI 入口 + 主循环
├── ci_run.py               # GitHub Actions CI 入口
├── monitor.py              # 快速启动脚本
├── web/                    # GitHub Pages 前端看板
│   ├── index.html
│   ├── style.css
│   └── app.js
├── data/                   # 自动生成的配额数据
├── .github/workflows/      # CI 工作流
├── config.example.json     # 配置模板
└── README.md
```

---

## 🔒 隐私与安全

- 本工具仅读取入境处**公开发布**的配额数据，不涉及任何个人隐私
- 邮件通知仅发送配额变化提醒，不含任何个人身份信息
- 飞书机器人仅向群内发送消息，不会读取群聊内容

---

## 📄 License

MIT © [Deng Zheyi](https://github.com/Zheyi-D)

---

## 🙏 鸣谢

数据来源：[香港入境事务处 — 预约配额预览](https://eservices.es2.immd.gov.hk/es/quota-enquiry-client/?l=zh-CN&appId=579)
