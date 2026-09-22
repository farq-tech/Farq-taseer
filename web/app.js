const state = {
  view: "home",
  query: "",
  files: [],
  intent: null,
  searchState: "",
  clarification: "",
  results: [],
  groups: [],
  intents: [],
  partial: false,
  notice: "",
  selected: new Map(),
  active: null,
  activeNeed: "",
  activeSeller: "",
  gallery: [],
  note: "",
  requests: [],
  thread: null,
  seller: null,
  sellerToken: "",
  token: localStorage.getItem("farq.token") || "",
  busy: false,
  sort: "cheap",
  filterCity: "",
  pricedOnly: false,
  credits: 50,
  copied: false,
  replyTo: null,
};

const app = document.querySelector("#app");
let poll = 0;
const sellerRoute = location.pathname.match(/^\/s\/([^/]+)\/?$/);

const icons = {
  edit: '<svg class="icon icon-16" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
  search: '<svg class="icon icon-24" viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  check: '<svg class="icon icon-14" viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 5 5L20 7"/></svg>',
  checkSq: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="m8 12 3 3 5-6"/></svg>',
  pencil: '<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"/></svg>',
  spark: '<svg class="icon icon-16" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18"/></svg>',
  back: '<svg class="icon icon-24" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14"/><path d="m13 6 6 6-6 6"/></svg>',
  bell: '<svg class="icon icon-24" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 8a6 6 0 1 1 12 0c0 7 3 7 3 7H3s3 0 3-7"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>',
};

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

