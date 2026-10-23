// Layout regression for Taseer's conversation screens on phones: no horizontal overflow, no
// input that makes iOS zoom, and a keyboard that never covers the composer or moves the header.
//
//   cd tests/web && npm install && node chat-layout.mjs
//   SHOTS=/path/to/dir node chat-layout.mjs      # also writes a screenshot per case
//   CHROMIUM=/opt/pw-browsers/chromium node chat-layout.mjs
//
// The page is web/ served as it ships, with every /v1 call answered by a fixture, so the run
// needs no backend and no network. Two keyboards are modelled:
//  - iOS: the layout viewport keeps its height; only window.visualViewport shrinks, and Safari
//    pans the visible band down to the focused field when the page does not make room itself.
//  - Android / Capacitor native resize: the viewport itself gets shorter.
// This is an emulation: it proves the layout reacts to both signals correctly, not how a
// particular iOS build draws its keyboard.

import { mkdir } from "node:fs/promises";
import { chromium } from "playwright";
import { serve } from "./serve.mjs";

const SHOTS = process.env.SHOTS || "";

// ---- the iOS keyboard model -----------------------------------------------------------------
// Installed before app.js runs so its visualViewport listeners attach to the model.
const IOS_KEYBOARD = () => {
  const fake = new EventTarget();
  let keyboard = 0;
  let pan = 0;
  const def = (name, get) => Object.defineProperty(fake, name, { get, enumerable: true });
  def("width", () => window.innerWidth);
  def("height", () => window.innerHeight - keyboard);
  def("scale", () => 1);
  def("offsetLeft", () => 0);
  def("offsetTop", () => pan);
  def("pageLeft", () => window.scrollX);
  def("pageTop", () => window.scrollY + pan);
  Object.defineProperty(window, "visualViewport", { get: () => fake, configurable: true });
  const fire = () => fake.dispatchEvent(new Event("resize"));
  // Safari's rule: after the keyboard lands, the focused field must be in the visible band.
  // If the document can scroll it scrolls; otherwise the visible band itself pans down.
  const reveal = () => {
    const field = document.activeElement;
    if (!field || !keyboard) return;
    const visible = window.innerHeight - keyboard;
    const bottom = field.getBoundingClientRect().bottom + 8;
    if (bottom <= visible + pan) return;
    const before = window.scrollY;
    window.scrollBy(0, bottom - visible - pan);
    const moved = window.scrollY - before;
    const still = bottom - moved - visible - pan;
    if (still > 0) pan = Math.min(keyboard, pan + still);
    fake.dispatchEvent(new Event("scroll"));
  };
  window.__keyboard = async (px) => {
    keyboard = px;
    pan = 0;
    fire();
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    reveal();
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  };
};

// ---- measurements ---------------------------------------------------------------------------
const MEASURE = () => {
  const vw = document.documentElement.clientWidth;
  const vv = window.visualViewport;
  const band = { top: vv.offsetTop, bottom: vv.offsetTop + vv.height };
  // the nearest ancestor that clips horizontally and itself fits the screen
  const clips = (node) => {
    for (let p = node.parentElement; p && p !== document.body; p = p.parentElement) {
      if (!/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflowX)) continue;
      const b = p.getBoundingClientRect();
      if (b.right <= vw + 0.5 && b.left >= -0.5) return p;
    }
    return null;
  };
  const overflow = [];
  for (const node of document.body.querySelectorAll("*")) {
    const s = getComputedStyle(node);
    if (s.display === "none" || s.visibility === "hidden" || s.position === "fixed" && s.opacity === "0") continue;
    const r = node.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    if (r.right <= vw + 0.5 && r.left >= -0.5) continue;
    const box = clips(node);
    if (box) {
      const b = box.getBoundingClientRect();
      if (b.right <= vw + 0.5 && b.left >= -0.5) continue;
    }
    overflow.push(`${node.tagName.toLowerCase()}.${String(node.className).split(" ").slice(0, 2).join(".")} [${Math.round(r.left)}..${Math.round(r.right)}]`);
  }
  const rect = (sel) => {
    const n = document.querySelector(sel);
    if (!n || !n.offsetParent && getComputedStyle(n).position !== "fixed") return null;
    const r = n.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, left: r.left, right: r.right, height: r.height };
  };
  const head = rect("html.fq-embed .fq-embar") || rect(".fq-head") || rect(".fq-embar");
  const composer = rect("#user-reply") || rect("#seller-reply .fq-composer");
  const wall = document.getElementById("chat-wall");
  const msgs = wall ? wall.querySelectorAll(".fq-msg") : [];
  const last = msgs.length ? msgs[msgs.length - 1].getBoundingClientRect() : null;
  const wallRect = wall ? wall.getBoundingClientRect() : null;
  const small = [...document.querySelectorAll("input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=file]), textarea, select")]
    .filter((n) => n.offsetParent)
    .map((n) => ({ id: n.id || n.name, size: parseFloat(getComputedStyle(n).fontSize) }))
    .filter((n) => n.size < 16);
  const field = document.querySelector("#user-reply textarea, #seller-body");
  return {
    vw,
    innerWidth: window.innerWidth,
    scrollWidth: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth),
    scrollY: window.scrollY,
    band,
    overflow: overflow.slice(0, 8),
    overflowCount: overflow.length,
    head,
    composer,
    wall: wallRect && { top: wallRect.top, bottom: wallRect.bottom },
    last: last && { top: last.top, bottom: last.bottom },
    small,
    viewport: document.querySelector("meta[name=viewport]")?.content || "",
    field: field && { h: field.getBoundingClientRect().height, sh: field.scrollHeight, ch: field.clientHeight, oy: getComputedStyle(field).overflowY },
  };
};


