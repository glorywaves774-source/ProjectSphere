/* Timesheet grid: live totals and extra rows. */
(function () {
  "use strict";
  var grid = document.getElementById("ts-grid");
  if (!grid) return;
  var body = grid.querySelector("tbody");

  function num(v) { var n = parseFloat(String(v).replace(",", ".")); return isNaN(n) ? 0 : n; }
  function fmt(n) { return (Math.round(n * 100) / 100).toString(); }

  function recalc() {
    var days = [0, 0, 0, 0, 0, 0, 0], grand = 0;
    body.querySelectorAll("tr.ts-row").forEach(function (tr) {
      var total = 0;
      tr.querySelectorAll("input.ts-h").forEach(function (inp, i) {
        var v = num(inp.value);
        total += v; days[i] += v;
        inp.classList.toggle("is-invalid", v < 0 || v > 24);
      });
      grand += total;
      tr.querySelector(".ts-row-total").textContent = fmt(total);
    });
    grid.querySelectorAll(".ts-day-total").forEach(function (td, i) {
      td.textContent = fmt(days[i]);
      td.classList.toggle("text-danger", days[i] > 24);
    });
    grid.querySelector(".ts-grand").textContent = fmt(grand);
  }
  body.addEventListener("input", recalc);
  recalc();

  var add = document.getElementById("ts-add-row");
  if (add) add.addEventListener("click", function () {
    var rows = body.querySelectorAll("tr.ts-row");
    if (rows.length >= 30) return;
    var clone = rows[rows.length - 1].cloneNode(true);
    var idx = rows.length;
    clone.querySelectorAll("[name]").forEach(function (el) {
      el.name = el.name.replace(/^row-\d+-/, "row-" + idx + "-");
      if (el.tagName === "SELECT") el.selectedIndex = 0; else el.value = "";
    });
    body.appendChild(clone);
    recalc();
    clone.querySelector("select").focus();
  });
})();
