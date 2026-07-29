/**
 * quota-monitor Worker v8 — 订阅 + 退订
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

async function modifySubscribers(env, action, email) {
  const headers = {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    "User-Agent": "quota",
    Accept: "application/vnd.github.v3+json",
  };
  const ghUrl = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/data/subscribers.json`;

  const r1 = await fetchWithTimeout(ghUrl, { headers }, 10000);

  let emails = [], sha = null;
  if (r1.status === 200) {
    const d = await r1.json();
    try { emails = JSON.parse(atob(d.content)); } catch { emails = []; }
    if (!Array.isArray(emails)) emails = [];
    sha = d.sha;
  } else if (r1.status !== 404) {
    throw new Error(`read failed: ${r1.status}`);
  }

  if (action === "subscribe") {
    if (emails.includes(email)) return { added: false, total: emails.length, emails };
    emails.push(email);
  } else {
    const before = emails.length;
    emails = emails.filter(e => e !== email);
    if (emails.length === before) return { added: false, total: emails.length, emails, notFound: true };
  }

  const content = btoa(JSON.stringify(emails, null, 2) + "\n");
  const body = { message: action === "subscribe" ? "Subscribe: new subscriber" : "Unsubscribe", content };
  if (sha) body.sha = sha;

  const r2 = await fetchWithTimeout(ghUrl, {
    method: "PUT",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, 10000);

  if (!r2.ok) throw new Error(`write failed: ${r2.status}`);
  return { added: action === "subscribe", total: emails.length, emails };
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }

    // ── Health ──
    if (url.pathname === "/" || url.pathname === "/health") {
      return json({ ok: true });
    }

    // ── Subscribe (POST from web page) ──
    if (request.method === "POST" && url.pathname === "/api/subscribe") {
      let body;
      try { body = await request.json(); } catch { return json({ok:false,msg:"bad json"},400); }
      const email = (body.email || "").trim().toLowerCase();
      if (!isValidEmail(email)) return json({ok:false,msg:"bad email"},400);

      try {
        const result = await modifySubscribers(env, "subscribe", email);
        if (!result.added) return json({ ok: true, msg: "already" });
        return json({ ok: true, msg: "subscribed", total: result.total });
      } catch (err) {
        return json({ ok: false, msg: err.message }, 500);
      }
    }

    // ── Unsubscribe (GET from email link or POST from web page) ──
    if (url.pathname === "/api/unsubscribe") {
      // Support both GET (email link) and POST (web page)
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
        const result = await modifySubscribers(env, "unsubscribe", email);
        if (result.notFound) {
          return request.method === "POST" ? json({ok:true,msg:"not found"}) : html("<h2>📭 该邮箱不在订阅列表中</h2><p>可能已经退订过了。</p>");
        }

        // Also remove from welcomed.json
        try {
          const whUrl = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/data/welcomed.json`;
          const whHeaders = {
            Authorization: `Bearer ${env.GITHUB_TOKEN}`,
            "User-Agent": "quota",
            Accept: "application/vnd.github.v3+json",
          };
          const wr = await fetchWithTimeout(whUrl, { headers: whHeaders }, 10000);
          if (wr.status === 200) {
            const wd = await wr.json();
            let welcomed = JSON.parse(atob(wd.content));
            if (Array.isArray(welcomed)) {
              welcomed = welcomed.filter(e => e !== email);
              const wContent = btoa(JSON.stringify(welcomed, null, 2) + "\n");
              await fetchWithTimeout(whUrl, {
                method: "PUT",
                headers: { ...whHeaders, "Content-Type": "application/json" },
                body: JSON.stringify({ message: "Unsubscribe", content: wContent, sha: wd.sha }),
              }, 10000);
            }
          }
        } catch { /* welcomed.json cleanup is best-effort */ }

        return request.method === "POST" ? json({ok:true,msg:"unsubscribed"}) : html("<h2>✅ 退订成功</h2><p>你已取消订阅，不会再收到配额通知邮件。</p>");
      } catch (err) {
        return request.method === "POST" ? json({ok:false,msg:err.message},500) : html("<h2>❌ 退订失败</h2><p>服务器错误，请稍后重试。</p>");
      }
    }

    return json({ ok: false, msg: "not found" }, 404);
  },
};
