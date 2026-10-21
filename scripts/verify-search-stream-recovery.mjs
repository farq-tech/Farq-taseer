import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const simulationUrl = process.env.SIMULATION_URL;
if (!simulationUrl) {
  throw new Error("Set SIMULATION_URL to the running Postman simulation URL.");
}

const source = await readFile(new URL("../web/app.js", import.meta.url), "utf8");
const signatures = [
  "async function api(path, options = {}) {",
  "async function apiSend(path, options = {}) {",
  'async function request(path, { method = "GET", json, form, skipAuth = false, quiet = false, signal, asSupplier = false, headers: extra } = {}) {',
  "async function readNdjson(response, onEvent) {",
  "function applyDone(event) {",
  "function failSearch(status) {",
  "async function recoverSearch(searchId, asked, started, error, timedOut) {",
  'async function runSearch(text, city = "") {',
];

const functions = signatures.map((signature) => {
  const start = source.indexOf(signature);
  assert.notEqual(start, -1, `Missing ${signature}`);
  const end = source.indexOf("\n}", start) + 2;
  assert.ok(end > start, `Could not extract ${signature}`);
  return source.slice(start, end);
});

const nativeFetch = globalThis.fetch.bind(globalThis);
const calls = [];
globalThis.fetch = (path, options) => {
  calls.push(path);
  return nativeFetch(`${simulationUrl}/search-api${path}`, options);
};

const state = {
  token: "",
  supplierToken: "",
  city: "الرياض",
  cities: [],
  query: "",
  searchId: "",
  searchText: "",
  originalText: "",
  view: "home",
  results: [],
  selected: new Set(),
  needs: [],
  searchState: "",
  partial: false,
  searching: false,
  notice: "",
  resultNeeds: new Map(),
  streamNeeds: [],
  nextPage: null,
  loadingMore: false,
};
const SEARCH_TIMEOUT_MS = 30_000;
const SLOW_SEARCH_MS = 12_000;
const readCache = new Map();
const setBusy = () => {};
const render = () => {};
const cityInText = () => false;
const cityLabel = (value) => value;
const rememberGroups = () => {};
const finalNotice = () => "";
const cueOnce = () => {};
const signOutLocally = () => {};
const supplierSignOutLocally = () => {};
const requireSignIn = () => {};

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const run = new AsyncFunction(
  "state",
  "SEARCH_TIMEOUT_MS",
  "SLOW_SEARCH_MS",
  "readCache",
  "setBusy",
  "render",
  "cityInText",
  "cityLabel",
  "rememberGroups",
  "finalNotice",
  "cueOnce",
  "signOutLocally",
  "supplierSignOutLocally",
  "requireSignIn",
  `${functions.join("\n\n")}\nreturn runSearch("سباك", "الرياض");`,
);

await run(
  state,
  SEARCH_TIMEOUT_MS,
  SLOW_SEARCH_MS,
  readCache,
  setBusy,
  render,
  cityInText,
  cityLabel,
  rememberGroups,
  finalNotice,
  cueOnce,
  signOutLocally,
  supplierSignOutLocally,
  requireSignIn,
);

assert.deepEqual(calls, ["/v1/search/stream", "/v1/search"]);
assert.equal(state.searching, false);
assert.equal(state.searchState, "DONE");
assert.equal(state.results[0]?.ad?.id, "recovered-ad");
process.stdout.write("PASS: interrupted stream recovered through one plain-search request.\n");
