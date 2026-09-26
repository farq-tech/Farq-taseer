const state = {
  view: "home",
  // What the customer typed in the composer. Nothing downstream writes over it: the request
  // sent to the suppliers carries these words, not the shortened search phrase.
  query: "",
  originalText: "",
  // The phrase the last search actually ran (built from the M02 items), kept apart from the above.
  searchText: "",
  needFilter: "",
  resultNeeds: new Map(),
  streamNeeds: [],
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
  compareOpen: false,
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
  // The supplier app is a second surface on the same bundle. Its session is its own: one
  // device can be a customer and a supplier at once, and signing out of one is not the other.
  supplierToken: localStorage.getItem("farq.supplierToken") || "",
  supplier: null,
  supplierRequests: [],
  supplierCounts: null,
  supplierFilter: "all",
  supplierCatalog: [],
  supplierPicked: [],
  supplierSuggested: [],
  supplierCapabilities: [],
  supplierInbox: [],
  supplierUnread: 0,
  supplierTab: "requests",
  supplierDesc: "",
  supplierCaret: null,
  supplierError: "",
  supplierMode: "join",
  supplierActivity: "both",
  sellerFrom: "",
  verify: null,
  verifyState: "",
  shareOpen: false,
  sharePlace: false,
  shareError: "",
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
const subscribeCallback = location.pathname === "/subscribe/callback";

// Decided once, at boot. The app rewrites the path as the customer moves, which drops
// ?embed=1 from the URL; re-reading it later would quietly turn the embed off mid-journey
// and put Taseer's own sign-in screen back in front of a Farq customer.
let farqEmbed = null;
function isFarqEmbed() {
  if (farqEmbed === null) {
    try {
      farqEmbed = new URLSearchParams(location.search).get("embed") === "1" || window.self !== window.top;
    } catch (_error) {
      farqEmbed = false;
    }
  }
  return farqEmbed;
}

if (isFarqEmbed()) document.documentElement.classList.add("fq-embed");

// Inside Farq the customer has already signed in, to Farq. Taseer must not put a second
// email-and-password door in front of him, so nothing here ever opens Taseer's own auth
// screen while embedded: the parent is asked to open Farq's sign-in instead, and it decides
// what to do. Search stays public either way, so browsing and results need no token at all.
function askFarqToSignIn(reason) {
  try {
    window.parent.postMessage({ source: "taseer", type: "farq-auth-required", reason }, "*");
  } catch (_error) {
    /* a parent that cannot be reached is not a reason to show our own form */
  }
}

/** True when the embed handled it, so the caller must stop. */
function farqHandlesSignIn(reason) {
  if (!isFarqEmbed()) return false;
  askFarqToSignIn(reason);
  return true;
}

// ---------------------------------------------------------------------------
// One account: the Farq account. Farq keeps its Supabase session in a cookie written for
// `.farq.sa`, so every *.farq.sa host - this one included, framed or not - can read it. The
// access token in it is handed to our own server, which asks Farq's Supabase whose it is and
// answers with a Taseer session for that same person. Inside Farq's frame the parent also
// posts the session over (the iOS app shares no cookie), and is asked for it at boot.
// ---------------------------------------------------------------------------
const FARQ_SITE = "https://www.farq.sa";
// farq.sa keeps the session under the first key (its own auth API); a Supabase-backed Farq
// under the second. Either is the same person.
const FARQ_SESSION_COOKIES = ["farq_local_auth_session_v1", "farq-auth.2"];
const FARQ_SESSION_COOKIE = FARQ_SESSION_COOKIES[1];
const FARQ_PARENT_ORIGINS = new Set([
  "https://farq.sa",
  "https://www.farq.sa",
  "capacitor://localhost",
  "https://localhost",
  "http://localhost:5173",
  "http://127.0.0.1:5173",
]);

/** Farq's Supabase access token from the shared cookie, or "". */
function farqSessionToken() {
  try {
    const jar = new Map(document.cookie.split(";").map((part) => part.trim()).filter(Boolean).map((part) => {
      const at = part.indexOf("=");
      return [part.slice(0, at), part.slice(at + 1)];
    }));
    for (const key of FARQ_SESSION_COOKIES) {
      const chunks = [];
      for (let i = 0; i < 12; i += 1) {
        const chunk = jar.get(`${key}.c${i}`);
        if (chunk === undefined) break;
        chunks.push(chunk);
      }
      if (!chunks.length) continue;
      const session = JSON.parse(decodeURIComponent(chunks.join("")));
      if (typeof session?.access_token === "string" && session.access_token) return session.access_token;
    }
    return "";
  } catch (_error) {
    return "";
  }
}

function clearFarqSession() {
  try {
    const domain = location.hostname.endsWith("farq.sa") ? "; Domain=.farq.sa" : "";
    for (const key of FARQ_SESSION_COOKIES) {
      for (let i = 0; i < 12; i += 1) {
        document.cookie = `${key}.c${i}=; Path=/; Max-Age=0; SameSite=Lax${domain}`;
        document.cookie = `${key}.c${i}=; Path=/; Max-Age=0; SameSite=Lax`;
      }
    }
  } catch (_error) {}
}

function keepSession(token, account) {
  clearTimeout(farqWait);
  state.farqFallback = false;
  state.token = token;
  state.account = account;
  try {
    localStorage.setItem("farq.token", token);
  } catch (_error) {}
}

let farqSignIn = null;
/** Turns a Farq access token into a Taseer session. Resolves true when signed in. */
function signInWithFarq(accessToken) {
  if (!accessToken) return Promise.resolve(false);
  if (farqSignIn) return farqSignIn;
  farqSignIn = api("/v1/auth/farq", { method: "POST", json: { access_token: accessToken }, skipAuth: true, quiet: true })
    .then((result) => {
      keepSession(result.token, { name: result.name, email: result.email });
      state.authError = "";
      state.farqLink = null;
      loadSubStatus().catch(() => {});
      loadVerification().catch(() => {});
      refreshUnread();
      return true;
    })
    .catch((error) => {
      if (error?.status === 409) {
        // A Taseer account made with a password carries this email. Its owner proves it once
        // (the password) with the Farq session present, and the two become one account.
        state.farqLink = { token: accessToken };
        state.legacyAuth = true;
        state.authMode = "login";
        state.authError = "";
        if (state.view !== "auth") state.returnView = RETURN_TO[state.view] || state.view;
        state.view = "auth";
        render();
      }
      return false;
    })
    .finally(() => {
      farqSignIn = null;
    });
  return farqSignIn;
}

/** After a sign-in that interrupted something, pick that thing up again. */
function resumeAfterSignIn() {
  const pending = state.pendingAuthAction;
  state.pendingAuthAction = "";
  if (pending === "send" && state.selected.size) {
    sendRequest();
    return;
  }
  if (state.returnRoute) {
    const route = state.returnRoute;
    state.returnRoute = "";
    applyRoute(route, { pop: true });
    return;
  }
  render();
}

function askFarqForSession() {
  try {
    window.parent.postMessage({ source: "taseer", type: "farq-session-request" }, "*");
  } catch (_error) {}
}

window.addEventListener("message", (event) => {
  if (!isFarqEmbed() || !FARQ_PARENT_ORIGINS.has(event.origin)) return;
  const data = event.data;
  if (!data || typeof data !== "object" || data.source !== "farq") return;
  if (data.type === "farq-session" && typeof data.access_token === "string") {
    if (state.token) return;
    signInWithFarq(data.access_token).then((ok) => ok && resumeAfterSignIn());
  } else if (data.type === "farq-signed-out") {
    if (!state.token) return;
    signOutLocally();
    if (state.view === "requests" || state.view === "thread" || state.view === "compare" || state.view === "account") state.view = "home";
    render();
  }
});

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
}

// Storage can be missing or refuse (private windows, blocked site data); the app works without it.
function storedGet(key, area = "local") {
  try {
    return (area === "session" ? sessionStorage : localStorage).getItem(key);
  } catch (_error) {
    return null;
  }
}

function storedSet(key, value, area = "local") {
  try {
    const store = area === "session" ? sessionStorage : localStorage;
    if (value == null) store.removeItem(key);
    else store.setItem(key, value);
  } catch (_error) {}
}

// Every number in the frames — prices, times, counts — is printed in Western digits.
function formatCount(value) {
  return new Intl.NumberFormat("ar-SA-u-nu-latn").format(value);
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
  return `${new Intl.NumberFormat("ar-SA-u-nu-latn", { maximumFractionDigits: 0 }).format(amount)} ر.س`;
}

// Arabic counts: one, two, three to ten, and eleven on. `forms` = [one, two, few, many];
// the few/many forms follow the number.
function plural(count, [one, two, few, many]) {
  const n = Number(count) || 0;
  if (n === 1) return one;
  if (n === 2) return two;
  if (n >= 3 && n <= 10) return `${formatCount(n)} ${few}`;
  return `${formatCount(n)} ${many}`;
}

const suppliers = (n) => plural(n, ["مورد واحد", "موردين", "موردين", "مورد"]);
const offersCount = (n) => plural(n, ["عرض واحد", "عرضين", "عروض", "عرض"]);

// An ad's age, from the date Haraj gives it. Old ads still show, but say so.
const OLD_AD_DAYS = 90;
function adAge(ad) {
  const then = Date.parse(ad?.posted_at || "");
  if (Number.isNaN(then)) return null;
  const days = Math.max(0, Math.floor((Date.now() - then) / 86400000));
  let label;
  if (days < 1) label = "اليوم";
  else if (days === 1) label = "أمس";
  else if (days < 30) label = `قبل ${plural(days, ["يوم", "يومين", "أيام", "يوم"])}`;
  else if (days < 365) label = `قبل ${plural(Math.round(days / 30), ["شهر", "شهرين", "أشهر", "شهر"])}`;
  else label = `قبل ${plural(Math.floor(days / 365), ["سنة", "سنتين", "سنوات", "سنة"])}`;
  return { days, label, old: days > OLD_AD_DAYS };
}

// A thin bar at the top while the app is waiting on the server (background polling stays quiet).
let busyRequests = 0;
function setBusy(delta) {
  busyRequests = Math.max(0, busyRequests + delta);
  document.documentElement.classList.toggle("is-busy", busyRequests > 0);
}

// Reads that several screens ask for at once (the plans, the subscription, the account) share
// one trip to the server: a GET already on its way is joined, and the plan list — which does
// not change within a visit — is kept for a few minutes. Any write to a path clears its reads.
const inflight = new Map();
const readCache = new Map();
const CACHE_FOR = { "/v1/subscriptions/plans": 5 * 60 * 1000 };

function apiRead(path, options) {
  const key = `${options.skipAuth ? "" : state.token}|${path}`;
  const kept = readCache.get(key);
  if (kept && kept.until > Date.now()) return Promise.resolve(kept.value);
  if (inflight.has(key)) return inflight.get(key);
  const pending = apiSend(path, options)
    .then((value) => {
      if (CACHE_FOR[path]) readCache.set(key, { value, until: Date.now() + CACHE_FOR[path] });
      return value;
    })
    .finally(() => inflight.delete(key));
  inflight.set(key, pending);
  return pending;
}

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  if (method === "GET" && !options.form && options.json === undefined) return apiRead(path, options);
  const area = path.split("/").slice(0, 3).join("/");
  for (const key of [...readCache.keys()]) if (key.split("|")[1].startsWith(area)) readCache.delete(key);
  return apiSend(path, options);
}

async function apiSend(path, options = {}) {
  if (options.quiet) return request(path, options);
  setBusy(1);
  try {
    return await request(path, options);
  } finally {
    setBusy(-1);
  }
}

async function request(path, { method = "GET", json, form, skipAuth = false, quiet = false, signal, asSupplier = false, headers: extra } = {}) {
  const headers = { ...(extra || {}) };
  if (asSupplier) {
    if (state.supplierToken) headers.Authorization = `Bearer ${state.supplierToken}`;
  } else if (state.token && !skipAuth) headers.Authorization = `Bearer ${state.token}`;
  let body;
  if (json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(json);
  } else if (form) body = form;
  const response = await fetch(path, { method, headers, body, signal });
  if (response.status === 401 && asSupplier) {
    // The supplier's session ended: drop it and ask him to sign in again. The customer's
    // session on the same device is untouched.
    supplierSignOutLocally();
    state.supplierMode = "signin";
    state.supplierError = "انتهت جلستك، سجّل دخولك من جديد.";
    state.view = "supplier-auth";
    render();
    const error = new Error("supplier sign-in required");
    error.auth = true;
    throw error;
  }
  if (response.status === 401 && !skipAuth) {
    // A session that ends mid-journey says so, and the journey waits for the sign-in.
    const hadSession = Boolean(state.token);
    signOutLocally();
    requireSignIn(hadSession ? "انتهت جلستك، سجّل دخولك من جديد وبنكمل من نفس المكان." : "");
    const error = new Error("sign-in required");
    error.auth = true;
    throw error;
  }
  if (!response.ok) {
    const error = new Error("request failed");
    error.status = response.status;
    // Limits refused by the server carry {code, message, limit} to show as is.
    error.detail = await response.json().then((data) => data?.detail, () => null);
    throw error;
  }
  const type = response.headers.get("content-type") || "";
  return type.includes("json") ? response.json() : response;
}

// Everyone signs in with a Taseer account before using the app; requests belong to that account.
// The sending screen is a moment, not a place: a sign-in asked for while sending returns to the
// review, where the chosen suppliers are still ticked.
const RETURN_TO = { sending: "review", detail: "flow" };
// What he was trying to do when the account was needed, so Farq can say why it is asking.
const EMBED_REASON = { sending: "send", review: "send", thread: "message", subscribe: "subscribe", plans: "subscribe" };
// Farq was asked for the session this long ago and has not answered: an older Farq app
// that never posts it, or a session that cannot be read here. The customer must not be
// left on a button that does nothing, so Taseer's own form opens inside the frame.
const FARQ_ANSWER_MS = 3000;
let farqWait = 0;
function waitForFarq() {
  clearTimeout(farqWait);
  farqWait = setTimeout(() => {
    if (state.token || state.farqLink || farqSignIn) return;
    state.farqFallback = true;
    state.legacyAuth = true;
    state.authMode = "login";
    state.authError = "";
    if (state.view !== "auth") state.returnView = RETURN_TO[state.view] || state.view;
    state.view = "auth";
    render();
  }, FARQ_ANSWER_MS);
}

function requireSignIn(message = "") {
  if (isFarqEmbed()) {
    // A Farq session may already be here (the shared cookie); use it before asking.
    const farqToken = farqSessionToken();
    if (farqToken && !farqSignIn) {
      signInWithFarq(farqToken).then((ok) => ok && resumeAfterSignIn());
    }
    waitForFarq();
    // Farq owns the sign-in. Ask for it and put him back where he was, with his suppliers
    // still ticked, rather than stranding him on a spinner or on our own form.
    askFarqToSignIn(EMBED_REASON[state.view] || "account");
    const back = RETURN_TO[state.view];
    if (back) {
      state.view = back;
      render();
    }
    return;
  }
  if (state.view !== "auth") state.returnView = RETURN_TO[state.view] || state.view;
  state.view = "auth";
  state.authError = message;
  render();
}

