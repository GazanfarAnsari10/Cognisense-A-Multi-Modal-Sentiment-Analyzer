// static/js/chart.js

// ── Chart registry ────────────────────────────────────────────
let charts = {};        // named charts by canvasId
let analysisChart = null; // dedicated emotions chart (base.html #analysisChart)

// ── Theme tokens (mirror CSS variables) ──────────────────────
function getChartTheme() {
  const isDark = document.body.classList.contains("dark") ||
                 !document.body.classList.contains("light");
  return {
    textColor:  isDark ? "#7a96b5" : "#4a6380",
    gridColor:  isDark ? "rgba(99,179,255,0.07)" : "rgba(37,99,235,0.07)",
    titleColor: isDark ? "#e8f1ff" : "#0f1c2e",
    tooltipBg:  isDark ? "#0d1d2e" : "#ffffff",
    tooltipText:isDark ? "#e8f1ff" : "#0f1c2e",
    borderColor:isDark ? "rgba(99,179,255,0.15)" : "rgba(37,99,235,0.12)"
  };
}

// ── Accent color palette (matches --accent / --cyan / sentiment) ──
const PALETTE = [
  "rgba(59,158,255,0.75)",   // electric blue
  "rgba(34,211,238,0.75)",   // cyan
  "rgba(52,211,153,0.75)",   // positive green
  "rgba(251,191,36,0.75)",   // amber
  "rgba(167,139,250,0.75)",  // violet
  "rgba(248,113,113,0.75)",  // negative red
  "rgba(251,146,60,0.75)",   // orange
  "rgba(148,163,184,0.75)"   // slate
];

const PALETTE_BORDER = [
  "rgba(59,158,255,1)",
  "rgba(34,211,238,1)",
  "rgba(52,211,153,1)",
  "rgba(251,191,36,1)",
  "rgba(167,139,250,1)",
  "rgba(248,113,113,1)",
  "rgba(251,146,60,1)",
  "rgba(148,163,184,1)"
];

// ── Global Chart.js defaults (applied once) ───────────────────
function applyChartDefaults() {
  if (typeof Chart === "undefined") return;
  const t = getChartTheme();
  Chart.defaults.color          = t.textColor;
  Chart.defaults.borderColor    = t.gridColor;
  Chart.defaults.font.family    = "'DM Sans', sans-serif";
  Chart.defaults.font.size      = 12;
  Chart.defaults.plugins.tooltip.backgroundColor = t.tooltipBg;
  Chart.defaults.plugins.tooltip.titleColor      = t.tooltipText;
  Chart.defaults.plugins.tooltip.bodyColor       = t.tooltipText;
  Chart.defaults.plugins.tooltip.borderColor     = t.borderColor;
  Chart.defaults.plugins.tooltip.borderWidth     = 1;
  Chart.defaults.plugins.tooltip.padding         = 10;
  Chart.defaults.plugins.tooltip.cornerRadius    = 8;
}

// ── Helper: generate colors from palette ─────────────────────
function generateColors(count) {
  const colors = [];
  for (let i = 0; i < count; i++) colors.push(PALETTE[i % PALETTE.length]);
  return colors;
}

function generateBorderColors(count) {
  const colors = [];
  for (let i = 0; i < count; i++) colors.push(PALETTE_BORDER[i % PALETTE_BORDER.length]);
  return colors;
}

// ── Helper: build themed scale options ───────────────────────
function buildScales(type) {
  if (type !== "bar" && type !== "line") return {};
  const t = getChartTheme();
  return {
    x: {
      ticks: { color: t.textColor },
      grid:  { color: t.gridColor }
    },
    y: {
      beginAtZero: true,
      ticks: { color: t.textColor },
      grid:  { color: t.gridColor }
    }
  };
}

/**
 * renderChart (named canvas version)
 * Render or update a chart by canvas ID.
 *
 * @param {string} canvasId  - id of the <canvas> element
 * @param {string} type      - "bar" | "pie" | "doughnut" | "line" etc.
 * @param {Array}  labels    - chart labels
 * @param {Array}  data      - numeric values
 * @param {string} title     - optional chart title
 * @param {Array}  colors    - optional custom background colors
 */
