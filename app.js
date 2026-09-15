(() => {
  const $ = (selector) => document.querySelector(selector);

  async function jsonRequest(url, options = {}) {
    let lastError;
    for (let attempt = 0; attempt < 2; attempt++) {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 90000);
      try {
        const response = await fetch(url, { ...options, credentials: "same-origin", cache: "no-store", signal: controller.signal });
        const text = await response.text();
        let data = {};
        try { data = text ? JSON.parse(text) : {}; } catch (_) {
          throw new Error(`Server returned an invalid response (${response.status}). Open the terminal for the Python error.`);
        }
        if (!response.ok || data.ok === false) throw new Error(data.error || `Request failed (${response.status})`);
        return data;
      } catch (err) {
        lastError = err;
        if (err.name === "AbortError") throw new Error("The request timed out. Check the Groq key, internet connection and terminal window.");
        if (err instanceof TypeError && attempt === 0) { await new Promise(r => setTimeout(r, 700)); continue; }
        if (err instanceof TypeError) throw new Error("Cannot reach KP NEXORA server. Keep start.bat running and use http://127.0.0.1:5000.");
        throw err;
      } finally { clearTimeout(timer); }
    }
    throw lastError || new Error("Request failed.");
  }

  function setBusy(form, busy, label = "Working…") {
    const button = form?.querySelector("button[type='submit'], button:not([type])");
    if (!button) return;
    if (busy) {
      button.dataset.originalText = button.textContent;
      button.disabled = true;
      button.textContent = label;
    } else {
      button.disabled = false;
      button.textContent = button.dataset.originalText || "Submit";
    }
  }

  function showStatus(form, message, ok = false) {
    let box = form.querySelector(".form-status");
    if (!box) {
      box = document.createElement("div");
      box.className = "form-status muted";
      form.appendChild(box);
    }
    box.textContent = message;
    box.classList.toggle("success", ok);
    box.classList.toggle("error", !ok);
  }

  const uploadForm = $("#uploadForm");
  if (uploadForm) uploadForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = uploadForm.querySelector("input[name='file']");
    if (!input?.files?.length) return showStatus(uploadForm, "Choose a CSV, Excel or JSON file first.");
    setBusy(uploadForm, true, "Analyzing…");
    try {
      const data = await jsonRequest("/api/upload", { method: "POST", body: new FormData(uploadForm) });
      showStatus(uploadForm, `Analysis complete — ${data.analysis.rows.toLocaleString()} rows, ${data.analysis.cols} columns.`, true);
      setTimeout(() => { window.location.href = "/analytics"; }, 250);
    } catch (err) {
      showStatus(uploadForm, err.message);
      setBusy(uploadForm, false);
    }
  });

  const pg = $("#postgresForm");
  const pgTest = $("#postgresTestBtn");
  if (pg && pgTest) pgTest.addEventListener("click", async () => {
    setBusy(pgTest, true, "Testing…");
    try {
      const d = await jsonRequest("/api/connect/postgres/test", { method: "POST", body: new FormData(pg) });
      showStatus(pg, d.message || "PostgreSQL connection successful.", true);
    } catch (err) { showStatus(pg, err.message); }
    finally { setBusy(pgTest, false); }
  });
  if (pg) pg.addEventListener("submit", async (e) => {
    e.preventDefault(); setBusy(pg, true, "Importing…");
    try {
      const d = await jsonRequest("/api/connect/postgres", { method: "POST", body: new FormData(pg) });
      showStatus(pg, `Imported ${d.analysis.rows.toLocaleString()} rows successfully.`, true);
      setTimeout(() => { window.location.href = "/analytics"; }, 250);
    } catch (err) { showStatus(pg, err.message); setBusy(pg, false); }
  });

  const gs = $("#googleForm");
  if (gs) gs.addEventListener("submit", async (e) => {
    e.preventDefault(); setBusy(gs, true, "Importing…");
    try {
      const d = await jsonRequest("/api/connect/google/public", { method: "POST", body: new FormData(gs) });
      showStatus(gs, `Imported ${d.analysis.rows.toLocaleString()} rows successfully.`, true);
      setTimeout(() => { window.location.href = "/analytics"; }, 250);
    } catch (err) { showStatus(gs, err.message); setBusy(gs, false); }
  });

  const gsp = $("#googlePrivateForm");
  if (gsp) gsp.addEventListener("submit", async (e) => {
    e.preventDefault(); setBusy(gsp, true, "Importing…");
    try {
      const d = await jsonRequest("/api/connect/google/private", { method: "POST", body: new FormData(gsp) });
      showStatus(gsp, `Imported ${d.analysis.rows.toLocaleString()} rows successfully.`, true);
      setTimeout(() => { window.location.href = "/analytics"; }, 250);
    } catch (err) { showStatus(gsp, err.message); setBusy(gsp, false); }
  });

  const aiStatus = $("#aiConnectionStatus");
  if (aiStatus) {
    fetch("/api/ai/status", { credentials: "same-origin" }).then(async r => {
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || `Status check failed (${r.status})`);
      aiStatus.textContent = d.connected
        ? `● Advanced AI ready · ${d.model}${d.web_search ? " · Web search ready" : " · Free mode"}`
        : "○ Advanced AI needs attention";
      aiStatus.classList.toggle("success", !!d.connected);
      aiStatus.classList.toggle("error", !d.connected);
      const detail = $("#aiConnectionDetail");
      if (detail) detail.textContent = d.detail || "";
    }).catch(err => {
      aiStatus.textContent = "○ Advanced AI status check failed";
      aiStatus.classList.add("error");
      const detail = $("#aiConnectionDetail");
      if (detail) detail.textContent = err.message;
    });
  }

  const aiTestButton = $("#aiTestButton");
  if (aiTestButton) aiTestButton.addEventListener("click", async () => {
    aiTestButton.disabled = true; aiTestButton.textContent = "Testing…";
    const detail = $("#aiConnectionDetail");
    try {
      const d = await jsonRequest("/api/ai/test", { method: "POST" });
      if (detail) detail.textContent = `Live completion test passed: ${d.answer}`;
      if (aiStatus) { aiStatus.textContent = `● AI is actually responding · ${d.model}`; aiStatus.classList.remove("error"); aiStatus.classList.add("success"); }
    } catch (err) {
      if (detail) detail.textContent = err.message;
      if (aiStatus) { aiStatus.textContent = "○ AI connection test failed"; aiStatus.classList.remove("success"); aiStatus.classList.add("error"); }
    } finally { aiTestButton.disabled = false; aiTestButton.textContent = "Run AI connection test"; }
  });

  const ai = $("#aiForm");
  if (ai) ai.addEventListener("submit", async (e) => {
    e.preventDefault();
    const answer = $("#aiAnswer");
    const sources = $("#aiSources");
    const question = $("#aiQuestion")?.value.trim();
    if (!question) { answer.textContent = "Ask a question first."; return; }
    setBusy(ai, true, "Thinking…");
    answer.textContent = "Advanced AI is analyzing your question…";
    if (sources) { sources.style.display = "none"; sources.innerHTML = ""; }
    try {
      const d = await jsonRequest("/api/ai", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }) });
      answer.textContent = d.answer;
      if (sources && Array.isArray(d.sources) && d.sources.length) {
        sources.style.display = "block";
        const title = document.createElement("div");
        title.className = "section-title";
        title.innerHTML = "<h3>Web sources</h3>";
        sources.appendChild(title);
        const list = document.createElement("div");
        list.className = "list";
        d.sources.forEach(src => {
          const row = document.createElement("div");
          const link = document.createElement("a");
          link.href = src.url; link.target = "_blank"; link.rel = "noopener noreferrer";
          link.textContent = src.title || src.url;
          row.appendChild(link);
          list.appendChild(row);
        });
        sources.appendChild(list);
      }
    } catch (err) { answer.textContent = err.message; }
    setBusy(ai, false);
  });

  const report = $("#reportForm");
  if (report) report.addEventListener("submit", async (e) => {
    e.preventDefault(); setBusy(report, true, "Creating…");
    try {
      await jsonRequest("/api/reports/create", { method: "POST", body: new FormData(report) });
      window.location.reload();
    } catch (err) { showStatus(report, err.message); setBusy(report, false); }
  });

  // Draw the analytics trend from the actual uploaded dataset + 5-step forecast.
  const chart = $("#trendChart");
  if (chart) {
    const actual = JSON.parse(chart.dataset.actual || "[]");
    const forecast = JSON.parse(chart.dataset.forecast || "[]");
    const all = actual.concat(forecast);
    if (all.length) {
      const width = 700, height = 260, pad = 28;
      const min = Math.min(...all), max = Math.max(...all);
      const range = max - min || 1;
      const points = all.map((v, i) => {
        const x = pad + i * ((width - pad * 2) / Math.max(all.length - 1, 1));
        const y = height - pad - ((v - min) / range) * (height - pad * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      });
      const actualCount = actual.length;
      const actualPoints = points.slice(0, actualCount).join(" ");
      const forecastPoints = points.slice(Math.max(0, actualCount - 1)).join(" ");
      chart.innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-label="Dataset trend and forecast"><path class="axis" d="M${pad} ${height-pad}H${width-pad}"/><polyline class="line1" points="${actualPoints}"/><polyline class="line2" points="${forecastPoints}"/><text x="${pad}" y="18" class="chart-label">${Number(min).toLocaleString()} – ${Number(max).toLocaleString()}</text></svg>`;
    }
  }
})();

// Global navigation helpers: keyboard search, theme preference, and help filtering.
(() => {
  const search = document.querySelector("#globalSearch");
  const routes = [
    ["home", "/"], ["dashboard", "/dashboard"], ["analytics", "/analytics"],
    ["ai analyst", "/ai-analyst"], ["data sources", "/data-sources"],
    ["data explorer", "/data-explorer"], ["forecasts", "/forecasts"],
    ["reports", "/reports"], ["team", "/team"], ["activity", "/activity"],
    ["notifications", "/notifications"], ["billing", "/billing"],
    ["profile", "/profile"], ["settings", "/workspace-settings"],
    ["help", "/help"], ["about", "/about"], ["pricing", "/pricing"], ["contact", "/contact"]
  ];
  function goSearch(value) {
    const q = String(value || "").trim().toLowerCase();
    if (!q) return;
    const match = routes.find(([name]) => name.includes(q) || q.includes(name));
    if (match) window.location.href = match[1];
  }
  if (search) {
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") goSearch(search.value); });
    document.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); search.focus(); search.select(); }
    });
  }
  const theme = document.querySelector("#themeToggle");
  const applyTheme = (mode) => {
    document.documentElement.dataset.theme = mode;
    localStorage.setItem("kp-nexora-theme", mode);
  };
  const saved = localStorage.getItem("kp-nexora-theme");
  if (saved) applyTheme(saved);
  if (theme) theme.addEventListener("click", () => applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light"));

  const help = document.querySelector("#helpSearch");
  if (help) help.addEventListener("input", () => {
    const q = help.value.trim().toLowerCase();
    document.querySelectorAll("#helpSearch ~ .grid3 .card").forEach(card => { card.hidden = q && !card.textContent.toLowerCase().includes(q); });
  });
})();
