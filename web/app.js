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
  account: null,
  authMode: "login",
  showPassword: false,
  authError: "",
  returnView: "home",
  picked: null,
  chatFiles: [],
  searching: false,
  seenCards: new Set(),
  shownCount: 0,
  unreadTotal: 0,
  pushState: "",
  pushDismissed: (() => {
    try {
      return localStorage.getItem("farq.pushDismissed") === "1";
    } catch (_error) {
      return false;
    }
  })(),
  seller: null,
  sellerToken: "",
  token: localStorage.getItem("farq.token") || "",
  busy: false,
  subPlans: [],
  subStatus: null,
  subActivePlan: "",
  subMountedPlan: "",
  subMountFailed: false,
  subBusy: false,
  subError: "",
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
const subscribeCallback = location.pathname === "/subscribe/callback";

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

// A thin bar at the top while the app is waiting on the server (background polling stays quiet).
let busyRequests = 0;
function setBusy(delta) {
  busyRequests = Math.max(0, busyRequests + delta);
  document.documentElement.classList.toggle("is-busy", busyRequests > 0);
}

async function api(path, options = {}) {
  if (options.quiet) return request(path, options);
  setBusy(1);
  try {
    return await request(path, options);
  } finally {
    setBusy(-1);
  }
}

async function request(path, { method = "GET", json, form, skipAuth = false, quiet = false } = {}) {
  const headers = {};
  if (state.token && !skipAuth) headers.Authorization = `Bearer ${state.token}`;
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) body = form;
  const response = await fetch(path, { method, headers, body });
  if (response.status === 401 && !skipAuth) {
    signOutLocally();
    requireSignIn();
    throw new Error("sign-in required");
  }
  if (!response.ok) {
    const error = new Error("request failed");
    error.status = response.status;
    throw error;
  }
  const type = response.headers.get("content-type") || "";
  return type.includes("json") ? response.json() : response;
}

// Everyone signs in with a Taseer account before using the app; requests belong to that account.
function requireSignIn() {
  if (state.view !== "auth") state.returnView = state.view === "seller" ? "home" : state.view;
  state.view = "auth";
  state.authError = "";
  render();
}

async function ensureAuth() {
  if (state.token) return;
  requireSignIn();
  throw new Error("sign-in required");
}

