/**
 * HK IMMD Appointment Quota Dashboard
 * 纯前端渲染，从 data/quota.json 读取数据
 */

const OFFICES = {
  FTO: "火炭辦事處",
  RHK: "港島辦事處",
  RKO: "九龍辦事處",
  RTK: "將軍澳辦事處",
  TMO: "屯門辦事處",
  YLO: "元朗辦事處",
};

const QUOTA_LABELS = {
  "quota-g": "有名額",
  "quota-y": "少量",
  "quota-r": "已滿",
};

const QUOTA_CLASSES = {
  "quota-g": "q-g",
  "quota-y": "q-y",
  "quota-r": "q-r",
  "no-quotaR": "q-no",
  "no-quotaK": "q-no",
};

const DAY_NAMES = ["日", "一", "二", "三", "四", "五", "六"];

// Cloudflare Worker 地址（部署后替换为实际 URL）
const SUBSCRIBE_URL = "https://quota-monitor.deng-zheyi.workers.dev/api/subscribe";

let quotaData = null;       // { "MM/DD/YYYY|OFFICE|R": "quota-g", ... }
let allDates = [];           // sorted unique dates

// ─── Load Data ────────────────────────────────────────────────

async function loadData() {
  const urls = [
    "../data/quota.json",
    "data/quota.json",
    "./data/quota.json",
  ];

  for (const url of urls) {
    try {
      const resp = await fetch(url);
      if (resp.ok) {
        const raw = await resp.json();
        quotaData = raw;
        allDates = extractDates(raw);
        document.getElementById("updateTime").textContent =
          "更新時間：" + await loadUpdateTime();
        document.getElementById("loading").classList.add("hidden");
        return;
      }
    } catch (_) {
      // try next URL
    }
  }

  // All URLs failed — show demo/error state
  throw new Error("無法載入配額數據。請確保 data/quota.json 存在。");
}

async function loadUpdateTime() {
  const urls = ["data/last_update.json", "../data/last_update.json", "./data/last_update.json"];
  for (const url of urls) {
    try {
      const resp = await fetch(url, { cache: "no-cache" });
      if (resp.ok) {
        const d = await resp.json();
        if (d.time) return d.time;
      }
    } catch {}
  }
  // fallback: page load time
  return new Date().toLocaleString("zh-HK");
}

function extractDates(data) {
  const dates = new Set();
  for (const key of Object.keys(data)) {
    const parts = key.split("|");
    if (parts.length >= 1) dates.add(parts[0]);
  }
  return Array.from(dates).sort((a, b) => {
    const [am, ad, ay] = a.split("/").map(Number);
    const [bm, bd, by] = b.split("/").map(Number);
    return ay - by || am - bm || ad - bd;
  });
}

// ─── Render ───────────────────────────────────────────────────

function render() {
  if (!quotaData) return;
  document.getElementById("quotaTable").classList.remove("hidden");

  renderTableHeader(allDates);
  renderTableBody(allDates);

  // Date range indicator
  if (allDates.length) {
    document.getElementById("dateRange").textContent =
      formatDateShort(allDates[0]) + " — " + formatDateShort(allDates[allDates.length - 1]);
  }

  // Auto-scroll to today
  requestAnimationFrame(() => scrollToToday());
}

function renderTableHeader(dates) {
  const thead = document.getElementById("tableHead");
  const today = formatToday();
  let html = "<tr><th>辦事處</th>";
  for (const date of dates) {
    const dow = getDayOfWeek(date);
    const isSun = dow === 0;
    const isToday = date === today;
    let cls = "";
    if (isSun) cls += " sun";
    if (isToday) cls += " today";
    html += `<th class="${cls}" data-date="${date}">${formatDateShort(date)}<br>${DAY_NAMES[dow]}</th>`;
  }
  html += "</tr>";
  thead.innerHTML = html;
}

function renderTableBody(dates) {
  const tbody = document.getElementById("tableBody");
  let html = "";
  for (const [code, name] of Object.entries(OFFICES)) {
    html += `<tr><td>${name}<br><small>${code}</small></td>`;
    for (const date of dates) {
      const statusR = quotaData[`${date}|${code}|R`] || "no-quotaR";
      const cls = QUOTA_CLASSES[statusR] || "q-no";
      const label = QUOTA_LABELS[statusR] || "不提供";
      html += `<td class="${cls}" title="${date} ${name} — ${label}">${label}</td>`;
    }
    html += "</tr>";
  }
  tbody.innerHTML = html;
}

function scrollToToday() {
  const today = formatToday();
  const th = document.querySelector(`th[data-date="${today}"]`);
  if (th) {
    th.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  }
}

// ─── Helpers ──────────────────────────────────────────────────

function formatDateShort(dateStr) {
  const [m, d] = dateStr.split("/");
  return `${parseInt(m)}/${parseInt(d)}`;
}

function formatToday() {
  const now = new Date();
  return [
    String(now.getMonth() + 1).padStart(2, "0"),
    String(now.getDate()).padStart(2, "0"),
    now.getFullYear(),
  ].join("/");
}