// For the screenshots only: the picture the phone shows - the band of the page the customer
// can see (after any pan Safari applied), with the keyboard drawn under it.
async function shoot(page, file, keyboard) {
  if (!SHOTS) return;
  if (!keyboard) return void (await page.screenshot({ path: `${SHOTS}/${file}` }));
  const band = await page.evaluate(() => ({ top: window.visualViewport.offsetTop, height: window.visualViewport.height, width: window.innerWidth }));
  const seen = await page.screenshot({ clip: { x: 0, y: band.top, width: band.width, height: band.height } });
  const phone = await page.context().newPage();
  await phone.setViewportSize({ width: band.width, height: Math.round(band.height + keyboard) });
  await phone.setContent(`<meta name="viewport" content="width=device-width,initial-scale=1"><body style="margin:0"><img style="display:block;width:${band.width}px;height:${band.height}px" src="data:image/png;base64,${seen.toString("base64")}">
    <div style="height:${keyboard}px;background:#d1d3d9;border-top:1px solid #9a9ca3;color:#555;font:600 14px system-ui;display:grid;place-items:center">keyboard · ${keyboard}px</div></body>`);
  await phone.screenshot({ path: `${SHOTS}/${file}` });
  await phone.close();
}

// ---- the matrix -----------------------------------------------------------------------------
const DEVICES = [
  ["320", 320, 568, 260],
  ["360-android", 360, 800, 300, "android"],
  ["375", 375, 667, 260],
  ["390", 390, 844, 336],
  ["393-android", 393, 851, 310, "android"],
  ["402", 402, 874, 336],
  ["414", 414, 896, 346],
  ["430", 430, 932, 346],
  ["iphone-landscape", 844, 390, 170],
  ["desktop", 1280, 800, 0],
];
const SCREENS = [
  ["thread", "/r/t1"],
  ["seller", "/s/tok"],
];
const REQUIRED_VIEWPORT = ["width=device-width", "initial-scale=1", "maximum-scale=1", "user-scalable=no", "viewport-fit=cover"];

const failures = [];
const report = [];
function check(label, ok, detail) {
  if (!ok) failures.push(`${label}: ${detail}`);
  return ok;
}

