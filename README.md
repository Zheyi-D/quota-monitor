# 🪪 香港入境处预约配额监控

实时追踪香港入境事务处**换领身份证**预约配额，新名额放出时 **飞书群 + 飞书私聊** 自动通知。

---

## 📱 方式一：加入飞书群（推荐）

机器人自动推送配额变化，**消息秒达**，加群即用，无需任何配置。

> 📖 **[群信息 & 加群方式](https://scn7uo58gnuo.feishu.cn/wiki/QSFlwcMBmil7sGkZRBTcAWqwnCf)**（多群方案，群满自动分流）

### 💬 方式二：飞书私聊订阅

如果不想加群，也可以**按日期过滤**，仅接收你关注日期的通知：

1. [私聊机器人「HKID放号自动监测」](https://applink.feishu.cn/T98uc8RbxI8c)（发送任意消息即可触发）
2. 机器人会回复交互卡片，支持：
   - 📅 **订阅特定日期** — 只关注你预约的那天
   - 🔔 **订阅全部日期** — 所有放号都通知
   - 📊 **查看 / 修改 / 取消订阅**
3. 也可以直接回复日期一键订阅，例如 `08/15, 08/20-08/25`

支持三种过滤模式：
- 📅 **按日期** — 只看特定日期，不限办事处
- 🏢 **按办事处** — 只看特定办事处，不限日期
- 🎯 **按日期+办事处** — 精确匹配

> 💡 机器人私聊有 1-2 秒延迟，着急的话推荐进群。

---

## 🖥 看板

> 🖥 **[quota-monitor 看板](https://Zheyi-D.github.io/quota-monitor)**

- 📊 实时查看全港各办事处预约配额状态
- 📈 放号规律热力图，可视化各时段放号频次
- 📖 附电话预约办理教程
- 🔧 内置管理后台，支持订阅者管理、统计概览、双通道群发

---

## 📨 通知流程

检测到新配额放出时，按以下顺序推送：

> **群聊广播**（多群并行）→ **私聊 DM**（并行，仅推送给匹配日期的订阅者）

飞书群和私聊几乎同时到达。

### 🔗 可选：HKID ReleaseSignal bridge

自部署实例可以在检测到 `newly_available` 后，向 `hkid-appointment-monitor`
发送经过 HMAC-SHA256 签名的无 PII `ReleaseSignal`。该事件只负责唤醒接收端；
接收端仍会独立读取官网并执行自己的日期、客户和预约规则，不能把飞书消息或本仓库
的 `state.json` 当成预约证据。

GitHub Actions Secrets：

```text
HKID_RELEASE_WEBHOOK_URL=https://<host>/internal/reschedule/release-signals/quota-monitor
HKID_RELEASE_WEBHOOK_SECRET=<与接收端相同的 32+ bytes 随机密钥>
```

未成功投递的事件会保存在 `state.json` 的 `pending_release_signals`（只含公开配额行）
并在后续运行重试。2xx 才删除，5xx/timeout 保留，4xx 记录为永久拒绝。生产 URL
必须为 HTTPS；明文 HTTP 只允许 loopback 开发地址。

---

## 🔒 隐私与安全

- 飞书用户数据使用 **AES-256-GCM 加密存储**，仓库中不可读
- 仅读取入境处**公开发布**的配额数据
- ⚠️ 免责声明：本系统为第三方开源工具，非香港入境事务处官方服务，请以官网信息为准
- ReleaseSignal 不含客户资料、证件、查询码、验证码或预约 Session

---

## 📄 License

MIT © [Deng Zheyi](https://github.com/Zheyi-D)

> ⚠️ **本开源项目仅供学习交流使用，请勿用于任何商业盈利目的。**

---

## 🙏 鸣谢

- 数据来源：[香港入境事务处 — 预约配额预览](https://eservices.es2.immd.gov.hk/es/quota-enquiry-client/?l=zh-CN&appId=579)
- 电话预约教程来源：小红书博主 [@八亿捌（增肌版）](https://www.xiaohongshu.com/explore/6a3006cc000000000f004f46)

---

## 🔧 开发者

自部署、技术架构、加密方案详见 [ARCHITECTURE.md](ARCHITECTURE.md)。
