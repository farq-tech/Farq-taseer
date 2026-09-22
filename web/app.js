const state = {
  view: "home",
  query: "",
  files: [],
  intent: null,
  searchState: "",
  clarification: "",
  results: [],
  partial: false,
  notice: "",
  selected: new Map(),
  active: null,
  gallery: [],
  note: "",
  requests: [],
  thread: null,
  activeSeller: "",
  replyTo: null,
  seller: null,
  sellerToken: "",
  token: localStorage.getItem("farq.token") || "",
  busy: false,
  city: "",
  cities: [
    { value: "الرياض", label: "الرياض" },
    { value: "جده", label: "جدة" },
    { value: "مكه", label: "مكة" },
    { value: "المدينة", label: "المدينة" },
    { value: "الدمام", label: "الدمام" },
    { value: "الخبر", label: "الخبر" },
  ],
};

const app = document.querySelector("#app");
let poll = 0;
const sellerRoute = location.pathname.match(/^\/s\/([^/]+)\/?$/);

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

function formatCount(value) {
  return new Intl.NumberFormat("ar-SA").format(value);
}

function ago(iso) {
  const then = Date.parse(iso || "");
  if (Number.isNaN(then)) return "";
  const minutes = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (minutes < 1) return "الآن";
  if (minutes < 60) return `منذ ${formatCount(minutes)} د`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return hours === 1 ? "منذ ساعة" : `منذ ${formatCount(hours)} س`;
  const days = Math.round(hours / 24);
  if (days === 1) return "منذ يوم";
  return `منذ ${formatCount(days)} ي`;
}

function money(amount) {
  if (amount == null || Number.isNaN(Number(amount))) return "";
  return `${new Intl.NumberFormat("ar-SA", { maximumFractionDigits: 0 }).format(amount)} ر.س`;
}

async function api(path, { method = "GET", json, form, skipAuth = false } = {}) {
  const headers = {};
  if (state.token && !skipAuth) headers.Authorization = `Bearer ${state.token}`;
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) body = form;
  const response = await fetch(path, { method, headers, body });
  if (response.status === 401 && state.token && !skipAuth) {
    state.token = "";
    localStorage.removeItem("farq.token");
    await ensureAuth();
    return api(path, { method, json, form });
  }
  if (!response.ok) {
    const error = new Error("request failed");
    error.status = response.status;
    throw error;
  }
  const type = response.headers.get("content-type") || "";
  return type.includes("json") ? response.json() : response;
}

async function ensureAuth() {
  if (state.token) return;
  const data = await api("/v1/auth/guest", { method: "POST", skipAuth: true });
  state.token = data.token;
  localStorage.setItem("farq.token", state.token);
}

function imageSources(ad) {
  if (!ad) return [];
  const urls = [...(ad.image_urls || [])];
  if (ad.image_ref && !String(ad.image_ref).startsWith("http")) {
    const name = encodeURIComponent(ad.image_ref);
    urls.push(`/v1/media/thumb?name=${name}&size=400`);
    urls.push(`/v1/media/thumb?name=${name}&size=140`);
  }
  return [...new Set(urls)];
}

function sellerOf(result) {
  return result.seller || result.ad?.seller || {};
}

function resultKey(result) {
  const seller = sellerOf(result);
  return `${seller.id || ""}:${result.ad?.id || ""}`;
}

function place(result) {
  const seller = sellerOf(result);
  return [result.ad?.city || seller.city, result.ad?.district || seller.district].filter(Boolean).join(" · ");
}

function evidenceLines(result) {
  const lines = [];
  if (result.ad?.title) lines.push(result.ad.title);
  for (const item of result.match_evidence || []) {
    if (item && item.length > 12 && !lines.includes(item)) lines.push(item);
  }
  return lines.slice(0, 3);
}

function facts(intent) {
  if (!intent) return [];
  const items = [];
  const city = typeof intent.location_city?.value === "string" ? intent.location_city.value : "";
  const models = {
    Camry: "كامري",
    "Land Cruiser": "لاندكروزر",
    "PlayStation 5": "بلايستيشن 5",
    Patrol: "باترول",
    "iPhone Pro Max": "آيفون برو ماكس",
  };
  if (intent.model?.value && models[intent.model.value]) {
    items.push(models[intent.model.value]);
    if (intent.year?.value) items.push(String(intent.year.value));
  } else {
    let phrase = intent.need || intent.original_query || "";
    if (city) {
      const escaped = city.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      phrase = phrase.replace(new RegExp(`(?:في\\s+|بال|ب)?${escaped}`, "g"), " ");
    }
    phrase = phrase.replace(/\s+/g, " ").trim();
    if (phrase) items.push(phrase);
  }
  if (intent.material?.value && !items.some((item) => item.includes(intent.material.value))) items.push(intent.material.value);
  if (intent.condition?.value === "used") items.push("مستعمل");
  if (intent.condition?.value === "new") items.push("جديد");
  if (city) items.push(city);
  return items;
}

function customerCity() {
  if (state.city) return state.city;
  const value = state.intent?.location_city?.value;
  return typeof value === "string" ? value : "";
}

function cityLabel(value) {
  const found = state.cities.find((item) => item.value === value || item.label === value);
  return found?.label || value || "";
}

