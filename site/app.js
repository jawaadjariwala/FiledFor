// FiledFor front end. Reads jobs-recent.json (last 7 days) first, then
// jobs-older.json only when needed, plus companies.json and health.json; all
// filtering happens in the browser. No build step, no dependencies.
//
// State that matters for sharing lives in the URL (?f=ai&age=1&loc=m:Bay Area).
// Saved jobs, applied jobs and the last visit live in localStorage only.

"use strict";

const PAGE = 40;
const REPO = "https://github.com/jawaadjariwala/FiledFor";
const FIELD = { swe: "Software", ai: "AI/ML", data: "Data", hardware: "Hardware", it: "IT & Cloud", security: "Security", product: "Product", design: "Design" };
const DEFAULTS = { f: Object.keys(FIELD), l: ["entry", "early"], age: "7", loc: [], filed: true, hideApplied: false, q: "", sort: "new", pay: 0, setup: [], strong: false };
// A search gets its own filters, wide by default, so browsing filters never hide matches
const SEARCH_DEFAULTS = { f: Object.keys(FIELD), l: ["intern", "entry", "early", "mid", "senior"], age: "30", loc: [], filed: false, hideApplied: false, q: "", sort: "match", pay: 0, setup: [], strong: false };
const defaultsFor = (s) => (s.q ? SEARCH_DEFAULTS : DEFAULTS);
const EXPLAIN = {
  filings: ["H-1B filings", "Labor Condition Applications this company filed with the Department of Labor for this kind of role, certified between October 2024 and June 2026. Every H-1B petition starts with one, so more filings means a longer record of sponsoring this work."],
  "new-hires": ["New hires", "Filings for someone joining the company, as opposed to extending or transferring an existing visa. The closest sign the company hires people who need their first H-1B."],
  "wage-level": ["Wage level", "Each filing sets a prevailing wage level for the role and city, from I (entry) to IV (most experienced). Most new grads are filed at Level I or II. From FY2027 the lottery gives higher levels more entries, based on the wage actually offered."],
  median: ["Median wage", "The middle yearly salary on this company's filings for this kind of role."],
  pay: ["Pay", "The pay range the job posting itself states, made yearly when it's hourly. Many US states require it, but not every posting lists one, so the pay filter only shows jobs that do."],
  level: ["Level", "New grad: the title says new grad, junior, entry level or similar. Not in title: the title doesn't say a level, and the description asks for 2 years of experience or less, or doesn't say. Mid and Senior: from the title (II, Senior, Staff, Lead and so on), or from the years the description asks for (3 to 4 is mid, 5 or more is senior). Internship: interns and co-ops."],
  "no-filings": ["No filings found", "No certified H-1B filings for this kind of role under the names we matched for this company. It may file under a different legal name, so this isn't proof it doesn't sponsor."],
};

