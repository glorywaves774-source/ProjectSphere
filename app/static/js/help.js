/* ProjectSphere Assistant — answers from the help guide. */
(function () {
  "use strict";
  var fab = document.getElementById("help-fab");
  var panel = document.getElementById("help-panel");
  if (!fab || !panel) return;
  var bodyEl = panel.querySelector(".help-body");
  var form = panel.querySelector(".help-form");
  var input = form.querySelector("input");
  var suggest = panel.querySelector(".help-suggest");
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  var loaded = false;

  function add(role, parts) {
    var el = document.createElement("div");
    el.className = "msg " + role;
    if (typeof parts === "string") {
      el.textContent = parts;
    } else {
      if (parts.title) { var t = document.createElement("span"); t.className = "msg-title"; t.textContent = parts.title; el.appendChild(t); }
      el.appendChild(document.createTextNode(parts.answer || ""));
      if (parts.note) { var n = document.createElement("span"); n.className = "msg-note"; n.textContent = parts.note; el.appendChild(n); }
      (parts.links || []).forEach(function (l) {
        if (!/^\/[^/]/.test(l.url)) return; // only same-site links
        var a = document.createElement("a"); a.className = "msg-link"; a.href = l.url; a.textContent = l.label + " →";
        el.appendChild(document.createElement("br")); el.appendChild(a);
      });
    }
    bodyEl.appendChild(el);
    // Show the start of the newest message
    bodyEl.scrollTop = role === "bot" ? Math.max(0, el.offsetTop - bodyEl.offsetTop - 8) : bodyEl.scrollHeight;
  }

  function open() {
    panel.classList.add("open");
    fab.setAttribute("aria-expanded", "true");
    if (!loaded) {
      loaded = true;
      fetch("/help/suggestions", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
        (d.items || []).forEach(function (q) {
          var b = document.createElement("button"); b.type = "button"; b.textContent = q;
          b.addEventListener("click", function () { ask(q); });
          suggest.appendChild(b);
        });
      }).catch(function () {});
    }
    // Do not pop the keyboard up on phones
    if (!window.matchMedia("(max-width: 760px)").matches) input.focus();
  }
  function close() { panel.classList.remove("open", "typing"); fab.setAttribute("aria-expanded", "false"); fab.focus(); }
  fab.addEventListener("click", function () { panel.classList.contains("open") ? close() : open(); });
  panel.querySelector("[data-help-close]").addEventListener("click", close);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && panel.classList.contains("open")) close(); });

  function ask(q) {
    panel.classList.add("asked");
    add("me", q);
    fetch("/help/ask", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify({ q: q })
    }).then(function (r) {
      if (r.status === 429) throw new Error("Too many questions at once. Please wait a moment.");
      return r.json();
    }).then(function (d) { add("bot", d.error ? d.error : d); })
      .catch(function (e) { add("bot", e.message || "Sorry, I couldn't answer that right now."); });
  }
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = input.value.trim();
    if (!q) return;
    input.value = "";
    ask(q);
  });

  // While typing on phones, use the space above the keyboard
  var vv = window.visualViewport;
  input.addEventListener("focus", function () {
    if (!window.matchMedia("(max-width: 760px)").matches) return;
    panel.classList.add("typing");
    fit();
  });
  input.addEventListener("blur", function () { panel.classList.remove("typing"); panel.style.height = ""; panel.style.bottom = ""; });
  function fit() {
    if (!vv || !panel.classList.contains("typing")) return;
    var keyboard = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
    panel.style.bottom = (keyboard + 8) + "px";
    panel.style.height = Math.max(220, vv.height - 24) + "px";
  }
  if (vv) vv.addEventListener("resize", fit);
})();
