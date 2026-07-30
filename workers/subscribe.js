/**
 * quota-monitor Worker v9 — 订阅 + 退订 + AES 加密存储
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

// ── AES-GCM Encryption ──

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
  if (!data || !data.enc) return data;  // plain JSON (backward compat)
  const akey = key;
  const buf = new Uint8Array(atob(data.data).split("").map(c => c.charCodeAt(0)));
  const iv = buf.slice(0, 12);
  const ct = buf.slice(12);
  const decrypted = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, akey, ct);
  return JSON.parse(new TextDecoder().decode(decrypted));
}

// ── GitHub Helpers ──

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
    // 如果是加密数据，解密
    if (wrapper.enc && env.ENCRYPTION_KEY) {
      const key = await getKey(env);
      decoded = await decryptData(key, wrapper);
    } else {
      decoded = wrapper;
    }
  } catch {
    // 旧格式明文
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

  // 加密
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

  if (!resp.ok) throw new Error(`write ${path}: ${resp.status}`);
}

// ── Main Handler ──

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }

    if (url.pathname === "/" || url.pathname === "/health") {
      return json({ ok: true });
    }

    // ── Subscribe (POST) ──
    if (request.method === "POST" && url.pathname === "/api/subscribe") {
      let body;
      try { body = await request.json(); } catch { return json({ok:false,msg:"bad json"},400); }
      const email = (body.email || "").trim().toLowerCase();
      if (!isValidEmail(email)) return json({ok:false,msg:"bad email"},400);

      try {
        const { raw: emails, sha } = await readJSON(env, "data/subscribers.json");
        const list = Array.isArray(emails) ? emails : [];
        if (list.includes(email)) return json({ ok: true, msg: "already" });
        list.push(email);
        await writeJSON(env, "data/subscribers.json", list, sha, "Subscribe: new subscriber");
        return json({ ok: true, msg: "subscribed", total: list.length });
      } catch (err) {
        return json({ ok: false, msg: err.message }, 500);
      }
    }

    // ── Unsubscribe ──
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

        // Also clean welcomed.json
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

    // ── Admin send message to Feishu group (POST) ──
    if (request.method === "POST" && url.pathname === "/api/admin-send") {
      // 密码核验
      const password = env.ADMIN_PASSWORD;
      if (!password) return json({ ok: false, msg: "ADMIN_PASSWORD not configured" }, 500);

      let body;
      try { body = await request.json(); } catch { return json({ ok: false, msg: "bad json" }, 400); }
      if (body.password !== password) return json({ ok: false, msg: "wrong password" }, 403);

      // 仅校验密码，不发消息
      if (body.auth_only) return json({ ok: true, msg: "auth ok" });

      const text = (body.text || "").trim();
      if (!text || text.length > 4000) return json({ ok: false, msg: "text empty or too long (max 4000)" }, 400);

      // 发送飞书消息
      const appId = env.FEISHU_APP_ID;
      const appSecret = env.FEISHU_APP_SECRET;
      const chatId = env.FEISHU_CHAT_ID;
      if (!appId || !appSecret || !chatId) {
        return json({ ok: false, msg: "Feishu credentials not configured" }, 500);
      }

      try {
        // 获取 token
        const tokenResp = await fetch("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ app_id: appId, app_secret: appSecret }),
        });
        const tokenData = await tokenResp.json();
        if (tokenData.code !== 0) throw new Error(`token: ${tokenData.msg}`);

        // 发送消息
        const msgResp = await fetch(
          `https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id`,
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${tokenData.tenant_access_token}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              receive_id: chatId,
              msg_type: "interactive",
              content: JSON.stringify({
                header: { title: { content: "📢 群主消息", tag: "plain_text" }, template: "blue" },
                elements: [{ tag: "markdown", content: text }],
              }),
            }),
          }
        );
        const msgData = await msgResp.json();
        if (msgData.code !== 0) throw new Error(`send: ${msgData.msg}`);

        return json({ ok: true, msg: "sent" });
      } catch (err) {
        return json({ ok: false, msg: err.message }, 500);
      }
    }

    return json({ ok: false, msg: "not found" }, 404);
  },
};
