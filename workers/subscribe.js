/**
 * quota-monitor 邮箱订阅 Cloudflare Worker
 *
 * 部署步骤：
 * 1. `npx wrangler deploy` 或通过 Cloudflare Dashboard 粘贴
 * 2. 设置环境变量：
 *    - GITHUB_TOKEN: GitHub Personal Access Token (classic), scope: repo
 *    - GITHUB_REPO: Zheyi-D/quota-monitor
 * 3. 获取 Worker URL，填入 web/app.js 的 SUBSCRIBE_URL
 */

// CORS headers
const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

const SUBSCRIBERS_PATH = "data/subscribers.json";

function json(data, status = 200) {
  return new Response(JSON.stringify(data, null, 2), {
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

// 基础邮箱格式校验
function isValidEmail(email) {
  return /^[^\s@]{1,100}@[^\s@]{1,100}\.[^\s@]{2,20}$/.test(email);
}

async function fetchSubscribers(token, repo) {
  const url = `https://api.github.com/repos/${repo}/contents/${SUBSCRIBERS_PATH}`;
  const resp = await fetch(url, {
    headers: {
      Authorization: `Bearer ${token}`,
      "User-Agent": "quota-monitor-worker",
      Accept: "application/vnd.github.v3+json",
    },
  });

  if (!resp.ok) {
    if (resp.status === 404) {
      // 文件不存在，返回空数组
      return { emails: [], sha: null };
    }
    const body = await resp.text();
    throw new Error(`GitHub GET failed: ${resp.status} ${body.substring(0, 200)}`);
  }

  const data = await resp.json();
  // content is base64-encoded
  const raw = atob(data.content);
  const emails = JSON.parse(raw);
  return { emails: Array.isArray(emails) ? emails : [], sha: data.sha };
}

async function putSubscribers(token, repo, emails, sha) {
  const content = btoa(JSON.stringify(emails, null, 2) + "\n");
  const url = `https://api.github.com/repos/${repo}/contents/${SUBSCRIBERS_PATH}`;
  const body = {
    message: `📧 Subscribe: ${emails[emails.length - 1]}`,
    content,
    ...(sha ? { sha } : {}),
  };

  const resp = await fetch(url, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${token}`,
      "User-Agent": "quota-monitor-worker",
      Accept: "application/vnd.github.v3+json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  if (!resp.ok) {
    const errBody = await resp.text();
    throw new Error(`GitHub PUT failed: ${resp.status} ${errBody.substring(0, 200)}`);
  }

  return await resp.json();
}

async function handleSubscribe(request, token, repo) {
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

  // 常见免费邮箱域名保护
  if (email.length > 200) {
    return fail("邮箱地址过长");
  }

  const { emails, sha } = await fetchSubscribers(token, repo);

  if (emails.includes(email)) {
    return ok("已订阅过了！", { already_subscribed: true });
  }

  emails.push(email);
  await putSubscribers(token, repo, emails, sha);

  return ok("订阅成功！", { total_subscribers: emails.length });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }

    if (request.method === "POST" && url.pathname === "/api/subscribe") {
      const token = env.GITHUB_TOKEN;
      const repo = env.GITHUB_REPO;

      if (!token || !repo) {
        return fail("服务器配置缺失", 500);
      }

      try {
        return await handleSubscribe(request, token, repo);
      } catch (err) {
        console.error("subscribe error:", err.message);
        return fail("服务器内部错误，请稍后重试", 500);
      }
    }

    return fail("Not Found", 404);
  },
};
