/* Runs synchronously in <head> so the page never flashes the wrong theme. */
(function () {
  var choice = null;
  try { choice = localStorage.getItem("ps-theme"); } catch (e) { /* storage blocked */ }
  if (!choice) {
    var m = document.cookie.match(/(?:^|;\s*)ps_theme=(light|dark)/);
    choice = m ? m[1] : null;
  }
  if (choice !== "light" && choice !== "dark") {
    choice = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  var root = document.documentElement;
  root.setAttribute("data-theme", choice);
  root.setAttribute("data-bs-theme", choice);
  var standalone = (window.matchMedia && window.matchMedia("(display-mode: standalone)").matches) || window.navigator.standalone === true;
  if (standalone) root.classList.add("is-standalone");
})();