function renderChart(canvasId, type, labels, data, title = "", colors = null) {
  const canvasEl = document.getElementById(canvasId);
  if (!canvasEl) return;

  applyChartDefaults();
  const t   = getChartTheme();
  const ctx = canvasEl.getContext("2d");

  // Destroy old instance
  if (charts[canvasId]) {
    charts[canvasId].destroy();
    delete charts[canvasId];
  }

  charts[canvasId] = new Chart(ctx, {
    type: type,
    data: {
      labels: labels,
      datasets: [{
        label:           title,
        data:            data,
        backgroundColor: colors || generateColors(data.length),
        borderColor:     generateBorderColors(data.length),
        borderWidth:     1.5,
        borderRadius:    type === "bar" ? 6 : 0,
        hoverOffset:     type !== "bar" ? 8 : 0
      }]
    },
    options: {
      responsive: true,
      animation: { duration: 600, easing: "easeOutQuart" },
      plugins: {
        legend: {
          display:  type !== "bar",
          position: "top",
          labels:   { color: t.titleColor, padding: 16, font: { size: 12 } }
        },
        title: {
          display: !!title,
          text:    title,
          color:   t.titleColor,
          font:    { size: 14, weight: "700", family: "'Syne', sans-serif" },
          padding: { bottom: 16 }
        }
      },
      scales: buildScales(type)
    }
  });
}

/**
 * renderEmotionChart (emotions bar chart — base.html #analysisChart)
 * Called with a data object that has a .emotions property.
 *
 * @param {object} data - API response containing data.emotions
 */
function renderEmotionChart(data) {
  const chartContainer = document.getElementById("chartContainer");
  const canvasEl       = document.getElementById("analysisChart");

  if (!data || !data.emotions || !canvasEl) {
    if (chartContainer) chartContainer.style.display = "none";
    return;
  }

  chartContainer.style.display = "block";

  applyChartDefaults();
  const t      = getChartTheme();
  const labels = Object.keys(data.emotions);
  const values = Object.values(data.emotions).map(v => parseFloat(v.toFixed(3)));
  const ctx    = canvasEl.getContext("2d");

  if (analysisChart) {
    analysisChart.destroy();
    analysisChart = null;
  }

  analysisChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: labels,
      datasets: [{
        label:           "Emotion Scores",
        data:            values,
        backgroundColor: generateColors(values.length),
        borderColor:     generateBorderColors(values.length),
        borderWidth:     1.5,
        borderRadius:    6
      }]
    },
    options: {
      responsive: true,
      animation: { duration: 700, easing: "easeOutQuart" },
      plugins: {
        legend: { display: false },
        title: {
          display: true,
          text:    "Emotion Distribution",
          color:   t.titleColor,
          font:    { size: 14, weight: "700", family: "'Syne', sans-serif" },
          padding: { bottom: 16 }
        }
      },
      scales: buildScales("bar")
    }
  });
}

/**
 * clearChart — destroy a named chart
 * @param {string} canvasId
 */
function clearChart(canvasId) {
  if (charts[canvasId]) {
    charts[canvasId].destroy();
    delete charts[canvasId];
  }
}

// ── Re-apply theme on mode toggle ────────────────────────────
const modeToggleBtn = document.getElementById("modeToggle");
if (modeToggleBtn) {
  modeToggleBtn.addEventListener("click", () => {
    // Small delay to let body class flip first
    setTimeout(() => {
      applyChartDefaults();
      // Re-render any active named charts with updated theme
      Object.keys(charts).forEach(id => {
        const chart = charts[id];
        if (chart) {
          const t = getChartTheme();
          chart.options.plugins.title.color        = t.titleColor;
          chart.options.scales?.x && (chart.options.scales.x.ticks.color = t.textColor);
          chart.options.scales?.x && (chart.options.scales.x.grid.color  = t.gridColor);
          chart.options.scales?.y && (chart.options.scales.y.ticks.color = t.textColor);
          chart.options.scales?.y && (chart.options.scales.y.grid.color  = t.gridColor);
          chart.update("none");
        }
      });
      // Also update the emotions chart if active
      if (analysisChart) {
        const t = getChartTheme();
        analysisChart.options.plugins.title.color        = t.titleColor;
        analysisChart.options.scales.x.ticks.color       = t.textColor;
        analysisChart.options.scales.x.grid.color        = t.gridColor;
        analysisChart.options.scales.y.ticks.color       = t.textColor;
        analysisChart.options.scales.y.grid.color        = t.gridColor;
        analysisChart.update("none");
      }
    }, 50);
  });
}

// Apply defaults on load
document.addEventListener("DOMContentLoaded", applyChartDefaults);