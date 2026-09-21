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
  return `<header class="top"><a class="brand" href="/" data-action="home">FARQ</a><button class="ghost" type="button" data-action="requests">طلباتي</button></header><main class="shell">${body}</main>`;
}

function composer({ id = "composer-query", compact = false } = {}) {
  return `<form class="composer" id="${compact ? "refine" : "composer"}">
    <label class="sr" for="${id}" style="position:absolute;width:1px;height:1px;overflow:hidden">${compact ? "عدّل الطلب" : "وش تبي؟"}</label>
    <textarea id="${id}" name="query" rows="${compact ? 2 : 3}" placeholder="أبي درابزين ستانلس بالرياض">${esc(state.query)}</textarea>
    ${filePreview()}
    <div class="composer-bar">
      <label class="file-btn">صورة<input type="file" accept="image/*" data-action="add-files"></label>
      <label class="file-btn">ملف<input type="file" data-action="add-files"></label>
      <button class="send" type="submit">${compact ? "حدّث" : "دور لي"}</button>
    </div>
  </form>`;
}

function renderHome() {
  const ideas = ["أبي درابزين ستانلس بالرياض", "كامري 2024 مستعملة", "PS5 مستعمل", "أبي نجار بالرياض", "كفرات باترول"];
  return `<section class="home">
    <h1>وش تبي؟</h1>
    <p class="lede">اكتبه بطريقتك.</p>
    ${composer()}
    <ul class="ideas">${ideas.map((idea) => `<li><button type="button" data-action="idea" data-query="${esc(idea)}">${esc(idea)}</button></li>`).join("")}</ul>
  </section>`;
}

function renderFacts() {
  const items = facts(state.intent);
  if (!items.length) return "";
  return `<div class="facts" aria-label="فهم الطلب">${items.map((item) => `<span>${esc(item)}</span>`).join("")}</div>`;
}

function renderQuestion() {
  const question = state.clarification || "";
  const cities = question.includes("مدينة") ? ["الرياض", "جدة", "الدمام", "مكة"] : [];
  return `<section class="question">
    <p>${esc(question)}</p>
    ${cities.length ? `<div class="choices">${cities.map((city) => `<button type="button" data-action="answer" data-value="${esc(city)}">${esc(city)}</button>`).join("")}</div>` : ""}
    <form id="answer" class="answer"><input name="value" placeholder="جوابك" autocomplete="off"><button class="primary" type="submit">كمّل</button></form>
  </section>`;
}

function renderCard(result, index) {
  const key = resultKey(result);
  const selected = state.selected.has(key);
  const seller = sellerOf(result);
  const product = result.result_unit === "ad";
  const price = money(result.ad?.price_amount);
  const where = place(result);
  if (!product) {
    return `<article class="card service">
      ${result.ad ? frame(result.ad, { thumb: true, eager: index < 2 }) : ""}
      <h2>${esc(seller.name || result.ad?.title || "جهة")}</h2>
      ${evidenceLines(result).map((line) => `<p class="evidence">${esc(line)}</p>`).join("")}
      ${where ? `<p class="meta">${esc(where)}</p>` : ""}
      <div class="card-actions">
        <button class="text-btn" type="button" data-action="open" data-key="${esc(key)}">التفاصيل</button>
        <button class="text-btn pick" type="button" data-action="toggle" data-key="${esc(key)}" aria-pressed="${selected}">${selected ? "مختارة" : "اختَر"}</button>
      </div>
    </article>`;
  }
  return `<article class="card">
    <button class="card-hit" type="button" data-action="open" data-key="${esc(key)}">
      ${frame(result.ad, { eager: index < 2 })}
      ${price ? `<p class="price">${esc(price)}</p>` : ""}
      <h2>${esc(result.ad?.title || "")}</h2>
      ${where ? `<p class="meta">${esc(where)}</p>` : ""}
    </button>
    <div class="card-actions">
      <button class="text-btn pick" type="button" data-action="toggle" data-key="${esc(key)}" aria-pressed="${selected}">${selected ? "مختارة" : "اختَر"}</button>
    </div>
  </article>`;
}