function cityChoices(action) {
  return `<div class="choices">${state.cities
    .map(
      (city) =>
        `<button type="button" data-action="${action}" data-value="${esc(city.label)}" data-city="${esc(city.value)}">${esc(city.label)}</button>`,
    )
    .join("")}</div>`;
}

function bubbles(messages, mine) {
  return (messages || [])
    .map((message) => {
      const role = message.sender_role === mine ? "user" : "seller";
      const price = message.offer?.amount != null ? `<div class="offer"><strong>${esc(money(message.offer.amount))}</strong></div>` : "";
      const body = esc(message.body || "").replace(/\n/g, "<br>");
      return `<div class="bubble ${role}">${price}<div>${body}</div></div>`;
    })
    .join("");
}

function finalNotice(status, count) {
  if (status === "PARTIAL_RESULTS") return count ? "ما قدرنا نكمل البحث من حراج. هذي الخيارات اللي وصلت." : "البحث ما اكتمل.";
  if (status === "LIVE_UNAVAILABLE") return "حراج ما استجاب الحين، فما نقدر نأكد إذا فيه نتائج أو لا.";
  if (status === "TIMEOUT") return count ? "البحث طال، وهذي الخيارات اللي وصلت." : "انقطع البحث قبل ما يكتمل. جرّب مرة ثانية.";
  if (status === "NO_QUALIFIED_RESULTS") return "شفت إعلانات، بس ما فيه شيء يطابق طلبك.";
  if (status === "LIVE_EMPTY" || status === "LOCAL_EMPTY") return "ما رجع المصدر إعلان يطابق هذا الطلب.";
  if (status === "NOT_UNDERSTOOD") return "ما فهمت الطلب. اكتبه بطريقة أوضح.";
  if (status === "DELETED_AD") return "الإعلان ما عاد متاح.";
  if (status === "SELLER_UNAVAILABLE") return "البائع ما عاد متاح.";
  if (status === "STALE_AD") return "هذي الإعلانات قديمة. تأكد قبل ما ترسل.";
  if (status === "INTERNAL_ERROR") return "صار خطأ عندنا. جرّب مرة ثانية.";
  return "";
}

function bindImages(root) {
  root.querySelectorAll("img[data-src]").forEach((img) => {
    const urls = (img.dataset.src || "").split("|").filter(Boolean);
    let index = 0;
    const fail = () => img.closest("[data-frame]")?.classList.add("is-missing");
    const next = () => {
      if (index >= urls.length) {
        fail();
        img.removeAttribute("src");
        return;
      }
      img.src = urls[index];
      index += 1;
    };
    img.addEventListener("error", next);
    next();
  });
}

function frame(ad, { eager = false, thumb = false } = {}) {
  const sources = imageSources(ad);
  const className = thumb ? "frame thumb" : "frame";
  if (!sources.length) return `<div class="${className} is-missing" data-frame></div>`;
  const loading = eager ? "eager" : "lazy";
  return `<div class="${className}" data-frame><img alt="" data-src="${esc(sources.join("|"))}" loading="${loading}" decoding="async"></div>`;
}

function filePreview() {
  if (!state.files.length) return "";
  return `<div class="previews">${state.files
    .map(
      (item, index) => `<figure>${
        item.preview ? `<img src="${item.preview}" alt="">` : `<div class="file-chip">${esc(item.file.name)}</div>`
      }<button type="button" data-action="remove-file" data-index="${index}" aria-label="حذف ${esc(item.file.name)}">×</button></figure>`,
    )
    .join("")}</div>`;
}

function icon(name, { size = 20, flip = false, label = "" } = {}) {
  return `<img class="${flip ? "flip" : ""}" src="/icons/${name}.svg" width="${size}" height="${size}" alt="${esc(label)}">`;
}

function initial(name) {
  const text = String(name || "ف").trim();
  return esc(text.charAt(0) || "ف");
}

function topBar({ title = "فرق تسعير", back = "", end = '<span class="slot" aria-hidden="true"></span>', lined = false } = {}) {
  const start = back || '<span class="slot" aria-hidden="true"></span>';
  return `<header class="top-bar${lined ? " lined" : ""}">${end}<a class="brand-title" href="/" data-action="home">${esc(title)}</a>${start}</header>`;
}

function tabBar(active) {
  return `<nav class="tab-bar" aria-label="التنقل">
    <button class="tab${active === "home" ? " active" : ""}" type="button" data-action="home">${icon("home")}<span>الرئيسية</span></button>
    <button class="tab${active === "requests" ? " active" : ""}" type="button" data-action="requests">${icon("briefcase")}<span>طلباتي</span></button>
    <button class="tab" type="button" disabled aria-disabled="true">${icon("settings")}<span>الإعدادات</span></button>
  </nav>`;
}

function shell(body, { bare = false } = {}) {
  if (bare) return `<main class="shell">${body}</main>`;
  return `<main class="shell">${body}</main>`;
}

