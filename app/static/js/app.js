/* Laari ProjectSphere — shell behaviour: theme, phone menu, nav filter, toasts, meters. */
(function () {
  "use strict";
  var body = document.body;
  var root = document.documentElement;

  /* ---------- Theme ---------- */
  function setTheme(theme, remember) {
    root.setAttribute("data-theme", theme);
    root.setAttribute("data-bs-theme", theme);
    if (remember) {
      try { localStorage.setItem("ps-theme", theme); } catch (e) { /* ignore */ }
      document.cookie = "ps_theme=" + theme + ";path=/;max-age=31536000;samesite=lax";
    }
    document.querySelectorAll(".theme-toggle").forEach(function (b) {
      var label = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
      b.setAttribute("aria-label", label);
      b.setAttribute("title", label);
    });
    document.dispatchEvent(new CustomEvent("ps:theme", { detail: theme }));
  }
  setTheme(root.getAttribute("data-theme") || "light", false);
  document.querySelectorAll(".theme-toggle").forEach(function (btn) {
    btn.addEventListener("click", function () {
      setTheme(root.getAttribute("data-theme") === "dark" ? "light" : "dark", true);
    });
  });
  if (window.matchMedia) {
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function (e) {
      var saved = null;
      try { saved = localStorage.getItem("ps-theme"); } catch (err) { /* ignore */ }
      if (!saved && !/ps_theme=/.test(document.cookie)) setTheme(e.matches ? "dark" : "light", false);
    });
  }

  if (root.classList.contains("is-standalone")) body.classList.add("standalone");

  /* ---------- Phone menu (drawer) ---------- */
  var drawerOpen = false;
  var phone = window.matchMedia("(max-width: 760px)");
  function openDrawer() {
    if (drawerOpen || !phone.matches) return;
    drawerOpen = true;
    body.classList.add("drawer-open");
    history.pushState({ psDrawer: true }, "");
    var f = document.querySelector(".nav-filter input");
    var sb = document.getElementById("sidebar");
    if (sb) sb.setAttribute("aria-hidden", "false");
    document.querySelectorAll("[data-drawer-open]").forEach(function (b) { b.setAttribute("aria-expanded", "true"); });
    if (f) { f.value = ""; filterNav(""); }
  }
  function closeDrawer(fromPopState) {
    if (!drawerOpen) return;
    drawerOpen = false;
    body.classList.remove("drawer-open");
    document.querySelectorAll("[data-drawer-open]").forEach(function (b) { b.setAttribute("aria-expanded", "false"); });
    if (!fromPopState && history.state && history.state.psDrawer) history.back();
  }
  document.querySelectorAll("[data-drawer-open]").forEach(function (b) { b.addEventListener("click", openDrawer); });
  document.querySelectorAll("[data-drawer-close]").forEach(function (b) { b.addEventListener("click", function () { closeDrawer(false); }); });
  window.addEventListener("popstate", function () { if (drawerOpen) closeDrawer(true); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeDrawer(false); });
  phone.addEventListener("change", function () { if (!phone.matches) closeDrawer(false); });

  // Choosing a page closes the menu without leaving an extra Back step
  document.querySelectorAll(".sidebar a.nav-link-ps[href]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      if (!drawerOpen) return;
      e.preventDefault();
      var href = a.getAttribute("href");
      drawerOpen = false;
      body.classList.remove("drawer-open");
      if (history.state && history.state.psDrawer) {
        history.replaceState(null, "");
        window.location.replace(href);
      } else {
        window.location.assign(href);
      }
    });
  });

  /* ---------- Menu filter (accent-insensitive) ---------- */
  function fold(s) { return (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase(); }
  function filterNav(q) {
    q = fold(q.trim());
    document.querySelectorAll(".nav-section").forEach(function (sec) {
      var any = false;
      sec.querySelectorAll(".nav-link-ps").forEach(function (a) {
        var hit = !q || fold(a.textContent).indexOf(q) !== -1;
        a.hidden = !hit;
        if (hit) any = true;
      });
      sec.hidden = !any;
    });
  }
  var navFilter = document.querySelector(".nav-filter input");
  if (navFilter) navFilter.addEventListener("input", function () { filterNav(navFilter.value); });

  /* ---------- Back button for installed apps ---------- */
  document.querySelectorAll("[data-back]").forEach(function (b) {
    b.addEventListener("click", function () { if (history.length > 1) history.back(); else window.location.assign("/"); });
  });

  /* ---------- Meters (CSP-safe widths) ---------- */
  requestAnimationFrame(function () {
    document.querySelectorAll("[data-width]").forEach(function (el) {
      var w = Math.max(0, Math.min(100, parseFloat(el.getAttribute("data-width")) || 0));
      el.style.width = w + "%";
    });
  });

  /* ---------- Category colours (CSP-safe) ---------- */
  document.querySelectorAll("[data-color]").forEach(function (el) {
    var c = el.getAttribute("data-color");
    if (/^#[0-9a-fA-F]{6}$/.test(c)) el.style.background = c;
  });

  /* ---------- Toasts ---------- */
  document.querySelectorAll(".flash").forEach(function (f, i) {
    var close = function () { f.classList.add("hide"); setTimeout(function () { f.remove(); }, 320); };
    var x = f.querySelector(".x");
    if (x) x.addEventListener("click", close);
    if (!f.classList.contains("danger")) setTimeout(close, 6000 + i * 800);
  });

  /* ---------- Show / hide password ---------- */
  document.querySelectorAll(".pw-toggle").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var input = btn.parentElement.querySelector("input");
      var show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
      btn.setAttribute("aria-pressed", show ? "true" : "false");
    });
  });

  /* ---------- Two-step confirm for risky buttons (no browser dialogs) ---------- */
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (form.dataset.confirmed) return;
      e.preventDefault();
      var btn = form.querySelector("[type=submit]");
      form.dataset.confirmed = "1";
      if (btn) { btn.dataset.label = btn.textContent; btn.textContent = form.getAttribute("data-confirm"); }
      setTimeout(function () {
        delete form.dataset.confirmed;
        if (btn && btn.dataset.label) btn.textContent = btn.dataset.label;
      }, 4000);
    });
  });

  /* ---------- Show / hide extra filters on phones ---------- */
  document.querySelectorAll("[data-toggle-class]").forEach(function (btn) {
    var target = document.querySelector(btn.getAttribute("data-toggle-class"));
    if (!target) return;
    btn.addEventListener("click", function () {
      var open = target.classList.toggle("show");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });

  /* ---------- Auto-submit filters ---------- */
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { el.form.submit(); });
  });

  /* ---------- Copy to clipboard ---------- */
  document.querySelectorAll("[data-copy]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var target = document.querySelector(btn.getAttribute("data-copy"));
      if (!target) return;
      var done = function () { var t = btn.lastChild; btn.dataset.label = btn.dataset.label || t.textContent; t.textContent = " Copied"; setTimeout(function () { t.textContent = btn.dataset.label; }, 2000); };
      if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(target.value).then(done, function () {});
      else { target.select(); try { document.execCommand("copy"); done(); } catch (e) { /* ignore */ } }
    });
  });
  document.querySelectorAll("[data-select-all]").forEach(function (el) {
    el.addEventListener("focus", function () { el.select(); });
  });

  /* ---------- Position markers (e.g. "expected by now" on indicator bars) ---------- */
  document.querySelectorAll("[data-left]").forEach(function (el) {
    var x = Math.max(0, Math.min(100, parseFloat(el.getAttribute("data-left")) || 0));
    el.style.left = "calc(" + x + "% - 1px)";
  });

  /* ---------- "Use my location" for field visits (only shown where the browser supports it) ---------- */
  document.querySelectorAll("[data-geolocate]").forEach(function (btn) {
    if (!("geolocation" in navigator) || !window.isSecureContext) return;
    var ids = btn.getAttribute("data-geolocate").split(",");
    var lat = document.querySelector(ids[0]), lng = document.querySelector(ids[1]);
    var status = btn.closest("fieldset").querySelector("[data-geo-status]");
    btn.hidden = false;
    btn.addEventListener("click", function () {
      btn.disabled = true;
      if (status) status.textContent = "Finding your location…";
      navigator.geolocation.getCurrentPosition(function (pos) {
        lat.value = pos.coords.latitude.toFixed(6);
        lng.value = pos.coords.longitude.toFixed(6);
        if (status) status.textContent = "Location added (accurate to about " + Math.round(pos.coords.accuracy) + " m).";
        btn.disabled = false;
      }, function () {
        if (status) status.textContent = "Couldn't get your location. Check location permission, or type the coordinates.";
        btn.disabled = false;
      }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
    });
  });

  /* ---------- Purchase request items: live totals and "add another item" ---------- */
  document.querySelectorAll("[data-items]").forEach(function (grid) {
    function n(v) { var x = parseFloat(String(v || "").replace(/,/g, "")); return isFinite(x) ? x : 0; }
    function fmt(x) { return x.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
    function recalc() {
      var grand = 0;
      grid.querySelectorAll("[data-item-row]").forEach(function (row) {
        var t = n(row.querySelector("[data-qty]").value) * n(row.querySelector("[data-cost]").value);
        grand += t;
        row.querySelector("[data-total]").textContent = t ? fmt(t) : "—";
      });
      var g = grid.querySelector("[data-grand]");
      if (g) g.textContent = grand ? fmt(grand) : "—";
    }
    grid.addEventListener("input", recalc);
    recalc();
    var add = grid.querySelector("[data-add-item]");
    if (add) add.addEventListener("click", function () {
      var next = grid.querySelector("[data-item-row][hidden]");
      if (next) { next.hidden = false; next.querySelector("input").focus(); }
      if (!grid.querySelector("[data-item-row][hidden]")) add.hidden = true;
    });
  });

  /* ---------- Risk score preview (likelihood × impact) ---------- */
  document.querySelectorAll("[data-score-from]").forEach(function (box) {
    var ids = box.getAttribute("data-score-from").split(",");
    var l = document.querySelector(ids[0]), i = document.querySelector(ids[1]);
    if (!l || !i) return;
    function show() {
      var s = (parseInt(l.value, 10) || 0) * (parseInt(i.value, 10) || 0);
      var band = s >= 16 ? "critical" : s >= 10 ? "high" : s >= 5 ? "medium" : "low";
      box.innerHTML = "";
      var b = document.createElement("b"); b.textContent = "Score " + s + " · " + band;
      box.appendChild(b);
      if (s >= 10) box.appendChild(document.createTextNode(" — needs a mitigation plan unless accepted"));
    }
    l.addEventListener("change", show); i.addEventListener("change", show); show();
  });

  /* ---------- Monthly / yearly price switch on the plans page ---------- */
  document.querySelectorAll("[data-cycle]").forEach(function (tab) {
    tab.addEventListener("click", function (e) {
      e.preventDefault();
      var cycle = tab.getAttribute("data-cycle");
      document.querySelectorAll("[data-cycle]").forEach(function (t) {
        if (t === tab) t.setAttribute("aria-current", "page"); else t.removeAttribute("aria-current");
      });
      document.querySelectorAll("[data-show-cycle]").forEach(function (el) { el.hidden = el.getAttribute("data-show-cycle") !== cycle; });
      document.querySelectorAll("[data-cycle-input]").forEach(function (el) { el.value = cycle; });
    });
  });

  /* ---------- Filter a grid of checkbox tiles (people pickers) ---------- */
  document.querySelectorAll("[data-filter-tiles]").forEach(function (box) {
    var grid = box.parentElement.querySelector(".check-grid");
    if (!grid) return;
    box.addEventListener("input", function () {
      var q = fold(box.value.trim());
      grid.querySelectorAll(".check-tile").forEach(function (t) {
        t.hidden = !!q && fold(t.textContent).indexOf(q) === -1 && !t.querySelector("input:checked");
      });
    });
  });

  /* ---------- Chat: open at the newest message; Enter sends, Shift+Enter adds a line ---------- */
  var thread = document.querySelector(".chat-thread");
  if (thread) thread.scrollTop = thread.scrollHeight;
  document.querySelectorAll(".chat-compose textarea").forEach(function (ta) {
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && window.matchMedia("(pointer: fine)").matches) {
        e.preventDefault();
        if (ta.value.trim()) ta.form.requestSubmit ? ta.form.requestSubmit() : ta.form.submit();
      }
    });
  });

  if (window.lucide) window.lucide.createIcons();
})();