function initial(name) {
  const text = String(name || "ج").trim();
  return esc(text[0] || "ج");
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

function resultKey(result, need = result.need || "") {
  const seller = sellerOf(result);
  return `${need}:${seller.id || ""}:${result.ad?.id || ""}`;
}

function needGroups() {
  if (state.groups?.length) return state.groups;
  const label = state.intent?.need || state.query || "الطلب";
  return [{ need: label, results: state.results, intent: state.intent }];
}

function ago(iso) {
  if (!iso) return "";
  const mins = Math.max(1, Math.round((Date.now() - Date.parse(iso)) / 60000));
  return `آخر تحديث قبل ${formatCount(mins)} دقيقة`;
}

function place(result) {
  const seller = sellerOf(result);
  return [result.ad?.city || seller.city, result.ad?.district || seller.district].filter(Boolean).join(" · ");
}

function categoryLabel(intent) {
  const type = intent?.type?.value;
  const sub = intent?.subcategory?.value;
  if (sub === "carpenter") return "نجارة وأثاث";
  if (sub === "plumber") return "سباكة";
  if (sub === "electrician") return "كهرباء";
  if (sub === "railings") return "مقاولات";
  if (type === "vehicle") return "سيارات";
  if (type === "product") return "منتجات";
  if (type === "property") return "عقارات";
  if (type === "service") return "خدمات";
  return "طلب";
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

function parsedNeed() {
  const items = facts(state.intent);
  return items.slice(0, 3).join(" ") || state.query;
}

function finalNotice(status, count) {
  if (status === "PARTIAL_RESULTS") return count ? "لقينا خيارات مناسبة، وهذي اللي وصلت." : "البحث ما اكتمل.";
  if (status === "LIVE_UNAVAILABLE") return "المصدر ما استجاب الحين، فما نقدر نأكد إذا فيه نتائج أو لا.";
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

function frame(ad, { eager = false, size = "thumb" } = {}) {
  const sources = imageSources(ad);
  const cls = size === "cover" ? "cover" : "thumb";
  if (!sources.length) return `<div class="${cls} is-missing" data-frame></div>`;
  const loading = eager ? "eager" : "lazy";
  return `<div class="${cls}" data-frame><img alt="" data-src="${esc(sources.join("|"))}" loading="${loading}" decoding="async"></div>`;
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

function brandLockup() {
  return `<a class="brand-lockup brand-center" href="/" data-action="home"><span class="wordmark">فرق تسعير</span></a>`;
}

function avatarBtn() {
  return `<button class="avatar" type="button" data-action="requests" aria-label="طلباتي"><span class="avatar-mark">ف</span></button>`;
}

function homeHeader() {
  return `<header class="top"><button class="avatar" type="button" data-action="requests" aria-label="طلباتي"><span class="avatar-mark">ف</span></button><div class="brand-center">${brandLockup()}</div><span class="icon-btn" aria-hidden="true"></span></header>`;
}

function nav({ title, subtitle = "", back = "back-results", extra = "none" } = {}) {
  const trailing =
    extra === "bell"
      ? `<button class="icon-btn" type="button" data-action="requests" aria-label="العروض">${icons.bell}</button>`
      : extra === "search"
        ? `<button class="icon-btn" type="button" data-action="home" aria-label="بحث جديد">${icons.search}</button>`
        : `<span class="icon-btn" aria-hidden="true"></span>`;
  return `<header class="nav">${trailing}<div class="nav-title"><strong>${esc(title)}</strong>${subtitle ? `<span>${esc(subtitle)}</span>` : ""}</div><button class="icon-btn" type="button" data-action="${esc(back)}" aria-label="رجوع">${icons.back}</button></header>`;
}

function displayNeed(label) {
  const raw = String(label || "").trim();
  if (/سباك/.test(raw)) return "سباك";
  if (/كهرب/.test(raw)) return "كهربائي";
  if (/نجار/.test(raw)) return "نجار";
  if (/درابزين|حديد/.test(raw)) return "درابزين";
  return raw.replace(/\s*(بالرياض|الرياض|بجدة|جدة|بالدمام|الدمام)\s*/g, " ").replace(/\s+/g, " ").trim() || raw;
}

function creditPill() {
  return `<div class="credit-pill"><span class="dot"></span>رصيدك: ${formatCount(state.credits)} كريدت</div>`;
}

function composer({ id = "composer-query", compact = false } = {}) {
  return `<form class="composer" id="${compact ? "refine" : "composer"}">
    <label class="sr" for="${id}">${compact ? "عدّل الطلب" : "أدخل طلباتك هنا"}</label>
    <textarea id="${id}" name="query" rows="${compact ? 2 : 4}" maxlength="500" placeholder="مثال: أبي سباك وأبي كهربائي بالرياض">${esc(state.query)}</textarea>
    ${filePreview()}
  </form>`;
}

function renderHome() {
  const ideas = [
    { label: "سباك بالرياض", query: "أبي سباك بالرياض" },
    { label: "كهربائي بجدة", query: "أبي كهربائي بجدة" },
    { label: "نقل عفش", query: "أبي نقل عفش بالرياض" },
  ];
  return `${homeHeader()}
    <section class="hero center">
      <h1>وش تبي نسعّر لك؟</h1>
      <p>اكتب اللي تحتاجه وخلنا ندور لك</p>
    </section>
    ${composer()}
    <p class="hint">اقتراحات سريعة</p>
    <div class="chips">${ideas
      .map((idea) => `<button class="chip" type="button" data-action="idea" data-query="${esc(idea.query)}">${esc(idea.label)}</button>`)
      .join("")}</div>
    <div class="pad"><button class="primary" type="button" data-action="search-now">ابحث</button></div>`;
}

function understoodNeeds() {
  if (state.intents?.length) return state.intents.map((item) => item.need || item.original_query).filter(Boolean);
  const fromGroups = (state.groups || []).map((item) => item.need).filter(Boolean);
  if (fromGroups.length) return fromGroups;
  return facts(state.intent).slice(0, 3);
}

function renderSearching() {
  return `<header class="top"><span></span>${brandLockup()}<span class="icon-btn"></span></header>
    <section class="searching">
      <div class="search-orb">${icons.search}</div>
      <h1>ندور لك...</h1>
      <p>نبحث في أكثر من 20,000 جهة</p>
      <div class="search-dots"><i class="is-on"></i><i></i><i></i></div>
      <div class="raw-card"><em>طلبك الأصلي</em><p>${esc(state.query)}</p></div>
    </section>`;
}

function renderParse() {
  if (state.partial && !state.results.length) return renderSearching();
  const items = understoodNeeds();
  const asking = state.searchState === "CLARIFICATION_REQUIRED" || state.searchState === "LOCATION_AMBIGUOUS";
  const city = typeof state.intent?.location_city?.value === "string" ? state.intent.location_city.value : "";
  return `${nav({ title: "فرق تسعير", back: "home", extra: "none" })}
    <section class="page-head">
      <h1>فهمنا طلبك</h1>
      <p class="lede">قسمنا الطلب وحددنا الخدمات المطلوبة</p>
    </section>
    ${asking ? renderQuestion() : ""}
    <div class="need-list">${items
      .map((item) => {
        const name = displayNeed(item);
        return `<article class="need-row">
          <div class="need-ico">${name === "كهربائي" ? "⚡" : "🔧"}</div>
          <div><strong>${esc(name)}</strong><span>${esc(categoryLabel(state.intent))}</span></div>
          ${city ? `<span class="city-chip">${esc(city)}</span>` : ""}
        </article>`;
      })
      .join("")}</div>
    <div class="dock">
      <button class="linkish" type="button" data-action="home">عدّل الطلبات</button>
      <button class="primary" type="button" data-action="show-results" ${state.results.length || !state.partial ? "" : "disabled"}>اعرض النتائج</button>
    </div>`;
}

function renderQuestion() {
  const question = state.clarification || "";
  const cities = question.includes("مدينة")
    ? ["الرياض", "جدة", "الدمام", "الخبر", "مكة", "المدينة", "الطائف", "أبها", "القصيم", "تبوك"]
    : [];
  return `<section class="question">
    <p>${esc(question)}</p>
    ${cities.length ? `<div class="choices">${cities.map((city) => `<button type="button" data-action="answer" data-value="${esc(city)}">${esc(city)}</button>`).join("")}</div>` : ""}
    <form id="answer" class="answer"><input name="value" placeholder="جوابك" autocomplete="off"><button class="primary" type="submit">كمّل</button></form>
  </section>`;
}

function resultCity(result) {
  return result.ad?.city || sellerOf(result).city || "";
}

function visibleResults(rows = state.results) {
  let list = [...rows];
  if (state.filterCity) list = list.filter((item) => resultCity(item) === state.filterCity);
  if (state.pricedOnly) list = list.filter((item) => item.ad?.price_amount != null);
  if (state.sort === "cheap") {
    list.sort((a, b) => (a.ad?.price_amount ?? 1e12) - (b.ad?.price_amount ?? 1e12));
  } else if (state.sort === "new") {
    list.sort((a, b) => String(b.ad?.posted_at || "").localeCompare(String(a.ad?.posted_at || "")));
  }
  return list;
}

function cityFilters() {
  return [...new Set(state.results.map(resultCity).filter(Boolean))].slice(0, 8);
}

function renderCard(result, index, need) {
  const tagged = { ...result, need };
  const key = resultKey(tagged, need);
  const selected = state.selected.has(key);
  const seller = sellerOf(result);
  const price = money(result.ad?.price_amount);
  const city = resultCity(result);
  const preview = (result.ad?.description || "").trim().slice(0, 70);
  return `<article class="listing">
    <button class="listing-photo" type="button" data-action="open" data-key="${esc(key)}" aria-label="التفاصيل">
      ${result.ad ? frame(result.ad, { eager: index < 4, size: "thumb" }) : `<div class="thumb mark-cover">${initial(seller.name)}</div>`}
    </button>
    <div class="listing-body">
      <p class="vendor-name">${esc(result.ad?.title || seller.name || "جهة")}</p>
      <p class="meta">${esc([seller.name, city].filter(Boolean).join(" · "))}</p>
      ${preview ? `<p class="meta">${esc(preview)}${(result.ad?.description || "").length > 70 ? "…" : ""}</p>` : ""}
      ${price ? `<p class="price">${esc(price)}</p>` : ""}
    </div>
    <button class="box ${selected ? "is-on" : ""}" type="button" data-action="toggle" data-key="${esc(key)}" aria-pressed="${selected}" aria-label="${selected ? "مختارة" : "اختَر"}">${selected ? icons.check : ""}</button>
  </article>`;
}

function renderFlow() {
  const asking = state.searchState === "CLARIFICATION_REQUIRED" || state.searchState === "LOCATION_AMBIGUOUS";
  if ((state.partial || asking) && !state.results.length) return renderParse();
  const groups = needGroups();
  const showEmpty = !asking && !state.partial && !groups.some((item) => item.results?.length);
  return `${nav({ title: "النتائج", back: "home", extra: "none" })}
    ${asking ? renderQuestion() : ""}
    ${showEmpty ? `<div class="empty"><h2>${esc(state.notice || "ما فيه شيء نعرضه")}</h2><button class="linkish" type="button" data-action="retry">جرّب مرة ثانية</button></div>` : ""}
    ${groups
      .map((group) => {
        const rows = visibleResults(group.results || []);
        return `<section class="need-block">
          <div class="need-head">
            <h2>${esc(displayNeed(group.need))}</h2>
            <span>${formatCount(rows.length)} جهة مطابقة</span>
          </div>
          ${rows.length ? `<div class="feed">${rows.map((item, index) => renderCard(item, index, group.need)).join("")}</div>` : `<p class="status">ما ظهرت جهات لهذا الطلب بعد.</p>`}
        </section>`;
      })
      .join("")}
    ${dock()}`;
}

function dock() {
  const count = state.selected.size;
  if (state.view === "review") return "";
  return `<div class="dock"><button class="primary" type="button" data-action="review" ${count ? "" : "disabled"}>اطلب السعر من ${formatCount(count)} جهة</button></div>`;
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
  return `${nav({ title: seller.name || result.ad?.title || "التفاصيل" })}
    <article class="detail">
      <div class="gallery">${frames}</div>
      <div class="detail-body">
        ${price ? `<p class="price">${esc(price)}</p>` : `<p class="meta">ما فيه سعر معلن في الإعلان</p>`}
        <h2>${esc(result.ad?.title || seller.name || "")}</h2>
        <p class="meta">${esc([seller.name, place(result)].filter(Boolean).join(" · "))}</p>
        ${result.ad?.description ? `<p class="story">${esc(result.ad.description)}</p>` : ""}
        ${result.ad?.url ? `<p><a href="${esc(result.ad.url)}" target="_blank" rel="noopener">الإعلان الأصلي</a></p>` : ""}
        ${result.ad?.listing_state === "deleted" ? `<p class="warn">هذا الإعلان محذوف.</p>` : ""}
      </div>
    </article>
    <div class="dock"><button class="primary" type="button" data-action="toggle" data-key="${esc(key)}">${selected ? "تم الاختيار" : "اختَر هذي الجهة"}</button></div>`;
}

function renderReview() {
  const picked = [...state.selected.values()];
  const ready = picked.length > 0;
  return `${nav({ title: "تأكيد الطلب", back: "back-results" })}
    <section class="review">
      <h1>اطلب السعر من ${formatCount(picked.length)} جهة</h1>
      <p class="lede">نرسل طلبك للجهات المختارة، والأسعار ترجع هنا.</p>
      <ul class="who">${picked
        .map((result) => `<li><span>${esc(sellerOf(result).name || result.ad?.title || "جهة")}</span><span class="badge">${esc(result.need || "")}</span></li>`)
        .join("")}</ul>
      <label class="sr" for="note">ملاحظة</label>
      <textarea class="note" id="note" placeholder="تفاصيل إضافية (اختياري)">${esc(state.note)}</textarea>
      <div class="composer-bar">
        <label class="file-btn">صورة<input type="file" accept="image/*" data-action="add-files"></label>
        <label class="file-btn">ملف<input type="file" data-action="add-files"></label>
      </div>
      ${filePreview()}
    </section>
    <div class="dock"><button class="primary" type="button" data-action="send" ${ready ? "" : "disabled"}>${state.busy ? "نرسل…" : `اطلب السعر من ${formatCount(picked.length)} جهة`}</button></div>`;
}

function renderRequests() {
  const cards = [];
  for (const item of state.requests) {
    const groups = item.needs?.length ? item.needs : [{ need: item.need || item.original_text, recipient_count: item.recipient_count, offer_count: item.replied_count, lowest_total: item.latest_offer_amount }];
    for (const group of groups) {
      const city = item.city ? ` بال${item.city.replace(/^ال/, "")}` : "";
      const status = group.offer_count ? `وصلت ${formatCount(group.offer_count)} أسعار` : "بانتظار الأسعار";
      cards.push(`<button class="request-card" type="button" data-action="offers" data-id="${esc(item.id)}" data-need="${esc(group.need || "")}">
        <strong>${esc(displayNeed(group.need || item.original_text))}${esc(city)}</strong>
        <p class="hits">${esc(status)}</p>
        <em>أرسل إلى ${formatCount(group.recipient_count || item.recipient_count || 0)} جهة</em>
        ${group.lowest_total != null ? `<p class="request-low">من ${esc(money(group.lowest_total))}</p>` : ""}
        ${item.sent_count != null ? `<p class="send-status">${[
          item.sent_count ? `${formatCount(item.sent_count)} وصل في حراج` : "",
          item.queued_count ? `${formatCount(item.queued_count)} بانتظار الإرسال` : "",
          item.failed_count ? `${formatCount(item.failed_count)} تعذر الإرسال` : "",
        ].filter(Boolean).join(" · ")}</p>` : ""}
      </button>`);
    }
  }
  return `<header class="top">${brandLockup()}<span></span></header>
    <section style="display:flex;flex-direction:column;padding-top:8px">
      ${cards.join("") || `<p class="lede" style="padding:0 24px">لما تطلب سعر، يبين هنا.</p>`}
    </section>`;
}

function offersForNeed(thread, need) {
  const offers = (thread.offers || []).filter((item) => !need || !item.need || item.need === need);
  return [...offers].sort((a, b) => (a.total_price ?? 1e12) - (b.total_price ?? 1e12));
}

function renderOffers() {
  const thread = state.thread;
  if (!thread) return `${nav({ title: "الأسعار", back: "requests" })}<section class="thread-page"><p>نحمّل الأسعار…</p></section>`;
  const need = state.activeNeed || thread.need || "";
  const offers = offersForNeed(thread, need);
  const recipients = (thread.recipients || []).filter((item) => !need || !item.need || item.need === need);
  const title = `${displayNeed(need)}${thread.city ? ` بال${thread.city.replace(/^ال/, "")}` : ""}`;
  return `${nav({ title, back: "requests" })}
    <div class="need-head">
      <h2>${formatCount(offers.length)} عروض أسعار</h2>
      <span>ترتيب حسب الأرخص</span>
    </div>
    ${offers.length || (thread.messages || []).some((item) => item.sender_role === "seller") ? `<button class="ghost-btn all-replies-btn" type="button" data-action="chat" data-seller="">محادثة البند</button>` : ""}
    <section style="display:flex;flex-direction:column">
      ${offers
        .map((offer) => {
          const seller = recipientForOffer(recipients, offer);
          const sellerId = seller?.seller_id || "";
          const breakdown = offer.delivery_included
            ? "السعر يشمل الوصول"
            : `الخدمة ${money(offer.base_price)} + الوصول ${money(offer.delivery_price)}`;
          return `<article class="quote-card ${offer.cheapest ? "is-cheapest" : ""}">
            <div class="need-head" style="padding:0">
              <strong>${esc(offer.provider_name || seller?.seller_name || "جهة")}</strong>
              ${offer.cheapest ? `<span class="cheap-badge">الأرخص</span>` : ""}
            </div>
            <p class="total">الإجمالي: ${esc(money(offer.total_price))}</p>
            <p class="meta">${esc(breakdown)}</p>
            <button class="ghost-btn" type="button" data-action="chat" data-seller="${esc(sellerId)}">محادثة</button>
          </article>`;
        })
        .join("") || `<p class="lede" style="padding:0 24px">بانتظار الأسعار.</p>`}
    </section>`;
}

function recipientForOffer(recipients, offer) {
  return (
    recipients.find((item) => offer.seller_id && item.seller_id === offer.seller_id) ||
    recipients.find((item) => item.seller_name === offer.provider_name || item.seller_id === offer.phone) ||
    recipients.find((item) => item.need === offer.need) ||
    recipients[0]
  );
}

function offerForSeller(thread, recipient) {
  const offers = thread.offers || [];
  return (
    offers.find((item) => item.seller_id && item.seller_id === recipient.seller_id && (!item.need || !recipient.need || item.need === recipient.need)) ||
    offers.find((item) => !item.seller_id && item.provider_name === recipient.seller_name)
  );
}

function itemRecipients(thread) {
  const need = state.activeNeed || thread.need || "";
  return (thread.recipients || []).filter((item) => !need || !item.need || item.need === need);
}

function offerBreakdown(offer) {
  if (!offer || offer.base_price == null) return "";
  return offer.delivery_included ? "السعر يشمل الوصول" : `الخدمة ${money(offer.base_price)} + الوصول ${money(offer.delivery_price)}`;
}

function snippet(text, size = 60) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  return value.length > size ? `${value.slice(0, size)}…` : value;
}

function mentionedSeller(body, recipients) {
  const matches = recipients.filter((item) => item.seller_name && body.includes(`@${item.seller_name}`));
  return matches.sort((a, b) => b.seller_name.length - a.seller_name.length)[0] || null;
}

function replyForm(placeholder) {
  const target = state.replyTo;
  return `<form class="reply-form" id="user-reply">
    ${target ? `<div class="reply-target"><span>ترد على <strong>${esc(target.name)}</strong>: ${esc(snippet(target.body, 40))}</span><button type="button" data-action="cancel-reply" aria-label="إلغاء">✕</button></div>` : ""}
    <div class="mention-list" id="mention-list" hidden></div>
    <div class="reply-row"><input name="body" placeholder="${esc(placeholder)}" autocomplete="off"><button class="primary" type="submit">إرسال</button></div>
  </form>`;
}

function harajSync(thread) {
  return thread.last_synced_at ? `${ago(thread.last_synced_at)} من حراج` : "ما تمت مزامنة حراج بعد";
}

function deliveryLabel(message, nameOf) {
  const deliveries = message.deliveries || [];
  const to = message.scope === "single_seller" && message.seller_id ? `إلى ${nameOf(message.seller_id)} فقط` : `للكل (${formatCount(deliveries.length)})`;
  const sent = deliveries.filter((item) => item.status === "sent").length;
  const failed = deliveries.filter((item) => item.status === "failed").length;
  let status = "بانتظار الإرسال لحراج";
  if (message.delivery_state === "sent") status = "وصلت في حراج";
  else if (message.delivery_state === "partial") status = `وصلت لـ ${formatCount(sent)} · تعذرت ${formatCount(failed)}`;
  else if (message.delivery_state === "failed") status = "تعذر الإرسال";
  return `${to} · ${status}`;
}

function sellerBubble(thread, message, nameOf, firstOffer) {
  const recipient = (thread.recipients || []).find((item) => item.seller_id === message.seller_id) || {};
  const offer = offerForSeller(thread, recipient);
  const name = recipient.seller_name || offer?.provider_name || "جهة";
  const amount = message.offer?.total_price ?? message.offer?.amount;
  const isOffer = amount != null;
  const cheapest = firstOffer && offer?.cheapest && offer.total_price === amount;
  return `<div class="bubble seller ${cheapest ? "is-cheapest" : ""}">
    <div class="bubble-head">
      <span><button class="bubble-name" type="button" data-action="chat" data-seller="${esc(recipient.seller_id || "")}">${esc(name)}</button>${isOffer ? ` <span class="bubble-verb">قدّم سعر</span>` : ""}</span>
      ${cheapest ? `<span class="cheap-badge">الأرخص</span>` : ""}
    </div>
    ${isOffer ? `<strong class="offer-amt">${esc(money(amount))}</strong>` : ""}
    ${firstOffer && offerBreakdown(offer) ? `<p class="bubble-meta">${esc(offerBreakdown(offer))}</p>` : ""}
    ${message.body ? `<div>${esc(message.body)}</div>` : ""}
    <button class="bubble-reply" type="button" data-action="reply" data-message="${esc(message.id)}">ردّ عليه</button>
  </div>`;
}

function conversationBubbles(thread, messages) {
  const byMessage = new Map((thread.messages || []).map((item) => [item.id, item]));
  const nameOf = (sellerId) => (thread.recipients || []).find((item) => item.seller_id === sellerId)?.seller_name || "جهة";
  const priced = new Set();
  return messages
    .map((message) => {
      if (message.sender_role === "seller") {
        const hasPrice = (message.offer?.total_price ?? message.offer?.amount) != null;
        const firstOffer = hasPrice && !priced.has(message.seller_id);
        if (hasPrice) priced.add(message.seller_id);
        return sellerBubble(thread, message, nameOf, firstOffer);
      }
      const quoted = message.reply_to ? byMessage.get(message.reply_to) : null;
      const quote = quoted ? `<span class="bubble-quote">${esc(quoted.sender_role === "seller" ? nameOf(quoted.seller_id) : "أنت")}: ${esc(snippet(quoted.body, 50))}</span>` : "";
      return `<div class="bubble user">${quote}<div>${esc(message.body || "")}</div><span class="bubble-to">${esc(deliveryLabel(message, nameOf))}</span></div>`;
    })
    .join("");
}

function itemMessages(thread) {
  const need = state.activeNeed || thread.need || "";
  return (thread.messages || []).filter((message) => !need || !message.need || message.need === need);
}

// One conversation per item, for the customer only. Sellers never see it: each of them only has
// their own Haraj conversation, and every message here is routed to or synced from those.
function renderAllReplies(thread) {
  const need = state.activeNeed || thread.need || "";
  const recipients = itemRecipients(thread);
  const messages = itemMessages(thread);
  const replied = new Set(messages.filter((item) => item.sender_role === "seller").map((item) => item.seller_id));
  return `${nav({ title: displayNeed(need) || "المحادثة", subtitle: `مع ${formatCount(recipients.length)} جهة عبر حراج`, back: "offers-back" })}
    <section class="thread-page">
      <p class="sync">${replied.size ? `ردّ ${formatCount(replied.size)} من ${formatCount(recipients.length)} جهة · ` : ""}${esc(harajSync(thread))}</p>
      <p class="thread-hint">رسالتك توصل لكل الجهات في حراج. «ردّ عليه» أو @الاسم توصل له بس. اضغط اسم الجهة تشوف رسايلكم معه.</p>
      <div class="thread">${conversationBubbles(thread, messages) || `<p class="meta">ما وصلت ردود للحين.</p>`}</div>
      ${replyForm("اكتب للكل، أو @ لجهة معيّنة")}
    </section>`;
}

// One seller's view: a filter on the item conversation, not a separate chat.
// It holds everything that went into or came from this seller's Haraj conversation.
function renderThread() {
  const thread = state.thread;
  if (!thread) return `${nav({ title: "المحادثة", back: "offers-back" })}<section class="thread-page"><p>نحمّل المحادثة…</p></section>`;
  if (!state.activeSeller) return renderAllReplies(thread);
  const seller = itemRecipients(thread).find((item) => item.seller_id === state.activeSeller) || (thread.recipients || []).find((item) => item.seller_id === state.activeSeller) || {};
  const offer = offerForSeller(thread, seller);
  const messages = itemMessages(thread).filter(
    (message) => message.seller_id === state.activeSeller || (message.scope === "all_sellers" && (message.deliveries || []).some((item) => item.seller_id === state.activeSeller)),
  );
  return `${nav({ title: seller.seller_name || "محادثة", subtitle: "محادثته في حراج", back: "all-replies" })}
    <section class="thread-page">
      ${offer ? `<article class="quote-card ${offer.cheapest ? "is-cheapest" : ""}">
        <div class="need-head" style="padding:0">
          <p class="total">الإجمالي: ${esc(money(offer.total_price))}</p>
          ${offer.cheapest ? `<span class="cheap-badge">الأرخص</span>` : ""}
        </div>
        ${offerBreakdown(offer) ? `<p class="meta">${esc(offerBreakdown(offer))}</p>` : ""}
      </article>` : ""}
      <p class="sync">${esc(harajSync(thread))}</p>
      <div class="thread">${conversationBubbles(thread, messages) || `<p class="meta">ما فيه رسايل معه للحين.</p>`}</div>
      <form class="reply-form" id="user-reply"><div class="reply-row"><input name="body" placeholder="رسالة له بس" autocomplete="off"><button class="primary" type="submit">إرسال</button></div></form>
    </section>`;
}

function renderSeller() {
  const seller = state.seller;
  if (!seller) return `<main class="shell"><section class="seller"><h1>${esc(state.notice || "نحمّل الطلب…")}</h1></section></main>`;
  const options = seller.recipients || [];
  return `<section class="seller">
    <header class="top" style="padding:0 0 16px">${brandLockup()}<span></span></header>
    <h1>قدّم سعرك</h1>
    <p class="lede">املأ البيانات لتقديم السعر للعميل مباشرة</p>
    <div class="ask-box">
      <em style="display:block;font-style:normal;color:var(--ink-muted);font-size:12px">الطلب المطلوب</em>
      <p style="margin:6px 0 0;font-weight:600">${esc(seller.need || seller.original_text)}${seller.city ? ` في ${esc(seller.city)}` : ""}</p>
    </div>
    ${seller.notes ? `<p>${esc(seller.notes)}</p>` : ""}
    ${(seller.attachments || []).map((item) => `<p><a href="/v1/seller/${esc(state.sellerToken)}/attachments/${esc(item.id)}">${esc(item.filename)}</a></p>`).join("")}
    <form id="seller-reply">
      ${options.length > 1 ? `<label class="field"><span>الجهة</span><select name="seller_id">${options.map((item) => `<option value="${esc(item.seller_id)}">${esc(item.seller_name)}</option>`).join("")}</select></label>` : `<input type="hidden" name="seller_id" value="${esc(options[0]?.seller_id || "")}">`}
      <label class="field"><span>الاسم</span><input name="provider_name" value="${esc(options[0]?.seller_name || "")}" required></label>
      <label class="field"><span>رقم الجوال</span><input name="phone" inputmode="tel" placeholder="+966" required></label>
      <label class="field"><span>سعر الخدمة</span><input name="amount" inputmode="decimal" placeholder="ر.س" required></label>
      <p>هل السعر يشمل الوصول / التوصيل؟</p>
      <div class="choice-row">
        <button type="button" class="is-on" data-action="delivery" data-value="yes">نعم، يشمل</button>
        <button type="button" data-action="delivery" data-value="no">لا، السعر لا يشمل</button>
      </div>
      <input type="hidden" name="delivery_included" value="1">
      <label class="field" id="delivery-field" hidden><span>تكلفة الوصول / التوصيل</span><input name="delivery_price" inputmode="decimal" placeholder="ر.س" value="0"></label>
      <div class="total-box">الإجمالي المتوقع: <strong id="seller-total">—</strong></div>
      <label class="field"><span>ملاحظة</span><textarea name="body" rows="3"></textarea></label>
      <button class="primary" type="submit">أرسل عرضك</button>
    </form>
    ${state.notice ? `<p class="status">${esc(state.notice)}</p>` : ""}
  </section>`;
}

function render() {
  clearInterval(poll);
  const view = {
    home: renderHome,
    parse: renderParse,
    flow: renderFlow,
    detail: renderDetail,
    review: renderReview,
    requests: renderRequests,
    thread: renderThread,
    offers: renderOffers,
    seller: renderSeller,
  }[state.view] || renderHome;
  const focused = document.activeElement?.id;
  app.innerHTML = state.view === "seller" ? `<main class="shell">${view()}</main>` : `<main class="shell">${view()}</main>`;
  bindImages(app);
  updateSellerTotal();
  if (focused) document.getElementById(focused)?.focus();
  if ((state.view === "thread" || state.view === "offers") && state.thread?.id) {
    poll = setInterval(() => loadThread(state.thread.id, true), 60000);
  }
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
  state.intents = event.intents || event.groups?.map((item) => item.intent).filter(Boolean) || state.intents;
  state.results = event.results || [];
  state.groups = event.groups || [];
  state.searchState = event.state;
  state.clarification = event.clarification_question || "";
  state.partial = false;
  const asking = event.state === "CLARIFICATION_REQUIRED" || event.state === "LOCATION_AMBIGUOUS";
  state.notice = asking ? "" : finalNotice(event.state, state.results.length);
  if (asking) state.view = "parse";
  else state.view = "flow";
}

async function runSearch(text) {
  const query = (text || "").trim();
  if (!query) return;
  state.query = query;
  state.view = "parse";
  state.partial = true;
  state.searchState = "";
  state.results = [];
  state.groups = [];
  state.intents = [];
  state.intent = null;
  state.clarification = "";
  state.notice = "ندور لك";
  state.filterCity = "";
  state.pricedOnly = false;
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
        state.intents = event.intents || [];
        state.clarification = event.clarification_question || "";
        if (!state.results.length && state.partial) state.notice = "ندور لك";
      } else if (event.type === "status") {
        state.searchState = event.state;
        state.partial = true;
        state.notice = "ندور لك";
      } else if (event.type === "results") {
        state.results = event.results || [];
        if (event.groups) state.groups = event.groups;
        state.partial = true;
        state.searchState = event.state;
        state.notice = "ندور لك";
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

function findResult(key) {
  if (state.selected.has(key)) return state.selected.get(key);
  if (state.active && resultKey(state.active, state.active.need) === key) return state.active;
  for (const group of needGroups()) {
    for (const result of group.results || []) {
      if (resultKey(result, group.need) === key) return { ...result, need: group.need };
    }
  }
  return state.results.find((item) => resultKey(item) === key);
}

async function openResult(key) {
  const result = findResult(key);
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
    const result = findResult(key);
    if (result && sellerOf(result).id) state.selected.set(key, result);
  }
  render();
}

function selectNeed(need, all = false) {
  for (const group of needGroups()) {
    if (!all && group.need !== need) continue;
    for (const result of group.results || []) {
      const tagged = { ...result, need: group.need };
      const key = resultKey(tagged, group.need);
      if (sellerOf(result).id) state.selected.set(key, tagged);
    }
  }
  render();
}

async function sendRequest() {
  if (!state.selected.size || state.busy) return;
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
          return {
            seller_id: seller.id,
            seller_name: seller.name || result.ad?.title || "جهة",
            ad_id: result.ad?.id || null,
            need: result.need || state.intent?.need || null,
            listing_url: result.ad?.url || null,
          };
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
    state.selected.clear();
    await loadRequests();
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
  if (!silent && state.view !== "offers" && state.view !== "thread") state.view = "thread";
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
    const body = String(new FormData(form).get("body") || "").trim();
    if (!body || !state.thread) return;
    const need = state.activeNeed || state.thread.need || null;
    const json = { body, need };
    if (state.activeSeller) json.seller_id = state.activeSeller;
    else if (state.replyTo) json.reply_to = state.replyTo.id;
    else json.seller_id = mentionedSeller(body, itemRecipients(state.thread))?.seller_id || null;
    api(`/v1/requests/${state.thread.id}/messages`, { method: "POST", json })
      .then(() => {
        state.replyTo = null;
        state.view = "thread";
        return loadThread(state.thread.id);
      })
      .catch(() => {});
  } else if (form.id === "seller-reply") {
    event.preventDefault();
    const data = new FormData(form);
    const amount = Number(String(data.get("amount") || "").replace(/[^\d.]/g, ""));
    const extra = Number(String(data.get("delivery_price") || "").replace(/[^\d.]/g, ""));
    api(`/v1/seller/${state.sellerToken}/messages`, {
      method: "POST",
      skipAuth: true,
      json: {
        body: data.get("body") || "",
        seller_id: data.get("seller_id") || null,
        provider_name: data.get("provider_name") || null,
        phone: data.get("phone") || null,
        offer_amount: Number.isFinite(amount) && amount > 0 ? amount : null,
        offer_currency: "SAR",
        delivery_included: data.get("delivery_included") !== "0",
        delivery_price: Number.isFinite(extra) ? extra : 0,
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

function updateSellerTotal() {
  const form = document.getElementById("seller-reply");
  const total = document.getElementById("seller-total");
  if (!form || !total) return;
  const base = Number(String(form.amount.value || "").replace(/[^\d.]/g, ""));
  const extra = form.delivery_included.value === "0" ? Number(String(form.delivery_price.value || "").replace(/[^\d.]/g, "")) || 0 : 0;
  total.textContent = Number.isFinite(base) && base > 0 ? money(base + extra) : "—";
}

document.addEventListener("input", (event) => {
  if (event.target.id === "note") state.note = event.target.value;
  if (event.target.closest("#seller-reply")) updateSellerTotal();
  if (event.target.closest("#user-reply") && !state.activeSeller && state.thread) {
    const list = document.getElementById("mention-list");
    const typed = event.target.value.match(/@([^@]*)$/);
    if (list) {
      const names = typed ? itemRecipients(state.thread).filter((item) => item.seller_name.includes(typed[1].trim())) : [];
      list.hidden = !names.length;
      list.innerHTML = names.map((item) => `<button type="button" data-action="mention" data-name="${esc(item.seller_name)}">${esc(item.seller_name)}</button>`).join("");
    }
  }
  if (event.target.name === "query") {
    state.query = event.target.value;
    const counter = document.querySelector(".counter");
    if (counter) counter.textContent = `${formatCount(Math.min(500, state.query.length))}/٥٠٠`;
  }
});

document.addEventListener("change", (event) => {
  if (event.target.dataset.action === "add-files") {
    rememberFiles(event.target.files || []);
    render();
  }
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
  else if (action === "search-now") runSearch(state.query || document.querySelector("[name=query]")?.value);
  else if (action === "requests") loadRequests().catch(() => {});
  else if (action === "remove-file") {
    const removed = state.files.splice(Number(target.dataset.index), 1)[0];
    if (removed?.preview) URL.revokeObjectURL(removed.preview);
    render();
  } else if (action === "open") openResult(target.dataset.key);
  else if (action === "toggle") toggle(target.dataset.key);
  else if (action === "select-need") selectNeed(target.dataset.need);
  else if (action === "select-all") selectNeed("", true);
  else if (action === "review") {
    state.view = "review";
    render();
  } else if (action === "show-results") {
    state.view = "flow";
    render();
  } else if (action === "sort") {
    state.sort = target.dataset.sort;
    render();
  } else if (action === "city") {
    state.filterCity = target.dataset.city || "";
    render();
  } else if (action === "priced") {
    state.pricedOnly = !state.pricedOnly;
    render();
  } else if (action === "back-results") {
    state.view = "flow";
    render();
  } else if (action === "retry") runSearch(state.query);
  else if (action === "answer") runSearch(`${state.query} ${target.dataset.value}`);
  else if (action === "send") sendRequest();
  else if (action === "thread") {
    state.activeSeller = "";
    loadThread(target.dataset.id).catch(() => {});
  } else if (action === "offers") {
    state.activeNeed = target.dataset.need || "";
    ensureAuth()
      .then(() => api(`/v1/requests/${target.dataset.id}`))
      .then((thread) => {
        state.thread = thread;
        state.view = "offers";
        render();
      })
      .catch(() => {});
  } else if (action === "chat") {
    state.replyTo = null;
    state.activeSeller = target.dataset.seller || "";
    state.view = "thread";
    render();
  } else if (action === "all-replies") {
    state.activeSeller = "";
    state.view = "thread";
    render();
  } else if (action === "reply") {
    const message = (state.thread?.messages || []).find((item) => item.id === target.dataset.message);
    if (!message) return;
    const recipient = (state.thread.recipients || []).find((item) => item.seller_id === message.seller_id);
    state.replyTo = { id: message.id, name: recipient?.seller_name || "جهة", body: message.body };
    render();
    document.querySelector("#user-reply input")?.focus();
  } else if (action === "cancel-reply") {
    state.replyTo = null;
    render();
  } else if (action === "mention") {
    const input = document.querySelector("#user-reply input");
    if (!input) return;
    input.value = input.value.replace(/@[^@]*$/, `@${target.dataset.name} `);
    document.getElementById("mention-list").hidden = true;
    input.focus();
  } else if (action === "offers-back") {
    state.view = "offers";
    render();
  } else if (action === "delivery") {
    const form = document.getElementById("seller-reply");
    if (!form) return;
    form.delivery_included.value = target.dataset.value === "yes" ? "1" : "0";
    form.querySelectorAll("[data-action=delivery]").forEach((btn) => btn.classList.toggle("is-on", btn === target));
    const field = document.getElementById("delivery-field");
    if (field) field.hidden = target.dataset.value === "yes";
    updateSellerTotal();
  }
  else if (action === "copy-reply" && state.thread?.reply_token) {
    const url = `${location.origin}/s/${state.thread.reply_token}`;
    const done = () => {
      state.copied = true;
      render();
      setTimeout(() => {
        state.copied = false;
        if (state.view === "thread") render();
      }, 2000);
    };
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(url).then(done).catch(done);
    else done();
  }
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.view === "thread" && state.thread?.id) {
    loadThread(state.thread.id, true).catch(() => {});
  }
});

window.addEventListener("popstate", () => {
  const match = location.pathname.match(/^\/s\/([^/]+)\/?$/);
  if (match) loadSeller(decodeURIComponent(match[1]));
  else if (state.view === "seller") {
    state.view = "home";
    render();
  }
});

if (sellerRoute) loadSeller(decodeURIComponent(sellerRoute[1]));
else render();
