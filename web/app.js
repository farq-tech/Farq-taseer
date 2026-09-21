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

function shell(body) {
  return `<header class="top"><a class="brand-lockup" href="/" data-action="home"><span class="brand-en">FARQ</span><span class="brand-ar">فرق تسعير</span></a><button class="avatar" type="button" data-action="requests" aria-label="طلباتي">ط</button></header><main class="shell">${body}</main>`;
}

function icon(name, { size = 20, flip = false, label = "" } = {}) {
  return `<img class="${flip ? "flip" : ""}" src="/icons/${name}.svg" width="${size}" height="${size}" alt="${esc(label)}">`;
}

function initial(name) {
  const text = String(name || "ف").trim();
  return esc(text.charAt(0) || "ف");
}

function renderHome() {
  const examples = ["أبي تلفزيون سامسونج 65 بوصة", "وأبي كامري 2019 نظيفة", "وأحتاج نجار يسوي لي دولاب غرفة"];
  return `<section class="home">
    <h1>وش تبي نسعّر لك؟</h1>
    <p class="lede">اكتب طلب واحد أو عدة أشياء حتى لو كانت مختلفة</p>
    <form class="composer" id="composer">
      <div class="field">
        <label class="sr" for="composer-query">وش تبي؟</label>
        <textarea id="composer-query" name="query" placeholder="أدخل طلباتك هنا...">${esc(state.query)}</textarea>
        <ul class="examples">${examples.map((idea) => `<li><button type="button" data-action="idea" data-query="${esc(idea)}">- ${esc(idea)}</button></li>`).join("")}</ul>
      </div>
      <button class="primary block" type="submit">سَعِّر طلباتي</button>
    </form>
    <ol class="steps">
      <li>${icon("edit")}<strong>١. اكتب</strong><span>اكتب ما تحتاجه بالعامية</span></li>
      <li>${icon("search")}<strong>٢. نبحث</strong><span>نسعرها من كبار الموردين</span></li>
      <li>${icon("check-square")}<strong>٣. تختار</strong><span>اختر العرض الأنسب لك</span></li>
    </ol>
  </section>`;
}

function renderFacts() {
  const items = facts(state.intent);
  if (!items.length) return "";
  return `<div class="parsed"><span class="tag">فهمنا طلبك</span><div class="facts" aria-label="فهم الطلب">${items.map((item) => `<span>${esc(item)}</span>`).join("")}</div></div>`;
}

function renderQuestion() {
  const question = state.clarification || "";
  const aboutCity = question.includes("مدينة");
  return `<section class="question">
    <span class="tag">${aboutCity ? "حدد المدينة" : "كمّل الطلب"}</span>
    <p>${esc(question)}</p>
    ${aboutCity ? cityChoices("answer") : ""}
    <form id="answer" class="answer"><input name="value" placeholder="جوابك" autocomplete="off"><button class="primary" type="submit">كمّل</button></form>
  </section>`;
}

function renderCard(result) {
  const key = resultKey(result);
  const selected = state.selected.has(key);
  const seller = sellerOf(result);
  const name = result.ad?.title || seller.name || "جهة";
  const price = money(result.ad?.price_amount);
  const where = place(result);
  return `<article class="vendor">
    <button class="vendor-body" type="button" data-action="open" data-key="${esc(key)}">
      ${result.ad && imageSources(result.ad).length ? frame(result.ad, { thumb: true }) : `<span class="mark">${initial(seller.name || name)}</span>`}
      <span class="vendor-copy">
        <strong>${esc(name)}</strong>
        ${where ? `<span class="meta">${esc(where)}</span>` : ""}
        ${seller.name && seller.name !== name ? `<span class="meta">${esc(seller.name)}</span>` : ""}
      </span>
    </button>
    <div class="vendor-side">
      ${price ? `<span class="price">${esc(price)}</span>` : ""}
      <button class="tick" type="button" data-action="toggle" data-key="${esc(key)}" aria-pressed="${selected}" aria-label="${selected ? "إزالة الجهة" : "اختيار الجهة"}">${selected ? icon("check", { size: 14 }) : ""}</button>
    </div>
  </article>`;
}

function renderFlow() {
  const asking = state.searchState === "CLARIFICATION_REQUIRED" || state.searchState === "LOCATION_AMBIGUOUS";
  const live = state.partial && (state.searchState === "LIVE_SEARCHING" || state.searchState === "PARTIAL_RESULTS");
  const products = state.results.length > 0 && state.results.every((item) => item.result_unit === "ad");
  const showEmpty = !asking && !state.partial && state.results.length === 0;
  return `<section class="flow">
    <div class="nav">
      <button class="icon-btn" type="button" data-action="home" aria-label="رجوع">${icon("arrow", { size: 24, flip: true, label: "" })}</button>
      <div class="nav-title"><h1>${esc(state.intent?.need || state.query || "الطلب")}</h1><p>${asking ? "كمّل الطلب قبل النتائج" : "تصفح الجهات المتاحة"}</p></div>
      <span class="icon-btn" aria-hidden="true">${icon("search", { size: 24 })}</span>
    </div>
    ${renderFacts()}
    <p class="status ${live ? "live" : ""}" aria-live="polite">${esc(showEmpty ? "" : state.notice)}</p>
    ${asking ? renderQuestion() : ""}
    ${showEmpty ? `<div class="empty"><h2>${esc(state.notice || "ما فيه شيء نعرضه")}</h2><button class="text-btn" type="button" data-action="retry">جرّب مرة ثانية</button></div>` : ""}
    ${state.results.length ? `<div class="cards ${products ? "products" : ""}">${state.results.map(renderCard).join("")}</div>` : ""}
    ${dock()}
  </section>`;
}