async function ensureAuth() {
  if (state.token) return;
  // The Farq account is the account: a farq.sa session on this device signs him in here.
  if (await signInWithFarq(farqSessionToken())) return;
  requireSignIn();
  const error = new Error("sign-in required");
  error.auth = true;
  throw error;
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

// The evidence carries the folded matching form ("مراقبه"), which no one writes. Show the
// word the way the supplier spelled it in his own advert, so the chip reads as language
// rather than as the machine's internal form.
function asWritten(word, source) {
  const folded = normalizeLoose(word);
  for (const token of String(source || "").split(/[\s،.,()\-—|/\\]+/)) {
    const clean = token.replace(/[^\p{L}\p{N}]/gu, "");
    if (!clean) continue;
    if (normalizeLoose(clean) === folded) return clean;
    // "المراقبة" is the same word he asked for, wearing the article.
    const bare = clean.replace(/^ال/, "");
    if (bare && normalizeLoose(bare) === folded) return bare;
  }
  return word;
}

function matchedWords(result) {
  const source = `${result.ad?.title || ""} ${result.ad?.description || ""}`;
  const seen = new Set();
  const words = [];
  for (const item of result.match_evidence || []) {
    const word = String(item || "").trim();
    if (!word || word.length > 14 || word.split(" ").length > 2) continue;
    const key = normalizeLoose(word);
    if (seen.has(key)) continue;
    seen.add(key);
    words.push(asWritten(word, source));
  }
  return words.slice(0, 3);
}

// What separates one listing from the next, in the supplier's own words. The match chips
// say the same thing on every card; these are the reasons to pick this one over that one.
const OFFER_MARKS = [
  { label: "تركيب", test: /تركيب|تمديد|تاسيس|تأسيس/ },
  { label: "ضمان", test: /ضمان/ },
  { label: "صيانة", test: /صيانه|صيانة/ },
  { label: "توريد", test: /توريد|بيع وتركيب/ },
  { label: "زيارة معاينة", test: /معاينه|معاينة|كشف مجاني/ },
];
function offerMarks(result) {
  const text = `${result.ad?.title || ""} ${result.ad?.description || ""}`;
  return OFFER_MARKS.filter((mark) => mark.test.test(text)).map((mark) => mark.label).slice(0, 3);
}

function normalizeLoose(value) {
  return String(value).replace(/[\u064b-\u0652]/g, "").replace(/[أإآ]/g, "ا").replace(/ة/g, "ه").replace(/ى/g, "ي").trim();
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

// The city screen serves three callers: a request with no city in it (then it carries on to M02),
// the header's city control (then it goes back home), and «جرّب مدينة ثانية» on an empty result
// (then it searches again in the new city).
function renderCityAsk() {
  const mode = state.cityMode || (state.query.trim() ? "request" : "pick");
  const action = mode === "pick" ? "set-city" : mode === "research" ? "research-city" : "search-city";
  const lead = mode === "research" ? "نعيد البحث عن نفس الطلب في المدينة اللي تختارها." : "نختصر البحث على الموردين القريبين منك.";
  const request = mode === "pick" ? "" : state.originalText || state.query;
  return `${fqHead({ title: "المدينة", back: mode === "research" ? "back-results" : "home" })}
  <section class="fq-body">
    <div class="fq-hero"><span class="halo" aria-hidden="true"></span>
      <h1>في أي مدينة؟</h1>
      <p>${esc(lead)}</p></div>
    ${request ? `<div class="fq-card pad"><p class="fq-small fq-muted">طلبك</p><p style="margin:0;font-size:15px;font-weight:600"><bdi>${esc(request)}</bdi></p></div>` : ""}
    <div class="fq-pills" style="gap:10px">${state.cities
      .map((city) => {
        const on = state.city && (state.city === city.value || state.city === city.label);
        return `<button class="fq-chip${on ? " on" : ""}" type="button" data-action="${action}" data-value="${esc(city.label)}" data-city="${esc(city.value)}" aria-pressed="${Boolean(on)}">${esc(city.label)}</button>`;
      })
      .join("")}</div>
  </section>`;
}

// M02_Understanding — node 27:68. One card per need the parser found; each opens M03 to edit.
// The parser's category codes are English slugs; a card shows Arabic or nothing.
function arabicOnly(value) {
  const text = String(value ?? "").trim();
  return /[\u0600-\u06FF]/.test(text) ? text : "";
}

function needLabel(need) {
  return need.name || "بند";
}

// Where a need is, in one line: the district and the city, never the city twice.
function needPlace(need) {
  const city = cityLabel(need.city);
  const district = need.district && need.district !== city && need.district !== need.city ? need.district : "";
  return [district, city].filter(Boolean).join("، ");
}

// When the parser could not read the request, M02 says so instead of dressing the raw text up
// as an item: a failed call offers a retry; a request it did not understand asks for another
// wording; a request it half-understood asks the one question it needs.
function understandProblem() {
  const head = fqHead({ title: "فهم الطلب", back: "home" });
  const typed = state.originalText || state.query;
  const quote = typed ? `<div class="fq-card pad"><p class="fq-small fq-muted">طلبك</p><p style="margin:0;font-size:15px;font-weight:600"><bdi>${esc(typed)}</bdi></p></div>` : "";
  if (state.intentError) {
    return `${head}<section class="fq-body center" role="alert">
      <div class="fq-blob warn">${ic("alert-triangle", 48)}</div>
      <div><h1 class="fq-h2">ما قدرنا نقرأ طلبك الحين</h1><p class="fq-lead">صار خلل في الاتصال بفرق. طلبك محفوظ، جرّب مرة ثانية بعد لحظات.</p></div>
      ${quote}
      <div class="fq-actions" style="width:100%">
        <button class="fq-btn" type="button" data-action="retry-intent">حاول مرة ثانية</button>
        <button class="fq-btn ghost" type="button" data-action="edit-request">عدّل الطلب</button>
      </div>
    </section>`;
  }
  if (state.intentProblem === "not-understood") {
    return `${head}<section class="fq-body center" role="alert">
      <div class="fq-blob warn">${ic("help-circle", 48)}</div>
      <div><h1 class="fq-h2">ما فهمنا وش تحتاج بالضبط</h1><p class="fq-lead">اكتب اسم الخدمة أو المنتج بالعربي، وإذا تقدر أضف التفاصيل المهمة.</p></div>
      ${quote}
      <div class="fq-card pad" style="gap:8px;text-align:start"><p class="fq-small" style="font-weight:700;margin:0">أمثلة:</p>
        ${["أبي سباك يصلح تسريب في المطبخ", "كهربائي يركب 3 أفياش", "درابزين ستانلس للدرج"].map((idea) => `<button class="fq-chip" type="button" data-action="idea" data-query="${esc(idea)}">${esc(idea)}</button>`).join("")}</div>
      <button class="fq-btn" type="button" data-action="edit-request">عدّل الطلب</button>
    </section>`;
  }
  return `${head}<section class="fq-body">
    <div><h1 class="fq-h1">نحتاج توضيح بسيط</h1><p class="fq-lead">${esc(state.intentQuestion || "وضّح طلبك أكثر.")}</p></div>
    ${quote}
    <form id="intent-answer" class="fq-card pad"><div class="fq-inp"><input name="value" placeholder="جوابك" autocomplete="off" maxlength="200" aria-label="جوابك"></div><button class="fq-btn sm" type="submit">كمّل</button></form>
    <button class="fq-link" type="button" data-action="edit-request">أكتب الطلب من جديد</button>
  </section>`;
}

function renderUnderstand() {
  if (state.intentError || state.intentProblem) return understandProblem();
  if (!state.needs) {
    const card = `<div class="fq-card"><span class="fq-skel" style="height:24px;width:40%;border-radius:8px"></span><span class="fq-skel" style="height:16px;width:85%;border-radius:8px"></span><span class="fq-skel" style="height:16px;width:55%;border-radius:8px"></span></div>`;
    return `${fqHead({ title: "فهم الطلب", back: "home" })}<section class="fq-body" aria-busy="true">${card.repeat(2)}</section>`;
  }
  const active = state.needs.filter((item) => item.on);
  const cityRow = state.city && !state.cityPick
    ? `<button class="fq-place" type="button" data-action="need-city-change" aria-label="تغيير المدينة">${ic("map-pin", 16)}<span>${esc(cityLabel(state.city))}</span><span class="fq-link" style="margin-inline-start:8px">تغيير</span></button>`
    : `<div class="fq-card pad" style="gap:10px" data-testid="need-city"><p style="margin:0;font-weight:700">في أي مدينة؟</p>
        <div class="fq-pills" style="gap:10px">${state.cities.map((city) => {
          const on = state.city && (state.city === city.value || state.city === city.label);
          return `<button class="fq-chip${on ? " on" : ""}" type="button" data-action="need-city" data-city="${esc(city.value)}" aria-pressed="${Boolean(on)}">${esc(city.label)}</button>`;
        }).join("")}</div></div>`;
  return `${fqHead({ title: "فهم الطلب", back: "home" })}
  <section class="fq-body">
    <div><h1 class="fq-h1">هذا اللي فهمناه</h1><p class="fq-lead">راجع طلبك وعدّل اللي تبي قبل نبدأ البحث.</p></div>
    ${cityRow}
    ${state.needs
      .map((need, index) => `<article class="fq-card fq-needcard${need.on ? "" : " is-off"}">
        <div class="fq-row">
          <button class="fq-editbtn" type="button" data-action="edit-need" data-index="${index}">تعديل</button>
          <span style="display:flex;align-items:center;gap:8px">
            <span class="name"><bdi>${esc(needLabel(need))}</bdi></span>
            <button class="fq-checkbadge${need.on ? "" : " off"}" type="button" data-action="toggle-need" data-index="${index}" aria-pressed="${need.on}" aria-label="${need.on ? "استبعاد البند" : "تضمين البند"}">${ic("check", 14)}</button>
          </span>
        </div>
        <p class="desc"><bdi>${esc(need.desc)}</bdi></p>
        <hr class="fq-line">
        <div style="display:flex;flex-direction:column;gap:8px">
          <span class="fq-meta" style="display:flex;align-items:center;gap:6px">${ic("map-pin", 16)}<bdi>${esc(needPlace(need))}</bdi></span>
          ${need.qty > 1 ? `<span class="fq-meta" style="display:flex;align-items:center;gap:6px">${ic("clipboard", 16)}<span>الكمية: ${formatCount(need.qty)}${need.unit ? ` <bdi>${esc(need.unit)}</bdi>` : ""}</span></span>` : ""}
          ${need.when ? `<span class="fq-meta" style="display:flex;align-items:center;gap:6px">${ic("calendar", 16)}<bdi>${esc(need.when)}</bdi></span>` : ""}
        </div>
      </article>`)
      .join("")}
    <button class="fq-addbtn" type="button" data-action="add-need" style="align-self:flex-start">${ic("plus", 14)}إضافة بند</button>
    <div class="fq-sticky"><button class="fq-btn" type="button" data-action="run-search" ${active.length && state.city ? "" : "disabled"}>${state.city ? "ابحث عن الخيارات" : "اختر المدينة أول"}</button></div>
  </section>
  ${state.editing != null ? editSheet(state.editing === state.needs.length ? blankNeed() : state.needs[state.editing], state.editing) : ""}`;
}

function blankNeed() {
  return { key: `n${Date.now()}`, name: "", desc: "", city: state.city, district: "", when: "", qty: 1, on: true, intent: null, fresh: true };
}

// M03_EditItemSheet — node 85:217. A real dialog: focus moves in, Escape closes it.
function editSheet(need, index) {
  if (!need) return "";
  const fresh = Boolean(need.fresh);
  return `<div class="fq-scrim" data-action="close-sheet">
    <form class="fq-sheet" id="edit-need" data-index="${index}" role="dialog" aria-modal="true" aria-labelledby="edit-need-title">
      <span class="fq-grab" aria-hidden="true"></span>
      <h2 id="edit-need-title">${fresh ? "إضافة بند" : "تعديل البند"}</h2>
      <div class="fq-field"><label for="need-name">اسم البند</label><div class="fq-inp"><input id="need-name" name="name" value="${esc(need.name)}" maxlength="60" ${fresh ? 'required placeholder="مثلاً: كهربائي"' : ""}></div></div>
      <div class="fq-field"><label for="need-desc">الوصف</label><div class="fq-inp" style="min-height:80px;align-items:flex-start"><textarea id="need-desc" name="desc" rows="2" maxlength="300" ${fresh ? 'placeholder="مثلاً: يركب 3 أفياش في الصالة"' : ""}>${esc(need.desc)}</textarea></div></div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
        <div class="fq-field"><label for="need-place">الموقع</label><div class="fq-inp"><input id="need-place" name="district" value="${esc(needPlace(need))}"></div></div>
        <div class="fq-field"><label for="need-qty">الكمية</label>
          <div class="fq-inp" style="justify-content:space-between">
            <button class="fq-eye" type="button" data-action="qty" data-step="-1" aria-label="أنقص">${ic("minus", 18)}</button>
            <input id="need-qty" name="qty" inputmode="numeric" value="${esc(String(need.qty || 1))}" style="text-align:center;max-width:48px">
            <button class="fq-eye" type="button" data-action="qty" data-step="1" aria-label="زد">${ic("plus", 18)}</button>
          </div></div>
      </div>
      <div class="fq-field"><label for="need-when">وقت التنفيذ المتوقع</label><div class="fq-inp">${ic("calendar", 18)}<input id="need-when" name="when" value="${esc(need.when || "")}" placeholder="مثلاً: السبت، 28 سبتمبر"></div></div>
      <div class="fq-actions">
        <button class="fq-btn sm" type="submit">${fresh ? "إضافة البند" : "حفظ التعديل"}</button>
        <button class="fq-btn ghost sm" type="button" data-action="close-sheet">إلغاء</button>
        ${fresh ? "" : `<button class="fq-btn quiet danger-text" type="button" data-action="delete-need" data-index="${index}">حذف البند</button>`}
      </div>
    </form>
  </div>`;
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


function finalNotice(status, count) {
  if (status === "PARTIAL_RESULTS") return count ? "ما قدرنا نكمل البحث. هذي الخيارات اللي وصلت." : "البحث ما اكتمل.";
  if (status === "LIVE_UNAVAILABLE") return "المصدر ما استجاب الحين، فما نقدر نأكد إذا فيه نتائج أو لا.";
  if (status === "TIMEOUT") return count ? "البحث طال، وهذي الخيارات اللي وصلت." : "انقطع البحث قبل ما يكتمل. جرّب مرة ثانية.";
  if (status === "NO_QUALIFIED_RESULTS") return "لقينا إعلانات، بس ما فيه شيء يطابق طلبك.";
  if (status === "LIVE_EMPTY" || status === "LOCAL_EMPTY") return "ما رجع المصدر إعلان يطابق هذا الطلب.";
  if (status === "NOT_UNDERSTOOD") return "ما فهمنا الطلب. اكتبه بطريقة أوضح.";
  if (status === "DELETED_AD") return "الإعلان ما عاد متاح.";
  if (status === "SELLER_UNAVAILABLE") return "المورد ما عاد متاح.";
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
  return `<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">${state.files
    .map((item, index) => `<span class="fq-file">${item.preview ? `<img src="${item.preview}" alt="">` : `<span>PDF</span>`}
      <button type="button" data-action="remove-file" data-index="${index}" aria-label="حذف ${esc(item.file.name)}">${ic("x", 12)}</button></span>`)
    .join("")}</div>`;
}


function initial(name) {
  const text = String(name || "ف").trim();
  return esc(text.charAt(0) || "ف");
}

// ---------------------------------------------------------------------------
// Figma design system (file sL4tnA7DTWWhhbarV0ttZZ, canvas "01 — FARQ SCREENS")
// Feather icon set, inlined so an icon inherits the colour of the text beside it.
// ---------------------------------------------------------------------------
const ICONS = {
  reply: '<path d="M9 17l-5-5 5-5"/><path d="M20 18v-2a4 4 0 0 0-4-4H4"/>',
  "chevron-down": '<path d="M6 9l6 6 6-6"/>',
  home: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',
  "file-text": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/><path d="M16 13H8M16 17H8M10 9H8"/>',
  user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  phone: '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.79 19.79 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.9.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/>',
  "rotate-cw": '<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>',
  back: '<polyline points="9 18 15 12 9 6"/>',
  forward: '<polyline points="15 18 9 12 15 6"/>',
  globe: '<circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
  "map-pin": '<path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  paperclip: '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
  smile: '<circle cx="12" cy="12" r="10"/><path d="M8 14s1.5 2 4 2 4-2 4-2"/><path d="M9 9h.01M15 9h.01"/>',
  send: '<path d="M22 2 11 13"/><path d="M22 2 15 22l-4-9-9-4z"/>',
  lock: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
  bell: '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>',
  star: '<polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26"/>',
  award: '<circle cx="12" cy="8" r="7"/><polyline points="8.21 13.89 7 23 12 20 17 23 15.79 13.88"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  "check-circle": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  camera: '<path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/>',
  folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  "alert-triangle": '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>',
  "help-circle": '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
  "message-square": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  "message-circle": '<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8z"/>',
  tag: '<path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z"/><path d="M7 7h.01"/>',
  "arrow-down": '<path d="M12 5v14"/><polyline points="19 12 12 19 5 12"/>',
  "more-vertical": '<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  mail: '<path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22 6 12 13 2 6"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
  "eye-off": '<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20C5 20 1 12 1 12a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><path d="M1 1l22 22"/>',
  search: '<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>',
  "log-out": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><path d="M21 12H9"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  minus: '<path d="M5 12h14"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  info: '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/>',
  "credit-card": '<rect x="1" y="4" width="22" height="16" rx="2"/><path d="M1 10h22"/>',
  zap: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
  clipboard: '<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/>',
};

function ic(name, size = 20) {
  const body = ICONS[name];
  if (!body) return "";
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;
}

// ---------------------------------------------------------------------------
// Sound. Short tones built with the Web Audio API — no files to download, and
// nothing plays until the person has tapped something, which is also what the
// browsers require. Off is remembered per device.
// ---------------------------------------------------------------------------
const SOUNDS = {
  // name: [ [frequency, start, length], ... ], gain — the note count is the message:
  // one for a reply, two for a price, three falling for a price that beats them all.
  tap: [[[660, 0, 0.05]], 0.05],
  reply: [[[880, 0, 0.08]], 0.06],
  offer: [[[784, 0, 0.1], [1047, 0.09, 0.14]], 0.09],
  lower: [[[1047, 0, 0.09], [784, 0.08, 0.1], [659, 0.16, 0.16]], 0.09],
  found: [[[659, 0, 0.09], [988, 0.08, 0.16]], 0.08],
  award: [[[523, 0, 0.12], [659, 0.1, 0.12], [784, 0.2, 0.14], [1047, 0.3, 0.3]], 0.1],
  fail: [[[300, 0, 0.14], [220, 0.12, 0.22]], 0.08],
};

const sound = {
  ctx: null,
  ready: false,
  get on() {
    try {
      return localStorage.getItem("farq.sound") !== "0";
    } catch (_error) {
      return true;
    }
  },
  set on(value) {
    try {
      localStorage.setItem("farq.sound", value ? "1" : "0");
    } catch (_error) {}
  },
  // The first tap unlocks audio; after that a cue can play whenever it likes.
  unlock() {
    if (this.ready) return;
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return;
    this.ctx = this.ctx || new Ctx();
    this.ctx.resume?.();
    this.ready = true;
  },
  // Reduced motion is about movement on screen; sound answers only to this app's
  // switch, to what the browser allows, and to the device's own silent mode.
  play(name) {
    if (!this.on || !this.ready || !this.ctx) return;
    const recipe = SOUNDS[name];
    if (!recipe) return;
    const [notes, gain] = recipe;
    const now = this.ctx.currentTime;
    for (const [frequency, at, length] of notes) {
      const osc = this.ctx.createOscillator();
      const amp = this.ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = frequency;
      amp.gain.setValueAtTime(0, now + at);
      amp.gain.linearRampToValueAtTime(gain, now + at + 0.012);
      amp.gain.exponentialRampToValueAtTime(0.0001, now + at + length);
      osc.connect(amp).connect(this.ctx.destination);
      osc.start(now + at);
      osc.stop(now + at + length + 0.02);
    }
  },
};

// A short buzz to go with the cue, where the phone allows it.
function buzz(pattern) {
  if (!sound.on) return;
  // Browsers refuse (and log) a vibration before the person has tapped the page.
  const activation = navigator.userActivation;
  if (activation ? !activation.hasBeenActive : !sound.ready) return;
  try {
    navigator.vibrate?.(pattern);
  } catch (_error) {}
}

function cue(name, pattern) {
  sound.play(name);
  if (pattern) buzz(pattern);
}

// Each happening sounds once. The key is the thing that happened — a message id, a
// search — so a re-render, another poll, a reconnect or a repeated event stays quiet.
const sounded = new Set();
function cueOnce(key, name, pattern) {
  if (!key || sounded.has(key)) return false;
  sounded.add(key);
  if (sounded.size > 500) for (const old of [...sounded].slice(0, 200)) sounded.delete(old);
  cue(name, pattern);
  return true;
}

// The screen header: deep green, the title in the middle, the brand accent line under it.
// `back` is the data-action for the chevron; in Arabic it points right, at the start of the line.
// The screen header: a deep-green gradient, the title in the middle, the brand accent under it.
// The frames put the back chevron on the LEFT and the language control on the RIGHT on every
// screen except the conversation, which swaps them — `backStart` asks for that swap.
function fqHead({ title = "", sub = "", back = "", start = "", end = "", mark = false, auth = false, backStart = false } = {}) {
  // Inside Farq the screen's own head is folded away (Farq's header stands above the
  // frame), which left no way back between Taseer's screens. A slim bar carries the way
  // back and the screen's name; the home screen and the tabs need none.
  if (isFarqEmbed()) {
    if (!back) return "";
    return `<div class="fq-embar">
      <button class="fq-embar-back" type="button" data-action="${esc(back)}" aria-label="رجوع">${ic("back", 18)}</button>
      <div class="fq-embar-mid"><span class="fq-embar-title"><bdi>${esc(title)}</bdi></span>${sub ? `<span class="fq-embar-sub"><bdi>${esc(sub)}</bdi></span>` : ""}</div>
      ${end && !end.includes("fq-lang") ? `<div class="fq-embar-end">${end}</div>` : "<span></span>"}
    </div>`;
  }
  const langBtn = `<button class="fq-lang" type="button" data-action="lang" aria-label="اللغة">${ic("globe", 16)}<span>العربية</span></button>`;
  // the standard header draws a bare chevron; the conversation draws it on white
  const backBtn = back
    ? `<button class="fq-ibtn ${backStart ? "light" : "plain"}" type="button" data-action="${esc(back)}" aria-label="رجوع">${ic("back", 18)}</button>`
    : "";
  const brand = mark ? `<span class="fq-head-mark">فرق</span>` : "";
  // right slot first: in Arabic the row starts on the right
  let lead = start || brand || (back && !backStart ? langBtn : "") || (back ? backBtn : langBtn);
  let tail = end || "";
  if (back && backStart) {
    lead = backBtn;
    tail = end || brand || "";
  } else if (back) {
    lead = start || brand || langBtn;
    tail = end || backBtn;
  }
  return `<header class="fq-head${auth ? " is-auth" : ""}${back ? " deep" : ""}">
    <div class="fq-head-row">${lead || "<span></span>"}
      <div class="fq-head-mid"><h1 class="fq-head-title"><bdi>${esc(title)}</bdi></h1>${sub ? `<p class="fq-head-sub"><bdi>${esc(sub)}</bdi></p>` : ""}</div>
      ${tail || "<span></span>"}</div>
    <div class="fq-head-accent"></div>
  </header>`;
}

// Bottom navigation, exactly the three tabs the final screens carry.
function fqNav(active) {
  const tab = (key, action, label, glyph) => {
    const badge = key === "requests" && state.unreadTotal ? `<span class="fq-tab-badge" data-unread-total>${formatCount(state.unreadTotal)}</span>` : "";
    return `<button class="fq-tab${active === key ? " on" : ""}" type="button" data-action="${action}" aria-current="${active === key ? "page" : "false"}">
      <span class="fq-tab-wrap">${ic(glyph, 24)}${badge}</span><span>${label}</span></button>`;
  };
  // طلباتي and حسابي need a session. Inside Farq that session is the Farq account, which
  // arrives on its own; until it has, the row is the one tab that needs none.
  const tabs = isFarqEmbed() && !state.token
    ? tab("home", "home", "الرئيسية", "home")
    : `${tab("home", "home", "الرئيسية", "home")}
    ${tab("requests", "requests", "طلباتي", "file-text")}
    ${tab("account", "account", "حسابي", "user")}`;
  return `<nav class="fq-nav" aria-label="التنقل"><div class="fq-nav-row">
    ${tabs}
  </div></nav>`;
  // (the row itself is laid out left-to-right, so this order renders الرئيسية · طلباتي · حسابي)
}

function fqScreen(head, body, nav = "") {
  return `${head}${body}${nav}`;
}

// One bar on every screen: the mark with «فرق تسعير» (or the screen's title), and the back arrow
// pointing right, the way back reads in Arabic.



function shell(body) {
  return `<main class="fq">${body}</main>`;
}

// The reveal button sits beside the input, not inside its label, so one tap counts once.

// AUTH01_Login_AR — node 19:74.
// AUTH01_Login_AR — node 19:74. The header is not mirrored: the wordmark sits left, the
// language control right, the way the frame draws it.
function farqSignInUrl() {
  // Sign in on Farq, then come back to the same screen with the journey's draft still here.
  const back = `${location.pathname}${location.search}`;
  return `${FARQ_SITE}/taseer?signin=1&back=${encodeURIComponent(back)}`;
}

function renderAuth() {
  const register = state.authMode === "register";
  if (!state.legacyAuth && !state.farqFallback) return renderFarqAuth();
  const linking = Boolean(state.farqLink) && !register;
  const fallback = Boolean(state.farqFallback) && !linking && !register;
  const title = register ? "إنشاء حساب" : linking ? "اربط حساب تسعير القديم" : "تسجيل الدخول";
  const sub = register ? "حساب واحد لكل طلباتك في فرق" : linking ? "عندك حساب تسعير قديم بنفس بريد فرق. ادخل بكلمة مروره مرة وحدة، وبعدها حساب فرق يكفي." : fallback ? "ما وصلتنا جلسة فرق من التطبيق. ادخل بحساب تسعير عشان نكمل إرسال طلبك، أو حدّث تطبيق فرق." : "ادخل إلى حسابك في فرق";
  const lang = `<button class="fq-lang" type="button" data-action="lang">${ic("globe", 16)}<span>العربية</span></button>`;
  return `<header class="fq-head is-auth">
    <div class="fq-head-row auth">${lang}<span class="fq-wordmark">Farq</span></div>
    <div class="fq-head-accent"></div>
  </header>
  <section class="fq-body" style="padding:24px 24px 32px">
    <div class="fq-hero soft">
      <span class="halo" aria-hidden="true"></span>
      <h1 class="fq-h1" style="font-size:28px;font-weight:700">${esc(title)}</h1>
      <p class="fq-lead">${esc(sub)}</p>
      <p class="fq-small fq-muted" style="line-height:1.5">قارن الأسعار وتواصل مع الموردين فوراً.</p>
    </div>
    <form id="auth-form" class="fq-card" style="gap:16px;padding:24px;border-radius:var(--fq-r-input);box-shadow:var(--fq-shadow-form)" novalidate>
      ${register
        ? `<div class="fq-field"><label for="auth-name">الاسم</label>
            <div class="fq-inp">${ic("user", 18)}<input id="auth-name" name="name" autocomplete="name" required minlength="2" maxlength="60" placeholder="اسمك"></div></div>`
        : ""}
      <div class="fq-field"><label for="auth-email">البريد الإلكتروني</label>
        <div class="fq-inp">${ic("mail", 18)}<input id="auth-email" name="email" type="email" inputmode="email" autocomplete="email" dir="ltr" required placeholder="farq@example.com"></div></div>
      <div class="fq-field"><label for="auth-password">كلمة المرور</label>
        <div class="fq-inp">${ic("lock", 18)}<input id="auth-password" name="password" type="${state.showPassword ? "text" : "password"}" autocomplete="${register ? "new-password" : "current-password"}" dir="ltr" required minlength="8" placeholder="••••••••">
          <button class="fq-eye" type="button" data-action="toggle-password" aria-label="${state.showPassword ? "إخفاء كلمة المرور" : "إظهار كلمة المرور"}" aria-pressed="${state.showPassword}">${ic(state.showPassword ? "eye-off" : "eye", 20)}</button></div>
        ${register ? "" : `<div style="display:flex;justify-content:flex-end;padding-top:4px"><button class="fq-link" type="button" data-action="forgot">نسيت كلمة المرور؟</button></div>`}
      </div>
      ${state.authError ? `<p class="fq-small" role="alert" style="color:#b3402a">${esc(state.authError)}</p>` : ""}
    </form>
    <div class="fq-actions" style="gap:20px;align-items:center">
      <button class="fq-btn" type="submit" form="auth-form" ${state.busy ? "disabled" : ""}>${state.busy ? "لحظة…" : title}</button>
      <p class="fq-small" style="text-align:center">${register ? "عندك حساب؟" : "ليس لديك حساب؟"}
        <button class="fq-link" type="button" data-action="auth-mode" style="text-decoration:underline;font-size:14px;font-weight:700">${register ? "تسجيل الدخول" : "إنشاء حساب جديد"}</button></p>
    </div>
    <p class="fq-legal">باستخدامك للتطبيق، فإنك توافق على <a href="/terms" data-action="legal" data-doc="terms">الشروط والأحكام</a> و<a href="/privacy" data-action="legal" data-doc="privacy">سياسة الخصوصية</a> و<a href="/refunds" data-action="legal" data-doc="refunds">سياسة الإلغاء والاسترداد</a></p>
    <p class="fq-legal"><a href="/plans" data-action="show-plans">الباقات والأسعار</a></p>
  </section>`;
}

// AUTH00 — one account for all of Farq. Taseer no longer opens its own door: the customer
// signs in (or up) on farq.sa, and that session signs him in here. The old email-and-password
// form stays one tap away for an account made on taseer.farq.sa before this.
function renderFarqAuth() {
  const lang = `<button class="fq-lang" type="button" data-action="lang">${ic("globe", 16)}<span>العربية</span></button>`;
  return `<header class="fq-head is-auth">
    <div class="fq-head-row auth">${lang}<span class="fq-wordmark">Farq</span></div>
    <div class="fq-head-accent"></div>
  </header>
  <section class="fq-body" style="padding:24px 24px 32px">
    <div class="fq-hero soft">
      <span class="halo" aria-hidden="true"></span>
      <h1 class="fq-h1" style="font-size:28px;font-weight:700">حسابك في فرق يكفي</h1>
      <p class="fq-lead">تسعير جزء من فرق: نفس الحساب، نفس الدخول. سجّل دخولك مرة وحدة وترجع لنفس المكان.</p>
    </div>
    ${state.authError ? `<p class="fq-small" role="alert" style="color:#b3402a">${esc(state.authError)}</p>` : ""}
    <div class="fq-actions" style="gap:16px;align-items:center">
      <a class="fq-btn" href="${esc(farqSignInUrl())}" data-action="farq-sign-in" style="text-decoration:none" ${state.busy ? 'aria-disabled="true"' : ""}>${state.busy ? "لحظة…" : "تسجيل الدخول بحساب فرق"}</a>
      <button class="fq-link" type="button" data-action="legacy-auth" style="text-decoration:underline;font-size:14px">عندك حساب تسعير قديم بكلمة مرور؟</button>
    </div>
    <p class="fq-legal">باستخدامك للتطبيق، فإنك توافق على <a href="/terms" data-action="legal" data-doc="terms">الشروط والأحكام</a> و<a href="/privacy" data-action="legal" data-doc="privacy">سياسة الخصوصية</a> و<a href="/refunds" data-action="legal" data-doc="refunds">سياسة الإلغاء والاسترداد</a></p>
    <p class="fq-legal"><a href="/plans" data-action="show-plans">الباقات والأسعار</a></p>
  </section>`;
}

async function submitAuth(form) {
  const data = new FormData(form);
  const register = state.authMode === "register";
  const json = { email: String(data.get("email") || "").trim(), password: String(data.get("password") || "") };
  if (register) json.name = String(data.get("name") || "").trim();
  const farqToken = state.farqLink?.token || farqSessionToken();
  if (!register && farqToken) json.farq_access_token = farqToken;
  if (register && json.name.length < 2) return showAuthError("اكتب اسمك");
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(json.email)) return showAuthError("اكتب بريد إلكتروني صحيح");
  if (json.password.length < 8) return showAuthError("كلمة المرور لازم تكون 8 أحرف أو أكثر");
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
    state.authError = "";
    state.legacyAuth = false;
    if (state.farqLink || state.farqFallback) {
      if (result.farq_linked) toast("تم ربط حسابك بحساب فرق. من الحين حساب فرق يكفي.");
      state.farqLink = null;
      state.farqFallback = false;
      if (isFarqEmbed()) {
        state.view = state.returnView || "home";
        state.returnView = "home";
        resumeAfterSignIn();
        return;
      }
    }
    // Back to the screen the sign-in interrupted: the address never left it (a sign-in has
    // none of its own), and the journey's draft is still here — a review keeps its ticks.
    const route = state.returnRoute || location.pathname;
    state.returnRoute = "";
    applyRoute(route, { pop: true });
    loadSubStatus().catch(() => {});
    loadVerification().catch(() => {});
    refreshUnread();
  } catch (error) {
    state.busy = false;
    const messages = { 401: "البريد أو كلمة المرور غير صحيحة", 409: "هذا البريد مسجّل من قبل، سجّل دخول", 422: "تأكد من البيانات" };
    showAuthError(messages[error.status] || "ما قدرنا نكمل، جرّب مرة ثانية");
  }
}

function showAuthError(message) {
  state.authError = message;
  render();
  return null;
}

// The twelve categories on the home screen, each with its clay icon. Tapping one narrows
// what the composer suggests — it does not search on its own, because a bare category is
// not a request: «سيارات» tells us nothing to price.
const CATEGORIES = [
  { code: "trades", name: "صيانة وحرفيين", examples: ["سباك يصلح تسريب حمام", "كهربائي يركب 3 أفياش", "دهان غرفتين"] },
  { code: "building_materials", name: "مواد بناء ومقاولات", examples: ["مقاول تشطيب شقة", "طوب أحمر 5000 حبة", "صبة خرسانة جاهزة"] },
  { code: "vehicles", name: "سيارات", examples: ["لاندكروزر ٢٠٢٥ لون ابيض", "كامري مستعملة موديل ٢٠٢٠", "هايلكس غمارتين"] },
  { code: "parts", name: "قطع غيار السيارات", examples: ["إطارات 265/60 R18", "بطارية 100 أمبير", "دبل كلتش هايلكس"] },
  { code: "property", name: "عقار", examples: ["شقة إيجار سنوي بالملقا", "أرض تجارية شمال الرياض", "فيلا للبيع بالياسمين"] },
  { code: "appliances", name: "أجهزة منزلية", examples: ["تركيب مكيف سبليت", "غسالة أوتوماتيك 8 كيلو", "ثلاجة بابين"] },
  { code: "electronics", name: "إلكترونيات", examples: ["تلفزيون سامسونج ٦٥ بوصة", "آيفون 15 برو ماكس", "لابتوب للتصميم"] },
  { code: "furniture", name: "أثاث", examples: ["كنب زاوية 6 مقاعد", "غرفة نوم كاملة", "طاولة طعام 8 كراسي"] },
  { code: "moving", name: "نقل وسطحات", examples: ["نقل عفش شقة من الرياض لجدة", "سطحة نقل سيارة", "دينا نقل أغراض"] },
  { code: "equipment", name: "معدات", examples: ["مولد كهرباء 10 كيلو", "ضاغط هواء", "سقالات للإيجار"] },
  { code: "animals", name: "حلال وحيوانات", examples: ["خروف نعيمي للذبح", "أعلاف برسيم", "نقل مواشي"] },
  { code: "general_services", name: "خدمات عامة", examples: ["تنظيف شقة بعد الترميم", "مكافحة حشرات", "تنظيف خزان"] },
];

function categoryIcon(code) {
  const name = `category-${code.replace(/_/g, "-")}`;
  return `/assets/taseer/categories/${name}.webp`;
}

function activeCategory() {
  return CATEGORIES.find((item) => item.code === state.category) || null;
}

// M01_Home — node 27:10.
const QUERY_MAX = 500;
function composerCount(text) {
  const length = String(text || "").length;
  const hint = coarsePointer() ? "" : " · Shift+Enter لسطر جديد";
  return `${formatCount(length)}/${formatCount(QUERY_MAX)}${hint}`;
}
function coarsePointer() {
  return Boolean(window.matchMedia?.("(pointer: coarse)").matches);
}
const HOME_CHIPS = ["مقاول", "كهربائي بالساعة", "شقة إيجار سنوي بالملقا", "لاندكروزر ٢٠٢٥ لون ابيض", "تركيب مكيف", "تلفزيون سامسونج ٦٥ بوصة"];
function renderHome() {
  const place = `<button class="fq-place" type="button" data-action="change-city">${ic("map-pin", 16)}<span>${esc(cityLabel(state.city) || "اختر مدينتك")}</span></button>`;
  const picked = activeCategory();
  const chips = picked ? picked.examples : HOME_CHIPS;
  const hint = picked ? `مثلاً: ${picked.examples[0]}` : "مثلاً: أبي سباك يوم السبت وكهربائي يركب 3 أفياش";
  return `${fqHead({ title: "فرق Farq", end: place })}
  <section class="fq-body">
    <div class="fq-hero">
      <span class="halo" aria-hidden="true"></span>
      <h1>وش تبي نسعّر لك؟</h1>
      <p>قل لنا وش تحتاج، وفرق يجيب لك الفرق من عدة مصادر في محادثة وحدة. قارن، شوف الفرق، وخذ الأوفر.</p>
    </div>
    <form class="fq-card pad" id="composer" style="gap:10px">
      <label class="sr" for="composer-query">وش تبي نسعّر لك؟</label>
      <textarea id="composer-query" name="query" rows="2" placeholder="${esc(hint)}"
        style="border:0;outline:none;resize:none;font:inherit;font-size:16px;line-height:30px;color:var(--fq-text);background:none;width:100%">${esc(state.query)}</textarea>
      <span style="color:var(--fq-muted)">${ic("edit", 20)}</span>
    </form>
    <div>
      <div class="fq-row" style="margin-bottom:12px">
        <p class="fq-sec-title"><span>وش تبي تسعّر؟</span></p>
        ${picked ? `<button class="fq-link" type="button" data-action="clear-category">كل التصنيفات</button>` : ""}
      </div>
      <div class="fq-cats">${CATEGORIES.map((item) => `
        <button class="fq-cat${state.category === item.code ? " on" : ""}" type="button" data-action="category" data-code="${esc(item.code)}" aria-pressed="${state.category === item.code}">
          <img src="${categoryIcon(item.code)}" alt="" width="64" height="64" loading="lazy" decoding="async">
          <span>${esc(item.name)}</span>
        </button>`).join("")}</div>
    </div>
    <div class="fq-pills" style="gap:10px">${chips.map((idea) => `<button class="fq-chip" type="button" data-action="idea" data-query="${esc(idea)}">${esc(idea)}</button>`).join("")}</div>
    <div class="fq-sticky"><button class="fq-btn breathe" type="submit" form="composer" style="border-radius:var(--fq-r-input)">ابدأ التسعير</button></div>
  </section>
  ${fqNav("home")}`;
}
// M04_SearchProgress — node 27:124. The banner, the running count and the four steps all
// live in one card; the skeletons wait below it.
// A step turns done only on the event that proves it: the intent arriving, then results. The
// last step speaks in the present until results are actually on the screen.
function searchSteps() {
  return ["نفهم طلبك", "ندور على الخيارات المناسبة", "نرتب النتائج", state.results.length ? "جهزنا لك الخيارات" : "نجهز لك الخيارات"];
}
function renderSearching() {
  const SEARCH_STEPS = searchSteps();
  const at = state.results.length ? 2 : state.intent ? 1 : 0;
  const seen = state.scanned || 0;
  const card = `<div class="fq-card" style="gap:12px"><div style="display:flex;align-items:center;gap:12px">
      <span class="fq-skel" style="width:44px;height:44px;border-radius:50%"></span>
      <span class="fq-skel" style="flex:1;height:34px;border-radius:10px"></span></div>
    <span class="fq-skel" style="height:14px;width:70%;border-radius:8px"></span>
    <span class="fq-skel" style="height:22px;width:35%;border-radius:8px"></span></div>`;
  return `${fqHead({ title: "ماعليك فرق بيجيب الفرق" })}
  <section class="fq-body" aria-live="polite">
    <div class="fq-card pad" style="gap:14px">
      <div class="fq-live wide"><span class="fq-pulse" aria-hidden="true"></span>
        <span style="flex:1">${state.results.length ? `وصل ${suppliers(state.results.length)} حتى الآن...` : "يبحث فرق عن أفضل سعر لك الآن..."}</span></div>
      <div class="fq-live-count">
        <span class="fq-dots" aria-hidden="true"><i></i><i></i><i></i></span>
        <span>تحديث في الوقت الفعلي</span>
        <span style="flex:1"></span>
        ${seen ? `<b style="color:var(--fq-success)">فحصنا ${plural(seen, ["إعلان واحد", "إعلانين", "إعلانات", "إعلان"])} حتى الآن</b>` : ""}
      </div>
      <div class="fq-steps">${SEARCH_STEPS.map((label, index) => {
        const cls = index < at ? "done" : index === at ? "now" : "";
        return `<div class="fq-step ${cls}"><span class="mark">${index < at ? ic("check", 14) : ""}</span>
          ${index === at ? `<span class="fq-dots" aria-hidden="true"><i></i><i></i><i></i></span>` : ""}
          <span style="flex:1">${esc(label)}</span></div>`;
      }).join("")}</div>
    </div>
    <div class="fq-stagger" style="display:flex;flex-direction:column;gap:12px">${card.repeat(3)}</div>
  </section>`;
}

// A 0–100 ring with a caption, drawn beside a price or a rating (nodes 91:29, 91:53, 91:77).
function scoreRing(percent, caption) {
  const value = Math.max(0, Math.min(100, Math.round(percent)));
  return `<span class="fq-score" style="--p:${value}"><span class="ring"><span>${formatCount(value)}%</span></span><span class="cap">${esc(caption)}</span></span>`;
}


function renderQuestion() {
  const question = state.clarification || "";
  const aboutCity = question.includes("مدينة");
  return `<div><h1 class="fq-h1">${aboutCity ? "حدد المدينة" : "كمّل الطلب"}</h1><p class="fq-lead">${esc(question)}</p></div>
    ${aboutCity
      ? `<div class="fq-pills" style="gap:10px">${state.cities.map((city) => `<button class="fq-chip" type="button" data-action="answer" data-value="${esc(city.label)}" data-city="${esc(city.value)}">${esc(city.label)}</button>`).join("")}</div>`
      : `<form id="answer" class="fq-card pad"><div class="fq-inp"><input name="value" placeholder="جوابك" autocomplete="off"></div><button class="fq-btn sm" type="submit">كمّل</button></form>`}`;
}

function snip(result) {
  const lines = evidenceLines(result);
  const text = adStory(result.ad?.description) || lines[1] || lines[0] || "";
  return String(text).replace(/\s+/g, " ").trim().slice(0, 72);
}

// M05_SearchResults result card — node 27:180. The tick sits on the left, the supplier's
// mark on the right, and a bar down the left edge carries the saving's colour.
// Which of the customer's items a result answers. The search reports its results per item
// («groups»); the map is filled from those as they stream in.
function needOf(result) {
  return state.resultNeeds.get(resultKey(result)) || "";
}

function multiNeed() {
  return new Set(state.results.map(needOf).filter(Boolean)).size > 1;
}

function visibleResults() {
  if (!state.needFilter) return state.results;
  return state.results.filter((item) => needOf(item) === state.needFilter);
}

// Group index i answers the i-th item the customer kept on M02 when the parser split the same
// way; otherwise the group's own label names it.
function rememberGroups(groups) {
  if (!Array.isArray(groups) || !groups.length) return;
  const active = (state.needs || []).filter((item) => item.on);
  groups.forEach((group, index) => {
    const at = Number.isInteger(group.need_index) ? group.need_index : index;
    const label = active[at] && (Number.isInteger(group.need_index) || active.length === groups.length) ? needLabel(active[at]) : stripCity(group.need || "");
    for (const result of group.results || []) if (label && !state.resultNeeds.has(resultKey(result))) state.resultNeeds.set(resultKey(result), label);
  });
}

// Haraj usernames are handles («electrician220», «عضو 9103211»). A handle that reads like a
// name is shown as the name; one that does not becomes «مقدم خدمة · الحي», with the handle
// kept underneath so the customer can still tell suppliers apart.
function readableName(name) {
  const text = String(name || "").trim();
  if (!text || text === "مورد") return false;
  if (/\d/.test(text)) return false;
  if (/^عضو\b/.test(text)) return false;
  if (/^[A-Za-z]+$/.test(text) && text.length > 3 && !/[aeiou]/i.test(text.slice(1))) return false;
  return true;
}

function displayName(seller, result) {
  const raw = String(seller?.name || "").trim();
  const tidy = tidyName(raw);
  if (readableName(tidy)) return { name: tidy, handle: "" };
  const area = result?.ad?.district || seller?.district || cityLabel(result?.ad?.city || seller?.city || "");
  return { name: area ? `مقدم خدمة · ${area}` : "مقدم خدمة", handle: raw ? `@${raw.replace(/\s+/g, "")}` : "" };
}

let newCardsInBatch = 0;
// A listing price is not a quote for this customer's job - it can be per metre, per piece,
// or a token "call me" (1, 8, 10 ر.س). The price reaches us as an offer or in the
// conversation, so no ad price is shown before the supplier has answered.
function renderCard(result) {
  const key = resultKey(result);
  const fresh = state.seenCards && !state.seenCards.has(key);
  const animated = fresh && newCardsInBatch < 8;
  if (fresh) newCardsInBatch += 1;
  const selected = state.selected.has(key);
  const seller = sellerOf(result);
  const who = displayName(seller, result);
  const name = who.name;
  // The advert's own headline is the sentence in which the supplier says what he does:
  // "عروض كاميرات مراقبة مع التركيب". It used to be shown only when there was no body
  // text, and every listing has body text, so it was never shown at all.
  const offer = String(result.ad?.title || "").trim();
  const where = cityLabel(result.ad?.city || seller.city || "");
  const detail = snip(result);
  const matched = matchedWords(result);
  const marks = offerMarks(result);
  const age = adAge(result.ad);
  const needName = multiNeed() ? needOf(result) : "";
  const sources = result.ad ? imageSources(result.ad) : [];
  // One photo per listing: Haraj gives a thumbnail, and the two URLs are the same picture
  // at two sizes, not two pictures. So it is shown once, large enough to judge, and opens
  // the listing when tapped - no carousel over a single image.
  const photo = sources.length
    ? `<button class="fq-shot" type="button" data-action="open" data-key="${esc(key)}" aria-label="افتح إعلان ${esc(offer || name)}">
        <img alt="" data-src="${esc(sources.join("|"))}" loading="lazy" decoding="async"></button>`
    : "";
  return `<article class="fq-card fq-result${animated ? " fq-in" : ""}${selected ? " picked" : ""}"${animated ? ` style="animation-delay:${(newCardsInBatch - 1) * 35}ms"` : ""}>
    <div class="fq-offerrow">
      ${photo}
      <button type="button" data-action="open" data-key="${esc(key)}" class="fq-offerhead">
        ${offer ? `<strong class="fq-offertitle"><bdi>${esc(offer)}</bdi></strong>` : `<strong class="fq-offertitle"><bdi>${esc(name)}</bdi></strong>`}
        <span class="fq-meta fq-offerwho"><bdi>${esc(name)}</bdi>${where ? ` · ${esc(where)}` : ""}</span>
      </button>
    </div>
    ${matched.length ? `<div class="fq-matched" aria-label="طابق طلبك في">${matched
      .map((word) => `<span class="fq-hit">${ic("check", 11)}<bdi>${esc(word)}</bdi></span>`)
      .join("")}</div>` : ""}
    ${detail ? `<p class="fq-small fq-offerbody"><bdi>${esc(detail)}</bdi></p>` : ""}
    ${needName || age || marks.length ? `<div class="fq-cardtags">${needName ? `<span class="fq-tag deep"><bdi>${esc(needName)}</bdi></span>` : ""}${marks
      .map((mark) => `<span class="fq-tag">${esc(mark)}</span>`)
      .join("")}${age ? `<span class="fq-tag${age.old ? " warn" : ""}">${age.old ? "إعلان قديم · " : ""}${esc(age.label)}</span>` : ""}</div>` : ""}
    <button class="fq-pick${selected ? " on" : ""}" type="button" data-action="toggle" data-key="${esc(key)}" aria-pressed="${selected}">
      ${ic(selected ? "check" : "plus", 16)}<span>${selected ? "مختار — اضغط للإزالة" : "اختر هذا المورد"}</span></button>
  </article>`;
}

// M05_SearchResults — node 27:180.
function renderFlow() {
  const asking = state.searchState === "CLARIFICATION_REQUIRED" || state.searchState === "LOCATION_AMBIGUOUS";
  const live = state.searching || (state.partial && (state.searchState === "LIVE_SEARCHING" || state.searchState === "PARTIAL_RESULTS"));
  const showEmpty = !asking && !state.partial && state.results.length === 0;
  if (live && !state.results.length && !asking) return renderSearching();
  const count = state.selected.size;
  const tabs = [...new Set(state.results.map(needOf).filter(Boolean))];
  if (state.needFilter && !tabs.includes(state.needFilter)) state.needFilter = "";
  const shown = visibleResults();
  return `${fqHead({ title: "نتائج البحث", back: "back-understand" })}
  <section class="fq-body tight">
    ${asking ? renderQuestion() : ""}
    ${asking || !state.results.length ? "" : `<div class="fq-filters-row">${filtersRow(tabs, shown)}</div>`}
    ${state.notice && !showEmpty ? `<p class="fq-meta" aria-live="polite">${esc(state.notice)}</p>` : ""}
    ${showEmpty ? emptyState() : ""}
    ${shown.length ? `<div class="fq-stagger" style="display:flex;flex-direction:column;gap:12px">${((newCardsInBatch = 0), shown.map(renderCard).join(""))}</div>` : ""}
    ${count ? `<div class="fq-sticky"><button class="fq-btn" type="button" data-action="review"><span class="count">${formatCount(count)}</span>متابعة مع ${esc(suppliers(count))}</button></div>` : ""}
  </section>
  ${state.view === "detail" ? detailSheet() : ""}
  ${state.capSheet ? capSheet() : ""}`;
}

// The count, the «still searching» mark and the item pills above the list. Drawn whole on a
// full render, and redrawn alone when a streamed batch changes the count or adds an item.
function filtersRow(tabs, shown) {
  return `<div class="fq-live"><span class="fq-pulse" aria-hidden="true"></span>
        <span data-count="${shown.length}">${esc(foundLine(state.shownCount || shown.length))}</span><span data-partial>${state.partial ? " • البحث مستمر" : ""}</span></div>
      <span class="fq-meta">الأسعار توصلك في عروضهم</span>
      ${tabs.length > 1 ? `<button class="fq-fpill all${state.needFilter ? "" : " on"}" type="button" data-action="filter-need" data-name="" aria-pressed="${!state.needFilter}">الكل</button>${tabs.map((name) => `<button class="fq-fpill${state.needFilter === name ? " on" : ""}" type="button" data-action="filter-need" data-name="${esc(name)}" aria-pressed="${state.needFilter === name}"><bdi>${esc(name)}</bdi></button>`).join("")}` : ""}`;
}

function foundLine(count) {
  return count ? `لقينا ${suppliers(count)}` : "ما فيه نتائج";
}

// An empty result says why, and offers what can change the outcome: a failure can be tried
// again as it is; an empty search needs a different wording, item or city, not the same search.
function emptyState() {
  const status = state.searchState;
  const failed = ["INTERNAL_ERROR", "TIMEOUT", "LIVE_UNAVAILABLE", "PARTIAL_RESULTS"].includes(status);
  const city = cityLabel(customerCity());
  const blob = (glyph) => `<div class="fq-blob warn">${ic(glyph, 48)}</div>`;
  if (failed) {
    return `<div class="fq-body center" style="padding:24px 0" role="alert">${blob("alert-triangle")}
      <div><h2 class="fq-h2">${esc(state.notice || finalNotice("INTERNAL_ERROR", 0))}</h2><p class="fq-lead">طلبك وبنودك محفوظة، ما يحتاج تكتبها من جديد.</p></div>
      <div class="fq-actions" style="width:100%">
        <button class="fq-btn" type="button" data-action="retry-search">حاول مرة ثانية</button>
        <button class="fq-btn ghost" type="button" data-action="back-understand">عدّل البنود</button>
      </div></div>`;
  }
  if (status === "NOT_UNDERSTOOD") {
    return `<div class="fq-body center" style="padding:24px 0" role="alert">${blob("help-circle")}
      <div><h2 class="fq-h2">ما فهمنا الطلب</h2><p class="fq-lead">اكتب اسم الخدمة أو المنتج بوضوح، مثل: «سباك يصلح تسريب» أو «درابزين ستانلس».</p></div>
      <div class="fq-actions" style="width:100%">
        <button class="fq-btn" type="button" data-action="back-understand">عدّل وصف البند</button>
        <button class="fq-btn ghost" type="button" data-action="edit-request">اكتب الطلب من جديد</button>
      </div></div>`;
  }
  const lead = status === "NO_QUALIFIED_RESULTS"
    ? "لقينا إعلانات، بس ما فيها شيء يطابق طلبك بالضبط."
    : `ما لقينا إعلانات تطابق هذا الطلب${city ? ` في ${city}` : ""} الحين.`;
  return `<div class="fq-body center" style="padding:24px 0">${blob("search")}
    <div><h2 class="fq-h2">ما لقينا خيارات مناسبة</h2><p class="fq-lead">${esc(lead)} جرّب وصف أبسط أو مدينة ثانية.</p></div>
    <div class="fq-actions" style="width:100%">
      <button class="fq-btn" type="button" data-action="back-understand">عدّل وصف البند</button>
      <button class="fq-btn ghost" type="button" data-action="other-city">جرّب مدينة ثانية</button>
      <button class="fq-link" type="button" data-action="edit-request" style="align-self:center">اكتب طلب جديد</button>
    </div></div>`;
}

// Haraj ads carry boilerplate lines that mean nothing inside Taseer.
const AD_NOISE = [/رقم\s*الجوال\s*يظهر/, /اضغط\s*(على\s*)?(زر\s*)?تواصل/, /للتواصل\s*واتس/, /^\s*للجادين\s*فقط\s*$/];
const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'", nbsp: " ", ndash: "–", mdash: "—", hellip: "…" };
function unescapeOnce(text) {
  return String(text || "").replace(/&(amp|lt|gt|quot|#39|nbsp|ndash|mdash|hellip);/g, (_m, name) => ENTITIES[name]);
}

function adStory(text) {
  // Haraj bodies arrive with entities escaped twice ("&amp;ndash;"), which the customer
  // would otherwise read as markup in the middle of a sentence.
  return unescapeOnce(unescapeOnce(text))
    .split("\n")
    .filter((line) => line.trim() && !AD_NOISE.some((pattern) => pattern.test(line)))
    .join("\n")
    .trim();
}

// M06_SupplierDetailSheet — node 85:279. A sheet over the results, not its own screen.
function detailSheet() {
  const result = state.active;
  if (!result) return "";
  const seller = sellerOf(result);
  const who = displayName(seller, result);
  const name = who.name;
  const age = adAge(result.ad);
  const gallery = state.gallery.length ? state.gallery : imageSources(result.ad);
  const key = resultKey(result);
  const selected = state.selected.has(key);
  const story = adStory(result.ad?.description);
  return `<div class="fq-scrim" data-action="close-sheet">
    <div class="fq-sheet" role="dialog" aria-modal="true" aria-label="${esc(name)}">
      <span class="fq-grab" aria-hidden="true"></span>
      <div class="fq-row" style="align-items:flex-start">
        <span class="fq-av" style="width:56px;height:56px;border-radius:28px;background:var(--fq-light);color:var(--fq-deep);font-size:20px">${initial(name)}</span>
        <span style="flex:1;min-width:0;display:flex;flex-direction:column;gap:4px;text-align:start">
          <strong style="font-size:22px;font-weight:800"><bdi>${esc(name)}</bdi></strong>
          ${who.handle ? `<span class="fq-meta fq-handle" dir="auto">${esc(who.handle)}</span>` : ""}
          <span class="fq-meta">${esc(place(result) || cityLabel(result.ad?.city || seller.city || ""))}</span>
          ${age ? `<span class="fq-meta"${age.old ? ' style="color:var(--fq-warning);font-weight:700"' : ""}>${age.old ? "إعلان قديم · " : "نُشر الإعلان "}${esc(age.label)}</span>` : ""}
        </span>
      </div>
      <hr class="fq-line">
      <h2 style="font-size:15px">عن المورد والخدمة</h2>
      ${story ? `<p class="fq-small" style="line-height:1.7"><bdi>${esc(story)}</bdi></p>` : `<p class="fq-meta">ما فيه وصف إضافي من المورد.</p>`}
      ${gallery.length ? `<h2 style="font-size:15px">صور من إعلان المورد</h2>
        <div id="gallery" style="display:flex;gap:8px;overflow-x:auto;scrollbar-width:none">${gallery
          .map((url) => `<div class="fq-skel" style="flex:none;width:96px;height:72px;border-radius:12px;overflow:hidden" data-frame><img alt="" data-src="${esc(url)}" loading="lazy" style="width:100%;height:100%;object-fit:cover"></div>`)
          .join("")}</div>` : ""}
      ${result.ad?.listing_state === "deleted" ? `<p class="fq-small" style="color:#b3402a">هذا الإعلان محذوف.</p>` : ""}
      <div class="fq-actions">
        <button class="fq-btn sm" type="button" data-action="quote" data-key="${esc(key)}">${selected ? "تم اختياره" : "اختيار هذا المورد"}</button>
        <button class="fq-btn quiet" type="button" data-action="close-sheet">إلغاء</button>
      </div>
    </div>
  </div>`;
}

// M07_SupplierSelection — node 27:279.
// The subtitle names the items without the city glued into them, then the city once.
function reviewTitle(city) {
  const label = cityLabel(city);
  const names = (state.needs || []).filter((item) => item.on).map(needLabel);
  const strip = (text) => {
    let value = String(text || "");
    for (const word of [label, city].filter(Boolean)) value = value.split(word).join(" ");
    return value.replace(/\s+(?:في|بال|ب)?\s*$/, "").replace(/\s+/g, " ").trim();
  };
  const items = (names.length ? names : [state.originalText || state.query]).map(strip).filter(Boolean);
  return [...new Set(items)].join(" + ") + (label ? ` · ${label}` : "");
}

function renderReview() {
  const chosen = [...state.selected.entries()];
  const city = customerCity();
  const ready = chosen.length > 0 && Boolean(city) && !state.busy;
  const extra = state.reviewExtra === true;
  const cap = sellerCap();
  return `${fqHead({ title: "اختيار الموردين", back: "back-results" })}
  <section class="fq-body tight">
    <div><h1 class="fq-h2">اختر من تبي نطلب منهم سعر</h1>
      <p class="fq-lead"><bdi>${esc(reviewTitle(city))}</bdi></p></div>
    ${Number.isFinite(cap) ? `<div class="fq-capbanner">تقدر تختار حتى ${esc(suppliers(cap))} لكل بند</div>` : ""}
    <div style="display:flex;justify-content:space-between;align-items:center;gap:12px">
      <button class="fq-link" type="button" data-action="toggle-extra" aria-expanded="${extra}">${extra ? "إخفاء" : "إضافة"} ملاحظة أو مرفقات (اختياري)</button>
      <span class="fq-count-pill">تم اختيار: ${formatCount(chosen.length)}${Number.isFinite(cap) ? ` من ${formatCount(cap)}` : ""}</span></div>
    ${extra
      ? `<div class="fq-card pad">
          <div class="fq-field"><label for="note">ملاحظة</label><div class="fq-inp" style="min-height:80px;align-items:flex-start"><textarea id="note" rows="2" maxlength="500" placeholder="مثلاً: التسريب تحت المغسلة، والأفضل الصباح">${esc(state.note)}</textarea></div></div>
          <div class="fq-pills">
            <label class="fq-pill" style="display:inline-flex;align-items:center;gap:6px">${ic("camera", 16)}<span>صورة</span><input type="file" accept="image/*" data-action="add-files" hidden></label>
            <label class="fq-pill" style="display:inline-flex;align-items:center;gap:6px">${ic("paperclip", 16)}<span>ملف PDF</span><input type="file" accept="application/pdf" data-action="add-files" hidden></label>
          </div>
          ${filePreview()}
        </div>`
      : ""}
    <div class="fq-stagger" style="display:flex;flex-direction:column;gap:12px">
      ${chosen
        .map(([key, result]) => {
          const seller = sellerOf(result);
          const who = displayName(seller, result);
          const need = multiNeed() ? needOf(result) : "";
          const age = adAge(result.ad);
          return `<article class="fq-card fq-seller picked">
            <div class="fq-row" style="align-items:flex-start">
              <button class="fq-tick round on" type="button" data-action="unselect" data-key="${esc(key)}" aria-pressed="true" aria-label="إزالة ${esc(who.name)}">${ic("check", 14)}</button>
              <span style="flex:1;min-width:0;display:flex;flex-direction:column;gap:6px;align-items:flex-start">
                <strong style="font-size:17px;font-weight:700"><bdi>${esc(who.name)}</bdi></strong>
                ${who.handle ? `<span class="fq-meta fq-handle" dir="auto">${esc(who.handle)}</span>` : ""}
                <span class="fq-meta">${esc(cityLabel(result.ad?.city || seller.city || "") || "")}</span>
                <span class="fq-cardtags">
                  ${need ? `<span class="fq-tag deep"><bdi>${esc(need)}</bdi></span>` : ""}
                  ${age ? `<span class="fq-tag${age.old ? " warn" : ""}">${age.old ? "إعلان قديم · " : ""}${esc(age.label)}</span>` : ""}
                </span>
              </span>
              <span class="fq-av" style="width:48px;height:48px;border-radius:24px;background:var(--fq-light);color:var(--fq-deep);font-size:18px">${initial(who.name)}</span>
            </div>
          </article>`;
        })
        .join("")}
    </div>
    ${city ? "" : `<div class="fq-card pad"><h2 class="fq-h2" style="font-size:17px">في أي مدينة؟</h2>
      <div class="fq-pills" style="gap:10px">${state.cities.map((item) => `<button class="fq-chip" type="button" data-action="pick-city" data-city="${esc(item.value)}">${esc(item.label)}</button>`).join("")}</div></div>`}
    ${state.notice ? `<p class="fq-small" role="alert" style="color:#b3402a">${esc(state.notice)}</p>` : ""}
    <div class="fq-sticky"><button class="fq-btn" type="button" data-action="send" ${ready ? "" : "disabled"}>أرسل طلب التسعير</button></div>
  </section>
  ${state.capSheet ? capSheet() : ""}`;
}

// FT04_SupplierLimitReached — node 64:611. Every plan has its own sellers-per-item cap,
// so this sheet names the plan the customer is actually on.
function capSheet() {
  const cap = allowance();
  const paid = cap.subscribed;
  return `<div class="fq-scrim" data-action="close-cap">
    <div class="fq-sheet" role="dialog" aria-label="حد الموردين لكل بند">
      <span class="fq-grab" aria-hidden="true"></span>
      <div><h2>${paid ? `وصلت لحد باقة «${esc(cap.plan_name)}»` : "وصلت لحد التجربة المجانية"}</h2>
        <p class="fq-lead">${paid ? "باقتك تسمح" : "التجربة المجانية تسمح"} لك بإرسال طلب التسعير إلى ${formatCount(cap.sellers_per_item)} موردين كحد أقصى لكل بند لمقارنة أفضل الأسعار.</p></div>
      <div class="fq-actions">
        <button class="fq-btn" type="button" data-action="close-cap">متابعة بـ ${formatCount(cap.sellers_per_item)} موردين</button>
        <button class="fq-btn ghost" type="button" data-action="show-plans">${paid ? "ترقية الباقة" : "عرض الباقات"}</button>
      </div>
    </div>
  </div>`;
}

// M09_SendingProcessing — node 85:338.
function renderSending() {
  const total = state.sendingTo || state.selected.size;
  // What actually happens here: the request is recorded and joins the one sending queue.
  // No invented "geographic scope" steps - the honest line, and the wait it implies.
  return `${fqHead({ title: "إرسال الطلب" })}
  <section class="fq-body center on-soft-mint" aria-live="polite" style="padding-top:80px">
    <div class="fq-sendring spin"><span>فرق</span></div>
    <div style="display:flex;flex-direction:column;gap:14px">
      <h1 class="fq-h1">نسجّل طلبك...</h1>
      <p class="fq-lead">طلبك إلى ${esc(suppliers(total))} في ${esc(cityLabel(customerCity()) || "مدينتك")} يدخل طابور الإرسال، ويوصلهم واحدًا واحدًا خلال دقائق. نبلغك أول ما يرد أحدهم.</p></div>
    <p class="fq-meta" style="margin-top:auto">تقدر تغلق التطبيق؛ الإرسال يكمل من عندنا.</p>
  </section>`;
}

// M10_RequestSent — node 27:374.
function renderSent() {
  const info = state.sentInfo || {};
  return `${fqHead({ title: "تم الإرسال" })}
  <section class="fq-body center">
    <div class="fq-blob land">${ic("check", 56)}</div>
    <div><h1 class="fq-h1">تم إرسال طلبك!</h1>
      <p class="fq-lead">${(info.needs || []).length > 1 ? `${formatCount(info.needs.length)} طلبات، كل بند بمحادثته، في طريقها إلى ${esc(suppliers(info.sellers || 0))}.` : `طلب التسعير في طريقه إلى ${esc(suppliers(info.sellers || 0))}.`} نبلغك أول ما يوصلك رد.</p></div>
    <div class="fq-card pad fq-facts" style="width:100%">
      <div class="fq-row"><span class="fq-meta">عدد الموردين</span><strong>${formatCount(info.sellers || 0)}</strong></div>
      <hr class="fq-line">
      ${(info.needs || []).length > 1
        ? info.needs.map((row) => `<div class="fq-row"><span class="fq-meta"><bdi>${esc(row.need || "")}</bdi></span><strong>${esc(suppliers(row.sellers))}</strong></div>`).join('<hr class="fq-line">')
        : `<div class="fq-row"><span class="fq-meta">المطلوب تسعيره</span><strong><bdi>${esc(info.need || state.originalText || state.query || "")}</bdi></strong></div>`}
      <hr class="fq-line">
      <div class="fq-row"><span class="fq-meta">الحالة</span><strong style="color:var(--fq-success)">بانتظار الإرسال والردود</strong></div>
    </div>
    ${state.pushAsk ? `<div class="fq-card pad fq-askcard" style="width:100%;gap:10px">
      <div style="display:flex;align-items:center;gap:10px"><span class="fq-bell small">${ic("bell", 20)}</span><strong>نبلغك أول ما يوصلك عرض؟</strong></div>
      <p class="fq-small fq-muted" style="margin:0">تنبيه فوري عند وصول رسالة أو عرض سعر على طلبك.</p>
      <div style="display:flex;gap:8px"><button class="fq-btn sm success r14" type="button" data-action="enable-notify" style="flex:1">تفعيل</button><button class="fq-btn sm soft r14" type="button" data-action="dismiss-notify" style="flex:1">لاحقًا</button></div>
    </div>` : ""}
    <div class="fq-actions" style="width:100%;margin-top:auto">
      <button class="fq-btn" type="button" data-action="open-sent">طلباتي</button>
      <button class="fq-btn ghost" type="button" data-action="home">العودة للرئيسية</button>
    </div>
  </section>
  ${fqNav("home")}`;
}

// Phone notifications when a supplier replies. iPhone only allows them for a site added to the home screen.
const pushSupported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const isStandalone = window.matchMedia?.("(display-mode: standalone)").matches || navigator.standalone === true;
const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent);

function notifyBanner() {
  if (state.pushState === "on" || state.pushDismissed) return "";
  if (isIOS && !isStandalone) {
    return `<div class="fq-banner"><span style="flex:1">تبي تنبيه لما يردون عليك؟ أضف تسعير للشاشة الرئيسية وافتحه من هناك.</span>
      <button type="button" data-action="dismiss-notify" aria-label="إغلاق">✕</button></div>`;
  }
  if (!pushSupported || typeof Notification === "undefined" || Notification.permission === "denied") return "";
  if (Notification.permission === "granted" && state.pushState !== "off") return "";
  return `<div class="fq-banner">${ic("bell", 16)}<span style="flex:1">تبي تنبيه لما يردون عليك؟</span>
    <button type="button" data-action="enable-notify">تفعيل التنبيهات</button></div>`;
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
  if (!state.token || state.view === "thread" || state.view === "compare" || document.visibilityState !== "visible") return;
  try {
    const data = await api("/v1/requests", { quiet: true });
    const changed = JSON.stringify(data.requests || []) !== JSON.stringify(state.requests);
    const was = state.unreadTotal;
    state.requests = data.requests || [];
    setUnread(state.requests);
    // Something arrived while the customer was on another screen.
    const newest = state.requests.map((item) => item.last_message_at || "").sort().pop() || "";
    if (state.unreadTotal > was) cueOnce(`inbox:${newest}`, "offer", 12);
    if (changed && (state.view === "requests" || state.view === "notifications")) render();
  } catch (_error) {}
}

// R01_MyRequests / R01_MyRequests_Empty — nodes 37:9 and 37:119.
// The status comes from what actually happened: messages still queued, delivered, failed, and
// the replies that came back — never «all offers received» for a request nobody has seen yet.
function requestStatus(item) {
  if (item.awarded_seller_id) return { tone: "ok", label: "✓ تم اختيار أفضل عرض" };
  const total = item.recipient_count || 0;
  const replied = item.replied_count || 0;
  const sent = item.sent_count ?? total;
  const failed = item.failed_count || 0;
  const queued = item.queued_count ?? Math.max(0, total - sent - failed);
  if (replied && total && replied >= total) return { tone: "ok", label: "ردّ كل الموردين" };
  if (replied) return { tone: "ok", label: `ردّ ${formatCount(replied)} من ${formatCount(total)}` };
  if (total && failed >= total) return { tone: "danger", label: "ما وصل الطلب للموردين" };
  if (!sent && queued) return { tone: "warn", label: "بانتظار الإرسال" };
  if (queued) return { tone: "warn", label: `وصل لـ ${formatCount(sent)} من ${formatCount(total)}` };
  return { tone: "warn", label: "بانتظار الردود" };
}

function renderRequests() {
  const head = fqHead({ title: "طلباتي", mark: true });
  if (state.requestsLoading && !state.requests.length) {
    const row = `<div class="fq-card"><span class="fq-skel" style="height:20px;width:45%;border-radius:8px"></span><span class="fq-skel" style="height:16px;width:80%;border-radius:8px"></span></div>`;
    return `${head}<section class="fq-body" aria-busy="true">${row.repeat(4)}</section>${fqNav("requests")}`;
  }
  if (!state.requests.length) {
    return `${head}<section class="fq-body center">
      <div class="fq-blob" style="width:110px;height:110px">${ic("clipboard", 48)}</div>
      <div><h1 class="fq-h2">ما عندك طلبات حتى الآن</h1><p class="fq-lead">اكتب اللي تحتاجه وخل فرق يجمع لك العروض.</p></div>
      <button class="fq-btn" type="button" data-action="home" style="margin-top:8px;height:56px">إنشاء أول طلب</button>
    </section>${fqNav("requests")}`;
  }
  const filter = state.requestFilter || "all";
  const match = (item) => {
    if (filter === "awarded") return Boolean(item.awarded_seller_id);
    if (filter === "active") return !item.awarded_seller_id;
    if (filter === "done") return Boolean(item.awarded_seller_id);
    return true;
  };
  const pill = (key, label) => `<button class="fq-pill${filter === key ? " on" : ""}" type="button" data-action="req-filter" data-filter="${key}">${label}</button>`;
  return `${head}<section class="fq-body tight">
    <div class="fq-row"><span class="fq-small" style="font-weight:600">تابع عروضك وطلباتك من مكان واحد</span>
      <button class="fq-addbtn" type="button" data-action="home">${ic("plus", 14)}طلب جديد</button></div>
    <div class="fq-pills" style="justify-content:flex-start">${pill("all", "الكل")}${pill("active", "نشطة")}${pill("awarded", "تمت الترسية")}${pill("done", "مكتملة")}</div>
    <div class="fq-stagger" style="display:flex;flex-direction:column;gap:12px">
      ${state.requests.filter(match).map((item) => {
        const need = item.need || item.original_text;
        const status = requestStatus(item);
        const unread = item.unread_count || 0;
        const parts = [suppliers(item.recipient_count || 0)];
        if (item.replied_count) parts.push(`${plural(item.replied_count, ["رد واحد", "ردّين", "ردود", "رد"])}`);
        else parts.push("ما وصلت ردود بعد");
        // only suppliers the request actually reached can be «reviewing» it
        const reached = item.sent_count ?? item.recipient_count ?? 0;
        const waiting = Math.max(0, reached - (item.replied_count || 0));
        const queued = item.queued_count || 0;
        // One shared sending account, one contact every 20 seconds: the wait is the queue
        // ahead plus this request's own suppliers, said plainly instead of «يراجع طلبك».
        const eta = queued ? Math.max(1, Math.ceil(((item.queue_ahead || 0) + queued) * 20 / 60)) : 0;
        const live = item.awarded_seller_id
          ? ""
          : queued && !item.replied_count
            ? `<div class="fq-live wide"><span class="fq-pulse" aria-hidden="true"></span><span>في طابور الإرسال إلى ${esc(suppliers(queued))} · يوصلهم خلال ${eta === 1 ? "دقيقة تقريبًا" : `~${formatCount(eta)} دقائق`}</span></div>`
            : waiting
              ? `<div class="fq-live wide"><span class="fq-pulse" aria-hidden="true"></span><span>${waiting === 1 ? "مورد واحد يراجع" : `${esc(suppliers(waiting))} يراجعون`} طلبك</span></div>`
              : "";
        return `<button class="fq-card" type="button" data-action="thread" data-id="${esc(item.id)}" aria-label="افتح محادثة ${esc(need)}">
          ${live}
          <div class="fq-row">
            <span style="display:flex;align-items:center;gap:8px">
              ${unread ? `<span style="width:6px;height:6px;border-radius:50%;background:var(--fq-success)"></span>` : ""}
              <strong style="font-size:15px;font-weight:700"><bdi>${esc(need)}</bdi></strong>
              <span class="fq-meta" style="font-size:11px;color:var(--fq-muted)">${esc(ago(item.last_message_at || item.created_at))}</span>
            </span>
            <span style="color:var(--fq-muted);display:grid;place-items:center">${ic("forward", 16)}</span>
          </div>
          <div class="fq-row">
            <span class="fq-tag ${status.tone}">${esc(status.label)}</span>
            <span style="display:flex;align-items:center;gap:8px">
              <span class="fq-meta">${esc(parts.join(" · "))}</span>
              ${unread ? `<span class="fq-unread">${formatCount(unread)} رسائل جديدة</span>` : ""}
            </span>
          </div>
          ${item.latest_offer_amount != null
            ? `<hr class="fq-line"><div style="display:flex;justify-content:flex-start"><strong style="font-size:13px;color:var(--fq-deep)">أقل عرض ${esc(money(item.latest_offer_amount))}</strong></div>`
            : ""}
        </button>`;
      }).join("")}
    </div>
  </section>${fqNav("requests")}`;
}

function snippet(text, size = 50) {
  const value = String(text || "").replace(/\s+/g, " ").trim();
  return value.length > size ? `${value.slice(0, size)}…` : value;
}

const CHAT_ZONE = "Asia/Riyadh";
const chatClock = new Intl.DateTimeFormat("ar-SA-u-nu-latn", { hour: "numeric", minute: "2-digit", timeZone: CHAT_ZONE });
const chatDate = new Intl.DateTimeFormat("ar-SA-u-nu-latn", { day: "numeric", month: "long", timeZone: CHAT_ZONE });
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

function messagePrice(message) {
  return message.offer?.total_price ?? message.offer?.amount ?? null;
}

// «@» suggests whoever answered most recently first; suppliers who never replied come last.
function byLatestReply(thread) {
  const latest = new Map();
  for (const message of thread?.messages || []) {
    if (message.sender_role === "seller" && message.seller_id) latest.set(message.seller_id, message.created_at);
  }
  return (thread?.recipients || [])
    .map((item) => ({ seller_id: item.seller_id, repliedAt: latest.get(item.seller_id) || "" }))
    .sort((a, b) => (b.repliedAt || "").localeCompare(a.repliedAt || ""));
}

// Haraj usernames arrive decorated: emoji, stars, dashes, phone numbers, ALL CAPS. Tidy them for reading,
// without renaming anyone: the supplier keeps his name, just legible.
const NAME_NOISE = /[\u{1F300}-\u{1FAFF}\u{2190}-\u{2BFF}\u{FE0F}\u{2600}-\u{27BF}]/gu;
function tidyName(raw) {
  let name = String(raw || "").replace(NAME_NOISE, " ");
  name = name.replace(/[ـ_|•·●◆★☆✦✿❀~^]+/g, " ");
  name = name.replace(/\b0\d{8,9}\b|\+9665\d{8}/g, " ");
  name = name.replace(/\s+/g, " ").replace(/^[\s\-–—.,،/\\]+|[\s\-–—.,،/\\]+$/g, "").trim();
  name = name.replace(/\b[a-z]+\b/g, (word) => word[0].toUpperCase() + word.slice(1));
  name = name.replace(/\b[A-Z]{4,}\b/g, (word) => word[0] + word.slice(1).toLowerCase());
  return name || "مورد";
}

function sellerIndex(thread, sellerId) {
  return [...new Set((thread?.recipients || []).map((item) => item.seller_id))].indexOf(sellerId);
}

function sellerName(thread, sellerId) {
  return tidyName((thread?.recipients || []).find((item) => item.seller_id === sellerId)?.seller_name || "مورد");
}

// One colour per supplier, from the Farq token palette, handed out in the request's order so no two
// suppliers in the same conversation share one.
const SELLER_COLORS = ["#0B6A63", "#22577A", "#DC6E41", "#C7911E", "#248F5C", "#22162B", "#065656", "#BE5532"];
// C01_UnifiedConversation — node 77:1092. Each supplier keeps one tone: avatar tint, name ink,
// bubble tint and bubble edge. The first four are the tones drawn in Figma; the rest follow the
// same recipe so a conversation with more than four suppliers never repeats one.
const SELLER_TONES = [
  { av: "#b2dfdb", ink: "#00796b", bub: "#f0f9f8", edge: "rgba(0,150,136,0.35)" },
  { av: "#cfd8dc", ink: "#37474f", bub: "#f2f4f5", edge: "rgba(69,90,100,0.35)" },
  { av: "#e8dcc8", ink: "#6d5b3e", bub: "#f6f4f1", edge: "rgba(161,136,100,0.35)" },
  { av: "#d7cee0", ink: "#5c4a6e", bub: "#f5f3f7", edge: "rgba(120,100,140,0.35)" },
  { av: "#cfe3f5", ink: "#2b5f8e", bub: "#f1f6fb", edge: "rgba(43,95,142,0.35)" },
  { av: "#f5d9cf", ink: "#8e4b2f", bub: "#fbf3f0", edge: "rgba(142,75,47,0.35)" },
  { av: "#d9e8cf", ink: "#4a6e34", bub: "#f4f8f1", edge: "rgba(74,110,52,0.35)" },
  { av: "#f0dfc0", ink: "#7a5b1c", bub: "#faf6ec", edge: "rgba(122,91,28,0.35)" },
];

function sellerTone(thread, sellerId) {
  const ids = [...new Set((thread?.recipients || []).map((item) => item.seller_id))];
  const index = ids.indexOf(sellerId);
  if (index >= 0) return SELLER_TONES[index % SELLER_TONES.length];
  let hash = 0;
  for (const char of String(sellerId || "")) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return SELLER_TONES[hash % SELLER_TONES.length];
}

function sellerColor(thread, sellerId) {
  return sellerTone(thread, sellerId).ink;
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
  const time = `<span class="fq-time">${esc(chatTime(message.created_at))}${mine ? deliveryTick(message) : ""}</span>`;
  const quote = quoted
    ? `<div class="fq-quote" style="--who:${quoted.sender_role === "seller" ? sellerColor(thread, quoted.seller_id) : "#575172"}"><strong>${esc(quoted.sender_role === "seller" ? sellerName(thread, quoted.seller_id) : "أنت")}</strong><span>${esc(snippet(quoted.body, 70))}</span></div>`
    : "";
  if (mine) {
    const some = message.scope === "some_sellers" ? (message.deliveries || []).map((item) => sellerName(thread, item.seller_id)).join("، ") : "";
    const only = message.scope === "single_seller" && message.seller_id ? sellerName(thread, message.seller_id) : some;
    const badge = group && only
      ? `<span class="fq-private">${ic("lock", 10)}خاص — إلى ${esc(only)}${message.scope === "single_seller" ? " فقط" : ""}</span>`
      : "";
    const failed = message.delivery_state === "failed" ? `<span class="fq-time" style="color:#b3402a">ما وصلت الرسالة</span>` : "";
    const latest = thread.messages?.filter((item) => item.sender_role !== "seller").slice(-1)[0];
    const progress = latest && latest.id === message.id ? deliveryProgress(message) : "";
    return `<div class="fq-msg mine${first ? " first" : ""}" data-message="${esc(message.id)}"><div style="display:flex;flex-direction:column;gap:4px;align-items:flex-start;min-width:0">
      ${badge}
      <div class="fq-mine-bub${first ? " tail" : ""}">${quote}${media}${body ? `<p>${body}</p>` : ""}${progress ? `<span class="fq-time">${esc(progress)}</span>` : ""}${failed}${time}</div>
    </div></div>`;
  }
  const tone = sellerTone(thread, message.seller_id);
  const who = sellerName(thread, message.seller_id);
  const price = messagePrice(message);
  const cheapest = price != null && best != null && price === best;
  const won = thread.awarded_seller_id && thread.awarded_seller_id === message.seller_id;
  const offer = price != null
    ? `<div class="fq-offer" style="color:${tone.ink}">
        <div class="head">${cheapest ? `<span class="low">الأقل حاليًا</span>` : "<span></span>"}<span class="amount">${esc(money(price))}</span></div>
        ${body ? `<p class="fq-offer-note">${body}</p>` : ""}
      </div>
      <div class="fq-offer-foot"><span class="fq-time">${esc(chatTime(message.created_at))}
        <button class="fq-replybtn" type="button" data-action="reply" data-message="${esc(message.id)}" aria-label="ردّ على ${esc(who)}">ردّ</button></span>
        <span style="color:${tone.ink}">عرض السعر المقدم ⚡</span></div>`
    : "";
  // The sender's name sits inside the bubble in his colour, as a group chat draws it; the
  // bubble itself is white, and only the first of a run carries the tail and the avatar.
  const name = first
    ? `<button class="fq-who" type="button" data-action="seller-filter" data-seller="${esc(message.seller_id || "")}" style="color:${tone.ink}"><bdi>${esc(who)}</bdi>${won ? " ✓" : ""}</button>`
    : "";
  return `<div class="fq-msg${first ? " first" : ""}" data-message="${esc(message.id)}">
    <span class="fq-av" style="background:${tone.av};color:${tone.ink};visibility:${first ? "visible" : "hidden"}">${initial(who)}</span>
    <div class="fq-grp">
      <div class="fq-bub${first ? " tail" : ""}" style="--who:${tone.ink}">
        ${name}${quote}${offer || `${media}${body ? `<p>${body}</p>` : ""}
          <span class="fq-time">${esc(chatTime(message.created_at))}
            <button class="fq-replybtn" type="button" data-action="reply" data-message="${esc(message.id)}" aria-label="ردّ على ${esc(who)}">${ic("reply", 12)}<span>ردّ</span></button></span>`}
      </div>
    </div>
  </div>`;
}

function waMessages(thread, messages, group) {
  const byId = new Map((thread.messages || []).map((item) => [item.id, item]));
  const prices = currentOffers(thread).map((item) => item.total_price);
  const best = prices.length > 1 ? Math.min(...prices) : null;
  let lastDay = "";
  let lastSender = "";
  return messages
    .map((message) => {
      const day = chatDay(message.created_at);
      const divider = day && day !== lastDay && day !== "اليوم" ? `<div class="fq-sys">${esc(day)}</div>` : "";
      const sender = message.sender_role === "seller" ? `s:${message.seller_id}` : "me";
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
// The offers, side by side: sorted by price, each with the gap to the cheapest, and one tap to pick a winner.
// O01_CompareOffers — node 39:180. Its own screen, reached from the conversation.
// O01_CompareOffers — node 39:180, with the savings ring, the gap chip and the badges
// the September update added.
// A supplier who revises his price has one offer on the table: the latest. Comparisons run
// between those, and within one item.
function currentOffers(thread) {
  const latest = new Map();
  for (const offer of thread?.offers || []) {
    if (offer.total_price == null) continue;
    latest.set(`${offer.seller_id}|${offer.need || ""}`, offer);
  }
  return [...latest.values()];
}

function renderCompare() {
  const thread = state.thread;
  if (!thread) return renderRequests();
  const offers = currentOffers(thread).sort((a, b) => a.total_price - b.total_price);
  const cheapest = offers[0]?.total_price ?? 0;
  const dearest = offers[offers.length - 1]?.total_price ?? 0;
  const awarded = thread.awarded_seller_id;
  const chat = `<button class="fq-ibtn light" type="button" data-action="all-sellers" aria-label="المحادثة">${ic("message-circle", 20)}</button>`;
  return `${fqHead({ title: "قارن العروض", sub: `${offersCount(offers.length)} · ${thread.need || thread.original_text || ""}`, back: "back-thread", end: chat })}
  <section class="fq-body tight fq-stagger fq-faceoff">
    ${offers.length ? "" : `<div class="fq-body center"><div class="fq-blob warn">${ic("tag", 48)}</div><h2 class="fq-h2">ما وصلت عروض بأسعار بعد</h2><p class="fq-lead">أول ما يرسل مورد سعرًا يظهر هنا للمقارنة.</p></div>`}
    ${offers
      .map((offer, index) => {
        const won = awarded && awarded === offer.seller_id;
        const gap = offer.total_price - cheapest;
        const peers = offers.filter((item) => (item.need || "") === (offer.need || ""));
        const peerTop = peers.length > 1 ? Math.max(...peers.map((item) => item.total_price)) : 0;
        const saving = peerTop > offer.total_price ? Math.round(((peerTop - offer.total_price) / peerTop) * 100) : 0;
        const best = index === 0;
        return `<article class="fq-cmp-card${best ? " best" : ""}">
          ${best ? `<span class="fq-valuetag">أفضل قيمة</span>` : ""}
          <div class="top">
            <span style="display:flex;flex-direction:column;gap:8px;align-items:flex-start">
              <span class="amount">${esc(money(offer.total_price))}</span>
              <span style="display:flex;align-items:center;gap:6px">
                ${best
                  ? offers.length > 1 ? `<span class="fq-delta">أوفر بـ ${esc(money(dearest - offer.total_price))}</span><span class="fq-tag deep">الأقل</span>` : ""
                  : `<span class="fq-delta up">+${esc(money(gap))}</span>`}
              </span>
              ${saving ? scoreRing(saving, "وفّر") : ""}
            </span>
            <span style="display:flex;align-items:center;gap:8px;flex:1;justify-content:flex-end">
              ${best ? `<span class="fq-best">أفضل سعر</span>` : ""}
              ${won ? `<span class="fq-tag ok">الفائز</span>` : ""}
              <span class="who"><bdi>${esc(sellerName(thread, offer.seller_id))}</bdi></span>
            </span>
          </div>
          <hr class="fq-line">
          <div class="fq-inc">
            <span><span class="y">✓</span>${best ? "أقل عرض وصل" : `أغلى بـ ${esc(money(gap))} عن الأقل`}</span>
            ${offer.delivery_included ? `<span><span class="y">✓</span>شامل التوصيل</span>` : ""}
            ${offer.note ? `<span><span class="y">✓</span><bdi>${esc(snippet(offer.note, 42))}</bdi></span>` : ""}
          </div>
          <hr class="fq-line">
          <div class="fq-row"><span class="fq-meta">${esc(ago(offer.created_at) ? `وصل ${ago(offer.created_at)}` : "")}</span>
            <span class="fq-meta">${offer.currency && offer.currency !== "SAR" ? esc(offer.currency) : ""}</span></div>
          <div class="fq-cmp-actions">
            ${won
              ? `<button class="fq-btn ghost" type="button" disabled>تمت الترسية</button>`
              : `<button class="fq-btn success" type="button" data-action="pick-winner" data-seller="${esc(offer.seller_id)}" data-price="${esc(String(offer.total_price))}">اختيار هذا العرض</button>`}
            <button class="fq-btn ghost" type="button" data-action="seller-filter" data-seller="${esc(offer.seller_id)}">مراسلته</button>
          </div>
        </article>`;
      })
      .join("")}
  </section>
  ${state.awardPick ? awardSheet(thread, state.awardPick) : ""}`;
}

// A01_AwardConfirmation — node 37:243.
function awardSheet(thread, pick) {
  const who = sellerName(thread, pick.sellerId);
  return `<div class="fq-scrim" data-action="cancel-award">
    <div class="fq-sheet" role="dialog" aria-label="ترسية الطلب">
      <span class="fq-grab" aria-hidden="true"></span>
      <div><h2 style="font-size:24px">ترسية الطلب</h2><p class="fq-lead">راجع العرض قبل التأكيد</p></div>
      <div class="fq-card flat fq-awardcard">
        <div class="fq-row"><span class="fq-price won">${esc(money(pick.price))}</span><strong style="font-size:18px"><bdi>${esc(who)}</bdi></strong></div>
        <hr class="fq-line">
        <div class="fq-row"><span class="fq-meta">${esc(thread.need || thread.original_text || "")}</span><span class="fq-meta">${esc(cityLabel(thread.city) || "")}</span></div>
      </div>
      <div class="fq-notice">بعد تأكيد الترسية سيتم اعتماد هذا العرض وإغلاق المنافسة على بقية الموردين.</div>
      <div class="fq-actions">
        <button class="fq-btn success" type="button" data-action="confirm-award" ${state.busy ? "disabled" : ""}>${state.busy ? "لحظة…" : `تأكيد الترسية على ${esc(who)}`}</button>
        <button class="fq-btn ghost" type="button" data-action="cancel-award">رجوع</button>
      </div>
    </div>
  </div>`;
}

// A02_AwardSuccess — node 37:289.
function renderAwarded() {
  const thread = state.thread;
  if (!thread) return renderRequests();
  const who = sellerName(thread, thread.awarded_seller_id);
  const offer = (thread.offers || []).find((item) => item.seller_id === thread.awarded_seller_id);
  return `${fqHead({ title: "تمت الترسية", mark: true })}
  <section class="fq-body center">
    <div class="fq-celebrate">
      <span class="fq-glow" style="width:320px;height:320px" aria-hidden="true"></span>
      <span class="fq-glow" style="width:220px;height:220px" aria-hidden="true"></span>
      <span class="fq-ringstack"><span class="ring">${ic("check", 28)}</span></span>
    </div>
    <div><h1 class="fq-h1">تمت الترسية!</h1><p class="fq-lead">تم اعتماد هذا العرض وأرسلنا للمورد إشعار القبول.</p></div>
    <div class="fq-card pad fq-wincard" style="width:100%;align-items:center;text-align:center">
      <strong style="font-size:18px"><bdi>${esc(who)}</bdi></strong>
      ${offer?.total_price != null ? `<span class="fq-price won">${esc(money(offer.total_price))}</span>` : ""}
    </div>
    ${shareContactCard(thread, who)}
    <div class="fq-actions" style="width:100%;margin-top:auto">
      <button class="fq-btn success" type="button" data-action="winner-chat">متابعة المحادثة</button>
      <button class="fq-btn ghost outline" type="button" data-action="open-compare">عرض تفاصيل العرض</button>
    </div>
  </section>
  ${fqNav("requests")}`;
}

// The supplier never gets the customer's phone or place unless the customer hands them over
// here, after choosing him, for this one request — and he can take them back at any time.
// Everything else in the product keeps the two sides talking inside the conversation.
function shareContactCard(thread, who) {
  if (!thread?.awarded_seller_id) return "";
  if (thread.contact_shared) {
    return `<div class="fq-card pad" style="width:100%">
      <div class="fq-row"><span class="fq-tag ok">مُشارَك</span><strong style="font-size:15px">رقمك مع <bdi>${esc(who)}</bdi></strong></div>
      <p class="fq-meta">يقدر يتصل عليك مباشرة لهذا الطلب. تقدر توقف المشاركة في أي وقت.</p>
      <button class="fq-btn ghost r14" type="button" data-action="revoke-contact">إيقاف المشاركة</button>
    </div>`;
  }
  if (!state.shareOpen) {
    return `<div class="fq-card pad" style="width:100%">
      <strong style="font-size:15px">تبي المورد يتصل عليك؟</strong>
      <p class="fq-meta">اختياري. بدونه تكمّلون التنسيق من داخل المحادثة، وما يوصله رقمك.</p>
      <button class="fq-btn ghost outline-deep r14" type="button" data-action="share-contact">شارك رقمي وموقعي</button>
    </div>`;
  }
  return `<form id="share-contact" class="fq-card pad" style="width:100%;gap:12px" novalidate>
    <strong style="font-size:15px">مشاركة بياناتك مع <bdi>${esc(who)}</bdi></strong>
    <div class="fq-field"><label for="share-phone">رقم جوالك</label>
      <div class="fq-inp">${ic("phone", 16)}<input id="share-phone" name="phone" inputmode="tel" placeholder="05xxxxxxxx" dir="ltr" required></div></div>
    <label class="fq-check"><input type="checkbox" name="place" ${state.sharePlace ? "checked" : ""} data-action="share-place"><span>أرسل موقعي الحالي كمان</span></label>
    ${state.shareError ? `<p class="fq-small" style="color:#b3402a">${esc(state.shareError)}</p>` : ""}
    <p class="fq-meta">يُشارَك مع هذا المورد فقط ولهذا الطلب فقط.</p>
    <div class="fq-actions" style="gap:10px">
      <button class="fq-btn r14" type="submit">شارك</button>
      <button class="fq-btn ghost r14" type="button" data-action="share-cancel">إلغاء</button>
    </div>
  </form>`;
}

// The offers summary above the conversation — node 33:159 (C02 underlay, offers-section).
function offersBar(thread, offers) {
  if (!offers.length) return "";
  // (the bar sits above the group conversation — TSR-077: comparing is one visible tap away)
  const sorted = [...offers].sort((a, b) => a.total_price - b.total_price);
  const low = sorted[0].total_price;
  const high = sorted[sorted.length - 1].total_price;
  const top = sorted.slice(0, 3);
  return `<div class="fq-offers-bar">
    <div class="fq-row">
      <button class="fq-pill on" type="button" data-action="open-compare">قارن العروض</button>
      <span class="fq-small"><b>${esc(offersCount(sorted.length))}</b>${sorted.length > 1 ? ` <span class="fq-meta">· من ${esc(money(low))} إلى ${esc(money(high))}</span>` : ""}</span>
    </div>
    ${top
      .map((offer, index) => `<div class="mini"><span>${esc(money(offer.total_price))}</span>
        <span style="display:flex;align-items:center;gap:6px"><bdi>${esc(sellerName(thread, offer.seller_id))}</bdi>${index === 0 ? `<span class="fq-tag deep">الأقل</span>` : ""}</span></div>`)
      .join("")}
    ${sorted.length > 3 ? `<button class="fq-link" type="button" data-action="open-compare">عرض جميع العروض (${formatCount(sorted.length)})</button>` : ""}
  </div>`;
}

// C02_RecipientPicker — node 33:159. Figma draws radio dots; Taseer keeps the multi-select the
// customer asked for, so «الكل» ticks everyone and each row toggles on its own.
// C02_RecipientPicker — node 33:159. Every recipient is its own card: the mark on the left,
// the name and its price beside it, the control on the right, and a green bar down the left
// edge of the ones this message will reach. Figma draws radios; Taseer keeps the multi-select
// the customer asked for, so «الكل» ticks everyone and each row toggles on its own.
function recipientSheet(thread) {
  const recipients = thread.recipients || [];
  const picked = state.picked || new Set();
  const all = recipients.length > 0 && picked.size === recipients.length;
  const priceOf = (id) => (thread.offers || []).find((item) => item.seller_id === id)?.total_price;
  const row = (on, avatar, name, sub, action, data) =>
    `<button class="fq-rcpt${on ? " on" : ""}" type="button" data-action="${action}" ${data} role="checkbox" aria-checked="${on}">
      ${avatar}
      <span class="meta">
        <strong><bdi>${esc(name)}</bdi></strong>
        ${sub ? `<span class="price">${esc(sub)}</span>` : ""}
      </span>
      <span class="fq-radio${on ? " on" : ""}" aria-hidden="true"></span>
    </button>`;
  return `<div class="fq-scrim" data-action="close-picker">
    <div class="fq-sheet" role="dialog" aria-label="إرسال إلى">
      <span class="fq-grab" aria-hidden="true"></span>
      <div class="fq-row"><h2>إرسال إلى:</h2>
        <button class="fq-link" type="button" data-action="${all ? "pick-none" : "pick-all"}">${all ? "إزالة الكل" : "تحديد الكل"}</button></div>
      <div style="display:flex;flex-direction:column;gap:10px">
        ${row(all, `<span class="fq-av group">${ic("users", 18)}</span>`, `الكل (${formatCount(recipients.length)} مورد)`, "", "pick-all", "")}
        ${recipients
          .map((item) => {
            const tone = sellerTone(thread, item.seller_id);
            const price = priceOf(item.seller_id);
            const name = sellerName(thread, item.seller_id);
            return row(
              picked.has(item.seller_id),
              `<span class="fq-av" style="background:${tone.av};color:${tone.ink}">${initial(name)}</span>`,
              name,
              price != null ? money(price) : "",
              "toggle-recipient",
              `data-seller="${esc(item.seller_id)}"`,
            );
          })
          .join("")}
      </div>
      <button class="fq-btn sm" type="button" data-action="close-picker">تم</button>
    </div>
  </div>`;
}

// C03_AttachmentFlow — node 85:378.
function attachSheet() {
  const option = (glyph, label, accept, capture) =>
    `<label class="fq-attach-opt">${ic(glyph, 24)}<span>${esc(label)}</span>
      <input type="file" accept="${accept}" ${capture} multiple data-action="chat-files" hidden></label>`;
  return `<div class="fq-scrim" data-action="close-attach">
    <div class="fq-sheet" role="dialog" aria-label="إرفاق ملف">
      <span class="fq-grab" aria-hidden="true"></span>
      <h2>إرفاق ملف</h2>
      <div class="fq-attach-grid">
        ${option("image", "معرض الصور", "image/*", "")}
        ${option("camera", "الكاميرا", "image/*", 'capture="environment"')}
        ${option("file-text", "ملف PDF", "application/pdf", "")}
        ${option("folder", "مستند", "application/pdf,image/*", "")}
      </div>
      ${state.chatFiles.length
        ? `<div><p class="fq-small" style="font-weight:600">الملفات المحددة</p>
            <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">${state.chatFiles
              .map((item, index) => `<span class="fq-file">${item.preview ? `<img src="${item.preview}" alt="">` : `<span>PDF</span>`}
                <button type="button" data-action="remove-chat-file" data-index="${index}" aria-label="إزالة">${ic("x", 12)}</button></span>`)
              .join("")}</div></div>`
        : ""}
      <button class="fq-btn sm" type="button" data-action="close-attach" ${state.chatFiles.length ? "" : "disabled"}>إرسال المرفقات</button>
    </div>
  </div>`;
}

// C01_UnifiedConversation (node 77:1092) and, once a winner is picked, C04_PostAwardChat (node 37:372).
function renderThread() {
  const thread = state.thread;
  if (!thread) {
    const known = state.requests.find((item) => item.id === state.pendingThread);
    const title = known?.need || known?.original_text || "المحادثة";
    return `${fqHead({ title, sub: "نحمّل المحادثة…", back: "requests" })}
      <section class="fq-chat" aria-busy="true">
        <div class="fq-msg mine"><div class="fq-skel" style="width:58%;height:70px;border-radius:18px"></div></div>
        <div class="fq-msg"><div class="fq-skel" style="width:66%;height:62px;border-radius:18px"></div></div>
        <div class="fq-msg"><div class="fq-skel" style="width:44%;height:52px;border-radius:18px"></div></div>
      </section>`;
  }
  const recipients = thread.recipients || [];
  const awarded = thread.awarded_seller_id || "";
  // Once a winner is picked the broadcast is over: the item becomes a 1:1 channel with him.
  const one = state.activeSeller || awarded || (recipients.length === 1 ? recipients[0].seller_id : "");
  const group = !one;
  const privateWinner = Boolean(awarded) && one === awarded;
  const messages = (thread.messages || []).filter(
    (message) => !one || message.seller_id === one || (message.deliveries || []).some((item) => item.seller_id === one),
  );
  const allOffers = currentOffers(thread);
  const offers = allOffers.filter((item) => !one || item.seller_id === one);
  const best = [...allOffers].sort((a, b) => a.total_price - b.total_price)[0];
  const need = thread.need || thread.original_text || "المحادثة";
  const title = one ? sellerName(thread, one) : need;
  const sub = one
    ? [need, offers[0]?.total_price != null ? money(offers[0].total_price) : ""].filter(Boolean).join(" · ")
    : [best ? `${sellerName(thread, best.seller_id)} · ${money(best.total_price)}` : "", allOffers.length ? offersCount(allOffers.length) : suppliers(recipients.length)]
        .filter(Boolean)
        .join(" — ");
  const backAction = state.activeSeller && recipients.length > 1 ? "all-sellers" : "requests";
  const menu = `<button class="fq-ibtn" type="button" data-action="thread-menu" aria-label="خيارات">${ic("more-vertical", 18)}</button>`;

  // Who the next message goes to: everyone by default, any subset via C02.
  if (group && (!state.picked || state.pickedFor !== thread.id)) {
    state.picked = new Set(recipients.map((item) => item.seller_id));
    state.pickedFor = thread.id;
  }
  const picked = group ? recipients.filter((item) => state.picked.has(item.seller_id)) : [];
  const target = state.replyTo;
  const nonePicked = group && !target && !picked.length;
  const toLabel = target
    ? `ردّ على ${target.name}`
    : one
      ? `إلى ${sellerName(thread, one)}`
      : picked.length === recipients.length
        ? `إلى الكل (${formatCount(recipients.length)} مورد)`
        : picked.length
          ? `إلى ${picked.length === 1 ? sellerName(thread, picked[0].seller_id) : `${formatCount(picked.length)} موردين`}`
          : "ما اخترت أحد";
  const placeholder = target ? `ردّ على ${target.name}` : group && picked.length === recipients.length ? "اكتب رسالة للجميع..." : "اكتب رسالتك...";

  const pills = group && !awarded
    ? `<div class="fq-filters">
        <button class="fq-fpill all${state.activeSeller ? "" : " on"}" type="button" data-action="all-sellers">الكل</button>
        ${recipients
          .map((item) => {
            const tone = sellerTone(thread, item.seller_id);
            return `<button class="fq-fpill" type="button" data-action="seller-filter" data-seller="${esc(item.seller_id)}">
              <span class="dot" style="background:${tone.ink}"></span><bdi>${esc(sellerName(thread, item.seller_id))}</bdi></button>`;
          })
          .join("")}
      </div>`
    : "";

  const awardedHead = privateWinner
    ? `<div class="fq-offers-bar">
        <div class="fq-row">
          <strong style="font-size:17px;color:var(--fq-deep)">${esc(offers[0]?.total_price != null ? money(offers[0].total_price) : "")}</strong>
          <span style="display:flex;align-items:center;gap:8px"><span class="fq-tag ok">محادثة خاصة</span><strong><bdi>${esc(sellerName(thread, awarded))}</bdi></strong></span>
        </div>
        <hr class="fq-line">
        <button class="fq-link" type="button" data-action="open-compare">عرض تفاصيل العرض</button>
      </div>`
    : "";

  return `${fqHead({ title, sub, back: backAction, backStart: true, end: menu })}
    ${awardedHead}
    ${group && !awarded ? offersBar(thread, allOffers) : ""}
    ${pills}
    <section class="fq-chat${privateWinner ? " won" : ""}" id="chat-wall">
      ${privateWinner ? `<div class="fq-sys">✓ محادثة خاصة مع ${esc(sellerName(thread, awarded))}</div>` : ""}
      ${waMessages(thread, messages, group) || `<div class="fq-sys">بانتظار الرد.</div>`}
    </section>
    <div>
      ${state.notice ? `<div class="fq-target" style="background:#fdeee9;color:#b3402a" role="alert">${esc(state.notice)}<button type="button" data-action="clear-notice" aria-label="إغلاق">${ic("x", 14)}</button></div>` : ""}
      ${target
        ? `<div class="fq-target" style="--who:${sellerColor(thread, target.sellerId)}">${ic("message-square", 14)}<bdi>${esc(target.name)}: ${esc(snippet(target.body, 40))}</bdi><button type="button" data-action="cancel-reply">إلغاء</button></div>`
        : privateWinner
          ? `<div class="fq-target">${ic("lock", 14)}<span>قناة تواصل مغلقة 1:1 مع ${esc(sellerName(thread, awarded))}</span></div>`
          : `<div class="fq-target">${ic("users", 14)}<span>${esc(toLabel)}</span>${group ? `<button type="button" data-action="open-picker">تغيير</button>` : ""}</div>`}
      ${state.chatFiles.length
        ? `<div style="display:flex;gap:8px;padding:8px 16px;background:var(--fq-surface);border-top:1px solid var(--fq-line);overflow-x:auto">${state.chatFiles
            .map((item, index) => `<span class="fq-file">${item.preview ? `<img src="${item.preview}" alt="">` : `<span>PDF</span>`}
              <button type="button" data-action="remove-chat-file" data-index="${index}" aria-label="إزالة">${ic("x", 12)}</button></span>`)
            .join("")}</div>`
        : ""}
      <div class="mention-list" id="mention-list" hidden></div>
      <form class="fq-composer wa" id="user-reply">
        <button class="fq-plus" type="button" data-action="open-attach" aria-label="إرفاق">${ic("plus", 22)}</button>
        <div class="fq-inputg">
          <textarea name="body" rows="1" placeholder="${esc(placeholder)}" ${nonePicked ? "disabled" : ""}></textarea>
          <button class="fq-iconbtn" type="button" data-action="emoji" aria-label="رموز">${ic("smile", 22)}</button>
        </div>
        <button class="fq-send" type="submit" aria-label="إرسال" ${nonePicked || state.sending ? "disabled" : ""}>${state.sending ? `<span class="fq-ring spin" style="width:18px;height:18px;--p:60%"></span>` : ic("send", 18)}</button>
      </form>
      <button class="fq-tobottom" type="button" data-action="chat-bottom" aria-label="إلى آخر المحادثة" hidden>${ic("chevron-down", 20)}</button>
    </div>
    ${state.pickerOpen ? recipientSheet(thread) : ""}
    ${state.attachOpen ? attachSheet() : ""}`;
}

// AC01_Account — node 85:438.
// AC01_Account — node 85:438. The profile sits on a deep-green banner, each entry is its own
// card, and signing out is drawn in red.
function renderAccount() {
  const name = state.account?.name || "حسابي";
  const contact = state.account?.email || "";
  const plan = isSubscribed() ? planName(state.subStatus?.subscription?.plan || "") : trialLabel();
  const item = (label, action, badge = "", danger = false) =>
    `<button class="fq-entry${danger ? " danger" : ""}" type="button" data-action="${action}">
      <span class="lead">${esc(label)}</span>
      ${badge ? `<span class="fq-tag deep">${esc(badge)}</span>` : ""}
      <span class="go">${ic("forward", 16)}</span></button>`;
  const right = `<span style="display:flex;align-items:center;gap:10px"><span class="fq-head-mark">فرق</span>
    <button class="fq-lang" type="button" data-action="lang">${ic("globe", 16)}<span>العربية</span></button></span>`;
  return `${fqHead({ title: "حسابي", start: right })}
  <div class="fq-profile-banner">
    <span class="pic">${initial(name)}</span>
    <span><b><bdi>${esc(name)}</bdi></b><span dir="ltr">${esc(contact)}</span></span>
  </div>
  <section class="fq-body tight fq-stagger unfold">
    ${item("بياناتي", "profile")}
    ${item("اشتراكي", "subscribe", plan)}
    ${item("الإشعارات", "notifications")}
    ${item("إعدادات الإشعارات", "notify-settings")}
    ${item("الدعم والمساعدة", "support")}
    ${item("سياسة الخصوصية", "privacy")}
    ${item("الشروط والأحكام", "terms")}
    ${item("الإلغاء والاسترداد", "refunds")}
    ${isFarqEmbed() ? "" : item("تسجيل الخروج", "sign-out", "", true)}
    <p style="text-align:center;margin:8px 0 0"><span class="fq-chip-version">الإصدار 1.0.0</span></p>
    ${operatorNote()}
  </section>
  ${fqNav("account")}`;
}

function planName(code) {
  const plan = state.subPlans.find((item) => item.code === code);
  return plan ? planLabel(plan) : code;
}

// A placeholder-priced plan carries internal wording ("سعر تجريبي مؤقت", "Sandbox"); the
// customer only ever sees a neutral name for it, never its description or features.
function planLabel(plan) {
  if (!plan?.is_placeholder_price) return plan?.name_ar || "";
  if (plan.duration_days === 30 || plan.duration_days === 31) return "الاشتراك الشهري";
  if (plan.duration_days === 365) return "الاشتراك السنوي";
  return "باقة الاشتراك";
}

function planDetails(plan) {
  return plan?.is_placeholder_price ? { description: "", features: [] } : { description: plan?.description_ar || "", features: plan?.features || [] };
}

// The server says whether a subscription can be bought right now (Moyasar keys set, and a
// plan that may take money with them). Unknown until the plans have loaded.
function paymentsOff() {
  return state.paymentsAvailable === false;
}

// Cards (mada, Visa, Mastercard) always; Apple Pay only where the device offers it.
function payMethodLabel() {
  const applePay = Boolean(window.ApplePaySession && ApplePaySession.canMakePayments && ApplePaySession.canMakePayments());
  return applePay ? "Apple Pay أو البطاقة" : "بطاقة مدى أو ائتمانية";
}

function paymentsOffNote() {
  return `<div class="fq-card pad grey" role="status">
    <h2 class="fq-h2" style="font-size:17px">الاشتراك غير متاح حالياً</h2>
    <p class="fq-lead">نجهّز الاشتراكات المدفوعة. تقدر تكمل استخدام التطبيق كالمعتاد وترجع لها لاحقاً.</p>
  </div>`;
}

function trialLabel() {
  return openAccount() ? "حساب مفتوح" : `التجربة المجانية · ${formatCount(allowance().items_left)} بند متبقٍ`;
}

// N01_NotificationCenter — node 85:4.
const NOTE_KINDS = {
  offer: { glyph: "tag", title: "عرض جديد" },
  lower: { glyph: "arrow-down", title: "عرض أقل" },
  message: { glyph: "message-square", title: "رسالة جديدة" },
  award: { glyph: "check-circle", title: "تمت الترسية" },
  question: { glyph: "help-circle", title: "سؤال من مورد" },
};

function renderNotifications() {
  const list = state.notifications || [];
  const markAll = `<button class="fq-markall" type="button" data-action="read-all">تحديد الكل كمقروء</button>`;
  if (!list.length) {
    return `${fqHead({ title: "الإشعارات", mark: true, end: markAll })}
    <section class="fq-body center">
      <div class="fq-blob">${ic("bell", 48)}</div>
      <div><h1 class="fq-h2">ما فيه إشعارات بعد</h1><p class="fq-lead">أول ما يوصل عرض أو رسالة، تلقاه هنا.</p></div>
    </section>${fqNav("account")}`;
  }
  const groups = [["اليوم", list.filter((item) => chatDay(item.at) === "اليوم")], ["أمس", list.filter((item) => chatDay(item.at) === "أمس")], ["أقدم", list.filter((item) => !["اليوم", "أمس"].includes(chatDay(item.at)))]];
  return `${fqHead({ title: "الإشعارات", mark: true, end: markAll })}
  <section class="fq-body">
    ${groups
      .filter(([, items]) => items.length)
      .map(([label, items]) => `<div style="display:flex;flex-direction:column;gap:12px">
        <p class="fq-sec-title"><span>${esc(label)}</span></p>
        ${items
          .map((note) => {
            const kind = NOTE_KINDS[note.kind] || NOTE_KINDS.message;
            return `<button class="fq-note" type="button" data-action="thread" data-id="${esc(note.request_id || "")}">
              <span class="ic">${ic(kind.glyph, 18)}</span>
              <span class="mid"><span class="kind">${esc(kind.title)} <em>· ${esc(note.need || "")}</em></span>
                <span class="txt">${esc(note.text || "")}</span></span>
              <span class="when">${note.unread ? `<span class="unread"></span>` : ""}${esc(ago(note.at))}</span>
            </button>`;
          })
          .join("")}
      </div>`)
      .join("")}
  </section>
  ${fqNav("account")}`;
}

// N03_NotificationSettings — node 85:145.
const NOTIFY_ROWS = [
  ["messages", "رسائل الموردين", "تنبيه فوري عند وصول رسالة جديدة في المحادثة"],
  ["offers", "عروض جديدة", "تنبيه عند تقديم مورد لعرض سعر جديد لطلبك"],
  ["lower", "عرض أقل", "تنبيه فوري عند وصول عرض سعر أقل من العروض الحالية"],
  ["updates", "تحديثات الطلب", "ترسية الطلب، إغلاق الطلب، أو تحديث حالته الرسمية"],
  ["billing", "الاشتراك والاستخدام", "تذكير بحد الاستخدام الشهري وتجديد باقة العضوية"],
];

function renderNotifySettings() {
  const prefs = state.notifyPrefs || {};
  const on = sound.on;
  return `${fqHead({ title: "إعدادات الإشعارات", back: "account", backStart: true })}
  <section class="fq-body tight">
    <div class="fq-setting">
      <span class="meta">
        <strong>أصوات التطبيق</strong>
        <span class="fq-meta">أصوات خفيفة عند وصول الرسائل والعروض وإتمام العمليات المهمة.</span>
        <button class="fq-link" type="button" data-action="try-sound" style="align-self:flex-start">جرّب الصوت</button>
      </span>
      <button class="fq-switch${on ? " on" : ""}" type="button" data-action="sound-toggle" role="switch" aria-checked="${on}" aria-label="أصوات التطبيق"><span></span></button>
    </div>
    ${NOTIFY_ROWS.map(([key, title, desc]) => {
      const on = prefs[key] !== false;
      return `<div class="fq-setting">
        <span class="meta">
          <strong>${esc(title)}</strong>
          <span class="fq-meta">${esc(desc)}</span>
        </span>
        <button class="fq-switch${on ? " on" : ""}" type="button" data-action="notify-toggle" data-key="${key}" role="switch" aria-checked="${on}" aria-label="${esc(title)}"><span></span></button>
      </div>`;
    }).join("")}
  </section>
  ${fqNav("account")}`;
}

// N02_PushPermission — node 85:101. A modal over whatever screen asked for it.
function pushPrompt() {
  return `<div class="fq-scrim deep" data-action="dismiss-notify">
    <div class="fq-prompt">
      <span class="fq-bell">${ic("bell", 28)}</span>
      <h2>تبغى نبلغك أول ما توصلك عروض؟</h2>
      <p class="fq-lead">نرسل لك تنبيه فوري عند وصول رسائل جديدة أو عروض أسعار منافسة على طلباتك.</p>
      <div class="fq-actions" style="width:100%;gap:12px">
        <button class="fq-btn success r14" type="button" data-action="enable-notify">تفعيل التنبيهات</button>
        <button class="fq-btn soft r14" type="button" data-action="dismiss-notify">لاحقًا</button>
      </div>
    </div>
  </div>`;
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


// Subscription + free trial. Every number shown here comes from the server's own
// entitlement (GET /v1/subscriptions/me -> entitlement), which is the same object
// farq/limits.py enforces with, so the screen can never promise more than the server allows.
// The constants below are only the fallback for a screen drawn before that call returns.
const TRIAL_ITEMS = 10;
const TRIAL_SELLERS = 6;

// An open account has no number worth showing him: "0 من 100,000" reads as a
// misconfiguration, not as freedom. Anywhere a count would be printed, it says «غير محدود».
const OPEN_PLAN = "open";
function openAccount() {
  return allowance().plan === OPEN_PLAN;
}
function formatAllowance(value) {
  return openAccount() ? "غير محدود" : formatCount(value);
}

function allowance() {
  const server = state.subStatus?.entitlement;
  if (server) return server;
  const used = state.requests.length;
  return {
    plan: null,
    plan_name: "التجربة المجانية",
    subscribed: false,
    items: TRIAL_ITEMS,
    items_used: used,
    items_left: Math.max(0, TRIAL_ITEMS - used),
    sellers_per_item: TRIAL_SELLERS,
    daily_contacts: 30,
    contacts_today: 0,
    contacts_left_today: 30,
  };
}

// Arabic has a form for one, a form for two, and a form for the rest.
function items(count) {
  if (count === 1) return "بند واحد";
  if (count === 2) return "بندين";
  if (count >= 3 && count <= 10) return `${formatCount(count)} بنود`;
  return `${formatCount(count)} بند`;
}

function trialUsed() {
  return allowance().items_used;
}

function isSubscribed() {
  return state.subStatus?.status === "active";
}

function planCard(plan, { popular = false } = {}) {
  const price = plan.price_amount != null ? money(plan.price_amount / 100) : "XX ر.س";
  const { description, features } = planDetails(plan);
  const buyable = !paymentsOff() && plan.purchasable !== false;
  return `<article class="fq-plan${popular ? " pop" : ""}">
    <div class="fq-row">
      <span>${popular ? `<span class="badge">الأكثر طلبًا</span>` : ""}</span>
      <span class="name">${esc(planLabel(plan))}</span>
    </div>
    <div class="fq-row"><span></span><span><span class="amount">${esc(String(price).replace(" ر.س", ""))} ر.س</span> <span class="per">/ ${esc(planPeriod(plan.duration_days))}يًا</span></span></div>
    ${description ? `<p class="desc">${esc(description)}</p>` : ""}
    ${features.length ? `<div class="fq-feats">${features.map((f) => `<span class="fq-feat"><span class="y">✓</span>${esc(f)}</span>`).join("")}</div>` : ""}
    ${buyable ? `<button class="fq-btn sm${popular ? "" : " ghost"}" type="button" data-action="choose-plan" data-plan="${esc(plan.code)}">اختيار ${esc(planLabel(plan))}</button>` : ""}
  </article>`;
}

// The allowance card — SUB07/SUB08 and FT01/FT02/FT05/FT06 are all this one card at
// different points in the month. The title leads on the right, the badge answers on the
// left as an outlined capsule, and the bar fills from the left through a gradient whose
// colour says how close the end is.
function usageCard({ title, badge, badgeTone = "ok", price, per, used, limit, foot, warn = false, deep = false }) {
  const pct = limit ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  const hot = badgeTone === "danger";
  const near = badgeTone === "warn";
  const valueColour = hot ? "var(--fq-danger)" : near ? "var(--fq-warning)" : "var(--fq-text)";
  return `<div class="fq-plan">
    <div class="fq-row rtl"><span class="name">${esc(title)}</span><span class="fq-pillbadge ${badgeTone}">${esc(badge)}</span></div>
    <div class="fq-price-line"><span class="amount">${esc(price)}</span> <span class="per">${esc(per)}</span></div>
    <hr class="fq-line">
    <div class="fq-usage">
      <div class="fq-row rtl"><span class="fq-meta">البنود المستخدمة</span><span class="fq-small" style="font-weight:700;color:${valueColour}">${formatCount(used)} من ${formatCount(limit)} بند</span></div>
      <div class="fq-track ${hot ? "danger" : near ? "warn" : deep ? "deep" : "ok"}"><span style="width:${Math.min(pct, 96)}%"></span></div>
      <p class="fq-meta" style="margin:0">${esc(foot)}</p>
    </div>
  </div>`;
}

function renderSubscribe() {
  const view = state.subView || (isSubscribed() ? "my-plan" : "my-plan");
  const plans = state.subPlans || [];
  const popularIndex = plans.length > 1 ? 1 : 0;

  // SUB04_PaymentProcessing — node 60:161.
  if (view === "paying") {
    return `<section class="fq-body center">
      <div class="fq-spinner" aria-hidden="true"></div>
      <div><h1 class="fq-h2">جاري تفعيل اشتراكك...</h1><p class="fq-lead">لا تغلق الصفحة، نجهز لك تجربة البحث الآن.</p></div>
    </section>`;
  }

  // SUB05_PaymentSuccess / FT11_SubscriptionActivated — nodes 60:179 and 64:1114.
  if (view === "paid") {
    return `${fqHead({ title: "تم التفعيل", mark: true })}
    <section class="fq-body center" style="padding-top:60px;padding-bottom:60px">
      <div class="fq-squircle ringed land">${ic("check", 56)}</div>
      <div style="display:flex;flex-direction:column;gap:14px">
        <h1 class="fq-h1">تم تفعيل اشتراكك</h1>
        <p class="fq-lead">باقة: ${esc(planName(state.subStatus?.subscription?.plan || state.subActivePlan))}</p>
        <span class="fq-statepill green">نشط الآن</span></div>
      <div class="fq-actions" style="width:100%;margin-top:auto">
        <button class="fq-btn r14" type="button" data-action="resume-request">إكمال إرسال الطلب</button>
        <button class="fq-btn ghost r14" type="button" data-action="my-plan">تم</button>
      </div>
    </section>`;
  }

  // SUB06_PaymentFailed — node 60:214.
  if (view === "failed") {
    return `${fqHead({ title: "تعذر الدفع", mark: true })}
    <section class="fq-body center" style="padding-top:48px">
      <div class="fq-squircle danger">${ic("alert-triangle", 56)}</div>
      <div style="display:flex;flex-direction:column;gap:20px">
        <h1 class="fq-h1">تعذر إكمال الدفع</h1>
        <p class="fq-lead">${esc(state.subError || "لم يتم خصم قيمة الاشتراك. يرجى مراجعة تفاصيل حسابك أو المحاولة مرة أخرى.")}</p></div>
      <div class="fq-actions" style="width:100%;margin-top:auto;gap:12px">
        <button class="fq-btn r14" type="button" data-action="retry-payment">إعادة المحاولة</button>
        <button class="fq-btn danger-soft r14" type="button" data-action="my-plan">رجوع</button>
      </div>
    </section>`;
  }

  // SUB12_ResumeRequest / FT12 — nodes 62:278 and 64:1148.
  if (view === "resume") {
    return `${fqHead({ title: "تأكيد التفعيل", mark: true })}
    <section class="fq-body" style="padding-top:24px;align-items:center;text-align:center">
      <div class="fq-squircle pale land">${ic("check", 64)}</div>
      <div style="display:flex;flex-direction:column;gap:18px">
        <h1 class="fq-h1">تم تفعيل اشتراكك بنجاح!</h1>
        <p class="fq-lead">الباقة الحالية: ${esc(planName(state.subStatus?.subscription?.plan || state.subActivePlan))}</p></div>
      <div class="fq-card pad" style="width:100%;align-items:center;text-align:center;gap:10px">
        <span class="fq-arc" aria-hidden="true"></span>
        <p class="fq-small" style="font-weight:700;color:var(--fq-text)">جاري إكمال إرسال طلبك تلقائياً...</p>
        <p class="fq-meta">يتم الآن إرسال طلب التسعير إلى الموردين المحددين مسبقاً.</p>
      </div>
    </section>`;
  }

  // SUB01_SubscriptionGate / FT08_SubscriptionRequired — nodes 60:10 and 64:956.
  if (view === "gate") {
    const exhausted = allowance().items_left <= 0;
    return `${fqHead({ title: exhausted ? "اشترك للمتابعة" : "اشترك معنا", back: "back-gate", mark: true })}
    <section class="fq-body">
      <div class="fq-card pad grey">
        <h1 class="fq-h2" style="font-size:22px">${exhausted ? "أكملت التجربة المجانية!" : "باقي خطوة وحدة بس!"}</h1>
        <p class="fq-lead">${exhausted ? "اختر الباقة المناسبة لمواصلة استخدام فرق وإرسال طلباتك مباشرة." : "اختر الباقة المناسبة، وبعد التفعيل نكمل إرسال طلبك مباشرة للموردين المحددين."}</p></div>
      <div class="fq-feats" style="gap:16px">
        ${["طلبك ومواصفاته محفوظة بالكامل", "الموردون الذين اخترتهم محفوظون", "لن تحتاج تبدأ كتابة طلبك من جديد"]
          .map((line) => `<span class="fq-feat"><span class="fq-tick-round">${ic("check", 14)}</span>${esc(line)}</span>`)
          .join("")}
      </div>
      ${paymentsOff() ? paymentsOffNote() : ""}
      <div class="fq-actions" style="margin-top:auto;gap:12px">
        ${paymentsOff() ? "" : `<button class="fq-btn r14" type="button" data-action="show-plans">عرض الباقات</button>`}
        <button class="fq-btn ghost r14" type="button" data-action="back-gate">ليس الآن</button>
      </div>
    </section>`;
  }

  // SUB09_LimitReached / FT07_TrialExhausted — nodes 62:134 and 64:919.
  // FT07_TrialExhausted — node 64:919
  if (view === "limit" && !isSubscribed()) {
    return `${fqHead({ title: "انتهت التجربة", mark: true })}
    <section class="fq-body center" style="padding-top:48px">
      <div class="fq-squircle warn">${ic("alert-triangle", 56)}</div>
      <div style="display:flex;flex-direction:column;gap:14px">
        <h1 class="fq-h1">استخدمت التجربة المجانية</h1>
        <p class="fq-lead">${openAccount() ? "غير محدود" : `${formatCount(allowance().items)} من ${formatCount(allowance().items)} بند`}</p>
        <span class="fq-tag deep" style="font-size:13px;padding:8px 14px;border-radius:999px">طلبك الأخير محفوظ ولن يضيع</span></div>
      ${paymentsOff() ? `<div style="width:100%">${paymentsOffNote()}</div>` : ""}
      <div class="fq-actions" style="width:100%;margin-top:auto;gap:12px">
        ${paymentsOff() ? "" : `<button class="fq-btn r14" type="button" data-action="show-plans">عرض الباقات</button>`}
        <button class="fq-btn soft r14" type="button" data-action="back-gate">رجوع</button>
      </div>
    </section>`;
  }
  // SUB09_LimitReached — node 62:134
  if (view === "limit") {
    const planCode = state.subStatus?.subscription?.plan || "";
    const paid = plans.find((item) => item.code === planCode);
    const cap = allowance().items;
    return `${fqHead({ title: "تجاوزت الحد المسموح", back: "back-gate", mark: true })}
    <section class="fq-body tight">
      <div class="fq-alert">عذرًا، لقد استهلكت كامل رصيد البنود المتاحة لباقة «${esc(planName(planCode))}».</div>
      <div class="fq-plan">
        <div class="fq-row"><span class="fq-tag danger">مكتمل / منتهي</span><span class="name">باقة: ${esc(planName(planCode))}</span></div>
        <div class="fq-usage">
          <div class="fq-row"><span class="fq-small hot">${formatCount(cap)} من ${formatCount(cap)} بند</span><span class="fq-meta">البنود المستخدمة</span></div>
          <div class="fq-track danger"><span style="width:100%"></span></div>
          <p class="fq-meta" style="margin:0">استخدمت ${formatCount(cap)} من ${formatCount(cap)} بند. رقّ باقتك للاستمرار.</p>
        </div>
      </div>
      ${paymentsOff() ? paymentsOffNote() : ""}
      <div class="fq-actions" style="margin-top:auto;gap:12px">
        ${paymentsOff() ? "" : `<button class="fq-btn r14" type="button" data-action="upgrade">ترقية الباقة الآن</button>`}
        <button class="fq-btn ghost r14" type="button" data-action="back-gate">رجوع للطلب</button>
      </div>
    </section>`;
  }

  // SUB10_UpgradePlan — node 62:179: the plan you are on, an arrow, the plan you would move to.
  if (view === "upgrade") {
    const current = plans.find((item) => item.code === state.subStatus?.subscription?.plan);
    const target = plans.find((item) => item.code !== current?.code) || plans[0];
    return `${fqHead({ title: "ترقية الباقة", back: "my-plan", mark: true })}
    <section class="fq-body tight">
      <h1 class="fq-h2">اختر الترقية المناسبة</h1>
      <div class="fq-current-strip">
        <span class="fq-meta">${current ? `${formatCount(Number(current.monthly_items || allowance().items))} بند شهريًا` : (openAccount() ? "حساب مفتوح" : "التجربة المجانية")}</span>
        <strong style="font-size:15px">الباقة الحالية: ${esc(current ? planName(current.code) : "التجربة المجانية")}</strong>
      </div>
      <div class="fq-arrow-down">${ic("arrow-down", 18)}</div>
      ${paymentsOff()
        ? paymentsOffNote()
        : target
        ? `<article class="fq-plan target">
            <div class="fq-row"><span class="badge">الترقية الموصى بها</span><span class="name">${esc(planLabel(target))}</span></div>
            <div class="fq-row"><span></span><span><span class="amount">${esc(money(target.price_amount / 100))}</span> <span class="per">/ ${esc(planPeriod(target.duration_days))}</span></span></div>
            ${planDetails(target).description ? `<p class="desc">${esc(planDetails(target).description)}</p>` : ""}
            ${planDetails(target).features.length ? `<hr class="fq-line"><div class="fq-feats">${planDetails(target).features.map((f) => `<span class="fq-feat"><span class="y">✓</span>${esc(f)}</span>`).join("")}</div>` : ""}
          </article>`
        : `<p class="fq-lead">لا توجد باقة أعلى متاحة حالياً.</p>`}
      <div class="fq-actions" style="margin-top:auto;gap:12px">
        ${paymentsOff() ? "" : `<button class="fq-btn r14" type="button" data-action="choose-plan" data-plan="${esc(target?.code || "")}" ${target ? "" : "disabled"}>متابعة للترقية</button>`}
        <button class="fq-btn ghost r14" type="button" data-action="my-plan">إلغاء</button>
      </div>
    </section>`;
  }

  // SUB02_Plans / FT09_PlansWithTrial — nodes 60:54 and 64:1001.
  if (view === "plans") {
    const upgrade = false;
    return `${fqHead({ title: upgrade ? "ترقية الباقة" : state.token ? "حسابي" : "الباقات والأسعار", back: state.token ? "my-plan" : "legal-back", mark: true })}
    <section class="fq-body tight">
      <div><h1 class="fq-h1">${upgrade ? "اختر الترقية المناسبة" : "اختر اللي يناسب استخدامك"}</h1>
        <p class="fq-lead">اشتراك شهري، وتقدر تبدأ بالباقة المناسبة لك وتعدلها بأي وقت.</p></div>
      ${isSubscribed()
        ? ""
        : `<article class="fq-plan trial">
            <div class="fq-row"><span class="fq-tag ok">مفعلة حالياً</span><span class="name" style="font-size:17px">التجربة المجانية</span></div>
            <div class="fq-row"><span class="per">ابدأ بدون بطاقة</span><span class="amount">0 ر.س</span></div>
            <p class="desc">✓ ${formatAllowance(allowance().items)} بند تسعير • ✓ حتى ${formatCount(allowance().sellers_per_item)} موردين لكل بند</p>
          </article>
          <div style="display:flex;align-items:center;gap:12px"><hr class="fq-line" style="flex:1"><span class="fq-meta">الباقات المدفوعة</span><hr class="fq-line" style="flex:1"></div>`}
      ${paymentsOff() ? paymentsOffNote() : state.subError ? `<p class="fq-small" style="color:#b3402a">${esc(state.subError)}</p>` : ""}
      ${plans.length ? plans.map((plan, index) => planCard(plan, { popular: index === popularIndex })).join("") : `<p class="fq-lead">لا توجد باقات متاحة حالياً.</p>`}
      <p class="fq-small" style="text-align:center;line-height:1.9">
        الأسعار شهرية ونهائية، بدون ضريبة قيمة مضافة. الاشتراك لا يتجدّد تلقائياً.<br>
        <a class="fq-link" href="/terms" data-action="legal" data-doc="terms">الشروط والأحكام</a> ·
        <a class="fq-link" href="/refunds" data-action="legal" data-doc="refunds">الإلغاء والاسترداد</a> ·
        <a class="fq-link" href="/privacy" data-action="legal" data-doc="privacy">الخصوصية</a>
      </p>
      ${operatorNote()}
    </section>`;
  }

  // SUB03_PlanReview / SUB11_ApplePayConfirm / FT10_PaymentAfterTrial — 60:109, 62:229, 64:1063.
  if (view === "review") {
    const plan = plans.find((item) => item.code === state.subActivePlan);
    if (!plan) return renderSubscribe.call(null);
    const upgrading = isSubscribed();
    const title = upgrading ? "تأكيد الدفع" : "مراجعة الاشتراك";
    const lead = upgrading
      ? { h: "ملخص الطلب", p: "" }
      : { h: "تفاصيل الدفع للترقية", p: "مراجعة سريعة قبل إرسال طلبك المحفوظ." };
    return `${fqHead({ title, back: "show-plans", mark: true })}
    <section class="fq-body">
      <div><h1 class="fq-h2">${esc(lead.h)}</h1>${lead.p ? `<p class="fq-lead">${esc(lead.p)}</p>` : ""}</div>
      <div class="fq-card pad fq-summary">
        <div class="fq-row"><strong class="value"><bdi>${esc(planLabel(plan))}</bdi></strong><span class="label">الخطة المختارة</span></div>
        <div class="fq-row"><span class="label">قيمة الاشتراك</span><strong class="value money">${esc(money(plan.price_amount / 100))} / ${esc(planPeriod(plan.duration_days))}يًا</strong></div>
        ${planDetails(plan).features.length ? `<hr class="fq-line"><div class="fq-feats">${planDetails(plan).features.map((f) => `<span class="fq-feat"><span class="y">✓</span>${esc(f)}</span>`).join("")}</div>` : ""}
      </div>
      ${paymentsOff() || plan.purchasable === false
        ? `${paymentsOffNote()}
      <div class="fq-actions" style="margin-top:auto;gap:12px">
        <button class="fq-btn ghost r14" type="button" data-action="my-plan">رجوع</button>
      </div>
    </section>`
        : `<div><p class="fq-sec-title"><span>طريقة الدفع</span></p>
        <button class="fq-payrow on" type="button" data-action="pay" aria-pressed="true">
          <span class="mark">${ic("credit-card", 18)}</span>
          <strong>${esc(payMethodLabel())}</strong>
          <span class="fq-radio on" aria-hidden="true"></span>
        </button>
        ${state.subBusy || state.subMountedPlan === plan.code
          ? `<div class="fq-card pad" style="margin-top:10px">
              ${state.subBusy ? `<p class="fq-lead">نجهّز الدفع…</p>` : ""}
              <div id="moyasar-form-${esc(plan.code)}"></div>
              ${state.subError ? `<p class="fq-small" style="color:#b3402a">${esc(state.subError)}</p><button class="fq-btn ghost sm" type="button" data-action="retry-payment">حاول مرة ثانية</button>` : ""}
            </div>`
          : ""}
      </div>
      <p class="fq-small" style="text-align:center;line-height:1.9;margin-top:12px">
        الاشتراك شهري ولا يتجدّد تلقائياً. السعر نهائي بدون ضريبة قيمة مضافة.<br>
        بالمتابعة توافق على <a class="fq-link" href="/terms" data-action="legal" data-doc="terms">الشروط</a>
        و<a class="fq-link" href="/refunds" data-action="legal" data-doc="refunds">سياسة الإلغاء والاسترداد</a>.
      </p>
      <div class="fq-actions" style="margin-top:auto;gap:12px">
        ${state.subBusy || state.subMountedPlan === plan.code ? "" : `<button class="fq-btn r14" type="button" data-action="pay">متابعة الدفع</button>`}
        <button class="fq-btn ghost outline-deep r14" type="button" data-action="show-plans">تغيير الباقة</button>
      </div>
      ${operatorNote()}
    </section>`}`;
  }

  // SUB07_CurrentPlan / SUB08_NearLimit / FT01–FT06 — nodes 62:16, 62:73, 64:325…64:835.
  const used = trialUsed();
  const subscribed = isSubscribed();
  const plan = subscribed ? plans.find((item) => item.code === state.subStatus?.subscription?.plan) : null;
  const limit = allowance().items;
  const left = Math.max(0, limit - used);
  const near = left <= Math.max(2, Math.round(limit * 0.2));
  const critical = left <= 2;
  const banner = near
    ? `<div class="fq-banner${critical ? " danger" : ""}">${ic("alert-triangle", 16)}<span style="flex:1">باقي لك ${esc(items(left))} ${subscribed ? "هذا الشهر" : critical ? "في التجربة المجانية" : "مجانية"}.</span>
        ${paymentsOff() ? "" : `<button type="button" data-action="${subscribed ? "upgrade" : critical ? "upgrade" : "show-plans"}">${subscribed ? "عرض الباقة" : critical ? "ترقية الآن" : "عرض الباقات"}</button>`}</div>`
    : "";
  const upsell = subscribed
    ? paymentsOff()
      ? ""
      : near
        ? `<div class="fq-upsell"><h2 class="fq-h2" style="font-size:17px">ترقية سريعة لتفادي الانقطاع</h2>
            <button class="fq-btn r14" type="button" data-action="upgrade">ترقية الآن</button></div>`
        : `<div class="fq-upsell"><h2 class="fq-h2" style="font-size:17px">تحتاج مساحة أكبر؟</h2>
            <p class="fq-lead">رقّ باقتك لتحصل على بنود تسعير أكثر ومزايا إضافية لك.</p>
            <button class="fq-btn r14" type="button" data-action="upgrade">ترقية الاشتراك</button></div>`
    : "";
  return `${fqHead({ title: "حسابي", mark: true, end: `<button class="fq-lang" type="button" data-action="lang">${ic("globe", 16)}<span>العربية</span></button>` })}
  ${banner}
  <section class="fq-body tight">
    ${subscribed
      ? usageCard({ deep: !near, title: `الباقة الحالية: ${planName(state.subStatus?.subscription?.plan || "")}`, badge: near ? "قارب على الانتهاء" : "نشطة", badgeTone: near ? "warn" : "ok", price: plan ? money(plan.price_amount / 100) : "", per: "/ شهريًا", used, limit, foot: near ? `المتبقي ${items(left)} فقط لتفادي توقف الخدمة` : `المتبقي ${items(left)} هذا الشهر`, warn: near })
      : usageCard({ title: "التجربة المجانية", badge: critical ? "شارفت على الانتهاء" : "مفعلة", badgeTone: critical ? "danger" : near ? "warn" : "ok", price: "0 ر.س", per: "/ ابدأ بدون بطاقة", used, limit, foot: `المتبقي ${items(left)}`, warn: near })}
    ${subscribed
      ? upsell
      : `<div class="fq-card pad"><h2 class="fq-h2" style="font-size:17px">مزايا الفترة التجريبية:</h2>
          <div class="fq-feats">${[`حتى ${formatAllowance(allowance().items)} بند تسعير`, `حتى ${formatCount(allowance().sellers_per_item)} موردين لكل بند`, "البحث والمقارنة السريعة", "المحادثات واستقبل العروض"]
            .map((line) => `<span class="fq-feat"><span class="y">✓</span>${esc(line)}</span>`)
            .join("")}</div></div>
        <p class="fq-meta">يمكنك التواصل مع حتى ${formatCount(allowance().sellers_per_item)} موردين لكل بند مجاناً.</p>
        <div class="fq-sticky">
          ${critical && !paymentsOff()
            ? `<button class="fq-btn r14" type="button" data-action="show-plans">ترقية باقة الاشتراك لتفادي الانقطاع</button>`
            : near
              ? `<button class="fq-btn amber-chip r14" type="button" disabled>تجربتك نشطة</button>`
              : `<button class="fq-btn mint r14" type="button" disabled>تجربتك مفعلة</button>`}
          ${paymentsOff()
            ? `<p class="fq-small" style="text-align:center;margin-top:12px">الاشتراك غير متاح حالياً</p>`
            : `<p class="fq-small" style="text-align:center;margin-top:12px">تحتاج أكثر؟ <button class="fq-link" type="button" data-action="show-plans">عرض الباقات</button></p>`}
        </div>`}
  </section>
  ${fqNav("account")}`;
}

// ---------------------------------------------------------------------------
// Addresses. Every screen has its own URL, so the phone's Back walks back through the
// journey instead of leaving the app, a reload lands on the same screen, and a link to a
// conversation opens it. Moving between screens pushes an entry; Back and Forward only
// re-draw from what is already here — they never search, send or pay again.
// ---------------------------------------------------------------------------
function threadPath(suffix = "") {
  const id = state.thread?.id || state.pendingThread;
  return id ? `/r/${encodeURIComponent(id)}${suffix}` : "/requests";
}

const ROUTE_OF = {
  home: () => "/",
  "city-ask": () => "/city",
  understand: () => "/understand",
  flow: () => "/results",
  detail: () => "/results/ad",
  review: () => "/review",
  sent: () => "/sent",
  requests: () => "/requests",
  thread: () => threadPath(),
  compare: () => threadPath("/compare"),
  awarded: () => threadPath(),
  account: () => "/account",
  notifications: () => "/notifications",
  "notify-settings": () => "/account/notifications",
  subscribe: () => (state.subView === "plans" ? "/plans" : "/subscribe"),
  verify: () => "/verify",
  "supplier-auth": () => (state.supplierMode === "join" ? "/supplier/join" : "/supplier"),
  "supplier-requests": () => "/supplier",
  legal: () => `/${state.legalDoc || "terms"}`,
  seller: () => `/s/${encodeURIComponent(state.sellerToken)}`,
  // «sending» and «auth» have no address of their own: they stand over the screen that led to them.
};

function syncUrl() {
  const make = ROUTE_OF[state.view];
  const replace = state.replaceUrl;
  state.replaceUrl = false;
  if (!make) return;
  const path = make();
  if (location.pathname + location.search === path) return;
  try {
    history[replace ? "replaceState" : "pushState"]({ view: state.view }, "", path);
  } catch (_error) {}
}

function closeSheets() {
  state.editing = null;
  state.capSheet = false;
  state.pickerOpen = false;
  state.attachOpen = false;
  state.awardPick = null;
}

// Draws the screen an address names, from what this visit already holds. A screen whose data
// is gone (a review with nothing ticked, results never fetched) falls back one step, and the
// address is corrected in place.
function applyRoute(path, { pop = false } = {}) {
  const [head = "", id = "", sub = ""] = String(path || "/")
    .split("?")[0]
    .split("/")
    .filter(Boolean)
    .map((part) => {
      try {
        return decodeURIComponent(part);
      } catch (_error) {
        return part;
      }
    });
  state.replaceUrl = pop;
  closeSheets();
  const show = (view) => {
    state.view = view;
    render();
  };
  if (LEGAL_TITLES[head]) return openLegal(head);
  if (head === "verify") {
    confirmEmail(new URLSearchParams(location.search).get("token") || "");
    return;
  }
  if (head === "plans") {
    // Public on purpose: a payment provider reviewing the service, and anyone deciding
    // whether to sign up, must be able to read the prices without creating an account.
    state.view = "subscribe";
    state.subView = "plans";
    render();
    loadSubscribe({ keepView: true, allowSignedOut: true }).catch(() => {});
    return;
  }
  if (head === "supplier") return openSupplier(id);
  if (head === "s" && id) return openSellerPage(id);
  if (!state.token) {
    if (farqHandlesSignIn("open-request")) return;
    state.returnRoute = path;
    state.view = "auth";
    render();
    return;
  }
  const hasResults = state.results.length > 0 || Boolean(state.searchState);
  const hasNeeds = Boolean(state.needs || state.intentError || state.intentProblem);
  if (!head) return show("home");
  if (head === "city") {
    if (!state.cityMode) state.cityMode = state.query.trim() ? "request" : "pick";
    return show("city-ask");
  }
  if (head === "understand") return show(hasNeeds ? "understand" : "home");
  if (head === "results") {
    if (id === "ad" && state.active && hasResults) return show("detail");
    state.active = null;
    return show(hasResults ? "flow" : hasNeeds ? "understand" : "home");
  }
  if (head === "review") return show(state.selected.size ? "review" : hasResults ? "flow" : hasNeeds ? "understand" : "home");
  if (head === "sent") {
    if (state.sentInfo) return show("sent");
    return void loadRequests().catch(() => {});
  }
  if (head === "requests") return void loadRequests().catch(() => {});
  if (head === "r" && id) {
    if (state.thread?.id === id) {
      state.activeSeller = pop ? state.activeSeller : "";
      return show(sub === "compare" ? "compare" : "thread");
    }
    state.activeSeller = "";
    state.replyTo = null;
    state.picked = null;
    state.stickChat = true;
    return void loadThread(id)
      .then(() => {
        if (sub === "compare" && state.view === "thread") {
          state.replaceUrl = true;
          show("compare");
        }
      })
      .catch(() => {});
  }
  if (head === "account" && id === "notifications") return show("notify-settings");
  if (head === "account") {
    show("account");
    return void loadSubStatus().catch(() => {});
  }
  if (head === "notifications") return void loadNotifications();
  if (head === "subscribe") {
    state.subView = state.subView || "my-plan";
    return void loadSubscribe({ keepView: true }).catch(() => {});
  }
  state.replaceUrl = true;
  return show("home");
}

// A reload or a closed tab writes the draft at once instead of waiting for the next pause.
window.addEventListener("pagehide", () => {
  if (state.view !== "seller" && state.view !== "legal") saveDraft();
});

window.addEventListener("popstate", () => {
  // A request on its way finishes on its own; Back does not cancel or repeat it.
  if (state.view === "sending") return;
  applyRoute(location.pathname, { pop: true });
});

// ---------------------------------------------------------------------------
// The journey in progress — the words, the city, the items and their edits, the results and
// the ticks — is kept for this tab, so a reload or a dropped connection does not wipe it.
// Attached photos cannot be kept this way; they are the one thing a reload loses.
// ---------------------------------------------------------------------------
const DRAFT_KEY = "farq.draft";
const DRAFT_TTL = 24 * 60 * 60 * 1000;
let draftTimer = 0;

function draftSnapshot(withResults = true) {
  return {
    v: 1,
    at: Date.now(),
    query: state.query,
    originalText: state.originalText,
    searchText: state.searchText,
    traceId: state.traceId || "",
    city: state.city,
    cityMode: state.cityMode || "",
    needs: state.needs,
    intent: state.intent,
    intentError: state.intentError || false,
    intentProblem: state.intentProblem || "",
    intentQuestion: state.intentQuestion || "",
    results: withResults ? state.results : [],
    resultNeeds: withResults ? [...state.resultNeeds.entries()] : [],
    searchState: withResults ? (state.searching || state.partial ? "" : state.searchState) : "",
    notice: withResults && !state.searching && !state.partial ? state.notice : "",
    clarification: state.clarification,
    selected: [...state.selected.entries()],
    needFilter: state.needFilter,
    note: state.note,
    reviewExtra: state.reviewExtra === true,
    sendKey: state.sendKey || "",
  };
}

function saveDraft() {
  clearTimeout(draftTimer);
  const empty = !state.query && !state.needs && !state.results.length && !state.selected.size;
  if (empty) return storedSet(DRAFT_KEY, null, "session");
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draftSnapshot(true)));
  } catch (_error) {
    // too big for this browser's allowance: keep the request and the ticks, drop the list
    try {
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draftSnapshot(false)));
    } catch (_again) {}
  }
}

