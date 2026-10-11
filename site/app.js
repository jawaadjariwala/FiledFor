// FiledFor job board. Reads jobs-recent.json (last 7 days) first, then
// jobs-older.json only when needed, plus companies.json and health.json; all
// filtering happens in the browser. No build step, no dependencies.
//
// On wide screens a job opens in the preview beside the list; on phones, and
// with "See the whole job", it opens its own page (/job/...). State that
// matters for sharing lives in the URL (?f=ai&age=1&loc=m:Bay Area). Saved
// jobs, applied jobs and the last visit live in localStorage only.

"use strict";

const PAGE = 40;
const REPO = "https://github.com/jawaadjariwala/FiledFor";
const FIELD = { swe: "Software", ai: "AI/ML", data: "Data", hardware: "Hardware", it: "IT & Cloud", security: "Security", product: "Product", design: "Design" };
const LEVELS = { intern: "Internship", entry: "New grad", early: "Not in title", mid: "Mid level", senior: "Senior" };
const LEVEL_HINT = { intern: "Interns and co-ops", entry: "Says new grad, junior, entry level or similar", early: "No level in the title, asks 2 years or less", mid: "II or 3 to 4 years asked", senior: "Senior, staff, lead or 5+ years asked" };
const DEFAULTS = { f: Object.keys(FIELD), l: ["entry", "early"], age: "7", loc: [], filed: true, hideApplied: false, q: "", sort: "new", pay: 0, setup: [], strong: false };
// A search gets its own filters, wide by default, so browsing filters never hide matches
const SEARCH_DEFAULTS = { f: Object.keys(FIELD), l: ["intern", "entry", "early", "mid", "senior"], age: "30", loc: [], filed: false, hideApplied: false, q: "", sort: "match", pay: 0, setup: [], strong: false };
const defaultsFor = (s) => (s.q ? SEARCH_DEFAULTS : DEFAULTS);
const EXPLAIN = {
  filings: ["Certified filings", "Labor Condition Applications this company filed with the Department of Labor for this kind of role, certified between October 2024 and June 2026. Every H-1B petition starts with one, so more filings means a longer record of sponsoring this work."],
  "new-hires": ["New H-1B employment", "Filings for a new H-1B job, often a person's first H-1B (this can include someone already working at the company on student work authorization), as opposed to an extension or a transfer from another employer. The closest sign the company takes on people who need their first H-1B."],
  "wage-level": ["Entry wage levels", "Each filing sets a prevailing wage level for the role and city, from I (entry) to IV (most experienced). This is the share at levels I and II, where most new grads are filed. From FY2027 the lottery gives higher levels more entries, based on the wage actually offered."],
  median: ["Median wage filed", "The middle yearly salary on this company's filings for this kind of role."],
  pay: ["Pay", "The pay range the job posting itself states, made yearly when it's hourly. Many US states require it, but not every posting lists one, so the pay filter only shows jobs that do."],
  level: ["Level", "New grad: the title says new grad, junior, entry level or similar. Not in title: the title doesn't say a level, and the description asks for 2 years of experience or less, or doesn't say. Mid and Senior: from the title (II, Senior, Staff, Lead and so on), or from the years the description asks for (3 to 4 is mid, 5 or more is senior). Internship: interns and co-ops."],
  "no-filings": ["No filings found", "No certified H-1B filings for this kind of role under the names we matched for this company. It may file under a different legal name, so this isn't proof it doesn't sponsor."],
};

const $ = (id) => document.getElementById(id);
const store = {
  get(k, fallback) { try { const v = localStorage.getItem(k); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};
const wide = () => matchMedia("(min-width: 1000px)").matches;
const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const LEVEL_ORDER = ["intern", "entry", "early", "mid", "senior"];

let jobs = [];
let companies = {};
let browse = structuredClone(DEFAULTS); // filters while browsing
let state = browse; // the filters in use: `browse`, or a search's own
let view = "jobs"; // or "saved"
let onlyNew = false;
let shown = PAGE;
let currentJob = null; // key of the job in the preview
let visible = []; // keys of the rows on screen, in order, for keyboard moves
const saved = new Set(store.get("ff.saved", []));
const applied = new Set(store.get("ff.applied", []));
// Jobs and companies a visitor chose to hide, kept in this browser only
const hiddenJobs = new Set(store.get("ff.hiddenJobs", []));
const hiddenCompanies = new Set(store.get("ff.hiddenCompanies", []));
let showHidden = false;
// Tracker: a status per job, a private note, and a snapshot (title, company,
// link) so a job stays in "My jobs" after its posting closes
const STAGES = ["saved", "applied", "interviewing", "offer", "rejected"];
const STAGE_NAMES = { saved: "Saved", applied: "Applied", interviewing: "Interviewing", offer: "Offer", rejected: "Not selected" };
const stages = store.get("ff.stage", {}); // key -> interviewing | offer | rejected
const notes = store.get("ff.notes", {});
const snap = store.get("ff.snap", {});
let mineFilter = "all";
const stageOf = (k) => stages[k] || (applied.has(k) ? "applied" : saved.has(k) ? "saved" : null);
const tracked = () => [...new Set([...saved, ...applied, ...Object.keys(stages)])];
// Saved searches: filters or a query, with the time they were last opened
const searches = store.get("ff.searches", []);
const isHidden = (j) => hiddenJobs.has(key(j)) || hiddenCompanies.has(j.company);
let lastVisit = null;

function remember(j) {
  snap[key(j)] = { t: j.title, c: j.company, u: j.url, l: j.location || "", p: j.posted_at || "", g: j.page || "" };
  store.set("ff.snap", snap);
}
function setStage(k, st) {
  delete stages[k];
  if (!st) { saved.delete(k); applied.delete(k); }
  else if (st === "saved") { saved.add(k); applied.delete(k); }
  else { saved.add(k); applied.add(k); if (st !== "applied") stages[k] = st; }
  const j = byKey(k);
  if (j) remember(j);
  store.set("ff.saved", [...saved]);
  store.set("ff.applied", [...applied]);
  store.set("ff.stage", stages);
  if (st) track(`stage-${st}`);
  updateSavedCount();
}

// ---- small helpers ----------------------------------------------------------

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c !== null && c !== undefined && c !== false) node.append(c);
  return node;
}
// replaceChildren prints null as text; this skips empty slots like el() does
function fill(node, ...kids) {
  node.replaceChildren(...kids.flat().filter((k) => k !== null && k !== undefined && k !== false));
}
function svg(path, extra = "") {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  if (extra) s.setAttribute("class", extra);
  s.innerHTML = path; // static icon markup only, never data
  return s;
}
const ICON = {
  flag: '<path d="M5 21V4m0 0h11l-2 4 2 4H5"/>',
  out: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  eye: '<path d="M3 3l18 18M10.6 10.6a2 2 0 0 0 2.8 2.8M9.9 5.1A9.8 9.8 0 0 1 12 5c5 0 9 4.5 10 7a12 12 0 0 1-3 4M6.1 6.1C3.9 7.6 2.5 9.8 2 12c1 2.5 5 7 10 7 1.6 0 3-.4 4.3-1"/>',
};
const key = (j) => `${j.system}|${j.slug}|${j.job_id}`;
let byKeyMap = new Map();
const byKey = (k) => byKeyMap.get(k);
const pageOf = (j) => (j.page ? `/job/${j.page}/` : j.url);
// The level filter's buckets. Titles that don't say a level are placed by the
// years the description asks for.
function bucket(j) {
  if (j.level !== "unclear") return j.level;
  if (j.min_years == null || j.min_years <= 2) return "early";
  return j.min_years >= 5 ? "senior" : "mid";
}
// Work setup: the posting's own arrangement, or "remote" when the location says so
const setupOf = (j) => j.arrangement || (j.is_remote ? "remote" : null);
const SETUP_NAMES = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site" };
const STRONG_SPONSOR = 50; // filings for the role
const money = (n) => (n ? `$${Math.round(n / 1000)}K` : "n/a");
const payText = (j) => (j.salary_min ? (j.salary_min === j.salary_max ? money(j.salary_min) : `${money(j.salary_min)}–${money(j.salary_max)}`) : null);
// Filing evidence lives once per company and field in companies.json
const evOf = (j) => companies[j.company]?.evidence?.[j.role] || null;
const num = (n) => n.toLocaleString("en-US");
function ago(iso) {
  const mins = Math.max(0, (Date.now() - Date.parse(iso)) / 60000);
  if (mins < 60) return `${Math.max(1, Math.round(mins))} min ago`;
  const h = mins / 60;
  if (h < 24) return `${Math.round(h)}h ago`;
  const d = Math.round(h / 24);
  return d === 1 ? "1 day ago" : `${d} days ago`;
}
function initials(name) {
  const words = name.replace(/[^A-Za-z0-9 ]/g, " ").split(/\s+/).filter(Boolean);
  return ((words[0] || "?")[0] + (words.length > 1 ? words[1][0] : (words[0] || "")[1] || "")).toUpperCase();
}
function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 2200);
}
function toastUndo(msg, undo) {
  const t = $("toast");
  t.replaceChildren(msg, " ", el("button", { type: "button", class: "linkbtn toast-undo", onclick: () => { undo(); t.hidden = true; } }, "Undo"));
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 5000);
}
// Anonymous counts of what people do (GoatCounter, no cookies). These are
// the only record of saves and applies, which otherwise live in one browser.
function track(name, title = "") {
  try { window.goatcounter?.count?.({ path: name, title, event: true }); } catch (e) {}
}

