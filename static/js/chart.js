let analysisChart = null;

// static/js/chart.js

let charts = {}; // store active charts by element id

/**
 * Render or update a chart
 * @param {string} canvasId - The id of the canvas element
 * @param {string} type - "bar", "pie", "doughnut", "line", etc.
 * @param {Array} labels - Array of labels for the chart
 * @param {Array} data - Array of numeric values
 * @param {string} title - Title of the chart
 * @param {Array} colors - Optional custom colors
 */
function renderChart(canvasId, type, labels, data, title = "", colors = null) {
  const ctx = document.getElementById(canvasId).getContext("2d");

  // Destroy old chart if it exists
  if (charts[canvasId]) {
    charts[canvasId].destroy();
  }

  charts[canvasId] = new Chart(ctx, {
    type: type,
    data: {
      labels: labels,
      datasets: [
        {
          label: title,
          data: data,
          backgroundColor: colors || generateColors(data.length),
          borderWidth: 1
        }
      ]
    },
    options: {
      responsive: true,
      plugins: {
        legend: {
          display: type !== "bar", // Hide legend for bar charts
          position: "top"
        },
        title: {
          display: !!title,
          text: title
        }
      },
      scales: type === "bar" ? {
        y: {
          beginAtZero: true
        }
      } : {}
    }
  });
}

/**
 * Generate a set of nice colors automatically
 */
function generateColors(count) {
  const baseColors = [
    "#2563eb", "#16a34a", "#dc2626", "#f59e0b",
    "#9333ea", "#0ea5e9", "#d946ef", "#64748b"
  ];
  let colors = [];
  for (let i = 0; i < count; i++) {
    colors.push(baseColors[i % baseColors.length]);
  }
  return colors;
}

/**
 * Clear chart from a canvas
 */
function clearChart(canvasId) {
  if (charts[canvasId]) {
    charts[canvasId].destroy();
    delete charts[canvasId];
  }
}


// Render chart dynamically
function renderChart(data) {
  const chartContainer = document.getElementById("chartContainer");
  const ctx = document.getElementById("analysisChart").getContext("2d");

  if (!data || !data.emotions) {
    chartContainer.style.display = "none";
    return;
  }

  chartContainer.style.display = "block";

  const labels = Object.keys(data.emotions);
  const values = Object.values(data.emotions).map(v => parseFloat(v.toFixed(3)));

  if (analysisChart) analysisChart.destroy(); // destroy previous chart

  analysisChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        label: 'Emotion Scores',
        data: values,
        backgroundColor: [
          'rgba(54, 162, 235, 0.6)',
          'rgba(255, 99, 132, 0.6)',
          'rgba(255, 206, 86, 0.6)',
          'rgba(75, 192, 192, 0.6)',
          'rgba(153, 102, 255, 0.6)',
          'rgba(255, 159, 64, 0.6)',
          'rgba(201, 203, 207, 0.6)'
        ],
        borderColor: 'rgba(0,0,0,0.1)',
        borderWidth: 1
      }]
    },
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
        title: { display: true, text: 'Emotion Distribution' }
      },
      scales: {
        y: { beginAtZero:true }
      }
    }
  });
}
