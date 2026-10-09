// FiledFor front end. Reads jobs.json, companies.json and health.json; all
// filtering happens in the browser. No build step, no dependencies.
//
// State that matters for sharing lives in the URL (?f=ai&age=1&loc=m:Bay Area).
// Saved jobs, applied jobs and the last visit live in localStorage only.

"use strict";

const PAGE = 40;
const REPO = "https://github.com/jawaadjariwala/FiledFor";
const FIELD = { swe: "Software", ai: "AI/ML", data: "Data" };
const DEFAULTS = { f: ["swe", "ai", "data"], l: ["entry", "unclear"], age: "7", loc: [], filed: true, hideApplied: false, q: "", sort: "new" };
const EXPLAIN = {
  filings: ["H-1B filings", "Labor Condition Applications this company filed with the Department of Labor for this kind of role, certified between October 2024 and June 2026. Every H-1B petition starts with one, so more filings means a longer record of sponsoring this work."],
  "new-hires": ["New hires", "Filings for someone joining the company, as opposed to extending or transferring an existing visa. The closest sign the company hires people who need their first H-1B."],
  "wage-level": ["Wage level", "Each filing sets a prevailing wage level for the role and city, from I (entry) to IV (most experienced). Most new grads are filed at Level I or II. From FY2027 the lottery gives higher levels more entries, based on the wage actually offered."],
  median: ["Median wage", "The middle yearly salary on this company's filings for this kind of role."],
  level: ["Level", "New grad: the title says new grad, junior, entry level or similar. Not in title: the title doesn't say, and the description asks for 2 years of experience or less, or doesn't say. Internship: interns and co-ops."],
  "no-filings": ["No filings found", "No certified H-1B filings for this kind of role under the names we matched for this company. It may file under a different legal name, so this isn't proof it doesn't sponsor."],
};

