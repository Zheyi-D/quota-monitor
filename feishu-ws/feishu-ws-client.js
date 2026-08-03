/**
 * feishu-ws-client — 飞书长连接客户端
 *
 * 使用飞书官方 Node.js SDK，通过 WebSocket 长连接接收机器人私聊消息和卡片按钮事件。
 * 在 GitHub Actions 中长时间运行（每 5 小时 cron 重启一次）。
 *
 * 功能：
 *   用户私聊机器人 → 回复交互卡片 → 支持文本命令/按钮操作
 *   订阅/退订/查询均通过 Worker REST API 持久化到 feishu_subs.json
 */

import { Client, WSClient, EventDispatcher, LoggerLevel } from "@larksuiteoapi/node-sdk";

// ── 配置 ─────────────────────────────────────────────────────────────

const APP_ID = process.env.FEISHU_APP_ID;
const APP_SECRET = process.env.FEISHU_APP_SECRET;
const WORKER_URL = process.env.WORKER_URL || "https://quota-monitor.deng-zheyi.workers.dev";
const QUOTA_DATA_URL = "https://Zheyi-D.github.io/quota-monitor/data/quota.json";

if (!APP_ID || !APP_SECRET) {
  console.error("❌ FEISHU_APP_ID / FEISHU_APP_SECRET 未设置");
  process.exit(1);
}

const client = new Client({
  appId: APP_ID,
  appSecret: APP_SECRET,
});

const wsClient = new WSClient({
  appId: APP_ID,
  appSecret: APP_SECRET,
  loggerLevel: LoggerLevel.info,
});

// ── Worker API 调用 ───────────────────────────────────────────────────