// ---- URL state ----------------------------------------------------------------

// Filters from URL parameters. Used for the address bar and for saved searches.
function parseState(p) {
  const list = (k) => (p.has(k) ? p.get(k).split(",").filter(Boolean) : null);
  const q = (p.get("q") || "").trim();
  const st = structuredClone(q ? SEARCH_DEFAULTS : DEFAULTS);
  st.f = list("f") ?? st.f;
  st.l = (list("l") ?? st.l).map((x) => (x === "unclear" ? "early" : x)); // links from before mid/senior existed
  st.loc = list("loc") ?? [];
  if (["1", "3", "7", "30"].includes(p.get("age"))) st.age = p.get("age");
  if (p.get("all") === "1") st.filed = false;
  if (p.get("filed") === "1") st.filed = true;
  if (p.get("hideapplied") === "1") st.hideApplied = true;
  st.pay = Math.max(0, Number(p.get("pay")) || 0);
  st.setup = (list("setup") ?? []).filter((x) => ["remote", "hybrid", "onsite"].includes(x));
  if (p.get("strong") === "1") st.strong = true;
  st.q = q;
  if (["new", "filings", "wage", "match", "pay"].includes(p.get("sort"))) st.sort = p.get("sort");
  return st;
}
function readURL() {
  const p = new URLSearchParams(location.search);
  state = parseState(p);
  if (!state.q) browse = state;
  view = p.get("view") === "saved" ? "saved" : "jobs";
  return { company: p.get("company"), job: p.get("job") };
}
// Campaign tags (?ref=reddit, utm_*) say which channel a visitor came from.
// They stay in the address for the whole visit, so the analytics script reads
// them whenever it loads, but "Share" copies a link without them.
const CAMPAIGN = new URLSearchParams(
  [...new URLSearchParams(location.search)].filter(([k]) => k === "ref" || k.startsWith("utm_")),
);
function writeURL({ withJob = true } = {}) {
  const p = new URLSearchParams();
  const same = (a, b) => a.length === b.length && a.every((x) => b.includes(x));
  const d = defaultsFor(state);
  if (state.q) p.set("q", state.q);
  if (!same(state.f, d.f)) p.set("f", state.f.join(","));
  if (!same(state.l, d.l)) p.set("l", state.l.join(","));
  if (state.age !== d.age) p.set("age", state.age);
  if (state.loc.length) p.set("loc", state.loc.join(","));
  if (state.filed !== d.filed) p.set(state.filed ? "filed" : "all", "1");
  if (state.hideApplied) p.set("hideapplied", "1");
  if (state.pay) p.set("pay", state.pay);
  if (state.setup.length) p.set("setup", state.setup.join(","));
  if (state.strong) p.set("strong", "1");
  if (state.sort !== d.sort) p.set("sort", state.sort);
  if (view === "saved") p.set("view", "saved");
  if (withJob && currentJob && wide()) p.set("job", currentJob);
  for (const [k, v] of CAMPAIGN) p.set(k, v);
  const qs = p.toString().replace(/%2C/g, ",").replace(/%3A/g, ":").replace(/%7C/g, "|");
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
}

// ---- filtering -------------------------------------------------------------------

function placeMatch(j, loc) {
  return loc.some((p) => (p === "remote" ? j.is_remote : p.startsWith("m:") ? j.metros.includes(p.slice(2)) : p.startsWith("s:") ? j.states.includes(p.slice(2)) : false));
}
// `ignore` leaves one filter out, so each filter's menu can count what picking
// an option would give with everything else as it is
function matches(j, ignore = null) {
  if (!showHidden && isHidden(j)) return false;
  if (ignore !== "f" && !state.f.includes(j.role)) return false;
  if (ignore !== "l" && !state.l.includes(bucket(j))) return false;
  if (ignore !== "age" && j.posted_at && Date.now() - Date.parse(j.posted_at) > Number(state.age) * 864e5) return false;
  if (state.filed && !evOf(j)) return false;
  if (state.hideApplied && applied.has(key(j))) return false;
  if (state.pay && !(j.salary_max >= state.pay)) return false; // only jobs that list pay
  if (ignore !== "setup" && state.setup.length && !state.setup.includes(setupOf(j))) return false;
  if (state.strong && !(evOf(j)?.filings >= STRONG_SPONSOR)) return false;
  if (ignore !== "loc" && state.loc.length && !placeMatch(j, state.loc)) return false;
  if (scores && !scores.has(key(j))) return false;
  return true;
}

// ---- search ----------------------------------------------------------------------
// Ranked matching with MiniSearch (site/vendor): typos, plurals and prefixes,
// plus the abbreviations and related roles people actually type.

