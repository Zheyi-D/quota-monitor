/**
 * quota-monitor Worker v10 — 邮件订阅/退订 + 管理员群发 + 飞书 DM 订阅 API
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS, GET",
  "Access-Control-Allow-Headers": "Content-Type",
};

function json(d, s) { return new Response(JSON.stringify(d), {status:s||200, headers:{"Content-Type":"application/json",...CORS}}); }
function html(body) { return new Response(`<!DOCTYPE html><html lang="zh-HK"><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><body style="font-family:sans-serif;text-align:center;padding:48px 16px">${body}</body></html>`, {headers:{"Content-Type":"text/html; charset=utf-8",...CORS}}); }

function isValidEmail(email) {
  return /^[^\s@]{1,100}@[^\s@]{1,100}\.[^\s@]{2,20}$/.test(email);
}

async function fetchWithTimeout(url, opts, ms) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  try { return await fetch(url, { ...opts, signal: ctrl.signal }); }
  finally { clearTimeout(t); }
}

// ── AES-GCM Encryption ───────────────────────────────────────────────

async function getKey(env) {
  const raw = Uint8Array.from(atob(env.ENCRYPTION_KEY), c => c.charCodeAt(0));
  return await crypto.subtle.importKey("raw", raw, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}

async function encryptData(key, plaintext) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const encoded = new TextEncoder().encode(plaintext);
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, encoded);
  const combined = new Uint8Array(iv.length + ct.byteLength);
  combined.set(iv);
  combined.set(new Uint8Array(ct), iv.length);
  return { enc: true, data: btoa(String.fromCharCode(...combined)) };
}

async function decryptData(key, data) {
  if (!data || !data.enc) return data;
  const akey = key;
  const buf = new Uint8Array(atob(data.data).split("").map(c => c.charCodeAt(0)));
  const iv = buf.slice(0, 12);
  const ct = buf.slice(12);
  const decrypted = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, akey, ct);
  return JSON.parse(new TextDecoder().decode(decrypted));
}

// ── GitHub Helpers ────────────────────────────────────────────────────

async function readJSON(env, path) {
  const headers = {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    "User-Agent": "quota",
    Accept: "application/vnd.github.v3+json",
  };
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/${path}`;

  const resp = await fetchWithTimeout(url, { headers }, 10000);
  if (resp.status === 404) return { raw: null, sha: null };
  if (!resp.ok) throw new Error(`read ${path}: ${resp.status}`);

  const d = await resp.json();
  let decoded;
  try {
    const wrapper = JSON.parse(atob(d.content));
    if (wrapper.enc && env.ENCRYPTION_KEY) {
      const key = await getKey(env);
      decoded = await decryptData(key, wrapper);
    } else {
      decoded = wrapper;
    }
  } catch {
    try { decoded = JSON.parse(atob(d.content)); } catch { decoded = []; }
  }
  return { raw: decoded, sha: d.sha };
}

async function writeJSON(env, path, data, sha, commitMsg) {
  const headers = {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    "User-Agent": "quota",
    Accept: "application/vnd.github.v3+json",
    "Content-Type": "application/json",
  };
  const url = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/${path}`;

  let contentObj;
  if (env.ENCRYPTION_KEY) {
    const key = await getKey(env);
    contentObj = await encryptData(key, JSON.stringify(data));
  } else {
    contentObj = data;
  }
  const content = btoa(JSON.stringify(contentObj, null, 2) + "\n");

  const body = { message: commitMsg, content };
  if (sha) body.sha = sha;

  const resp = await fetchWithTimeout(url, {
    method: "PUT",
    headers,
    body: JSON.stringify(body),
  }, 10000);

  if (!resp.ok) throw new Error(`write ${path}: ${resp.status} ${await resp.text()}`);
}

// ── Feishu API Constants (used by admin-send) ────────────────────────

const FEISHU_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal";
const FEISHU_MSG_URL = "https://open.feishu.cn/open-apis/im/v1/messages";

// ── Event Log Helper ───────────────────────────────────────────────

async function appendFeishuLog(env, action, openId, dates) {
  try {
    const { raw: log, sha } = await readJSON(env, "data/feishu_subs_log.json");
    const list = Array.isArray(log) ? log : [];
    list.push({
      time: new Date().toISOString(),
      action,
      open_id: openId,
      dates: dates || [],
    });
    // Keep last 500 entries
    const trimmed = list.length > 500 ? list.slice(-500) : list;
    await writeJSON(env, "data/feishu_subs_log.json", trimmed, sha, `${action}: ${openId}`);
  } catch { /* best-effort */ }
}

