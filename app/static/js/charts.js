/* Draws Chart.js charts from JSON data blocks: <canvas data-chart="bar|doughnut|line" data-source="id"> */
(function () {
  "use strict";
  if (!window.Chart) return;
  var charts = [];

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  var palette = ["#6d5bf6", "#2f7cf6", "#14b8a6", "#f59e0b", "#ec4899", "#22c55e", "#8b5cf6", "#0ea5e9", "#ef4444", "#64748b", "#a3e635"];

  function draw() {
    charts.forEach(function (c) { c.destroy(); });
    charts = [];
    var text = css("--ps-text-3") || "#64748b";
    var grid = css("--ps-border") || "rgba(0,0,0,.08)";
    Chart.defaults.font.family = css("--ps-font");
    Chart.defaults.color = text;

    document.querySelectorAll("canvas[data-chart]").forEach(function (canvas) {
      var src = document.getElementById(canvas.getAttribute("data-source"));
      if (!src) return;
      var data;
      try { data = JSON.parse(src.textContent); } catch (e) { return; }
      var type = canvas.getAttribute("data-chart");
      var ctx = canvas.getContext("2d");
      var bg = data.colors || palette;
      if (type === "bar" || type === "line") {
        var g = ctx.createLinearGradient(0, 0, 0, canvas.clientHeight || 240);
        g.addColorStop(0, "rgba(109,91,246,.95)");
        g.addColorStop(1, "rgba(47,124,246,.65)");
        bg = type === "line" ? "rgba(109,91,246,.15)" : (data.colors || g);
      }
      if (data.series) {
        var colors = data.colors || ["rgba(109,91,246,.35)", "#6d5bf6", "#2f7cf6"];
        var isLine = type === "line";
        charts.push(new Chart(ctx, {
          type: type,
          data: { labels: data.labels, datasets: data.series.map(function (vals, i) {
            return { label: (data.seriesLabels || [])[i] || "", data: vals, backgroundColor: colors[i % colors.length],
                     borderColor: colors[i % colors.length], borderRadius: 8, maxBarThickness: 26,
                     tension: .3, spanGaps: true, borderWidth: isLine ? 2.5 : 0,
                     pointRadius: isLine ? (i === 1 ? 7 : 4) : 0, pointStyle: isLine && i === 1 ? "rectRot" : "circle" }; }) },
          options: { responsive: true, maintainAspectRatio: false,
            indexAxis: canvas.getAttribute("data-horizontal") ? "y" : "x",
            plugins: { legend: { display: true, position: "bottom", labels: { usePointStyle: true, boxWidth: 8 } },
                       tooltip: { padding: 10, cornerRadius: 10 } },
            scales: { x: { grid: { display: false }, border: { display: false } },
                      y: { beginAtZero: true, grid: { color: grid }, border: { display: false } } } }
        }));
        return;
      }
      charts.push(new Chart(ctx, {
        type: type,
        data: {
          labels: data.labels,
          datasets: [{
            label: canvas.getAttribute("data-label") || "",
            data: data.values,
            backgroundColor: bg,
            borderColor: type === "line" ? "#6d5bf6" : (type === "doughnut" ? css("--ps-surface-solid") : "transparent"),
            borderWidth: type === "doughnut" ? 3 : 2,
            borderRadius: type === "bar" ? 10 : 0,
            maxBarThickness: 38,
            tension: .35,
            fill: type === "line",
            hoverOffset: 6
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          cutout: type === "doughnut" ? "68%" : undefined,
          plugins: {
            legend: { display: type === "doughnut", position: "bottom", labels: { usePointStyle: true, boxWidth: 8, padding: 14 } },
            tooltip: { padding: 10, cornerRadius: 10 }
          },
          scales: type === "doughnut" ? {} : {
            x: { grid: { display: false }, border: { display: false } },
            y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: grid }, border: { display: false } }
          }
        }
      }));
    });
  }
  draw();
  document.addEventListener("ps:theme", draw);
})();
