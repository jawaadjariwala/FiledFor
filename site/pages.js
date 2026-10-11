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