const SYNONYMS = {
  swe: "software engineer", sde: "software development engineer", dev: "developer", eng: "engineer",
  ml: "machine learning", mle: "machine learning engineer", ai: "ai", nlp: "natural language",
  ds: "data scientist", de: "data engineer", da: "data analyst", bi: "business intelligence",
  pm: "product manager", tpm: "technical program manager", po: "product owner",
  qa: "quality assurance", sdet: "test engineer", sre: "site reliability", devops: "devops",
  ux: "ux", ui: "ui", frontend: "front end", backend: "back end", fullstack: "full stack",
  fe: "front end", be: "back end", infosec: "security", cyber: "security", cybersecurity: "security",
  appsec: "application security", hw: "hardware", ee: "electrical engineer", it: "it",
  newgrad: "new grad", jr: "junior", sr: "senior", swes: "software engineer",
};
const RELATED = {
  "software engineer": ["software developer", "developer", "programmer", "software development engineer"],
  "data engineer": ["analytics engineer", "etl developer", "data platform engineer"],
  "data scientist": ["data science", "machine learning", "applied scientist"],
  "data analyst": ["business intelligence", "analytics", "reporting analyst"],
  "machine learning engineer": ["ml engineer", "ai engineer", "applied scientist"],
  "machine learning": ["ai", "deep learning"],
  "product designer": ["ux designer", "ui designer", "user experience"],
  "ux designer": ["product designer", "user experience", "ui designer"],
  "security engineer": ["cybersecurity", "information security", "application security"],
  "product manager": ["product owner", "technical program manager"],
  "site reliability": ["devops", "platform engineer", "infrastructure engineer"],
  devops: ["site reliability", "platform engineer", "cloud engineer"],
  "front end": ["frontend", "react", "web developer"],
  "back end": ["backend", "api", "server"],
  "electrical engineer": ["hardware engineer", "electronics engineer"],
  "new grad": ["entry level", "junior", "graduate", "university"],
};
const STOP = new Set(["and", "or", "the", "a", "an", "of", "in", "for", "to", "at", "with", "job", "jobs", "role", "roles"]);
const processTerm = (t) => {
  t = t.toLowerCase();
  if (STOP.has(t)) return null;
  return t.length > 3 && t.endsWith("s") && !t.endsWith("ss") ? t.slice(0, -1) : t;
};
const expand = (q) => q.toLowerCase().split(/[^a-z0-9+#.]+/).filter(Boolean).map((t) => SYNONYMS[t] || t).join(" ");

let index = null;
let indexed = -1;
let scores = null; // key -> relevance while a search is active
let loose = false; // nothing matched every word, so showing the closest jobs
let titles = new Map(); // key -> normalized title, for exact-phrase boosts
function ensureIndex() {
  if (!window.MiniSearch) return null;
  if (index && indexed === jobs.length) return index;
  index = new window.MiniSearch({
    idField: "k",
    fields: ["title", "company", "location", "field"],
    processTerm,
    searchOptions: { boost: { title: 3, company: 2 }, prefix: true, fuzzy: (t) => (t.length > 4 ? 0.2 : false) },
  });
  index.addAll(jobs.map((j) => ({ k: key(j), title: j.title, company: j.company, location: j.location || "", field: FIELD[j.role] })));
  titles = new Map(jobs.map((j) => [key(j), j.title.toLowerCase().replace(/[^a-z0-9+#]+/g, " ")]));
  indexed = jobs.length;
  return index;
}
function searchScores(q) {
  const out = new Map();
  loose = false;
  const ix = ensureIndex();
  if (!ix) {
    // MiniSearch didn't load: every word must appear
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    for (const j of jobs) if (words.every((w) => `${j.title} ${j.company} ${j.location || ""}`.toLowerCase().includes(w))) out.set(key(j), 1);
    return out;
  }
  const add = (results, weight) => results.forEach((r) => out.set(r.id, Math.max(out.get(r.id) || 0, r.score * weight)));
  const main = expand(q);
  add(ix.search(main, { combineWith: "AND" }), 1);
  if (main !== q.toLowerCase()) add(ix.search(q, { combineWith: "AND" }), 1);
  for (const [phrase, others] of Object.entries(RELATED)) {
    if (main.includes(phrase)) for (const o of others) add(ix.search(main.replace(phrase, o), { combineWith: "AND" }), 0.6);
  }
  if (!out.size) {
    // Closest jobs: they must share at least half the search words
    loose = true;
    const need = Math.ceil(main.split(" ").filter((t) => processTerm(t)).length / 2);
    add(ix.search(main, { combineWith: "OR" }).filter((r) => (r.queryTerms || r.terms).length >= need), 0.5);
  }
  // A title holding the exact phrase beats one that only shares its words
  const phrase = ` ${main.replace(/[^a-z0-9+#]+/g, " ").trim()} `;
  for (const [k, v] of out) if (` ${titles.get(k)} `.includes(phrase)) out.set(k, v * 3);
  return out;
}
const isNew = (j) => lastVisit && j.first_seen_at && Date.parse(j.first_seen_at) > lastVisit;
function filtered() {
  scores = state.q ? searchScores(state.q) : null;
  let out = jobs.filter((j) => matches(j));
  const newCount = out.filter(isNew).length;
  if (onlyNew) out = out.filter(isNew);
  const when = (j) => Date.parse(j.posted_at || j.first_seen_at || 0);
  const by = {
    new: (a, b) => when(b) - when(a),
    filings: (a, b) => (evOf(b)?.filings || 0) - (evOf(a)?.filings || 0) || when(b) - when(a),
    wage: (a, b) => (evOf(b)?.median_wage || 0) - (evOf(a)?.median_wage || 0) || when(b) - when(a),
    match: (a, b) => (scores?.get(key(b)) || 0) - (scores?.get(key(a)) || 0) || when(b) - when(a),
    pay: (a, b) => (b.salary_max || 0) - (a.salary_max || 0) || when(b) - when(a),
  };
  out.sort(by[scores ? state.sort : state.sort === "match" ? "new" : state.sort]);
  return { out, newCount };
}

// ---- record pieces ------------------------------------------------------------

function levelBar(ev, label) {
  const total = ev.level_1 + ev.level_2 + ev.level_3 + ev.level_4;
  if (!total) return null;
  const bar = el("div", { class: "bar", role: "img", "aria-label": `${label}: Level I ${ev.level_1}, II ${ev.level_2}, III ${ev.level_3}, IV ${ev.level_4} filings` });
  [["l1", "I", ev.level_1], ["l2", "II", ev.level_2], ["l3", "III", ev.level_3], ["l4", "IV", ev.level_4]].forEach(([cls, name, n]) => {
    if (!n) return;
    const seg = el("span", { class: `seg ${cls}`, style: `flex:${n}` });
    seg.dataset.tip = JSON.stringify([`Level ${name}`, `${num(n)} filings`, `${Math.round((n * 100) / total)}%`]);
    bar.append(seg);
  });
  return bar;
}
const lowShare = (ev) => {
  const t = ev.level_1 + ev.level_2 + ev.level_3 + ev.level_4;
  return t ? Math.round(((ev.level_1 + ev.level_2) * 100) / t) : null;
};
function infoBtn(topic) {
  return el("button", { type: "button", class: "info", "data-explain": topic, "aria-label": `What does ${EXPLAIN[topic][0].toLowerCase()} mean?` }, "?");
}
function logo(name, cls = "") {
  const c = companies[name];
  const box = el("span", { class: `logo ${cls}`, "aria-hidden": "true" }, initials(name));
  if (c?.logo) {
    const img = el("img", { src: c.logo, alt: "", loading: "lazy" });
    img.addEventListener("error", () => img.remove());
    img.addEventListener("load", () => { box.firstChild.nodeType === 3 && box.firstChild.remove(); });
    box.append(img);
  }
  return box;
}
function companyLink(name) {
  const page = companies[name]?.page;
  return page ? el("a", { href: page }, name) : el("span", {}, name);
}
function reportURL(j) {
  const p = new URLSearchParams({ template: "job-report.yml", title: `Report: ${j.title} at ${j.company}`, "job-url": j.url, company: j.company });
  return `${REPO}/issues/new?${p}`;
}

// ---- rows in the register ---------------------------------------------------------

function row(j) {
  const k = key(j);
  const ev = evOf(j);
  const notice = companies[j.company]?.notice;
  return el("li", {},
    el("a", { class: `row${applied.has(k) ? " applied" : ""}`, href: pageOf(j), "data-k": k, "aria-current": String(k === currentJob) },
      logo(j.company),
      el("span", { class: "row-main" },
        el("span", { class: "row-title" }, j.title),
        el("span", { class: "row-sub" }, j.company, el("span", { class: "slash", "aria-hidden": "true" }, "/"), j.location || "Location not listed"),
        ev
          ? el("span", { class: "row-ev" }, `${num(ev.filings)} ${FIELD[j.role].toLowerCase()} filings on record`)
          : el("span", { class: "row-ev none" }, "No filings found for this kind of role")),
      el("span", { class: "row-side" },
        payText(j) ? el("span", { class: "pay" }, payText(j)) : null,
        j.posted_at ? el("span", {}, ago(j.posted_at)) : null,
        el("span", { class: "row-tags" },
          isNew(j) ? el("span", { class: "tag new" }, "New") : null,
          applied.has(k) ? el("span", { class: "tag done" }, "Applied") : saved.has(k) ? el("span", { class: "tag done" }, "Saved") : null,
          notice ? el("span", { class: "tag notice", title: notice.detail }, notice.notice) : null))));
}

// ---- preview pane -------------------------------------------------------------------

function fact(label, value) {
  return value ? el("div", {}, el("dt", {}, label), el("dd", {}, value)) : null;
}
function toggleBtn(set, storeKey, k, on, off, onMsg, offMsg, event) {
  const b = el("button", { type: "button", class: "btn", "aria-pressed": String(set.has(k)) }, set.has(k) ? on : off);
  b.addEventListener("click", () => {
    const now = !set.has(k);
    now ? set.add(k) : set.delete(k);
    if (set === applied && now) saved.add(k); // applied jobs are tracked too
    store.set(storeKey, [...set]);
    store.set("ff.saved", [...saved]);
    const j = byKey(k);
    if (now && j) { remember(j); track(event, j.company); }
    b.setAttribute("aria-pressed", String(now));
    b.textContent = now ? on : off;
    b.classList.remove("saved-pop");
    void b.offsetWidth;
    if (now) b.classList.add("saved-pop");
    toast(now ? onMsg : offMsg);
    updateSavedCount();
    refreshRow(k);
  });
  return b;
}
function refreshRow(k) {
  const a = document.querySelector(`.row[data-k="${CSS.escape(k)}"]`);
  const j = byKey(k);
  if (a && j) a.parentElement.replaceWith(row(j));
}
function record(j) {
  const ev = evOf(j);
  const c = companies[j.company] || {};
  const field = FIELD[j.role];
  const more = c.page ? el("a", { href: c.page }, `${j.company}'s full sponsor page`) : null;
  if (!ev) {
    return el("section", { class: "record none", "aria-label": "Sponsor record" },
      el("div", { class: "record-head" }, el("h3", {}, "Sponsor record"), el("span", {}, `${j.company}, ${field.toLowerCase()} roles`)),
      el("div", { class: "record-grid" }, el("p", { class: "muted" }, "No H-1B filings found for this kind of role under the names we matched. ", infoBtn("no-filings"))),
      more ? el("div", { class: "record-foot" }, more) : null);
  }
  const share = lowShare(ev);
  const cell = (label, value, topic) => (value == null ? null : el("div", {}, el("dt", {}, label, infoBtn(topic)), el("dd", {}, value)));
  return el("section", { class: "record", "aria-label": "Sponsor record" },
    el("div", { class: "record-head" }, el("h3", {}, `Sponsor record: ${j.company}, ${field.toLowerCase()} roles`), el("span", {}, "Department of Labor data, Oct 2024 to Jun 2026")),
    el("dl", { class: "record-grid" },
      cell("Certified filings", num(ev.filings), "filings"),
      cell("New H-1B employment", num(ev.new_hire_filings), "new-hires"),
      cell("At entry wage levels", share === null ? null : `${share}%`, "wage-level"),
      cell("Median wage filed", ev.median_wage ? money(ev.median_wage) : null, "median")),
    el("div", { class: "record-foot" },
      levelBar(ev, `${j.company} ${field} filings by wage level`),
      el("div", { class: "key" }, ...["I", "II", "III", "IV"].map((n, i) => el("span", {}, el("i", { class: `sw l${i + 1}` }), n)), el("span", {}, "Wage levels, I is the entry end")),
      el("p", {}, "A filing is an application the employer made before hiring or keeping someone on H-1B in this kind of role. It shows what they did, not a promise for this job."),
      more));
}
function paneFor(j) {
  const k = key(j);
  const c = companies[j.company] || {};
  const years = j.min_years == null ? null : j.min_years === 0 ? "None asked" : `${j.min_years}+ years asked`;
  const others = jobs.filter((x) => x.company === j.company && key(x) !== k).sort((a, b) => Date.parse(b.posted_at || 0) - Date.parse(a.posted_at || 0)).slice(0, 4);
  return el("div", { class: "pane-body" },
    el("div", { class: "pane-head" },
      logo(j.company, "lg"),
      el("div", {},
        el("h2", {}, j.title),
        el("p", {}, companyLink(j.company), `, ${j.location || "location not listed"}`)),
      el("a", { class: "btn apply", href: j.url, target: "_blank", rel: "noopener", onclick: () => track("apply", j.company) }, "Apply", svg(ICON.out))),
    el("dl", { class: "facts" },
      fact("Pay", payText(j) ? `${payText(j)} a year` : "Not in the posting"),
      fact("Work setup", SETUP_NAMES[setupOf(j)] || "Not stated"),
      fact("Level", LEVELS[bucket(j)]),
      fact("Experience", years),
      fact("Posted", j.posted_at ? ago(j.posted_at) : null)),
    el("div", { class: "actions" },
      el("a", { class: "btn primary", href: pageOf(j), onclick: () => track("see-whole-job", j.company) }, "See the whole job"),
      toggleBtn(saved, "ff.saved", k, "Saved", "Save", "Saved", "Removed from saved", "save"),
      toggleBtn(applied, "ff.applied", k, "Applied", "Mark applied", "Marked as applied", "Unmarked", "applied")),
    c.notice ? el("div", { class: "notice-box", role: "note" }, el("strong", {}, c.notice.notice), ` (${c.notice.date}). ${c.notice.detail} `, el("a", { href: c.notice.source, target: "_blank", rel: "noopener" }, "Source")) : null,
    record(j),
    others.length ? el("section", { class: "pane-section" }, el("h3", {}, `More at ${j.company}`),
      el("ul", { class: "mini-jobs" }, ...others.map((x) => el("li", {}, el("a", { href: pageOf(x), "data-k": key(x) },
        el("strong", {}, x.title), el("span", {}, `${x.location || "Location not listed"}${x.posted_at ? `, ${ago(x.posted_at)}` : ""}`)))))) : null,
    el("div", { class: "pane-links" },
      el("button", { type: "button", class: "linkbtn", onclick: () => hide("job", j) }, "Hide this job"),
      el("button", { type: "button", class: "linkbtn", onclick: () => hide("company", j) }, `Hide all ${j.company} jobs`),
      el("a", { class: "linkbtn", href: reportURL(j), target: "_blank", rel: "noopener", onclick: () => track("report", j.company) }, "Report a problem")),
  );
}
function renderPane() {
  const j = currentJob && byKey(currentJob);
  const pane = $("pane");
  if (!j) {
    fill(pane, el("div", { class: "pane-empty" }, visible.length ? "Pick a job to see it here." : "No job to show."));
    return;
  }
  fill(pane, paneFor(j));
  pane.scrollTop = 0;
}
// Show a job in the preview, fading the new one in
function select(k, { transition = true } = {}) {
  if (!byKey(k)) return;
  currentJob = k;
  document.querySelectorAll(".row[aria-current]").forEach((a) => a.setAttribute("aria-current", String(a.dataset.k === k)));
  renderPane();
  if (transition && !calm()) $("pane").firstChild?.classList.add("fade-in");
  writeURL();
}
function hide(what, j) {
  const set = what === "job" ? hiddenJobs : hiddenCompanies;
  const value = what === "job" ? key(j) : j.company;
  set.add(value);
  store.set(what === "job" ? "ff.hiddenJobs" : "ff.hiddenCompanies", [...set]);
  track(what === "job" ? "hide-job" : "hide-company");
  currentJob = null;
  render();
  toastUndo(what === "job" ? "Job hidden" : `${j.company} hidden`, () => {
    set.delete(value);
    store.set(what === "job" ? "ff.hiddenJobs" : "ff.hiddenCompanies", [...set]);
    render();
  });
}

// ---- render ----------------------------------------------------------------------

function render() {
  if (view === "saved") return renderMine();
  const { out, newCount } = filtered();
  // Load far enough down the list to include the job in the preview
  const at = currentJob ? out.findIndex((j) => key(j) === currentJob) : -1;
  if (at >= shown) shown = Math.ceil((at + 1) / PAGE) * PAGE;
  const list = out.slice(0, shown);
  visible = list.map(key);
  if (wide() && (!currentJob || !visible.includes(currentJob))) currentJob = visible[0] || null;
  const ol = $("jobs");
  if (!list.length) {
    ol.replaceChildren(el("li", { class: "empty" }, el("strong", {}, "No jobs match these filters"), "Try a longer time window, another location, or fewer filters."));
  } else {
    const frag = document.createDocumentFragment();
    list.forEach((j) => frag.append(row(j)));
    ol.replaceChildren(frag);
  }
  const searching = Boolean(scores);
  const n = out.length;
  const ageText = { 1: "the last 24 hours", 3: "the last 3 days", 7: "the last 7 days", 30: "the last 30 days" }[state.age];
  fill($("count"),
    el("b", {}, `${num(n)} ${searching ? (n === 1 ? "result" : "results") : n === 1 ? "job" : "jobs"}`),
    searching ? ` for “${state.q}”` : ` posted in ${ageText}${state.filed ? " at companies that filed H-1B applications for that kind of role" : ""}.`,
    searching && loose && n ? el("span", { class: "loose" }, " No exact matches, so these are the closest.") : null,
    searching ? [" ", el("button", { type: "button", class: "linkbtn", onclick: () => setQuery("") }, "Clear search")] : null);
  const nt = $("new-toggle");
  nt.hidden = !newCount;
  nt.textContent = onlyNew ? `Showing ${newCount} new. Show all` : `${newCount} new since your last visit`;
  nt.setAttribute("aria-pressed", String(onlyNew));
  $("more").hidden = n <= shown;
  $("more").textContent = `Show ${Math.min(PAGE, n - shown)} more`;
  $("nav-jobs").setAttribute("aria-current", "page");
  $("nav-saved").setAttribute("aria-current", "false");
  $("pane").hidden = false;
  renderPane();
  if (state.age === "30" || state.q) loadOlder();
  syncControls();
  writeURL();
}
function rerender() { shown = PAGE; currentJob = null; render(); }

// ---- My jobs: tracker and saved searches --------------------------------------------

function trackerRow(k) {
  const sel = el("select", { class: "select stage", "aria-label": "Status" },
    ...STAGES.map((st) => el("option", { value: st, selected: stageOf(k) === st }, STAGE_NAMES[st])));
  sel.addEventListener("change", () => { setStage(k, sel.value); renderMine(); });
  const note = el("textarea", { class: "note", rows: 1, placeholder: "Notes: referral, recruiter, interview dates", "aria-label": "Notes" });
  note.value = notes[k] || "";
  note.addEventListener("input", () => {
    if (note.value.trim()) notes[k] = note.value; else delete notes[k];
    store.set("ff.notes", notes);
  });
  const remove = el("button", { type: "button", class: "linkbtn", onclick: () => {
    const before = { st: stageOf(k), n: notes[k] };
    setStage(k, null);
    renderMine();
    toastUndo("Removed from My jobs", () => { setStage(k, before.st); if (before.n) { notes[k] = before.n; store.set("ff.notes", notes); } renderMine(); });
  } }, "Remove");
  return el("div", { class: "tracker" }, sel, note, remove);
}
function closedRow(k) {
  const s0 = snap[k] || { t: "A job you saved", c: "", u: "#", l: "" };
  return el("li", {},
    el("a", { class: "row closed", href: s0.u, target: "_blank", rel: "noopener" },
      logo(s0.c || "?"),
      el("span", { class: "row-main" },
        el("span", { class: "row-title" }, s0.t),
        el("span", { class: "row-sub" }, s0.c, el("span", { class: "slash", "aria-hidden": "true" }, "/"), s0.l || "Location not listed"),
        el("span", { class: "row-ev none" }, "No longer listed on FiledFor"))),
    trackerRow(k));
}
function describe(st) {
  if (st.q) return `“${st.q}”`;
  const parts = [];
  if (st.f.length < Object.keys(FIELD).length) parts.push(st.f.map((x) => FIELD[x]).join(", "));
  parts.push(st.l.map((x) => LEVELS[x]).join(", "));
  if (st.loc.length) parts.push(st.loc.map(placeName).join(", "));
  if (st.setup.length) parts.push(st.setup.map((x) => SETUP_NAMES[x]).join(", "));
  if (st.pay) parts.push(`${money(st.pay)}+`);
  return parts.join(", ");
}
function searchQS() {
  const p = new URLSearchParams(location.search);
  for (const k of ["company", "job", "view", "ref"]) p.delete(k);
  for (const k of [...p.keys()]) if (k.startsWith("utm_")) p.delete(k);
  return p.toString();
}
function saveSearch() {
  const qs = searchQS();
  if (searches.some((x) => x.qs === qs)) return toast("This search is already saved");
  searches.push({ id: Date.now(), name: describe(state), qs, seen: Date.now() });
  store.set("ff.searches", searches);
  track("save-search");
  toast("Search saved. Find it under My jobs");
  updateSavedCount();
}
function newFor(s0) {
  // Evaluate a saved search without leaving the current view
  const keep = { state, scores, view };
  try {
    state = parseState(new URLSearchParams(s0.qs));
    view = "jobs";
    scores = state.q ? searchScores(state.q) : null;
    return jobs.filter((j) => matches(j) && j.first_seen_at && Date.parse(j.first_seen_at) > s0.seen).length;
  } finally {
    ({ state, scores, view } = keep);
  }
}
function openSearch(s0) {
  s0.seen = Date.now();
  store.set("ff.searches", searches);
  history.replaceState(null, "", `?${s0.qs}`);
  readURL();
  view = "jobs";
  rerender();
}
function exportCSV() {
  const rows = [["Title", "Company", "Location", "Status", "Note", "Link", "Posted"]];
  for (const k of tracked()) {
    const j = byKey(k);
    const s0 = j ? { t: j.title, c: j.company, l: j.location || "", u: j.url, p: j.posted_at || "" } : snap[k] || {};
    rows.push([s0.t, s0.c, s0.l, STAGE_NAMES[stageOf(k)] || "", notes[k] || "", s0.u, (s0.p || "").slice(0, 10)]);
  }
  const csv = rows.map((r) => r.map((v) => `"${String(v ?? "").replace(/"/g, '""')}"`).join(",")).join("\n");
  const a = el("a", { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: "filedfor-my-jobs.csv" });
  document.body.append(a);
  a.click();
  a.remove();
  track("export-csv");
}
function renderMine() {
  const keys = tracked().filter((k) => mineFilter === "all" || stageOf(k) === mineFilter);
  keys.sort((a, b) => STAGES.indexOf(stageOf(a)) - STAGES.indexOf(stageOf(b)));
  visible = keys.filter((k) => byKey(k));
  if (wide() && (!currentJob || !visible.includes(currentJob))) currentJob = visible[0] || null;
  const head = el("li", { class: "mine-head" },
    searches.length ? el("section", { class: "searches" }, el("h3", {}, "Saved searches"),
      el("ul", {}, ...searches.map((s0) => {
        const n = newFor(s0);
        return el("li", {},
          el("button", { type: "button", class: "linkbtn search-name", onclick: () => openSearch(s0) }, s0.name),
          n ? el("span", { class: "tag new" }, `${n} new`) : el("span", { class: "muted" }, "nothing new"),
          el("button", { type: "button", class: "linkbtn muted", "aria-label": `Remove saved search ${s0.name}`, onclick: () => { searches.splice(searches.indexOf(s0), 1); store.set("ff.searches", searches); renderMine(); } }, "Remove"));
      }))) : null,
    el("div", { class: "mine-bar" },
      el("div", { class: "chips" }, ...["all", ...STAGES].map((st) => el("button", {
        type: "button", class: "chip", "aria-pressed": String(mineFilter === st),
        onclick: () => { mineFilter = st; renderMine(); },
      }, st === "all" ? `All (${tracked().length})` : `${STAGE_NAMES[st]} (${tracked().filter((k) => stageOf(k) === st).length})`))),
      tracked().length ? el("button", { type: "button", class: "btn", onclick: exportCSV }, "Export CSV") : null));
  const items = keys.map((k) => {
    const j = byKey(k);
    if (!j) return closedRow(k);
    remember(j);
    const li = row(j);
    li.append(trackerRow(k));
    return li;
  });
  $("jobs").replaceChildren(head, ...(items.length ? items : [el("li", { class: "empty" },
    el("strong", {}, tracked().length ? "Nothing in this status" : "Nothing here yet"),
    "Save a job from its preview or its page to track it here. Everything stays in this browser.")]));
  fill($("count"), el("b", {}, `${num(tracked().length)} tracked ${tracked().length === 1 ? "job" : "jobs"}`), ". Saved in this browser only.");
  $("new-toggle").hidden = true;
  $("more").hidden = true;
  $("nav-jobs").setAttribute("aria-current", "false");
  $("nav-saved").setAttribute("aria-current", "page");
  renderPane();
  loadOlder();
  syncControls();
  writeURL();
}

// Starting a search switches to its own wide filters; clearing it brings the
// browsing filters back exactly as they were
let searchTracked;
function setQuery(q) {
  if (q && !state.q) state = { ...structuredClone(SEARCH_DEFAULTS), q };
  else if (q) state.q = q;
  else state = browse;
  if (!q) $("q").value = "";
  view = "jobs";
  clearTimeout(searchTracked);
  if (q) searchTracked = setTimeout(() => track("search", q.slice(0, 60)), 1500);
  rerender();
}

// ---- filter menus ------------------------------------------------------------------

const STATE_NAMES = { AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado", CT: "Connecticut", DE: "Delaware", DC: "District of Columbia", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah", VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming", PR: "Puerto Rico" };
const placeName = (p) => (p === "remote" ? "Remote" : p.startsWith("m:") ? p.slice(2) : STATE_NAMES[p.slice(2)] || p.slice(2));

let openDD = null; // name of the open menu
function option({ label, hint, n, checked, radio = false, onpick }) {
  return el("button", { type: "button", class: `opt${radio ? " radio" : ""}`, role: radio ? "menuitemradio" : "menuitemcheckbox", "aria-checked": String(checked), onclick: onpick },
    el("span", { class: "box", "aria-hidden": "true" }),
    el("span", {}, label, hint ? el("small", {}, hint) : null),
    n == null ? null : el("span", { class: "n" }, num(n)));
}
// Counts for one filter, with every other filter applied
function countBy(dim, fn) {
  const m = new Map();
  for (const j of jobs) if (matches(j, dim)) for (const v of [].concat(fn(j))) m.set(v, (m.get(v) || 0) + 1);
  return m;
}
function toggleIn(list, v, keepOne) {
  return list.includes(v) ? (list.length > 1 || !keepOne ? list.filter((x) => x !== v) : list) : [...list, v];
}
const MENUS = {
  f() {
    const n = countBy("f", (j) => j.role);
    const all = state.f.length === Object.keys(FIELD).length;
    return [
      option({ label: "All tech fields", checked: all, onpick: () => { state.f = Object.keys(FIELD); rerender(); } }),
      ...Object.entries(FIELD).map(([v, name]) => option({ label: name, n: n.get(v) || 0, checked: !all && state.f.includes(v), onpick: () => { state.f = all ? [v] : toggleIn(state.f, v, true); rerender(); } })),
    ];
  },
  l() {
    const n = countBy("l", bucket);
    return [
      el("h3", {}, "Level ", infoBtn("level")),
      ...Object.entries(LEVELS).map(([v, name]) => option({ label: name, hint: LEVEL_HINT[v], n: n.get(v) || 0, checked: state.l.includes(v), onpick: () => { state.l = toggleIn(state.l, v, true); rerender(); } })),
    ];
  },
  loc() {
    const counts = countBy("loc", (j) => [...(j.is_remote ? ["remote"] : []), ...j.metros.map((m) => `m:${m}`), ...j.states.map((s) => `s:${s}`)]);
    const input = el("input", { type: "search", class: "place-search", placeholder: "Find a metro area or state", "aria-label": "Find a location" });
    const list = el("div", { class: "placelist" });
    const draw = () => {
      const term = input.value.trim().toLowerCase();
      const pick = (prefix) => [...counts].filter(([p]) => p.startsWith(prefix)).sort((a, b) => b[1] - a[1]);
      const groups = [["", [["remote", counts.get("remote") || 0]]], ["Metro areas", pick("m:")], ["States", pick("s:")]];
      const kids = [];
      for (const [title, items] of groups) {
        const rows = items.filter(([p]) => !term || placeName(p).toLowerCase().includes(term) || p.slice(2).toLowerCase() === term);
        if (!rows.length) continue;
        if (title) kids.push(el("h4", {}, title));
        rows.forEach(([p, c]) => kids.push(option({ label: placeName(p), n: c, checked: state.loc.includes(p), onpick: () => { state.loc = toggleIn(state.loc, p, false); rerender(); } })));
      }
      fill(list, kids.length ? kids : el("p", { class: "place-empty" }, "No matching locations"));
    };
    input.addEventListener("input", draw);
    draw();
    setTimeout(() => wide() && input.focus(), 0);
    return [
      state.loc.length ? el("div", { class: "selected-places" }, ...state.loc.map((p) => el("button", { type: "button", class: "tagx", "aria-label": `Remove ${placeName(p)}`, onclick: () => { state.loc = state.loc.filter((x) => x !== p); rerender(); } }, placeName(p), svg(ICON.x)))) : null,
      input, list,
    ];
  },
  more() {
    const seg = (items, cur, set) => el("div", { class: "seg-row", role: "radiogroup" }, ...items.map(([v, label]) => el("button", { type: "button", role: "radio", "aria-checked": String(cur === v), onclick: () => { set(v); rerender(); } }, label)));
    const setupN = countBy("setup", setupOf);
    const nHidden = hiddenJobs.size + hiddenCompanies.size;
    const pay = el("select", { class: "select", "aria-label": "Minimum pay" },
      ...[[0, "Any pay"], [80000, "$80K or more"], [100000, "$100K or more"], [120000, "$120K or more"], [150000, "$150K or more"], [200000, "$200K or more"]].map(([v, t]) => el("option", { value: v, selected: state.pay === v }, t)));
    pay.addEventListener("change", () => { state.pay = Number(pay.value) || 0; rerender(); });
    return [
      el("h3", {}, "Posted within"),
      seg([["1", "24 hours"], ["3", "3 days"], ["7", "7 days"], ["30", "30 days"]], state.age, (v) => (state.age = v)),
      el("h3", {}, "Work setup"),
      ...Object.entries(SETUP_NAMES).map(([v, name]) => option({ label: name, n: setupN.get(v) || 0, checked: state.setup.includes(v), onpick: () => { state.setup = toggleIn(state.setup, v, false); rerender(); } })),
      el("h3", {}, "Pay in the posting ", infoBtn("pay")),
      el("div", { class: "seg-row" }, pay),
      el("h3", {}, "Sponsor record"),
      option({ label: "Only companies with filings for the role", checked: state.filed, onpick: () => { state.filed = !state.filed; rerender(); } }),
      option({ label: "Frequent sponsors only", hint: "50 or more filings for this kind of role", checked: state.strong, onpick: () => { state.strong = !state.strong; rerender(); } }),
      option({ label: "Hide jobs I applied to", checked: state.hideApplied, onpick: () => { state.hideApplied = !state.hideApplied; rerender(); } }),
      nHidden ? option({ label: "Show jobs and companies I hid", hint: `${hiddenJobs.size} ${hiddenJobs.size === 1 ? "job" : "jobs"}, ${hiddenCompanies.size} ${hiddenCompanies.size === 1 ? "company" : "companies"}`, checked: showHidden, onpick: () => { showHidden = !showHidden; rerender(); } }) : null,
      el("div", { class: "dd-foot" },
        el("button", { type: "button", class: "linkbtn", onclick: () => { resetFilters(); } }, "Reset all filters"),
        el("button", { type: "button", class: "btn primary small", onclick: closeMenu }, "Done")),
    ];
  },
};
function menuFor(name) {
  const box = document.querySelector(`.dd[data-dd="${name}"]`);
  return { box, btn: box.querySelector(".dd-btn") };
}
function drawMenu() {
  if (!openDD) return;
  const { box } = menuFor(openDD);
  let panel = box.querySelector(".dd-panel");
  if (!panel) {
    panel = el("div", { class: `dd-panel${openDD === "more" ? " right" : ""}`, role: "dialog", "aria-label": box.querySelector(".dd-label").textContent });
    box.append(panel);
  }
  const keepScroll = panel.scrollTop;
  const focused = document.activeElement?.classList.contains("place-search") ? document.activeElement.value : null;
  fill(panel, MENUS[openDD]());
  panel.scrollTop = keepScroll;
  if (focused !== null) { const i = panel.querySelector(".place-search"); i.value = focused; i.dispatchEvent(new Event("input")); i.focus(); }
}
function openMenu(name) {
  if (openDD === name) return closeMenu();
  closeMenu();
  openDD = name;
  menuFor(name).btn.setAttribute("aria-expanded", "true");
  drawMenu();
}
function closeMenu() {
  if (!openDD) return;
  const { box, btn } = menuFor(openDD);
  box.querySelector(".dd-panel")?.remove();
  btn.setAttribute("aria-expanded", "false");
  openDD = null;
}
function resetFilters() {
  state = state.q ? { ...structuredClone(SEARCH_DEFAULTS), q: state.q } : (browse = structuredClone(DEFAULTS));
  onlyNew = false;
  showHidden = false;
  rerender();
}
function syncControls() {
  const d = defaultsFor(state);
  const same = (a, b) => a.length === b.length && a.every((x) => b.includes(x));
  const set = (name, label, on, count = 0) => {
    const { btn } = menuFor(name);
    fill(btn.querySelector(".dd-label"), label, count ? el("span", { class: "pill" }, String(count)) : null);
    btn.classList.toggle("on", on);
  };
  const fields = state.f.map((x) => FIELD[x]);
  set("f", state.f.length === Object.keys(FIELD).length ? "All fields" : fields.length <= 2 ? fields.join(", ") : `${fields[0]} and ${fields.length - 1} more`, !same(state.f, d.f));
  // New grad and "not in title" read as one group when both are on
  const lvNames = LEVEL_ORDER.filter((x) => state.l.includes(x) && !(x === "early" && state.l.includes("entry")))
    .map((x) => (x === "entry" && state.l.includes("early") ? "New grad" : LEVELS[x]));
  const lv = state.l.length === 5 ? "Any level" : lvNames.length <= 2 ? lvNames.join(", ") : `${lvNames[0]} and ${lvNames.length - 1} more`;
  set("l", lv, !same(state.l, d.l));
  set("loc", state.loc.length ? (state.loc.length === 1 ? placeName(state.loc[0]) : `${placeName(state.loc[0])} and ${state.loc.length - 1} more`) : "Anywhere in the US", Boolean(state.loc.length));
  const extra = (state.age !== d.age) + state.setup.length + Boolean(state.pay) + (state.filed !== d.filed) + state.strong + state.hideApplied + showHidden;
  set("more", "More filters", Boolean(extra), extra);
  $("sort-match").hidden = $("sort-match").disabled = !state.q;
  $("sort").value = state.sort;
  if ($("q").value !== state.q && document.activeElement !== $("q")) $("q").value = state.q;
  document.querySelectorAll(".dd-btn").forEach((b) => (b.disabled = view === "saved"));
  drawMenu();
}
function updateSavedCount() {
  const n = tracked().length;
  $("saved-count").textContent = n;
  $("saved-count").hidden = n === 0;
}

// ---- explainers and tooltips ---------------------------------------------------------------

function showPopover(btn) {
  const pop = $("popover");
  const [title, text] = EXPLAIN[btn.dataset.explain];
  if (!pop.hidden && pop.anchor === btn) { pop.hidden = true; return; }
  pop.replaceChildren(el("strong", {}, title), text);
  pop.anchor = btn;
  pop.hidden = false;
  const r = btn.getBoundingClientRect();
  const w = pop.offsetWidth, h = pop.offsetHeight;
  pop.style.left = `${Math.min(Math.max(8, r.left + r.width / 2 - w / 2), innerWidth - w - 8)}px`;
  pop.style.top = `${r.bottom + 8 + h > innerHeight - 8 ? r.top - h - 8 : r.bottom + 8}px`;
}
function showTip(seg, x, y) {
  const tip = $("tip");
  const [a, b, c] = JSON.parse(seg.dataset.tip);
  tip.replaceChildren(el("b", {}, b), `, ${c} at ${a}`);
  tip.hidden = false;
  const w = tip.offsetWidth;
  tip.style.left = `${Math.min(Math.max(8, x - w / 2), innerWidth - w - 8)}px`;
  tip.style.top = `${y - tip.offsetHeight - 12}px`;
}

// ---- wiring ---------------------------------------------------------------------------------

function wire() {
  document.querySelectorAll(".dd").forEach((box) => box.querySelector(".dd-btn").addEventListener("click", () => openMenu(box.dataset.dd)));
  document.addEventListener("click", (e) => {
    const info = e.target.closest(".info");
    if (info) { e.preventDefault(); showPopover(info); return; }
    if (!e.target.closest("#popover")) $("popover").hidden = true;
    // The path is fixed when the click starts, so a menu redrawn by the
    // click itself still counts as inside
    if (openDD && !e.composedPath().some((n) => n.classList?.contains("dd"))) closeMenu();
  });
  // A plain click on a row (or a "More at" link) previews the job on wide
  // screens; anything else follows the link to the job's page
  const preview = (e) => {
    const a = e.target.closest("a[data-k]");
    if (!a || !wide() || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
    e.preventDefault();
    select(a.dataset.k);
    if (a.classList.contains("row")) track("preview");
    else a.closest(".pane") && $("pane").scrollTo({ top: 0 });
  };
  $("jobs").addEventListener("click", preview);
  $("pane").addEventListener("click", preview);
  document.addEventListener("pointermove", (e) => {
    const seg = e.target.closest?.(".seg");
    if (seg && e.pointerType === "mouse") showTip(seg, e.clientX, e.clientY);
    else $("tip").hidden = true;
  });
  window.addEventListener("scroll", () => { $("tip").hidden = true; }, { passive: true });
  // j and k move through the list, o opens the job's page, / jumps to search
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { $("popover").hidden = true; if (openDD) { const b = menuFor(openDD).btn; closeMenu(); b.focus(); } return; }
    if (e.target.closest("input, textarea, select") || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "/") { e.preventDefault(); $("q").focus(); return; }
    if (!wide() || !visible.length) return;
    const i = visible.indexOf(currentJob);
    if (e.key === "j" || e.key === "k") {
      const next = visible[Math.min(visible.length - 1, Math.max(0, i + (e.key === "j" ? 1 : -1)))];
      select(next, { transition: false });
      document.querySelector(`.row[data-k="${CSS.escape(next)}"]`)?.scrollIntoView({ block: "nearest" });
    } else if (e.key === "o" && currentJob) location.href = pageOf(byKey(currentJob));
  });
  $("sort").addEventListener("change", (e) => { state.sort = e.target.value; rerender(); });
  let qTimer;
  $("q").addEventListener("input", (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => setQuery(e.target.value.trim()), 150); });
  $("more").addEventListener("click", () => { shown += PAGE; render(); });
  $("new-toggle").addEventListener("click", () => { onlyNew = !onlyNew; if (onlyNew) track("new-since-visit"); rerender(); });
  $("save-search").addEventListener("click", saveSearch);
  $("copy-link").addEventListener("click", async () => {
    track("share-search");
    writeURL({ withJob: false });
    const link = new URL(location.href);
    for (const k of CAMPAIGN.keys()) link.searchParams.delete(k);
    const clean = link.toString().replace(/%2C/g, ",").replace(/%3A/g, ":");
    writeURL();
    try { await navigator.clipboard.writeText(clean); toast("Link copied"); } catch (e) { toast("Copy the address bar to share this search"); }
  });
  $("nav-jobs").addEventListener("click", () => { view = "jobs"; rerender(); });
  $("nav-saved").addEventListener("click", () => { track("saved-view"); view = "saved"; onlyNew = false; closeMenu(); render(); });
  $("theme").addEventListener("click", () => {
    const dark = getComputedStyle(document.documentElement).colorScheme === "dark";
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("ff.theme", next); } catch (e) {} // read raw by the inline script in <head>
  });
  // The search band stays on screen under the header; the preview sits below
  // both, so it needs their heights (the header wraps to two lines on phones)
  const root = document.documentElement.style;
  new ResizeObserver(() => {
    root.setProperty("--topbar", `${document.querySelector(".topbar").offsetHeight}px`);
    root.setProperty("--band", `${document.querySelector(".searchband").offsetHeight}px`);
  }).observe(document.querySelector(".searchband"));
  // Crossing the wide breakpoint shows or hides the preview
  matchMedia("(min-width: 1000px)").addEventListener("change", () => render());
  $("defs").replaceChildren(...["filings", "new-hires", "wage-level", "median", "pay", "level", "no-filings"].flatMap((t) => [el("dt", {}, EXPLAIN[t][0]), el("dd", {}, EXPLAIN[t][1])]));
}

// Coming back from a job's page lands on the same job, at the same place in
// the list. The browser can't restore the scroll itself because the list is
// drawn after the jobs load, so the spot is kept for this tab and put back.
const SPOT = "ff.spot";
history.scrollRestoration = "manual";
function saveSpot() {
  try { sessionStorage.setItem(SPOT, JSON.stringify({ qs: location.search, job: currentJob, shown, y: scrollY, view })); } catch (e) {}
}
// The spot applies on Back, and on any plain visit to the board straight
// from a job's page (its "Back to jobs" link, the header's "Jobs")
function readSpot() {
  try {
    const s = JSON.parse(sessionStorage.getItem(SPOT) || "null");
    if (!s) return null;
    if (s.qs === location.search) return s;
    const from = document.referrer ? new URL(document.referrer) : null;
    const fromJob = from && from.origin === location.origin && from.pathname.startsWith("/job/");
    return fromJob && !location.search ? s : null;
  } catch (e) { return null; }
}
addEventListener("pagehide", saveSpot);

function rememberVisit() {
  // Jobs first seen after the previous visit are "new". A reload within the
  // same browser session keeps the same baseline, so badges don't vanish.
  try {
    const session = sessionStorage.getItem("ff.prev");
    const prev = session !== null ? session : localStorage.getItem("ff.lastVisit") || "";
    sessionStorage.setItem("ff.prev", prev);
    localStorage.setItem("ff.lastVisit", String(Date.now()));
    lastVisit = prev ? Number(prev) : null;
  } catch (e) { lastVisit = null; }
}

const getJSON = (f) => fetch(f, { cache: "no-cache" }).then((r) => { if (!r.ok) throw new Error(f); return r.json(); });
const withPlaces = (j) => ({ ...j, states: j.states || [], metros: j.metros || [] });
function setJobs(list) {
  jobs = list;
  byKeyMap = new Map(jobs.map((j) => [key(j), j]));
}
let older = null; // jobs posted 8 to 30 days ago, fetched the first time they're needed
function loadOlder() {
  older ??= getJSON("jobs-older.json")
    .then((d) => { setJobs(jobs.concat(d.jobs.map(withPlaces))); updateSavedCount(); render(); })
    .catch(() => {});
  return older;
}

async function load() {
  // Arriving from a job's page without filters in the address: put back the
  // filters the visitor had before opening it
  const back = readSpot();
  if (back && back.qs !== location.search) history.replaceState(null, "", back.qs || location.pathname);
  const { company: companyParam, job: jobParam } = readURL();
  wire();
  rememberVisit();
  $("jobs").replaceChildren(...Array.from({ length: 6 }, () => el("li", { class: "skeleton" })));
  try {
    const [data, comp, health] = await Promise.all([getJSON("jobs-recent.json"), getJSON("companies.json").catch(() => ({})), getJSON("health.json").catch(() => null)]);
    companies = comp;
    // Old links to a company's panel go to its page now
    if (companyParam && companies[companyParam]?.page) { location.replace(companies[companyParam].page); return; }
    setJobs(data.jobs.map(withPlaces));
    fill($("status"), el("i", { class: "dot", "aria-hidden": "true" }),
      el("span", {}, "Updated ", el("b", {}, ago(data.updated)), health ? [", ", el("b", {}, num(health.boards_ok)), " job boards checked"] : null));
    updateSavedCount();
    const spot = readSpot();
    if (spot) {
      shown = Math.max(PAGE, spot.shown || PAGE);
      if (spot.job && !byKey(spot.job)) await loadOlder();
      if (spot.job && byKey(spot.job)) currentJob = spot.job;
    }
    if (jobParam) {
      if (!byKey(jobParam)) await loadOlder();
      const j = byKey(jobParam);
      if (j && !wide()) { location.replace(pageOf(j)); return; }
      if (j) currentJob = jobParam;
    }
    render();
    if (spot) {
      if (spot.shown > PAGE && jobs.length <= PAGE) await loadOlder();
      // Back to the same place; if the list came out a different height, at
      // least bring the job that was open into view
      const settle = () => {
        scrollTo(0, spot.y || 0);
        const a = spot.job && document.querySelector(`.row[data-k="${CSS.escape(spot.job)}"]`);
        const r = a?.getBoundingClientRect();
        if (r && (r.top < 0 || r.bottom > innerHeight)) a.scrollIntoView({ block: "center" });
      };
      requestAnimationFrame(settle);
      document.fonts?.ready.then(settle);
    }
    if (location.search && !spot) track("opened-shared-link");
    if (lastVisit) track("returning-visit");
  } catch (e) {
    $("status").textContent = "Couldn't load jobs";
    $("jobs").replaceChildren(el("li", { class: "empty" }, el("strong", {}, "Couldn't load jobs"), "Refresh the page to try again."));
  }
}
load();
// Installable app and offline fallback (only on the real https site)
if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("sw.js").catch(() => {});
