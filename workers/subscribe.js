/**
 * quota-monitor 邮箱订阅 Cloudflare Worker v2
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

const SUBSCRIBERS_PATH = "data/subscribers.json";
const API_BASE = "https://api.github.com";

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json", ...CORS },
  });
}

function ok(msg, extra = {}) {
  return json({ ok: true, message: msg, ...extra });
}

function fail(msg, status = 400) {
  return json({ ok: false, message: msg }, status);
}

function isValidEmail(email) {
  return /^[^\s@]{1,100}@[^\s@]{1,100}\.[^\s@]{2,20}$/.test(email);
}

// ── GitHub API helpers ──

async function ghGet(path, token, repo) {
  const url = `${API_BASE}/repos/${repo}/contents/${path}`;
  const resp = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      "User-Agent": "quota-monitor",
      Accept: "application/vnd.github.v3+json",
    },
  });
  // 404 means file doesn't exist yet — that's ok
  if (resp.status === 404) {
    return { exists: false, emails: [], sha: null };
  }
  if (!resp.ok) {
    throw new Error(`GET ${resp.status}: ${(await resp.text()).substring(0, 200)}`);
  }
  const data = await resp.json();
  const decoder = new TextDecoder();
  const raw = decoder.decode(Uint8Array.from(atob(data.content), c => c.charCodeAt(0)));
  const emails = JSON.parse(raw);
  return { exists: true, emails: Array.isArray(emails) ? emails : [], sha: data.sha };
}

async function ghPut(path, emails, sha, token, repo, newEmail) {
  const content = btoa(unescape(encodeURIComponent(JSON.stringify(emails, null, 2) + "\n")));
  const url = `${API_BASE}/repos/${repo}/contents/${path}`;
  const body = {
    message: `📧 Subscribe: ${newEmail}`,
    content,
  };
  if (sha) body.sha = sha;

  const resp = await fetch(url, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${token}`,
      "User-Agent": "quota-monitor",
      Accept: "application/vnd.github.v3+json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    throw new Error(`PUT ${resp.status}: ${(await resp.text()).substring(0, 200)}`);
  }
  return await resp.json();
}

// ── Main handler ──

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }

    if (request.method !== "POST" || url.pathname !== "/api/subscribe") {
      return json({ ok: false, message: "Not Found" }, 404);
    }

    const token = env.GITHUB_TOKEN;
    const repo = env.GITHUB_REPO;

    // Debug: check env vars (remove in production)
    console.log("repo:", repo, "token_len:", token ? token.length : 0);

    if (!token) {
      return fail("GITHUB_TOKEN 未配置", 500);
    }
    if (!repo) {
      return fail("GITHUB_REPO 未配置", 500);
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return fail("请求格式错误，需要 JSON");
    }

    const email = (body.email || "").trim().toLowerCase();
    if (!email || !isValidEmail(email)) {
      return fail("邮箱格式无效");
    }

    try {
      const { emails, sha } = await ghGet(SUBSCRIBERS_PATH, token, repo);
      if (emails.includes(email)) {
        return ok("已订阅过了！", { already_subscribed: true });
      }
      emails.push(email);
      await ghPut(SUBSCRIBERS_PATH, emails, sha, token, repo, email);
      return ok("订阅成功！", { total_subscribers: emails.length });
    } catch (err) {
      console.error("error:", err.message);
      return fail("服务器错误: " + err.message.substring(0, 100), 500);
    }
  },
};