function scheduleDraftSave() {
  if (state.view === "seller" || state.view === "legal") return;
  clearTimeout(draftTimer);
  draftTimer = setTimeout(saveDraft, state.searching ? 1500 : 250);
}

function restoreDraft() {
  let draft = null;
  try {
    draft = JSON.parse(storedGet(DRAFT_KEY, "session") || "null");
  } catch (_error) {
    draft = null;
  }
  if (!draft || draft.v !== 1 || Date.now() - (draft.at || 0) > DRAFT_TTL) return;
  state.query = draft.query || "";
  state.originalText = draft.originalText || "";
  state.searchText = draft.searchText || "";
  state.traceId = draft.traceId || "";
  state.city = draft.city || "";
  state.cityMode = draft.cityMode || "";
  state.needs = Array.isArray(draft.needs) ? draft.needs : null;
  state.intent = draft.intent || null;
  state.intentError = Boolean(draft.intentError);
  state.intentProblem = draft.intentProblem || "";
  state.intentQuestion = draft.intentQuestion || "";
  state.results = Array.isArray(draft.results) ? draft.results : [];
  state.resultNeeds = new Map(Array.isArray(draft.resultNeeds) ? draft.resultNeeds : []);
  state.searchState = draft.searchState || "";
  state.notice = draft.notice || "";
  state.clarification = draft.clarification || "";
  state.selected = new Map(Array.isArray(draft.selected) ? draft.selected : []);
  state.needFilter = draft.needFilter || "";
  state.note = draft.note || "";
  state.reviewExtra = Boolean(draft.reviewExtra);
  state.sendKey = typeof draft.sendKey === "string" ? draft.sendKey : "";
  state.partial = false;
  state.searching = false;
  state.seenCards = new Set(state.results.map(resultKey));
  state.shownCount = state.results.length;
}