function renderHome() {
  const chips = ["سباك بالرياض", "كهربائي بجدة", "نقل عفش"];
  return `${topBar()}
  <section class="page home">
    <h1>وش تبي نسعّر لك؟</h1>
    <p class="lede">اكتب اللي تحتاجه وخلنا ندور لك</p>
    <form class="composer" id="composer">
      <div class="field">
        <label class="sr" for="composer-query">وش تبي؟</label>
        <textarea id="composer-query" name="query" placeholder="مثال: أبي سباك وأبي كهربائي بالرياض">${esc(state.query)}</textarea>
      </div>
      <div class="suggestions">
        <span>اقتراحات سريعة</span>
        <div class="chips">${chips.map((idea) => `<button type="button" data-action="idea" data-query="${esc(idea)}">${esc(idea)}</button>`).join("")}</div>
      </div>
      <button class="primary block" type="submit">ابحث</button>
    </form>
  </section>
  ${tabBar("home")}`;
}

function renderSearching() {
  return `${topBar()}
  <section class="page searching" aria-live="polite">
    <div class="pulse"><div class="pulse-mid"><div class="pulse-core">${icon("search-white", { size: 20 })}</div></div></div>
    <div><h2>ندور لك...</h2><p class="lede">نبحث في أكثر من 2,000 جهة</p></div>
    <div class="dots" aria-hidden="true"><span class="on"></span><span class="on"></span><span></span></div>
    <div class="original-card"><span>طلبك الأصلي</span><strong>${esc(state.query)}</strong></div>
  </section>`;
}

function renderFacts() {
  const items = facts(state.intent);
  if (!items.length) return "";
  const city = typeof state.intent?.location_city?.value === "string" ? state.intent.location_city.value : "";
  const need = items.filter((item) => item !== city)[0] || state.intent?.need || state.query;
  return `<div class="need-card">${city ? `<span class="loc">${esc(cityLabel(city))}</span>` : "<span></span>"}<div class="copy"><div class="glyph">${initial(need)}</div><div><strong>${esc(need)}</strong><em>فهمنا طلبك وجاهزين ندور</em></div></div></div>`;
}

function renderQuestion() {
  const question = state.clarification || "";
  const aboutCity = question.includes("مدينة");
  return `<section class="section-head"><h1>${aboutCity ? "حدد المدينة" : "كمّل الطلب"}</h1><p class="lede">${esc(question)}</p></section>
    ${aboutCity ? cityChoices("answer") : ""}
    <form id="answer" class="answer"><input name="value" placeholder="جوابك" autocomplete="off"><button class="primary" type="submit">كمّل</button></form>`;
}

function snip(result) {
  const lines = evidenceLines(result);
  const text = result.ad?.description || lines[1] || lines[0] || "";
  return String(text).replace(/\s+/g, " ").trim().slice(0, 72);
}

function renderCard(result) {
  const key = resultKey(result);
  const selected = state.selected.has(key);
  const seller = sellerOf(result);
  const name = result.ad?.title || seller.name || "جهة";
  const price = money(result.ad?.price_amount);
  const city = result.ad?.city || seller.city || "";
  const blurb = snip(result);
  const thumb = result.ad && imageSources(result.ad).length ? frame(result.ad, { thumb: true }) : `<span class="mark">${initial(seller.name || name)}</span>`;
  return `<article class="vendor${selected ? " is-selected" : ""}">
    <button class="vendor-body" type="button" data-action="open" data-key="${esc(key)}">
      ${thumb}
      <span class="vendor-copy">
        <strong>${esc(name)}</strong>
        <span class="vendor-meta">${seller.name ? `<span>${esc(seller.name)}</span>` : ""}${seller.name && city ? " · " : ""}${city ? `<span class="muted">${esc(cityLabel(city))}</span>` : ""}</span>
        ${blurb ? `<span class="snip">${esc(blurb)}</span>` : ""}
        <span class="price">${esc(price || "السعر عند الطلب")}</span>
      </span>
    </button>
    <button class="tick" type="button" data-action="toggle" data-key="${esc(key)}" aria-pressed="${selected}" aria-label="${selected ? "إزالة الجهة" : "اختيار الجهة"}">${selected ? icon("check", { size: 12 }) : ""}</button>
  </article>`;
}

function renderFlow() {
  const asking = state.searchState === "CLARIFICATION_REQUIRED" || state.searchState === "LOCATION_AMBIGUOUS";
  const live = state.partial && (state.searchState === "LIVE_SEARCHING" || state.searchState === "PARTIAL_RESULTS");
  const showEmpty = !asking && !state.partial && state.results.length === 0;
  if (live && !state.results.length && !asking) return renderSearching();
  const need = state.intent?.need || facts(state.intent)[0] || state.query || "النتائج";
  const back = `<button class="icon-btn" type="button" data-action="home" aria-label="رجوع">${icon("chevron", { size: 20 })}</button>`;
  return `${topBar({ title: asking ? "فرق تسعير" : "النتائج", back })}
  <section class="page tight flow">
    ${asking ? "" : `<div class="group-head"><span>${formatCount(state.results.length)} جهة مطابقة</span><strong>${esc(need)}</strong></div>`}
    ${asking ? renderFacts() + renderQuestion() : ""}
    <p class="status ${live ? "live" : ""}" aria-live="polite">${esc(showEmpty ? "" : state.notice)}</p>
    ${showEmpty ? `<div class="empty"><h2>${esc(state.notice || "ما فيه شيء نعرضه")}</h2><button class="text-btn" type="button" data-action="retry">جرّب مرة ثانية</button></div>` : ""}
    ${state.results.length ? `<div class="cards">${state.results.map(renderCard).join("")}</div>` : ""}
    ${dock()}
  </section>`;
}

