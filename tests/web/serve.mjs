// A static server for web/ that answers every /v1 call from fixtures, so the layout
// checks need no backend and no network.
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const WEB = fileURLToPath(new URL("../../web/", import.meta.url));
const TYPES = { ".js": "text/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml", ".png": "image/png", ".json": "application/json", ".webmanifest": "application/manifest+json", ".md": "text/markdown" };

// ---- fixtures -------------------------------------------------------------------------------
const LONG_AR = "السلام عليكم ورحمة الله، ".repeat(6) + "نحتاج توريد حديد تسليح مقاس ١٦ مم بكمية ٢٠ طن مع التوصيل إلى الموقع في حي الملقا شمال الرياض، والدفع عند الاستلام بعد الفحص.".repeat(3);
const LONG_TOKEN = "https://haraj.com.sa/11223344556677889900/%D8%AD%D8%AF%D9%8A%D8%AF-%D8%AA%D8%B3%D9%84%D9%8A%D8%AD-%D9%85%D9%82%D8%A7%D8%B3-16?ref=farq&utm=aaaaaaaaaaaaaaaaaaaaaaaaaaaa 0501234567890123456789012345";
const LONG_NAME = "مؤسسة الأفق الذهبي للمقاولات العامة والتوريدات الإنشائية المحدودة فرع شمال الرياض";
const now = Date.now();
const at = (min) => new Date(now - min * 60_000).toISOString();

function thread() {
  const messages = [
    { id: "m1", sender_role: "customer", body: "أبغى سعر ٢٠ طن حديد ١٦ مم", created_at: at(90), scope: "all_sellers", deliveries: [{ seller_id: "s1", status: "sent" }, { seller_id: "s2", status: "sent" }] },
    { id: "m2", sender_role: "seller", seller_id: "s1", body: "هلا، متوفر", created_at: at(80) },
    { id: "m3", sender_role: "seller", seller_id: "s2", body: LONG_AR, created_at: at(70) },
    { id: "m4", sender_role: "seller", seller_id: "s1", body: LONG_TOKEN, created_at: at(60) },
    { id: "m5", sender_role: "customer", body: LONG_AR, created_at: at(50), scope: "all_sellers", deliveries: [{ seller_id: "s1", status: "sent" }, { seller_id: "s2", status: "sent" }] },
    { id: "m6", sender_role: "customer", body: LONG_TOKEN, created_at: at(40), scope: "all_sellers", deliveries: [{ seller_id: "s1", status: "sent" }] },
    { id: "m7", sender_role: "seller", seller_id: "s2", body: "السعر شامل التوصيل", offer_amount: 48250, total_price: 48250, created_at: at(30) },
    { id: "m8", sender_role: "customer", body: "تمام", created_at: at(20), scope: "all_sellers", deliveries: [{ seller_id: "s1", status: "sent" }, { seller_id: "s2", status: "sent" }] },
    { id: "last", sender_role: "seller", seller_id: "s1", body: "آخر رسالة", created_at: at(1) },
  ];
  return {
    id: "t1",
    need: "حديد تسليح ١٦ مم — ٢٠ طن",
    original_text: "حديد تسليح",
    city: "riyadh",
    recipients: [{ seller_id: "s1", seller_name: "مؤسسة البناء" }, { seller_id: "s2", seller_name: LONG_NAME }],
    offers: [{ seller_id: "s2", total_price: 48250 }],
    messages,
  };
}

function seller() {
  const t = thread();
  return {
    need: t.need,
    original_text: t.original_text,
    city: "riyadh",
    offers_open: true,
    messages: t.messages.map((m) => ({ ...m, sender_role: m.sender_role === "seller" ? "seller" : "customer" })),
  };
}

function answer(path) {
  if (/^\/v1\/requests\/[^/]+$/.test(path)) return thread();
  if (path === "/v1/requests") return { requests: [{ id: "t1", need: thread().need, unread: 0, created_at: at(90) }] };
  if (path.startsWith("/v1/seller/")) return seller();
  if (path === "/v1/auth/me") return { id: "u1", name: "عميل", email: "c@example.com", email_verified: true };
  if (path === "/v1/subscriptions/me") return { active: true, subscription: { plan: "pro" } };
  if (path === "/v1/cities") return { cities: [] };
  return {};
}

// ---- static server --------------------------------------------------------------------------
export function serve(port = 0) {
  const server = createServer(async (req, res) => {
    const url = new URL(req.url, "http://x");
    if (url.pathname.startsWith("/v1/")) {
      res.writeHead(200, { "content-type": "application/json" });
      return res.end(JSON.stringify(answer(url.pathname)));
    }
    let file = normalize(join(WEB, url.pathname));
    if (!file.startsWith(WEB) || !extname(file)) file = join(WEB, "index.html");
    try {
      const body = await readFile(file);
      res.writeHead(200, { "content-type": TYPES[extname(file)] || "application/octet-stream" });
      res.end(body);
    } catch {
      res.writeHead(200, { "content-type": "text/html" });
      res.end(await readFile(join(WEB, "index.html")));
    }
  });
  return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve(server)));
}


// node serve.mjs → prints the address, for poking at the fixtures by hand.
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const server = await serve(Number(process.env.PORT || 0));
  console.log(`http://127.0.0.1:${server.address().port}`);
}