async function run() {
  const server = await serve();
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || undefined });
  if (SHOTS) await mkdir(SHOTS, { recursive: true });

  for (const [name, width, height, kb, kind = "ios"] of DEVICES) {
    for (const [screen, path] of SCREENS) {
      const mobile = width < 900 || name === "iphone-landscape";
      const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 2, isMobile: mobile, hasTouch: mobile, locale: "ar-SA" });
      if (kind === "ios") await context.addInitScript(IOS_KEYBOARD);
      await context.addInitScript(() => localStorage.setItem("farq.token", "test-token"));
      const page = await context.newPage();
      const errors = [];
      page.on("pageerror", (e) => errors.push(String(e)));
      await page.goto(base + path);
      await page.waitForSelector(screen === "thread" ? "#user-reply textarea" : "#seller-body");
      await page.waitForFunction(() => document.querySelector("#chat-wall .fq-msg:last-child"));
      await page.waitForTimeout(700); // entrance animation
      const tag = `${name}/${screen}`;
      const rows = { case: tag };

      // A. at rest
      const rest = await page.evaluate(MEASURE);
      await shoot(page, `${name}-${screen}-A-rest.png`, 0);
      check(`${tag} A`, rest.overflowCount === 0 && rest.scrollWidth <= rest.vw, `horizontal overflow ${rest.scrollWidth}>${rest.vw} ${rest.overflow.join(", ")}`);
      check(`${tag} viewport`, REQUIRED_VIEWPORT.every((part) => rest.viewport.replace(/\s/g, "").includes(part)), `meta viewport "${rest.viewport}"`);
      check(`${tag} font`, !rest.small.length, `fields under 16px zoom iOS on focus: ${JSON.stringify(rest.small)}`);
      check(`${tag} G`, rest.last && rest.composer && rest.last.bottom <= rest.composer.top + 1 && rest.last.bottom > rest.wall.top, `last message not visible above composer ${JSON.stringify({ last: rest.last, composer: rest.composer })}`);
      rows.A = rest.overflowCount === 0 && rest.scrollWidth <= rest.vw;

      if (kb) {
        // B + H. open and close the keyboard three times; the header must not move.
        const field = screen === "thread" ? "#user-reply textarea" : "#seller-body";
        let open;
        for (let i = 0; i < 3; i++) {
          await page.focus(field);
          if (kind === "ios") await page.evaluate((px) => window.__keyboard(px), kb);
          else await page.setViewportSize({ width, height: height - kb });
          await page.waitForTimeout(150);
          open = await page.evaluate(MEASURE);
          const visTop = open.band.top;
          const visBottom = open.band.bottom;
          check(`${tag} B${i}`, open.head && open.head.top >= visTop - 1 && Math.abs(open.head.top - visTop - (rest.head.top - rest.band.top)) <= 1, `header moved: rest ${rest.head?.top} open ${open.head?.top} band ${visTop}`);
          check(`${tag} B${i}`, open.composer && open.composer.bottom <= visBottom + 1 && open.composer.top >= visTop, `composer outside visible band: ${JSON.stringify(open.composer)} band ${visTop}..${visBottom}`);
          // the conversation is pinned: the end of the last message sits at the bottom of a
          // message area that still has room to read in
          check(`${tag} B${i}`, open.last && open.last.bottom <= open.wall.bottom + 1 && open.last.bottom >= open.wall.bottom - 40 && open.last.bottom > visTop && open.wall.bottom - open.wall.top >= 56, `last message hidden: ${JSON.stringify({ last: open.last, wall: open.wall, composer: open.composer })}`);
          check(`${tag} B${i}`, open.overflowCount === 0 && open.scrollWidth <= open.vw, `overflow with keyboard ${open.overflow.join(", ")}`);
          if (i === 0) await shoot(page, `${name}-${screen}-B-keyboard.png`, kind === "ios" ? kb : 0);
          // close
          await page.evaluate(() => document.activeElement?.blur());
          if (kind === "ios") await page.evaluate(() => window.__keyboard(0));
          else await page.setViewportSize({ width, height });
          await page.waitForTimeout(150);
          const closed = await page.evaluate(MEASURE);
          check(`${tag} H${i}`, Math.abs(closed.head.top - rest.head.top) <= 1 && Math.abs(closed.composer.bottom - rest.composer.bottom) <= 1, `layout did not return after closing keyboard: head ${closed.head.top} vs ${rest.head.top}, composer ${closed.composer.bottom} vs ${rest.composer.bottom}`);
        }
        rows.B = !failures.some((f) => f.startsWith(`${tag} B`));
        rows.H = !failures.some((f) => f.startsWith(`${tag} H`));

        // F. multiline: grows to a cap, then scrolls inside; the composer stays in view.
        await page.focus(field);
        if (kind === "ios") await page.evaluate((px) => window.__keyboard(px), kb);
        else await page.setViewportSize({ width, height: height - kb });
        await page.fill(field, Array.from({ length: 12 }, (_, i) => `سطر رقم ${i + 1} من رسالة طويلة`).join("\n"));
        await page.dispatchEvent(field, "input");
        await page.waitForTimeout(120);
        const multi = await page.evaluate(MEASURE);
        check(`${tag} F`, multi.field.h <= 130 && multi.field.sh > multi.field.ch && /(auto|scroll)/.test(multi.field.oy), `textarea did not cap and scroll: ${JSON.stringify(multi.field)}`);
        check(`${tag} F`, multi.composer.bottom <= multi.band.bottom + 1 && multi.head.top >= multi.band.top - 1, `multiline pushed composer/header out: ${JSON.stringify({ c: multi.composer, h: multi.head, band: multi.band })}`);
        check(`${tag} F`, multi.last.bottom <= multi.wall.bottom + 1 && multi.last.bottom >= multi.wall.bottom - 40 && multi.wall.bottom - multi.wall.top >= 40, `last message hidden behind a grown composer: ${JSON.stringify({ last: multi.last, wall: multi.wall })}`);
        check(`${tag} F`, multi.overflowCount === 0, `overflow with multiline ${multi.overflow.join(", ")}`);
        await shoot(page, `${name}-${screen}-F-multiline.png`, kind === "ios" ? kb : 0);
        rows.F = !failures.some((f) => f.startsWith(`${tag} F`));
      }
      check(`${tag} errors`, !errors.length, errors.join(" | "));
      rows.ok = !failures.some((f) => f.startsWith(tag));
      report.push(rows);
      await context.close();
    }
  }
  await browser.close();
  server.close();
  console.table(report);
  if (failures.length) {
    console.error(`\n${failures.length} failure(s):\n` + failures.map((f) => `  ✗ ${f}`).join("\n"));
    process.exit(1);
  }
  console.log("\nall layout checks passed");
}

run().catch((error) => {
  console.error(error);
  process.exit(1);
});