function dock() {
  const count = state.selected.size;
  if (!count || state.view === "review") return "";
  const label = count === 1 ? "اطلب السعر من جهة واحدة" : `اطلب السعر من ${formatCount(count)} جهات`;
  return `<div class="dock"><p class="hint">سنرسل طلبك لكل الجهات المختارة</p><button class="primary compact" type="button" data-action="review">${esc(label)}</button></div>`;
}

function renderDetail() {
  const result = state.active;
  if (!result) return renderFlow();
  const seller = sellerOf(result);
  const gallery = state.gallery.length ? state.gallery : imageSources(result.ad);
  const frames = gallery.length
    ? gallery
        .map((url) => {
          const sources = [...new Set([url, ...imageSources(result.ad)])].join("|");
          return `<div class="frame" data-frame><img alt="" data-src="${esc(sources)}" loading="lazy" decoding="async"></div>`;
        })
        .join("")
    : `<div class="frame is-missing" data-frame></div>`;
  const price = money(result.ad?.price_amount);
  const key = resultKey(result);
  const selected = state.selected.has(key);
  const back = `<button class="icon-btn" type="button" data-action="back-results" aria-label="رجوع">${icon("chevron", { size: 20 })}</button>`;
  return `${topBar({ title: "التفاصيل", back })}
  <article class="page tight detail">
    <div class="gallery">${frames}</div>
    <h1 style="font-size:22px">${esc(result.ad?.title || seller.name || "")}</h1>
    ${price ? `<p class="price">${esc(price)}</p>` : `<p class="meta">السعر عند الطلب</p>`}
    <p class="meta">${esc(place(result))}${seller.name ? ` · ${esc(seller.name)}` : ""}</p>
    ${result.ad?.description ? `<p class="story">${esc(result.ad.description)}</p>` : ""}
    ${result.ad?.url ? `<p><a href="${esc(result.ad.url)}" target="_blank" rel="noopener">الإعلان في حراج</a></p>` : ""}
    ${result.ad?.listing_state === "deleted" ? `<p class="warn">هذا الإعلان محذوف.</p>` : ""}
    <div class="detail-actions"><button class="primary" type="button" data-action="quote" data-key="${esc(key)}">${selected ? "كمّل طلب عرض السعر" : "طلب عرض سعر"}</button></div>
    ${dock()}
  </article>`;
}

function renderReview() {
  const names = [...state.selected.entries()].map(([key, result]) => ({ key, name: sellerOf(result).name || "بائع" }));
  const city = customerCity();
  const ready = state.selected.size > 0 && Boolean(city);
  const back = `<button class="icon-btn" type="button" data-action="back-results" aria-label="رجوع">${icon("chevron", { size: 20 })}</button>`;
  return `${topBar({ title: "طلب عرض سعر", back })}
  <section class="page tight review">
    <h1>جاهز نرسله؟</h1>
    <p class="summary">${esc(state.query)}</p>
    <ul class="who">${names.map((item) => `<li><span>${esc(item.name)}</span><button class="text-btn" type="button" data-action="unselect" data-key="${esc(item.key)}">شيل</button></li>`).join("")}</ul>
    ${city ? `<p class="meta">المدينة: ${esc(cityLabel(city))}</p>` : `<div class="section-head"><h1 style="font-size:20px">في أي مدينة؟</h1></div>${cityChoices("pick-city")}`}
    <label>ملاحظة<textarea class="note" id="note" placeholder="اختياري">${esc(state.note)}</textarea></label>
    <p class="optional">الصورة والفيديو اختياريين. تقدر ترسل الطلب بدونها.</p>
    <div class="composer-bar">
      <label class="file-btn">${icon("camera")} صورة<input type="file" accept="image/*" data-action="add-files"></label>
      <label class="file-btn">${icon("paperclip")} فيديو<input type="file" accept="video/*" data-action="add-files"></label>
    </div>
    ${filePreview()}
    <button class="primary block" type="button" data-action="send" ${ready ? "" : "disabled"}>${state.busy ? "نرسل…" : "أرسل طلب عرض السعر"}</button>
  </section>`;
}

function renderRequests() {
  const logo = `<span class="slot" aria-hidden="true"></span>`;
  const title = `<a class="brand-mark" href="/" data-action="home"><span class="logo" aria-hidden="true"></span><span class="farq-en">Farq</span> <span class="farq-ar">فرق</span></a>`;
  const head = `<header class="top-bar lined">${logo}${title}${logo}</header>`;
  if (!state.requests.length) {
    return `${head}<section class="page soft requests-page"><p class="lede">لما ترسل طلب عرض سعر، يبين هنا.</p></section>${tabBar("requests")}`;
  }
  return `${head}<section class="page soft requests-page">${state.requests
    .map((item) => {
      const need = item.need || item.original_text;
      const when = ago(item.last_message_at || item.created_at);
      const replied = item.replied_count || 0;
      const sent = item.recipient_count || 0;
      const from = item.latest_offer_amount != null ? `من ${money(item.latest_offer_amount)}` : "";
      return `<button class="request-card" type="button" data-action="thread" data-id="${esc(item.id)}">
        <div class="row"><span class="when">${esc(when || "")}</span><strong class="title">${esc(need)}</strong></div>
        <hr>
        <div class="row">
          <div class="stats"><strong>${replied ? `وصلت ${formatCount(replied)} أسعار` : "بانتظار الرد"}</strong><span>أرسل إلى ${formatCount(sent)} جهات</span></div>
          <span class="from">${esc(from || "بانتظار السعر")}</span>
        </div>
        ${item.last_message ? `<div class="request-foot"><span>${esc(item.last_message)}</span></div>` : ""}
      </button>`;
    })
    .join("")}</section>${tabBar("requests")}`;
}