// After a request is sent the journey starts over.
function clearDraft() {
  clearTimeout(draftTimer);
  storedSet(DRAFT_KEY, null, "session");
  state.query = "";
  state.originalText = "";
  state.searchText = "";
  state.needs = null;
  state.intent = null;
  state.intentError = false;
  state.intentProblem = "";
  state.results = [];
  state.resultNeeds = new Map();
  state.searchState = "";
  state.notice = "";
  state.needFilter = "";
  state.selected.clear();
  state.reviewExtra = false;
  state.sendKey = "";
}

// ---------------------------------------------------------------------------
// The supplier's quote page — the link in every invitation. No sign-in: the token in the
// address is the supplier's key to his own request. It shows only what /v1/seller/{token}
// returns and posts his reply or price back to the customer's conversation.
// ---------------------------------------------------------------------------
async function openSellerPage(token) {
  const changed = state.sellerToken !== token;
  state.view = "seller";
  state.sellerToken = token;
  if (changed) {
    state.seller = null;
    state.sellerError = "";
    state.sellerSent = false;
  }
  render();
  try {
    state.seller = await api(`/v1/seller/${encodeURIComponent(token)}`, { skipAuth: true });
    state.sellerError = "";
  } catch (error) {
    state.sellerError = error?.status === 404 ? "missing" : "failed";
  }
  if (state.view === "seller") render();
}

