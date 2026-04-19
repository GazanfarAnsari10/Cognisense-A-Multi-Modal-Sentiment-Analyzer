// static/js/text.js

document.addEventListener("DOMContentLoaded", () => {
    const form          = document.getElementById("textForm");
    const textInput     = document.getElementById("textInput");
    const fileInput     = document.getElementById("fileInput");
    const resultSection = document.getElementById("result");

    // ── File name display (new UI element, replaces missing #text-name) ──
    // The new HTML uses #fileNameText + #fileNameDisplay instead of #text-name.
    // We wire into those if present; if neither exists we skip silently.
    const fileNameDisplay = document.getElementById("fileNameDisplay");
    const fileNameText    = document.getElementById("fileNameText");

    if (fileInput) {
        fileInput.addEventListener("change", function () {
            if (this.files && this.files.length > 0) {
                // Update the new styled file display
                if (fileNameText)    fileNameText.textContent = this.files[0].name;
                if (fileNameDisplay) fileNameDisplay.style.display = "flex";
            } else {
                if (fileNameDisplay) fileNameDisplay.style.display = "none";
            }
        });
    }

    // ── Form submit (ALL ORIGINAL LOGIC — UNTOUCHED) ─────────────────────
    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        resultSection.innerHTML = "<p>Analyzing...</p>";

        const formData = new FormData();

        // Check if a file is uploaded
        if (fileInput.files.length > 0) {
            formData.append("file", fileInput.files[0]);
        } else if (textInput.value.trim() !== "") {
            formData.append("text", textInput.value.trim());
        } else {
            resultSection.innerHTML = "<p style='color:red;'>Please provide text or upload a file.</p>";
            return;
        }

        try {
            const resp = await fetch("/api/analyze/text", {
                method: "POST",
                body: formData
            });

            const data = await resp.json();

            if (data.error) {
                resultSection.innerHTML = `<p style='color:red;'>Error: ${data.error}</p>`;
                return;
            }

            renderResult(data);
        } catch (err) {
            console.error(err);
            resultSection.innerHTML = "<p style='color:red;'>An error occurred while analyzing.</p>";
        }
    });

    // ── Render helpers (ALL ORIGINAL — UNTOUCHED) ────────────────────────
    function renderResult(data) {
        let html = "";

        // Overall sentiment
        if (data.overall) {
            html += `<h3>Overall Sentiment:</h3>
                     <p>Label: <strong>${data.overall.label}</strong>, Score: ${data.overall.score}</p>`;
        }

        // Word-level
        if (data.word_level) {
            html += `<h3>Word-level Sentiment:</h3><ul>`;
            data.word_level.forEach(w => {
                html += `<li>${w.word}: ${w.label} (${w.score})</li>`;
            });
            html += `</ul>`;
        }

        // Sentence-level
        if (data.sentence_level) {
            html += `<h3>Sentence-level Sentiment:</h3><ul>`;
            data.sentence_level.forEach(s => {
                html += `<li>${s.sentence}: ${s.label} (${s.score})</li>`;
            });
            html += `</ul>`;
        }

        // AI reasoning
        if (data.ai_reasoning) {
            html += `<h3>AI Reasoning:</h3>
                     <p>${data.ai_reasoning.summary}</p>`;

            if (data.ai_reasoning.top_positive_sentences?.length) {
                html += "<p><strong>Top Positive Sentences:</strong></p><ul>";
                data.ai_reasoning.top_positive_sentences.forEach(s => {
                    html += `<li>${s.sentence} (${s.score})</li>`;
                });
                html += "</ul>";
            }

            if (data.ai_reasoning.top_negative_sentences?.length) {
                html += "<p><strong>Top Negative Sentences:</strong></p><ul>";
                data.ai_reasoning.top_negative_sentences.forEach(s => {
                    html += `<li>${s.sentence} (${s.score})</li>`;
                });
                html += "</ul>";
            }

            if (data.ai_reasoning.top_positive_words?.length) {
                html += "<p><strong>Top Positive Words:</strong></p><ul>";
                data.ai_reasoning.top_positive_words.forEach(w => {
                    html += `<li>${w.word} (${w.score})</li>`;
                });
                html += "</ul>";
            }

            if (data.ai_reasoning.top_negative_words?.length) {
                html += "<p><strong>Top Negative Words:</strong></p><ul>";
                data.ai_reasoning.top_negative_words.forEach(w => {
                    html += `<li>${w.word} (${w.score})</li>`;
                });
                html += "</ul>";
            }

            if (data.ai_reasoning.why) {
                html += `<p><em>${data.ai_reasoning.why}</em></p>`;
            }
        }

        resultSection.innerHTML = html;
    }
});