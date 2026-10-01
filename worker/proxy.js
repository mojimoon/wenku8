// Cloudflare Worker：转发请求到 wenku8（仅白名单域名），用于测试/代理抓取。
// 环境变量（Secrets）：TOKEN（共享口令）
const ALLOW = ['www.wenku8.net', 'pic.777743.xyz', 'img.wenku8.com'];

export default {
  async fetch(req, env) {
    if (req.headers.get('X-Token') !== env.TOKEN) return new Response('forbidden', { status: 403 });
    const target = new URL(req.url).searchParams.get('url');
    let u;
    try { u = new URL(target); } catch { return new Response('bad url', { status: 400 }); }
    if (!ALLOW.includes(u.hostname)) return new Response('host not allowed', { status: 400 });
    const headers = { 'User-Agent': 'Mozilla/5.0' };
    const cookie = req.headers.get('X-Cookie');
    if (cookie) headers['Cookie'] = cookie;
    const r = await fetch(u.toString(), { headers, redirect: 'manual' });
    const out = new Response(r.body, { status: r.status });
    out.headers.set('Content-Type', r.headers.get('Content-Type') || 'application/octet-stream');
    out.headers.set('Access-Control-Allow-Origin', '*');
    return out;
  },
};