const $ = (id) => document.getElementById(id);
const store = {
  get(k, fallback) { try { const v = localStorage.getItem(k); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};

let jobs = [];
let companies = {};
let browse = structuredClone(DEFAULTS); // filters while browsing
let state = browse; // the filters in use: `browse`, or a search's own
let view = "jobs"; // or "saved"
let onlyNew = false;
let shown = PAGE;
const saved = new Set(store.get("ff.saved", []));
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
function remember(j) {
  snap[key(j)] = { t: j.title, c: j.company, u: j.url, l: j.location || "", p: j.posted_at || "" };
  store.set("ff.snap", snap);
}
function setStage(k, st) {
  delete stages[k];
  if (!st) { saved.delete(k); applied.delete(k); }
  else if (st === "saved") { saved.add(k); applied.delete(k); }
  else { saved.add(k); applied.add(k); if (st !== "applied") stages[k] = st; }
  const j = jobs.find((x) => key(x) === k);
  if (j) remember(j);
  store.set("ff.saved", [...saved]);
  store.set("ff.applied", [...applied]);
  store.set("ff.stage", stages);
  if (st) track(`stage-${st}`);
  updateSavedCount();
}
// Saved searches: filters or a query, with the time they were last opened
const searches = store.get("ff.searches", []);
const isHidden = (j) => hiddenJobs.has(key(j)) || hiddenCompanies.has(j.company);
const applied = new Set(store.get("ff.applied", []));
let lastVisit = null;

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
  star: '<path d="M12 3.5l2.6 5.3 5.9.9-4.2 4.1 1 5.8L12 16.9l-5.3 2.7 1-5.8-4.2-4.1 5.9-.9z"/>',
  check: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16.5 9.5"/>',
  flag: '<path d="M5 21V4m0 0h11l-2 4 2 4H5"/>',
  out: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
};
const key = (j) => `${j.system}|${j.slug}|${j.job_id}`;
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
const payText = (j) => (j.salary_min ? (j.salary_min === j.salary_max ? money(j.salary_min) : `${money(j.salary_min)}–${money(j.salary_max)}`) : null);
// Filing evidence lives once per company and field in companies.json
const evOf = (j) => companies[j.company]?.evidence?.[j.role] || null;
const money = (n) => (n ? `$${Math.round(n / 1000)}K` : "n/a");
const num = (n) => n.toLocaleString("en-US");
function ago(iso) {
  const mins = Math.max(0, (Date.now() - Date.parse(iso)) / 60000);
  if (mins < 60) return `${Math.max(1, Math.round(mins))} min ago`;
  const h = mins / 60;
  if (h < 24) return `${Math.round(h)}h ago`;
  const d = Math.round(h / 24);
  return d === 1 ? "1 day ago" : `${d} days ago`;
}
function hash(s) { let h = 0; for (const c of s) h = (h * 31 + c.charCodeAt(0)) | 0; return Math.abs(h); }
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
// them whenever it loads, but "Share search" copies a link without them.
const CAMPAIGN = new URLSearchParams(
  [...new URLSearchParams(location.search)].filter(([k]) => k === "ref" || k.startsWith("utm_")),
);

function writeURL(company = currentCompany) {
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
  if (company) p.set("company", company);
  if (currentJob) p.set("job", currentJob);
  for (const [k, v] of CAMPAIGN) p.set(k, v);
  const qs = p.toString().replace(/%2C/g, ",").replace(/%3A/g, ":");
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
}

// ---- filtering -------------------------------------------------------------------

function placeMatch(j, loc) {
  return loc.some((p) => (p === "remote" ? j.is_remote : p.startsWith("m:") ? j.metros.includes(p.slice(2)) : p.startsWith("s:") ? j.states.includes(p.slice(2)) : false));
}
function matches(j, { ignoreLoc = false } = {}) {
  if (view === "saved") return saved.has(key(j));
  if (!showHidden && isHidden(j)) return false;
  if (!state.f.includes(j.role) || !state.l.includes(bucket(j))) return false;
  if (j.posted_at && Date.now() - Date.parse(j.posted_at) > Number(state.age) * 864e5) return false;
  if (state.filed && !evOf(j)) return false;
  if (state.hideApplied && applied.has(key(j))) return false;
  if (state.pay && !(j.salary_max >= state.pay)) return false; // only jobs that list pay
  if (state.setup.length && !state.setup.includes(setupOf(j))) return false;
  if (state.strong && !(evOf(j)?.filings >= STRONG_SPONSOR)) return false;
  if (!ignoreLoc && state.loc.length && !placeMatch(j, state.loc)) return false;
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
  scores = state.q && view !== "saved" ? searchScores(state.q) : null;
  let out = jobs.filter((j) => matches(j));
  const newCount = out.filter(isNew).length;
  if (onlyNew && view !== "saved") out = out.filter(isNew);
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

// ---- evidence pieces ------------------------------------------------------------

function levelBar(ev, label) {
  const total = ev.level_1 + ev.level_2 + ev.level_3 + ev.level_4;
  const bar = el("div", { class: "bar", role: "img", "aria-label": `${label}: Level I ${ev.level_1}, II ${ev.level_2}, III ${ev.level_3}, IV ${ev.level_4} filings` });
  if (!total) return null;
  [["l1", "I", ev.level_1], ["l2", "II", ev.level_2], ["l3", "III", ev.level_3], ["l4", "IV", ev.level_4]].forEach(([cls, name, n]) => {
    if (!n) return;
    const pct = Math.round((n * 100) / total);
    const seg = el("span", { class: `seg ${cls}`, style: `flex:${n}` });
    seg.dataset.tip = JSON.stringify([`Level ${name}`, `${num(n)} filings`, `${pct}%`]);
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
function evidenceLine(j) {
  const ev = evOf(j);
  if (!ev) return el("div", { class: "ev none" }, `No H-1B filings found for ${FIELD[j.role]} roles`, infoBtn("no-filings"));
  const share = lowShare(ev);
  return el("div", { class: "ev" },
    el("div", { class: "stats" },
      el("span", { class: "stat" }, el("b", {}, num(ev.filings)), ` ${FIELD[j.role]} filings `, infoBtn("filings")),
      el("span", { class: "stat" }, el("b", {}, num(ev.new_hire_filings)), " new hires"),
      share !== null ? el("span", { class: "stat" }, el("b", {}, `${share}%`), " at Level I-II") : null,
      el("span", { class: "stat" }, el("b", {}, money(ev.median_wage)), " median"),
    ),
    levelBar(ev, `${j.company} ${FIELD[j.role]} filings by wage level`),
  );
}
function logo(name) {
  const c = companies[name];
  const box = el("div", { class: `logo m${hash(name) % 8}`, "aria-hidden": "true" }, initials(name));
  if (c?.logo) {
    const img = el("img", { src: c.logo, alt: "", loading: "lazy", width: 44, height: 44 });
    img.addEventListener("error", () => img.remove());
    img.addEventListener("load", () => { box.firstChild.nodeType === 3 && box.firstChild.remove(); });
    box.append(img);
  }
  return box;
}
// Anonymous counts of what people do (GoatCounter, no cookies). These are
// the only record of saves and applies, which otherwise live in one browser.
function track(name, title = "") {
  try { window.goatcounter?.count?.({ path: name, title, event: true }); } catch (e) {}
}

function reportURL(j) {
  const p = new URLSearchParams({
    template: "job-report.yml",
    title: `Report: ${j.title} at ${j.company}`,
    "job-url": j.url,
    company: j.company,
  });
  return `${REPO}/issues/new?${p}`;
}

// ---- job card --------------------------------------------------------------------

function card(j) {
  const k = key(j);
  const tags = el("div", { class: "tags" },
    isNew(j) ? el("span", { class: "tag new" }, "New") : null,
    el("span", { class: "tag" }, FIELD[j.role]),
    { entry: "New grad", intern: "Internship", mid: "Mid level", senior: "Senior" }[j.level] ? el("span", { class: "tag" }, { entry: "New grad", intern: "Internship", mid: "Mid level", senior: "Senior" }[j.level]) : null,
    j.min_years != null ? el("span", { class: "tag" }, j.min_years === 0 ? "No experience asked" : `Asks ${j.min_years}+ yr`) : null,
    setupOf(j) ? el("span", { class: "tag" }, SETUP_NAMES[setupOf(j)]) : null,
    payText(j) ? el("span", { class: "tag pay" }, payText(j)) : null,
    applied.has(k) ? el("span", { class: "tag done" }, "Applied") : null,
    companies[j.company]?.notice ? el("span", { class: "tag notice", title: companies[j.company].notice.detail }, companies[j.company].notice.notice) : null,
  );
  const meta = el("p", { class: "meta" },
    el("button", { type: "button", class: "company-btn", onclick: () => openCompany(j.company) }, j.company),
    el("span", { class: "sep" }, "·"), j.location || "Location not listed",
    j.posted_at ? [el("span", { class: "sep" }, "·"), `posted ${ago(j.posted_at)}`] : null,
  );
  const saveBtn = el("button", { type: "button", class: "iconbtn save", "aria-pressed": String(saved.has(k)), "aria-label": "Save job", title: "Save" }, svg(ICON.star));
  saveBtn.addEventListener("click", () => toggleSet(saved, "ff.saved", k, saveBtn, "Saved", "Removed from saved"));
  const appliedBtn = el("button", { type: "button", class: "iconbtn", "aria-pressed": String(applied.has(k)), "aria-label": "Mark as applied", title: "Mark as applied" }, svg(ICON.check));
  appliedBtn.addEventListener("click", () => { toggleSet(applied, "ff.applied", k, appliedBtn, "Marked as applied", "Unmarked"); render(); });
  return el("li", { class: `job${applied.has(k) ? " applied" : ""}` },
    logo(j.company),
    el("div", { class: "job-main" },
      el("h3", { class: "job-title" }, el("a", {
        href: j.url, target: "_blank", rel: "noopener",
        onclick: (e) => { if (e.metaKey || e.ctrlKey || e.shiftKey || e.button) return; e.preventDefault(); openJob(k); },
      }, j.title)),
      meta, tags, evidenceLine(j),
    ),
    el("div", { class: "actions" },
      el("div", { class: "icons" }, saveBtn, appliedBtn,
        el("a", { class: "iconbtn", href: reportURL(j), target: "_blank", rel: "noopener", "aria-label": "Report a problem with this job", title: "Report a problem", onclick: () => track("report", j.company) }, svg(ICON.flag))),
      el("a", { class: "btn primary apply", href: j.url, target: "_blank", rel: "noopener", onclick: () => track("apply", j.company) }, "Apply", svg(ICON.out)),
    ),
  );
}
function toggleSet(set, storeKey, k, btn, onMsg, offMsg) {
  const on = !set.has(k);
  if (on) track(set === saved ? "save" : "applied");
  on ? set.add(k) : set.delete(k);
  store.set(storeKey, [...set]);
  const tj = jobs.find((x) => key(x) === k);
  if (on && tj) remember(tj);
  btn.setAttribute("aria-pressed", String(on));
  toast(on ? onMsg : offMsg);
  updateSavedCount();
  if (view === "saved" && set === saved && !on) render();
}
function updateSavedCount() {
  const n = tracked().length;
  $("saved-count").textContent = n;
  $("saved-count").hidden = n === 0;
}

// ---- render ----------------------------------------------------------------------

function render() {
  if (view === "saved") return renderMine();
  const { out, newCount } = filtered();
  const list = out.slice(0, shown);
  const ol = $("jobs");
  if (!list.length) {
    ol.replaceChildren(el("li", { class: "empty" },
      view === "saved"
        ? [el("strong", {}, "No saved jobs yet"), "Tap the star on any job to keep it here. Saved jobs stay in this browser."]
        : [el("strong", {}, "No jobs match these filters"), "Try a longer time window, another location, or fewer filters."]));
  } else {
    const frag = document.createDocumentFragment();
    list.forEach((j) => frag.append(card(j)));
    ol.replaceChildren(frag);
  }
  const searching = Boolean(scores);
  $("count").replaceChildren(
    `${num(out.length)} ${searching ? (out.length === 1 ? "result" : "results") : out.length === 1 ? "job" : "jobs"}`,
    view === "saved" ? el("span", {}, " saved") : "",
    searching ? el("span", {}, ` for “${state.q}”`) : "",
    searching && loose && out.length ? el("span", { class: "loose" }, " · no exact matches, showing the closest") : "",
    searching ? el("button", { type: "button", class: "linkbtn clear-search", onclick: () => setQuery("") }, "Clear search") : "",
  );
  const nt = $("new-toggle");
  nt.hidden = view === "saved" || !newCount;
  nt.textContent = onlyNew ? `Showing ${newCount} new · show all` : `${newCount} new since your last visit`;
  nt.setAttribute("aria-pressed", String(onlyNew));
  $("filters-done").textContent = view === "saved" ? "Done" : `Show ${num(out.length)} ${out.length === 1 ? "job" : "jobs"}`;
  $("more").hidden = out.length <= shown;
  $("more").textContent = `Show ${Math.min(PAGE, out.length - shown)} more`;
  $("nav-jobs").setAttribute("aria-current", view === "jobs" ? "page" : "false");
  $("nav-saved").setAttribute("aria-current", view === "saved" ? "page" : "false");
  $("legend").hidden = !list.some(evOf);
  if (state.age === "30" || view === "saved" || state.q) loadOlder();
  syncControls();
  renderPlaces();
  writeURL();
}
function rerender() { shown = PAGE; render(); }

// ---- My jobs: tracker and saved searches --------------------------------------------

function trackerRow(k, j) {
  const sel = el("select", { class: "select stage", "aria-label": "Status" },
    ...STAGES.map((st) => el("option", { value: st, selected: stageOf(k) === st }, STAGE_NAMES[st])));
  sel.addEventListener("change", () => { setStage(k, sel.value); renderMine(); });
  const note = el("textarea", { class: "note", rows: 2, placeholder: "Notes: referral, recruiter, interview dates…", "aria-label": "Notes" });
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
function closedCard(k) {
  const s0 = snap[k] || { t: "A job you saved", c: "", u: "#", l: "" };
  return el("li", { class: "job closed" },
    logo(s0.c || "?"),
    el("div", { class: "job-main" },
      el("h3", { class: "job-title" }, el("a", { href: s0.u, target: "_blank", rel: "noopener" }, s0.t)),
      el("p", { class: "meta" }, s0.c, el("span", { class: "sep" }, "·"), s0.l || "Location not listed"),
      el("div", { class: "tags" }, el("span", { class: "tag" }, "No longer listed on FiledFor")),
      trackerRow(k, null)));
}
function describe(st) {
  if (st.q) return `“${st.q}”`;
  const parts = [];
  if (st.f.length < Object.keys(FIELD).length) parts.push(st.f.map((x) => FIELD[x]).join(", "));
  const lv = { intern: "Internship", entry: "New grad", early: "Not in title", mid: "Mid", senior: "Senior" };
  parts.push(st.l.map((x) => lv[x]).join(", "));
  if (st.loc.length) parts.push(st.loc.map(placeName).join(", "));
  if (st.setup.length) parts.push(st.setup.map((x) => SETUP_NAMES[x]).join(", "));
  if (st.pay) parts.push(`${money(st.pay)}+`);
  return parts.join(" · ");
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
  window.scrollTo({ top: $("results").offsetTop - 70, behavior: "smooth" });
}
function exportCSV() {
  const rows = [["Title", "Company", "Location", "Status", "Note", "Link", "Posted"]];
  for (const k of tracked()) {
    const j = jobs.find((x) => key(x) === k);
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
  const byKey = new Map(jobs.map((j) => [key(j), j]));
  const keys = tracked().filter((k) => mineFilter === "all" || stageOf(k) === mineFilter);
  keys.sort((a, b) => STAGES.indexOf(stageOf(a)) - STAGES.indexOf(stageOf(b)));
  const ol = $("jobs");
  const head = el("li", { class: "mine-head" },
    searches.length ? el("section", { class: "searches" }, el("h3", {}, "Saved searches"),
      el("ul", {}, ...searches.map((s0) => {
        const n = newFor(s0);
        return el("li", {},
          el("button", { type: "button", class: "linkbtn search-name", onclick: () => openSearch(s0) }, s0.name),
          n ? el("span", { class: "pill" }, `${n} new`) : el("span", { class: "muted" }, "nothing new"),
          el("button", { type: "button", class: "linkbtn muted", "aria-label": `Remove saved search ${s0.name}`, onclick: () => { searches.splice(searches.indexOf(s0), 1); store.set("ff.searches", searches); renderMine(); } }, "Remove"));
      }))) : null,
    el("div", { class: "mine-bar" },
      el("div", { class: "chips" }, ...["all", ...STAGES].map((st) => el("button", {
        type: "button", class: "chip", "aria-pressed": String(mineFilter === st),
        onclick: () => { mineFilter = st; renderMine(); },
      }, st === "all" ? `All (${tracked().length})` : `${STAGE_NAMES[st]} (${tracked().filter((k) => stageOf(k) === st).length})`))),
      tracked().length ? el("button", { type: "button", class: "btn ghost", onclick: exportCSV }, "Export CSV") : null));
  const items = keys.map((k) => {
    const j = byKey.get(k);
    if (!j) return closedCard(k);
    remember(j);
    const c = card(j);
    c.querySelector(".job-main").append(trackerRow(k, j));
    return c;
  });
  ol.replaceChildren(head, ...(items.length ? items : [el("li", { class: "empty" },
    el("strong", {}, tracked().length ? "Nothing in this status" : "Nothing here yet"),
    "Save a job with the star, or open a job to track it. Everything stays in this browser.")]));
  $("count").replaceChildren(`${num(tracked().length)} tracked ${tracked().length === 1 ? "job" : "jobs"}`);
  $("new-toggle").hidden = true;
  $("more").hidden = true;
  $("legend").hidden = true;
  $("nav-jobs").setAttribute("aria-current", "false");
  $("nav-saved").setAttribute("aria-current", "page");
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
  clearTimeout(searchTracked);
  if (q) searchTracked = setTimeout(() => track("search", q.slice(0, 60)), 1500);
  rerender();
}

function syncControls() {
  document.querySelectorAll(".filters .chips[data-key]").forEach((g) => g.querySelectorAll(".chip").forEach((b) => b.setAttribute("aria-pressed", String(state[g.dataset.key].includes(b.dataset.value)))));
  document.querySelectorAll(".segmented button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.value === state.age)));
  $("filed").checked = state.filed;
  $("hide-applied").checked = state.hideApplied;
  $("strong").checked = state.strong;
  const nHidden = hiddenJobs.size + hiddenCompanies.size;
  $("show-hidden-row").hidden = !nHidden;
  $("show-hidden-label").textContent = `Show hidden (${hiddenJobs.size} ${hiddenJobs.size === 1 ? "job" : "jobs"}, ${hiddenCompanies.size} ${hiddenCompanies.size === 1 ? "company" : "companies"})`;
  $("show-hidden").checked = showHidden;
  $("pay").value = String(state.pay);
  $("sort-match").hidden = $("sort-match").disabled = !state.q;
  $("sort").value = state.sort;
  if ($("q").value !== state.q && document.activeElement !== $("q")) $("q").value = state.q;
  $("filters-title").textContent = state.q ? "Search filters" : "Filters";
  const d = defaultsFor(state);
  const changed = ["f", "l"].filter((k) => state[k].join() !== d[k].join()).length + (state.age !== d.age) + state.loc.length + (state.filed !== d.filed) + state.hideApplied + Boolean(state.pay) + state.setup.length + state.strong;
  $("filter-count").textContent = changed;
  $("filter-count").hidden = !changed;
  document.querySelector(".filters").inert = view === "saved" ? true : false;
}

// ---- location list ------------------------------------------------------------------

const STATE_NAMES = { AL: "Alabama", AK: "Alaska", AZ: "Arizona", AR: "Arkansas", CA: "California", CO: "Colorado", CT: "Connecticut", DE: "Delaware", DC: "District of Columbia", FL: "Florida", GA: "Georgia", HI: "Hawaii", ID: "Idaho", IL: "Illinois", IN: "Indiana", IA: "Iowa", KS: "Kansas", KY: "Kentucky", LA: "Louisiana", ME: "Maine", MD: "Maryland", MA: "Massachusetts", MI: "Michigan", MN: "Minnesota", MS: "Mississippi", MO: "Missouri", MT: "Montana", NE: "Nebraska", NV: "Nevada", NH: "New Hampshire", NJ: "New Jersey", NM: "New Mexico", NY: "New York", NC: "North Carolina", ND: "North Dakota", OH: "Ohio", OK: "Oklahoma", OR: "Oregon", PA: "Pennsylvania", RI: "Rhode Island", SC: "South Carolina", SD: "South Dakota", TN: "Tennessee", TX: "Texas", UT: "Utah", VT: "Vermont", VA: "Virginia", WA: "Washington", WV: "West Virginia", WI: "Wisconsin", WY: "Wyoming", PR: "Puerto Rico" };
const placeName = (p) => (p === "remote" ? "Remote" : p.startsWith("m:") ? p.slice(2) : STATE_NAMES[p.slice(2)] || p.slice(2));

function renderPlaces() {
  // Counts reflect every other filter, so the numbers match what you'd get
  const pool = view === "saved" ? [] : jobs.filter((j) => matches(j, { ignoreLoc: true }));
  const counts = new Map();
  const bump = (p) => counts.set(p, (counts.get(p) || 0) + 1);
  pool.forEach((j) => { if (j.is_remote) bump("remote"); j.metros.forEach((m) => bump(`m:${m}`)); j.states.forEach((s) => bump(`s:${s}`)); });
  const term = $("place-search").value.trim().toLowerCase();
  const pick = (prefix) => [...counts].filter(([p]) => p.startsWith(prefix)).sort((a, b) => b[1] - a[1]);
  const groups = [["", [["remote", counts.get("remote") || 0]]], ["Metro areas", pick("m:")], ["States", pick("s:")]];
  const box = $("placelist");
  box.replaceChildren();
  let any = false;
  for (const [title, items] of groups) {
    const rows = items.filter(([p]) => !term || placeName(p).toLowerCase().includes(term) || p.slice(2).toLowerCase() === term);
    if (!rows.length) continue;
    any = true;
    if (title) box.append(el("h4", {}, title));
    rows.forEach(([p, n]) => {
      const on = state.loc.includes(p);
      box.append(el("button", { type: "button", class: "place", "aria-pressed": String(on), onclick: () => togglePlace(p) },
        el("input", { type: "checkbox", tabindex: "-1", "aria-hidden": "true", checked: on }), placeName(p), el("span", { class: "n" }, num(n))));
    });
  }
  if (!any) box.append(el("p", { class: "place-empty" }, "No matching locations"));
  $("selected-places").replaceChildren(...state.loc.map((p) =>
    el("button", { type: "button", class: "tagx", "aria-label": `Remove ${placeName(p)}`, onclick: () => togglePlace(p) }, placeName(p), svg(ICON.x))));
}
function togglePlace(p) {
  state.loc = state.loc.includes(p) ? state.loc.filter((x) => x !== p) : [...state.loc, p];
  rerender();
}

// ---- company panel --------------------------------------------------------------------

// ---- job detail --------------------------------------------------------------------

let currentJob = null;
function fact(label, value) {
  return value ? el("div", { class: "fact" }, el("dt", {}, label), el("dd", {}, value)) : null;
}
function openJob(k) {
  const j = jobs.find((x) => key(x) === k);
  if (!j) return;
  currentJob = k;
  track("job", j.company);
  const ev = evOf(j);
  const c = companies[j.company] || {};
  const more = jobs.filter((x) => x.company === j.company && key(x) !== k).slice(0, 5);
  const level = { intern: "Internship", entry: "New grad", mid: "Mid level", senior: "Senior" }[j.level] || "Not stated in the title";
  const years = j.min_years == null ? null : j.min_years === 0 ? "No experience asked" : `${j.min_years}+ years asked`;
  const hideJob = el("button", { type: "button", class: "btn ghost", onclick: () => hide("job", j) }, "Hide this job");
  const hideCo = el("button", { type: "button", class: "btn ghost", onclick: () => hide("company", j) }, `Hide all ${j.company} jobs`);
  fill($("job-body"),
    el("div", { class: "panel-head" },
      logo(j.company),
      el("div", { class: "head-text" },
        el("h2", { id: "job-title" }, j.title),
        el("button", { type: "button", class: "company-btn", onclick: () => { $("jobpanel").close(); openCompany(j.company); } }, j.company)),
      el("button", { type: "button", class: "closebtn", "aria-label": "Close", onclick: closeJob }, svg(ICON.x))),
    el("div", { class: "job-actions" },
      el("a", { class: "btn primary", href: j.url, target: "_blank", rel: "noopener", onclick: () => track("apply", j.company) }, "Apply on company site", svg(ICON.out)),
      (() => {
        const b = el("button", { type: "button", class: "btn", "aria-pressed": String(saved.has(k)) }, saved.has(k) ? "Saved" : "Save");
        b.addEventListener("click", () => { toggleSet(saved, "ff.saved", k, b, "Saved", "Removed from saved"); b.textContent = saved.has(k) ? "Saved" : "Save"; render(); });
        return b;
      })(),
      (() => {
        const b = el("button", { type: "button", class: "btn", "aria-pressed": String(applied.has(k)) }, applied.has(k) ? "Applied" : "Mark applied");
        b.addEventListener("click", () => { toggleSet(applied, "ff.applied", k, b, "Marked as applied", "Unmarked"); b.textContent = applied.has(k) ? "Applied" : "Mark applied"; render(); });
        return b;
      })()),
    c.notice ? el("div", { class: "notice-box", role: "note" }, el("strong", {}, c.notice.notice), ` (${c.notice.date}). ${c.notice.detail} `, el("a", { href: c.notice.source, target: "_blank", rel: "noopener" }, "Source")) : null,
    el("dl", { class: "facts" },
      fact("Location", j.location || "Not listed"),
      fact("Work setup", SETUP_NAMES[setupOf(j)] || "Not stated"),
      fact("Pay", payText(j) ? `${payText(j)} a year (from the posting)` : "Not listed in the posting"),
      fact("Level", level),
      fact("Experience", years),
      fact("Field", FIELD[j.role]),
      fact("Posted", j.posted_at ? ago(j.posted_at) : null),
      fact("Last checked", j.checked_at ? `${ago(j.checked_at)}, still open` : null)),
    el("h4", {}, "Sponsor record"),
    el("div", { class: "field-card" },
      evidenceLine(j),
      el("button", { type: "button", class: "linkbtn", onclick: () => { $("jobpanel").close(); openCompany(j.company); } }, `See ${j.company}'s full filing record`)),
    more.length ? el("h4", {}, `More at ${j.company}`) : null,
    more.length ? el("ul", { class: "roles" }, ...more.map((x) => el("li", {}, el("a", { href: "#", onclick: (e) => { e.preventDefault(); openJob(key(x)); } }, x.title, el("span", { class: "sub" }, `${x.location || "Location not listed"}${x.posted_at ? ` · posted ${ago(x.posted_at)}` : ""}`))))) : null,
    el("div", { class: "panel-links" }, hideJob, hideCo,
      el("a", { class: "btn ghost", href: reportURL(j), target: "_blank", rel: "noopener", onclick: () => track("report", j.company) }, svg(ICON.flag), "Report a problem")),
    el("p", { class: "fineprint" }, "Filing numbers show what the company did before, not a promise for this job. Source: U.S. Department of Labor LCA disclosure data."),
  );
  const d = $("jobpanel");
  if (!d.open) d.showModal();
  d.scrollTop = 0;
  writeURL();
}
function closeJob() {
  currentJob = null;
  if ($("jobpanel").open) $("jobpanel").close();
  writeURL();
}
function hide(what, j) {
  const set = what === "job" ? hiddenJobs : hiddenCompanies;
  const value = what === "job" ? key(j) : j.company;
  set.add(value);
  store.set(what === "job" ? "ff.hiddenJobs" : "ff.hiddenCompanies", [...set]);
  track(what === "job" ? "hide-job" : "hide-company");
  closeJob();
  render();
  toastUndo(what === "job" ? "Job hidden" : `${j.company} hidden`, () => {
    set.delete(value);
    store.set(what === "job" ? "ff.hiddenJobs" : "ff.hiddenCompanies", [...set]);
    render();
  });
}
function toastUndo(msg, undo) {
  const t = $("toast");
  t.replaceChildren(msg, " ", el("button", { type: "button", class: "linkbtn toast-undo", onclick: () => { undo(); t.hidden = true; } }, "Undo"));
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 5000);
}

let currentCompany = null;
function openCompany(name) {
  const c = companies[name] || {};
  if (currentCompany !== name) track("company", name);
  currentCompany = name;
  const open = jobs.filter((j) => j.company === name).sort((a, b) => Date.parse(b.posted_at || 0) - Date.parse(a.posted_at || 0));
  const evs = Object.entries(c.evidence || {});
  const total = c.tech_filings || 0;
  const anyField = evs.some(([, e]) => e);
  const body = $("panel-body");
  const fieldCard = ([role, ev]) => {
    if (!ev) return el("section", { class: "field-card" }, el("h3", {}, FIELD[role]), el("p", { class: "none" }, `No H-1B filings found for ${FIELD[role]} roles.`));
    const share = lowShare(ev);
    const tile = (label, value, topic) => el("div", { class: "tile" }, el("div", { class: "label" }, label, topic ? infoBtn(topic) : null), el("div", { class: "value" }, value));
    return el("section", { class: "field-card" },
      el("h3", {}, FIELD[role]),
      el("div", { class: "tiles" },
        tile("Filings", num(ev.filings), "filings"),
        tile("New hires", num(ev.new_hire_filings), "new-hires"),
        tile("Level I-II", share === null ? "n/a" : `${share}%`, "wage-level"),
        tile("Median wage", money(ev.median_wage), "median")),
      levelBar(ev, `${name} ${FIELD[role]} filings by wage level`),
      el("table", { class: "levels" },
        el("tr", {}, el("th", {}, "Level"), ...["I", "II", "III", "IV"].map((x, i) => el("th", {}, el("i", { class: `sw l${i + 1}` }), x))),
        el("tr", {}, el("th", {}, "Filings"), ...[ev.level_1, ev.level_2, ev.level_3, ev.level_4].map((n) => el("td", {}, num(n))))),
    );
  };
  fill(body,
    el("div", { class: "panel-head" },
      logo(name),
      el("h2", { id: "panel-title" }, name),
      el("button", { type: "button", class: "closebtn", "aria-label": "Close", onclick: closeCompany }, svg(ICON.x))),
    el("p", { class: "panel-summary" }, total || anyField
      ? [`Filed `, el("b", {}, num(total)), ` H-1B applications for tech roles between October 2024 and June 2026. `,
        `Below, the same filings by field. One filing can count in more than one field, since software developer filings cover AI and data work too.`]
      : "No H-1B filings found for software, AI/ML or data roles under the names we matched. It may file under a different legal name."),
    c.notice
      ? el("div", { class: "notice-box", role: "note" },
          el("strong", {}, c.notice.notice), ` (${c.notice.date}). ${c.notice.detail} `,
          el("a", { href: c.notice.source, target: "_blank", rel: "noopener" }, "Source"))
      : null,
    ...evs.filter(([, e]) => e).map(fieldCard),
    el("h4", {}, `Open roles on FiledFor (${open.length})`),
    open.length
      ? el("ul", { class: "roles" }, ...open.slice(0, 25).map((j) => el("li", {}, el("a", { href: j.url, target: "_blank", rel: "noopener" }, j.title, el("span", { class: "sub" }, `${j.location || "Location not listed"}${j.posted_at ? ` · posted ${ago(j.posted_at)}` : ""}`)))))
      : el("p", { class: "none" }, "None right now."),
    el("div", { class: "panel-links" },
      ...(c.careers || []).slice(0, 3).map((u, i) => el("a", { class: "btn", href: u, target: "_blank", rel: "noopener" }, i ? "Another careers page" : "All jobs at this company", svg(ICON.out))),
      el("a", { class: "btn ghost", href: `${REPO}/issues/new?${new URLSearchParams({ template: "job-report.yml", title: `Wrong company match: ${name}`, company: name })}`, target: "_blank", rel: "noopener" }, svg(ICON.flag), "Wrong company?")),
    el("p", { class: "fineprint" }, "Filings show what a company did before, not a promise for any one job. Source: U.S. Department of Labor LCA disclosure data."),
  );
  const d = $("panel");
  if (!d.open) d.showModal();
  d.scrollTop = 0;
  writeURL(name);
}
function closeCompany() {
  currentCompany = null;
  if ($("panel").open) $("panel").close();
  writeURL(null);
}

// ---- explainers and tooltips ---------------------------------------------------------------

function showPopover(btn) {
  const pop = $("popover");
  const [title, text] = EXPLAIN[btn.dataset.explain];
  if (!pop.hidden && pop.dataset.for === btn.dataset.explain && pop.anchor === btn) { pop.hidden = true; return; }
  pop.replaceChildren(el("strong", {}, title), text);
  pop.dataset.for = btn.dataset.explain;
  pop.anchor = btn;
  pop.hidden = false;
  // Inside the open panel the popover must live in the dialog's top layer
  (btn.closest("dialog") || document.body).append(pop);
  const r = btn.getBoundingClientRect();
  const w = pop.offsetWidth, h = pop.offsetHeight;
  let left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), innerWidth - w - 8);
  let top = r.bottom + 8;
  if (top + h > innerHeight - 8) top = r.top - h - 8;
  pop.style.left = `${left}px`;
  pop.style.top = `${top}px`;
}
document.addEventListener("click", (e) => {
  const info = e.target.closest(".info");
  if (info) { e.preventDefault(); showPopover(info); return; }
  if (!e.target.closest("#popover")) $("popover").hidden = true;
});
function showTip(seg, x, y) {
  const tip = $("tip");
  const [a, b, c] = JSON.parse(seg.dataset.tip);
  tip.replaceChildren(el("b", {}, b), ` · ${c} · ${a}`);
  tip.hidden = false;
  (seg.closest("dialog") || document.body).append(tip);
  const w = tip.offsetWidth;
  tip.style.left = `${Math.min(Math.max(8, x - w / 2), innerWidth - w - 8)}px`;
  tip.style.top = `${y - tip.offsetHeight - 12}px`;
}
document.addEventListener("pointermove", (e) => {
  const seg = e.target.closest?.(".seg");
  if (seg && e.pointerType === "mouse") showTip(seg, e.clientX, e.clientY);
  else $("tip").hidden = true;
});
document.addEventListener("pointerdown", (e) => {
  const seg = e.target.closest?.(".seg");
  if (seg && e.pointerType !== "mouse") { const r = seg.getBoundingClientRect(); showTip(seg, r.left + r.width / 2, r.top); }
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("popover").hidden = true; });
window.addEventListener("scroll", () => { $("tip").hidden = true; }, { passive: true });

// ---- wiring ---------------------------------------------------------------------------------

function wire() {
  document.querySelectorAll(".filters .chips[data-key]").forEach((g) => g.addEventListener("click", (e) => {
    const b = e.target.closest(".chip");
    if (!b) return;
    const k = g.dataset.key, v = b.dataset.value;
    const cur = state[k];
    // Never let a group go empty: tapping the last selected chip selects it alone
    // Field and level never go empty (tapping the last one keeps it); work setup can
    state[k] = cur.includes(v) ? (cur.length > 1 || k === "setup" ? cur.filter((x) => x !== v) : cur) : [...cur, v];
    rerender();
  }));
  document.querySelector(".segmented").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) { state.age = b.dataset.value; rerender(); }
  });
  $("filed").addEventListener("change", (e) => { state.filed = e.target.checked; rerender(); });
  $("hide-applied").addEventListener("change", (e) => { state.hideApplied = e.target.checked; rerender(); });
  $("strong").addEventListener("change", (e) => { state.strong = e.target.checked; rerender(); });
  $("show-hidden").addEventListener("change", (e) => { showHidden = e.target.checked; rerender(); });
  $("jobpanel").addEventListener("close", () => { currentJob = null; writeURL(); $("popover").hidden = true; });
  $("jobpanel").addEventListener("click", (e) => { if (e.target === $("jobpanel")) closeJob(); });
  $("pay").addEventListener("change", (e) => { state.pay = Number(e.target.value) || 0; rerender(); });
  $("sort").addEventListener("change", (e) => { state.sort = e.target.value; rerender(); });
  let qTimer;
  $("q").addEventListener("input", (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => setQuery(e.target.value.trim()), 150); });
  $("place-search").addEventListener("input", renderPlaces);
  $("reset").addEventListener("click", () => {
    state = state.q ? { ...structuredClone(SEARCH_DEFAULTS), q: state.q } : (browse = structuredClone(DEFAULTS));
    onlyNew = false;
    $("place-search").value = "";
    rerender();
  });
  $("more").addEventListener("click", () => { shown += PAGE; render(); });
  $("new-toggle").addEventListener("click", () => { onlyNew = !onlyNew; if (onlyNew) track("new-since-visit"); rerender(); });
  $("save-search").addEventListener("click", saveSearch);
  $("copy-link").addEventListener("click", async () => {
    track("share-search");
    writeURL(null);
    const link = new URL(location.href);
    for (const k of CAMPAIGN.keys()) link.searchParams.delete(k);
    const clean = link.toString().replace(/%2C/g, ",").replace(/%3A/g, ":");
    try { await navigator.clipboard.writeText(clean); toast("Link copied"); } catch (e) { toast("Copy the address bar to share this search"); }
  });
  $("nav-jobs").addEventListener("click", () => { view = "jobs"; rerender(); });
  $("nav-saved").addEventListener("click", () => { track("saved-view"); view = "saved"; onlyNew = false; rerender(); window.scrollTo({ top: $("results").offsetTop - 70, behavior: "smooth" }); });
  $("filters-open").addEventListener("click", () => document.body.classList.add("filters-open"));
  const closeFilters = () => document.body.classList.remove("filters-open");
  $("filters-close").addEventListener("click", closeFilters);
  $("filters-done").addEventListener("click", () => { closeFilters(); $("results").focus(); });
  $("panel").addEventListener("close", () => { currentCompany = null; writeURL(null); $("popover").hidden = true; });
  $("panel").addEventListener("click", (e) => { if (e.target === $("panel")) closeCompany(); });
  $("theme").addEventListener("click", () => {
    const dark = getComputedStyle(document.documentElement).colorScheme === "dark";
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("ff.theme", next); } catch (e) {} // read raw by the inline script in <head>
  });
  $("defs").replaceChildren(...["filings", "new-hires", "wage-level", "median", "pay", "level", "no-filings"].flatMap((t) => [el("dt", {}, EXPLAIN[t][0]), el("dd", {}, EXPLAIN[t][1])]));
}

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
let older = null; // jobs posted 8 to 30 days ago, fetched the first time they're needed
function loadOlder() {
  older ??= getJSON("jobs-older.json")
    .then((d) => { jobs = jobs.concat(d.jobs.map(withPlaces)); updateSavedCount(); render(); })
    .catch(() => {});
  return older;
}