function getDayOfWeek(dateStr) {
  const [m, d, y] = dateStr.split("/").map(Number);
  return new Date(y, m - 1, d).getDay();
}

// ─── Subscribe ────────────────────────────────────────────────

async function handleSubscribe() {
  const input = document.getElementById("subscribeEmail");
  const btn = document.getElementById("subscribeBtn");
  const msg = document.getElementById("subscribeMsg");
  const email = input.value.trim();

  // show/hide helper
  function showMsg(text, cls) {
    msg.textContent = text;
    msg.className = "subscribe-msg " + cls;
    msg.classList.remove("hidden");
  }
  function hideMsg() {
    msg.classList.add("hidden");
  }

  if (!email) {
    showMsg("请输入邮箱", "warning");
    return;
  }
  if (!/^[^\s@]{1,100}@[^\s@]{1,100}\.[^\s@]{2,20}$/.test(email)) {
    showMsg("邮箱格式不正确", "warning");
    return;
  }

  btn.disabled = true;
  btn.textContent = "提交中...";
  hideMsg();

  try {
    const resp = await fetch(SUBSCRIBE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const data = await resp.json();

    if (data.ok) {
      if (data.already_subscribed) {
        showMsg("已订阅过了！", "success");
      } else {
        showMsg("订阅成功！", "success");
        input.value = "";
        btn.disabled = false;
        btn.textContent = "订阅";
        return;
      }
    } else {
      showMsg(data.message || "订阅失败，请稍后重试", "error");
    }
  } catch {
    showMsg("网络错误，请稍后重试", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "订阅";
  }
}

async function handleUnsubscribe() {
  const input = document.getElementById("subscribeEmail");
  const btn = document.getElementById("unsubscribeBtn");
  const msg = document.getElementById("subscribeMsg");
  const email = input.value.trim();

  function showMsg(text, cls) {
    msg.textContent = text;
    msg.className = "subscribe-msg " + cls;
    msg.classList.remove("hidden");
  }

  if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    showMsg("请输入要退订的邮箱", "warning");
    return;
  }

  btn.disabled = true;
  btn.textContent = "...";

  try {
    const resp = await fetch(SUBSCRIBE_URL.replace("/api/subscribe", "/api/unsubscribe"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email }),
    });
    const data = await resp.json();
    if (data.ok) {
      showMsg("退订成功！", "success");
      input.value = "";
    } else {
      showMsg(data.msg === "not found" ? "该邮箱未订阅" : "退订失败，请稍后重试", "error");
    }
  } catch {
    showMsg("网络错误，请稍后重试", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "退订";
  }
}

// ─── Init ─────────────────────────────────────────────────────

async function init() {
  try {
    await loadData();
    render();
  } catch (err) {
    document.getElementById("loading").classList.add("hidden");
    document.getElementById("error").classList.remove("hidden");
    document.getElementById("error").textContent =
      "⚠️ " + err.message + " 請稍後再試，或直接訪問入境處官網查詢。";
  }

  // Setup event listeners (null-safe: email subscribe UI may be removed)
  const subBtn = document.getElementById("subscribeBtn");
  const unsubBtn = document.getElementById("unsubscribeBtn");
  const subInput = document.getElementById("subscribeEmail");
  if (subBtn) subBtn.addEventListener("click", handleSubscribe);
  if (unsubBtn) unsubBtn.addEventListener("click", handleUnsubscribe);
  if (subInput) subInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") handleSubscribe();
  });

  // Auto-refresh every 5 minutes
  setInterval(async () => {
    try {
      await loadData();
      render();
    } catch (_) { /* silent on auto-refresh */ }
    document.getElementById("updateTime").textContent =
      "更新時間：" + await loadUpdateTime();
  }, 5 * 60 * 1000);
}

init();

// ─── Tab switching ────────────────────────────────────────

document.getElementById("tabQuota").addEventListener("click", () => {
  document.getElementById("tabQuota").classList.add("active");
  document.getElementById("tabTrend").classList.remove("active");
  document.getElementById("viewQuota").classList.remove("hidden");
  document.getElementById("viewTrend").classList.add("hidden");
});

document.getElementById("tabTrend").addEventListener("click", () => {
  document.getElementById("tabTrend").classList.add("active");
  document.getElementById("tabQuota").classList.remove("active");
  document.getElementById("viewQuota").classList.add("hidden");
  document.getElementById("viewTrend").classList.remove("hidden");
  if (!window._trendLoaded) { initTrend(); window._trendLoaded = true; }
});

// ─── Trend View: release log rendering ─────────────────────

const HOURS_TREND = (() => { const a = []; for (let h = 8; h <= 23; h++) a.push(h); return a; })();
const DAYS_TREND = ["日","一","二","三","四","五","六"];

let batchesTrend = [];