const $ = (id) => document.getElementById(id);
const store = {
  get(k, fallback) { try { const v = localStorage.getItem(k); return v === null ? fallback : JSON.parse(v); } catch (e) { return fallback; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
};

let jobs = [];
let companies = {};
let state = structuredClone(DEFAULTS);
let view = "jobs"; // or "saved"
let onlyNew = false;
let shown = PAGE;
const saved = new Set(store.get("ff.saved", []));
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

function readURL() {
  const p = new URLSearchParams(location.search);
  const list = (k) => (p.has(k) ? p.get(k).split(",").filter(Boolean) : null);
  state = structuredClone(DEFAULTS);
  state.f = list("f") ?? state.f;
  state.l = list("l") ?? state.l;
  state.loc = list("loc") ?? [];
  if (["1", "3", "7", "30"].includes(p.get("age"))) state.age = p.get("age");
  if (p.get("all") === "1") state.filed = false;
  if (p.get("hideapplied") === "1") state.hideApplied = true;
  state.q = p.get("q") || "";
  if (["new", "filings", "wage"].includes(p.get("sort"))) state.sort = p.get("sort");
  view = p.get("view") === "saved" ? "saved" : "jobs";
  return p.get("company");
}
function writeURL(company = currentCompany) {
  const p = new URLSearchParams();
  const same = (a, b) => a.length === b.length && a.every((x) => b.includes(x));
  if (!same(state.f, DEFAULTS.f)) p.set("f", state.f.join(","));
  if (!same(state.l, DEFAULTS.l)) p.set("l", state.l.join(","));
  if (state.age !== DEFAULTS.age) p.set("age", state.age);
  if (state.loc.length) p.set("loc", state.loc.join(","));
  if (!state.filed) p.set("all", "1");
  if (state.hideApplied) p.set("hideapplied", "1");
  if (state.q) p.set("q", state.q);
  if (state.sort !== "new") p.set("sort", state.sort);
  if (view === "saved") p.set("view", "saved");
  if (company) p.set("company", company);
  const qs = p.toString().replace(/%2C/g, ",").replace(/%3A/g, ":");
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
}

// ---- filtering -------------------------------------------------------------------

function placeMatch(j, loc) {
  return loc.some((p) => (p === "remote" ? j.is_remote : p.startsWith("m:") ? j.metros.includes(p.slice(2)) : p.startsWith("s:") ? j.states.includes(p.slice(2)) : false));
}
function matches(j, { ignoreLoc = false } = {}) {
  if (view === "saved") return saved.has(key(j));
  if (!state.f.includes(j.role) || !state.l.includes(j.level)) return false;
  if (j.posted_at && Date.now() - Date.parse(j.posted_at) > Number(state.age) * 864e5) return false;
  if (state.filed && !j.evidence) return false;
  if (state.hideApplied && applied.has(key(j))) return false;
  if (!ignoreLoc && state.loc.length && !placeMatch(j, state.loc)) return false;
  if (state.q) {
    const hay = `${j.title} ${j.company} ${j.location || ""}`.toLowerCase();
    if (!state.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
  }
  return true;
}
const isNew = (j) => lastVisit && j.first_seen_at && Date.parse(j.first_seen_at) > lastVisit;
function filtered() {
  let out = jobs.filter((j) => matches(j));
  const newCount = out.filter(isNew).length;
  if (onlyNew && view !== "saved") out = out.filter(isNew);
  const when = (j) => Date.parse(j.posted_at || j.first_seen_at || 0);
  const by = {
    new: (a, b) => when(b) - when(a),
    filings: (a, b) => (b.evidence?.filings || 0) - (a.evidence?.filings || 0) || when(b) - when(a),
    wage: (a, b) => (b.evidence?.median_wage || 0) - (a.evidence?.median_wage || 0) || when(b) - when(a),
  };
  out.sort(by[state.sort]);
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
  const ev = j.evidence;
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
    { entry: el("span", { class: "tag" }, "New grad"), intern: el("span", { class: "tag" }, "Internship") }[j.level] || null,
    j.min_years != null ? el("span", { class: "tag" }, j.min_years === 0 ? "No experience asked" : `Asks ${j.min_years}+ yr`) : null,
    j.is_remote ? el("span", { class: "tag" }, "Remote") : null,
    applied.has(k) ? el("span", { class: "tag done" }, "Applied") : null,
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
      el("h3", { class: "job-title" }, el("a", { href: j.url, target: "_blank", rel: "noopener" }, j.title)),
      meta, tags, evidenceLine(j),
    ),
    el("div", { class: "actions" },
      el("div", { class: "icons" }, saveBtn, appliedBtn,
        el("a", { class: "iconbtn", href: reportURL(j), target: "_blank", rel: "noopener", "aria-label": "Report a problem with this job", title: "Report a problem" }, svg(ICON.flag))),
      el("a", { class: "btn primary apply", href: j.url, target: "_blank", rel: "noopener" }, "Apply", svg(ICON.out)),
    ),
  );
}
function toggleSet(set, storeKey, k, btn, onMsg, offMsg) {
  const on = !set.has(k);
  on ? set.add(k) : set.delete(k);
  store.set(storeKey, [...set]);
  btn.setAttribute("aria-pressed", String(on));
  toast(on ? onMsg : offMsg);
  updateSavedCount();
  if (view === "saved" && set === saved && !on) render();
}
function updateSavedCount() {
  const n = jobs.filter((j) => saved.has(key(j))).length;
  $("saved-count").textContent = n;
  $("saved-count").hidden = n === 0;
}

// ---- render ----------------------------------------------------------------------

function render() {
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
  $("count").replaceChildren(
    `${num(out.length)} ${out.length === 1 ? "job" : "jobs"}`,
    view === "saved" ? el("span", {}, " saved") : "",
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
  $("legend").hidden = !list.some((j) => j.evidence);
  syncControls();
  renderPlaces();
  writeURL();
}
function rerender() { shown = PAGE; render(); }

function syncControls() {
  document.querySelectorAll(".chips").forEach((g) => g.querySelectorAll(".chip").forEach((b) => b.setAttribute("aria-pressed", String(state[g.dataset.key].includes(b.dataset.value)))));
  document.querySelectorAll(".segmented button").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.value === state.age)));
  $("filed").checked = state.filed;
  $("hide-applied").checked = state.hideApplied;
  $("sort").value = state.sort;
  if ($("q").value !== state.q) $("q").value = state.q;
  const changed = ["f", "l"].filter((k) => state[k].join() !== DEFAULTS[k].join()).length + (state.age !== DEFAULTS.age) + state.loc.length + (state.filed !== DEFAULTS.filed) + state.hideApplied;
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

