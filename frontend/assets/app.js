/* NPlayStudio — shared frontend runtime.
 *
 * One file drives every page: chrome (header/footer), theme, the generic
 * upload -> poll -> download tool runner, and the dashboard widgets. Tool
 * pages declare their behaviour with data-* attributes instead of bespoke
 * scripts, so adding a tool means adding HTML only.
 */
(() => {
  "use strict";

  const API = "";
  const POLL_MS = 1200;
  const POLL_TIMEOUT_MS = 30 * 60 * 1000;

  /* ------------------------------------------------------------ utilities */

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  // Per-browser id so the dashboard can show this visitor their own jobs
  // instead of every job on the server. Random, no personal data, and the only
  // thing it is used for is scoping /api/history.
  const CLIENT_KEY = "nplay.client";
  const CLIENT_HEADER = "X-Client-Id";

  function clientId() {
    let id = localStorage.getItem(CLIENT_KEY);
    if (!id) {
      const bytes = new Uint8Array(16);
      if (window.crypto?.getRandomValues) {
        window.crypto.getRandomValues(bytes);
      } else {
        for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
      }
      id = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
      localStorage.setItem(CLIENT_KEY, id);
    }
    return id;
  }

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value;
      else if (key.startsWith("on") && typeof value === "function") {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of [].concat(children)) {
      if (child) node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
    }
    return node;
  }

  function humanBytes(bytes) {
    const n = Number(bytes);
    if (!Number.isFinite(n) || n <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = n;
    let i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
    return i === 0 ? `${Math.round(value)} B` : `${value.toFixed(1)} ${units[i]}`;
  }

  const ICONS = {
    compress: "🗜", convert: "🔄", merge: "🔗", gif: "🎞",
    extract: "🎵", crop: "✂", default: "⚙",
  };

  /* ---------------------------------------------------------------- theme */

  const THEME_KEY = "nplay.theme";

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    const toggle = $("#theme-toggle");
    if (toggle) {
      toggle.textContent = theme === "dark" ? "☀" : "☾";
      toggle.setAttribute("aria-label", `Switch to ${theme === "dark" ? "light" : "dark"} theme`);
    }
  }

  function initTheme() {
    const stored = localStorage.getItem(THEME_KEY);
    const prefersLight = window.matchMedia?.("(prefers-color-scheme: light)").matches;
    applyTheme(stored || (prefersLight ? "light" : "dark"));
  }

  /* --------------------------------------------------------------- chrome */

  const NAV = [
    { href: "/", label: "Dashboard" },
    { href: "/#video", label: "Video" },
    { href: "/#audio", label: "Audio" },
    { href: "/#image", label: "Image" },
    { href: "/faq.html", label: "FAQ" },
  ];

  const FOOTER_COLS = [
    { title: "Tools", links: [
      ["Video Compress", "/video-compress.html"], ["Video Convert", "/video-convert.html"],
      ["Video Merge", "/video-merge.html"], ["Video to GIF", "/video-to-gif.html"],
    ]},
    { title: "Audio & Image", links: [
      ["Audio Extract", "/audio-extract.html"], ["Audio Convert", "/audio-convert.html"],
      ["Image Compress", "/image-compress.html"], ["Image Convert", "/image-convert.html"],
      ["Image Crop", "/image-crop.html"],
    ]},
    { title: "Company", links: [
      ["About", "/about.html"], ["Contact", "/contact.html"], ["FAQ", "/faq.html"],
      ["Sitemap", "/sitemap.html"],
    ]},
    { title: "Legal", links: [
      ["Privacy Policy", "/privacy.html"], ["Terms", "/terms.html"], ["DMCA", "/dmca.html"],
      ["Disclaimer", "/disclaimer.html"], ["Cookie Policy", "/cookie.html"],
      ["GDPR", "/gdpr.html"],
    ]},
  ];

  function buildHeader() {
    const here = location.pathname.replace(/index\.html$/, "");
    const nav = el("nav", { class: "nav" },
      NAV.map((item) => el("a", {
        href: item.href,
        text: item.label,
        "aria-current": item.href === here || (item.href !== "/" && here.startsWith(item.href)) ? "page" : null,
      }))
    );
    const toggle = el("button", {
      class: "btn-ghost", id: "theme-toggle", type: "button", text: "☾",
      style: "padding:.35rem .6rem", title: "Toggle theme",
    });
    return el("header", { class: "site-header" }, [
      el("div", { class: "wrap" }, [
        el("a", { class: "logo", href: "/" }, [
          el("span", { class: "logo-mark", text: "N" }),
          el("span", { html: "NPlay<span class='gradient-text'>Studio</span>" }),
        ]),
        nav,
        toggle,
      ]),
    ]);
  }

  function buildFooter() {
    const cols = FOOTER_COLS.map((col) =>
      el("div", {}, [
        el("h4", { text: col.title }),
        el("ul", {}, col.links.map(([label, href]) =>
          el("li", {}, el("a", { href, text: label })))),
      ])
    );
    return el("footer", { class: "site-footer" }, [
      el("div", { class: "wrap" }, [
        el("div", { class: "footer-cols" }, cols),
        el("div", { class: "footer-bottom" }, [
          el("span", { html: `© ${new Date().getFullYear()} NPlayStudio — Create. Convert. Compress.` }),
          el("span", { text: "Uploaded files are deleted automatically after 1 hour." }),
        ]),
      ]),
    ]);
  }

  function mountChrome() {
    const header = buildHeader();
    const footer = buildFooter();
    document.body.prepend(header);
    document.body.appendChild(footer);
    $("#theme-toggle")?.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      localStorage.setItem(THEME_KEY, next);
      applyTheme(next);
    });
  }

  /* -------------------------------------------------------- cookie banner */

  const CONSENT_KEY = "nplay.consent";
  const ADSENSE_CLIENT = "ca-pub-0000000000000000"; // set the real id when deploying

  function adsAllowed() {
    return localStorage.getItem(CONSENT_KEY) === "accepted";
  }

  // The AdSense loader is injected only after the visitor accepts, so the
  // promise that "Essential only" avoids advertising cookies actually holds.
  function loadAds() {
    if (!adsAllowed()) return;
    if (!ADSENSE_CLIENT || ADSENSE_CLIENT.includes("0000000000000000")) return;
    if ($("script[data-adsense]")) return;
    const script = document.createElement("script");
    script.async = true;
    script.crossOrigin = "anonymous";
    script.dataset.adsense = "1";
    script.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${ADSENSE_CLIENT}`;
    document.head.appendChild(script);
  }

  function mountConsent() {
    const stored = localStorage.getItem(CONSENT_KEY);
    if (stored) {
      if (stored === "accepted") loadAds();
      return;
    }
    const banner = el("div", {
      class: "notice notice-info",
      style: "position:fixed;left:1rem;right:1rem;bottom:1rem;z-index:60;max-width:640px;margin-inline:auto;box-shadow:var(--shadow)",
      role: "dialog", "aria-label": "Cookie consent",
    }, [
      el("p", { style: "margin:0 0 .7rem" , html:
        "We use cookies for essential functionality and, via Google AdSense, for advertising. " +
        "See our <a href='/cookie.html'>Cookie Policy</a> and <a href='/privacy.html'>Privacy Policy</a>." }),
      el("div", { style: "display:flex;gap:.6rem;flex-wrap:wrap" }, [
        el("button", { type: "button", text: "Accept", onclick: () => decide("accepted") }),
        el("button", { class: "btn-ghost", type: "button", text: "Essential only", onclick: () => decide("essential") }),
      ]),
    ]);
    function decide(choice) {
      localStorage.setItem(CONSENT_KEY, choice);
      if (choice === "accepted") loadAds();
      banner.remove();
    }
    document.body.appendChild(banner);
  }

  /* ------------------------------------------------------- api interaction */

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set(CLIENT_HEADER, clientId());
    const response = await fetch(`${API}${path}`, { ...options, headers });
    const type = response.headers.get("content-type") || "";
    const body = type.includes("application/json") ? await response.json() : null;
    if (!response.ok) {
      const detail = body?.detail;
      const message = typeof detail === "string"
        ? detail
        : Array.isArray(detail)
          ? detail.map((d) => d.msg || JSON.stringify(d)).join("; ")
          : `request failed (${response.status})`;
      throw new Error(message);
    }
    return body;
  }

  /* ----------------------------------------------------------- tool runner */

  class ToolRunner {
    constructor(root) {
      this.root = root;
      this.endpoint = root.dataset.endpoint;
      this.multiple = root.dataset.multiple === "true";
      this.accept = root.dataset.accept || "";
      this.files = [];
      this.polling = false;
      this.cacheDom();
      this.bind();
      this.render();
    }

    cacheDom() {
      this.dropzone = $("[data-dropzone]", this.root);
      this.input = $("input[type=file]", this.root);
      this.list = $("[data-file-list]", this.root);
      this.preview = $("[data-preview]", this.root);
      this.form = $("form", this.root);
      this.submit = $("[data-submit]", this.root);
      this.progressWrap = $("[data-progress-wrap]", this.root);
      this.bar = $("[data-progress-bar]", this.root);
      this.pct = $("[data-progress-pct]", this.root);
      this.pill = $("[data-status-pill]", this.root);
      this.notice = $("[data-notice]", this.root);
      this.result = $("[data-result]", this.root);
      this.resetBtn = $("[data-reset]", this.root);
    }

    bind() {
      const zone = this.dropzone;
      if (zone) {
        zone.addEventListener("click", () => this.input.click());
        zone.addEventListener("keydown", (event) => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); this.input.click(); }
        });
        ["dragenter", "dragover"].forEach((type) =>
          zone.addEventListener(type, (event) => {
            event.preventDefault(); zone.classList.add("is-over");
          }));
        ["dragleave", "drop"].forEach((type) =>
          zone.addEventListener(type, (event) => {
            event.preventDefault(); zone.classList.remove("is-over");
          }));
        zone.addEventListener("drop", (event) => this.addFiles(event.dataTransfer.files));
      }
      this.input?.addEventListener("change", () => this.addFiles(this.input.files));
      this.form?.addEventListener("submit", (event) => { event.preventDefault(); this.start(); });
      this.resetBtn?.addEventListener("click", () => this.reset());
    }

    addFiles(fileList) {
      const incoming = Array.from(fileList || []);
      this.files = this.multiple ? [...this.files, ...incoming] : incoming.slice(0, 1);
      this.render();
    }

    removeFile(index) {
      this.files.splice(index, 1);
      this.render();
    }

    render() {
      if (!this.list) return;
      this.list.innerHTML = "";
      this.files.forEach((file, index) => {
        this.list.appendChild(el("li", {}, [
          el("span", { class: "name", text: file.name }),
          el("span", { class: "size", text: humanBytes(file.size) }),
          el("button", {
            class: "btn-ghost", type: "button", text: "✕", title: "Remove",
            style: "padding:.2rem .5rem",
            onclick: () => this.removeFile(index),
          }),
        ]));
      });

      const first = this.files[0];
      if (this.preview && first) {
        const url = URL.createObjectURL(first);
        if (first.type.startsWith("image/")) {
          this.preview.src = url; this.preview.hidden = false;
          this.preview.onload = () => this.syncImageDefaults(this.preview.naturalWidth, this.preview.naturalHeight);
        } else if (first.type.startsWith("video/")) {
          this.preview.src = url; this.preview.hidden = false;
        } else {
          this.preview.hidden = true;
        }
      } else if (this.preview) {
        this.preview.hidden = true;
      }

      if (this.submit) this.submit.disabled = this.files.length === 0;
    }

    syncImageDefaults(width, height) {
      const w = $("input[name=width]", this.root);
      const h = $("input[name=height]", this.root);
      const maxW = $("input[name=max_width]", this.root);
      if (w && !w.value) w.value = width;
      if (h && !h.value) h.value = height;
      if (maxW && !maxW.value) maxW.value = Math.min(width, 1920);
      const note = $("[data-dims]", this.root);
      if (note) note.textContent = `Source: ${width} × ${height} px`;
    }

    collectFormData() {
      const data = new FormData();
      const field = this.multiple ? "files" : "file";
      this.files.forEach((file) => data.append(field, file, file.name));

      $$("input, select, textarea", this.form || this.root).forEach((input) => {
        const name = input.name;
        if (!name || input.type === "file" || input.type === "submit" || input.type === "button") return;
        if (input.type === "checkbox") {
          if (input.checked) data.append(name, "true");
          return;
        }
        if (input.type === "radio" && !input.checked) return;
        const value = String(input.value ?? "").trim();
        if (value === "") return; // let the server apply its default
        data.append(name, value);
      });
      return data;
    }

    setNotice(message, kind = "info") {
      if (!this.notice) return;
      this.notice.hidden = !message;
      this.notice.className = `notice notice-${kind}`;
      this.notice.textContent = message || "";
    }

    setProgress(percent, status) {
      if (this.progressWrap) this.progressWrap.hidden = false;
      if (this.bar) this.bar.style.width = `${percent}%`;
      if (this.pct) this.pct.textContent = `${percent}%`;
      if (this.pill) {
        this.pill.dataset.status = status;
        this.pill.textContent = status;
      }
    }

    async start() {
      if (!this.files.length) return;
      this.submit.disabled = true;
      this.result.hidden = true;
      this.setNotice("");
      this.setProgress(2, "queued");

      let taskId;
      try {
        const accepted = await api(this.endpoint, { method: "POST", body: this.collectFormData() });
        taskId = accepted.task_id;
      } catch (error) {
        this.setNotice(error.message, "err");
        this.setProgress(0, "failed");
        this.submit.disabled = false;
        return;
      }

      this.poll(taskId);
    }

    async poll(taskId) {
      this.polling = true;
      const deadline = Date.now() + POLL_TIMEOUT_MS;
      while (this.polling && Date.now() < deadline) {
        let status;
        try {
          status = await api(`/api/status/${taskId}`);
        } catch (error) {
          this.setNotice(error.message, "err");
          this.submit.disabled = false;
          this.polling = false;
          return;
        }

        this.setProgress(status.progress ?? 0, status.status);

        if (status.status === "done") {
          this.polling = false;
          this.finish(status);
          return;
        }
        if (status.status === "failed") {
          this.polling = false;
          this.setNotice(status.error || "processing failed", "err");
          this.submit.disabled = false;
          return;
        }
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
      }

      if (this.polling) {
        this.polling = false;
        this.setNotice("timed out waiting for the job to finish", "err");
        this.submit.disabled = false;
      }
    }

    finish(status) {
      this.setNotice("Done! Your file is ready to download.", "ok");
      if (!this.result) { this.submit.disabled = false; return; }

      const before = status.input_size || 0;
      const after = status.output_size || 0;
      const delta = before > 0 && after > 0 ? Math.round((1 - after / before) * 100) : null;

      this.result.innerHTML = "";
      this.result.hidden = false;
      this.result.appendChild(el("div", { class: "compare" }, [
        el("div", {}, [el("span", { text: "Before" }), el("b", { text: humanBytes(before) })]),
        el("div", {}, [el("span", { text: "After" }), el("b", { text: humanBytes(after) })]),
        delta !== null ? el("div", {}, [
          el("span", { text: "Change" }),
          el("b", { class: delta >= 0 ? "saved" : "", text: delta >= 0 ? `−${delta}%` : `+${Math.abs(delta)}%` }),
        ]) : null,
      ]));
      this.result.appendChild(el("div", { style: "margin-top:1rem;display:flex;gap:.6rem;flex-wrap:wrap" }, [
        el("a", {
          class: "btn",
          href: `/api/file/${status.task_id}?client=${encodeURIComponent(clientId())}`,
          text: "⬇ Download",
        }),
        el("button", {
          class: "btn-ghost", type: "button", text: "Process another",
          onclick: () => this.reset(),
        }),
      ]));
      this.submit.disabled = false;
    }

    reset() {
      this.polling = false;
      this.files = [];
      if (this.input) this.input.value = "";
      this.render();
      this.setNotice("");
      this.setProgress(0, "queued");
      if (this.progressWrap) this.progressWrap.hidden = true;
      if (this.result) { this.result.hidden = true; this.result.innerHTML = ""; }
    }
  }

  /* ------------------------------------------------------------- dashboard */

  async function initDashboard() {
    const grid = $("#tool-grid");
    const search = $("#tool-search");
    const chips = $$(".chip[data-filter]");
    const activity = $("#activity-body");
    const statGrid = $("#stat-grid");

    let tools = [];
    try {
      const data = await api("/api/tools");
      tools = data.tools || [];
    } catch {
      tools = [];
    }

    let filter = "all";

    function renderTools() {
      if (!grid) return;
      const term = (search?.value || "").trim().toLowerCase();
      grid.innerHTML = "";
      tools
        .filter((tool) => filter === "all" || tool.category === filter)
        .filter((tool) => !term || `${tool.name} ${tool.description} ${tool.category}`.toLowerCase().includes(term))
        .forEach((tool) => {
          grid.appendChild(el("a", { class: "card", href: tool.path }, [
            el("div", { class: "tool-icon", text: ICONS[tool.icon] || ICONS.default }),
            el("h3", { text: tool.name }),
            el("p", { text: tool.description }),
          ]));
        });
      if (!grid.children.length) {
        grid.appendChild(el("p", { class: "notice notice-info", text: "No tools match your search." }));
      }
    }

    search?.addEventListener("input", renderTools);
    chips.forEach((chip) => chip.addEventListener("click", () => {
      filter = chip.dataset.filter;
      chips.forEach((c) => c.setAttribute("aria-pressed", String(c === chip)));
      renderTools();
    }));

    renderTools();

    try {
      const stats = await api("/api/stats");
      if (statGrid) {
        statGrid.innerHTML = "";
        [
          ["Jobs completed", stats.total_tasks ?? 0],
          ["Files downloaded", stats.total_downloads ?? 0],
          ["Processed", humanBytes(stats.total_input_bytes)],
          ["Delivered", humanBytes(stats.total_output_bytes)],
        ].forEach(([label, value]) => {
          statGrid.appendChild(el("div", { class: "stat" }, [
            el("b", { text: String(value) }),
            el("span", { text: label }),
          ]));
        });
      }
    } catch { /* stats are decorative */ }

    try {
      const history = await api("/api/history?limit=10");
      if (activity) {
        activity.innerHTML = "";
        if (!history.tasks?.length) {
          activity.appendChild(el("tr", {}, el("td", { colspan: "5", text: "No activity yet — pick a tool above to get started." })));
        } else {
          history.tasks.forEach((task) => {
            activity.appendChild(el("tr", {}, [
              el("td", { text: task.tool }),
              el("td", {}, el("span", { class: "status-pill", "data-status": task.status, text: task.status })),
              el("td", { class: "mono", text: humanBytes(task.input_size) }),
              el("td", { class: "mono", text: humanBytes(task.output_size) }),
              el("td", { class: "mono", text: (task.created_at || "").replace("T", " ").slice(0, 16) }),
            ]));
          });
        }
      }
    } catch { /* history is decorative */ }
  }

  /* ---------------------------------------------------------------- pwa */

  function initPwa() {
    const link = document.createElement("link");
    link.rel = "manifest";
    link.href = "/manifest.webmanifest";
    document.head.appendChild(link);
  }

  /* ----------------------------------------------------------------- boot */

  function boot() {
    initTheme();
    mountChrome();
    mountConsent();
    initPwa();

    const toolRoot = $("[data-tool]");
    if (toolRoot) new ToolRunner(toolRoot);
    if ($("#tool-grid")) initDashboard();

    // Re-render progress UI on pages that only need a static status pill.
    $$("[data-static-year]").forEach((node) => { node.textContent = String(new Date().getFullYear()); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }

  window.NPlay = { humanBytes, api, el };
})();
