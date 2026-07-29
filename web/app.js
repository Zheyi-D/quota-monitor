/**
 * HK IMMD Appointment Quota Dashboard
 * 纯前端渲染，从 data/quota.json 读取数据
 */

const OFFICES = {
  FTO: "火炭辦事處",
  RHK: "港島辦事處",
  RKO: "九龍辦事處",
  RTK: "觀塘辦事處",
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

const DAYS_PER_PAGE = 14;
const DAY_NAMES = ["日", "一", "二", "三", "四", "五", "六"];

// Cloudflare Worker 地址（部署后替换为实际 URL）
const SUBSCRIBE_URL = "https://quota-monitor.YOUR_SUBDOMAIN.workers.dev/api/subscribe";

let quotaData = null;       // { "MM/DD/YYYY|OFFICE|R": "quota-g", ... }
let allDates = [];           // sorted unique dates
let currentPageStart = 0;

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
          "更新時間：" + new Date().toLocaleString("zh-HK");
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

  const end = Math.min(currentPageStart + DAYS_PER_PAGE, allDates.length);
  const visibleDates = allDates.slice(currentPageStart, end);

  renderTableHeader(visibleDates);
  renderTableBody(visibleDates);
  updateToolbar(visibleDates);
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
    html += `<th class="${cls}">${formatDateShort(date)}<br>${DAY_NAMES[dow]}</th>`;
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

function updateToolbar(dates) {
  if (!dates.length) return;

  const start = dates[0];
  const end = dates[dates.length - 1];
  document.getElementById("dateRange").textContent =
    `${formatDateShort(start)} — ${formatDateShort(end)}`;

  document.getElementById("btnPrev").disabled = currentPageStart <= 0;
  document.getElementById("btnNext").disabled =
    currentPageStart + DAYS_PER_PAGE >= allDates.length;
}

// ─── Navigation ───────────────────────────────────────────────

function goToToday() {
  const today = formatToday();
  const idx = allDates.indexOf(today);
  if (idx >= 0) {
    currentPageStart = Math.max(0, idx - Math.floor(DAYS_PER_PAGE / 3));
    currentPageStart = Math.min(
      currentPageStart,
      Math.max(0, allDates.length - DAYS_PER_PAGE)
    );
  }
  render();
}

function goPrev() {
  currentPageStart = Math.max(0, currentPageStart - DAYS_PER_PAGE);
  render();
}

function goNext() {
  currentPageStart = Math.min(
    allDates.length - DAYS_PER_PAGE,
    currentPageStart + DAYS_PER_PAGE
  );
  render();
}

// ─── Helpers ──────────────────────────────────────────────────

function formatDateShort(dateStr) {
  // "MM/DD/YYYY" -> "M/D"
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
      showMsg(data.already_subscribed ? "已订阅过了！" : "订阅成功！", "success");
      if (!data.already_subscribed) {
        input.value = "";
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

// ─── Init ─────────────────────────────────────────────────────

async function init() {
  try {
    await loadData();
    goToToday();
  } catch (err) {
    document.getElementById("loading").classList.add("hidden");
    document.getElementById("error").classList.remove("hidden");
    document.getElementById("error").textContent =
      "⚠️ " + err.message + " 請稍後再試，或直接訪問入境處官網查詢。";
  }

  // Setup event listeners
  document.getElementById("btnToday").addEventListener("click", goToToday);
  document.getElementById("btnPrev").addEventListener("click", goPrev);
  document.getElementById("btnNext").addEventListener("click", goNext);
  document.getElementById("subscribeBtn").addEventListener("click", handleSubscribe);
  document.getElementById("subscribeEmail").addEventListener("keydown", (e) => {
    if (e.key === "Enter") handleSubscribe();
  });

  // Keyboard navigation
  document.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") goPrev();
    if (e.key === "ArrowRight") goNext();
  });

  // Auto-refresh every 5 minutes
  setInterval(async () => {
    try {
      await loadData();
      render();
    } catch (_) { /* silent on auto-refresh */ }
  }, 5 * 60 * 1000);
}

init();
