// Reads jobs.json and health.json, filters in the browser. No build step.

const PAGE = 50;
const FIELD = { swe: "Software", ai: "AI/ML", data: "Data" };
const $ = (id) => document.getElementById(id);

let jobs = [];
let shown = PAGE;

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else node.setAttribute(k, v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

function ago(iso) {
  const mins = Math.max(0, (Date.now() - Date.parse(iso)) / 60000);
  if (mins < 60) return `${Math.round(mins)} min ago`;
  const hours = mins / 60;
  if (hours < 24) return `${Math.round(hours)}h ago`;
  const days = Math.round(hours / 24);
  return days === 1 ? "1 day ago" : `${days} days ago`;
}

function money(n) {
  return n ? `$${Math.round(n / 1000)}K` : "n/a";
}

function checked(name) {
  return new Set([...document.querySelectorAll(`input[name=${name}]:checked`)].map((i) => i.value));
}

function filtered() {
  const q = $("q").value.trim().toLowerCase();
  const roles = checked("role");
  const levels = checked("level");
  const maxAge = Number($("age").value) * 86400000;
  const now = Date.now();
  const out = jobs.filter((j) => {
    if (!roles.has(j.role) || !levels.has(j.level)) return false;
    if (j.posted_at && now - Date.parse(j.posted_at) > maxAge) return false;
    if ($("filed").checked && !j.evidence) return false;
    if ($("remote").checked && !j.is_remote) return false;
    if (q && !`${j.title} ${j.company} ${j.location || ""}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const byDate = (a, b) => Date.parse(b.posted_at || 0) - Date.parse(a.posted_at || 0);
  if ($("sort").value === "filings") {
    out.sort((a, b) => (b.evidence?.filings || 0) - (a.evidence?.filings || 0) || byDate(a, b));
  } else {
    out.sort(byDate);
  }
  return out;
}

function evidence(j) {
  const ev = j.evidence;
  if (!ev) {
    return el("div", { class: "ev none" }, `No H-1B filings found for ${FIELD[j.role]} roles at ${j.company}`);
  }
  const levels = ev.level_1 + ev.level_2 + ev.level_3 + ev.level_4;
  const parts = [
    `${ev.filings.toLocaleString()} ${FIELD[j.role]} H-1B filings`,
    `${ev.new_hire_filings.toLocaleString()} new hires`,
  ];
  if (levels) parts.push(`${Math.round(((ev.level_1 + ev.level_2) * 100) / levels)}% at wage level I-II`);
  parts.push(`median ${money(ev.median_wage)}`);
  const bar = el("div", { class: "bar", role: "img", "aria-label": `Wage levels: I ${ev.level_1}, II ${ev.level_2}, III ${ev.level_3}, IV ${ev.level_4}` });
  if (levels) {
    for (const [cls, n] of [["l1", ev.level_1], ["l2", ev.level_2], ["l3", ev.level_3], ["l4", ev.level_4]]) {
      if (n) bar.append(el("span", { class: cls, style: `width:${(n * 100) / levels}%`, title: `Level ${cls[1]}: ${n}` }));
    }
  }
  return el("div", { class: "ev" }, parts.join(" · "), levels ? bar : null);
}

function card(j) {
  const tags = el("div", { class: "tags" }, el("span", { class: "tag" }, FIELD[j.role]));
  const level = { entry: "New grad", intern: "Internship" }[j.level];
  if (level) tags.append(el("span", { class: "tag" }, level));
  if (j.min_years != null) tags.append(el("span", { class: "tag" }, j.min_years === 0 ? "No experience asked" : `Asks ${j.min_years}+ yr`));
  if (j.is_remote) tags.append(el("span", { class: "tag" }, "Remote"));
  const meta = [j.company, j.location || "Location not listed"];
  if (j.posted_at) meta.push(`posted ${ago(j.posted_at)}`);
  return el(
    "li",
    { class: "job" },
    el("h3", {}, el("a", { href: j.url, target: "_blank", rel: "noopener" }, j.title)),
    el("p", { class: "meta" }, meta.join(" · ")),
    tags,
    evidence(j),
  );
}

function render() {
  const list = filtered();
  const page = list.slice(0, shown);
  $("jobs").replaceChildren(
    ...(page.length ? page.map(card) : [el("li", { class: "empty" }, "No jobs match these filters. Try a longer time window or fewer filters.")]),
  );
  $("count").textContent = `${list.length.toLocaleString()} ${list.length === 1 ? "job" : "jobs"}`;
  $("more").hidden = list.length <= shown;
}

function reset() {
  shown = PAGE;
  render();
}

async function load() {
  try {
    const [data, health] = await Promise.all([
      fetch("jobs.json", { cache: "no-cache" }).then((r) => r.json()),
      fetch("health.json", { cache: "no-cache" }).then((r) => r.json()).catch(() => null),
    ]);
    jobs = data.jobs;
    let status = `Updated ${ago(data.updated)} · ${jobs.length.toLocaleString()} open jobs`;
    if (health) status += ` · ${health.boards_ok.toLocaleString()} of ${(health.boards_ok + health.boards_failed).toLocaleString()} job boards checked`;
    $("status").textContent = status;
    render();
  } catch (e) {
    $("status").textContent = "Couldn't load jobs. Refresh to try again.";
  }
}

$("filters").addEventListener("input", reset);
$("more").addEventListener("click", () => {
  shown += PAGE;
  render();
});
load();
