/**
 * quota-monitor 邮箱订阅 Worker v6
 */

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "POST, OPTIONS, GET",
  "Access-Control-Allow-Headers": "Content-Type",
};

function json(d, s) { return new Response(JSON.stringify(d), {status:s||200, headers:{"Content-Type":"application/json",...CORS}}); }

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS });
    }

    if (url.pathname === "/" || url.pathname === "/health") {
      return json({ ok: true });
    }

    if (request.method === "POST" && url.pathname === "/api/subscribe") {
      let body;
      try { body = await request.json(); } catch { return json({ok:false},400); }

      const email = (body.email || "").trim().toLowerCase();
      if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
        return json({ok:false,msg:"bad email"},400);
      }

      const headers = {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        "User-Agent": "quota",
        Accept: "application/vnd.github.v3+json",
      };

      const apiUrl = `https://api.github.com/repos/${env.GITHUB_REPO}/contents/data/subscribers.json`;

      // Step 1: read
      const r1 = await fetch(apiUrl, { headers });
      if (r1.status !== 200 && r1.status !== 404) {
        return json({ok:false, step:"read", httpStatus:r1.status}, 500);
      }

      let emails = [], sha = null;
      if (r1.status === 200) {
        const d = await r1.json();
        const raw = atob(d.content);
        emails = JSON.parse(raw);
        sha = d.sha;
        if (!Array.isArray(emails)) emails = [];
      }

      if (emails.includes(email)) {
        return json({ ok: true, msg: "already" });
      }

      // Step 2: write
      emails.push(email);
      const content = btoa(JSON.stringify(emails,null,2)+"\n");
      const r2 = await fetch(apiUrl, {
        method: "PUT",
        headers: {...headers, "Content-Type":"application/json"},
        body: JSON.stringify({message:`Subscribe: ${email}`,content,...(sha?{sha}:{})}),
      });

      if (r2.ok) {
        return json({ok:true,msg:"subscribed",total:emails.length});
      }
      const t = await r2.text();
      return json({ok:false,step:"write",code:r2.status,detail:t.substring(0,200)},500);
    }

    return json({ok:false,msg:"not found"},404);
  },
};