function signOutLocally() {
  state.token = "";
  state.account = null;
  state.requests = [];
  state.requestsLoaded = false;
  state.thread = null;
  state.unreadTotal = 0;
  threadCache.clear();
  try {
    localStorage.removeItem("farq.token");
  } catch (_error) {}
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

function cityInText(text) {
  const value = String(text || "");
  const found = state.cities.find((city) => value.includes(city.label) || value.includes(city.value));
  return found ? found.value : "";
}

function renderCityAsk() {
  return `${appBar({ back: "home" })}
  <section class="page tight city-ask">
    <div class="original-card"><span>طلبك</span><strong>${esc(state.query)}</strong></div>
    <div class="section-head"><h1>في أي مدينة؟</h1><p class="lede">نختصر البحث على الجهات القريبة منك.</p></div>
    ${cityChoices("search-city")}
  </section>`;
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
  if (status === "PARTIAL_RESULTS") return count ? "ما قدرنا نكمل البحث. هذي الخيارات اللي وصلت." : "البحث ما اكتمل.";
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

// One bar on every screen: the mark with «فرق تسعير» (or the screen's title), and the back arrow
// pointing right, the way back reads in Arabic.
function appBar({ title = "", subtitle = "", back = "", end = "", avatar = "", lined = true } = {}) {
  const start = back
    ? `<button class="icon-btn back" type="button" data-action="${esc(back)}" aria-label="رجوع">${icon("chevron", { size: 20 })}</button>`
    : `<span class="slot" aria-hidden="true"></span>`;
  const mark = avatar || `<span class="bar-mark" aria-hidden="true"><img src="/brand/logo-square.png" alt="" width="30" height="30"></span>`;
  const heading = title
    ? `<span class="bar-title"><strong><bdi>${esc(title)}</bdi></strong>${subtitle ? `<span><bdi>${esc(subtitle)}</bdi></span>` : ""}</span>`
    : `<a class="bar-title brand" href="/" data-action="home" aria-label="فرق تسعير"><span class="wordmark"><img src="/brand/farq-wordmark-dark.svg" alt="فرق" width="52" height="24"><span>تسعير</span></span></a>`;
  return `<header class="top-bar${lined ? " lined" : ""}">${start}<span class="bar-body">${mark}${heading}</span>${end || '<span class="slot" aria-hidden="true"></span>'}</header>`;
}

function topBar(options = {}) {
  return appBar(options);
}

function tabBar(active) {
  return `<nav class="tab-bar" aria-label="التنقل">
    <button class="tab${active === "home" ? " active" : ""}" type="button" data-action="home">${icon("home")}<span>الرئيسية</span></button>
    <button class="tab${active === "requests" ? " active" : ""}" type="button" data-action="requests"><span class="tab-icon">${icon("briefcase")}<span class="unread-dot" data-unread-total ${state.unreadTotal ? "" : "hidden"} aria-label="فيه ردود جديدة"></span></span><span>طلباتي</span></button>
    <button class="tab${active === "subscribe" ? " active" : ""}" type="button" data-action="subscribe">${icon("check-square")}<span>الاشتراك</span></button>
  </nav>`;
}

function shell(body, { bare = false } = {}) {
  if (bare) return `<main class="shell">${body}</main>`;
  return `<main class="shell">${body}</main>`;
}

// The reveal button sits beside the input, not inside its label, so one tap counts once.
function field({ id, label, icon, attrs, after = "" }) {
  return `<div class="field-line">
    <label for="${id}">${esc(label)}</label>
    <span class="input-wrap"><img class="input-icon" src="/icons/${icon}.svg" alt="" width="20" height="20"><input id="${id}" ${attrs}>${after}</span>
  </div>`;
}

function renderAuth() {
  const register = state.authMode === "register";
  const reveal = state.showPassword ? "eye-off" : "eye";
  return `${appBar()}
  <section class="page auth">
    <div class="auth-hero">
      <span class="auth-glow" aria-hidden="true"></span>
      <img class="auth-logo" src="/brand/logo-square.png" alt="" width="84" height="84">
    </div>
    <h1>${register ? "أنشئ حسابك" : "سجّل دخولك"}</h1>
    <p class="lede">${register ? "حساب واحد تتابع فيه كل طلباتك ومحادثاتك مع البائعين." : "لازم تسجّل دخول عشان تطلب أسعار وتراسل البائعين."}</p>
    <form id="auth-form" class="auth-card" novalidate>
      ${register ? field({ id: "auth-name", label: "الاسم", icon: "user", attrs: 'name="name" autocomplete="name" required minlength="2" maxlength="60" placeholder="اسمك"' }) : ""}
      ${field({ id: "auth-email", label: "البريد الإلكتروني", icon: "mail", attrs: 'name="email" type="email" inputmode="email" autocomplete="email" dir="ltr" required placeholder="name@example.com"' })}
      ${field({
        id: "auth-password",
        label: "كلمة السر",
        icon: "lock",
        attrs: `name="password" type="${state.showPassword ? "text" : "password"}" autocomplete="${register ? "new-password" : "current-password"}" dir="ltr" required minlength="8" placeholder="${register ? "٨ أحرف أو أكثر" : ""}"`,
        after: `<button class="reveal" type="button" data-action="toggle-password" aria-label="${state.showPassword ? "إخفاء كلمة السر" : "إظهار كلمة السر"}" aria-pressed="${state.showPassword}"><img src="/icons/${reveal}.svg" alt="" width="22" height="22"></button>`,
      })}
      ${state.authError ? `<p class="status warn" role="alert">${esc(state.authError)}</p>` : ""}
      <button class="primary block tall" type="submit" ${state.busy ? "disabled" : ""}>${state.busy ? "لحظة…" : register ? "إنشاء الحساب" : "دخول"}</button>
    </form>
    <p class="auth-switch">${register ? "عندك حساب؟" : "ما عندك حساب؟"} <button class="text-btn" type="button" data-action="auth-mode">${register ? "سجّل دخول" : "أنشئ حساب"}</button></p>
  </section>`;
}

async function submitAuth(form) {
  const data = new FormData(form);
  const register = state.authMode === "register";
  const json = { email: String(data.get("email") || "").trim(), password: String(data.get("password") || "") };
  if (register) json.name = String(data.get("name") || "").trim();
  if (register && json.name.length < 2) return showAuthError("اكتب اسمك");
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(json.email)) return showAuthError("اكتب بريد إلكتروني صحيح");
  if (json.password.length < 8) return showAuthError("كلمة السر لازم تكون ٨ أحرف أو أكثر");
  state.busy = true;
  render();
  try {
    const result = await api(register ? "/v1/auth/register" : "/v1/auth/login", { method: "POST", json, skipAuth: true });
    state.token = result.token;
    state.account = { name: result.name, email: result.email };
    try {
      localStorage.setItem("farq.token", state.token);
    } catch (_error) {}
    state.busy = false;
    state.view = state.returnView && state.returnView !== "auth" ? state.returnView : "home";
    if (state.view === "requests") loadRequests().catch(() => {});
    else render();
    refreshUnread();
  } catch (error) {
    state.busy = false;
    const messages = { 401: "البريد أو كلمة السر غير صحيحة", 409: "هذا البريد مسجّل من قبل، سجّل دخول", 422: "تأكد من البيانات" };
    showAuthError(messages[error.status] || "ما قدرنا نكمل، جرّب مرة ثانية");
  }
}

function showAuthError(message) {
  state.authError = message;
  render();
  return null;
}

function renderHome() {
  const chips = ["سباك بالرياض", "كهربائي بجدة", "نقل عفش"];
  return `${appBar()}
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
  const understood = [state.intent?.need, typeof state.intent?.location_city?.value === "string" ? cityLabel(state.intent.location_city.value) : state.city ? cityLabel(state.city) : ""]
    .filter(Boolean)
    .join(" · ");
  return `${appBar()}
  <section class="page searching" aria-live="polite">
    <div class="pulse"><div class="pulse-mid"><div class="pulse-core">${icon("search-white", { size: 20 })}</div></div></div>
    <div><h2>ندور لك...</h2><p class="lede">${esc(understood || "نبحث في أكثر من 2,000 جهة")}</p></div>
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
  const text = adStory(result.ad?.description) || lines[1] || lines[0] || "";
  return String(text).replace(/\s+/g, " ").trim().slice(0, 72);
}

let newCardsInBatch = 0;
function renderCard(result) {
  const key = resultKey(result);
  // Cards that were already on screen must not flash again when the next batch lands.
  const fresh = state.seenCards && !state.seenCards.has(key);
  const animated = fresh && newCardsInBatch < 8;
  if (fresh) newCardsInBatch += 1;
  const selected = state.selected.has(key);
  const seller = sellerOf(result);
  const name = result.ad?.title || seller.name || "جهة";
  const price = money(result.ad?.price_amount);
  const city = result.ad?.city || seller.city || "";
  const blurb = snip(result);
  const thumb = result.ad && imageSources(result.ad).length ? frame(result.ad, { thumb: true }) : `<span class="mark">${initial(seller.name || name)}</span>`;
  return `<article class="vendor${selected ? " is-selected" : ""}${animated ? " is-new" : ""}"${animated ? ` style="animation-delay:${(newCardsInBatch - 1) * 35}ms"` : ""}>
    <button class="tick" type="button" data-action="toggle" data-key="${esc(key)}" aria-pressed="${selected}" aria-label="${selected ? "إزالة الجهة" : "اختيار الجهة"}">${selected ? icon("check", { size: 14 }) : ""}</button>
    <button class="vendor-body" type="button" data-action="open" data-key="${esc(key)}">
      ${thumb}
      <span class="vendor-copy">
        <strong><bdi>${esc(name)}</bdi></strong>
        <span class="vendor-meta">${seller.name ? `<bdi>${esc(seller.name)}</bdi>` : ""}${seller.name && city ? " · " : ""}${city ? `<span class="muted">${esc(cityLabel(city))}</span>` : ""}</span>
        ${blurb ? `<span class="snip">${esc(blurb)}</span>` : ""}
        <span class="price${price ? "" : " on-ask"}">${esc(price || "السعر عند الطلب")}</span>
      </span>
    </button>
  </article>`;
}

function renderFlow() {
  const asking = state.searchState === "CLARIFICATION_REQUIRED" || state.searchState === "LOCATION_AMBIGUOUS";
  const live = state.searching || (state.partial && (state.searchState === "LIVE_SEARCHING" || state.searchState === "PARTIAL_RESULTS"));
  const showEmpty = !asking && !state.partial && state.results.length === 0;
  // The pulse is the first frame after «ابحث», not an empty list.
  if (live && !state.results.length && !asking) return renderSearching();
  const need = state.intent?.need || facts(state.intent)[0] || state.query || "النتائج";
  return `${appBar({ title: asking ? "" : "النتائج", back: "home" })}
  <section class="page tight flow">
    ${asking ? "" : `<div class="group-head"><span data-count="${state.results.length}">${formatCount(state.shownCount || state.results.length)} جهة مطابقة</span><strong><bdi>${esc(need)}</bdi></strong></div>`}
    ${asking ? renderFacts() + renderQuestion() : ""}
    <p class="status ${live ? "live" : ""}" aria-live="polite">${esc(showEmpty ? "" : state.notice)}</p>
    ${showEmpty ? `<div class="empty"><h2>${esc(state.notice || "ما فيه شيء نعرضه")}</h2><button class="text-btn" type="button" data-action="retry">جرّب مرة ثانية</button></div>` : ""}
    ${state.results.length ? `<div class="cards">${((newCardsInBatch = 0), state.results.map(renderCard).join(""))}</div>` : ""}
    ${dock()}
  </section>`;
}

function dock() {
  const count = state.selected.size;
  if (!count || state.view === "review") return "";
  const label = count === 1 ? "اطلب السعر من جهة واحدة" : `اطلب السعر من ${formatCount(count)} جهات`;
  return `<div class="dock"><p class="hint">سنرسل طلبك لكل الجهات المختارة</p><button class="primary compact" type="button" data-action="review">${esc(label)}</button></div>`;
}

// Haraj ads carry boilerplate lines that mean nothing inside Taseer.
const AD_NOISE = [/رقم\s*الجوال\s*يظهر/, /اضغط\s*(على\s*)?(زر\s*)?تواصل/, /للتواصل\s*واتس/, /^\s*للجادين\s*فقط\s*$/];
function adStory(text) {
  return String(text || "")
    .split("\n")
    .filter((line) => line.trim() && !AD_NOISE.some((pattern) => pattern.test(line)))
    .join("\n")
    .trim();
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
  return `${appBar({ title: "التفاصيل", back: "back-results" })}
  <article class="page tight detail">
    <div class="gallery" id="gallery">${frames}</div>
    ${gallery.length > 1 ? `<div class="gallery-dots" id="gallery-dots">${gallery.map((_item, index) => `<span class="dot${index ? "" : " is-on"}"></span>`).join("")}</div>` : ""}
    <h1 style="font-size:22px"><bdi>${esc(result.ad?.title || seller.name || "")}</bdi></h1>
    <p class="price big">${esc(price || "السعر عند الطلب")}</p>
    <p class="meta">${esc(place(result))}${seller.name ? ` · ` : ""}${seller.name ? `<bdi>${esc(seller.name)}</bdi>` : ""}</p>
    ${adStory(result.ad?.description) ? `<p class="story">${esc(adStory(result.ad.description))}</p>` : ""}
    ${result.ad?.listing_state === "deleted" ? `<p class="warn">هذا الإعلان محذوف.</p>` : ""}
    <div class="detail-actions"><button class="primary" type="button" data-action="quote" data-key="${esc(key)}">${selected ? "كمّل طلب عرض السعر" : "طلب عرض سعر"}</button></div>
    ${dock()}
  </article>`;
}

function renderReview() {
  const names = [...state.selected.entries()].map(([key, result]) => ({ key, name: sellerOf(result).name || "بائع" }));
  const city = customerCity();
  const ready = state.selected.size > 0 && Boolean(city);
  return `${appBar({ title: "طلب عرض سعر", back: "back-results" })}
  <section class="page tight review">
    <h1>جاهز نرسله؟</h1>
    <p class="summary">${esc(state.query)}</p>
    <ul class="who">${names.map((item) => `<li><bdi>${esc(item.name)}</bdi><button class="plain-btn" type="button" data-action="unselect" data-key="${esc(item.key)}">شيل</button></li>`).join("")}</ul>
    ${city ? `<p class="meta">المدينة: ${esc(cityLabel(city))}</p>` : `<div class="section-head"><h1 style="font-size:20px">في أي مدينة؟</h1></div>${cityChoices("pick-city")}`}
    <label>ملاحظة<textarea class="note" id="note" placeholder="اختياري">${esc(state.note)}</textarea></label>
    <div class="attach-block">
      <p class="optional">أرفق صورة أو ملف PDF (اختياري).</p>
      <div class="composer-bar">
        <label class="file-btn">${icon("camera")}<span>صورة</span><input type="file" accept="image/*" data-action="add-files"></label>
        <label class="file-btn">${icon("paperclip")}<span>ملف PDF</span><input type="file" accept="application/pdf" data-action="add-files"></label>
      </div>
    </div>
    ${filePreview()}
    ${state.notice ? `<p class="status warn" role="alert">${esc(state.notice)}</p>` : ""}
    <button class="primary block" type="button" data-action="send" ${ready ? "" : "disabled"}>${state.busy ? "نرسل…" : "أرسل طلب عرض السعر"}</button>
  </section>`;
}

// Phone notifications when a supplier replies. iPhone only allows them for a site added to the home screen.
const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const isStandalone = window.matchMedia?.("(display-mode: standalone)").matches || navigator.standalone === true;
const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);

