// Static pages (jobs, companies, fields, cities): the theme switch, the header
// status, and Save / Mark applied on job pages, shared with the job board
// through the same browser storage.
"use strict";

const read = (k, d) => { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } };
const write = (k, v) => { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} };

document.getElementById("theme")?.addEventListener("click", () => {
  const dark = getComputedStyle(document.documentElement).colorScheme === "dark";
  const next = dark ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("ff.theme", next); } catch (e) {}
});

// "Updated 12 min ago, 2,639 job boards checked", from the latest run
const status = document.querySelector(".topbar .status");
if (status) {
  fetch("/health.json", { cache: "no-cache" }).then((r) => r.json()).then((h) => {
    const mins = Math.max(1, Math.round((Date.now() - Date.parse(h.finished_at)) / 60000));
    const ago = mins < 60 ? `${mins} min ago` : `${Math.round(mins / 60)}h ago`;
    status.innerHTML = "";
    const dot = document.createElement("i");
    dot.className = "dot";
    const b = (t) => { const x = document.createElement("b"); x.textContent = t; return x; };
    const text = document.createElement("span");
    text.append("Updated ", b(ago), ", ", b(h.boards_ok.toLocaleString("en-US")), " job boards checked");
    status.append(dot, text);
  }).catch(() => {});
}

// Save and Mark applied on a job page
const box = document.querySelector(".jobhead-actions[data-k]");
if (box) {
  const k = box.dataset.k;
  const saved = new Set(read("ff.saved", []));
  const applied = new Set(read("ff.applied", []));
  const btn = (act) => box.querySelector(`[data-act="${act}"]`);
  const sync = () => {
    btn("save").setAttribute("aria-pressed", String(saved.has(k)));
    btn("save").textContent = saved.has(k) ? "Saved" : "Save";
    btn("applied").setAttribute("aria-pressed", String(applied.has(k)));
    btn("applied").textContent = applied.has(k) ? "Applied" : "Mark applied";
  };
  const remember = () => {
    const snap = read("ff.snap", {});
    snap[k] = JSON.parse(box.dataset.snap);
    write("ff.snap", snap);
  };
  const count = (name) => { try { window.goatcounter?.count?.({ path: name, event: true }); } catch (e) {} };
  btn("save").addEventListener("click", () => {
    if (saved.has(k)) { saved.delete(k); applied.delete(k); } else { saved.add(k); remember(); count("save"); }
    write("ff.saved", [...saved]);
    write("ff.applied", [...applied]);
    sync();
  });
  btn("applied").addEventListener("click", () => {
    if (applied.has(k)) applied.delete(k); else { applied.add(k); saved.add(k); remember(); count("applied"); }
    write("ff.saved", [...saved]);
    write("ff.applied", [...applied]);
    sync();
  });
  sync();
}

// Company search on /companies/: filters the A to Z list as you type, keeps
// the search in ?q= so it can be shared, and Enter opens a single match
const find = document.querySelector(".cc-find");
if (find) {
  const input = find.querySelector("input");
  const note = find.querySelector(".cc-count");
  const jump = document.querySelector(".cc-jump");
  const plain = (t) => t.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
  const squash = (t) => plain(t).replace(/[^a-z0-9]+/g, "");
  // Spaces are ignored ("service now" finds ServiceNow), but a match has to
  // start at a word in the name, so "cisco" doesn't find San Francisco
  const starts = (name) => {
    const words = plain(name).split(/[^a-z0-9]+/).filter(Boolean);
    return words.map((_, i) => words.slice(i).join(""));
  };
  const rows = [...document.querySelectorAll(".cc-index li")].map((li) => {
    const a = li.querySelector("a");
    return { li, a, keys: starts(a.textContent) };
  });
  const sections = [...document.querySelectorAll('section[id^="letter-"]')];
  let shown = [];
  const run = () => {
    const q = squash(input.value);
    shown = [];
    for (const r of rows) {
      const ok = !q || r.keys.some((k) => k.startsWith(q));
      r.li.hidden = !ok;
      if (ok) shown.push(r);
    }
    for (const s of sections) s.hidden = Boolean(q) && !s.querySelector("li:not([hidden])");
    if (jump) jump.hidden = Boolean(q);
    note.textContent = !q ? ""
      : !shown.length ? `No company matches. FiledFor lists the ${rows.length.toLocaleString("en-US")} sponsors whose job boards it can read, so some large employers aren’t here yet. A shorter part of the name can also help.`
      : shown.length === 1 ? "1 company matches. Press Enter to open it."
      : `${shown.length.toLocaleString("en-US")} companies match`;
    const url = new URL(location.href);
    if (q) url.searchParams.set("q", input.value.trim()); else url.searchParams.delete("q");
    history.replaceState(history.state, "", url);
  };
  input.addEventListener("input", run);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && shown.length === 1 && input.value.trim()) location.href = shown[0].a.href;
    if (e.key === "Escape" && input.value) { input.value = ""; run(); }
  });
  input.value = new URLSearchParams(location.search).get("q") || "";
  find.hidden = false;
  if (input.value) run();
}

// "Back to jobs" (and the header's Jobs link) return to the board as the
// visitor left it when they came from there: same filters, job and place
const cameFromBoard = (() => {
  try { const r = new URL(document.referrer); return r.origin === location.origin && r.pathname === "/"; } catch (e) { return false; }
})();
document.querySelectorAll('a.back, .topnav a[href="/"]').forEach((a) => a.addEventListener("click", (e) => {
  if (!cameFromBoard || e.metaKey || e.ctrlKey || e.shiftKey || e.button) return;
  e.preventDefault();
  history.back();
}));
