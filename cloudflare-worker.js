// Cloudflare Worker - Telegram Bot API 反向代理
// 部署到 Cloudflare Workers (免费套餐：每天10万次请求)
// 获得 URL 后，在 HF Space 设置 TELEGRAM_PROXY_URL

export default {
  async fetch(request) {
    const url = new URL(request.url);

    // 转发到 Telegram API
    const tgUrl = "https://api.telegram.org" + url.pathname + url.search;

    const headers = new Headers(request.headers);
    headers.set("Host", "api.telegram.org");

    const init = {
      method: request.method,
      headers: headers,
    };

    if (request.method !== "GET" && request.method !== "HEAD") {
      init.body = await request.arrayBuffer();
    }

    const tgRequest = new Request(tgUrl, init);
    const response = await fetch(tgRequest);

    // 添加 CORS 头
    const newHeaders = new Headers(response.headers);
    newHeaders.set("Access-Control-Allow-Origin", "*");
    newHeaders.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    newHeaders.set("Access-Control-Allow-Headers", "Content-Type");

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: newHeaders,
    });
  },
};