async function load() {
  const { company: companyParam, job: jobParam } = readURL();
  wire();
  rememberVisit();
  $("jobs").replaceChildren(...Array.from({ length: 4 }, () => el("li", { class: "skeleton" })));
  try {
    const [data, comp, health] = await Promise.all([getJSON("jobs-recent.json"), getJSON("companies.json").catch(() => ({})), getJSON("health.json").catch(() => null)]);
    jobs = data.jobs.map(withPlaces);
    companies = comp;
    $("status").replaceChildren(
      el("span", {}, el("i", { class: "dot" }), "Updated ", el("b", {}, ago(data.updated))),
      el("span", {}, el("b", {}, num(data.total ?? jobs.length)), " open jobs at ", el("b", {}, num(data.companies ?? 0)), " companies"),
      health ? el("span", {}, el("b", {}, num(health.boards_ok)), " job boards checked") : "",
    );
    updateSavedCount();
    render();
    if (location.search) track("opened-shared-link");
    if (lastVisit) track("returning-visit");
    if (companyParam && companies[companyParam]) openCompany(companyParam);
    else if (jobParam) {
      if (!jobs.some((j) => key(j) === jobParam)) await loadOlder();
      openJob(jobParam);
    }
  } catch (e) {
    $("status").textContent = "Couldn't load jobs. Refresh to try again.";
    $("jobs").replaceChildren(el("li", { class: "empty" }, el("strong", {}, "Couldn't load jobs"), "Refresh the page to try again."));
  }
}
load();
// Installable app and offline fallback (only on the real https site)
if ("serviceWorker" in navigator && location.protocol === "https:") navigator.serviceWorker.register("sw.js").catch(() => {});