function dock() {
  const count = state.selected.size;
  if (!count || state.view === "review") return "";
  const label = count === 1 ? "أرسل إلى جهة واحدة" : `أرسل إلى ${formatCount(count)} جهات`;
  return `<div class="dock"><button class="primary" type="button" data-action="review">${esc(label)}</button></div>`;
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
  return `<article class="detail">
    <div class="nav">
      <button class="icon-btn" type="button" data-action="back-results" aria-label="رجوع">${icon("arrow", { size: 24, flip: true })}</button>
      <div class="nav-title"><h1>${esc(result.ad?.title || seller.name || "")}</h1><p>${esc(place(result))}</p></div>
      <span class="mark">${initial(seller.name)}</span>
    </div>
    <div class="gallery">${frames}</div>
    ${price ? `<p class="price">${esc(price)}</p>` : `<p class="meta">ما فيه سعر معلن</p>`}
    <p class="meta">${esc(seller.name || "")}</p>
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
  return `<section class="review">
    <button class="text-btn back" type="button" data-action="back-results">رجوع</button>
    <span class="tag">طلب عرض سعر</span>
    <h1>جاهز نرسله؟</h1>
    <p class="summary">${esc(state.query)}</p>
    <ul class="who">${names.map((item) => `<li><span>${esc(item.name)}</span><button class="text-btn" type="button" data-action="unselect" data-key="${esc(item.key)}">شيل</button></li>`).join("")}</ul>
    ${city ? `<p class="meta">المدينة: ${esc(cityLabel(city))}</p>` : `<div class="question"><p>في أي مدينة؟</p>${cityChoices("pick-city")}</div>`}
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
  const head = `<div class="nav"><button class="icon-btn" type="button" data-action="home" aria-label="رجوع">${icon("arrow", { size: 24, flip: true })}</button><div class="nav-title"><h1>المحادثات</h1></div><span class="icon-btn" aria-hidden="true"></span></div>`;
  if (!state.requests.length) return `<section class="requests">${head}<p class="lede">لما ترسل طلب عرض سعر، يبين هنا.</p></section>`;
  return `<section class="requests">${head}${state.requests
    .map((item) => {
      const title = (item.seller_names || []).filter(Boolean).join(" · ") || item.need || item.original_text;
      const need = item.need || item.original_text;
      const when = ago(item.last_message_at || item.created_at);
      return `<button class="convo" type="button" data-action="thread" data-id="${esc(item.id)}"><span class="mark round">${initial(title)}</span><span class="convo-copy"><strong>${esc(title)}</strong>${need ? `<span class="badge">${esc(need)}</span>` : ""}${item.last_message ? `<em>${esc(item.last_message)}</em>` : ""}</span><span class="convo-meta">${when ? `<span>${esc(when)}</span>` : ""}${item.has_new_offer ? `<span class="unread" aria-label="عرض جديد"></span>` : ""}</span></button>`;
    })
    .join("")}</section>`;
}

function renderThread() {
  const thread = state.thread;
  if (!thread) return `<section class="thread"><p>نحمّل المحادثة…</p></section>`;
  const who = (thread.recipients || []).map((item) => item.seller_name).join(" · ");
  return `<section class="chat">
    <header class="chat-head">
      <button class="icon-btn" type="button" data-action="requests" aria-label="رجوع">${icon("arrow", { size: 24, flip: true })}</button>
      <span class="mark round">${initial(who)}</span>
      <div class="who-line"><strong>${esc(who || "المحادثة")}</strong><span class="meta">${esc(thread.need || thread.original_text || "")}</span></div>
    </header>
    <div class="thread"><p class="day">اليوم</p>${bubbles(thread.messages, "user") || `<p class="meta">بانتظار الرد.</p>`}</div>
    <form class="reply-form" id="user-reply">
      <label class="icon-btn" aria-label="إرفاق اختياري">${icon("camera")}<input type="file" accept="image/*,video/*" data-action="add-files"></label>
      <input name="body" placeholder="اكتب رسالة..." autocomplete="off">
      <button class="send-icon" type="submit" aria-label="إرسال">${icon("send", { size: 18 })}</button>
    </form>
  </section>`;
}

function renderSeller() {
  const seller = state.seller;
  if (!seller) return `<section class="seller"><h1>${esc(state.notice || "نحمّل المحادثة…")}</h1></section>`;
  const options = seller.recipients || [];
  const partner = options[0]?.seller_name || "الجهة";
  return `<section class="seller">
    <header class="seller-head">
      <span class="mark round">${initial(partner)}</span>
      <div class="who-line"><strong>${esc(partner)}</strong><span class="meta">${esc([seller.need || seller.original_text, seller.city ? cityLabel(seller.city) : ""].filter(Boolean).join(" · "))}</span></div>
    </header>
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
  app.innerHTML = state.view === "seller" ? `<main class="shell">${view()}</main>` : shell(view());
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
    const body = new FormData(form).get("body");
    if (!body || !state.thread) return;
    api(`/v1/requests/${state.thread.id}/messages`, { method: "POST", json: { body } })
      .then(() => loadThread(state.thread.id))
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
  else if (action === "thread") loadThread(target.dataset.id).catch(() => {});
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