function snippet(text, size = 50) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  return value.length > size ? `${value.slice(0, size)}…` : value;
}

function sellerName(thread, sellerId) {
  return (thread.recipients || []).find((item) => item.seller_id === sellerId)?.seller_name || "جهة";
}

function messagePrice(message) {
  return message.offer?.total_price ?? message.offer?.amount ?? null;
}

function deliveryLabel(thread, message) {
  const deliveries = message.deliveries || [];
  if (!deliveries.length) return "";
  const to = message.scope === "single_seller" && message.seller_id ? `إلى ${sellerName(thread, message.seller_id)} فقط` : `للكل (${formatCount(deliveries.length)})`;
  const sent = deliveries.filter((item) => item.status === "sent").length;
  const failed = deliveries.filter((item) => item.status === "failed").length;
  let status = "بانتظار الإرسال لحراج";
  if (message.delivery_state === "sent") status = "وصلت في حراج";
  else if (message.delivery_state === "partial") status = `وصلت لـ ${formatCount(sent)} · تعذرت ${formatCount(failed)}`;
  else if (message.delivery_state === "failed") status = "تعذر الإرسال";
  return `${to} · ${status}`;
}

// The customer's conversation for one item. Sellers never see it: each has only their own Haraj
// conversation, and every message here is routed to or synced from those.
function threadBubbles(thread, messages) {
  const byId = new Map((thread.messages || []).map((item) => [item.id, item]));
  return messages
    .map((message) => {
      const body = esc(message.body || "").replace(/\n/g, "<br>");
      const quoted = message.reply_to ? byId.get(message.reply_to) : null;
      const quote = quoted ? `<span class="bubble-quote">${esc(quoted.sender_role === "seller" ? sellerName(thread, quoted.seller_id) : "أنت")}: ${esc(snippet(quoted.body))}</span>` : "";
      if (message.sender_role !== "seller") {
        const label = deliveryLabel(thread, message);
        return `<div class="bubble user">${quote}<div>${body}</div>${label ? `<span class="bubble-to">${esc(label)}</span>` : ""}</div>`;
      }
      const price = messagePrice(message);
      return `<div class="bubble seller">
        <div class="bubble-head"><button class="bubble-name" type="button" data-action="seller-filter" data-seller="${esc(message.seller_id || "")}">${esc(sellerName(thread, message.seller_id))}</button>${price != null ? `<span class="bubble-verb">قدّم سعر</span>` : ""}</div>
        ${price != null ? `<div class="offer"><strong>${esc(money(price))}</strong></div>` : ""}
        <div>${body}</div>
        <button class="bubble-reply" type="button" data-action="reply" data-message="${esc(message.id)}">ردّ عليه</button>
      </div>`;
    })
    .join("");
}

function renderThread() {
  const thread = state.thread;
  if (!thread) return `<section class="page"><p>نحمّل المحادثة…</p></section>`;
  const one = state.activeSeller;
  const recipients = thread.recipients || [];
  // Tapping a seller only filters this conversation to what went into or came from their Haraj chat.
  const messages = (thread.messages || []).filter(
    (message) => !one || message.seller_id === one || (message.scope === "all_sellers" && (message.deliveries || []).some((item) => item.seller_id === one)),
  );
  const offers = (thread.offers || []).filter((item) => item.total_price != null && (!one || item.seller_id === one));
  const best = [...offers].sort((a, b) => a.total_price - b.total_price)[0];
  const replied = new Set((thread.messages || []).filter((item) => item.sender_role === "seller").map((item) => item.seller_id));
  const title = one ? sellerName(thread, one) : recipients.map((item) => item.seller_name).join(" · ");
  const subtitle = one ? "محادثته في حراج" : thread.need || thread.original_text || "";
  const back = `<button class="icon-btn" type="button" data-action="${one ? "all-sellers" : "requests"}" aria-label="رجوع">${icon("chevron", { size: 20 })}</button>`;
  const phone = `<span class="icon-btn" aria-hidden="true">${icon("phone", { size: 20 })}</span>`;
  const sync = thread.last_synced_at ? `آخر مزامنة مع حراج ${ago(thread.last_synced_at)}` : "ما تمت مزامنة حراج بعد";
  const target = state.replyTo;
  return `<header class="chat-head">${phone}<div class="who-line"><strong>${esc(title || "المحادثة")}</strong><span class="meta">${esc(subtitle)}</span></div>${back}</header>
  <section class="page soft chat-page">
    ${best ? `<div class="quote-card"><div class="row"><span class="tag">${one ? "عرضه" : "أرخص عرض"}</span><strong>الإجمالي: ${esc(money(best.total_price))}</strong></div><p>${one ? "" : `${esc(best.provider_name || sellerName(thread, best.seller_id))} · `}${esc(thread.need || thread.original_text || "")}${thread.city ? ` في ${esc(cityLabel(thread.city))}` : ""}</p></div>` : ""}
    <p class="chat-hint">${one ? "رسايلك هنا توصل له بس." : `${replied.size ? `ردّ ${formatCount(replied.size)} من ${formatCount(recipients.length)} · ` : ""}رسالتك توصل للكل في حراج. «ردّ عليه» أو @الاسم توصل له بس.`}</p>
    <div class="thread"><p class="day">${esc(sync)}</p>${threadBubbles(thread, messages) || `<p class="meta">بانتظار الرد.</p>`}</div>
  </section>
  ${target ? `<div class="reply-target"><span>ترد على <strong>${esc(target.name)}</strong>: ${esc(snippet(target.body, 40))}</span><button type="button" data-action="cancel-reply" aria-label="إلغاء">✕</button></div>` : ""}
  <div class="mention-list" id="mention-list" hidden></div>
  <form class="reply-form" id="user-reply">
    <label class="icon-btn" aria-label="إرفاق اختياري">${icon("camera")}<input type="file" accept="image/*,video/*" data-action="add-files"></label>
    <input name="body" placeholder="${one ? "رسالة له بس..." : "اكتب للكل، أو @ لجهة معيّنة..."}" autocomplete="off">
    <button class="send-icon" type="submit" aria-label="إرسال">${icon("send", { size: 18 })}</button>
  </form>`;
}

