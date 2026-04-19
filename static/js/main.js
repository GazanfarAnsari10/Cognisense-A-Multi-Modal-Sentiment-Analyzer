// Simple front-end interactions: dark toggle, forms, fetch APIs
document.addEventListener("DOMContentLoaded", () => {
  const body          = document.body;
  const darkToggle    = document.getElementById("modeToggle");
  const sidebar       = document.getElementById("sidebar");
  const sidebarToggle = document.getElementById("sidebarToggle");
  const main          = document.getElementById("main") || document.querySelector(".page-content");

  // ── Learn More (homepage only) ──────────────────────────────
  const learnMoreBtn    = document.getElementById('learnMoreBtn');
  const expandedContent = document.getElementById('expandedContent');
  const arrow           = document.getElementById('arrow');

  if (learnMoreBtn && expandedContent && arrow) {
    learnMoreBtn.addEventListener('click', function () {
      expandedContent.classList.toggle('show');
      arrow.classList.toggle('rotated');
      if (expandedContent.classList.contains('show')) {
        learnMoreBtn.querySelector('span:first-child').textContent = 'Show Less';
      } else {
        learnMoreBtn.querySelector('span:first-child').textContent = 'Learn More';
      }
    });
  }

  // ── Sidebar toggle ──────────────────────────────────────────
  if (sidebarToggle) {
    sidebarToggle.addEventListener("click", () => {
      sidebar.classList.toggle("active");
      if (main) main.classList.toggle("shift");
    });
  }

  // ── Highlight active nav link ───────────────────────────────
  const navLinks = document.querySelectorAll('.nav-item');
  navLinks.forEach(link => {
    if (link.getAttribute('href') === window.location.pathname) {
      link.classList.add('active');
    }
  });

  // ── Dark / Light mode ───────────────────────────────────────
  const iconMoon = darkToggle?.querySelector('.icon-moon');
  const iconSun  = darkToggle?.querySelector('.icon-sun');

  function applyTheme(isDark) {
    if (isDark) {
      body.classList.add("dark");
      body.classList.remove("light");
      if (iconMoon) iconMoon.style.display = 'none';
      if (iconSun)  iconSun.style.display  = 'block';
    } else {
      body.classList.remove("dark");
      body.classList.add("light");
      if (iconMoon) iconMoon.style.display = 'block';
      if (iconSun)  iconSun.style.display  = 'none';
    }
  }

  // Restore from localStorage
  const savedTheme = localStorage.getItem("theme");
  applyTheme(savedTheme === "dark");

  if (darkToggle) {
    darkToggle.addEventListener("click", () => {
      const isDark = !body.classList.contains("dark");
      applyTheme(isDark);
      localStorage.setItem("theme", isDark ? "dark" : "light");
    });
  }

  // ── Text form ───────────────────────────────────────────────
  const textForm = document.getElementById("textForm");
  if (textForm) {
    textForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const text   = document.getElementById("textInput").value;
      const resBox = document.getElementById("result");
      resBox.innerHTML = "<p>Analyzing text…</p>";
      const form = new URLSearchParams();
      form.append("text", text);
      try {
        const resp = await fetch("/api/analyze/text", {
          method: "POST",
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: form.toString()
        });
        const data = await resp.json();
        if (data.error) resBox.innerHTML = "<b>Error:</b> " + data.error;
        else resBox.innerHTML = renderAnalysis(data);
      } catch (e) {
        resBox.innerHTML = "Request failed: " + e.message;
      }
    });
  }

  // ── Audio form ──────────────────────────────────────────────
  const audioForm = document.getElementById("audioForm");
  if (audioForm) {
    audioForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fileInput = document.getElementById("audioFile");
      const resBox    = document.getElementById("result");
      if (!fileInput.files.length) { resBox.innerHTML = "Select a file."; return; }
      resBox.innerHTML = "Uploading and analyzing audio…";
      const fd = new FormData();
      fd.append("file", fileInput.files[0]);
      try {
        const resp = await fetch("/api/analyze/audio", { method: "POST", body: fd });
        const data = await resp.json();
        if (data.error) resBox.innerHTML = "<b>Error:</b> " + data.error;
        else resBox.innerHTML = renderAudioAnalysis(data);
      } catch (e) {
        resBox.innerHTML = "Request failed: " + e.message;
      }
    });
  }

  // ── Video form ──────────────────────────────────────────────
  const videoForm = document.getElementById("videoForm");
  if (videoForm) {
    videoForm.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fileInput = document.getElementById("videoFile");
      const resBox    = document.getElementById("result");
      if (!fileInput.files.length) { resBox.innerHTML = "Select a file."; return; }
      resBox.innerHTML = "Uploading and analyzing video (may take longer)…";
      const fd = new FormData();
      fd.append("file", fileInput.files[0]);
      try {
        const resp = await fetch("/api/analyze/video", { method: "POST", body: fd });
        const data = await resp.json();
        if (data.error) resBox.innerHTML = "<b>Error:</b> " + data.error;
        else resBox.innerHTML = renderVideoAnalysis(data);
      } catch (e) {
        resBox.innerHTML = "Request failed: " + e.message;
      }
    });
  }

  // ── Render helpers (ALL ORIGINAL — UNTOUCHED) ───────────────
  function renderAnalysis(data) {
    let out = "<h3>Overall</h3>";
    out += `<p><b>Label:</b> ${data.overall.label} — score ${data.overall.score}</p><br>`;
    out += "<h4>Sentences</h4><ul>";
    data.sentence_level.forEach(s => out += `<li>${s.label} (${s.score}): ${s.sentence}</li><br>`);
    out += "</ul><h4>Words (sample)</h4><div style='max-height:180px;overflow:auto'><ul>";
    data.word_level.slice(0, 80).forEach(w => out += `<li>${w.word} — ${w.label} (${w.score})</li>`);
    out += "</ul></div><br>";
    if (data.ai_reasoning) {
      let reasoning = data.ai_reasoning;
      out += "<h4>AI Reasoning</h4>";
      if (reasoning.summary) out += `<p><b>Summary:</b> ${reasoning.summary}</p><br>`;
      if (reasoning.top_positive_words?.length) {
        out += "<p><b>Positive words:</b></p><ul>";
        reasoning.top_positive_words.forEach(w => out += `<li>${w.word} (score: ${w.score.toFixed(3)})</li>`);
        out += "</ul><br>";
      }
      if (reasoning.top_negative_words?.length) {
        out += "<p><b>Negative words:</b></p><ul>";
        reasoning.top_negative_words.forEach(w => out += `<li>${w.word} (score: ${w.score.toFixed(3)})</li>`);
        out += "</ul><br>";
      }
      if (reasoning.top_positive_sentences?.length) {
        out += "<p><b>Top Positive Sentences:</b></p><ul>";
        reasoning.top_positive_sentences.forEach(s => out += `<li>"${s.sentence}" (score: ${s.score.toFixed(3)})</li><br>`);
        out += "</ul>";
      }
      if (reasoning.top_negative_sentences?.length) {
        out += "<p><b>Top Negative Sentences:</b></p><ul>";
        reasoning.top_negative_sentences.forEach(s => out += `<li>"${s.sentence}" (score: ${s.score.toFixed(3)})</li>`);
        out += "</ul><br>";
      }
      if (reasoning.why) out += `<p><b>Why:</b> ${reasoning.why}</p>`;
    }
    return out;
  }

  function renderAudioAnalysis(data) {
    let out = `<h3>Transcription</h3><p>${data.transcription || "(no transcription)"}</p><br>`;
    if (data.analysis && data.analysis.overall) {
      out += renderAnalysis(data.analysis);
    } else if (data.analysis) {
      out += "<pre>" + JSON.stringify(data.analysis, null, 2) + "</pre>";
    }
    return out;
  }

  function renderVideoAnalysis(data) {
    if (!data) return "<p><b>No analysis returned.</b></p>";
    let out = "<h3>Video Analysis</h3><br>";
    if (data.audio_analysis) {
      if (data.audio_analysis.transcription) {
        out += `<p><b>Transcription:</b> ${data.audio_analysis.transcription}</p><br>`;
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
      if (fb.summary) out += `<p><b>Summary:</b> ${fb.summary}</p><br>`;
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

  // ── Carousel ────────────────────────────────────────────────
  (function initCarousel() {
    const track   = document.getElementById("carouselTrack");
    const dotsWrap = document.getElementById("carouselDots");
    if (!track) return;

    const leftBtn  = document.querySelector(".carousel-arrow.left");
    const rightBtn = document.querySelector(".carousel-arrow.right");
    const cards    = track.querySelectorAll(".carousel-card");

    let autoScrollInterval = null;
    const autoScrollDelay  = 2800;

    // ── Dot indicators ───────────────────────────────────────
    let dots = [];
    if (dotsWrap && cards.length) {
      cards.forEach((_, i) => {
        const dot = document.createElement('button');
        dot.className = 'carousel-dot' + (i === 0 ? ' active' : '');
        dot.setAttribute('aria-label', `Go to slide ${i + 1}`);
        dot.addEventListener('click', () => {
          stopAutoScroll();
          const card = cards[i];
          track.scrollTo({ left: card.offsetLeft - track.offsetLeft, behavior: 'smooth' });
          startAutoScroll();
        });
        dotsWrap.appendChild(dot);
        dots.push(dot);
      });

      // Update active dot on scroll
      track.addEventListener('scroll', () => {
        const center = track.scrollLeft + track.clientWidth / 2;
        let closest = 0;
        let minDist = Infinity;
        cards.forEach((card, i) => {
          const cardCenter = card.offsetLeft - track.offsetLeft + card.offsetWidth / 2;
          const dist = Math.abs(center - cardCenter);
          if (dist < minDist) { minDist = dist; closest = i; }
        });
        dots.forEach((d, i) => d.classList.toggle('active', i === closest));
      }, { passive: true });
    }

    // ── Scroll helpers (ORIGINAL LOGIC — UNTOUCHED) ─────────
    function getCardScrollAmount() {
      const card = track.querySelector(".carousel-card");
      if (!card) return 300;
      const style  = window.getComputedStyle(card);
      const margin = parseFloat(style.marginRight || 20);
      return card.offsetWidth + margin;
    }

    function scrollNext() {
      const amount    = getCardScrollAmount();
      const maxScroll = track.scrollWidth - track.clientWidth;
      if (track.scrollLeft + amount >= maxScroll - 2) {
        track.scrollTo({ left: 0, behavior: "smooth" });
      } else {
        track.scrollBy({ left: amount, behavior: "smooth" });
      }
    }

    function scrollPrev() {
      const amount = getCardScrollAmount();
      if (track.scrollLeft - amount <= 2) {
        track.scrollTo({ left: track.scrollWidth, behavior: "smooth" });
      } else {
        track.scrollBy({ left: -amount, behavior: "smooth" });
      }
    }

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

    rightBtn?.addEventListener("click", () => { stopAutoScroll(); scrollNext(); startAutoScroll(); });
    leftBtn?.addEventListener("click",  () => { stopAutoScroll(); scrollPrev(); startAutoScroll(); });

    track.addEventListener("pointerdown", (e) => { e.preventDefault(); });

    ["touchstart", "keydown"].forEach(evt => {
      window.addEventListener(evt, () => stopAutoScroll());
    });

    startAutoScroll();
  })();

}); // end DOMContentLoaded