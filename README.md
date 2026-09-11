# 🪪 香港入境处预约配额监控

实时追踪香港入境事务处**换领身份证**预约配额，新名额放出时自动通知；
自部署实例支持 **企业微信群机器人**，原有飞书群与飞书私聊仍可同时使用。

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

### 🏢 方式三：企业微信群（自部署）

1. 在目标企业微信群中添加群机器人并复制 Webhook URL。
2. 在你的 GitHub Fork 中进入 `Settings → Secrets and variables → Actions`，新增
   `WECOM_WEBHOOK_URL`，值为完整 Webhook URL。
3. 将本分支合并到默认分支后，在 Actions 中手动运行 `Fetch Quota & Notify`，勾选
   `test_wecom` 即可只发送测试消息，不会读取或改写配额状态。

`WECOM_WEBHOOK_URL` 可用逗号或换行分隔多个群机器人地址。Webhook 等同于发消息密钥，
不要写入代码、日志或公开 Issue。

如需临时暂停所有人工消息而继续监控和更新看板，请在 Actions Variables 中设置
`NOTIFICATIONS_PAUSED=true`；恢复推送时改为 `false`。该开关不会删除任何 Webhook
或应用密钥，也不会暂停内部 ReleaseSignal。

---

## 🖥 看板

> 🖥 **[quota-monitor 看板](https://Zheyi-D.github.io/quota-monitor)**

- 📊 实时查看全港各办事处预约配额状态
- 📈 放号规律热力图，可视化各时段放号频次
- 📖 附电话预约办理教程
- 🔧 内置管理后台，支持订阅者管理、统计概览、双通道群发

---

## 📨 通知流程

检测到新配额放出时，会向所有已配置通道推送：

> **飞书群 + 企业微信群广播**（多群并行）→ **飞书私聊 DM**（仅推送给匹配日期的订阅者）

各通道失败互不影响；企业微信超长 Markdown 会自动安全分片。

飞书和企业微信群消息只推送香港日期从今天起两周内（含第 14 天）的新增配额。
只有更远日期的新增配额时不发送群消息；同一轮同时包含近、远期配额时，消息
只展示两周内项目。此窗口不限制内部 ReleaseSignal，自动预约服务仍会按客户
数据库中的日期条件独立复查和匹配。

同一日期、办事处和时段只在官网由不可用变为可用时广播。
通知记录保存在 `state.json` 的 `notification_episodes`。当前生产配置在配额变为
不可用后立即重新武装，因此同一行出现红→黄的新跳变时会再次发送群提醒和
ReleaseSignal；连续保持黄色不会重复发送。自部署实例可用
`NOTIFICATION_REARM_SECONDS` 增加防抖窗口。

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

Linux 服务器可使用 `quota-monitor-daemon` 直接常驻轮询，无需依赖外部
定时器唤醒 GitHub Actions。守护进程、影子迁移和 systemd 配置见
[deploy/systemd/README.md](deploy/systemd/README.md)。


### 企业微信配额投递恢复

新发现且符合原两周规则的配额先写入 `state.json` 的 `pending_wecom`，远端持久化成功后才发送。发现去重与送达记录分开；单轮最多投递一个事件，各群分别记录成功结果，失败按30秒起、最多300秒的间隔退避。任务重启后从原队列接续，不从旧快照补造历史消息。

提醒最长保留15分钟；配额已不可用、日期超出原通知窗口或达到时效后终止。网络确认丢失可能重复投递，不保证严格 exactly-once。没有新增监测实例；原消息格式、六办事处、香港当天至当天+14天边界保持。此队列不参与预约提交和预约成功通知。