function renderSeller() {
  const seller = state.seller;
  if (!seller) return `<section class="page"><h1>${esc(state.notice || "نحمّل المحادثة…")}</h1></section>`;
  const options = seller.recipients || [];
  const partner = options[0]?.seller_name || "الجهة";
  return `<header class="seller-head">
      <span class="mark round">${initial(partner)}</span>
      <div class="who-line"><strong>${esc(partner)}</strong><span class="meta">${esc([seller.need || seller.original_text, seller.city ? cityLabel(seller.city) : ""].filter(Boolean).join(" · "))}</span></div>
    </header>
    <section class="page soft">
    ${(seller.attachments || []).map((item) => `<p><a href="/v1/seller/${esc(state.sellerToken)}/attachments/${esc(item.id)}">${esc(item.filename)}</a></p>`).join("")}
    <div class="thread"><p class="day">اليوم</p>${bubbles(seller.messages, "seller") || `<p class="meta">بانتظار الرسالة.</p>`}</div>
    <form id="seller-reply">
      ${options.length > 1 ? `<select name="seller_id">${options.map((item) => `<option value="${esc(item.seller_id)}">${esc(item.seller_name)}</option>`).join("")}</select>` : `<input type="hidden" name="seller_id" value="${esc(options[0]?.seller_id || "")}">`}
      <input name="amount" inputmode="decimal" placeholder="السعر، إذا عندك" autocomplete="off">
      <textarea name="body" rows="2" placeholder="اكتب رسالة..."></textarea>
      <button class="send-icon" type="submit" aria-label="إرسال">${icon("send", { size: 18 })}</button>
    </form>
    ${state.notice ? `<p class="status">${esc(state.notice)}</p>` : ""}
    </section>`;
}

function render() {
  clearInterval(poll);
  const view = {
    home: renderHome,
    flow: renderFlow,
    detail: renderDetail,
    review: renderReview,
    requests: renderRequests,
    thread: renderThread,
    seller: renderSeller,
  }[state.view] || renderHome;
  const focused = document.activeElement?.id;
  app.innerHTML = shell(view());
  bindImages(app);
  if (focused) document.getElementById(focused)?.focus();
  if (state.view === "thread" && state.thread?.id) poll = setInterval(() => loadThread(state.thread.id, true), 4000);
  if (state.view === "seller" && state.sellerToken) poll = setInterval(() => loadSeller(state.sellerToken, true), 4000);
}

function rememberFiles(fileList) {
  for (const file of fileList) {
    state.files.push({
      file,
      preview: file.type.startsWith("image/") ? URL.createObjectURL(file) : "",
    });
  }
}

async function readNdjson(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";
    for (const line of lines) if (line.trim()) onEvent(JSON.parse(line));
  }
  if (buffer.trim()) onEvent(JSON.parse(buffer));
}

function applyDone(event) {
  state.intent = event.intent;
  state.results = event.results || [];
  state.searchState = event.state;
  state.clarification = event.clarification_question || "";
  state.partial = false;
  const asking = event.state === "CLARIFICATION_REQUIRED" || event.state === "LOCATION_AMBIGUOUS";
  state.notice = asking ? "" : finalNotice(event.state, state.results.length);
}

