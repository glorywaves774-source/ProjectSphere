/* Laari ProjectSphere — lightweight Gantt chart (no third-party code, CSP-safe). */
(function () {
  "use strict";
  var host = document.getElementById("gantt");
  if (!host) return;
  var src = document.getElementById(host.getAttribute("data-source"));
  var data;
  try { data = JSON.parse(src.textContent); } catch (e) { return; }
  var taskUrl = host.getAttribute("data-task-url");
  var ROW = 40, HEAD = 52, SVGNS = "http://www.w3.org/2000/svg";
  var zooms = { day: 34, week: 14, month: 5 };
  var zoom = "week";
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function d(s) { var p = s.split("-"); return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])); }
  function days(a, b) { return Math.round((b - a) / 86400000); }
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }

  function render() {
    host.innerHTML = "";
    var px = zooms[zoom];
    var start = d(data.span.start), end = d(data.span.end);
    // align to Monday for week view, 1st for month view
    if (zoom !== "day") start = new Date(start - ((start.getUTCDay() + 6) % 7) * 86400000);
    var total = days(start, end) + 1;
    var width = total * px;

    var wrap = el("div", "gantt-scroll");
    var grid = el("div", "gantt-grid");
    grid.style.gridTemplateRows = HEAD + "px repeat(" + data.rows.length + ", " + ROW + "px)";

    // header
    var corner = el("div", "g-corner", "Task");
    corner.style.gridRow = "1"; corner.style.gridColumn = "1";
    grid.appendChild(corner);
    var head = el("div", "g-head");
    head.style.width = width + "px";
    head.style.gridRow = "1"; head.style.gridColumn = "2";
    var cur = new Date(start);
    while (cur <= end) {
      var mEnd = new Date(Date.UTC(cur.getUTCFullYear(), cur.getUTCMonth() + 1, 1));
      var span = Math.min(days(cur, mEnd), days(cur, end) + 1);
      var m = el("div", "g-month", MONTHS[cur.getUTCMonth()] + " " + cur.getUTCFullYear());
      m.style.left = days(start, cur) * px + "px"; m.style.width = span * px + "px";
      head.appendChild(m);
      cur = mEnd;
    }
    for (var i = 0; i < total; i++) {
      var day = new Date(start.getTime() + i * 86400000);
      var label = null;
      if (zoom === "day") label = String(day.getUTCDate());
      else if (zoom === "week" && day.getUTCDay() === 1) label = String(day.getUTCDate());
      if (label !== null) {
        var t = el("div", "g-tick", label);
        t.style.left = i * px + "px"; t.style.width = (zoom === "day" ? px : 7 * px) + "px";
        head.appendChild(t);
      }
    }
    grid.appendChild(head);

    // rows
    var pos = {};
    data.rows.forEach(function (r, idx) {
      var label = el("a", "g-label" + (r.summary ? " summary" : "") + (r.critical ? " critical" : ""));
      label.href = taskUrl + r.id;
      label.style.paddingLeft = (12 + r.depth * 16) + "px";
      label.appendChild(el("span", "g-wbs", r.wbs));
      var tt = el("span", "g-title", (r.milestone ? "◆ " : "") + r.title);
      tt.setAttribute("translate", "no");
      label.appendChild(tt);
      label.title = r.title + (r.assignee ? " · " + r.assignee : "");
      grid.appendChild(label);

      var lane = el("div", "g-lane");
      lane.style.width = width + "px";
      lane.style.gridRow = String(idx + 2); lane.style.gridColumn = "2";
      if (r.start) {
        var s = d(r.start), e = d(r.end || r.start);
        var left = days(start, s) * px, w = (days(s, e) + 1) * px;
        if (r.milestone) {
          var ms = el("a", "g-ms" + (r.done ? " done" : "") + (r.critical ? " critical" : ""));
          ms.href = taskUrl + r.id; ms.title = r.title + " · " + r.end;
          ms.style.left = (left + px / 2 - 9) + "px";
          lane.appendChild(ms);
          pos[r.id] = { x1: left + px / 2 - 9, x2: left + px / 2 + 9, y: idx * ROW + ROW / 2 };
        } else {
          var bar = el("a", "g-bar" + (r.summary ? " summary" : "") + (r.critical ? " critical" : "") +
            (r.done ? " done" : "") + (r.overdue ? " overdue" : ""));
          bar.href = taskUrl + r.id;
          bar.style.left = left + "px"; bar.style.width = Math.max(w, 6) + "px";
          bar.title = r.title + " · " + r.start + " → " + r.end + " · " + r.progress + "%";
          var fill = el("span", "g-fill"); fill.style.width = r.progress + "%";
          bar.appendChild(fill);
          if (w > 60 && !r.summary && r.progress > 0) { var bl = el("span", "g-bar-label", r.progress + "%"); bar.appendChild(bl); }
          lane.appendChild(bar);
          pos[r.id] = { x1: left, x2: left + Math.max(w, 6), y: idx * ROW + ROW / 2 };
        }
      }
      grid.appendChild(lane);
    });

    // overlay: today, project end, dependency arrows
    var overlay = document.createElementNS(SVGNS, "svg");
    overlay.setAttribute("class", "g-overlay");
    overlay.setAttribute("width", width); overlay.setAttribute("height", data.rows.length * ROW);
    var defs = document.createElementNS(SVGNS, "defs");
    defs.innerHTML = '<marker id="g-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" class="g-arrowhead"/></marker>';
    overlay.appendChild(defs);
    function vline(date, cls) {
      var x = days(start, d(date)) * px + px / 2;
      if (x < 0 || x > width) return;
      var l = document.createElementNS(SVGNS, "line");
      l.setAttribute("x1", x); l.setAttribute("x2", x); l.setAttribute("y1", 0); l.setAttribute("y2", data.rows.length * ROW);
      l.setAttribute("class", cls); overlay.appendChild(l);
    }
    if (data.projectEnd) vline(data.projectEnd, "g-end");
    vline(data.today, "g-today");
    data.links.forEach(function (lk) {
      var a = pos[lk.from], b = pos[lk.to];
      if (!a || !b) return;
      var x1 = a.x2, y1 = a.y, x2 = b.x1, y2 = b.y, mid = Math.max(x1 + 10, Math.min(x2 - 10, x1 + 14));
      var p = document.createElementNS(SVGNS, "path");
      p.setAttribute("d", "M" + x1 + "," + y1 + " H" + mid + " V" + y2 + " H" + (x2 - 2));
      p.setAttribute("class", "g-link"); p.setAttribute("marker-end", "url(#g-arrow)");
      overlay.appendChild(p);
    });
    var overlayCell = el("div", "g-overlay-cell");
    overlayCell.style.gridRow = "2 / span " + data.rows.length;
    overlayCell.appendChild(overlay);
    grid.appendChild(overlayCell);

    wrap.appendChild(grid);
    host.appendChild(wrap);
    // start scrolled near today
    var todayX = days(start, d(data.today)) * px;
    if (todayX > wrap.clientWidth * 0.6) wrap.scrollLeft = todayX - wrap.clientWidth * 0.35;
  }

  document.querySelectorAll("[data-zoom]").forEach(function (a) {
    a.addEventListener("click", function (e) {
      e.preventDefault();
      zoom = a.getAttribute("data-zoom");
      document.querySelectorAll("[data-zoom]").forEach(function (b) { b.removeAttribute("aria-current"); });
      a.setAttribute("aria-current", "page");
      render();
    });
  });
  var pr = document.querySelector("[data-gantt-print]");
  if (pr) pr.addEventListener("click", function () { window.print(); });
  if (window.matchMedia("(max-width: 760px)").matches) {
    zoom = "month";
    document.querySelectorAll("[data-zoom]").forEach(function (b) {
      if (b.getAttribute("data-zoom") === "month") b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
    });
  }
  render();
})();
