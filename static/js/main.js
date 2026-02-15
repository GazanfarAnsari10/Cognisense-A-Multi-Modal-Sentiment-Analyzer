// Simple front-end interactions: dark toggle, forms, fetch APIs
document.addEventListener("DOMContentLoaded", ()=> {
  const body = document.body;
  const darkToggle = document.getElementById("modeToggle");
  const sidebar = document.getElementById("sidebar");
  const sidebarToggle = document.getElementById("sidebarToggle");
    const learnMoreBtn = document.getElementById('learnMoreBtn');
  const expandedContent = document.getElementById('expandedContent');
  const arrow = document.getElementById('arrow');
  

  
// restore from localStorage
 sidebarToggle.addEventListener("click", () => {
 sidebar.classList.toggle("active");
 if (main) main.classList.toggle('shift');

});


  if (localStorage.getItem("theme") === "dark") {
  body.classList.add("dark");
  darkToggle.textContent = "☀︎"; // Sun icon for light mode toggle
} else {
  darkToggle.textContent = "☽"; // Moon icon for dark mode toggle
}

darkToggle.addEventListener("click", () => {
  body.classList.toggle("dark");

  if (body.classList.contains("dark")) {
    localStorage.setItem("theme", "dark");
    darkToggle.textContent = "☀︎";
  } else {
    localStorage.setItem("theme", "light");
    darkToggle.textContent = "☽";
  }
});

  // Text form
  const textForm = document.getElementById("textForm");
  if (textForm) {
    textForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const text = document.getElementById("textInput").value;
      const resBox = document.getElementById("result");
      resBox.innerHTML = "Analyzing text...";
      const form = new URLSearchParams();
      form.append("text", text);
      try {
        const resp = await fetch("/api/analyze/text", { method:"POST", headers: {'Content-Type':'application/x-www-form-urlencoded'}, body: form.toString() });
        const data = await resp.json();
        if (data.error) resBox.innerHTML = "<b>Error:</b> " + data.error;
        else resBox.innerHTML = renderAnalysis(data);
      } catch (e) {
        resBox.innerHTML = "Request failed: " + e.message;
      }
    });
  }

  // Audio form
  const audioForm = document.getElementById("audioForm");
  if (audioForm) {
    audioForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fileInput = document.getElementById("audioFile");
      const resBox = document.getElementById("result");
      if (!fileInput.files.length) { resBox.innerHTML = "Select a file."; return; }
      resBox.innerHTML = "Uploading and analyzing audio...";
      const fd = new FormData();
      fd.append("file", fileInput.files[0]);
      try {
        const resp = await fetch("/api/analyze/audio", { method:"POST", body:fd });
        const data = await resp.json();
        if (data.error) resBox.innerHTML = "<b>Error:</b> " + data.error;
        else resBox.innerHTML = renderAudioAnalysis(data);
      } catch (e) {
        resBox.innerHTML = "Request failed: " + e.message;
      }
    });
  }

  // Video form
  const videoForm = document.getElementById("videoForm");
  if (videoForm) {
    videoForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fileInput = document.getElementById("videoFile");
      const resBox = document.getElementById("result");
      if (!fileInput.files.length) { resBox.innerHTML = "Select a file."; return; }
      resBox.innerHTML = "Uploading and analyzing video (may take longer)...";
      const fd = new FormData();
      fd.append("file", fileInput.files[0]);
      try {
        const resp = await fetch("/api/analyze/video", { method:"POST", body:fd });
        const data = await resp.json();
        if (data.error) resBox.innerHTML = "<b>Error:</b> " + data.error;
        else resBox.innerHTML = renderVideoAnalysis(data);
      } catch (e) {
        resBox.innerHTML = "Request failed: " + e.message;
      }
    });
  }

  // Render helpers
  function renderAnalysis(data){
    let out = "<h3>Overall</h3>";
    out += `<p><b>Label:</b> ${data.overall.label} — score ${data.overall.score}</p>`;
    out += "<h4>Sentences</h4><ul>";
    data.sentence_level.forEach(s => out += `<li>${s.label} (${s.score}): ${s.sentence}</li>`);
    out += "</ul><h4>Words (sample)</h4><div style='max-height:180px;overflow:auto'><ul>";
    data.word_level.slice(0,80).forEach(w => out += `<li>${w.word} — ${w.label} (${w.score})</li>`);
    out += "</ul></div>";
    if (data.ai_reasoning) {
      let reasoning = data.ai_reasoning;
      out += "<h4>AI Reasoning</h4>";
      if (reasoning.summary) out += `<p><b>Summary:</b> ${reasoning.summary}</p>`;
      if (reasoning.top_positive_words?.length) {
        out += "<p><b>Positive words:</b></p><ul>";
        reasoning.top_positive_words.forEach(w => out += `<li>${w.word} (score: ${w.score.toFixed(3)})</li>`);
        out += "</ul>";
      }
      if (reasoning.top_negative_words?.length) {
        out += "<p><b>Negative words:</b></p><ul>";
        reasoning.top_negative_words.forEach(w => out += `<li>${w.word} (score: ${w.score.toFixed(3)})</li>`);
        out += "</ul>";
      }
      if (reasoning.top_positive_sentences?.length) {
        out += "<p><b>Top Positive Sentences:</b></p><ul>";
        reasoning.top_positive_sentences.forEach(s => out += `<li>"${s.sentence}" (score: ${s.score.toFixed(3)})</li>`);
        out += "</ul>";
      }
      if (reasoning.top_negative_sentences?.length) {
        out += "<p><b>Top Negative Sentences:</b></p><ul>";
        reasoning.top_negative_sentences.forEach(s => out += `<li>"${s.sentence}" (score: ${s.score.toFixed(3)})</li>`);
        out += "</ul>";
      }
      if (reasoning.why) out += `<p><b>Why:</b> ${reasoning.why}</p>`;
    }
    return out;
  }

  function renderAudioAnalysis(data){
    let out = `<h3>Transcription</h3><p>${data.transcription || "(no transcription)"}</p>`;
    if (data.analysis && data.analysis.overall){
      out += renderAnalysis(data.analysis);
    } else if (data.analysis){
      out += "<pre>"+JSON.stringify(data.analysis, null, 2)+"</pre>";
    }
    return out;
  }

  function renderVideoAnalysis(data) {
    if (!data) return "<p><b>No analysis returned.</b></p>";
    let out = "<h3>Video Analysis</h3>";
    if (data.audio_analysis) {
      out += "<h4>Audio Analysis</h4>";
      if (data.audio_analysis.transcription) {
        out += `<p><b>Transcription:</b> ${data.audio_analysis.transcription}</p>`;
      }
      if (data.audio_analysis.analysis) {
        out += renderAnalysis(data.audio_analysis.analysis);
      }
    }
    if (data.frame_brightness) {
      let fb = data.frame_brightness;
      out += "<h4>Frame Brightness Heuristic</h4>";
      if (fb.frames?.length > 0) {
        out += "<ul>";
        fb.frames.forEach((frame, idx) => {
          out += `<li>Frame ${idx}: brightness = ${frame.brightness?.toFixed(2) ?? "N/A"}</li>`;
        });
        out += "</ul>";
      }
      if (fb.summary) out += `<p><b>Summary:</b> ${fb.summary}</p>`;
    }
    if (data.ai_reasoning) {
      let reasoning = data.ai_reasoning;
      out += "<h4>AI Reasoning</h4>";
      if (reasoning.summary) out += `<p><b>Summary:</b> ${reasoning.summary}</p>`;
      if (reasoning.top_positive_words?.length) {
        out += "<p><b>Positive words:</b></p><ul>";
        reasoning.top_positive_words.forEach(w => out += `<li>${w.word} (score: ${w.score.toFixed(3)})</li>`);
        out += "</ul>";
      }
      if (reasoning.top_negative_words?.length) {
        out += "<p><b>Negative words:</b></p><ul>";
        reasoning.top_negative_words.forEach(w => out += `<li>${w.word} (score: ${w.score.toFixed(3)})</li>`);
        out += "</ul>";
      }
      if (reasoning.why) out += `<p><b>Why:</b> ${reasoning.why}</p>`;
    }
    return out;
  }
});