async function runSearch(text) {
  const query = (text || "").trim();
  if (!query) return;
  state.query = query;
  state.city = "";
  state.view = "flow";
  state.partial = true;
  state.searchState = "";
  state.results = [];
  state.intent = null;
  state.clarification = "";
  state.notice = "نفهم طلبك…";
  state.selected.clear();
  render();
  try {
    const headers = { "Content-Type": "application/json" };
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    const response = await fetch("/v1/search/stream", { method: "POST", headers, body: JSON.stringify({ query }) });
    if (!response.ok || !response.body) throw new Error("stream");
    await readNdjson(response, (event) => {
      if (event.type === "intent") {
        state.intent = event.intent;
        state.clarification = event.clarification_question || "";
        if (!state.results.length && state.partial) state.notice = "نفهم طلبك…";
      } else if (event.type === "status") {
        state.searchState = event.state;
        state.partial = true;
        state.notice = state.results.length ? "لقينا خيارات مناسبة، وقاعدين ندور لك على أكثر." : "ندور في حراج…";
      } else if (event.type === "results") {
        state.results = event.results || [];
        state.partial = true;
        state.searchState = event.state;
        if (state.results.length) state.notice = "لقينا خيارات مناسبة، وقاعدين ندور لك على أكثر.";
      } else if (event.type === "done") applyDone(event);
      render();
    });
  } catch (_error) {
    try {
      const done = await api("/v1/search", { method: "POST", json: { query } });
      applyDone(done);
    } catch (_fallback) {
      state.partial = false;
      state.searchState = "INTERNAL_ERROR";
      state.notice = finalNotice("INTERNAL_ERROR", 0);
    }
    render();
  }
}

async function openResult(key) {
  const result = state.results.find((item) => resultKey(item) === key);
  if (!result) return;
  state.active = result;
  state.gallery = imageSources(result.ad);
  state.view = "detail";
  render();
  if (!result.ad?.url) return;
  try {
    const data = await api(`/v1/listings/images?url=${encodeURIComponent(result.ad.url)}`);
    if (data.images?.length) {
      state.gallery = [...new Set([...data.images, ...state.gallery])];
      if (state.view === "detail" && state.active === result) render();
    }
  } catch (_error) {
    /* the thumbnail remains */
  }
}

function selectResult(key) {
  const result = state.results.find((item) => resultKey(item) === key) || (state.active && resultKey(state.active) === key ? state.active : null);
  if (result && sellerOf(result).id) state.selected.set(key, result);
}

function toggle(key) {
  if (state.selected.has(key)) state.selected.delete(key);
  else selectResult(key);
  render();
}

function openQuote(key) {
  selectResult(key);
  if (!state.selected.has(key)) return;
  state.view = "review";
  render();
}

async function sendRequest() {
  const city = customerCity();
  if (!state.selected.size || !city || state.busy) return;
  state.busy = true;
  render();
  try {
    await ensureAuth();
    const attributes = {};
    if (state.intent?.material?.value) attributes.material = state.intent.material.value;
    if (state.intent?.condition?.value === "used") attributes.condition = "مستعمل";
    if (state.intent?.condition?.value === "new") attributes.condition = "جديد";
    if (state.intent?.year?.value) attributes.year = state.intent.year.value;
    const created = await api("/v1/requests", {
      method: "POST",
      json: {
        original_text: state.query,
        need: state.intent?.need || null,
        notes: state.note || null,
        city,
        attributes,
        recipients: [...state.selected.values()].map((result) => {
          const seller = sellerOf(result);
          return { seller_id: seller.id, seller_name: seller.name || "بائع", ad_id: result.ad?.id || null };
        }),
      },
    });
    for (const item of state.files) {
      const form = new FormData();
      form.append("file", item.file);
      await api(`/v1/requests/${created.id}/attachments`, { method: "POST", form });
    }
    state.busy = false;
    state.files = [];
    state.note = "";
    state.thread = created;
    state.view = "thread";
    await loadThread(created.id);
  } catch (_error) {
    state.notice = "ما قدرنا نرسل الطلب. جرّب مرة ثانية.";
    state.busy = false;
    state.view = "review";
    render();
  }
}

async function loadRequests() {
  await ensureAuth();
  state.view = "requests";
  state.requests = (await api("/v1/requests")).requests || [];
  render();
}

async function loadThread(id, silent = false) {
  await ensureAuth();
  const thread = await api(`/v1/requests/${id}`);
  const previous = JSON.stringify(state.thread?.messages || []);
  state.thread = thread;
  state.view = "thread";
  if (!silent || previous !== JSON.stringify(thread.messages || [])) render();
}