async function initTrend() {
  try {
    // Parse run.log: "[2026-07-31 11:24:30 BJT] ALERT | 新配额放出: 8 个"
    const resp = await fetch("data/run.log");
    if (resp.ok) {
      const text = await resp.text();
      const re = /\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) BJT\] ALERT \| 新配额放出: (\d+) 个/g;
      let m;
      while ((m = re.exec(text)) !== null) {
        batchesTrend.push({
          t: new Date(m[1] + "+08:00"),
          count: parseInt(m[2]),
          dates: []  // run.log doesn't store individual dates, not needed for heatmap
        });
      }
      batchesTrend.sort((a, b) => b.t - a.t);
    }
  } catch (_) { /* file may not exist yet */ }

  updateCountdown();
  setInterval(updateCountdown, 60000);
  renderHeatmap(7);

  document.querySelectorAll(".pill").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".pill").forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    renderHeatmap(+b.dataset.period);
  }));
}

function fmtTrendTime(ts) {
  // ts = Date object (北京时间)
  const bj = new Date(ts.toLocaleString("en-US", { timeZone: "Asia/Shanghai" }));
  const y = bj.getFullYear();
  const m = String(bj.getMonth() + 1).padStart(2, "0");
  const d = String(bj.getDate()).padStart(2, "0");
  const h = String(bj.getHours()).padStart(2, "0");
  const min = String(bj.getMinutes()).padStart(2, "0");
  return `${y}/${m}/${d} ${h}:${min}`;
}

function updateCountdown() {
  document.getElementById("tcVal").textContent =
    batchesTrend.length > 0 ? fmtTrendTime(batchesTrend[0].t) : "暂无记录";
}

function fmtDate(ts) {
  const bj = new Date(ts.toLocaleString("en-US", { timeZone: "Asia/Shanghai" }));
  return `${bj.getFullYear()}/${String(bj.getMonth()+1).padStart(2,"0")}/${String(bj.getDate()).padStart(2,"0")}`;
}

function renderHeatmap(pd) {
  const N = HOURS_TREND.length;
  if (batchesTrend.length === 0) {
    document.getElementById("tmHead").innerHTML = "";
    document.getElementById("tmBody").innerHTML = `<tr><td colspan="${N+1}" style="text-align:center;padding:48px 16px;color:var(--text2);font-size:0.9rem">📊 数据收集中，放号规律将在检测到配额变化后自动生成<br><small style="color:var(--text2);opacity:0.7">系统每 2 分钟扫描一次（08:00-24:00）</small></td></tr>`;
    return;
  }

  const now = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Shanghai" }));
  const cutoff = new Date(now - pd * 86400000);
  const keys = [], dts = [];
  for (let d = new Date(cutoff); d <= now; d.setDate(d.getDate() + 1)) {
    if (d.getDay() !== 0) { keys.push(fmtDate(d)); dts.push(new Date(d)); }
  }
  function bjHour(ts) {
    return new Date(ts.toLocaleString("en-US", { timeZone: "Asia/Shanghai" })).getHours();
  }
  const cells = {};
  for (const b of batchesTrend) {
    if (b.t < cutoff) continue;
    const dk = fmtDate(b.t);
    const di = keys.indexOf(dk); if (di < 0) continue;
    const h = bjHour(b.t);
    const slot = h - 8;
    if (slot < 0 || slot >= N) continue;
    cells[di * N + slot] = (cells[di * N + slot] || 0) + b.count;
  }
  // Only show dates that have data (skip empty rows from before recording started)
  const activeSet = new Set();
  for (let i = 0; i < keys.length; i++) {
    for (let j = 0; j < N; j++) { if (cells[i * N + j]) { activeSet.add(i); break; } }
  }
  document.getElementById("tmHead").innerHTML =
    `<tr><th>日期 \\ 时间</th>${HOURS_TREND.map(h => `<th>${h}</th>`).join("")}</tr>`;
  let bd = "";
  for (let i = 0; i < keys.length; i++) {
    if (!activeSet.has(i)) continue;
    const p = keys[i].split("/"), dow = DAYS_TREND[dts[i].getDay()];
    bd += `<tr><td>${parseInt(p[1])}/${parseInt(p[2])} 周${dow}</td>`;
    for (let j = 0; j < N; j++) {
      const v = cells[i * N + j] || 0;
      let c = "v0"; if (v >= 10) c = "v5"; else if (v >= 7) c = "v4"; else if (v >= 4) c = "v3"; else if (v >= 2) c = "v2"; else if (v >= 1) c = "v1";
      bd += `<td${v ? ` title="${keys[i]} ${HOURS_TREND[j]}:00 · ${v} 个日期"` : ""}><span class="tm-cell ${c}">${v || ""}</span></td>`;
    }
    bd += "</tr>";
  }
  document.getElementById("tmBody").innerHTML = bd;

  const hc = {}; for (const h of HOURS_TREND) hc[h] = 0;
  for (const b of batchesTrend) { const h = bjHour(b.t); if (h in hc) hc[h] += b.count; }
  const ranked = Object.entries(hc).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, 3);
  document.getElementById("top3List").innerHTML = ranked.map(([h, v], i) =>
    `<li><span><span class="r">${["\u{1F947}", "\u{1F948}", "\u{1F949}"][i]}</span><span class="h">${String(h).padStart(2, "0")}:00~${String(h).padStart(2, "0")}:59</span></span><span class="c">${v} 个</span></li>`
  ).join("") || '<li style="color:var(--text2)">暂无数据</li>';
}