// ── Password Check Helper ──────────────────────────────────────────

function checkAdmin(env, body) {
  const pwd = env.ADMIN_PASSWORD;
  if (!pwd) return false;
  return body && body.password === pwd;
}

// ── Main Handler ────────────────────────────────────────────────────

export default {
  async fetch(request, env) {

    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }

    if (url.pathname === "/" || url.pathname === "/health") {
      return json({ ok: true });
    }

    // ── Feishu DM Subscribe REST API ──
    // Called by feishu-ws-client.js (GitHub Actions 长连接)

    if (request.method === "POST" && url.pathname === "/api/feishu-subscribe") {
      let body;
      try { body = await request.json(); } catch { return json({ok:false,msg:"bad json"},400); }
      const openId = (body.open_id || "").trim();
      const dates = Array.isArray(body.dates) ? body.dates : [];
      if (!openId) return json({ok:false,msg:"open_id required"},400);

      try {
        const { raw: subs, sha } = await readJSON(env, "data/feishu_subs.json");
        const list = Array.isArray(subs) ? subs : [];
        const idx = list.findIndex(s => s.open_id === openId);
        const entry = { open_id: openId, dates, subscribed_at: new Date().toISOString() };
        const action = idx >= 0 ? "updated" : "subscribed";
        if (idx >= 0) list[idx] = entry;
        else list.push(entry);
        await writeJSON(env, "data/feishu_subs.json", list, sha, `Feishu ${action}: ${openId}`);
        await appendFeishuLog(env, "subscribe", openId, dates);
        return json({ ok: true, open_id: openId, dates, action });
      } catch (err) {
        return json({ ok: false, msg: err.message }, 500);
      }
    }

    if (request.method === "POST" && url.pathname === "/api/feishu-unsubscribe") {
      let body;
      try { body = await request.json(); } catch { return json({ok:false,msg:"bad json"},400); }
      const openId = (body.open_id || "").trim();
      if (!openId) return json({ok:false,msg:"open_id required"},400);

      try {
        const { raw: subs, sha } = await readJSON(env, "data/feishu_subs.json");
        const list = Array.isArray(subs) ? subs : [];
        const filtered = list.filter(s => s.open_id !== openId);
        if (filtered.length === list.length) return json({ok:false,msg:"not found"});
        await writeJSON(env, "data/feishu_subs.json", filtered, sha, `Feishu unsubscribe: ${openId}`);
        await appendFeishuLog(env, "unsubscribe", openId, []);
        return json({ok:true,open_id:openId,action:"unsubscribed"});
      } catch (err) {
        return json({ ok: false, msg: err.message }, 500);
      }
    }

    if (request.method === "GET" && url.pathname === "/api/feishu-status") {
      const openId = url.searchParams.get("open_id");
      if (!openId) return json({ok:false,msg:"open_id required"},400);
      try {
        const { raw: subs } = await readJSON(env, "data/feishu_subs.json");
        const list = Array.isArray(subs) ? subs : [];
        const entry = list.find(s => s.open_id === openId);
        return json({ ok: true, subscribed: !!entry, dates: entry ? entry.dates : null });
      } catch (err) {
        return json({ ok: false, msg: err.message }, 500);
      }
    }

    // ── Admin APIs (all require password) ────────────────────────────

    // Stats
    if (request.method === "POST" && url.pathname === "/api/admin/stats") {
      let body;
      try { body = await request.json(); } catch { return json({ok:false,msg:"bad json"},400); }
      if (!checkAdmin(env, body)) return json({ok:false,msg:"unauthorized"},403);

      try {
        // Email count
        const { raw: emails } = await readJSON(env, "data/subscribers.json");
        const emailCount = Array.isArray(emails) ? emails.length : 0;

        // DM active count
        const { raw: dmSubs } = await readJSON(env, "data/feishu_subs.json");
        const dmList = Array.isArray(dmSubs) ? dmSubs : [];
        const dmActive = dmList.length;
        const dmAll = dmList.filter(s => !s.dates || s.dates.length === 0).length;
        const dmPick = dmList.filter(s => s.dates && s.dates.length > 0).length;

	        // Daily stats from log (non-fatal: file may not exist yet)
	        let dailyNew = 0, dailyUnsub = 0;
	        const everIds = new Set(dmList.map(s => s.open_id));
	        try {
	          const { raw: log } = await readJSON(env, "data/feishu_subs_log.json");
	          const logList = Array.isArray(log) ? log : [];
	          // BJT today: UTC+8
	          const bjt = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
	          dailyNew = logList.filter(e => e.action === "subscribe" && e.time.slice(0,10) === bjt).length;
	          dailyUnsub = logList.filter(e => e.action === "unsubscribe" && e.time.slice(0,10) === bjt).length;
	          logList.forEach(e => everIds.add(e.open_id));
	        } catch (_) { /* log file read failed, use dmActive as fallback */ }
	        const everSubscribed = everIds.size;

        return json({ ok: true, email_count: emailCount, dm_active: dmActive, dm_all: dmAll, dm_pick: dmPick, dm_daily_new: dailyNew, dm_daily_unsub: dailyUnsub, dm_ever: everSubscribed });
      } catch (err) {
        return json({ ok: false, msg: err.message }, 500);
      }
    }

    // Email subscriber list
    if (request.method === "POST" && url.pathname === "/api/admin/subscribers") {
      let body;
      try { body = await request.json(); } catch { return json({ok:false,msg:"bad json"},400); }
      if (!checkAdmin(env, body)) return json({ok:false,msg:"unauthorized"},403);

      try {
        const { raw: emails } = await readJSON(env, "data/subscribers.json");
        const list = Array.isArray(emails) ? emails : [];
        return json({ ok: true, subscribers: list, total: list.length });
      } catch (err) {
        return json({ ok: false, msg: err.message }, 500);
      }
    }

    // DM subscriber list
    if (request.method === "POST" && url.pathname === "/api/admin/dm-subscribers") {
      let body;
      try { body = await request.json(); } catch { return json({ok:false,msg:"bad json"},400); }
      if (!checkAdmin(env, body)) return json({ok:false,msg:"unauthorized"},403);

      try {
        const { raw: dmSubs } = await readJSON(env, "data/feishu_subs.json");
        const list = Array.isArray(dmSubs) ? dmSubs : [];
        return json({ ok: true, subscribers: list, total: list.length });
      } catch (err) {
        return json({ ok: false, msg: err.message }, 500);
      }
    }

    // DM mass send
    if (request.method === "POST" && url.pathname === "/api/admin/dm-send") {
      let body;
      try { body = await request.json(); } catch { return json({ok:false,msg:"bad json"},400); }
      if (!checkAdmin(env, body)) return json({ok:false,msg:"unauthorized"},403);

      const text = (body.text || "").trim();
      if (!text || text.length > 4000) return json({ ok: false, msg: "text empty or too long (max 4000)" }, 400);

      const appId = env.FEISHU_APP_ID;
      const appSecret = env.FEISHU_APP_SECRET;
      if (!appId || !appSecret) return json({ ok: false, msg: "Feishu not configured" }, 500);

      try {
        const { raw: dmSubs } = await readJSON(env, "data/feishu_subs.json");
        const list = Array.isArray(dmSubs) ? dmSubs : [];
        if (list.length === 0) return json({ ok: false, msg: "no DM subscribers" });

        // Get token
        const tokenResp = await fetch(FEISHU_TOKEN_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ app_id: appId, app_secret: appSecret }),
        });
        const tokenData = await tokenResp.json();
        if (tokenData.code !== 0) throw new Error(`token: ${tokenData.msg}`);
        const token = tokenData.tenant_access_token;

        let sent = 0, failed = 0;
        for (const sub of list) {
          try {
            const resp = await fetch(`${FEISHU_MSG_URL}?receive_id_type=open_id`, {
              method: "POST",
              headers: {
                "Authorization": `Bearer ${token}`,
                "Content-Type": "application/json",
              },
              body: JSON.stringify({
                receive_id: sub.open_id,
                msg_type: "interactive",
                content: JSON.stringify({
                  header: { title: { content: "📢 系统消息", tag: "plain_text" }, template: "blue" },
                  elements: [{ tag: "markdown", content: text }],
                }),
              }),
            });
            const data = await resp.json();
            if (data.code === 0) sent++;
            else { failed++; console.error(`DM to ${sub.open_id.slice(0,16)}: ${data.msg}`); }
          } catch { failed++; }
          // Rate limit: 500ms per user
          await new Promise(r => setTimeout(r, 500));
        }

        return json({ ok: true, msg: `sent to ${sent}/${list.length} users`, sent, failed });
      } catch (err) {
        return json({ ok: false, msg: err.message }, 500);
      }
    }

    // ── Email Subscribe ───────────────────────────────────────────────

    if (request.method === "POST" && url.pathname === "/api/subscribe") {
      let body;
      try { body = await request.json(); } catch { return json({ok:false,msg:"bad json"},400); }
      const email = (body.email || "").trim().toLowerCase();
      if (!isValidEmail(email)) return json({ok:false,msg:"bad email"},400);

      try {
        const { raw: emails, sha } = await readJSON(env, "data/subscribers.json");
        const list = Array.isArray(emails) ? emails : [];
        if (list.includes(email)) return json({ ok: true, already_subscribed: true });
        list.push(email);
        await writeJSON(env, "data/subscribers.json", list, sha, "Subscribe: new subscriber");
        return json({ ok: true, total: list.length });
      } catch (err) {
        return json({ ok: false, msg: err.message }, 500);
      }
    }

    // ── Email Unsubscribe ─────────────────────────────────────────────

    if (url.pathname === "/api/unsubscribe") {
      let email;
      if (request.method === "POST") {
        try { const b = await request.json(); email = b.email; } catch { email = ""; }
      } else {
        email = url.searchParams.get("email") || "";
      }
      email = email.trim().toLowerCase();
      if (!isValidEmail(email)) {
        return request.method === "POST" ? json({ok:false,msg:"bad email"},400) : html("<h2>❌ 邮箱格式不正确</h2>");
      }

      try {
        const { raw: emails, sha } = await readJSON(env, "data/subscribers.json");
        const list = Array.isArray(emails) ? emails : [];
        const before = list.length;
        const filtered = list.filter(e => e !== email);
        if (filtered.length === before) {
          return request.method === "POST" ? json({ok:false,msg:"not found"}) : html("<h2>📭 该邮箱不在订阅列表中</h2>");
        }
        await writeJSON(env, "data/subscribers.json", filtered, sha, "Unsubscribe");

        try {
          const { raw: w, sha: ws } = await readJSON(env, "data/welcomed.json");
          const wList = Array.isArray(w) ? w : [];
          const wFiltered = wList.filter(e => e !== email);
          if (wFiltered.length !== wList.length) {
            await writeJSON(env, "data/welcomed.json", wFiltered, ws, "Unsubscribe (clean welcomed)");
          }
        } catch { /* best-effort */ }

        return request.method === "POST" ? json({ok:true,msg:"unsubscribed"}) : html("<h2>✅ 退订成功</h2>");
      } catch (err) {
        return request.method === "POST" ? json({ok:false,msg:err.message},500) : html("<h2>❌ 退订失败</h2>");
      }
    }

    // ── Admin send to Feishu group ────────────────────────────────────

    if (request.method === "POST" && url.pathname === "/api/admin-send") {
      const password = env.ADMIN_PASSWORD;
      if (!password) return json({ ok: false, msg: "ADMIN_PASSWORD not configured" }, 500);

      let body;
      try { body = await request.json(); } catch { return json({ ok: false, msg: "bad json" }, 400); }
      if (body.password !== password) return json({ ok: false, msg: "wrong password" }, 403);

      if (body.auth_only) return json({ ok: true, msg: "auth ok" });

      const text = (body.text || "").trim();
      if (!text || text.length > 4000) return json({ ok: false, msg: "text empty or too long (max 4000)" }, 400);

      const appId = env.FEISHU_APP_ID;
      const appSecret = env.FEISHU_APP_SECRET;
      const chatIdsRaw = env.FEISHU_CHAT_ID;
      if (!appId || !appSecret || !chatIdsRaw) {
        return json({ ok: false, msg: "Feishu credentials not configured" }, 500);
      }

      const chatIds = chatIdsRaw.split(",").map(c => c.trim()).filter(Boolean);

      try {
        const tokenResp = await fetch(FEISHU_TOKEN_URL, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ app_id: appId, app_secret: appSecret }),
        });
        const tokenData = await tokenResp.json();
        if (tokenData.code !== 0) throw new Error(`token: ${tokenData.msg}`);

        let okCount = 0;
        const sends = chatIds.map(cid =>
          fetch(`${FEISHU_MSG_URL}?receive_id_type=chat_id`, {
            method: "POST",
            headers: {
              Authorization: `Bearer ${tokenData.tenant_access_token}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              receive_id: cid,
              msg_type: "interactive",
              content: JSON.stringify({
                header: { title: { content: "📢 系统消息", tag: "plain_text" }, template: "blue" },
                elements: [{ tag: "markdown", content: text }],
              }),
            }),
          }).then(async r => { const d = await r.json(); if (d.code === 0) okCount++; else console.error(`send to ${cid}: ${d.msg}`); })
        );
        await Promise.all(sends);

        return json({ ok: okCount > 0, msg: `sent to ${okCount}/${chatIds.length} groups` });
      } catch (err) {
        return json({ ok: false, msg: err.message }, 500);
      }
    }

    return json({ ok: false, msg: "not found" }, 404);
  },
};