learnMoreBtn.addEventListener('click', function() {
expandedContent.classList.toggle('show');
arrow.classList.toggle('rotated');
if (expandedContent.classList.contains('show')) {
learnMoreBtn.querySelector('span:first-child').textContent = 'Show Less';
            } else {
                learnMoreBtn.querySelector('span:first-child').textContent = 'Learn More';
            }
        });
// ----------------------
// CLEAN, FIXED CAROUSEL MODULE
// ----------------------
(function initCarousel() {
    const track = document.getElementById("carouselTrack");
    if (!track) return;

    const leftBtn = document.querySelector(".carousel-arrow.left");
    const rightBtn = document.querySelector(".carousel-arrow.right");

    let autoScrollInterval = null;
    const autoScrollDelay = 2800;

    // SCROLL BY EXACT CARD WIDTH
   function getCardScrollAmount() {
        const card = track.querySelector(".carousel-card");
        if (!card) return 300;
        const style = window.getComputedStyle(card);
        const margin = parseFloat(style.marginRight || 20);
        return card.offsetWidth + margin;
    }

    // MOVE TO NEXT CARD (NO SNAP)
    function scrollNext() {
        const amount = getCardScrollAmount();
        const maxScroll = track.scrollWidth - track.clientWidth;

        if (track.scrollLeft + amount >= maxScroll - 2) {
            // Loop smoothly
            track.scrollTo({ left: 0, behavior: "smooth" });
        } else {
            track.scrollBy({ left: amount, behavior: "smooth" });
        }
    }

    // MOVE TO PREVIOUS CARD (NO SNAP)
    function scrollPrev() {
        const amount = getCardScrollAmount();

        if (track.scrollLeft - amount <= 2) {
            // Loop smoothly to end
            track.scrollTo({ left: track.scrollWidth, behavior: "smooth" });
        } else {
            track.scrollBy({ left: -amount, behavior: "smooth" });
        }
    }

    // AUTO-SCROLL
    function startAutoScroll() {
        stopAutoScroll();
        autoScrollInterval = setInterval(scrollNext, autoScrollDelay);
    }

    function stopAutoScroll() {
        if (autoScrollInterval) {
            clearInterval(autoScrollInterval);
            autoScrollInterval = null;
        }
    }

    // ARROW EVENTS (NO SNAP INTERFERENCE)
    rightBtn?.addEventListener("click", () => {
        stopAutoScroll();
        scrollNext();
        startAutoScroll();
    });

    leftBtn?.addEventListener("click", () => {
        stopAutoScroll();
        scrollPrev();
        startAutoScroll();
    });

    // REMOVE pointerdrag + snapping features (cause of “chaotic movement”)
    track.addEventListener("pointerdown", (e) => {
        // Prevent drag behavior entirely
        e.preventDefault();
    });

    // REMOVE pause-on-hover (your request)
    // track.addEventListener("mouseenter", stopAutoScroll);
    // track.addEventListener("mouseleave", startAutoScroll);

    // STOP auto-scroll when user interacts (optional)
    ["touchstart", "keydown"].forEach(evt => {
        window.addEventListener(evt, () => stopAutoScroll());
    });

    // INIT
    startAutoScroll();
})();