async function workerPost(path, body) {
  const resp = await fetch(`${WORKER_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return resp.json();
}

async function workerGet(path) {
  const resp = await fetch(`${WORKER_URL}${path}`);
  return resp.json();
}

// ── 日期工具 ──────────────────────────────────────────────────────────

let quotaDatesCache = null;
let quotaDatesCacheTime = 0;
const CACHE_TTL = 5 * 60 * 1000;

async function getQuotaDates() {
  if (quotaDatesCache && Date.now() - quotaDatesCacheTime < CACHE_TTL) {
    return quotaDatesCache;
  }
  try {
    const resp = await fetch(QUOTA_DATA_URL);
    if (resp.ok) {
      const raw = await resp.json();
      const dates = new Set();
      for (const key of Object.keys(raw)) {
        const parts = key.split("|");
        if (parts.length >= 1) dates.add(parts[0]);
      }
      const sorted = [...dates].sort((a, b) => {
        const [am, ad, ay] = a.split("/").map(Number);
        const [bm, bd, by] = b.split("/").map(Number);
        return ay - by || am - bm || ad - bd;
      });
      quotaDatesCache = { set: dates, sorted };
      quotaDatesCacheTime = Date.now();
      return quotaDatesCache;
    }
  } catch {}
  return quotaDatesCache || { set: new Set(), sorted: [] };
}

function parseDates(text, validDateSet) {
  const cleaned = text.replace(/\s+/g, "").replace(/，/g, ",");
  const parts = cleaned.split(",").filter(Boolean);
  const now = new Date();
  const thisYear = now.getFullYear();
  const thisMonth = now.getMonth() + 1;
  const result = { valid: [], invalid: [] };

  for (const part of parts) {
    const rangeMatch = part.match(/^(\d{1,2})[\/.](\d{1,2})-(\d{1,2})[\/.]?(\d{1,2})?$/);
    if (rangeMatch) {
      const sm = parseInt(rangeMatch[1]), sd = parseInt(rangeMatch[2]);
      const em = rangeMatch[4] ? parseInt(rangeMatch[3]) : sm;
      const ed = rangeMatch[4] ? parseInt(rangeMatch[4]) : parseInt(rangeMatch[3]);
      let year = thisYear;
      if (sm < thisMonth) year = thisYear + 1;
      const startDate = new Date(year, sm - 1, sd);
      const endDate = new Date(year, em - 1, ed);
      if (endDate < startDate) endDate.setFullYear(endDate.getFullYear() + 1);
      const cur = new Date(startDate);
      while (cur <= endDate) {
        const ds = fmtDate(cur);
        if (validDateSet.has(ds)) result.valid.push(ds);
        cur.setDate(cur.getDate() + 1);
      }
      continue;
    }

    const dateMatch = part.match(/^(\d{1,2})[\/.](\d{1,2})(?:[\/.](\d{4}))?$/);
    if (dateMatch) {
      const m = parseInt(dateMatch[1]), d = parseInt(dateMatch[2]);
      const y = dateMatch[3] ? parseInt(dateMatch[3]) : null;
      const label = `${String(m).padStart(2, "0")}/${String(d).padStart(2, "0")}`;
      if (y) {
        const ds = `${String(m).padStart(2, "0")}/${String(d).padStart(2, "0")}/${y}`;
        if (validDateSet.has(ds)) result.valid.push(ds);
        else result.invalid.push(label);
      } else {
        const dsThis = `${String(m).padStart(2, "0")}/${String(d).padStart(2, "0")}/${thisYear}`;
        const dsNext = `${String(m).padStart(2, "0")}/${String(d).padStart(2, "0")}/${thisYear + 1}`;
        if (validDateSet.has(dsThis)) result.valid.push(dsThis);
        else if (validDateSet.has(dsNext)) result.valid.push(dsNext);
        else result.invalid.push(label);
      }
      continue;
    }
    result.invalid.push(part);
  }
  result.valid = [...new Set(result.valid)];
  result.invalid = [...new Set(result.invalid)];
  return result;
}

function fmtDate(d) {
  return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}/${d.getFullYear()}`;
}

function looksLikeDates(text) {
  return /[\d]{1,2}[\/.][\d]{1,2}/.test(text);
}

// ── 发消息 ────────────────────────────────────────────────────────────

async function sendDM(openId, card) {
  const content = JSON.stringify(card);
  await client.im.message.create({
    params: { receive_id_type: "open_id" },
    data: { receive_id: openId, msg_type: "interactive", content },
  });
}

async function sendTextDM(openId, text) {
  await client.im.message.create({
    params: { receive_id_type: "open_id" },
    data: {
      receive_id: openId,
      msg_type: "text",
      content: JSON.stringify({ text }),
    },
  });
}

// ── 办事处常量 ──────────────────────────────────────────────────────

const OFFICES = {
  FTO: "火炭", RHK: "港岛", RKO: "九龙",
  RTK: "将军澳", TMO: "屯门", YLO: "元朗",
};
const OFFICE_CODES = Object.keys(OFFICES);

// ── 用户状态（多步骤交互）──────────────────────────────────────────

const userState = new Map(); // openId -> { mode, selectedOffices: Set }

function getUserState(openId) {
  if (!userState.has(openId)) {
    userState.set(openId, { mode: null, selectedOffices: new Set() });
  }
  return userState.get(openId);
}

function clearUserState(openId) {
  userState.delete(openId);
}

// ── 卡片构建 ──────────────────────────────────────────────────────────

function buildWelcomeCard() {
  return {
    header: { title: { content: "🤖 HKID 放号通知", tag: "plain_text" }, template: "blue" },
    elements: [
      { tag: "markdown", content: "当**你关注的日期和办事处**放出新名额时，我会第一时间私聊通知你。\n\n群聊广播已覆盖全量通知，私聊可以**按日期、按办事处**精细过滤。" },
      { tag: "hr" },
      { tag: "action", actions: [
        { tag: "button", text: { tag: "plain_text", content: "📅 按日期订阅" }, type: "primary", value: "sub_pick_date" },
        { tag: "button", text: { tag: "plain_text", content: "🏢 按办事处订阅" }, type: "default", value: "sub_pick_office" },
      ]},
      { tag: "action", actions: [
        { tag: "button", text: { tag: "plain_text", content: "🎯 按日期+办事处" }, type: "default", value: "sub_pick_both" },
        { tag: "button", text: { tag: "plain_text", content: "🔔 订阅全部" }, type: "default", value: "sub_all" },
      ]},
      { tag: "action", actions: [
        { tag: "button", text: { tag: "plain_text", content: "📊 我的订阅" }, type: "default", value: "my_subs" },
        { tag: "button", text: { tag: "plain_text", content: "📋 查看可选日期" }, type: "default", value: "show_dates" },
      ]},
      { tag: "action", actions: [
        { tag: "button", text: { tag: "plain_text", content: "❌ 取消订阅" }, type: "danger", value: "unsub" },
      ]},
      { tag: "hr" },
      { tag: "note", elements: [{ tag: "plain_text", content: "💡 也可以直接回复日期来订阅\n例: 08/15, 08/20-08/25, 9/1\n回复「全部」订阅所有日期" }]},
    ],
  };
}

function buildSubPickCard() {
  return {
    header: { title: { content: "📅 选择日期", tag: "plain_text" }, template: "blue" },
    elements: [
      { tag: "markdown", content: "请回复你想要关注的日期。\n\n**支持的格式：**\n• 单个日期：`08/15`、`9/1`\n• 多个日期：`08/15, 08/20, 09/01`\n• 日期段：`08/15-08/20`（15号到20号每天）\n• 混合：`08/15, 08/20-08/25`\n\n我会自动校验日期并订阅。" },
      { tag: "action", actions: [
        { tag: "button", text: { tag: "plain_text", content: "📋 查看可选日期" }, type: "default", value: "show_dates" },
        { tag: "button", text: { tag: "plain_text", content: "🔔 订阅全部" }, type: "default", value: "sub_all" },
      ]},
    ],
  };
}

function buildOfficePickCard(selectedOffices) {
  const sel = selectedOffices || new Set();
  const rows = [];
  // Build button rows, 2 per row, marking selected with ✓
  for (let i = 0; i < OFFICE_CODES.length; i += 2) {
    const btns = [];
    for (let j = i; j < i + 2 && j < OFFICE_CODES.length; j++) {
      const code = OFFICE_CODES[j];
      const name = OFFICES[code];
      const isSel = sel.has(code);
      btns.push({
        tag: "button",
        text: { tag: "plain_text", content: isSel ? `✓ ${name}` : name },
        type: isSel ? "primary" : "default",
        value: `pick_office:${code}`,
      });
    }
    rows.push({ tag: "action", actions: btns });
  }
  const selectedList = sel.size > 0
    ? `\n\n已选：${[...sel].map(c => OFFICES[c]).join("、")}`
    : "";

  return {
    header: { title: { content: "🏢 选择办事处", tag: "plain_text" }, template: "blue" },
    elements: [
      { tag: "markdown", content: `请选择你关注的办事处（可多选，点击切换）：${selectedList}` },
      ...rows,
      { tag: "action", actions: [
        { tag: "button", text: { tag: "plain_text", content: "✅ 确认选择" }, type: "primary", value: "confirm_offices" },
        { tag: "button", text: { tag: "plain_text", content: "全选" }, type: "default", value: "select_all_offices" },
      ]},
    ],
  };
}

function buildConfirmCard(dates, offices, mode) {
  let markdown;
  if (mode === "all") {
    markdown = "✅ 已订阅**全部**通知\n\n所有日期、所有办事处放号时都会私聊通知你。";
  } else {
    const parts = [];
    if (dates.length > 0) {
      const list = dates.slice(0, 15).map(d => { const [m, d2] = d.split("/"); return `${parseInt(m)}/${parseInt(d2)}`; }).join("、");
      const more = dates.length > 15 ? ` …+${dates.length - 15}` : "";
      parts.push(`📅 ${list}${more}`);
    } else {
      parts.push("📅 所有日期");
    }
    if (offices.length > 0) {
      const names = offices.map(c => OFFICES[c] || c).join("、");
      parts.push(`🏢 ${names}`);
    } else {
      parts.push("🏢 所有办事处");
    }
    markdown = "✅ 订阅成功\n\n" + parts.join("\n") + "\n\n仅当匹配的日期和办事处放号时通知你。";
  }
  return {
    header: { title: { content: "✅ 订阅成功", tag: "plain_text" }, template: "green" },
    elements: [
      { tag: "markdown", content: markdown },
      { tag: "action", actions: [
        { tag: "button", text: { tag: "plain_text", content: "📊 我的订阅" }, type: "default", value: "my_subs" },
        { tag: "button", text: { tag: "plain_text", content: "🔥 修改订阅" }, type: "default", value: "sub_pick" },
      ]},
    ],
  };
}

function buildStatusCard(entry, openId) {
  // Determine mode
  const hasDates = entry && entry.dates && entry.dates.length > 0;
  const hasOffices = entry && entry.offices && entry.offices.length > 0;

  if (entry && !hasDates && !hasOffices) {
    return {
      header: { title: { content: "📊 我的订阅", tag: "plain_text" }, template: "blue" },
      elements: [
        { tag: "markdown", content: `当前订阅：**全部**\n\n所有日期、所有办事处放号都会通知你。\n\n<font color='grey' size='1'>ID: ${openId||""}</font>` },
        { tag: "action", actions: [
          { tag: "button", text: { tag: "plain_text", content: "🔥 修改订阅" }, type: "default", value: "sub_pick" },
          { tag: "button", text: { tag: "plain_text", content: "❌ 取消订阅" }, type: "danger", value: "unsub" },
        ]},
      ],
    };
  }
  if (!entry) {
    return {
      header: { title: { content: "📊 我的订阅", tag: "plain_text" }, template: "blue" },
      elements: [
        { tag: "markdown", content: `你还没有订阅通知。\n\n订阅后，当关注的日期/办事处放号时会私聊通知你。\n\n<font color='grey' size='1'>ID: ${openId||""}</font>` },
        { tag: "action", actions: [
          { tag: "button", text: { tag: "plain_text", content: "📅 按日期订阅" }, type: "primary", value: "sub_pick_date" },
          { tag: "button", text: { tag: "plain_text", content: "🏢 按办事处订阅" }, type: "default", value: "sub_pick_office" },
          { tag: "button", text: { tag: "plain_text", content: "🔔 订阅全部" }, type: "default", value: "sub_all" },
        ]},
      ],
    };
  }

  const parts = [];
  if (hasDates) {
    const list = entry.dates.slice(0, 30).map(d => { const [m, d2] = d.split("/"); return `${parseInt(m)}/${parseInt(d2)}`; }).join("、");
    const more = entry.dates.length > 30 ? `\n…还有 ${entry.dates.length - 30} 个日期` : "";
    parts.push(`📅 **${entry.dates.length}** 个日期\n${list}${more}`);
  } else {
    parts.push("📅 所有日期");
  }
  if (hasOffices) {
    const names = entry.offices.map(c => OFFICES[c] || c).join("、");
    parts.push(`🏢 ${names}`);
  } else {
    parts.push("🏢 所有办事处");
  }
  const markdown = parts.join("\n\n") + `\n\n订阅时间：${entry.subscribed_at ? entry.subscribed_at.slice(0, 10) : "未知"}\n\n<font color='grey' size='1'>ID: ${openId||""}</font>`;

  return {
    header: { title: { content: "📊 我的订阅", tag: "plain_text" }, template: "blue" },
    elements: [
      { tag: "markdown", content: markdown },
      { tag: "action", actions: [
        { tag: "button", text: { tag: "plain_text", content: "🔥 修改订阅" }, type: "default", value: "sub_pick" },
        { tag: "button", text: { tag: "plain_text", content: "❌ 取消订阅" }, type: "danger", value: "unsub" },
      ]},
    ],
  };
}

function buildUnsubConfirmCard() {
  return {
    header: { title: { content: "✅ 已取消订阅", tag: "plain_text" }, template: "green" },
    elements: [
      { tag: "markdown", content: "你已取消所有日期通知，不会再收到私聊提醒。\n\n群聊广播不受影响，随时可以重新订阅。" },
      { tag: "action", actions: [
        { tag: "button", text: { tag: "plain_text", content: "📅 重新订阅" }, type: "primary", value: "sub_pick" },
        { tag: "button", text: { tag: "plain_text", content: "🔔 重新订阅全部" }, type: "default", value: "sub_all" },
      ]},
    ],
  };
}

async function buildShowDatesCard() {
  const { sorted } = await getQuotaDates();
  if (sorted.length === 0) {
    return {
      header: { title: { content: "📋 可选日期", tag: "plain_text" }, template: "blue" },
      elements: [{ tag: "markdown", content: "暂时无法获取日期列表，请稍后再试。" }],
    };
  }
  const months = {};
  for (const ds of sorted) {
    const [m, d, y] = ds.split("/");
    const key = `${y}/${m}`;
    if (!months[key]) months[key] = [];
    months[key].push(`${parseInt(d)}`);
  }
  let text = `可选日期范围（共 **${sorted.length}** 天）：\n\n`;
  for (const [key, days] of Object.entries(months)) {
    text += `**${key}月**：${days.join(", ")}\n\n`;
  }
  if (text.length > 3500) text = text.slice(0, 3470) + "\n\n…部分日期已省略";
  return {
    header: { title: { content: "📋 可选日期列表", tag: "plain_text" }, template: "blue" },
    elements: [
      { tag: "markdown", content: text },
      { tag: "action", actions: [
        { tag: "button", text: { tag: "plain_text", content: "📅 订阅特定日期" }, type: "primary", value: "sub_pick" },
        { tag: "button", text: { tag: "plain_text", content: "🔔 订阅全部日期" }, type: "default", value: "sub_all" },
      ]},
    ],
  };
}

// ── 命令处理 ──────────────────────────────────────────────────────────

async function handleText(openId, text) {
  const trimmed = text.trim();
  const state = getUserState(openId);

  // 全部订阅
  if (/^(全部|订阅全部|所有|all|订阅所有)$/i.test(trimmed)) {
    clearUserState(openId);
    return await doSubscribe(openId, [], []);
  }
  // 查看状态
  if (/^(状态|我的|查看|status|my)$/i.test(trimmed)) {
    clearUserState(openId);
    return await doStatus(openId);
  }
  // 退订
  if (/^(退订|取消|停止|unsub|unsubscribe)$/i.test(trimmed)) {
    clearUserState(openId);
    return await doUnsubscribe(openId);
  }
  // 帮助/菜单
  if (/^(日期|可选|帮助|help|菜单|开始)$/i.test(trimmed)) {
    clearUserState(openId);
    return await sendDM(openId, buildWelcomeCard());
  }
  // Date input — check user state for mode
  if (looksLikeDates(trimmed)) {
    if (state.mode === "both") {
      // Waiting for dates after selecting offices
      const offices = [...state.selectedOffices];
      clearUserState(openId);
      return await doDateSubscribe(openId, trimmed, offices);
    }
    // Default: date-only mode
    clearUserState(openId);
    return await doDateSubscribe(openId, trimmed, []);
  }
  // 默认：欢迎卡片
  clearUserState(openId);
  return await sendDM(openId, buildWelcomeCard());
}

async function handleAction(openId, actionValue) {
  const state = getUserState(openId);

  // Office pick toggle
  if (actionValue.startsWith("pick_office:")) {
    const code = actionValue.slice(12);
    if (state.selectedOffices.has(code)) {
      state.selectedOffices.delete(code);
    } else {
      state.selectedOffices.add(code);
    }
    return await sendDM(openId, buildOfficePickCard(state.selectedOffices));
  }

  switch (actionValue) {
    case "sub_all":
      clearUserState(openId);
      return await doSubscribe(openId, [], []);

    case "sub_pick":
      clearUserState(openId);
      return await sendDM(openId, buildWelcomeCard());

    case "sub_pick_date":
      state.mode = "date";
      return await sendDM(openId, buildSubPickCard());

    case "sub_pick_office": {
      state.mode = "office";
      state.selectedOffices = new Set();
      return await sendDM(openId, buildOfficePickCard(new Set()));
    }

    case "sub_pick_both": {
      state.mode = "both";
      state.selectedOffices = new Set();
      return await sendDM(openId, buildOfficePickCard(new Set()));
    }

    case "select_all_offices":
      state.selectedOffices = new Set(OFFICE_CODES);
      return await sendDM(openId, buildOfficePickCard(state.selectedOffices));

    case "confirm_offices": {
      const offices = [...state.selectedOffices];
      if (state.mode === "both") {
        // Need dates next
        return await sendDM(openId, {
          header: { title: { content: "📅 请输入日期", tag: "plain_text" }, template: "blue" },
          elements: [
            { tag: "markdown", content: `已选办事处：${offices.map(c => OFFICES[c]).join("、")}\n\n请回复你想要关注的日期：\n• 单个日期：\`08/15\`\n• 多个日期：\`08/15, 08/20\`\n• 日期段：\`08/15-08/20\`` },
          ],
        });
      }
      // Office-only mode: subscribe directly
      clearUserState(openId);
      return await doSubscribe(openId, [], offices);
    }

    case "my_subs":
      clearUserState(openId);
      return await doStatus(openId);

    case "show_dates":
      return await sendDM(openId, await buildShowDatesCard());

    case "unsub":
      clearUserState(openId);
      return await doUnsubscribe(openId);

    default:
      clearUserState(openId);
      return await sendDM(openId, buildWelcomeCard());
  }
}

async function doSubscribe(openId, dates, offices) {
  const resp = await workerPost("/api/feishu-subscribe", { open_id: openId, dates, offices });
  if (resp.ok) {
    const isAll = dates.length === 0 && offices.length === 0;
    return await sendDM(openId, buildConfirmCard(dates, offices, isAll ? "all" : "pick"));
  } else {
    return await sendTextDM(openId, `订阅失败：${resp.msg || "请稍后重试"}`);
  }
}

async function doDateSubscribe(openId, text, offices) {
  offices = offices || [];
  const { set: validSet } = await getQuotaDates();
  const parsed = parseDates(text, validSet);

  if (parsed.valid.length === 0 && parsed.invalid.length === 0) {
    return await sendDM(openId, buildSubPickCard());
  }

  if (parsed.valid.length === 0) {
    return await sendDM(openId, {
      header: { title: { content: "⚠️ 无效日期", tag: "plain_text" }, template: "red" },
      elements: [
        { tag: "markdown", content: `以下日期不在配额窗口内：\n${parsed.invalid.join("、")}\n\n请回复有效日期。` },
        { tag: "action", actions: [
          { tag: "button", text: { tag: "plain_text", content: "📋 查看可选日期" }, type: "default", value: "show_dates" },
        ]},
      ],
    });
  }

  const resp = await workerPost("/api/feishu-subscribe", { open_id: openId, dates: parsed.valid, offices });
  if (!resp.ok) {
    return await sendTextDM(openId, `订阅失败：${resp.msg || "请稍后重试"}`);
  }

  const card = buildConfirmCard(parsed.valid, offices, "pick");
  if (parsed.invalid.length > 0) {
    card.elements[0].content += `\n\n⚠️ 以下日期不在配额窗口内，已跳过：${parsed.invalid.join("、")}`;
  }
  return await sendDM(openId, card);
}

async function doStatus(openId) {
  const resp = await workerGet(`/api/feishu-status?open_id=${encodeURIComponent(openId)}`);
  if (resp.ok) {
    const entry = resp.subscribed
      ? { dates: resp.dates || [], offices: resp.offices || [], subscribed_at: resp.subscribed_at || "" }
      : null;
    return await sendDM(openId, buildStatusCard(entry, openId));
  } else {
    return await sendDM(openId, buildStatusCard(null, openId));
  }
}

async function doUnsubscribe(openId) {
  const resp = await workerPost("/api/feishu-unsubscribe", { open_id: openId });
  if (resp.ok) {
    return await sendDM(openId, buildUnsubConfirmCard());
  } else {
    // Already unsubscribed or error
    return await sendDM(openId, buildUnsubConfirmCard());
  }
}

// ── 长连接启动 ────────────────────────────────────────────────────────

const dispatcher = new EventDispatcher({}).register({
  "im.message.receive_v1": async (data) => {
    try {
      const sender = data.sender || {};
      const openId = (sender.sender_id && sender.sender_id.open_id) || "";
      if (!openId) return;

      const msg = data.message || {};
      // Only respond to text messages
      if (msg.message_type !== "text") {
        await sendDM(openId, buildWelcomeCard());
        return;
      }

      let msgText = "";
      try {
        const content = JSON.parse(msg.content || "{}");
        msgText = content.text || "";
      } catch { return; }

      if (!msgText.trim()) return;

      await handleText(openId, msgText);
    } catch (e) {
      console.error(`handle text error: openId=${openId.slice(0,20)}, msg=${e.message}, resp=${JSON.stringify(e.response?.data || "").slice(0,300)}`);
    }
  },

  "card.action.trigger": async (data) => {
    try {
      const operator = data.operator || {};
      const openId = operator.open_id || "";
      let actionValue = (data.action && data.action.value) || "";
      // Feishu may double-quote the value: "\"my_subs\"" -> "my_subs"
      if (typeof actionValue === "string" && actionValue.startsWith('"')) {
        try { actionValue = JSON.parse(actionValue); } catch {}
      }
      console.log(`🎯 card.action: openId=${openId}, action=${actionValue}`);

      if (!openId || !actionValue) return { toast: { type: "error", content: "参数缺失" } };

      await handleAction(openId, actionValue);
      return { toast: { type: "success", content: "ok" } };
    } catch (e) {
      console.error(`card action error: openId=${openId.slice(0,20)}, action=${actionValue}, msg=${e.message}, resp=${JSON.stringify(e.response?.data || "").slice(0,300)}`);
      return { toast: { type: "error", content: e.message } };
    }
  },
});

console.log(`🚀 飞书长连接客户端启动中...`);
console.log(`   App ID: ${APP_ID}`);
console.log(`   Worker: ${WORKER_URL}`);

// 启动长连接（SDK 自动处理重连）
wsClient.start({ eventDispatcher: dispatcher })
  .then(() => {
    console.log("✅ 飞书长连接已启动，等待消息...");
  })
  .catch((err) => {
    console.error("❌ 长连接启动失败:", err.message);
    process.exit(1);
  });

// 定期心跳日志
setInterval(() => {
  console.log(`💓 ${new Date().toISOString()} — 长连接运行中`);
}, 10 * 60 * 1000);