function renderFlow() {
  const asking = state.searchState === "CLARIFICATION_REQUIRED" || state.searchState === "LOCATION_AMBIGUOUS";
  const live = state.partial && (state.searchState === "LIVE_SEARCHING" || state.searchState === "PARTIAL_RESULTS");
  const products = state.results.length > 0 && state.results.every((item) => item.result_unit === "ad");
  const showEmpty = !asking && !state.partial && state.results.length === 0;
  return `<section>
    ${composer({ compact: true })}
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
  const label = count === 1 ? "اطلب سعر" : `اطلب سعر من ${formatCount(count)}`;
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
    <button class="text-btn back" type="button" data-action="back-results">رجوع</button>
    <div class="gallery">${frames}</div>
    ${price ? `<p class="price">${esc(price)}</p>` : `<p class="meta">ما فيه سعر معلن في الإعلان</p>`}
    <h2>${esc(result.ad?.title || seller.name || "")}</h2>
    <p class="meta">${esc([seller.name, place(result)].filter(Boolean).join(" · "))}</p>
    ${result.ad?.description ? `<p class="story">${esc(result.ad.description)}</p>` : ""}
    ${result.ad?.url ? `<p><a href="${esc(result.ad.url)}" target="_blank" rel="noopener">الإعلان في حراج</a></p>` : ""}
    ${result.ad?.listing_state === "deleted" ? `<p class="warn">هذا الإعلان محذوف.</p>` : ""}
    <div class="detail-actions"><button class="primary" type="button" data-action="toggle" data-key="${esc(key)}">${selected ? "تم الاختيار" : "اختَر الجهة"}</button></div>
    ${dock()}
  </article>`;
}

function renderReview() {
  const names = [...state.selected.values()].map((result) => sellerOf(result).name || "بائع");
  const ready = state.files.length > 0 && state.selected.size > 0;
  return `<section class="review">
    <button class="text-btn back" type="button" data-action="back-results">رجوع</button>
    <h1>جاهز نرسله؟</h1>
    <p class="summary">${esc(state.query)}</p>
    <ul class="who">${names.map((name) => `<li>${esc(name)}</li>`).join("")}</ul>
    <label>ملاحظة<textarea class="note" id="note">${esc(state.note)}</textarea></label>
    <div class="composer-bar" style="margin-top:14px">
      <label class="file-btn">صورة<input type="file" accept="image/*" data-action="add-files"></label>
      <label class="file-btn">ملف<input type="file" data-action="add-files"></label>
    </div>
    ${filePreview()}
    ${state.files.length ? "" : `<p class="warn">أرفق صورة أو ملف قبل الإرسال.</p>`}
    <button class="primary" type="button" data-action="send" ${ready ? "" : "disabled"} style="margin-top:18px">${state.busy ? "نرسل…" : "أرسل الطلب"}</button>
  </section>`;
}

function activity(item) {
  const lines = [];
  if (item.replied_count > 0) lines.push(`${formatCount(item.replied_count)} من ${formatCount(item.recipient_count)} ردوا`);
  else lines.push(item.recipient_count ? "بانتظار الرد" : "");
  if (item.has_new_offer) lines.push("وصل سعر جديد");
  return lines.filter(Boolean);
}

function renderRequests() {
  if (!state.requests.length) return `<section class="requests"><h1>طلباتك</h1><p class="lede">لما ترسل طلب سعر، يبين هنا.</p></section>`;
  return `<section class="requests"><h1>طلباتك</h1>${state.requests
    .map((item) => {
      const lines = activity(item);
      return `<button class="request" type="button" data-action="thread" data-id="${esc(item.id)}"><strong>${esc(item.need || item.original_text)}</strong>${lines.map((line) => `<em>${esc(line)}</em>`).join("")}</button>`;
    })
    .join("")}</section>`;
}

