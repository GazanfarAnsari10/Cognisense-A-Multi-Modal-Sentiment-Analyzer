// static/js/chart.js

// ── Chart registry ────────────────────────────────────────────
let charts = {};        // named charts by canvasId

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

function isFiniteChartValue(value) {
  return (typeof value === "number" || (typeof value === "string" && value.trim() !== "")) &&
    Number.isFinite(Number(value));
}

function getSentimentAnalysis(data) {
  if (data.analysis && typeof data.analysis === "object") return data.analysis;
  if (data.audio_analysis) {
    if (data.audio_analysis.error) return {};
    if (data.audio_analysis.analysis && typeof data.audio_analysis.analysis === "object") {
      return data.audio_analysis.analysis;
    }
  }
  return data;
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
  if (!canvasEl || typeof Chart === "undefined" ||
      !Array.isArray(labels) || !Array.isArray(data) ||
      !labels.length || labels.length !== data.length ||
      !data.every(value => Number.isFinite(value))) {
    clearChart(canvasId);
    return false;
  }

  applyChartDefaults();
  const t   = getChartTheme();
  const config = {
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
  };
  const existingChart = charts[canvasId];
  if (existingChart && existingChart.config.type === type) {
    existingChart.data.labels = labels;
    existingChart.data.datasets = config.data.datasets;
    existingChart.options = config.options;
    existingChart.update("none");
  } else {
    if (existingChart) existingChart.destroy();
    charts[canvasId] = new Chart(canvasEl.getContext("2d"), config);
  }
  return true;
}

/**
 * renderEmotionChart (emotions bar chart — base.html #analysisChart)
 * Called with a data object that has a .emotions property.
 *
 * @param {object} data - API response containing data.emotions
 */
function renderEmotionChart(data) {
  const emotions = data && data.emotions && typeof data.emotions === "object"
    ? Object.entries(data.emotions).filter(([, value]) =>
        typeof value === "number" && Number.isFinite(value))
    : [];
  if (!emotions.length) return false;
  return renderChart(
    "analysisChart",
    "bar",
    emotions.map(([label]) => label),
    emotions.map(([, value]) => Number(value.toFixed(3))),
    "Emotion Distribution"
  );
}

function renderAnalyzerChart(data) {
  const container = document.getElementById("chartContainer");
  const detailCanvasId = "analysisDetailChart";
  const detailCanvas = document.getElementById(detailCanvasId);
  let rendered = false;
  let renderedDetail = false;

  const clearDetailChart = () => {
    clearChart(detailCanvasId);
    if (detailCanvas) detailCanvas.remove();
  };
  const hideCharts = () => {
    clearChart("analysisChart");
    clearDetailChart();
    if (container) container.style.display = "none";
  };

  if (!data || typeof data !== "object" || typeof Chart === "undefined") {
    hideCharts();
    return false;
  }

  const emotions = data.emotions && typeof data.emotions === "object"
    ? Object.entries(data.emotions).filter(([, value]) =>
        typeof value === "number" && Number.isFinite(value))
    : [];
  if (emotions.length) {
    rendered = renderEmotionChart({ emotions: Object.fromEntries(emotions) });
  } else {
    const analysis = getSentimentAnalysis(data);
    const sentimentItems = Array.isArray(analysis.word_level) && analysis.word_level.length
      ? analysis.word_level
      : Array.isArray(analysis.sentence_level) ? analysis.sentence_level : [];
    const counts = { positive: 0, neutral: 0, negative: 0 };
    sentimentItems.forEach(item => {
      const label = item && typeof item.label === "string" ? item.label.toLowerCase() : "";
      if (Object.prototype.hasOwnProperty.call(counts, label)) counts[label]++;
    });
    const sentimentCount = Object.values(counts).reduce((sum, value) => sum + value, 0);

    if (sentimentCount) {
      rendered = renderChart(
        "analysisChart", "bar",
        Object.keys(counts), Object.values(counts),
        "Sentiment Distribution"
      );
    } else if (analysis.overall && isFiniteChartValue(analysis.overall.score)) {
      rendered = renderChart(
        "analysisChart", "bar",
        [String(analysis.overall.label || "Overall")],
        [Number(analysis.overall.score)],
        "Overall Sentiment Score"
      );
    } else {
      const frames = data.frame_brightness && Array.isArray(data.frame_brightness.frames)
        ? data.frame_brightness.frames.filter(frame => frame &&
            isFiniteChartValue(frame.time) && isFiniteChartValue(frame.brightness))
        : [];
      if (frames.length) {
        rendered = renderChart(
          "analysisChart", "line",
          frames.map(frame => `${Number(frame.time).toFixed(1)}s`),
          frames.map(frame => Number(frame.brightness)),
          "Video Frame Brightness"
        );
      } else if (isFiniteChartValue(data.brightness)) {
        rendered = renderChart(
          "analysisChart", "bar", ["Brightness"], [Number(data.brightness)],
          "Current Frame Brightness"
        );
      }
    }
  }

  const videoFrames = data.frame_brightness && Array.isArray(data.frame_brightness.frames)
    ? data.frame_brightness.frames.filter(frame => frame &&
        isFiniteChartValue(frame.time) && isFiniteChartValue(frame.brightness))
    : [];
  if (videoFrames.length && emotions.length === 0) {
    const primaryAnalysis = getSentimentAnalysis(data);
    const hasSentiment = (Array.isArray(primaryAnalysis.word_level) &&
        primaryAnalysis.word_level.some(item => item && countsLabel(item.label))) ||
      (Array.isArray(primaryAnalysis.sentence_level) &&
      primaryAnalysis.sentence_level.some(item => item && countsLabel(item.label))) ||
    (primaryAnalysis.overall && countsLabel(primaryAnalysis.overall.label) &&
      isFiniteChartValue(primaryAnalysis.overall.score));
    if (hasSentiment) {
      let canvas = document.getElementById(detailCanvasId);
      if (!canvas && container) {
        canvas = document.createElement("canvas");
        canvas.id = detailCanvasId;
        canvas.setAttribute("aria-label", "Video frame brightness chart");
        container.appendChild(canvas);
      }
      if (canvas) {
        renderedDetail = renderChart(
          detailCanvasId, "line",
          videoFrames.map(frame => `${Number(frame.time).toFixed(1)}s`),
          videoFrames.map(frame => Number(frame.brightness)),
          "Video Frame Brightness"
        );
        rendered = renderedDetail || rendered;
      }
    }
  }

  if (!rendered) {
    hideCharts();
    return false;
  }
  if (container) container.style.display = "block";
  if (!renderedDetail) clearDetailChart();
  return true;
}

function countsLabel(label) {
  return typeof label === "string" &&
    ["positive", "neutral", "negative"].includes(label.toLowerCase());
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
    }, 50);
  });
}

// Apply defaults on load
document.addEventListener("DOMContentLoaded", applyChartDefaults);