function notifyBanner() {
  if (state.pushState === "on" || state.pushDismissed) return "";
  if (isIOS && !isStandalone) {
    return `<div class="notify-banner"><div><strong>تبي تنبيه لما يردون عليك؟</strong><span>اضغط زر المشاركة ثم «إضافة إلى الشاشة الرئيسية»، وافتح تسعير من هناك.</span></div><button type="button" data-action="dismiss-notify" aria-label="إغلاق">✕</button></div>`;
  }
  if (!pushSupported || typeof Notification === "undefined" || Notification.permission === "denied") return "";
  if (Notification.permission === "granted" && state.pushState !== "off") return "";
  return `<div class="notify-banner"><div><strong>تبي تنبيه لما يردون عليك؟</strong><span>يوصلك إشعار على جوالك أول ما يرد أي بائع.</span></div><button class="notify-on" type="button" data-action="enable-notify">فعّل التنبيهات</button></div>`;
}

function urlKey(base64) {
  const padded = (base64 + "=".repeat((4 - (base64.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(padded), (char) => char.charCodeAt(0));
}

async function enableNotifications(fromClick = false) {
  if (!pushSupported) return;
  const registration = await navigator.serviceWorker.ready;
  if (fromClick && Notification.permission === "default") await Notification.requestPermission();
  if (Notification.permission !== "granted") {
    state.pushState = "off";
    return;
  }
  const { public_key: key } = await api("/v1/push/key", { skipAuth: true });
  if (!key) return;
  const subscription = (await registration.pushManager.getSubscription()) || (await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlKey(key) }));
  await ensureAuth();
  await api("/v1/push/subscribe", { method: "POST", json: subscription.toJSON() });
  state.pushState = "on";
}

function setUnread(requests) {
  state.unreadTotal = (requests || []).reduce((sum, item) => sum + (item.unread_count || 0), 0);
  document.querySelectorAll("[data-unread-total]").forEach((node) => {
    node.hidden = !state.unreadTotal;
  });
}

// Keep the unread count fresh on every screen except the open conversation.
async function refreshUnread() {
  if (!state.token || state.view === "thread" || state.view === "seller" || document.visibilityState !== "visible") return;
  try {
    const data = await api("/v1/requests", { quiet: true });
    const changed = JSON.stringify(data.requests || []) !== JSON.stringify(state.requests);
    state.requests = data.requests || [];
    setUnread(state.requests);
    if (changed && state.view === "requests") render();
  } catch (_error) {}
}

function renderRequests() {
  const account = `<button class="account-chip" type="button" data-action="sign-out">${state.account?.name ? `<bdi>${esc(state.account.name)}</bdi> · ` : ""}خروج</button>`;
  const head = appBar({ end: account });
  if (state.requestsLoading && !state.requests.length) {
    const row = `<div class="chat-row"><span class="chat-avatar skeleton"></span><span class="chat-main"><span class="skeleton line" style="width:55%"></span><span class="skeleton line" style="width:80%"></span><span class="skeleton line" style="width:35%"></span></span></div>`;
    return `${head}<section class="page soft requests-page" aria-busy="true"><div class="chat-list">${row.repeat(4)}</div></section>${tabBar("requests")}`;
  }
  if (!state.requests.length) {
    return `${head}<section class="page soft requests-page"><p class="lede">لما ترسل طلب عرض سعر، يبين هنا.</p></section>${tabBar("requests")}`;
  }
  // A chat list, like WhatsApp's: each request is a conversation you tap to open.
  return `${head}<section class="page soft requests-page">${notifyBanner()}<div class="chat-list">${state.requests
    .map((item) => {
      const need = item.need || item.original_text;
      const names = item.seller_names || [];
      const one = names.length === 1;
      const when = ago(item.last_message_at || item.created_at);
      const unread = item.unread_count || 0;
      const price = item.latest_offer_amount != null ? `أرخص سعر ${money(item.latest_offer_amount)}` : item.replied_count ? `ردّ ${formatCount(item.replied_count)} من ${formatCount(item.recipient_count || 0)}` : `بانتظار الرد من ${formatCount(item.recipient_count || 0)}`;
      const avatar = `<span class="chat-avatar" style="background:${SELLER_COLORS[0]}">${initial(names[0] || need)}</span>${one ? "" : `<span class="chat-count">${formatCount(names.length || item.recipient_count || 0)}</span>`}`;
      return `<button class="chat-row${unread ? " has-unread" : ""}" type="button" data-action="thread" data-id="${esc(item.id)}" aria-label="افتح محادثة ${esc(need)}">
        ${avatar}
        <span class="chat-main">
          <span class="chat-top"><strong><bdi>${esc(one ? names[0] : need)}</bdi></strong><time>${esc(when || "")}</time></span>
          <span class="chat-sub"><bdi>${esc(one ? need : names.join("، "))}</bdi></span>
          <span class="chat-bottom"><span class="chat-last">${esc(item.last_message || "")}</span>${unread ? `<span class="unread-count">${formatCount(unread)}</span>` : ""}</span>
          <span class="chat-price">${esc(price)}</span>
        </span>
        <span class="chat-open" aria-hidden="true">${icon("chevron", { size: 18 })}</span>
      </button>`;
    })
    .join("")}</div></section>${tabBar("requests")}`;
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

const CHAT_ZONE = "Asia/Riyadh";
const chatClock = new Intl.DateTimeFormat("ar-SA", { hour: "numeric", minute: "2-digit", timeZone: CHAT_ZONE });
const chatDate = new Intl.DateTimeFormat("ar-SA", { day: "numeric", month: "long", timeZone: CHAT_ZONE });
const dayKey = (date) => date.toLocaleDateString("en-CA", { timeZone: CHAT_ZONE });

function chatTime(iso) {
  const date = new Date(iso || "");
  return Number.isNaN(date.getTime()) ? "" : chatClock.format(date);
}

function chatDay(iso) {
  const date = new Date(iso || "");
  if (Number.isNaN(date.getTime())) return "";
  const today = new Date();
  const yesterday = new Date(today.getTime() - 86400000);
  if (dayKey(date) === dayKey(today)) return "اليوم";
  if (dayKey(date) === dayKey(yesterday)) return "أمس";
  return chatDate.format(date);
}

// Colored sender names in the item conversation, the way a WhatsApp group shows them.
// One color per supplier, from the Farq token palette, handed out in the request's order so no two
// suppliers in the same conversation share one.
const SELLER_COLORS = ["#0B6A63", "#22577A", "#DC6E41", "#C7911E", "#248F5C", "#22162B", "#065656", "#BE5532"];
function sellerColor(thread, sellerId) {
  const ids = [...new Set((thread?.recipients || []).map((item) => item.seller_id))];
  const index = ids.indexOf(sellerId);
  if (index >= 0) return SELLER_COLORS[index % SELLER_COLORS.length];
  let hash = 0;
  for (const char of String(sellerId || "")) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return SELLER_COLORS[hash % SELLER_COLORS.length];
}

const TICK = {
  queued: '<svg class="wa-tick" viewBox="0 0 16 16" aria-label="بانتظار الإرسال"><circle cx="8" cy="8" r="6"/><path d="M8 4.5V8l2.5 1.5"/></svg>',
  sent: '<svg class="wa-tick" viewBox="0 0 16 16" aria-label="وصلت"><path d="m3 8.5 3 3 7-7"/></svg>',
  failed: '<span class="wa-failed" aria-label="ما وصلت">!</span>',
};

function deliveryTick(message) {
  const deliveries = message.deliveries || [];
  if (!deliveries.length) return "";
  const sent = deliveries.filter((item) => item.status === "sent").length;
  if (sent) return TICK.sent;
  if (message.delivery_state === "failed") return TICK.failed;
  return TICK.queued;
}

// Messages leave one at a time, 20 s apart, so a message to several suppliers shows its progress.
function deliveryProgress(message) {
  const deliveries = message.deliveries || [];
  const sent = deliveries.filter((item) => item.status === "sent").length;
  const waiting = deliveries.filter((item) => item.status === "queued" || item.status === "sending").length;
  if (!deliveries.length || !waiting) return "";
  if (deliveries.length === 1) return "يُرسل الآن…";
  return `وصلت لـ ${formatCount(sent)} من ${formatCount(deliveries.length)} · الباقي خلال ${formatCount(Math.max(1, Math.ceil((waiting * 20) / 60)))} د`;
}

function mediaHtml(media) {
  return (media || [])
    .map((item) => {
      const url = esc(item.url || "");
      if (String(item.type || "").startsWith("image/")) {
        const ratio = item.width && item.height ? ` style="aspect-ratio:${Number(item.width)}/${Number(item.height)}"` : "";
        return `<a class="wa-photo" href="${url}" target="_blank" rel="noopener"><img src="${url}" alt="صورة" loading="lazy"${ratio}></a>`;
      }
      if (item.type === "application/pdf") {
        const size = item.size ? ` · ${formatCount(Math.max(1, Math.round(item.size / 1024)))} ك.ب` : "";
        return `<a class="wa-doc" href="${url}" target="_blank" rel="noopener"><span class="wa-file-icon">PDF</span><span>${esc(item.name || "ملف")}${size}</span></a>`;
      }
      if (item.type === "video/mp4") return `<video class="wa-video" src="${url}" controls preload="metadata"></video>`;
      if (item.type === "audio/aac") return `<audio class="wa-audio" src="${url}" controls preload="none"></audio>`;
      return "";
    })
    .join("");
}

function waBubble(thread, message, { group, byId, first = true, best = null }) {
  const mine = message.sender_role !== "seller";
  const body = esc(message.body || "").replace(/\n/g, "<br>");
  const media = mediaHtml(message.media);
  const quoted = message.reply_to ? byId.get(message.reply_to) : null;
  const quote = quoted
    ? `<div class="wa-quote" style="--who:${quoted.sender_role === "seller" ? sellerColor(thread, quoted.seller_id) : "#83F1B1"}"><strong>${esc(quoted.sender_role === "seller" ? sellerName(thread, quoted.seller_id) : "أنت")}</strong><span>${esc(snippet(quoted.body, 70))}</span></div>`
    : "";
  const time = `<time>${esc(chatTime(message.created_at))}</time>`;
  if (mine) {
    const some = message.scope === "some_sellers" ? (message.deliveries || []).map((item) => sellerName(thread, item.seller_id)).join("، ") : "";
    const only = group && message.scope === "single_seller" && message.seller_id ? `<div class="wa-to">إلى ${esc(sellerName(thread, message.seller_id))} فقط</div>` : group && some ? `<div class="wa-to">إلى ${esc(some)}</div>` : "";
    const failed = message.delivery_state === "failed" ? `<div class="wa-to warn">ما وصلت الرسالة</div>` : "";
    const progress = deliveryProgress(message);
    return `<div class="wa-row out${first ? " first" : ""}"><div class="wa-bubble${media && !body ? " media-only" : ""}">${quote}${media}${body ? `<div class="wa-text">${body}</div>` : ""}${only}${failed}${progress ? `<div class="wa-to">${esc(progress)}</div>` : ""}<span class="wa-meta">${time}${deliveryTick(message)}</span></div></div>`;
  }
  // A group chat: each supplier keeps his colour, his avatar and his name, and his price stands out.
  const color = sellerColor(thread, message.seller_id);
  const who = sellerName(thread, message.seller_id);
  const price = messagePrice(message);
  const cheapest = price != null && best != null && price === best;
  const avatar = group ? `<span class="wa-avatar-sm"${first ? ` style="background:${color}"` : ""}>${first ? initial(who) : ""}</span>` : "";
  const name = group && first
    ? `<button class="wa-name" type="button" data-action="seller-filter" data-seller="${esc(message.seller_id || "")}" style="color:${color}"><bdi>${esc(who)}</bdi></button>`
    : "";
  const offer = price != null
    ? `<div class="wa-offer${cheapest ? " is-cheapest" : ""}">
        <span class="wa-offer-head">عرض سعر${cheapest ? `<span class="wa-cheapest">الأرخص</span>` : ""}</span>
        <strong>${esc(money(price))}</strong>
      </div>`
    : "";
  return `<div class="wa-row in${first ? " first" : ""}" style="--seller:${color}">${avatar}<div class="wa-bubble${price != null ? " has-offer" : ""}">${name}${quote}${offer}${media}${body ? `<div class="wa-text">${body}</div>` : ""}<span class="wa-meta"><button class="wa-reply" type="button" data-action="reply" data-message="${esc(message.id)}" aria-label="ردّ">ردّ</button>${time}</span></div></div>`;
}

function waMessages(thread, messages, group) {
  const byId = new Map((thread.messages || []).map((item) => [item.id, item]));
  const prices = (thread.offers || []).map((item) => item.total_price).filter((value) => value != null);
  const best = prices.length ? Math.min(...prices) : null;
  let lastDay = "";
  let lastSender = "";
  return messages
    .map((message) => {
      const day = chatDay(message.created_at);
      const divider = day && day !== lastDay ? `<div class="wa-day"><span>${esc(day)}</span></div>` : "";
      const sender = message.sender_role === "seller" ? `s:${message.seller_id}` : "me";
      // Messages in a row from the same sender group together, the way WhatsApp stacks them.
      const first = Boolean(divider) || sender !== lastSender;
      lastDay = day || lastDay;
      lastSender = sender;
      return divider + waBubble(thread, message, { group, byId, first, best });
    })
    .join("");
}

// The customer's conversation, laid out like WhatsApp. With one seller it is a private chat;
// with several it is the item's group chat. Sellers never see it: each has only their own Haraj
// conversation, and every message here is routed to or synced from those.
function renderThread() {
  const thread = state.thread;
  if (!thread) {
    const known = state.requests.find((item) => item.id === state.pendingThread);
    const names = known?.seller_names || [];
    const title = names.length === 1 ? names[0] : known?.need || known?.original_text || "المحادثة";
    return `<div class="wa-screen">
      ${appBar({ title, subtitle: "نحمّل المحادثة…", back: "requests", avatar: `<span class="bar-avatar${names.length === 1 ? "" : " group"}"${names.length === 1 ? ` style="background:${SELLER_COLORS[0]}"` : ""}>${names.length === 1 ? initial(title) : formatCount(names.length || 0)}</span>` })}
      <section class="wa-wall" aria-busy="true">
        <div class="wa-row out"><div class="wa-bubble skeleton" style="width:58%;height:78px"></div></div>
        <div class="wa-row in"><div class="wa-bubble skeleton" style="width:66%;height:64px"></div></div>
        <div class="wa-row in"><div class="wa-bubble skeleton" style="width:44%;height:52px"></div></div>
      </section>
    </div>`;
  }
  const recipients = thread.recipients || [];
  const one = state.activeSeller || (recipients.length === 1 ? recipients[0].seller_id : "");
  const group = !one;
  const messages = (thread.messages || []).filter(
    (message) => !one || message.seller_id === one || (message.deliveries || []).some((item) => item.seller_id === one),
  );
  const offers = (thread.offers || []).filter((item) => item.total_price != null && (!one || item.seller_id === one));
  const best = [...offers].sort((a, b) => a.total_price - b.total_price)[0];
  const title = one ? sellerName(thread, one) : thread.need || thread.original_text || "المحادثة";
  const status = one
    ? thread.need || thread.original_text || ""
    : recipients.map((item) => item.seller_name).join("، ");
  const backAction = state.activeSeller && recipients.length > 1 ? "all-sellers" : "requests";
  const avatar = one
    ? `<span class="wa-avatar" style="background:${sellerColor(thread, one)}">${initial(title)}</span>`
    : `<span class="wa-avatar group">${formatCount(recipients.length)}</span>`;
  const notice = group
    ? `رسالتك توصل لكل الجهات (${formatCount(recipients.length)}). «ردّ» أو @الاسم توصل له بس.`
    : `محادثتك مع ${sellerName(thread, one)}. رسايلك توصل له بس.`;
  const target = state.replyTo;
  // Who this message goes to: everyone ticked by default; untick anyone before sending.
  const pickable = group && !target;
  if (pickable && (!state.picked || state.pickedFor !== thread.id)) {
    state.picked = new Set(recipients.map((item) => item.seller_id));
    state.pickedFor = thread.id;
  }
  const picked = pickable ? recipients.filter((item) => state.picked.has(item.seller_id)) : [];
  const open = state.pickerOpen !== false;
  const picker = pickable
    ? `<div class="wa-recipients" role="group" aria-label="المستلمين">
        <div class="wa-recipients-head">
          <button type="button" class="wa-recipients-toggle" data-action="toggle-picker" aria-expanded="${open}">
            <span>إلى ${picked.length === recipients.length ? "الكل" : `${formatCount(picked.length)} من ${formatCount(recipients.length)}`}</span>
            <span class="wa-caret${open ? " is-open" : ""}" aria-hidden="true">${icon("chevron", { size: 14 })}</span>
          </button>
          <span class="wa-recipients-actions">
            <button type="button" class="text-btn" data-action="pick-all" ${picked.length === recipients.length ? "disabled" : ""}>تحديد الكل</button>
            <button type="button" class="text-btn" data-action="pick-none" ${picked.length ? "" : "disabled"}>إزالة الكل</button>
          </span>
        </div>
        ${open
          ? `<ul class="wa-recipient-list">${recipients
              .map((item) => {
                const on = state.picked.has(item.seller_id);
                return `<li><button type="button" class="wa-recipient${on ? " is-on" : ""}" style="--seller:${sellerColor(thread, item.seller_id)}" data-action="toggle-recipient" data-seller="${esc(item.seller_id)}" role="checkbox" aria-checked="${on}">
                  <span class="wa-check" aria-hidden="true">${on ? "✓" : ""}</span>
                  <span class="wa-recipient-name"><bdi>${esc(item.seller_name)}</bdi></span>
                </button></li>`;
              })
              .join("")}</ul>`
          : ""}
      </div>`
    : "";
  const files = state.chatFiles.length
    ? `<div class="wa-files">${state.chatFiles
        .map(
          (item, index) => `<div class="wa-file-chip">${item.preview ? `<img src="${item.preview}" alt="">` : `<span class="wa-file-icon">PDF</span>`}<span>${esc(snippet(item.name, 18))}</span><button type="button" data-action="remove-chat-file" data-index="${index}" aria-label="إزالة">✕</button></div>`,
        )
        .join("")}</div>`
    : "";
  const nonePicked = pickable && !picked.length;
  const placeholder = target ? `ردّ على ${target.name}` : !group ? "اكتب رسالة" : picked.length === recipients.length ? "اكتب رسالة للكل" : picked.length ? `اكتب رسالة لـ ${picked.length === 1 ? picked[0].seller_name : `${formatCount(picked.length)} بائعين`}` : "اختر بائع واحد على الأقل";
  const headAvatar = one
    ? `<span class="bar-avatar" style="background:${sellerColor(thread, one)}">${initial(title)}</span>`
    : `<span class="bar-avatar group">${formatCount(recipients.length)}</span>`;
  return `<div class="wa-screen">
    ${appBar({ title, subtitle: status, back: backAction, avatar: headAvatar })}
    ${best ? `<div class="wa-pinned"><span class="wa-pin">${one ? "عرضه" : "أرخص عرض"}</span><strong>${esc(money(best.total_price))}</strong>${one ? "" : `<span>${esc(best.provider_name || sellerName(thread, best.seller_id))}</span>`}</div>` : ""}
    <section class="wa-wall" id="chat-wall">
      <div class="wa-system">${esc(notice)}</div>
      ${waMessages(thread, messages, group) || `<div class="wa-system">بانتظار الرد.</div>`}
      <div class="wa-system subtle">${esc(thread.last_synced_at ? `آخر تحديث ${ago(thread.last_synced_at)}` : "بانتظار أول تحديث")}</div>
    </section>
    <div class="wa-dock">
      ${target ? `<div class="wa-replying" style="--who:${sellerColor(state.thread, target.sellerId)}"><div><strong>${esc(target.name)}</strong><span>${esc(snippet(target.body, 60))}</span></div><button type="button" data-action="cancel-reply" aria-label="إلغاء">✕</button></div>` : ""}
      ${state.notice ? `<div class="wa-notice" role="alert">${esc(state.notice)}<button type="button" data-action="clear-notice" aria-label="إغلاق">✕</button></div>` : ""}
      ${picker}
      ${files}
      <form class="wa-compose" id="user-reply">
        <div class="wa-field">
          <input name="body" placeholder="${esc(placeholder)}" autocomplete="off" ${nonePicked ? "disabled" : ""}>
          <label class="wa-attach" aria-label="أرفق صورة أو ملف PDF">${icon("paperclip")}<input type="file" accept="image/*,application/pdf" multiple data-action="chat-files"></label>
        </div>
        <button class="wa-send" type="submit" aria-label="إرسال" ${nonePicked || state.sending ? "disabled" : ""}>${state.sending ? '<span class="wa-spinner" aria-hidden="true"></span>' : icon("send", { size: 20 })}</button>
      </form>
    </div>
  </div>`;
}

function renderSeller() {
  const seller = state.seller;
  if (!seller) return `<section class="page"><h1>${esc(state.notice || "نحمّل المحادثة…")}</h1></section>`;
  const options = seller.recipients || [];
  const partner = options[0]?.seller_name || "الجهة";
  return `${appBar({ title: partner, subtitle: [seller.need || seller.original_text, seller.city ? cityLabel(seller.city) : ""].filter(Boolean).join(" · "), avatar: `<span class="bar-avatar" style="background:${SELLER_COLORS[0]}">${initial(partner)}</span>` })}
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

function subStatusBanner() {
  const status = state.subStatus?.status;
  const sub = state.subStatus?.subscription;
  if (status === "active") {
    const until = sub?.expires_at ? new Date(sub.expires_at).toLocaleDateString("ar-SA-u-ca-gregory") : "";
    return `<div class="sub-banner active">اشتراكك فعّال${until ? ` حتى ${esc(until)}` : ""}</div>`;
  }
  if (status === "expired") return `<div class="sub-banner warn">انتهى اشتراكك. جدده لتستمر بالمزايا المدفوعة.</div>`;
  if (status === "payment_pending") return `<div class="sub-banner warn">هناك عملية دفع قيد المعالجة.</div>`;
  return "";
}

function planPeriod(days) {
  if (days === 30 || days === 31) return "شهر";
  if (days === 365) return "سنة";
  if (days === 7) return "أسبوع";
  return `${formatCount(days)} يوم`;
}

function renderPlanCard(plan) {
  const chosen = state.subActivePlan === plan.code;
  const formId = `moyasar-form-${plan.code}`;
  return `<div class="plan-card${plan.is_placeholder_price ? " is-placeholder" : ""}">
    <h2 class="plan-name">${esc(plan.name_ar)}</h2>
    ${plan.is_placeholder_price ? `<p class="plan-note">سعر تجريبي مؤقت لاختبار الدفع - ليس السعر النهائي.</p>` : ""}
    <div class="plan-price"><strong>${esc(money(plan.price_amount / 100))}</strong><span>/ ${esc(planPeriod(plan.duration_days))}</span></div>
    <ul class="plan-features">${(plan.features || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ul>
    ${
      chosen
        ? `<div class="pay-actions" id="pay-actions" data-plan="${esc(plan.code)}">
            ${state.subBusy ? `<p class="lede">نجهّز الدفع…</p>` : `<div id="${formId}"></div><div id="applepay-slot-${esc(plan.code)}"></div>`}
          </div>
          ${state.subError ? `<p class="pay-error">${esc(state.subError)}</p><button class="ghost-btn block" type="button" data-action="retry-payment">حاول مرة ثانية</button>` : ""}`
        : `<button class="primary block" type="button" data-action="choose-plan" data-plan="${esc(plan.code)}">اشترك بهذه الخطة</button>`
    }
  </div>`;
}

function renderSubscribe() {
  const active = state.subStatus?.status === "active";
  return `${appBar({ title: "الاشتراك" })}
  <section class="page tight subscribe-page">
    <div class="section-head"><h1>اشترك في فرق تسعير</h1><p class="lede">افتح كل المزايا المدفوعة بخطة واحدة بسيطة.</p></div>
    ${subStatusBanner()}
    ${!active && !state.subActivePlan && state.subError ? `<p class="pay-error">${esc(state.subError)}</p>` : ""}
    ${active ? "" : state.subPlans.length ? state.subPlans.map(renderPlanCard).join("") : `<p class="lede">لا توجد خطط متاحة حالياً.</p>`}
  </section>
  ${tabBar("subscribe")}`;
}

let lastView = "";
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
    subscribe: renderSubscribe,
    auth: renderAuth,
    "city-ask": renderCityAsk,
  }[state.view] || renderHome;
  const focused = document.activeElement?.id;
  const keepScroll = state.view === "flow" && lastView === "flow" ? window.scrollY : null;
  const nearBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 120;
  const stick = state.view === "thread" && (state.stickChat || nearBottom);
  app.innerHTML = shell(view());
  if (state.view !== lastView) {
    app.firstElementChild?.classList.add("view-enter");
    lastView = state.view;
  }
  bindImages(app);
  bindGallery(app);
  bindCounter(app);
  if (state.view === "flow" && state.seenCards) for (const result of state.results) state.seenCards.add(resultKey(result));
  if (focused) document.getElementById(focused)?.focus();
  if (keepScroll) window.scrollTo(0, keepScroll);
  if (stick) {
    state.stickChat = false;
    window.scrollTo(0, document.documentElement.scrollHeight);
  }
  if (state.view === "thread" && state.thread?.id) poll = setInterval(() => loadThread(state.thread.id, true).catch(() => {}), 4000);
  if (state.view === "seller" && state.sellerToken) poll = setInterval(() => loadSeller(state.sellerToken, true), 4000);
  if (state.view === "subscribe" && state.subStatus?.status !== "active" && state.subActivePlan && state.subMountedPlan !== state.subActivePlan && !state.subMountFailed) mountPayment(state.subActivePlan);
}

// «٢٠ جهة مطابقة» counts up to the new number instead of jumping.
function bindCounter(root) {
  const node = root.querySelector("[data-count]");
  if (!node) return;
  const target = Number(node.dataset.count || 0);
  const from = Number(state.shownCount || 0);
  if (from === target) return;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    state.shownCount = target;
    node.textContent = `${formatCount(target)} جهة مطابقة`;
    return;
  }
  const started = performance.now();
  const step = (now) => {
    const ratio = Math.min(1, (now - started) / 200);
    const value = Math.round(from + (target - from) * ratio);
    node.textContent = `${formatCount(value)} جهة مطابقة`;
    state.shownCount = value;
    if (ratio < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// The dots under the detail gallery follow the photo in view.
function bindGallery(root) {
  const gallery = root.querySelector("#gallery");
  const dots = root.querySelector("#gallery-dots");
  if (!gallery || !dots) return;
  const update = () => {
    const frames = [...gallery.children];
    const middle = gallery.scrollLeft + gallery.clientWidth / 2;
    let current = 0;
    frames.forEach((frame, index) => {
      if (Math.abs(frame.offsetLeft + frame.clientWidth / 2 - middle) < Math.abs(frames[current].offsetLeft + frames[current].clientWidth / 2 - middle)) current = index;
    });
    [...dots.children].forEach((dot, index) => dot.classList.toggle("is-on", index === current));
  };
  gallery.addEventListener("scroll", () => requestAnimationFrame(update), { passive: true });
  update();
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

async function runSearch(text, city = "") {
  const query = (text || "").trim();
  if (!query) return;
  state.query = query;
  state.city = city || cityInText(query);
  // Without a city the search is nationwide; ask first, then search inside that city.
  if (!state.city && state.cities.length) {
    state.view = "city-ask";
    state.results = [];
    state.intent = null;
    state.searching = false;
    render();
    return;
  }
  const asked = state.city && !cityInText(query) ? `${query} ${cityLabel(state.city)}` : query;
  state.view = "flow";
  state.partial = true;
  state.searching = true;
  state.seenCards = new Set();
  state.shownCount = 0;
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
    const response = await fetch("/v1/search/stream", { method: "POST", headers, body: JSON.stringify({ query: asked }) });
    if (!response.ok || !response.body) throw new Error("stream");
    await readNdjson(response, (event) => {
      if (event.type === "intent") {
        state.intent = event.intent;
        state.clarification = event.clarification_question || "";
        if (state.clarification) state.searching = false;
        if (!state.results.length && state.partial) state.notice = "نفهم طلبك…";
      } else if (event.type === "status") {
        state.searchState = event.state;
        state.partial = true;
        state.notice = state.results.length ? "لقينا خيارات مناسبة، وقاعدين ندور لك على أكثر." : "ندور لك…";
      } else if (event.type === "results") {
        if ((event.results || []).length) state.searching = false;
        state.results = event.results || [];
        state.partial = true;
        state.searchState = event.state;
        if (state.results.length) state.notice = "لقينا خيارات مناسبة، وقاعدين ندور لك على أكثر.";
      } else if (event.type === "done") {
        state.searching = false;
        applyDone(event);
      }
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
      if (item.file.type === "application/pdf") form.append("file", item.file);
      else {
        const photo = await preparePhoto(item.file).catch(() => null);
        if (!photo) continue;
        form.append("file", photo.blob, photo.name);
      }
      await api(`/v1/requests/${created.id}/attachments`, { method: "POST", form }).catch(() => {});
    }
    state.busy = false;
    state.files = [];
    state.note = "";
    state.notice = "";
    state.selected.clear();
    state.thread = created;
    threadCache.set(created.id, created);
    state.replyTo = null;
    // One seller: straight into the chat with him. Several: the item's group chat.
    state.activeSeller = created.recipients?.length === 1 ? created.recipients[0].seller_id : "";
    state.stickChat = true;
    state.view = "thread";
    history.pushState({}, "", "/");
    await loadThread(created.id);
  } catch (_error) {
    state.notice = "ما قدرنا نرسل الطلب. جرّب مرة ثانية.";
    state.busy = false;
    state.view = "review";
    render();
  }
}

// Screens switch at once: what we already have (or a placeholder) shows while the server answers.
const threadCache = new Map();

async function sendChatMessage(body) {
  const thread = state.thread;
  const recipients = thread.recipients || [];
  const one = state.activeSeller || (recipients.length === 1 ? recipients[0].seller_id : "");
  const json = { body, need: thread.need || null };
  if (state.replyTo) json.reply_to = state.replyTo.id;
  else if (one) json.seller_id = one;
  else {
    const picked = recipients.filter((item) => state.picked?.has(item.seller_id)).map((item) => item.seller_id);
    if (!picked.length) return;
    json.seller_ids = picked;
  }
  state.sending = true;
  render();
  try {
    if (state.chatFiles.length) {
      json.media_ids = [];
      for (const item of state.chatFiles) {
        const form = new FormData();
        form.append("file", item.blob, item.name);
        if (item.width) form.append("width", String(item.width));
        if (item.height) form.append("height", String(item.height));
        json.media_ids.push((await api(`/v1/requests/${thread.id}/files`, { method: "POST", form })).file_id);
      }
    }
    await api(`/v1/requests/${thread.id}/messages`, { method: "POST", json });
    state.chatFiles.forEach((item) => item.preview && URL.revokeObjectURL(item.preview));
    state.chatFiles = [];
    state.replyTo = null;
    state.stickChat = true;
    state.sending = false;
    await loadThread(thread.id, true);
    render();
  } catch (error) {
    state.sending = false;
    state.notice = error.status === 413 ? "الملف أكبر من ٤ ميجا" : error.status === 415 ? "نرسل صور وملفات PDF فقط" : "ما انرسلت الرسالة، جرّب مرة ثانية";
    render();
  }
}

// Photos leave the phone as JPEG (what Haraj's chat takes), at most 1600 px and about 1 MB.
async function preparePhoto(file) {
  const bitmap = await createImageBitmap(file).catch(() => null);
  if (!bitmap) throw new Error("unreadable image");
  const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
  const width = Math.round(bitmap.width * scale);
  const height = Math.round(bitmap.height * scale);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  canvas.getContext("2d").drawImage(bitmap, 0, 0, width, height);
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.82));
  return { blob, width, height, name: `${(file.name || "photo").replace(/\.[^.]+$/, "")}.jpg`, type: "image/jpeg", preview: URL.createObjectURL(blob) };
}

async function prepareChatFiles(files) {
  for (const file of files) {
    if (state.chatFiles.length >= 6) break;
    try {
      if (file.type === "application/pdf") {
        if (file.size > 4 * 1024 * 1024) throw new Error("too large");
        state.chatFiles.push({ blob: file, name: file.name || "ملف.pdf", type: "application/pdf" });
      } else if (file.type.startsWith("image/") || /\.(heic|heif)$/i.test(file.name || "")) {
        state.chatFiles.push(await preparePhoto(file));
      }
    } catch (_error) {
      state.notice = "ما قدرنا نرفق هذا الملف";
    }
  }
  render();
}

async function loadRequests() {
  state.view = "requests";
  state.requestsLoading = !state.requestsLoaded;
  render();
  await ensureAuth();
  state.requests = (await api("/v1/requests")).requests || [];
  state.requestsLoaded = true;
  state.requestsLoading = false;
  setUnread(state.requests);
  if (state.view === "requests") render();
}

async function loadThread(id, silent = false) {
  if (!silent) {
    state.view = "thread";
    state.thread = threadCache.get(id) || null;
    state.pendingThread = id;
    render();
  }
  await ensureAuth();
  const thread = await api(`/v1/requests/${id}`, { quiet: silent });
  threadCache.set(id, thread);
  if (state.view !== "thread" || (state.pendingThread && state.pendingThread !== id)) return;
  const previous = JSON.stringify(state.thread?.messages || []);
  const first = !state.thread;
  state.thread = thread;
  if (first) state.stickChat = true;
  if (!silent || first || previous !== JSON.stringify(thread.messages || [])) render();
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

let moyasarScriptPromise = null;
function loadMoyasarSdk() {
  if (moyasarScriptPromise) return moyasarScriptPromise;
  moyasarScriptPromise = new Promise((resolve, reject) => {
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://cdn.moyasar.com/mpf/1.15.0/moyasar.css";
    document.head.appendChild(css);
    const script = document.createElement("script");
    script.src = "https://cdn.moyasar.com/mpf/1.15.0/moyasar.js";
    script.onload = () => resolve(window.Moyasar);
    script.onerror = () => {
      // Don't cache a rejected promise: a transient CDN hiccup would
      // otherwise fail every future attempt for the rest of the session,
      // even after the user hits "retry".
      moyasarScriptPromise = null;
      reject(new Error("moyasar-sdk-failed"));
    };
    document.head.appendChild(script);
  });
  return moyasarScriptPromise;
}

async function loadSubscribe() {
  await ensureAuth();
  state.view = "subscribe";
  state.subError = "";
  state.subActivePlan = "";
  state.subMountedPlan = "";
  state.subMountFailed = false;
  render();
  try {
    const [plans, me] = await Promise.all([api("/v1/subscriptions/plans", { skipAuth: true }), api("/v1/subscriptions/me")]);
    state.subPlans = plans.plans || [];
    state.subStatus = me;
    if (state.subPlans.length === 1) state.subActivePlan = state.subPlans[0].code;
  } catch (_error) {
    state.subError = "ما قدرنا نجيب بيانات الاشتراك. جرّب مرة ثانية.";
  }
  render();
}

async function handleSubscribeCallback() {
  // mada/3DS cards leave the app entirely and Moyasar redirects the whole
  // page back to callback_url (?id=<moyasar_payment_id>...) instead of
  // firing Moyasar.js's on_completed - without this, that flow silently
  // never verifies and the user lands looking subscribed to nothing.
  //
  // This deliberately does NOT reuse loadSubscribe(): that sets
  // subActivePlan for a single-plan catalog, and render()'s auto-mount hook
  // would then start a *fresh* checkout concurrently with the verify call
  // below, racing over the same localStorage pending-payment entry.
  const params = new URLSearchParams(location.search);
  const moyasarPaymentId = params.get("id") || params.get("payment_id");
  let pending = null;
  try {
    pending = JSON.parse(localStorage.getItem("farq.pendingPayment") || "null");
  } catch (_error) {
    pending = null;
  }
  history.replaceState({}, "", "/subscribe");

  await ensureAuth();
  state.view = "subscribe";
  state.subError = "";
  state.subActivePlan = "";
  state.subMountedPlan = "";
  state.subMountFailed = false;
  render();

  try {
    const [plans, me] = await Promise.all([api("/v1/subscriptions/plans", { skipAuth: true }), api("/v1/subscriptions/me")]);
    state.subPlans = plans.plans || [];
    state.subStatus = me;
  } catch (_error) {
    state.subError = "ما قدرنا نجيب بيانات الاشتراك. جرّب مرة ثانية.";
  }

  if (moyasarPaymentId && pending?.payment_id) {
    try {
      const result = await api("/v1/subscriptions/verify", {
        method: "POST",
        json: { payment_id: pending.payment_id, moyasar_payment_id: moyasarPaymentId },
      });
      localStorage.removeItem("farq.pendingPayment");
      state.subStatus = { status: result.subscription?.status === "active" ? "active" : "none", subscription: result.subscription };
      state.subError = result.activated ? "" : "الدفع لم يكتمل، تحققنا منه ولم يُفعَّل الاشتراك.";
    } catch (_error) {
      state.subError = "ما قدرنا نتحقق من الدفع. جرّب مرة ثانية من صفحة الاشتراك.";
    }
  }

  // Only auto-select/auto-mount the single plan when this load never
  // attempted a verification (someone just landed on /subscribe/callback
  // directly). When a verify was attempted, auto-mounting here would
  // immediately overwrite the success/failure message above with a fresh
  // checkout attempt - leave the plan list showing an explicit "try again"
  // button instead.
  const verifyAttempted = Boolean(moyasarPaymentId && pending?.payment_id);
  if (!verifyAttempted && state.subStatus?.status !== "active" && state.subPlans.length === 1) {
    state.subActivePlan = state.subPlans[0].code;
  }
  render();
}

async function mountPayment(planCode) {
  const plan = state.subPlans.find((item) => item.code === planCode);
  if (!plan || state.subMountedPlan === planCode) return;
  state.subMountedPlan = planCode;
  state.subBusy = true;
  state.subError = "";
  render();
  try {
    const checkout = await api("/v1/subscriptions/checkout", { method: "POST", json: { plan: plan.code } });
    // mada/3DS cards redirect the whole page away and back to callback_url
    // instead of firing on_completed - stash our payment_id so the page
    // that reloads at /subscribe/callback can still verify it.
    localStorage.setItem("farq.pendingPayment", JSON.stringify({ payment_id: checkout.payment_id, plan: plan.code }));
    await loadMoyasarSdk();
    state.subBusy = false;
    render();
    const formId = `moyasar-form-${plan.code}`;
    const mount = document.getElementById(formId);
    if (!mount || !window.Moyasar) throw new Error("moyasar-unavailable");
    const methods = ["creditcard"];
    if (window.ApplePaySession && ApplePaySession.canMakePayments && ApplePaySession.canMakePayments()) methods.push("applepay");
    window.Moyasar.init({
      element: `#${formId}`,
      amount: checkout.amount,
      currency: checkout.currency,
      description: `${plan.name_ar} - فرق تسعير`,
      publishable_api_key: checkout.publishable_key,
      callback_url: checkout.callback_url,
      metadata: checkout.metadata,
      methods,
      apple_pay: { label: "فرق تسعير", country: "SA" },
      language: "ar",
      on_completed: async (payment) => {
        try {
          const result = await api("/v1/subscriptions/verify", {
            method: "POST",
            json: { payment_id: checkout.payment_id, moyasar_payment_id: payment.id },
          });
          localStorage.removeItem("farq.pendingPayment");
          state.subStatus = { status: result.subscription?.status === "active" ? "active" : "none", subscription: result.subscription };
          state.subError = "";
        } catch (_error) {
          state.subError = "الدفع لم يكتمل، تحققنا منه ولم يُفعَّل الاشتراك. جرّب مرة ثانية.";
        }
        render();
      },
    });
  } catch (_error) {
    state.subMountFailed = true;
    state.subBusy = false;
    state.subError = "الدفع غير متاح حالياً. حاول لاحقاً.";
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
  } else if (form.id === "auth-form") {
    event.preventDefault();
    submitAuth(form);
  } else if (form.id === "user-reply") {
    event.preventDefault();
    const body = String(new FormData(form).get("body") || "").trim();
    if ((!body && !state.chatFiles.length) || !state.thread || state.sending) return;
    sendChatMessage(body);
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
  if (event.target.dataset.action === "chat-files") {
    const files = [...(event.target.files || [])];
    event.target.value = "";
    if (files.length) prepareChatFiles(files);
    return;
  }
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
  else if (action === "subscribe") loadSubscribe().catch(() => {});
  else if (action === "choose-plan") {
    state.subActivePlan = target.dataset.plan;
    state.subMountedPlan = "";
    state.subMountFailed = false;
    state.subError = "";
    render();
  } else if (action === "retry-payment") {
    state.subMountedPlan = "";
    state.subMountFailed = false;
    state.subError = "";
    render();
  }
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
  } else if (action === "search-city") {
    runSearch(state.query, target.dataset.city || "");
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
  else if (action === "clear-notice") {
    state.notice = "";
    render();
  } else if (action === "toggle-password") {
    const field = document.querySelector("#auth-password");
    const keep = field?.value || "";
    state.showPassword = !state.showPassword;
    render();
    const next = document.querySelector("#auth-password");
    if (next) {
      next.value = keep;
      next.focus();
      next.setSelectionRange(keep.length, keep.length);
    }
  } else if (action === "auth-mode") {
    state.authMode = state.authMode === "register" ? "login" : "register";
    state.authError = "";
    render();
  } else if (action === "sign-out") {
    api("/v1/auth/logout", { method: "POST" }).catch(() => {});
    signOutLocally();
    state.returnView = "home";
    state.authMode = "login";
    requireSignIn();
  } else if (action === "toggle-picker") {
    state.pickerOpen = state.pickerOpen === false;
    render();
  } else if (action === "pick-all") {
    state.picked = new Set((state.thread?.recipients || []).map((item) => item.seller_id));
    render();
  } else if (action === "pick-none") {
    state.picked = new Set();
    render();
  } else if (action === "toggle-recipient") {
    const id = target.dataset.seller;
    if (!state.picked) return;
    if (state.picked.has(id)) state.picked.delete(id);
    else state.picked.add(id);
    render();
  } else if (action === "remove-chat-file") {
    const [removed] = state.chatFiles.splice(Number(target.dataset.index), 1);
    if (removed?.preview) URL.revokeObjectURL(removed.preview);
    render();
  } else if (action === "enable-notify") {
    enableNotifications(true)
      .catch(() => {
        state.pushState = "off";
      })
      .finally(() => render());
  } else if (action === "dismiss-notify") {
    state.pushDismissed = true;
    try {
      localStorage.setItem("farq.pushDismissed", "1");
    } catch (_error) {}
    render();
  } else if (action === "thread") {
    state.activeSeller = "";
    state.replyTo = null;
    state.picked = null;
    state.chatFiles = [];
    state.stickChat = true;
    loadThread(target.dataset.id).catch(() => {});
  } else if (action === "seller-filter") {
    state.activeSeller = target.dataset.seller || "";
    state.replyTo = null;
    state.stickChat = true;
    render();
  } else if (action === "all-sellers") {
    state.activeSeller = "";
    state.stickChat = true;
    render();
  } else if (action === "reply") {
    const message = (state.thread?.messages || []).find((item) => item.id === target.dataset.message);
    if (!message) return;
    state.replyTo = { id: message.id, sellerId: message.seller_id, name: sellerName(state.thread, message.seller_id), body: message.body };
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

const openRequest = new URLSearchParams(location.search).get("r");
if (!sellerRoute && !state.token) {
  state.view = "auth";
  if (openRequest) state.returnView = "requests";
} else if (!sellerRoute) {
  api("/v1/auth/me", { quiet: true })
    .then((account) => {
      state.account = account;
      if (state.view === "requests") render();
    })
    .catch(() => {});
}
if (sellerRoute) loadSeller(decodeURIComponent(sellerRoute[1]));
else if (!state.token) render();
else if (subscribeCallback) handleSubscribeCallback().catch(() => render());
else if (openRequest) {
  // Opened from a notification: straight into that conversation.
  history.replaceState({}, "", "/");
  state.stickChat = true;
  loadThread(openRequest).catch(() => render());
} else render();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker
    .register("/sw.js")
    .then(() => (pushSupported && Notification.permission === "granted" ? enableNotifications() : null))
    .catch(() => {});
}
setInterval(refreshUnread, 30000);
refreshUnread();

api("/v1/cities", { skipAuth: true })
  .then((data) => {
    if (data.cities?.length) state.cities = data.cities;
    if (state.view === "flow" || state.view === "review") render();
  })
  .catch(() => {});