let currentCompany = null;
function openCompany(name) {
  const c = companies[name] || {};
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
  body.replaceChildren(
    el("div", { class: "panel-head" },
      logo(name),
      el("h2", { id: "panel-title" }, name),
      el("button", { type: "button", class: "closebtn", "aria-label": "Close", onclick: closeCompany }, svg(ICON.x))),
    el("p", { class: "panel-summary" }, total || anyField
      ? [`Filed `, el("b", {}, num(total)), ` H-1B applications for tech roles between October 2024 and June 2026. `,
        `Below, the same filings by field. One filing can count in more than one field, since software developer filings cover AI and data work too.`]
      : "No H-1B filings found for software, AI/ML or data roles under the names we matched. It may file under a different legal name."),
    ...evs.map(fieldCard),
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
  document.querySelectorAll(".chips").forEach((g) => g.addEventListener("click", (e) => {
    const b = e.target.closest(".chip");
    if (!b) return;
    const k = g.dataset.key, v = b.dataset.value;
    const cur = state[k];
    // Never let a group go empty: tapping the last selected chip selects it alone
    state[k] = cur.includes(v) ? (cur.length > 1 ? cur.filter((x) => x !== v) : cur) : [...cur, v];
    rerender();
  }));
  document.querySelector(".segmented").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (b) { state.age = b.dataset.value; rerender(); }
  });
  $("filed").addEventListener("change", (e) => { state.filed = e.target.checked; rerender(); });
  $("hide-applied").addEventListener("change", (e) => { state.hideApplied = e.target.checked; rerender(); });
  $("sort").addEventListener("change", (e) => { state.sort = e.target.value; rerender(); });
  let qTimer;
  $("q").addEventListener("input", (e) => { clearTimeout(qTimer); qTimer = setTimeout(() => { state.q = e.target.value.trim(); rerender(); }, 120); });
  $("place-search").addEventListener("input", renderPlaces);
  $("reset").addEventListener("click", () => { state = structuredClone(DEFAULTS); onlyNew = false; $("place-search").value = ""; rerender(); });
  $("more").addEventListener("click", () => { shown += PAGE; render(); });
  $("new-toggle").addEventListener("click", () => { onlyNew = !onlyNew; rerender(); });
  $("copy-link").addEventListener("click", async () => {
    writeURL(null);
    try { await navigator.clipboard.writeText(location.href); toast("Link copied"); } catch (e) { toast("Copy the address bar to share this search"); }
  });
  $("nav-jobs").addEventListener("click", () => { view = "jobs"; rerender(); });
  $("nav-saved").addEventListener("click", () => { view = "saved"; onlyNew = false; rerender(); window.scrollTo({ top: $("results").offsetTop - 70, behavior: "smooth" }); });
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
  $("defs").replaceChildren(...["filings", "new-hires", "wage-level", "median", "level", "no-filings"].flatMap((t) => [el("dt", {}, EXPLAIN[t][0]), el("dd", {}, EXPLAIN[t][1])]));
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

async function load() {
  const companyParam = readURL();
  wire();
  rememberVisit();
  $("jobs").replaceChildren(...Array.from({ length: 4 }, () => el("li", { class: "skeleton" })));
  try {
    const get = (f) => fetch(f, { cache: "no-cache" }).then((r) => { if (!r.ok) throw new Error(f); return r.json(); });
    const [data, comp, health] = await Promise.all([get("jobs.json"), get("companies.json").catch(() => ({})), get("health.json").catch(() => null)]);
    jobs = data.jobs.map((j) => ({ ...j, states: j.states || [], metros: j.metros || [] }));
    companies = comp;
    const nCompanies = new Set(jobs.map((j) => j.company)).size;
    $("status").replaceChildren(
      el("span", {}, el("i", { class: "dot" }), "Updated ", el("b", {}, ago(data.updated))),
      el("span", {}, el("b", {}, num(jobs.length)), " open jobs at ", el("b", {}, num(nCompanies)), " companies"),
      health ? el("span", {}, el("b", {}, num(health.boards_ok)), " job boards checked") : "",
    );
    updateSavedCount();
    render();
    if (companyParam && companies[companyParam]) openCompany(companyParam);
  } catch (e) {
    $("status").textContent = "Couldn't load jobs. Refresh to try again.";
    $("jobs").replaceChildren(el("li", { class: "empty" }, el("strong", {}, "Couldn't load jobs"), "Refresh the page to try again."));
  }
}
load();