function renderThread() {
  const thread = state.thread;
  if (!thread) return `<section class="thread"><p>نحمّل المحادثة…</p></section>`;
  const messages = (thread.messages || [])
    .map((message) => {
      const role = message.sender_role === "seller" ? "seller" : "user";
      const price = message.offer?.amount != null ? `<div class="offer"><strong>${esc(money(message.offer.amount))}</strong></div>` : "";
      return `<div class="bubble ${role}">${price}<div>${esc(message.body || "")}</div></div>`;
    })
    .join("");
  return `<section>
    <button class="text-btn back" type="button" data-action="requests">طلباتي</button>
    <h1>${esc(thread.need || thread.original_text)}</h1>
    <p class="meta">${esc(thread.recipients.map((item) => item.seller_name).join(" · "))}</p>
    <div class="thread">${messages || `<p class="meta">بانتظار الرد.</p>`}</div>
    ${thread.reply_token ? `<p class="reply-link">عشان يوصلك السعر، أرسل للبائع <a href="/s/${esc(thread.reply_token)}">رابط الرد</a>.</p>` : ""}
    <form class="reply-form" id="user-reply"><input name="body" placeholder="اكتب رسالتك" autocomplete="off"><button class="primary" type="submit">إرسال</button></form>
  </section>`;
}

function renderSeller() {
  const seller = state.seller;
  if (!seller) return `<section class="seller"><h1>${esc(state.notice || "نحمّل الطلب…")}</h1></section>`;
  const options = seller.recipients || [];
  return `<section class="seller">
    <p class="brand">FARQ</p>
    <h1>رد على الطلب</h1>
    <p class="summary">${esc(seller.need || seller.original_text)}</p>
    ${seller.notes ? `<p>${esc(seller.notes)}</p>` : ""}
    ${seller.city ? `<p class="meta">${esc(seller.city)}</p>` : ""}
    ${(seller.attachments || []).map((item) => `<p><a href="/v1/seller/${esc(state.sellerToken)}/attachments/${esc(item.id)}">${esc(item.filename)}</a></p>`).join("")}
    <form id="seller-reply">
      ${options.length > 1 ? `<select name="seller_id">${options.map((item) => `<option value="${esc(item.seller_id)}">${esc(item.seller_name)}</option>`).join("")}</select>` : `<input type="hidden" name="seller_id" value="${esc(options[0]?.seller_id || "")}">`}
      <input name="amount" inputmode="decimal" placeholder="السعر" autocomplete="off">
      <textarea name="body" rows="3" placeholder="يشمل التوصيل والتركيب"></textarea>
      <button class="primary" type="submit">أرسل السعر</button>
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

function toggle(key) {
  if (state.selected.has(key)) state.selected.delete(key);
  else {
    const result = state.results.find((item) => resultKey(item) === key) || (state.active && resultKey(state.active) === key ? state.active : null);
    if (result && sellerOf(result).id) state.selected.set(key, result);
  }
  render();
}

async function sendRequest() {
  if (!state.files.length || !state.selected.size || state.busy) return;
  state.busy = true;
  render();
  try {
    await ensureAuth();
    const city = typeof state.intent?.location_city?.value === "string" ? state.intent.location_city.value : null;
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
    state.thread = created;
    state.view = "thread";
    await loadThread(created.id);
  } catch (_error) {
    state.notice = "ما قدرنا نرسل الطلب. جرّب مرة ثانية.";
    state.busy = false;
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

async function loadSeller(token) {
  state.view = "seller";
  state.sellerToken = token;
  state.notice = "";
  try {
    state.seller = await api(`/v1/seller/${token}`, { skipAuth: true });
  } catch (_error) {
    state.seller = null;
    state.notice = "ما لقينا الطلب.";
  }
  render();
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
      .then(() => {
        state.notice = "وصل الرد لصاحب الطلب.";
        render();
      })
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

document.addEventListener("change", (event) => {
  if (event.target.dataset.action === "add-files") {
    rememberFiles(event.target.files || []);
    render();
  }
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
  else if (action === "review") {
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

if (sellerRoute) loadSeller(decodeURIComponent(sellerRoute[1]));
else render();
