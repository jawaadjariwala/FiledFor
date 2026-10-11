// Static pages (companies, fields, cities): only the theme switch needs script.
document.getElementById("theme")?.addEventListener("click", () => {
  const dark = getComputedStyle(document.documentElement).colorScheme === "dark";
  const next = dark ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("ff.theme", next); } catch (e) {}
});