async function loadSeller(token, silent = false) {
  state.view = "seller";
  state.sellerToken = token;
  if (!silent) state.notice = "";
  try {
    const seller = await api(`/v1/seller/${token}`, { skipAuth: true });
    const previous = JSON.stringify(state.seller?.messages || []);
    state.seller = seller;
    if (!silent || previous !== JSON.stringify(seller.messages || [])) render();
  } catch (_error) {
    state.seller = null;
    state.notice = "ما لقينا الطلب.";
    render();
  }
}

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form.id === "composer" || form.id === "refine") {
    event.preventDefault();
    runSearch(new FormData(form).get("query"));
  } else if (form.id === "answer") {
    event.preventDefault();
    const value = new FormData(form).get("value");
    if (value) runSearch(`${state.query} ${value}`);
  } else if (form.id === "user-reply") {
    event.preventDefault();
    const body = String(new FormData(form).get("body") || "").trim();
    if (!body || !state.thread) return;
    const recipients = state.thread.recipients || [];
    const json = { body, need: state.thread.need || null };
    if (state.activeSeller) json.seller_id = state.activeSeller;
    else if (state.replyTo) json.reply_to = state.replyTo.id;
    else {
      const named = recipients.filter((item) => item.seller_name && body.includes(`@${item.seller_name}`));
      json.seller_id = named.sort((a, b) => b.seller_name.length - a.seller_name.length)[0]?.seller_id || null;
    }
    api(`/v1/requests/${state.thread.id}/messages`, { method: "POST", json })
      .then(() => {
        state.replyTo = null;
        return loadThread(state.thread.id);
      })
      .catch(() => {});
  } else if (form.id === "seller-reply") {
    event.preventDefault();
    const data = new FormData(form);
    const amount = Number(String(data.get("amount") || "").replace(/[^\d.]/g, ""));
    api(`/v1/seller/${state.sellerToken}/messages`, {
      method: "POST",
      skipAuth: true,
      json: {
        body: data.get("body") || "",
        seller_id: data.get("seller_id") || null,
        offer_amount: Number.isFinite(amount) && amount > 0 ? amount : null,
        offer_currency: "SAR",
      },
    })
      .then(() => loadSeller(state.sellerToken))
      .catch(() => {
        state.notice = "ما انرسل الرد.";
        render();
      });
  }
});

document.addEventListener("input", (event) => {
  if (event.target.id === "note") state.note = event.target.value;
  if (event.target.name === "query") state.query = event.target.value;
  if (event.target.closest("#user-reply") && event.target.name === "body" && !state.activeSeller && state.thread) {
    const list = document.getElementById("mention-list");
    const typed = event.target.value.match(/@([^@]*)$/);
    const names = typed ? (state.thread.recipients || []).filter((item) => item.seller_name.includes(typed[1].trim())) : [];
    if (list) {
      list.hidden = !names.length;
      list.innerHTML = names.map((item) => `<button type="button" data-action="mention" data-name="${esc(item.seller_name)}">${esc(item.seller_name)}</button>`).join("");
    }
  }
});

document.addEventListener("change", async (event) => {
  if (event.target.dataset.action !== "add-files") return;
  const files = [...(event.target.files || [])];
  if (!files.length) return;
  if (state.view === "thread" && state.thread?.id) {
    try {
      await ensureAuth();
      for (const file of files) {
        const form = new FormData();
        form.append("file", file);
        await api(`/v1/requests/${state.thread.id}/attachments`, { method: "POST", form });
      }
      await loadThread(state.thread.id);
    } catch (_error) {
      state.notice = "ما انرفق الملف. تقدر ترسل الرسالة بدونه.";
      render();
    }
    return;
  }
  rememberFiles(files);
  render();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.target.name !== "query") return;
  event.preventDefault();
  runSearch(event.target.value);
});

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  if (action === "home") {
    event.preventDefault();
    state.view = "home";
    history.pushState({}, "", "/");
    render();
  } else if (action === "idea") runSearch(target.dataset.query);
  else if (action === "requests") loadRequests().catch(() => {});
  else if (action === "remove-file") {
    const removed = state.files.splice(Number(target.dataset.index), 1)[0];
    if (removed?.preview) URL.revokeObjectURL(removed.preview);
    render();
  } else if (action === "open") openResult(target.dataset.key);
  else if (action === "toggle") toggle(target.dataset.key);
  else if (action === "quote") openQuote(target.dataset.key);
  else if (action === "unselect") {
    state.selected.delete(target.dataset.key);
    render();
  } else if (action === "pick-city") {
    state.city = target.dataset.city || "";
    render();
  } else if (action === "review") {
    state.view = "review";
    render();
  } else if (action === "back-results") {
    state.view = "flow";
    render();
  } else if (action === "retry") runSearch(state.query);
  else if (action === "answer") runSearch(`${state.query} ${target.dataset.value}`);
  else if (action === "send") sendRequest();
  else if (action === "thread") {
    state.activeSeller = "";
    state.replyTo = null;
    loadThread(target.dataset.id).catch(() => {});
  } else if (action === "seller-filter") {
    state.activeSeller = target.dataset.seller || "";
    state.replyTo = null;
    render();
  } else if (action === "all-sellers") {
    state.activeSeller = "";
    render();
  } else if (action === "reply") {
    const message = (state.thread?.messages || []).find((item) => item.id === target.dataset.message);
    if (!message) return;
    state.replyTo = { id: message.id, name: sellerName(state.thread, message.seller_id), body: message.body };
    render();
    document.querySelector("#user-reply input[name=body]")?.focus();
  } else if (action === "cancel-reply") {
    state.replyTo = null;
    render();
  } else if (action === "mention") {
    const input = document.querySelector("#user-reply input[name=body]");
    if (!input) return;
    input.value = input.value.replace(/@[^@]*$/, `@${target.dataset.name} `);
    document.getElementById("mention-list").hidden = true;
    input.focus();
  }
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.view === "thread" && state.thread?.id) {
    loadThread(state.thread.id, true).catch(() => {});
  }
});

if (sellerRoute) loadSeller(decodeURIComponent(sellerRoute[1]));
else render();

api("/v1/cities", { skipAuth: true })
  .then((data) => {
    if (data.cities?.length) state.cities = data.cities;
    if (state.view === "flow" || state.view === "review") render();
  })
  .catch(() => {});