async function submitShareContact(form) {
  const phone = String(new FormData(form).get("phone") || "").trim();
  state.shareError = "";
  let place = null;
  if (state.sharePlace && navigator.geolocation) {
    place = await new Promise((resolve) =>
      navigator.geolocation.getCurrentPosition(
        (pos) => resolve({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
        () => resolve(null),
        { timeout: 8000 },
      ),
    );
  }
  try {
    await api(`/v1/requests/${encodeURIComponent(state.thread.id)}/contact`, {
      method: "POST",
      json: { phone, lat: place?.lat ?? null, lng: place?.lng ?? null },
    });
    state.shareOpen = false;
    state.thread = { ...state.thread, contact_shared: true };
    toast(place ? "تمت مشاركة رقمك وموقعك" : "تمت مشاركة رقمك");
    render();
  } catch (error) {
    state.shareError = error?.detail === "invalid phone" ? "رقم الجوال غير صحيح. اكتبه بصيغة 05xxxxxxxx." : "ما قدرنا نشارك بياناتك. حاول مرة ثانية.";
    render();
  }
}

async function revokeContact() {
  try {
    await api(`/v1/requests/${encodeURIComponent(state.thread.id)}/contact`, { method: "DELETE" });
    state.thread = { ...state.thread, contact_shared: false };
    toast("أوقفنا مشاركة بياناتك");
    render();
  } catch (_error) {
    toast("ما قدرنا نوقف المشاركة. حاول مرة ثانية.");
  }
}

async function submitSupplierJoin(form) {
  const data = new FormData(form);
  state.supplierError = "";
  try {
    const created = await api("/v1/supplier/register", {
      method: "POST",
      skipAuth: true,
      json: {
        name: String(data.get("name") || "").trim(),
        email: String(data.get("email") || "").trim(),
        phone: String(data.get("phone") || "").trim(),
        password: String(data.get("password") || ""),
        activity_type: state.supplierActivity || "both",
        description: (state.supplierDesc || String(data.get("description") || "")).trim(),
        categories: state.supplierPicked,
        // Registering from an invite link binds the account to the Haraj seller that link
        // proves, so the requests list works from the first second.
        token: state.sellerToken || undefined,
      },
    });
    keepSupplierSession(created.token, created.supplier);
    state.supplierPicked = [];
    state.supplierSuggested = [];
    state.supplierCapabilities = [];
    state.supplierDesc = "";
    openSupplier();
  } catch (error) {
    state.supplierError = supplierMessage(error, "ما قدرنا نكمل التسجيل. راجع بياناتك وحاول مرة ثانية.");
    render();
  }
}

async function submitSupplierSignIn(form) {
  const data = new FormData(form);
  state.supplierError = "";
  try {
    const signed = await api("/v1/supplier/login", {
      method: "POST",
      skipAuth: true,
      json: { email: String(data.get("email") || "").trim(), password: String(data.get("password") || "") },
    });
    keepSupplierSession(signed.token, signed.supplier);
    openSupplier();
  } catch (error) {
    state.supplierError = supplierMessage(error, "البريد أو كلمة المرور غير صحيحة.");
    render();
  }
}

function supplierMessage(error, fallback) {
  const detail = error?.detail;
  if (error?.status === 409) return "هذا البريد أو الحساب مسجّل من قبل.";
  if (error?.status === 429) return "محاولات كثيرة. انتظر شوي وحاول مرة ثانية.";
  if (detail === "invalid phone") return "رقم الجوال غير صحيح. اكتبه بصيغة 05xxxxxxxx.";
  if (detail === "invalid email") return "البريد الإلكتروني غير صحيح.";
  if (detail === "password too short") return "كلمة المرور لازم ٨ أحرف على الأقل.";
  if (detail === "name required") return "اكتب اسم المنشأة أو اسمك.";
  return fallback;
}

async function submitSellerReply(form) {
  if (state.sellerBusy) return;
  const data = new FormData(form);
  const body = String(data.get("body") || "").trim();
  const amountText = String(data.get("offer_amount") || "").replace(/[٠-٩]/g, (d) => "٠١٢٣٤٥٦٧٨٩".indexOf(d)).replace(/[,،\s]/g, "");
  const deliveryText = String(data.get("delivery_price") || "").replace(/[٠-٩]/g, (d) => "٠١٢٣٤٥٦٧٨٩".indexOf(d)).replace(/[,،\s]/g, "");
  const amount = amountText ? Number(amountText) : null;
  const included = data.get("delivery_included") === "on";
  const deliveryPrice = !included && deliveryText ? Number(deliveryText) : null;
  state.sellerFormError = "";
  if (amount != null && (!Number.isFinite(amount) || amount <= 0)) state.sellerFormError = "اكتب السعر بالأرقام، مثل 350.";
  else if (deliveryPrice != null && (!Number.isFinite(deliveryPrice) || deliveryPrice < 0)) state.sellerFormError = "اكتب سعر التوصيل بالأرقام.";
  else if (!body && amount == null) state.sellerFormError = "اكتب ردّك أو السعر قبل الإرسال.";
  if (state.sellerFormError) {
    state.sellerDraft = { body, amount: amountText, included, delivery: deliveryText };
    render();
    return;
  }
  const json = { body };
  if (amount != null) {
    json.offer_amount = amount;
    json.delivery_included = included;
    if (deliveryPrice != null) json.delivery_price = deliveryPrice;
  }
  state.sellerBusy = true;
  render();
  try {
    await api(`/v1/seller/${encodeURIComponent(state.sellerToken)}/messages`, { method: "POST", json, skipAuth: true });
    state.sellerBusy = false;
    state.sellerSent = true;
    state.sellerDraft = null;
    await openSellerPage(state.sellerToken);
  } catch (error) {
    state.sellerBusy = false;
    state.sellerDraft = { body, amount: amountText, included, delivery: deliveryText };
    const reason = typeof error?.detail === "string" ? error.detail : error?.detail?.message;
    state.sellerFormError = error?.status === 404 ? "هذا الطلب ما عاد متاح." : error?.status === 409 || /award/i.test(reason || "") ? "اختار العميل عرضاً آخر لهذا الطلب." : error?.status === 422 ? "تأكد من البيانات وجرّب مرة ثانية." : "ما قدرنا نرسل ردّك. تأكد من الاتصال وجرّب مرة ثانية.";
    render();
  }
}

// The address is proved on the way to the first send, not on the way in: someone must be
// able to look at the product before proving anything.
async function confirmEmail(token) {
  state.view = "verify";
  state.verifyState = token ? "working" : "bad";
  render();
  if (!token) return;
  try {
    await api("/v1/auth/verify", { method: "POST", json: { token }, skipAuth: true });
    state.verifyState = "done";
    if (state.verify) state.verify.verified = true;
  } catch (_error) {
    state.verifyState = "bad";
  }
  render();
}

async function loadVerification() {
  if (!state.token) return;
  try {
    state.verify = await api("/v1/auth/verify/status", { quiet: true });
    render();
  } catch (_error) {}
}

async function resendVerification() {
  try {
    const sent = await api("/v1/auth/verify/send", { method: "POST", json: {} });
    toast(sent.verified ? "بريدك مؤكد أصلاً" : "أرسلنا لك رابط التأكيد");
    if (sent.verified && state.verify) state.verify.verified = true;
    render();
  } catch (error) {
    toast(error?.status === 429 ? "محاولات كثيرة. انتظر شوي." : "ما قدرنا نرسل الرابط. حاول مرة ثانية.");
  }
}

// Shown above the app while an address is unproved and proving it is required.
function verifyBanner() {
  const check = state.verify;
  if (!check || !check.required || check.verified) return "";
  return `<div class="fq-banner">${ic("alert-triangle", 16)}<span style="flex:1">أكّد بريدك <bdi>${esc(check.email || "")}</bdi> قبل إرسال أول طلب.</span>
    <button type="button" data-action="resend-verify">أعد الإرسال</button></div>`;
}

function renderVerify() {
  const working = state.verifyState === "working";
  const done = state.verifyState === "done";
  return `${fqHead({ title: "تأكيد البريد", mark: true })}
  <section class="fq-body center" style="padding-top:56px">
    ${working
      ? `<div class="fq-spinner" aria-hidden="true"></div><p class="fq-lead">نتأكد من الرابط…</p>`
      : done
        ? `<div class="fq-squircle ringed land">${ic("check", 56)}</div>
           <div><h1 class="fq-h1">تم تأكيد بريدك</h1><p class="fq-lead">تقدر الآن ترسل طلباتك.</p></div>
           <div class="fq-actions" style="width:100%;margin-top:auto">
             <button class="fq-btn r14" type="button" data-action="home">ابدأ التسعير</button></div>`
        : `<div class="fq-squircle danger">${ic("alert-triangle", 56)}</div>
           <div><h1 class="fq-h1">الرابط غير صالح</h1>
             <p class="fq-lead">يمكن انتهت صلاحيته أو استُخدم من قبل. اطلب رابطاً جديداً من داخل التطبيق.</p></div>
           <div class="fq-actions" style="width:100%;margin-top:auto">
             <button class="fq-btn r14" type="button" data-action="resend-verify">أرسل رابطاً جديداً</button>
             <button class="fq-btn ghost r14" type="button" data-action="home">رجوع</button></div>`}
  </section>`;
}

// ---------------------------------------------------------------------------
// The supplier app. A second surface on the same bundle: SUP01 join, SUP02 categories,
// SC09 the requests list. It never passes through the customer's sign-in gate, and it
// keeps its own session, because a supplier is not a Taseer customer.
//
// Why an account at all: an invite link carries one request. A supplier who signs in sees
// every request he was written to — and, once registered, those requests reach him in this
// app instead of through Haraj, which is the only channel with a hard capacity ceiling.
// ---------------------------------------------------------------------------

const SUPPLIER_STATES = {
  new: { label: "جديد", tone: "warn" },
  quoted: { label: "مرسل عرض", tone: "deep" },
  awarded: { label: "تم الترسية", tone: "ok" },
  lost: { label: "لم يتم اختيارك", tone: "" },
};

const SUPPLIER_FILTERS = [
  { key: "all", label: "الكل" },
  { key: "new", label: "جديدة" },
  { key: "quoted", label: "مرسل" },
  { key: "awarded", label: "مُرسى" },
];

function supplierSignOutLocally() {
  state.supplierToken = "";
  state.supplier = null;
  state.supplierRequests = [];
  state.supplierCounts = null;
  try {
    localStorage.removeItem("farq.supplierToken");
  } catch (_error) {}
}

function keepSupplierSession(token, supplier) {
  state.supplierToken = token || "";
  state.supplier = supplier || null;
  try {
    if (token) localStorage.setItem("farq.supplierToken", token);
  } catch (_error) {}
}

async function openSupplier(section = "") {
  state.supplierError = "";
  if (section === "join") {
    state.view = "supplier-auth";
    state.supplierMode = "join";
    render();
    loadSupplierCatalog().catch(() => {});
    return;
  }
  if (!state.supplierToken) {
    state.view = "supplier-auth";
    state.supplierMode = state.supplierMode === "join" ? "join" : "signin";
    render();
    loadSupplierCatalog().catch(() => {});
    return;
  }
  state.view = "supplier-requests";
  render();
  loadSupplierRequests().catch(() => {});
  loadSupplierInbox().catch(() => {});
}

async function loadSupplierCatalog() {
  if (state.supplierCatalog.length) return;
  try {
    const data = await api("/v1/supplier/categories", { skipAuth: true, quiet: true });
    state.supplierCatalog = data.categories || [];
    render();
  } catch (_error) {}
}

// Rung one of the ladder, as close to realtime as serverless allows: it reloads whenever
// the supplier opens or refreshes his list.
async function loadSupplierInbox() {
  if (!state.supplierToken) return;
  try {
    const data = await api("/v1/supplier/notifications", { asSupplier: true, quiet: true });
    state.supplierInbox = data.notifications || [];
    state.supplierUnread = data.unread || 0;
    if (state.view === "supplier-requests") render();
  } catch (_error) {}
}

async function readSupplierInbox(id) {
  try {
    await api("/v1/supplier/notifications/read", { method: "POST", json: { id: id || null }, asSupplier: true, quiet: true });
  } catch (_error) {}
  loadSupplierInbox().catch(() => {});
}

async function loadSupplierRequests() {
  try {
    const data = await api("/v1/supplier/requests", { asSupplier: true, quiet: true });
    state.supplierRequests = data.requests || [];
    state.supplierCounts = data.counts || null;
    state.supplier = data.supplier || state.supplier;
  } catch (error) {
    if (!error.auth) state.supplierError = "ما قدرنا نجيب طلباتك. جرّب مرة ثانية.";
  }
  render();
}

// SUP02. The description is the source: the server reads it and returns both halves of
// the taxonomy — the categories it recognised, and every other meaningful term the
// supplier used. The second half is why the list is not closed: a trade we have no
// category for is still kept, in his own words.
let describeTimer = 0;
function describeBusiness(text) {
  clearTimeout(describeTimer);
  describeTimer = setTimeout(async () => {
    if (!text.trim()) {
      state.supplierSuggested = [];
      state.supplierCapabilities = [];
      render();
      return;
    }
    try {
      const data = await api("/v1/supplier/describe", { method: "POST", json: { text }, skipAuth: true, quiet: true });
      state.supplierSuggested = data.categories || [];
      state.supplierCapabilities = data.capabilities || [];
      render();
    } catch (_error) {}
  }, 400);
}

function supplierChip(item, picked) {
  return `<button class="fq-pill${picked ? " on" : ""}" type="button" data-action="supplier-category" data-key="${esc(item.key)}">
    ${picked ? `<span class="y">✓</span>` : ""}${esc(item.label)}</button>`;
}

// SUP01 + SUP02 in one screen: the form, and the categories the description reveals.
function renderSupplierAuth() {
  const joining = state.supplierMode === "join";
  const picked = state.supplierPicked;
  const suggested = state.supplierSuggested.filter((item) => !picked.includes(item.key));
  // The activity chips narrow the list: a services-only supplier is not shown products.
  const activity = state.supplierActivity || "both";
  const wanted = activity === "services" ? ["service"] : activity === "products" ? ["product"] : ["service", "product"];
  const catalog = state.supplierCatalog.filter(
    (item) => wanted.includes(item.kind) && !picked.includes(item.key) && !suggested.some((s) => s.key === item.key),
  );
  const pickedItems = picked
    .map((key) => state.supplierCatalog.find((item) => item.key === key) || state.supplierSuggested.find((item) => item.key === key))
    .filter(Boolean);

  if (!joining) {
    return `${fqHead({ title: "دخول الموردين", back: "supplier-home", backStart: true, mark: true })}
    <section class="fq-body tight">
      <div><h1 class="fq-h1">أهلاً بك مرة ثانية</h1>
        <p class="fq-lead">ادخل لتشوف طلبات التسعير اللي وصلتك، وترد عليها من مكان واحد.</p></div>
      ${state.supplierError ? `<p class="fq-small" style="color:#b3402a">${esc(state.supplierError)}</p>` : ""}
      <form id="supplier-signin" class="fq-card pad" style="gap:14px" novalidate>
        <div class="fq-field"><label for="sup-email">البريد الإلكتروني</label>
          <div class="fq-inp"><input id="sup-email" name="email" type="email" inputmode="email" autocomplete="email" dir="ltr" required></div></div>
        <div class="fq-field"><label for="sup-password">كلمة المرور</label>
          <div class="fq-inp"><input id="sup-password" name="password" type="password" autocomplete="current-password" required></div></div>
        <button class="fq-btn r14" type="submit">دخول</button>
      </form>
      <p class="fq-small" style="text-align:center">ما عندك حساب؟
        <button class="fq-link" type="button" data-action="supplier-join">سجّل الآن</button></p>
      ${operatorNote()}
    </section>`;
  }

  return `${fqHead({ title: "طلب انضمام مورد", back: "supplier-home", backStart: true, mark: true })}
  <section class="fq-body tight">
    <div><h1 class="fq-h1">انضم كمورد في فرق</h1>
      <p class="fq-lead">سجّل مرة وحدة، وتوصلك طلبات التسعير في مجالك مباشرة هنا — بدون ما تنتظر رسالة.</p></div>
    ${state.supplierError ? `<p class="fq-small" style="color:#b3402a">${esc(state.supplierError)}</p>` : ""}
    <form id="supplier-join" class="fq-card pad" style="gap:14px" novalidate>
      <div class="fq-field"><label for="join-name">اسم المنشأة أو اسمك</label>
        <div class="fq-inp">${ic("user", 16)}<input id="join-name" name="name" autocomplete="organization" required></div></div>
      <div class="fq-field"><label for="join-email">البريد الإلكتروني</label>
        <div class="fq-inp">${ic("mail", 16)}<input id="join-email" name="email" type="email" inputmode="email" autocomplete="email" dir="ltr" required></div></div>
      <div class="fq-field"><label for="join-phone">رقم الجوال</label>
        <div class="fq-inp">${ic("phone", 16)}<input id="join-phone" name="phone" inputmode="tel" autocomplete="tel" placeholder="05xxxxxxxx" dir="ltr" required></div></div>
      <div class="fq-field"><label for="join-password">كلمة المرور</label>
        <div class="fq-inp"><input id="join-password" name="password" type="password" autocomplete="new-password" minlength="8" required></div>
        <span class="fq-meta">٨ أحرف على الأقل</span></div>

      <hr class="fq-line">
      <div class="fq-field"><label>نوع النشاط</label>
        <div class="fq-pills">
          ${[["both", "منتجات وخدمات"], ["services", "خدمات"], ["products", "منتجات"]]
            .map(([key, label]) => `<button class="fq-pill${(state.supplierActivity || "both") === key ? " on" : ""}" type="button" data-action="supplier-activity" data-key="${key}">${esc(label)}</button>`)
            .join("")}
        </div></div>

      <div class="fq-field"><label for="join-desc">وش تشتغل بالضبط؟</label>
        <div class="fq-inp" style="min-height:88px;align-items:flex-start"><textarea id="join-desc" name="description" rows="3" maxlength="400" placeholder="مثال: أشتغل سباكة وأصلح تسريبات المياه وأركب سخانات">${esc(state.supplierDesc)}</textarea></div>
        <span class="fq-meta">اكتب بالعامية وبتفصيل. كل كلمة لها معنى نحفظها، حتى لو ما لها تصنيف جاهز.</span></div>

      ${pickedItems.length
        ? `<div class="fq-field"><label>تصنيفاتك</label>
            <div class="fq-pills">${pickedItems.map((item) => supplierChip(item, true)).join("")}</div></div>`
        : ""}
      ${suggested.length
        ? `<div class="fq-field"><label>تم التعرف على:</label>
            <div class="fq-pills">${suggested.map((item) => supplierChip(item, false)).join("")}</div></div>`
        : ""}
      ${state.supplierCapabilities.length
        ? `<div class="fq-field"><label>وفهمنا كمان إنك تشتغل في:</label>
            <div class="fq-pills">${state.supplierCapabilities.map((word) => `<span class="fq-pill" style="opacity:.85">${esc(word)}</span>`).join("")}</div>
            <span class="fq-meta">نحفظها بكلامك حتى لو ما لها تصنيف عندنا، عشان توصلك طلباتها.</span></div>`
        : ""}
      ${catalog.length
        ? `<details class="fq-card flat" style="padding:12px"><summary class="fq-meta">تصفّح كل التصنيفات (${formatCount(catalog.length)})</summary>
            ${[...new Set(catalog.map((item) => item.group))]
              .map((group) => `<div style="margin-top:12px"><span class="fq-meta">${esc(group)}</span>
                <div class="fq-pills" style="margin-top:6px">${catalog.filter((item) => item.group === group).map((item) => supplierChip(item, false)).join("")}</div></div>`)
              .join("")}
          </details>`
        : ""}

      <button class="fq-btn r14" type="submit">سجّل وشاهد الطلبات</button>
      <p class="fq-small" style="text-align:center;margin:0">بالتسجيل توافق على
        <a class="fq-link" href="/terms" data-action="legal" data-doc="terms">الشروط</a> و<a class="fq-link" href="/privacy" data-action="legal" data-doc="privacy">الخصوصية</a>.</p>
    </form>
    <p class="fq-small" style="text-align:center">عندك حساب؟
      <button class="fq-link" type="button" data-action="supplier-signin">دخول</button></p>
    ${operatorNote()}
  </section>`;
}

// SC09: every request this supplier was written to, in one list.
function renderSupplierRequests() {
  const supplier = state.supplier;
  const counts = state.supplierCounts || {};
  const rows = state.supplierRequests.filter((row) => state.supplierFilter === "all" || row.state === state.supplierFilter);
  const pending = supplier && supplier.status !== "active";

  const card = (row) => {
    const tone = SUPPLIER_STATES[row.state] || SUPPLIER_STATES.new;
    const meta = row.state === "awarded"
      ? `قيمة العقد: ${esc(money(row.offer || 0))}`
      : row.offer != null
        ? `عرضك: ${esc(money(row.offer))}`
        : `${ic("map-pin", 12)} ${esc(cityLabel(row.city) || "")}`;
    return `<button class="fq-card pad fq-suprow${row.state === "awarded" ? " won" : ""}" type="button" data-action="supplier-open" data-token="${esc(row.token || "")}" style="width:100%;text-align:inherit;font:inherit;gap:10px">
      <div class="fq-row"><span class="fq-tag${tone.tone ? ` ${tone.tone}` : ""}">${esc(tone.label)}</span>
        <strong style="font-size:16px"><bdi>${esc(row.need || "طلب تسعير")}</bdi></strong></div>
      <div class="fq-row"><span class="fq-meta">${meta}</span>
        <span class="fq-meta">${ic("map-pin", 12)} ${esc(cityLabel(row.city) || "")}</span></div>
    </button>`;
  };

  const bell = `<button class="fq-ibtn plain" type="button" data-action="supplier-inbox" aria-label="التنبيهات">
    <span class="fq-tab-wrap">${ic("bell", 18)}${state.supplierUnread ? `<span class="fq-tab-badge">${formatCount(state.supplierUnread)}</span>` : ""}</span></button>`;
  if (state.supplierTab === "inbox") {
    return `${fqHead({ title: "التنبيهات", back: "supplier-home", backStart: true, mark: true,
      end: state.supplierUnread ? `<button class="fq-link" type="button" data-action="supplier-read-all">تعليم الكل</button>` : "" })}
    <section class="fq-body tight">
      ${state.supplierInbox.length
        ? state.supplierInbox.map((item) => `<button class="fq-card pad fq-suprow${item.read ? "" : " won"}" type="button"
            data-action="supplier-open-note" data-id="${esc(item.id)}" data-url="${esc(item.url || "")}" style="width:100%;text-align:inherit;font:inherit;gap:6px">
            <div class="fq-row"><span class="fq-meta">${esc(chatTime(item.created_at))}</span>
              <strong style="font-size:15px">${esc(item.title)}</strong></div>
            ${item.body ? `<p class="fq-meta" style="margin:0"><bdi>${esc(item.body)}</bdi></p>` : ""}
          </button>`).join("")
        : `<div class="fq-card pad center"><p class="fq-lead">ما عندك تنبيهات بعد.</p></div>`}
    </section>
    <nav class="fq-nav" aria-label="التنقل"><div class="fq-nav-row">
      <button class="fq-tab" type="button" data-action="supplier-home"><span class="fq-tab-wrap">${ic("file-text", 24)}</span><span>طلبات التسعير</span></button>
      <button class="fq-tab" type="button" data-action="supplier-signout"><span class="fq-tab-wrap">${ic("user", 24)}</span><span>خروج</span></button>
    </div></nav>`;
  }
  return `${fqHead({ title: "طلبات التسعير", mark: true, end: bell })}
  <section class="fq-body tight">
    ${pending
      ? `<div class="fq-card pad grey"><h2 class="fq-h2" style="font-size:17px">حسابك تحت المراجعة</h2>
          <p class="fq-lead">سجّلنا طلب انضمامك. لين نربط حسابك بإعلاناتك، ما تقدر تشوف طلبات هنا — وإذا وصلك رابط طلب من فرق، افتحه وهو يربط حسابك تلقائياً.</p></div>`
      : `<div class="fq-pills" style="overflow-x:auto;flex-wrap:nowrap;padding-bottom:2px">${SUPPLIER_FILTERS.map((item) => {
          const n = counts[item.key];
          return `<button class="fq-pill${state.supplierFilter === item.key ? " on" : ""}" type="button" data-action="supplier-filter" data-key="${item.key}" style="white-space:nowrap">${esc(item.label)}${n != null ? ` (${formatCount(n)})` : ""}</button>`;
        }).join("")}</div>`}
    ${state.supplierError ? `<p class="fq-small" style="color:#b3402a">${esc(state.supplierError)}</p>` : ""}
    ${rows.length
      ? rows.map(card).join("")
      : pending
        ? ""
        : `<div class="fq-card pad center"><p class="fq-lead">ما وصلك طلب بعد. أول ما يختارك عميل، يوصلك هنا مع إشعار.</p></div>`}
  </section>
  <nav class="fq-nav" aria-label="التنقل"><div class="fq-nav-row">
    <button class="fq-tab on" type="button" data-action="supplier-home" aria-current="page"><span class="fq-tab-wrap">${ic("file-text", 24)}</span><span>طلبات التسعير</span></button>
    <button class="fq-tab" type="button" data-action="supplier-signout"><span class="fq-tab-wrap">${ic("user", 24)}</span><span>خروج</span></button>
  </div></nav>`;
}

// The supplier's side of the item, reached from the link in his Haraj message. It is the same
// conversation the customer sees, from the other end: his own messages on the right, the
// customer's on the left, his prices drawn as offer cards. He never sees another supplier.
function renderSeller() {
  const fromList = state.sellerFrom === "supplier-requests" && state.supplierToken;
  const view = state.seller;
  const need = view?.need || view?.original_text || "";
  const priced = (view?.messages || []).filter((item) => item.sender_role === "seller" && messagePrice(item) != null);
  const mine = priced.length ? messagePrice(priced[priced.length - 1]) : null;
  const head = fqHead({
    title: need || "عرض سعر",
    sub: view ? [cityLabel(view.city), mine != null ? money(mine) : "لم تقدّم سعرًا بعد"].filter(Boolean).join(" · ") : "",
    mark: !fromList,
    back: fromList ? "supplier-home" : "",
    backStart: fromList,
  });

  if (state.sellerError) {
    const missing = state.sellerError === "missing";
    return `${head}<section class="fq-body center" role="alert">
      <div class="fq-blob warn">${ic(missing ? "alert-triangle" : "info", 48)}</div>
      <div><h1 class="fq-h2">${missing ? "الرابط غير صالح أو انتهت صلاحيته" : "ما قدرنا نفتح الطلب"}</h1>
        <p class="fq-lead">${missing ? "تأكد إنك فتحت الرابط كامل من رسالة فرق." : "تأكد من الاتصال وجرّب مرة ثانية."}</p></div>
      ${missing ? "" : `<button class="fq-btn" type="button" data-action="seller-reload">حاول مرة ثانية</button>`}
    </section>`;
  }
  if (!view) {
    return `${head}<section class="fq-chat" aria-busy="true">
      <div class="fq-msg"><div class="fq-skel" style="width:64%;height:62px;border-radius:18px"></div></div>
      <div class="fq-msg mine"><div class="fq-skel" style="width:52%;height:70px;border-radius:18px"></div></div>
    </section>`;
  }

  const closed = view.offers_open === false;
  const draft = state.sellerDraft || { body: "", amount: "", included: true, delivery: "" };
  // The invite already reached him in Haraj; repeating it here is noise.
  const messages = (view.messages || [])
    .filter((item) => item.body || item.offer)
    .filter((item) => !(item.sender_role !== "seller" && /^\s*السلام عليكم عزيزي البائع/.test(item.body || "")));
  const facts = [
    ["المطلوب", need],
    ["المدينة", view.city ? cityLabel(view.city) : ""],
    ["التفاصيل", view.notes || ""],
  ].filter(([, value]) => value);

  const bubbles = messages
    .map((item) => {
      const me = item.sender_role === "seller";
      const price = messagePrice(item);
      const time = `<span class="fq-time">${esc(chatTime(item.created_at))}</span>`;
      const body = esc(item.body || "").replace(/\n/g, "<br>");
      if (me) {
        const offer = price != null
          ? `<div class="fq-offer" style="color:var(--fq-success)">
              <div class="head"><span></span><span class="amount">${esc(money(price))}</span></div>
              ${body ? `<p class="fq-offer-note">${body}</p>` : ""}
            </div>
            <div class="fq-offer-foot">${time}<span style="color:var(--fq-success)">سعرك المقدَّم ⚡</span></div>`
          : "";
        return `<div class="fq-msg mine"><div style="display:flex;flex-direction:column;gap:4px;align-items:flex-start">
          <span class="fq-who mineName">أنت (المورد)</span>
          <div class="fq-mine-bub">${offer || `${body ? `<p>${body}</p>` : ""}${time}`}</div></div></div>`;
      }
      return `<div class="fq-msg">
        <span class="fq-av" style="background:var(--fq-light);color:var(--fq-deep)">ع</span>
        <div class="fq-grp">
          <span class="fq-who" style="color:var(--fq-deep)">العميل</span>
          <div class="fq-bub" style="background:var(--fq-surface);border-left-color:var(--fq-line);border:1px solid var(--fq-line);border-left-width:2px">
            ${body ? `<p>${body}</p>` : ""}${time}</div>
        </div></div>`;
    })
    .join("");

  return `${head}
    <div class="fq-offers-bar">
      ${facts.map(([label, value]) => `<div class="mini"><span><bdi>${esc(value)}</bdi></span><span class="fq-meta">${esc(label)}</span></div>`).join("")}
      ${(view.attachments || []).length
        ? `<div style="display:flex;gap:10px;flex-wrap:wrap">${view.attachments
            .map((item) => `<a class="fq-link" href="/v1/seller/${encodeURIComponent(state.sellerToken)}/attachments/${encodeURIComponent(item.id)}" target="_blank" rel="noopener">${ic("paperclip", 14)} ${esc(item.filename || "مرفق")}</a>`)
            .join("")}</div>`
        : ""}
    </div>
    <section class="fq-chat" id="chat-wall">
      <div class="fq-sys">طلب تسعير من عميل عبر فرق — ردّك يوصله مباشرة</div>
      ${bubbles || `<div class="fq-sys">ابدأ بتقديم سعرك أو اسأل العميل عن التفاصيل.</div>`}
      ${view.awarded_to_me ? sellerAwarded(view) : ""}
    </section>
    ${closed
      ? `<div class="fq-card pad grey" style="margin:16px" role="status">
          <h2 class="fq-h2" style="font-size:17px">لم يتم اختيار عرضك لهذا الطلب</h2>
          <p class="fq-lead">اختار العميل مورداً آخر. لا تقلق — بنرسل لك فرص تسعير جديدة ومناسبة لمجالك.</p>
          ${state.supplierToken
            ? `<button class="fq-btn ghost r14" type="button" data-action="supplier-home">شاهد الفرص المتاحة ←</button>`
            : `<button class="fq-btn ghost r14" type="button" data-action="supplier-join">سجّل كمورد لتوصلك الطلبات مباشرة</button>`}
        </div>`
      : `<form id="seller-reply" novalidate>
          ${state.sellerFormError ? `<div class="fq-target" style="background:#fdeee9;color:#b3402a" role="alert">${esc(state.sellerFormError)}</div>` : ""}
          ${state.sellerSent ? `<div class="fq-target" role="status">${ic("check-circle", 14)}<span>وصل ردّك للعميل. تقدر ترسل تحديث إذا تغيّر السعر.</span></div>` : ""}
          <div class="fq-pricebar${state.sellerPriceOpen ? "" : " shut"}">
            <button class="fq-pricetoggle" type="button" data-action="toggle-price">${ic("tag", 14)}<span>${mine != null ? "حدّث السعر" : "أضف سعرك"}</span></button>
            ${state.sellerPriceOpen ? `<div class="fields">
              <div class="fq-inp"><input name="offer_amount" inputmode="decimal" autocomplete="off" placeholder="السعر (ر.س)" value="${esc(draft.amount)}" dir="ltr" style="text-align:end"></div>
              <label class="fq-check"><input type="checkbox" name="delivery_included" ${draft.included ? "checked" : ""}><span>شامل التوصيل</span></label>
              <div class="fq-inp"><input name="delivery_price" inputmode="decimal" autocomplete="off" placeholder="سعر التوصيل (اختياري)" value="${esc(draft.delivery)}" dir="ltr" style="text-align:end"></div>
            </div>` : ""}
          </div>
          <div class="fq-composer">
            <button class="fq-send" type="submit" aria-label="إرسال" ${state.sellerBusy ? "disabled" : ""}>${state.sellerBusy ? `<span class="fq-arc" style="width:18px;height:18px"></span>` : ic("send", 18)}</button>
            <div class="fq-inputg">
              <textarea name="body" rows="1" maxlength="1000" placeholder="اكتب للعميل… تفاصيل السعر أو سؤال">${esc(draft.body)}</textarea>
            </div>
          </div>
          <p class="fq-meta" style="text-align:center;padding:0 16px 12px">فرق ما يطلب منك أي دفع أو بيانات بنكية أو كلمة مرور على هذه الصفحة.</p>
        </form>`}`;
}

// ---------------------------------------------------------------------------
// Terms, privacy and the refund policy: the text lives in web/legal/*.ar.md and is drawn
// here, open to anyone signed in or not.
// ---------------------------------------------------------------------------
const LEGAL_TITLES = { terms: "الشروط والأحكام", privacy: "سياسة الخصوصية", refunds: "الإلغاء والاسترداد" };

async function openLegal(doc) {
  const name = LEGAL_TITLES[doc] ? doc : "terms";
  if (state.view !== "legal") state.legalFrom = state.view === "auth" || !state.token ? "auth" : state.view;
  state.view = "legal";
  if (state.legalDoc !== name) state.legalText = "";
  state.legalDoc = name;
  state.legalError = false;
  render();
  if (state.legalText) return;
  try {
    const response = await fetch(`/legal/${name}.ar.md`);
    if (!response.ok) throw new Error("legal");
    const text = await response.text();
    if (state.legalDoc === name) state.legalText = text;
  } catch (_error) {
    state.legalError = true;
  }
  if (state.view === "legal") render();
}

// Enough Markdown for these documents: headings, paragraphs, bullet lists, and a quoted note.
function markdownHtml(source) {
  const out = [];
  let list = false;
  const close = () => {
    if (list) out.push("</ul>");
    list = false;
  };
  const inline = (text) => esc(text).replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  for (const raw of String(source || "").split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) {
      close();
      continue;
    }
    const heading = line.match(/^(#{1,3})\s+(.*)$/);
    if (heading) {
      close();
      const level = heading[1].length + 1;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
    } else if (/^[-*]\s+/.test(line)) {
      if (!list) out.push("<ul>");
      list = true;
      out.push(`<li>${inline(line.replace(/^[-*]\s+/, ""))}</li>`);
    } else if (line.startsWith(">")) {
      close();
      out.push(`<div class="fq-notice">${inline(line.replace(/^>\s*/, ""))}</div>`);
    } else {
      close();
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  close();
  return out.join("");
}

// The operator, its commercial registration and how to reach it. A paying customer and
// the payment provider both need this visible, not buried in a document.
const OPERATOR = { name: "مؤسسة فارق تكنولوجي", cr: "7052132144", email: "support@farq.sa" };

function operatorNote() {
  return `<p class="fq-meta" style="text-align:center;line-height:1.9;margin:4px 0 0">
    ${esc(OPERATOR.name)} · السجل التجاري ${esc(OPERATOR.cr)}<br>
    <a class="fq-link" href="mailto:${esc(OPERATOR.email)}">${esc(OPERATOR.email)}</a>
  </p>`;
}

// SC04 + SC08. The phone and the place are here only because the customer chose to give
// them after picking this supplier; nothing about winning reveals them on its own.
function sellerAwarded(view) {
  const contact = view.contact;
  const maps = contact && contact.lat != null && contact.lng != null
    ? `https://www.google.com/maps/search/?api=1&query=${contact.lat},${contact.lng}`
    : "";
  return `<div class="fq-card pad" style="border-color:var(--fq-success)">
    <div class="fq-row"><span class="fq-tag ok">✓ تمت الترسية عليك</span>
      <strong style="font-size:17px">🎉 تم اختيار عرضك!</strong></div>
    <p class="fq-lead">اختارك العميل لتنفيذ الطلب. نسّق معه من نفس المحادثة تحت.</p>
    ${contact
      ? `<hr class="fq-line">
        <p class="fq-meta">شارك العميل بياناته معك لهذا الطلب:</p>
        <div class="fq-actions" style="gap:10px">
          <a class="fq-btn r14" href="tel:${esc(contact.phone)}">${ic("phone", 16)} اتصال مباشر</a>
          ${maps ? `<a class="fq-btn ghost outline-deep r14" href="${esc(maps)}" target="_blank" rel="noopener">${ic("map-pin", 16)} الموقع الجغرافي</a>` : ""}
        </div>`
      : `<hr class="fq-line"><p class="fq-meta">إذا احتجت رقمه أو موقعه، اطلبه منه في المحادثة — هو اللي يقرر يشاركه.</p>`}
  </div>`;
}

function renderLegal() {
  const title = LEGAL_TITLES[state.legalDoc] || LEGAL_TITLES.terms;
  const others = Object.keys(LEGAL_TITLES).filter((doc) => doc !== state.legalDoc);
  const body = state.legalError
    ? `<div class="fq-body center" role="alert"><h1 class="fq-h2">ما قدرنا نفتح الصفحة</h1><button class="fq-btn" type="button" data-action="legal" data-doc="${esc(state.legalDoc)}">حاول مرة ثانية</button></div>`
    : state.legalText
      ? `<article class="fq-legaldoc">${markdownHtml(state.legalText)}</article>`
      : `<div class="fq-card"><span class="fq-skel" style="height:18px;width:60%;border-radius:8px"></span><span class="fq-skel" style="height:14px;width:90%;border-radius:8px"></span><span class="fq-skel" style="height:14px;width:80%;border-radius:8px"></span></div>`;
  return `${fqHead({ title, back: "legal-back" })}
  <section class="fq-body tight">
    ${body}
    <div style="display:flex;gap:16px;justify-content:center;flex-wrap:wrap">
      ${others.map((doc) => `<a class="fq-link" href="/${doc}" data-action="legal" data-doc="${doc}">${esc(LEGAL_TITLES[doc])}</a>`).join("")}
    </div>
    ${operatorNote()}
  </section>`;
}

// A sheet that opens takes the focus; Tab stays inside it; Escape closes it.
let lastSheet = false;
function manageSheetFocus() {
  const sheet = app.querySelector(".fq-sheet");
  if (sheet && !lastSheet) {
    const first = sheet.querySelector("input:not([type=hidden]):not([hidden]), textarea, select, button:not([disabled])");
    (first || sheet).focus?.();
  }
  lastSheet = Boolean(sheet);
}

function closeTopSheet() {
  if (state.pushAsk) state.pushAsk = false;
  else if (state.editing != null) state.editing = null;
  else if (state.view === "detail") {
    state.view = "flow";
    state.active = null;
  } else if (state.capSheet) state.capSheet = false;
  else if (state.pickerOpen) state.pickerOpen = false;
  else if (state.attachOpen) state.attachOpen = false;
  else if (state.awardPick) state.awardPick = null;
  else return false;
  render();
  return true;
}

// The supplier surface has its own session and never shows the customer's banners.
const SUPPLIER_VIEWS = new Set(["supplier-auth", "supplier-requests", "seller"]);

const VIEWS = {
  home: renderHome,
  "city-ask": renderCityAsk,
  understand: renderUnderstand,
  flow: renderFlow,
  detail: renderFlow,
  review: renderReview,
  sending: renderSending,
  sent: renderSent,
  requests: renderRequests,
  thread: renderThread,
  compare: renderCompare,
  awarded: renderAwarded,
  account: renderAccount,
  verify: renderVerify,
  "supplier-auth": renderSupplierAuth,
  "supplier-requests": renderSupplierRequests,
  notifications: renderNotifications,
  "notify-settings": renderNotifySettings,
  subscribe: renderSubscribe,
  auth: () => (isFarqEmbed() && !state.farqLink && !state.farqFallback ? renderHome() : renderAuth()),
  seller: renderSeller,
  legal: renderLegal,
};

let lastView = "";
function render() {
  clearInterval(poll);
  const view = VIEWS[state.view] || renderHome;
  const focused = document.activeElement?.id;
  const keepScroll = state.view === "flow" && lastView === "flow" ? window.scrollY : null;
  const wall = document.getElementById("chat-wall");
  const stick = (state.view === "thread" || state.view === "seller") && (state.stickChat || !wall || wall.scrollHeight - wall.scrollTop - wall.clientHeight < 140);
  // A streaming search re-renders the list many times; that is not an arrival.
  const arriving = state.view !== lastView && !(state.view === "flow" && state.results.length);
  const banner = state.token && !SUPPLIER_VIEWS.has(state.view) && state.view !== "verify" ? verifyBanner() : "";
  app.innerHTML = shell(`${banner}${view()}${state.pushAsk && state.view !== "sent" ? pushPrompt() : ""}${state.toast ? `<div class="fq-toast" role="status">${esc(state.toast)}</div>` : ""}`);
  document.body.classList.toggle("fq-web", window.innerWidth >= 900);
  document.body.classList.toggle("fq-auth", state.view === "auth");
  // the choreographed entrance belongs to the screen, not to every render of it
  if (arriving) {
    app.firstElementChild?.setAttribute("data-enter", "");
    // a new screen starts at its top, not at the scroll position of the one before
    const sheetOnly = (lastView === "flow" && state.view === "detail") || (lastView === "detail" && state.view === "flow");
    if (!sheetOnly) window.scrollTo(0, 0);
    lastView = state.view;
  }
  bindImages(app);
  bindGallery(app);
  bindCounter(app);
  bindGrow(app);
  bindChat(app);
  if (state.view === "flow" && state.seenCards) for (const result of state.results) state.seenCards.add(resultKey(result));
  if (focused) document.getElementById(focused)?.focus();
  // The business description re-renders while it is being typed in, so the caret goes back
  // where the writer left it instead of jumping to the start of what he has written.
  if (focused === "join-desc" && state.supplierCaret != null) {
    const box = document.getElementById("join-desc");
    try {
      box?.setSelectionRange(state.supplierCaret, state.supplierCaret);
    } catch (_error) {}
  }
  if (keepScroll) window.scrollTo(0, keepScroll);
  if (stick) {
    state.stickChat = false;
    const next = document.getElementById("chat-wall");
    if (next) next.scrollTop = next.scrollHeight;
  }
  manageSheetFocus();
  syncUrl();
  scheduleDraftSave();
  if (state.view === "thread" && state.thread?.id) {
    // A reply is most likely soon after the conversation opens or a message goes out, so the
    // first two minutes poll every 4s; after that every 12s, and never while the tab is hidden
    // (it refreshes on return). Three quarters fewer calls on an idle conversation.
    const opened = Date.now();
    state.threadOpenedAt = opened;
    poll = setInterval(() => {
      if (document.visibilityState === "hidden" || !state.thread?.id) return;
      const quick = Date.now() - (state.lastChatSendAt || opened) < 120_000;
      const tick = Math.floor((Date.now() - opened) / 4000);
      if (!quick && tick % 3 !== 0) return;
      loadThread(state.thread.id, true).catch(() => {});
    }, 4000);
  }
  if (state.view === "subscribe" && state.subView === "review" && state.subActivePlan && state.subMountedPlan !== state.subActivePlan && !state.subMountFailed) mountPayment(state.subActivePlan);
}

// A conversation behaves like a chat: swipe a supplier's bubble to reply to it, and a
// chevron appears to jump back to the latest message when the customer has scrolled up.
function bindChat(root) {
  const wall = root.querySelector("#chat-wall");
  const jump = root.querySelector(".fq-tobottom");
  if (!wall) return;
  if (jump) {
    const check = () => {
      jump.hidden = wall.scrollHeight - wall.scrollTop - wall.clientHeight < 200;
    };
    wall.addEventListener("scroll", check, { passive: true });
    check();
  }
  let start = null;
  wall.addEventListener("touchstart", (event) => {
    const row = event.target.closest(".fq-msg[data-message]");
    if (!row || row.classList.contains("mine")) return;
    const touch = event.touches[0];
    start = { x: touch.clientX, y: touch.clientY, row };
  }, { passive: true });
  wall.addEventListener("touchmove", (event) => {
    if (!start) return;
    const touch = event.touches[0];
    const dx = touch.clientX - start.x;
    if (Math.abs(touch.clientY - start.y) > 24) { start.row.style.transform = ""; start = null; return; }
    if (dx > 0) start.row.style.transform = `translateX(${Math.min(dx, 56)}px)`;
  }, { passive: true });
  wall.addEventListener("touchend", (event) => {
    if (!start) return;
    const dx = (event.changedTouches[0]?.clientX ?? start.x) - start.x;
    start.row.style.transform = "";
    if (dx > 44) start.row.querySelector("[data-action=reply]")?.click();
    start = null;
  });
}

// The composer grows with the message, up to four lines, the way a chat input does.
function bindGrow(root) {
  root.querySelectorAll(".fq-inputg textarea, #composer textarea").forEach((node) => {
    const grow = () => {
      node.style.height = "auto";
      node.style.height = `${Math.min(node.scrollHeight, 96)}px`;
    };
    node.addEventListener("input", grow);
    grow();
  });
}

function toast(message) {
  state.toast = message;
  render();
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    state.toast = "";
    render();
  }, 2600);
}

// «لقينا 20 مورد» counts up to the new number instead of jumping.
function bindCounter(root) {
  const node = root.querySelector("[data-count]");
  if (!node) return;
  const target = Number(node.dataset.count || 0);
  const from = Number(state.shownCount || 0);
  if (from === target) return;
  if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    state.shownCount = target;
    node.textContent = foundLine(target);
    return;
  }
  const started = performance.now();
  const step = (now) => {
    const ratio = Math.min(1, (now - started) / 200);
    // counting up from nothing never flashes «no results» on the way
    const value = Math.max(target ? 1 : 0, Math.round(from + (target - from) * ratio));
    node.textContent = foundLine(value);
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
  if (event.trace_id) state.traceId = event.trace_id;
  state.intent = event.intent;
  state.results = event.results || [];
  rememberGroups(event.groups);
  state.searchState = event.state;
  state.clarification = event.clarification_question || "";
  state.partial = false;
  const asking = event.state === "CLARIFICATION_REQUIRED" || event.state === "LOCATION_AMBIGUOUS";
  state.notice = asking ? "" : finalNotice(event.state, state.results.length);
}

// A search gives up after this long: whatever arrived stays on screen, and an empty screen
// says the search failed and offers to try again. The spinner never outlives the search.
const SEARCH_TIMEOUT_MS = 30000;

function failSearch(status) {
  state.searching = false;
  state.partial = false;
  if (state.results.length) {
    // what arrived is real; say the rest did not
    state.searchState = "PARTIAL_RESULTS";
    state.notice = finalNotice(status === "TIMEOUT" ? "TIMEOUT" : "PARTIAL_RESULTS", state.results.length);
  } else {
    state.searchState = status;
    state.notice = status === "TIMEOUT" ? "البحث طوّل أكثر من اللازم وما رجعت نتائج." : "ما قدرنا نكمل البحث بسبب خلل في الاتصال.";
  }
}

async function runSearch(text, city = "") {
  const query = (text || "").trim();
  if (!query) return;
  // The search phrase is kept on its own; the customer's own words (state.originalText) stay as typed.
  state.searchText = query;
  if (!state.query) state.query = query;
  state.city = city || state.city || cityInText(query);
  // Without a city the search is nationwide; ask first, then search inside that city.
  if (!state.city && state.cities.length) {
    state.view = "city-ask";
    state.cityMode = "request";
    state.results = [];
    state.intent = null;
    state.searching = false;
    render();
    return;
  }
  const asked = state.city && !cityInText(query) ? `${query} ${cityLabel(state.city)}` : query;
  const searchId = `${Date.now()}`;
  const mine = () => state.searchId === searchId;
  state.view = "flow";
  state.searchId = searchId;
  state.partial = true;
  state.searching = true;
  state.seenCards = new Set();
  state.shownCount = 0;
  state.searchState = "";
  state.results = [];
  state.resultNeeds = new Map();
  state.streamNeeds = [];
  state.needFilter = "";
  state.intent = null;
  state.clarification = "";
  state.notice = "نفهم طلبك…";
  state.selected.clear();
  render();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), SEARCH_TIMEOUT_MS);
  const started = Date.now();
  let finished = false;
  try {
    const headers = { "Content-Type": "application/json" };
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    const response = await fetch("/v1/search/stream", { method: "POST", headers, body: JSON.stringify({ query: asked }), signal: controller.signal });
    if (!response.ok || !response.body) throw new Error("stream");
    await readNdjson(response, (event) => {
      if (!mine()) return;
      if (event.trace_id) state.traceId = event.trace_id;
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
        // partial batches move the counter and the list, and say nothing
        const hadCards = state.results.length > 0;
        if ((event.results || []).length) state.searching = false;
        if (event.scanned != null) state.scanned = event.scanned;
        state.results = event.results || [];
        rememberGroups(event.groups);
        if (event.need && event.results?.length && !event.groups) {
          // The server searches the items in the order M02 listed them, and each batch names
          // its item by the server's own reading («إصلاح تسريب»). The i-th distinct name is
          // the i-th item, so the card carries the label the customer saw and picked - the
          // same label the send uses to tell each supplier which item is his.
          const active = (state.needs || []).filter((item) => item.on);
          let at = Number.isInteger(event.need_index) ? event.need_index : state.streamNeeds.indexOf(event.need);
          if (at < 0) at = state.streamNeeds.push(event.need) - 1;
          const label = active.length === 1 ? needLabel(active[0]) : active[at] ? needLabel(active[at]) : event.need;
          for (const result of event.results) if (!state.resultNeeds.has(resultKey(result))) state.resultNeeds.set(resultKey(result), label);
        }
        state.partial = true;
        state.searchState = event.state;
        if (state.results.length) state.notice = "لقينا خيارات مناسبة، وقاعدين ندور لك على أكثر.";
        // A batch that arrives on a list already on screen adds its new cards to the end
        // instead of redrawing eighty cards and their pictures; the search's end reorders.
        if (hadCards && state.view === "flow" && patchResults()) return;
      } else if (event.type === "done") {
        finished = true;
        state.searching = false;
        applyDone(event);
        // one rising pair, only once, and only when the search actually found something
        if (state.results.length) cueOnce(`search:${state.searchId}`, "found", 10);
      }
      render();
    });
    // a stream that closes without its «done» did not finish
    if (!finished && mine()) {
      failSearch("INTERNAL_ERROR");
      render();
    }
  } catch (_error) {
    if (!mine()) return;
    const timedOut = controller.signal.aborted;
    const left = SEARCH_TIMEOUT_MS - (Date.now() - started);
    // The plain endpoint is a second chance only when the stream failed fast and nothing arrived.
    if (!timedOut && !state.results.length && left > 3000) {
      const fallback = new AbortController();
      const fallbackTimer = setTimeout(() => fallback.abort(), left);
      try {
        const done = await api("/v1/search", { method: "POST", json: { query: asked }, signal: fallback.signal });
        if (!mine()) return;
        state.searching = false;
        applyDone(done);
        if (state.results.length) cueOnce(`search:${state.searchId}`, "found", 10);
      } catch (_fallback) {
        if (!mine()) return;
        failSearch(fallback.signal.aborted ? "TIMEOUT" : "INTERNAL_ERROR");
      } finally {
        clearTimeout(fallbackTimer);
      }
    } else {
      failSearch(timedOut ? "TIMEOUT" : "INTERNAL_ERROR");
    }
    render();
  } finally {
    clearTimeout(timer);
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

// Sellers per item is a plan property now, not "six on the trial and no ceiling after".
function sellerCap() {
  return allowance().sellers_per_item || TRIAL_SELLERS;
}

function toggle(key) {
  if (state.selected.has(key)) state.selected.delete(key);
  else if (state.selected.size >= sellerCap()) {
    state.capSheet = true;
    render();
    return;
  } else selectResult(key);
  if (!patchSelection(key)) render();
}

function patchResults() {
  const list = app.querySelector(".fq-body .fq-stagger");
  const row = app.querySelector(".fq-filters-row");
  if (!list || !row || state.needFilter) return false;
  const present = new Set([...list.querySelectorAll("button[data-action=toggle]")].map((node) => node.dataset.key));
  const fresh = state.results.filter((result) => !present.has(resultKey(result)));
  newCardsInBatch = 0;
  if (fresh.length) list.insertAdjacentHTML("beforeend", fresh.map(renderCard).join(""));
  bindImages(list);
  for (const result of state.results) state.seenCards.add(resultKey(result));
  const tabs = [...new Set(state.results.map(needOf).filter(Boolean))];
  row.innerHTML = filtersRow(tabs, visibleResults());
  const notice = app.querySelector(".fq-body > p.fq-meta[aria-live]");
  if (notice) notice.textContent = state.notice;
  scheduleDraftSave();
  return true;
}

// A tap on «اختر هذا المورد» used to rebuild the whole list - eighty cards and their
// pictures - which on a phone read as the page hanging and flashing. Only the card that was
// tapped and the bar that counts the picks change, so only they are touched.
function patchSelection(key) {
  if (state.view !== "flow" || state.needFilter && !visibleResults().some((item) => resultKey(item) === key)) return false;
  const button = app.querySelector(`button[data-action="toggle"][data-key="${CSS.escape(key)}"]`);
  const card = button?.closest(".fq-result");
  const body = app.querySelector(".fq-body");
  if (!button || !card || !body) return false;
  const selected = state.selected.has(key);
  card.classList.toggle("picked", selected);
  button.classList.toggle("on", selected);
  button.setAttribute("aria-pressed", String(selected));
  button.innerHTML = `${ic(selected ? "check" : "plus", 16)}<span>${selected ? "مختار — اضغط للإزالة" : "اختر هذا المورد"}</span>`;
  const count = state.selected.size;
  let bar = body.querySelector(".fq-sticky");
  if (!count) {
    bar?.remove();
  } else {
    const html = `<button class="fq-btn" type="button" data-action="review"><span class="count">${formatCount(count)}</span>متابعة مع ${esc(suppliers(count))}</button>`;
    if (bar) bar.innerHTML = html;
    else {
      bar = document.createElement("div");
      bar.className = "fq-sticky";
      bar.innerHTML = html;
      body.appendChild(bar);
    }
  }
  scheduleDraftSave();
  return true;
}

function openQuote(key) {
  selectResult(key);
  if (!state.selected.has(key)) return;
  state.view = "review";
  render();
}

// M09_SendingProcessing then M10_RequestSent — nodes 85:338 and 27:374.
// What a supplier is asked to price: the item in the customer's words. The name alone
// («سباك») tells him nothing; the segment of the request it came from does.
function needText(item) {
  if (!item) return "";
  const name = needLabel(item);
  const desc = String(item.desc || "").trim();
  if (!desc || desc === name) return name;
  return desc.includes(name) ? desc : `${name}: ${desc}`;
}

// The details that do not fit in one line go to the notes, one line per item.
function requestNotes(active) {
  const lines = [];
  const detailed = active.length > 1 || active.some((item) => item.qty > 1 || item.when);
  if (detailed) {
    for (const item of active) {
      const parts = [needText(item)];
      if (item.qty > 1) parts.push(`الكمية: ${formatCount(item.qty)}${item.unit ? ` ${item.unit}` : ""}`);
      if (item.when) parts.push(`الموعد: ${item.when}`);
      if (item.district) parts.push(`الحي: ${item.district}`);
      lines.push(`• ${parts.join(" — ")}`);
    }
  }
  if (state.note.trim()) lines.push(state.note.trim());
  return lines.join("\n") || null;
}

async function sendRequest() {
  const city = customerCity();
  if (!state.selected.size || !city || state.busy) return;
  if (allowance().items_left <= 0) {
    state.view = "subscribe";
    state.subView = "limit";
    state.resumeAfterPay = true;
    render();
    return;
  }
  state.busy = true;
  state.sendingTo = state.selected.size;
  state.view = "sending";
  render();
  // One key per attempt at this request: a retry after a dropped answer replays the same
  // request on the server instead of creating a second one for the same suppliers.
  if (!state.sendKey) state.sendKey = (crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`);
  try {
    await ensureAuth();
    const attributes = {};
    if (state.intent?.material?.value) attributes.material = state.intent.material.value;
    if (state.intent?.condition?.value === "used") attributes.condition = "مستعمل";
    if (state.intent?.condition?.value === "new") attributes.condition = "جديد";
    if (state.intent?.year?.value) attributes.year = state.intent.year.value;
    const active = (state.needs || []).filter((item) => item.on);
    const byLabel = new Map(active.map((item) => [needLabel(item), item]));
    const itemFor = (result) => byLabel.get(needOf(result)) || (active.length === 1 ? active[0] : null);
    if (active.length > 1) {
      for (const result of state.selected.values()) if (!itemFor(result)) {
        console.warn("taseer: a picked supplier carries no item label", needOf(result));
        break;
      }
    }
    // One request per item: each item gets its own conversation, its own offers and its own
    // winner, and a supplier reads only the line that is his. A supplier picked from a card
    // that carries no item label goes with the first item.
    const picked = [...state.selected.values()];
    const groups = active.length > 1
      ? active.map((item) => ({ item, results: picked.filter((result) => itemFor(result) === item) })).filter((group) => group.results.length)
      : [{ item: active[0] || null, results: picked }];
    const orphans = active.length > 1 ? picked.filter((result) => !itemFor(result)) : [];
    if (orphans.length) (groups[0] || groups.push({ item: active[0], results: [] }) && groups[0]).results.push(...orphans);
    const created = [];
    for (const [index, group] of groups.entries()) {
      const record = await api("/v1/requests", {
        method: "POST",
        headers: { "Idempotency-Key": `${state.sendKey}-${index}` },
        json: {
          // the words the customer typed, never the search phrase built from them
          original_text: state.originalText || state.query,
          need: group.item ? needText(group.item) : state.intent?.need || null,
          notes: requestNotes(group.item ? [group.item] : []),
          city,
          attributes,
          // The server only accepts sellers a search showed; a search run before signing in is named here.
          trace_id: state.traceId || null,
          recipients: group.results.map((result) => {
            const seller = sellerOf(result);
            const recipient = { seller_id: seller.id, seller_name: seller.name || "مورد", ad_id: result.ad?.id || null };
            if (result.ad?.url) recipient.listing_url = result.ad.url;
            return recipient;
          }),
        },
      });
      created.push(record);
      for (const item of state.files) {
        const form = new FormData();
        if (item.file.type === "application/pdf") form.append("file", item.file);
        else {
          const photo = await preparePhoto(item.file).catch(() => null);
          if (!photo) continue;
          form.append("file", photo.blob, photo.name);
        }
        await api(`/v1/requests/${record.id}/attachments`, { method: "POST", form }).catch(() => {});
      }
    }
    const first = created[0];
    state.sentInfo = {
      sellers: created.reduce((sum, record) => sum + (record.recipients?.length || 0), 0) || state.sendingTo,
      need: created.length > 1 ? created.map((record) => record.need).filter(Boolean).join(" · ") : first.need || state.originalText || state.query,
      needs: created.map((record) => ({ id: record.id, need: record.need, sellers: record.recipients?.length || 0 })),
      id: first.id,
    };
    state.sendKey = "";
    state.busy = false;
    state.files = [];
    state.note = "";
    state.notice = "";
    state.selected.clear();
    for (const record of created) threadCache.set(record.id, record);
    state.thread = null;
    state.replyTo = null;
    // The journey is done: the draft goes, so neither Back nor a reload can bring it round again.
    clearDraft();
    state.view = "sent";
    state.replaceUrl = true;
    // N02 asks over the success screen, which is where the frame draws it
    if (!state.pushDismissed && state.pushState !== "on") state.pushAsk = true;
    render();
    // refresh the list behind the screen without navigating away from it
    api("/v1/requests", { quiet: true })
      .then((data) => {
        state.requests = data.requests || [];
        state.requestsLoaded = true;
        setUnread(state.requests);
      })
      .catch(() => {});
  } catch (error) {
    state.busy = false;
    if (error?.auth) {
      // the sign-in screen is up; it returns to the review with the same suppliers ticked,
      // and the send itself resumes once the session arrives
      state.notice = "";
      state.returnView = "review";
      state.pendingAuthAction = "send";
      saveDraft();
      render();
      return;
    }
    if (error?.status === 402) {
      // The server counted the free trial as used up.
      state.view = "subscribe";
      state.subView = "limit";
      state.resumeAfterPay = true;
      render();
      return;
    }
    if (error?.detail?.code === "EMAIL_NOT_VERIFIED") {
      loadVerification().catch(() => {});
      resendVerification().catch(() => {});
    }
    state.notice = error?.detail?.message || "ما قدرنا نرسل الطلب. جرّب مرة ثانية.";
    state.view = "review";
    state.replaceUrl = true;
    render();
  }
}

// M01 → M02: the parser splits the sentence into needs before any search runs.
// `fresh` marks a new request typed by the customer: only then do his words replace the saved ones.
async function startPricing(text, { fresh = true } = {}) {
  const query = (text || "").trim();
  if (!query) {
    // an empty request answers instead of doing nothing
    state.composerHint = "اكتب وش تبي نسعّر لك أول، مثلاً: سباك يصلح تسريب.";
    if (state.view !== "home") state.view = "home";
    render();
    document.getElementById("composer-query")?.focus();
    return;
  }
  state.composerHint = "";
  state.query = query;
  if (fresh || !state.originalText) state.originalText = query;
  // The city named in the sentence wins; else the one he used last time; else M02 asks for
  // it in place, so the journey does not gain a screen of its own for one tap.
  state.city = cityInText(query) || state.city || storedGet("farq.city") || "";
  state.cityPick = false;
  state.view = "understand";
  state.needs = null;
  state.editing = null;
  state.intentError = false;
  state.intentProblem = "";
  state.intentQuestion = "";
  state.results = [];
  state.searchState = "";
  state.selected.clear();
  render();
  const intentId = `${Date.now()}`;
  state.intentId = intentId;
  let data;
  try {
    data = await api("/v1/intent", { method: "POST", json: { query }, skipAuth: true });
  } catch (_error) {
    if (state.intentId !== intentId) return;
    // no fake M02 made of the raw words: say it failed, keep the text, offer a retry
    state.intentError = true;
    render();
    return;
  }
  if (state.intentId !== intentId) return;
  const all = (data.intents || []).length ? data.intents : [data.intent].filter(Boolean);
  const list = all.filter((intent) => intent.understood);
  state.intent = list[0] || all[0] || null;
  state.clarification = data.clarification_question || "";
  if (!list.length || data.state === "NOT_UNDERSTOOD") {
    state.intentProblem = "not-understood";
    render();
    return;
  }
  // The city is already chosen on this screen's way in; only a question about the item itself stops M02.
  const question = data.state === "CLARIFICATION_REQUIRED" ? data.clarification_question || "" : "";
  if (question && !question.includes("مدينة")) {
    state.intentProblem = "question";
    state.intentQuestion = question;
    render();
    return;
  }
  const cityOf = (intent) => (typeof intent?.location_city?.value === "string" ? intent.location_city.value : "") || state.city;
  state.needs = list.map((intent, index) => ({
    key: `n${index}`,
    // A part is named by the need («صدام كامري»), never by the car model alone.
    name: (intent.category?.value === "parts" ? stripCity(intent.need || "").split(/\s+/).slice(0, 3).join(" ") : "") || arabicOnly(intent.model?.value) || arabicOnly(intent.subcategory?.value) || arabicOnly(intent.category?.value) || stripCity(intent.need || query).split(/\s+/).slice(0, 2).join(" "),
    // the customer's own segment when the parser returns it, so «ستانلس» and «يصلح تسريب» survive
    // (a request with a single item is that item's segment in full)
    desc: stripCity(intent.segment_text || (list.length === 1 ? query : intent.need) || query),
    city: cityOf(intent),
    district: typeof intent?.location_district?.value === "string" ? intent.location_district.value : "",
    when: "",
    qty: Number(intent?.quantity?.value) || 1,
    unit: typeof intent?.quantity_unit === "string" ? intent.quantity_unit : intent?.quantity_unit?.value || "",
    on: true,
    intent,
  }));
  render();
}

// «سباك الرياض» reads «سباك» on a card that already says الرياض underneath.
function stripCity(text) {
  let value = String(text || "");
  for (const city of state.cities) {
    for (const word of new Set([city.label, city.value])) {
      const escaped = word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      value = value.replace(new RegExp(`\\s*(?:في\\s+|بال|ب)?${escaped}(?=\\s|$)`, "g"), " ");
    }
  }
  return value.replace(/\s+/g, " ").trim() || String(text || "").trim();
}

function searchFromNeeds() {
  const active = (state.needs || []).filter((item) => item.on);
  if (!active.length) return;
  // Each item searches by its own description; the quantity and the date are for the supplier,
  // not for the ad search, and would only narrow it wrongly.
  const text = active.map((item) => item.desc || needLabel(item)).filter(Boolean).join(" و ");
  state.intent = active[0].intent || state.intent;
  runSearch(text || state.originalText || state.query, state.city);
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
    state.lastChatSendAt = Date.now();
    state.chatFiles.forEach((item) => item.preview && URL.revokeObjectURL(item.preview));
    state.chatFiles = [];
    state.replyTo = null;
    state.stickChat = true;
    state.sending = false;
    await loadThread(thread.id, true);
    render();
  } catch (error) {
    state.sending = false;
    state.notice = error.status === 413 ? "الملف أكبر من ٤ ميجا" : error.status === 415 ? "نرسل صور وملفات PDF فقط" : error.detail?.message || "ما انرسلت الرسالة، جرّب مرة ثانية";
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
  const before = state.thread;
  const previous = JSON.stringify(before?.messages || []);
  const first = !before;
  state.thread = thread;
  if (first) state.stickChat = true;
  else announceArrivals(before, thread);
  if (!silent || first || previous !== JSON.stringify(thread.messages || [])) render();
}

// The payoff for the waiting: a reply, an offer, or an offer that undercuts the rest,
// each with its own cue.
// The payoff for the waiting. One cue per arrival, never two: a price that beats every
// other offer outranks a price, and a price outranks a plain reply.
function announceArrivals(before, after) {
  const seen = new Set((before.messages || []).map((item) => item.id));
  const fresh = (after.messages || []).filter((item) => item.sender_role === "seller" && !seen.has(item.id));
  if (!fresh.length) return;
  const older = (before.offers || []).map((item) => item.total_price).filter((value) => value != null);
  const priced = fresh.filter((item) => messagePrice(item) != null);
  // «Lowest» means it beat a price already on the table; the first price is just a price.
  const undercut = older.length ? priced.filter((item) => messagePrice(item) < Math.min(...older)) : [];
  if (undercut.length) cueOnce(`lower:${undercut[0].id}`, "lower", [10, 40, 16]);
  else if (priced.length) cueOnce(`offer:${priced[0].id}`, "offer", 12);
  else cueOnce(`reply:${fresh[0].id}`, "reply", 10);
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

async function loadSubscribe({ keepView = false, allowSignedOut = false } = {}) {
  // /plans is read by people deciding whether to sign up, and by a payment provider
  // reviewing the service. Demanding an account there hid the prices from exactly the
  // two audiences the page exists for.
  if (!allowSignedOut) await ensureAuth();
  state.view = "subscribe";
  if (!keepView) state.subView = state.subView || "my-plan";
  state.subError = "";
  state.subMountedPlan = "";
  state.subMountFailed = false;
  render();
  try {
    // Signed out on /plans, only the public half of the pair can be asked for.
    const [plans, me] = await Promise.all([
      api("/v1/subscriptions/plans", { skipAuth: true }),
      state.token ? api("/v1/subscriptions/me") : Promise.resolve(null),
    ]);
    state.subPlans = plans.plans || [];
    state.paymentsAvailable = plans.payments_available;
    if (me) state.subStatus = me;
  } catch (_error) {
    state.subError = "ما قدرنا نجيب بيانات الاشتراك. جرّب مرة ثانية.";
  }
  render();
  // A payment that left for 3-D Secure and never came back to /subscribe/callback (tab
  // closed, redirect lost) is checked again here; the server settles it once Moyasar says paid.
  resumePendingPayment().catch(() => {});
}

// «حسابي» needs the plan name and the usage count without leaving the account screen.
async function loadSubStatus() {
  if (!state.token) return;
  try {
    const [plans, me] = await Promise.all([api("/v1/subscriptions/plans", { skipAuth: true, quiet: true }), api("/v1/subscriptions/me", { quiet: true })]);
    state.subPlans = plans.plans || [];
    state.paymentsAvailable = plans.payments_available;
    state.subStatus = me;
    if (state.view === "account" || state.view === "subscribe") render();
  } catch (_error) {}
}

// N01 builds its feed from the requests list: each offer, reply and award is one line.
// N01 builds its feed from the requests list: each offer, reply and award is one line.
// It loads the list itself, because loadRequests() would switch the screen to «طلباتي».
async function loadNotifications() {
  state.view = "notifications";
  render();
  try {
    await ensureAuth();
    state.requests = (await api("/v1/requests")).requests || [];
    setUnread(state.requests);
  } catch (_error) {
    return;
  }
  const feed = [];
  for (const item of state.requests) {
    const need = item.need || item.original_text || "";
    const at = item.last_message_at || item.created_at;
    if (item.awarded_seller_id) feed.push({ kind: "award", need, text: "تم اعتماد العرض الفائز", at, request_id: item.id, unread: false });
    if (item.latest_offer_amount != null) feed.push({ kind: "offer", need, text: `وصل عرض بقيمة ${money(item.latest_offer_amount)}`, at, request_id: item.id, unread: Boolean(item.unread_count) });
    if (item.unread_count) feed.push({ kind: "message", need, text: item.last_message || "رسالة جديدة في المحادثة", at, request_id: item.id, unread: true });
  }
  // «تحديد الكل كمقروء» is remembered on this device until the server keeps read state.
  const readAt = Date.parse(storedGet("farq.notesReadAt") || "") || 0;
  for (const note of feed) if (note.unread && (Date.parse(note.at || "") || 0) <= readAt) note.unread = false;
  state.notifications = feed.sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")));
  if (state.view === "notifications") render();
}

// The checkout in flight, kept until the server says the subscription is active: a 3-D Secure
// card leaves the page for the bank and comes back at /subscribe/callback, and a payment the
// webhook settles later can still be confirmed from here. Never cleared before "activated".
function pendingPayment() {
  try {
    return JSON.parse(localStorage.getItem("farq.pendingPayment") || "null");
  } catch (_error) {
    return null;
  }
}

function keepPendingPayment(entry) {
  try {
    localStorage.setItem("farq.pendingPayment", JSON.stringify(entry));
  } catch (_error) {}
}

function dropPendingPayment() {
  try {
    localStorage.removeItem("farq.pendingPayment");
  } catch (_error) {}
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Asks the server to verify with Moyasar. While the bank is still confirming ("pending") it
// asks again a few times; the webhook settles it in any case.
async function verifyPayment(paymentId, moyasarPaymentId, { tries = 1, quiet = false } = {}) {
  let result = null;
  for (let attempt = 0; attempt < tries; attempt += 1) {
    if (attempt) await wait(2500);
    result = await api("/v1/subscriptions/verify", { method: "POST", json: { payment_id: paymentId, moyasar_payment_id: moyasarPaymentId }, quiet });
    if (!result.pending) break;
  }
  const active = result.activated || (result.already_processed && result.subscription?.status === "active" && result.status === "paid");
  if (active) {
    dropPendingPayment();
    state.subStatus = { status: "active", subscription: result.subscription };
  }
  return { ...result, active };
}

// Shows what a verification means for the customer.
function showVerifyResult(result) {
  if (result.active) {
    state.subError = "";
    state.subView = "paid";
  } else if (result.pending) {
    state.subError = "";
    state.subView = "my-plan";
    toast("البنك ما زال يؤكد الدفع. بنفعّل اشتراكك تلقائياً أول ما يكتمل.");
  } else {
    state.subError = "الدفع لم يكتمل، تحققنا منه ولم يُفعَّل الاشتراك. تقدر تحاول مرة ثانية.";
    state.subView = "failed";
  }
}

async function resumePendingPayment() {
  const pending = pendingPayment();
  if (!pending?.payment_id || !pending.moyasar_payment_id || isSubscribed()) return;
  try {
    const result = await verifyPayment(pending.payment_id, pending.moyasar_payment_id, { quiet: true });
    if (result.active && state.view === "subscribe") {
      state.subView = "paid";
      render();
    }
  } catch (_error) {}
}

async function handleSubscribeCallback() {
  // mada/3DS cards leave the app entirely and Moyasar redirects the whole
  // page back to callback_url (?id=<moyasar_payment_id>&status=...) instead of
  // settling in on_completed - without this, that flow silently
  // never verifies and the user lands looking subscribed to nothing.
  //
  // This deliberately does NOT reuse loadSubscribe(): that sets
  // subActivePlan for a single-plan catalog, and render()'s auto-mount hook
  // would then start a *fresh* checkout concurrently with the verify call
  // below, racing over the same localStorage pending-payment entry.
  const params = new URLSearchParams(location.search);
  const pending = pendingPayment();
  const moyasarPaymentId = params.get("id") || params.get("payment_id") || pending?.moyasar_payment_id;
  history.replaceState({}, "", "/subscribe");

  await ensureAuth();
  state.view = "subscribe";
  state.subError = "";
  state.subActivePlan = "";
  state.subMountedPlan = "";
  state.subMountFailed = false;
  const verifying = Boolean(moyasarPaymentId && pending?.payment_id);
  state.subView = verifying ? "paying" : "my-plan";
  render();

  try {
    const [plans, me] = await Promise.all([api("/v1/subscriptions/plans", { skipAuth: true }), api("/v1/subscriptions/me")]);
    state.subPlans = plans.plans || [];
    state.paymentsAvailable = plans.payments_available;
    state.subStatus = me;
  } catch (_error) {
    state.subError = "ما قدرنا نجيب بيانات الاشتراك. جرّب مرة ثانية.";
  }

  if (verifying) {
    keepPendingPayment({ ...pending, moyasar_payment_id: moyasarPaymentId });
    try {
      showVerifyResult(await verifyPayment(pending.payment_id, moyasarPaymentId, { tries: 4 }));
    } catch (error) {
      // Not verified yet (Moyasar unreachable, or payments paused): the entry stays, so opening
      // the subscription screen again checks it; the webhook settles it meanwhile.
      state.subView = "my-plan";
      state.subError = "";
      toast(error.status === 503 ? "الاشتراك غير متاح حالياً" : "ما قدرنا نتأكد من الدفع الآن. بنكمل التحقق تلقائياً.");
    }
  }
  // Nothing auto-mounts a new checkout here: the plan list offers an explicit choice instead.
  render();
}

async function mountPayment(planCode) {
  const plan = state.subPlans.find((item) => item.code === planCode);
  if (!plan || state.subMountedPlan === planCode) return;
  state.subMountedPlan = planCode;
  if (paymentsOff() || plan.purchasable === false) {
    state.subMountFailed = true;
    render();
    return;
  }
  state.subBusy = true;
  state.subError = "";
  render();
  try {
    const checkout = await api("/v1/subscriptions/checkout", { method: "POST", json: { plan: plan.code } });
    // mada/3DS cards redirect the whole page away and back to callback_url
    // instead of settling here - stash our payment_id so the page
    // that reloads at /subscribe/callback can still verify it.
    keepPendingPayment({ payment_id: checkout.payment_id, plan: plan.code });
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
      description: `${planLabel(plan)} - فرق تسعير`,
      publishable_api_key: checkout.publishable_key,
      callback_url: checkout.callback_url,
      metadata: checkout.metadata,
      methods,
      apple_pay: { label: "فرق تسعير", country: "SA" },
      language: "ar",
      on_completed: async (payment) => {
        // Moyasar reports the payment as soon as it is created. A card that needs 3-D Secure is
        // still "initiated" here and Moyasar now sends the page to the bank, then back to
        // /subscribe/callback, which verifies. Remember its id for that page, and verify now
        // only when there is a result to verify.
        keepPendingPayment({ payment_id: checkout.payment_id, plan: plan.code, moyasar_payment_id: payment.id });
        if (payment.status === "initiated") return;
        state.subView = "paying";
        render();
        try {
          showVerifyResult(await verifyPayment(checkout.payment_id, payment.id, { tries: 3 }));
        } catch (_error) {
          state.subView = "review";
          state.subError = "ما قدرنا نتأكد من الدفع الآن. إذا خُصم المبلغ بنفعّل اشتراكك تلقائياً.";
        }
        render();
      },
    });
  } catch (error) {
    state.subMountFailed = true;
    state.subBusy = false;
    if (error.status === 503) {
      // The server stopped taking payments (keys missing, or a placeholder price on live keys).
      state.paymentsAvailable = false;
      state.subError = "";
    } else {
      state.subError = "ما قدرنا نجهّز الدفع الآن. حاول مرة ثانية.";
    }
    render();
  }
}

// The place field holds «الحي، المدينة». A city typed in it becomes the item's city; what is
// left is the district — so saving without touching it never makes «الرياض، الرياض».
function readPlace(need, text) {
  const place = String(text || "").trim();
  const found = state.cities.find((city) => place.includes(city.label) || place.includes(city.value));
  if (found) need.city = found.value;
  let district = place;
  for (const word of [found?.label, found?.value, cityLabel(need.city), need.city].filter(Boolean)) district = district.split(word).join(" ");
  need.district = district.replace(/[،,]/g, " ").replace(/\s+/g, " ").trim();
}

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form.id === "composer") {
    event.preventDefault();
    startPricing(new FormData(form).get("query"));
  } else if (form.id === "answer") {
    event.preventDefault();
    const value = new FormData(form).get("value");
    if (value) runSearch(`${state.searchText || state.originalText || state.query} ${value}`, state.city);
  } else if (form.id === "intent-answer") {
    event.preventDefault();
    const value = String(new FormData(form).get("value") || "").trim();
    if (value) startPricing(`${state.originalText || state.query} ${value}`);
  } else if (form.id === "auth-form") {
    event.preventDefault();
    submitAuth(form);
  } else if (form.id === "seller-reply") {
    event.preventDefault();
    submitSellerReply(form);
  } else if (form.id === "share-contact") {
    event.preventDefault();
    submitShareContact(form);
  } else if (form.id === "supplier-join") {
    event.preventDefault();
    submitSupplierJoin(form);
  } else if (form.id === "supplier-signin") {
    event.preventDefault();
    submitSupplierSignIn(form);
  } else if (form.id === "edit-need") {
    event.preventDefault();
    const data = new FormData(form);
    const index = Number(form.dataset.index);
    const adding = state.needs && index === state.needs.length;
    const need = adding ? blankNeed() : state.needs?.[index];
    if (need) {
      const name = String(data.get("name") || "").trim();
      const desc = String(data.get("desc") || "").trim();
      if (adding && !name && !desc) {
        state.editing = null;
        render();
        return;
      }
      need.name = name || need.name || desc.split(/\s+/).slice(0, 2).join(" ");
      need.desc = desc || need.desc || need.name;
      readPlace(need, data.get("district"));
      need.qty = Math.max(1, Math.round(Number(String(data.get("qty") || "1").replace(/[٠-٩]/g, (d) => "٠١٢٣٤٥٦٧٨٩".indexOf(d))) || 1));
      need.when = String(data.get("when") || "").trim();
      if (adding) {
        delete need.fresh;
        state.needs.push(need);
      }
    }
    state.editing = null;
    render();
  } else if (form.id === "user-reply") {
    event.preventDefault();
    const field = form.querySelector("textarea[name=body]");
    const body = String(field?.value || "").trim();
    if ((!body && !state.chatFiles.length) || !state.thread || state.sending) return;
    if (field) field.value = "";
    sendChatMessage(body);
  }
});

document.addEventListener("input", (event) => {
  if (event.target.id === "join-desc") {
    // Reading happens as he writes, so the categories and his own terms appear under the
    // box instead of after a submit that might drop them. The text lives in state because
    // the reply re-renders the screen, which would otherwise empty the box under him.
    state.supplierDesc = event.target.value;
    state.supplierCaret = event.target.selectionStart;
    describeBusiness(state.supplierDesc);
    return;
  }
  if (event.target.id === "note") {
    state.note = event.target.value;
    scheduleDraftSave();
  }
  if (event.target.name === "query") {
    state.query = event.target.value;
    const count = document.getElementById("composer-count");
    if (count) count.textContent = composerCount(state.query);
    if (state.composerHint && state.query.trim()) {
      state.composerHint = "";
      clearComposerHint();
    }
    scheduleDraftSave();
  }
  if (event.target.closest("#user-reply") && event.target.name === "body" && !state.activeSeller && state.thread) {
    const list = document.getElementById("mention-list");
    const typed = event.target.value.match(/@([^@]*)$/);
    const wanted = typed ? typed[1].trim() : "";
    const suggestions = typed ? byLatestReply(state.thread).filter((item) => !wanted || sellerName(state.thread, item.seller_id).includes(wanted)) : [];
    if (list) {
      list.hidden = !suggestions.length;
      list.innerHTML = suggestions
        .map((item, index) => {
          const when = item.repliedAt ? ago(item.repliedAt) : "ما ردّ بعد";
          return `<button type="button" data-action="mention" data-seller="${esc(item.seller_id)}" style="--seller:${sellerColor(state.thread, item.seller_id)}">${index === 0 && item.repliedAt ? `<span class="mention-flag">آخر رد</span>` : ""}<bdi>${esc(sellerName(state.thread, item.seller_id))}</bdi><span class="mention-when">${esc(when)}</span></button>`;
        })
        .join("");
    }
  }
});

// the hint under the composer goes as soon as there is something to price, without a re-render
function clearComposerHint() {
  document.querySelector("#composer [role=alert]")?.remove();
}
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
  if (event.key === "Escape") {
    if (closeTopSheet()) event.preventDefault();
    return;
  }
  if (event.key === "Tab") {
    // keep the focus inside an open sheet
    const sheet = app.querySelector(".fq-sheet");
    if (!sheet) return;
    const items = [...sheet.querySelectorAll("a[href], button:not([disabled]), input:not([type=hidden]), textarea, select")].filter((node) => node.offsetParent !== null);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (!sheet.contains(document.activeElement)) {
      event.preventDefault();
      first.focus();
    } else if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
    return;
  }
  if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
  if (event.target.name === "query") {
    // On a phone the keyboard's return key writes a new line; the button sends.
    if (coarsePointer()) return;
    event.preventDefault();
    startPricing(event.target.value);
  } else if (event.target.name === "body" && event.target.closest("#user-reply")) {
    event.preventDefault();
    event.target.form?.requestSubmit();
  }
});

document.addEventListener("click", (event) => {
  sound.unlock();
  const target = event.target.closest("[data-action]");
  if (!target) return;
  // A sheet's backdrop carries the close action; a tap inside the sheet must not close it.
  if (target.classList.contains("fq-scrim") && event.target !== target) return;
  const action = target.dataset.action;
  const go = (view) => {
    state.view = view;
    render();
  };
  if (action === "home") {
    event.preventDefault();
    state.view = "home";
    state.cityMode = "";
    render();
  } else if (action === "category") {
    const code = target.dataset.code;
    state.category = state.category === code ? "" : code;
    render();
  } else if (action === "clear-category") {
    state.category = "";
    render();
  } else if (action === "idea") startPricing(target.dataset.query);
  else if (action === "legal") {
    event.preventDefault();
    openLegal(target.dataset.doc);
  } else if (action === "legal-back") {
    // back to where the page was opened from; opened directly, it goes home (or to sign-in)
    if (history.state?.view === "legal" && state.legalFrom && state.legalFrom !== "legal") history.back();
    else {
      state.view = state.token ? "home" : "auth";
      render();
    }
  } else if (action === "toggle-price") {
    state.sellerPriceOpen = !state.sellerPriceOpen;
    render();
  } else if (action === "seller-reload") openSellerPage(state.sellerToken);
  else if (action === "edit-request") {
    state.intentError = false;
    state.intentProblem = "";
    state.view = "home";
    render();
    document.getElementById("composer-query")?.focus();
  } else if (action === "retry-intent") startPricing(state.originalText || state.query, { fresh: false });
  else if (action === "retry-search") runSearch(state.searchText || state.originalText || state.query, state.city);
  else if (action === "add-need") {
    state.editing = (state.needs || []).length;
    render();
  } else if (action === "delete-need") {
    state.needs?.splice(Number(target.dataset.index), 1);
    state.editing = null;
    render();
  } else if (action === "set-city") {
    // the header's city control: pick, and back home with the city showing
    state.city = target.dataset.city || "";
    state.cityMode = "";
    storedSet("farq.city", state.city);
    state.view = "home";
    render();
  } else if (action === "research-city") {
    state.city = target.dataset.city || "";
    state.cityMode = "";
    storedSet("farq.city", state.city);
    for (const need of state.needs || []) need.city = state.city;
    runSearch(state.searchText || state.originalText || state.query, state.city);
  } else if (action === "other-city") {
    state.cityMode = "research";
    state.view = "city-ask";
    render();
  }
  else if (action === "requests") loadRequests().catch(() => {});
  else if (action === "account") {
    state.view = "account";
    render();
    loadSubStatus().catch(() => {});
  } else if (action === "notifications") loadNotifications();
  else if (action === "notify-settings") go("notify-settings");
  else if (action === "notify-toggle") {
    const key = target.dataset.key;
    state.notifyPrefs = { ...(state.notifyPrefs || {}), [key]: state.notifyPrefs?.[key] === false };
    try {
      localStorage.setItem("farq.notifyPrefs", JSON.stringify(state.notifyPrefs));
    } catch (_error) {}
    if (state.notifyPrefs[key] && state.pushState !== "on") enableNotifications(true).catch(() => {}).finally(render);
    else render();
  } else if (action === "sound-toggle") {
    sound.unlock();
    sound.on = !sound.on;
    if (sound.on) cue("offer", 12);
    render();
  } else if (action === "try-sound") {
    // one short cue, the same one an arriving offer plays
    sound.unlock();
    cue("offer", 12);
  } else if (action === "read-all") {
    const newest = (state.notifications || []).map((item) => item.at || "").sort().pop();
    storedSet("farq.notesReadAt", newest && newest > new Date().toISOString() ? newest : new Date().toISOString());
    state.notifications = (state.notifications || []).map((item) => ({ ...item, unread: false }));
    render();
  } else if (action === "change-city") {
    state.cityMode = "pick";
    go("city-ask");
  }
  else if (action === "run-search") searchFromNeeds();
  else if (action === "need-city") {
    const before = state.city;
    state.city = target.dataset.city || "";
    state.cityPick = false;
    storedSet("farq.city", state.city);
    for (const need of state.needs || []) if (!need.city || need.city === before) need.city = state.city;
    render();
  } else if (action === "need-city-change") {
    state.cityPick = true;
    render();
  }
  else if (action === "back-understand") go(state.needs ? "understand" : "home");
  else if (action === "edit-need") {
    state.editing = Number(target.dataset.index);
    render();
  } else if (action === "toggle-need") {
    const need = state.needs?.[Number(target.dataset.index)];
    if (need) need.on = !need.on;
    render();
  } else if (action === "qty") {
    const field = document.getElementById("need-qty");
    if (field) field.value = String(Math.max(1, Number(field.value || 1) + Number(target.dataset.step)));
  } else if (action === "close-sheet") {
    state.editing = null;
    if (state.view === "detail") {
      // the detail sheet has its own address; closing it is a step back, not a new step
      if (history.state?.view === "detail") {
        history.back();
        return;
      }
      state.view = "flow";
      state.active = null;
    }
    render();
  } else if (action === "close-cap") {
    state.capSheet = false;
    render();
  } else if (action === "toggle-extra") {
    state.reviewExtra = state.reviewExtra !== true;
    render();
  } else if (action === "filter-need") {
    const name = target.dataset.name || "";
    state.needFilter = !name || state.needFilter === name ? "" : name;
    render();
  } else if (action === "open-sent") loadRequests().catch(() => {});
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
    state.city = target.dataset.city || "";
    state.cityMode = "";
    storedSet("farq.city", state.city);
    startPricing(state.originalText || state.query, { fresh: false });
  }
  else if (action === "pick-city") {
    state.city = target.dataset.city || "";
    render();
  } else if (action === "review") {
    state.view = "review";
    render();
  } else if (action === "back-results") go("flow");
  else if (action === "retry") startPricing(state.originalText || state.query, { fresh: false });
  else if (action === "answer") runSearch(`${state.searchText || state.query} ${target.dataset.value}`, target.dataset.city || state.city);
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
  } else if (action === "legacy-auth") {
    state.legacyAuth = true;
    state.authMode = "login";
    render();
  } else if (action === "farq-sign-in") {
    // A Farq session may have appeared since this screen was drawn (another tab signed in).
    const farqToken = farqSessionToken();
    if (farqToken) {
      event.preventDefault();
      state.busy = true;
      render();
      signInWithFarq(farqToken).then((ok) => {
        state.busy = false;
        if (ok) resumeAfterSignIn();
        else {
          state.authError = "ما قدرنا نقرأ جلسة فرق، جرّب تسجيل الدخول مرة ثانية.";
          render();
        }
      });
    }
  } else if (action === "auth-mode") {
    state.authMode = state.authMode === "register" ? "login" : "register";
    state.authError = "";
    render();
  } else if (action === "sign-out") {
    api("/v1/auth/logout", { method: "POST" }).catch(() => {});
    signOutLocally();
    // One account, one sign-out: the Farq session on this device ends too, or the next
    // visit would sign him straight back in from the shared cookie.
    clearFarqSession();
    clearDraft();
    state.returnView = "home";
    state.authMode = "login";
    try {
      history.replaceState({}, "", "/");
    } catch (_error) {}
    requireSignIn();
  } else if (action === "open-compare") {
    state.view = "compare";
    state.awardPick = null;
    render();
  } else if (action === "back-thread") go("thread");
  else if (action === "pick-winner") {
    state.awardPick = { sellerId: target.dataset.seller, price: Number(target.dataset.price || 0) };
    render();
  } else if (action === "cancel-award") {
    state.awardPick = null;
    render();
  } else if (action === "confirm-award") {
    const pick = state.awardPick;
    if (!pick || state.busy) return;
    state.busy = true;
    render();
    api(`/v1/requests/${state.thread.id}/award`, { method: "POST", json: { seller_id: pick.sellerId, notify: true } })
      .then((record) => {
        threadCache.set(record.id, record);
        state.thread = record;
        state.awardPick = null;
        state.busy = false;
        state.view = "awarded";
        cue("award", [14, 60, 22]);
        render();
      })
      .catch(() => {
        state.busy = false;
        state.awardPick = null;
        cue("fail", 30);
        toast("ما قدرنا نسجّل الاختيار، جرّب مرة ثانية");
      });
  } else if (action === "winner-chat") {
    state.activeSeller = state.thread?.awarded_seller_id || "";
    state.stickChat = true;
    go("thread");
  } else if (action === "thread-menu") {
    if ((state.thread?.offers || []).some((item) => item.total_price != null)) {
      state.view = "compare";
      render();
    } else toast("ما فيه عروض بأسعار بعد");
  } else if (action === "req-filter") {
    state.requestFilter = target.dataset.filter;
    render();
  } else if (action === "open-picker") {
    state.pickerOpen = true;
    render();
  } else if (action === "close-picker") {
    state.pickerOpen = false;
    render();
  } else if (action === "open-attach") {
    state.attachOpen = true;
    render();
  } else if (action === "close-attach") {
    state.attachOpen = false;
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
    state.pushAsk = false;
    enableNotifications(true)
      .catch(() => {
        state.pushState = "off";
      })
      .finally(() => render());
  } else if (action === "dismiss-notify") {
    state.pushAsk = false;
    state.pushDismissed = true;
    try {
      localStorage.setItem("farq.pushDismissed", "1");
    } catch (_error) {}
    render();
  } else if (action === "thread") {
    if (!target.dataset.id) return;
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
    state.view = "thread";
    render();
  } else if (action === "all-sellers") {
    state.activeSeller = "";
    state.stickChat = true;
    state.view = "thread";
    render();
  } else if (action === "reply") {
    const message = (state.thread?.messages || []).find((item) => item.id === target.dataset.message);
    if (!message) return;
    state.replyTo = { id: message.id, sellerId: message.seller_id, name: sellerName(state.thread, message.seller_id), body: message.body };
    render();
    document.querySelector("#user-reply textarea")?.focus();
  } else if (action === "chat-bottom") {
    const wall = document.getElementById("chat-wall");
    if (wall) wall.scrollTo({ top: wall.scrollHeight, behavior: "smooth" });
  } else if (action === "cancel-reply") {
    state.replyTo = null;
    render();
  } else if (action === "mention") {
    const input = document.querySelector("#user-reply textarea");
    const seller = target.dataset.seller;
    if (!input || !seller) return;
    state.picked = new Set([seller]);
    const kept = input.value.replace(/@[^@]*$/, "");
    render();
    const next = document.querySelector("#user-reply textarea");
    if (next) {
      next.value = kept;
      next.focus();
    }
  } else if (action === "subscribe" || action === "my-plan") {
    state.subView = "my-plan";
    loadSubscribe().catch(() => {});
  } else if (action === "show-plans") {
    state.subView = "plans";
    state.view = "subscribe";
    render();
    loadSubscribe({ keepView: true }).catch(() => {});
  } else if (action === "upgrade") {
    state.subView = "upgrade";
    state.view = "subscribe";
    render();
  } else if (action === "back-gate") {
    state.resumeAfterPay = false;
    go(state.selected.size ? "review" : "home");
  } else if (action === "resume-request") {
    state.resumeAfterPay = false;
    state.subView = "resume";
    render();
    sendRequest();
  } else if (action === "choose-plan") {
    if (!state.token) {
      if (farqHandlesSignIn("subscribe")) return;
      state.returnRoute = "/plans";
      state.view = "auth";
      render();
      return;
    }
    state.subActivePlan = target.dataset.plan;
    state.subMountedPlan = "";
    state.subMountFailed = false;
    state.subError = "";
    state.subView = "review";
    render();
  } else if (action === "pay") {
    state.subMountedPlan = "";
    state.subMountFailed = false;
    state.subError = "";
    render();
    mountPayment(state.subActivePlan);
  } else if (action === "retry-payment") {
    state.subMountedPlan = "";
    state.subMountFailed = false;
    state.subError = "";
    state.subView = "review";
    render();
  } else if (action === "profile") toast("صفحة بياناتي قيد الإعداد");
  else if (action === "support") toast("الدعم: support@farq.sa");
  else if (action === "privacy" || action === "terms") openLegal(action);
  else if (action === "resend-verify") resendVerification().catch(() => {});
  else if (action === "share-contact") {
    state.shareOpen = true;
    state.shareError = "";
    render();
  } else if (action === "share-cancel") {
    state.shareOpen = false;
    state.shareError = "";
    render();
  } else if (action === "share-place") {
    state.sharePlace = target.checked;
  } else if (action === "revoke-contact") {
    revokeContact().catch(() => {});
  } else if (action === "supplier-home") {
    state.supplierTab = "requests";
    openSupplier();
  }
  else if (action === "supplier-join") {
    state.supplierMode = "join";
    state.supplierError = "";
    state.view = "supplier-auth";
    render();
    loadSupplierCatalog().catch(() => {});
  } else if (action === "supplier-signin") {
    state.supplierMode = "signin";
    state.supplierError = "";
    state.view = "supplier-auth";
    render();
  } else if (action === "supplier-signout") {
    api("/v1/supplier/logout", { method: "POST", asSupplier: true, quiet: true }).catch(() => {});
    supplierSignOutLocally();
    state.supplierMode = "signin";
    state.view = "supplier-auth";
    render();
  } else if (action === "supplier-activity") {
    state.supplierActivity = target.dataset.key;
    render();
  } else if (action === "supplier-category") {
    const key = target.dataset.key;
    state.supplierPicked = state.supplierPicked.includes(key)
      ? state.supplierPicked.filter((item) => item !== key)
      : [...state.supplierPicked, key];
    render();
  } else if (action === "supplier-filter") {
    state.supplierFilter = target.dataset.key;
    render();
  } else if (action === "supplier-refresh") {
    loadSupplierRequests().catch(() => {});
    loadSupplierInbox().catch(() => {});
  } else if (action === "supplier-inbox") {
    state.supplierTab = "inbox";
    render();
    loadSupplierInbox().catch(() => {});
  } else if (action === "supplier-read-all") readSupplierInbox(null);
  else if (action === "supplier-open-note") {
    const url = target.dataset.url || "";
    readSupplierInbox(target.dataset.id);
    const token = url.split("/s/")[1];
    if (token) {
      state.supplierTab = "requests";
      state.sellerFrom = "supplier-requests";
      openSellerPage(decodeURIComponent(token));
    }
  }
  else if (action === "supplier-open") {
    const token = target.dataset.token;
    // The request screen is the same one an invite link opens; coming from the list it
    // remembers where to go back to.
    if (token) {
      state.sellerFrom = "supplier-requests";
      openSellerPage(token);
    }
  } else if (action === "lang") toast("الواجهة الإنجليزية قيد الإعداد");
  else if (action === "forgot") toast("تواصل مع الدعم لإعادة تعيين كلمة المرور");
  else if (action === "emoji") document.querySelector("#user-reply textarea")?.focus();
});

// Lets a review session hold a screen still. Some of them — sending, searching, paying —
// pass in well under a tenth of a second against a local server, so they cannot otherwise
// be photographed and put beside their frames. Nothing a customer can reach touches this.
window.addEventListener("farq:sub", (event) => {
  // detail is either the state's name, or { view, used, subscribed } so a review can also
  // stand at a particular point in the month's allowance.
  const detail = event.detail;
  const options = typeof detail === "string" ? { view: detail } : detail || {};
  state.view = "subscribe";
  state.subView = String(options.view || "my-plan");
  if (options.used != null) state.usage = { items_used: Number(options.used) };
  if (options.subscribed) {
    const plan = state.subPlans[0];
    state.subStatus = { status: "active", subscription: { plan: plan?.code || "", expires_at: null } };
  } else if (options.subscribed === false) {
    state.subStatus = { status: "none" };
  }
  if (!state.subActivePlan && state.subPlans.length) state.subActivePlan = state.subPlans[0].code;
  render();
});

window.addEventListener("farq:view", (event) => {
  const view = String(event.detail || "home");
  if (view === "sending") state.sendingTo = state.sendingTo || state.selected.size || 6;
  if (view === "searching") {
    state.view = "flow";
    state.searching = true;
    state.partial = true;
    state.results = [];
    state.scanned = state.scanned || 147;
    render();
    return;
  }
  state.view = view;
  render();
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && state.view === "thread" && state.thread?.id) {
    loadThread(state.thread.id, true).catch(() => {});
  }
});

// Boot: the journey kept for this tab comes back first, then the address decides the screen.
restoreDraft();
state.city = state.city || storedGet("farq.city") || "";
const openRequest = new URLSearchParams(location.search).get("r");
const bootPath = location.pathname;
// Screens that stand on their own without a customer session: the policies, the public
// prices, the supplier app with its own session, an invite link, and the address
// confirmation a link in an email lands on.
const publicPage =
  /^\/(terms|privacy|refunds|plans|verify)\/?$/.test(bootPath) ||
  /^\/supplier(\/[^/]*)?\/?$/.test(bootPath) ||
  /^\/s\/[^/]+\/?$/.test(bootPath);
// Inside Farq's frame the address is always "/" (the frame is reloaded from its src), so the
// screen a reload lands on comes from the journey's draft: a review with ticks, results, or
// the items he was checking.
function draftView() {
  if (state.selected.size) return "review";
  if (state.results.length || state.searchState) return "flow";
  if (state.needs || state.intentError || state.intentProblem) return "understand";
  return "home";
}

function bootSignedOut() {
  // Embedded, the home screen and its search are what he came for; the account arrives from
  // Farq. Standalone, the sign-in screen is still the front door - and that door is Farq's.
  state.returnRoute = openRequest ? `/r/${encodeURIComponent(openRequest)}` : bootPath;
  if (isFarqEmbed()) {
    state.view = draftView();
    state.replaceUrl = true;
    askFarqForSession();
  } else {
    state.view = "auth";
  }
  render();
}

function bootSignedIn() {
  api("/v1/auth/me", { quiet: true })
    .then((account) => {
      state.account = account;
      if (state.view === "account" || state.view === "requests") render();
    })
    .catch(() => {});
  loadSubStatus().catch(() => {});
  loadVerification().catch(() => {});
  try {
    state.notifyPrefs = JSON.parse(localStorage.getItem("farq.notifyPrefs") || "{}");
  } catch (_error) {
    state.notifyPrefs = {};
  }
  if (subscribeCallback) handleSubscribeCallback().catch(() => render());
  else if (openRequest) applyRoute(`/r/${encodeURIComponent(openRequest)}`, { pop: true });
  else if (isFarqEmbed() && bootPath === "/") {
    state.view = draftView();
    state.replaceUrl = true;
    render();
  } else applyRoute(bootPath, { pop: true });
}

if (publicPage) {
  applyRoute(bootPath, { pop: true });
} else if (state.token) {
  bootSignedIn();
} else {
  const farqToken = farqSessionToken();
  if (farqToken) {
    // Signed in to Farq on this device: that is the account. No screen of our own first.
    signInWithFarq(farqToken).then((ok) => (ok ? bootSignedIn() : bootSignedOut()));
  } else bootSignedOut();
}

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
