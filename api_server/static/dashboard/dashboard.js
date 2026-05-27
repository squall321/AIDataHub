/* ai-data-api dashboard — vanilla JS, no bundler */
"use strict";

// ============================================================================
// Config / API helpers
// ============================================================================
const API_KEY_STORAGE = "aidh.api_key";
const BASE = "";

function getApiKey() {
  return localStorage.getItem(API_KEY_STORAGE) || "";
}

function setApiKey(value) {
  if (value) localStorage.setItem(API_KEY_STORAGE, value);
  else localStorage.removeItem(API_KEY_STORAGE);
}

async function apiFetch(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  if (opts.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(BASE + path, Object.assign({}, opts, { headers }));
  const ct = resp.headers.get("content-type") || "";
  let body = null;
  // 204/205 또는 빈 본문(DELETE 등)을 resp.json() 하면
  // "Unexpected end of JSON input" 가 난다 → 본문을 먼저 text 로 받고
  // 비어있지 않을 때만 파싱한다.
  if (resp.status !== 204 && resp.status !== 205) {
    const txt = await resp.text();
    if (txt) {
      body = ct.includes("application/json") ? JSON.parse(txt) : txt;
    }
  }
  if (!resp.ok) {
    const detail = (body && body.detail) || body || `HTTP ${resp.status}`;
    const err = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    err.status = resp.status;
    err.body = body;
    throw err;
  }
  return body;
}

// Raw fetch — returns full response (for try-it that wants status, headers, time).
async function apiFetchRaw(path, opts = {}) {
  const headers = Object.assign({}, opts.headers || {});
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  if (opts.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const t0 = performance.now();
  const resp = await fetch(BASE + path, Object.assign({}, opts, { headers }));
  const t1 = performance.now();
  const ct = resp.headers.get("content-type") || "";
  let body;
  try {
    if (ct.includes("application/json")) body = await resp.json();
    else body = await resp.text();
  } catch (e) {
    body = "[no body]";
  }
  return { status: resp.status, ok: resp.ok, body, ms: Math.round(t1 - t0), contentType: ct };
}

// ============================================================================
// DOM helpers
// ============================================================================
function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on") && typeof v === "function")
        node.addEventListener(k.slice(2).toLowerCase(), v);
      else node.setAttribute(k, v);
    }
  }
  if (children) {
    for (const c of [].concat(children)) {
      if (c == null) continue;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
  }
  return node;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

function setState(target, kind, msg) {
  clear(target);
  target.appendChild(el("div", { class: `state ${kind || ""}` }, msg || ""));
}

function showError(target, err) {
  setState(target, "error", "오류: " + (err.message || err));
}

function fmtDate(s) {
  if (!s) return "";
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toISOString().slice(0, 19).replace("T", " ");
}

function badge(text, kind) {
  return el("span", { class: `badge ${kind || ""}` }, text);
}

// JSON syntax highlighter (lightweight, regex-based)
function highlightJson(value) {
  let str;
  if (typeof value === "string") {
    // Try parse & re-pretty if string looks like JSON.
    try { str = JSON.stringify(JSON.parse(value), null, 2); }
    catch { return escapeHtml(value); }
  } else {
    try { str = JSON.stringify(value, null, 2); }
    catch { return escapeHtml(String(value)); }
  }
  return str
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(
      /("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(\.\d+)?([eE][+-]?\d+)?)/g,
      (m) => {
        let cls = "tk-num";
        if (/^"/.test(m)) cls = /:$/.test(m) ? "tk-key" : "tk-str";
        else if (/true|false/.test(m)) cls = "tk-bool";
        else if (/null/.test(m)) cls = "tk-null";
        return `<span class="${cls}">${m}</span>`;
      }
    )
    .replace(/([{}\[\],])/g, '<span class="tk-punc">$1</span>');
}

function escapeHtml(s) {
  if (s == null) return "";
  return String(s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

// ============================================================================
// Tabs
// ============================================================================
const LOADERS = {};

function initTabs() {
  const buttons = document.querySelectorAll("nav.tabs button[data-tab]");
  const panels = document.querySelectorAll("section.panel[data-panel]");
  buttons.forEach((b) => {
    b.addEventListener("click", () => {
      const target = b.getAttribute("data-tab");
      buttons.forEach((x) => x.classList.toggle("active", x === b));
      panels.forEach((p) =>
        p.classList.toggle("active", p.getAttribute("data-panel") === target)
      );
      if (!b.dataset.loaded) {
        b.dataset.loaded = "1";
        const fn = LOADERS[target];
        if (fn) fn();
      }
    });
  });
}

// ============================================================================
// Section 1: 상태
// ============================================================================
async function loadStatus() {
  const heroBox = document.getElementById("status-hero");
  const target = document.getElementById("status-cards");
  setState(heroBox, "", "로드 중...");
  setState(target, "", "로드 중...");

  try {
    const [health, discover] = await Promise.all([
      apiFetch("/api/system/health").catch((e) => ({ _err: e })),
      apiFetch("/api/discover").catch((e) => ({ _err: e })),
    ]);

    // Hero
    clear(heroBox);
    const heroL = el("div", { class: "hero-card" });
    const statusKind = health._err ? "err" : (health.status === "ok" ? "ok" : "warn");
    heroL.appendChild(el("div", { class: "label" }, "API 서버"));
    heroL.appendChild(
      el("div", { class: "big" }, health._err ? "OFFLINE" : (health.status || "?").toUpperCase())
    );
    heroL.appendChild(
      el("div", { class: "sub" }, [
        el("span", { class: `pill ${statusKind}` }, health._err ? "no response" : "healthy"),
        el("span", { class: "pill" }, "v" + (health.version || "—")),
        el("span", { class: "pill" },
          "auth: " + (health._err ? "?" : (health.auth_required ? "required" : "open"))),
      ])
    );
    heroBox.appendChild(heroL);

    const statsBox = el("div", { class: "hero-stats" });
    const totalRecords = discover._err ? "—" : String(discover.total_records ?? "—");
    const dtCount = discover._err ? "—"
      : String(Object.keys(discover.by_data_type || {}).length || "0");
    let agentCount = "—";
    if (!discover._err) {
      if (Array.isArray(discover.agents)) agentCount = String(discover.agents.length);
      else if (discover.agents && typeof discover.agents === "object")
        agentCount = String(Object.keys(discover.agents).length);
      else if (typeof discover.agent_count === "number")
        agentCount = String(discover.agent_count);
    }
    [["records", totalRecords, "전체 레코드"],
     ["data types", dtCount, "분류 수"],
     ["agents", agentCount, "등록된 agent"]].forEach(([lab, n, sub]) => {
       const stat = el("div", { class: "hero-stat" });
       stat.appendChild(el("div", { class: "label" }, lab));
       stat.appendChild(el("div", { class: "num" }, n));
       stat.appendChild(el("div", { class: "delta" }, sub));
       statsBox.appendChild(stat);
     });
    heroBox.appendChild(statsBox);

    // Cards (detail strip)
    clear(target);

    // Card: data_type 분포
    const dtCard = el("div", { class: "card" });
    dtCard.appendChild(el("div", { class: "label" }, "data_type 분포"));
    const dtUl = el("ul");
    const byDt = (discover && discover.by_data_type) || {};
    const dtKeys = Object.keys(byDt);
    if (dtKeys.length === 0) dtUl.appendChild(el("li", {}, [el("span", {}, "(없음)"), el("span", {}, "0")]));
    else dtKeys.forEach((k) =>
      dtUl.appendChild(el("li", {}, [el("span", {}, k), el("span", {}, String(byDt[k]))]))
    );
    dtCard.appendChild(dtUl);
    target.appendChild(dtCard);

    // Card: 빌드 정보
    const buildCard = el("div", { class: "card" });
    buildCard.appendChild(el("div", { class: "label" }, "빌드"));
    buildCard.appendChild(el("div", { class: "value small" }, (health && health.build) || "—"));
    if (!health._err && health.embedder) {
      buildCard.appendChild(badge("embedder: " + health.embedder, "ok"));
    }
    target.appendChild(buildCard);

    // footer build
    const footerBuild = document.getElementById("footer-build");
    if (footerBuild) footerBuild.textContent = "build · " + ((health && health.version) || "—");

    // Card: API key 상태
    const keyCard = el("div", { class: "card" });
    keyCard.appendChild(el("div", { class: "label" }, "API key"));
    if (getApiKey()) {
      keyCard.appendChild(el("div", { class: "value small" }, "saved"));
      keyCard.appendChild(badge("localStorage", "ok"));
    } else {
      keyCard.appendChild(el("div", { class: "value small" }, "anonymous"));
      keyCard.appendChild(badge("no key", "warn"));
    }
    target.appendChild(keyCard);

    // Card: 빠른 링크
    const linkCard = el("div", { class: "card" });
    linkCard.appendChild(el("div", { class: "label" }, "빠른 링크"));
    const linkUl = el("ul");
    [["/docs", "Swagger UI"],
     ["/api/discover", "Discover"],
     ["/api/system/health", "Health"]].forEach(([href, lab]) => {
      linkUl.appendChild(el("li", {}, [
        el("span", {}, lab),
        el("a", { href, target: "_blank", rel: "noopener" }, "↗"),
      ]));
    });
    linkCard.appendChild(linkUl);
    target.appendChild(linkCard);

    // Card: VSCode Extension 다운로드
    const extCard = el("div", { class: "card" });
    extCard.appendChild(el("div", { class: "label" }, "VSCode Extension"));
    // 캐시 무력화 — 브라우저가 옛 메타(버전)를 재사용하던 문제 방지.
    apiFetch("/downloads/extension-meta.json?_=" + Date.now())
      .then((meta) => {
        // 버전 박힌 파일 우선 (고유 URL → 브라우저 캐시 무관). 없으면 latest.
        const vfile = meta.versioned_filename || meta.filename || "ai-data-hub-uploader-latest.vsix";
        const latest = meta.filename || "ai-data-hub-uploader-latest.vsix";
        const fullUrl = location.origin + "/downloads/" + vfile;
        extCard.appendChild(el("div", { class: "value small" }, "v" + (meta.version || "?")));
        extCard.appendChild(el("div", { style: "margin-top:8px; display:flex; gap:8px; flex-wrap:wrap;" }, [
          el("a", {
            href: "/downloads/" + vfile,
            class: "btn",
            style: "display:inline-block; padding:5px 12px; font-size:12px; text-decoration:none;",
          }, "Download v" + (meta.version || "?")),
          el("a", {
            href: "/downloads/" + latest,
            class: "btn ghost",
            style: "display:inline-block; padding:5px 12px; font-size:12px; text-decoration:none;",
          }, "latest"),
        ]));
        // 직접 링크 — 다른 사람에게 공유/복사용 (현재 접속 origin 기준).
        const linkRow = el("div", { style: "margin-top:8px; display:flex; gap:6px; align-items:center;" });
        const linkInput = el("input", {
          type: "text", readonly: "readonly", value: fullUrl,
          onclick: function () { this.select(); },
          style: "flex:1; font-size:11px; padding:3px 6px; font-family:var(--mono); "
               + "border:1px solid var(--border,#ccc); border-radius:3px; background:transparent; color:inherit;",
        });
        const copyBtn = el("button", {
          class: "btn ghost",
          style: "padding:3px 8px; font-size:11px;",
          onclick: () => {
            navigator.clipboard?.writeText(fullUrl);
            copyBtn.textContent = "복사됨";
            setTimeout(() => (copyBtn.textContent = "복사"), 1200);
          },
        }, "복사");
        linkRow.appendChild(linkInput);
        linkRow.appendChild(copyBtn);
        extCard.appendChild(linkRow);
        if (meta.built_at) {
          extCard.appendChild(el("div", { class: "value small", style: "margin-top:4px; opacity:.6;" },
            "빌드: " + fmtDate(meta.built_at)));
        }
      })
      .catch(() => {
        extCard.appendChild(el("div", { class: "value small" }, "(빌드 없음 — setup.sh 실행 필요)"));
      });
    target.appendChild(extCard);
  } catch (err) {
    showError(target, err);
  }
}

LOADERS["status"] = loadStatus;

// ============================================================================
// Section 2: 카탈로그
// ============================================================================
const CATALOG = { limit: 20, offset: 0, total: 0 };

function buildCatalogQuery() {
  const params = new URLSearchParams();
  const dt = document.getElementById("cat-data-type").value.trim();
  const cls = document.getElementById("cat-classification").value.trim();
  const tags = document.getElementById("cat-tags").value.trim();
  const ag = document.getElementById("cat-agent").value.trim();
  if (dt) params.set("data_type", dt);
  if (cls) params.append("classification", cls);
  if (tags) tags.split(",").map((s) => s.trim()).filter(Boolean).forEach((t) => params.append("tag", t));
  if (ag) ag.split(",").map((s) => s.trim()).filter(Boolean).forEach((a) => params.append("agent", a));
  params.set("limit", String(CATALOG.limit));
  params.set("offset", String(CATALOG.offset));
  return params;
}

async function loadCatalog() {
  const target = document.getElementById("cat-table-wrap");
  setState(target, "", "로드 중...");
  try {
    const params = buildCatalogQuery();
    const cls = document.getElementById("cat-classification").value.trim();
    params.delete("classification");
    const data = await apiFetch("/api/records?" + params.toString());
    let items = data.items || [];
    if (cls) items = items.filter((r) => (r.classification || "") === cls);
    CATALOG.total = data.total || 0;
    renderCatalog(target, items);
  } catch (err) {
    showError(target, err);
  }
}

function renderCatalog(target, items) {
  clear(target);
  if (items.length === 0) {
    setState(target, "", "결과 없음");
    renderPaginator();
    return;
  }
  const table = el("table", { class: "data-table" });
  const thead = el("thead", {}, el("tr", {}, [
    el("th", {}, "record_id"),
    el("th", {}, "data_type"),
    el("th", {}, "title"),
    el("th", {}, "tags"),
    el("th", {}, "classification"),
    el("th", {}, "status"),
    el("th", {}, "created_at"),
  ]));
  table.appendChild(thead);
  const tbody = el("tbody");
  items.forEach((rec) => {
    const tr = el("tr", { class: "clickable" });
    tr.appendChild(el("td", { class: "mono" }, rec.id || ""));
    tr.appendChild(el("td", {}, rec.data_type || ""));
    tr.appendChild(el("td", {}, rec.title || ""));
    const tagCell = el("td");
    (rec.tags || []).slice(0, 6).forEach((t) =>
      tagCell.appendChild(el("span", { class: "tag" }, t))
    );
    tr.appendChild(tagCell);
    tr.appendChild(el("td", {}, rec.classification || ""));
    tr.appendChild(el("td", {}, rec.status || ""));
    tr.appendChild(el("td", { class: "mono" }, fmtDate(rec.created_at)));
    tr.addEventListener("click", () => toggleRecordDetail(tr, rec));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  target.appendChild(table);
  renderPaginator();
}

function renderPaginator() {
  const p = document.getElementById("cat-paginator");
  clear(p);
  const info = `${CATALOG.offset + 1}–${Math.min(
    CATALOG.offset + CATALOG.limit,
    CATALOG.total
  )} / ${CATALOG.total}`;
  const prev = el("button", {
    class: "btn ghost",
    onclick: () => {
      if (CATALOG.offset >= CATALOG.limit) {
        CATALOG.offset -= CATALOG.limit;
        loadCatalog();
      }
    },
  }, "← 이전");
  prev.disabled = CATALOG.offset === 0;
  const next = el("button", {
    class: "btn ghost",
    onclick: () => {
      if (CATALOG.offset + CATALOG.limit < CATALOG.total) {
        CATALOG.offset += CATALOG.limit;
        loadCatalog();
      }
    },
  }, "다음 →");
  next.disabled = CATALOG.offset + CATALOG.limit >= CATALOG.total;
  p.appendChild(prev);
  p.appendChild(next);
  p.appendChild(el("span", {}, info));
}

async function toggleRecordDetail(tr, rec) {
  const next = tr.nextElementSibling;
  if (next && next.classList.contains("detail-row")) {
    next.parentNode.removeChild(next);
    return;
  }
  document.querySelectorAll("#cat-table-wrap tr.detail-row").forEach((n) =>
    n.parentNode.removeChild(n)
  );

  const detailRow = el("tr", { class: "detail-row" });
  const detailCell = el("td", { colspan: 7 });
  detailCell.appendChild(el("div", { class: "state" }, "본문 로드 중..."));
  detailRow.appendChild(detailCell);
  tr.parentNode.insertBefore(detailRow, tr.nextSibling);

  try {
    const full = await apiFetch("/api/records/" + encodeURIComponent(rec.id));
    clear(detailCell);
    const dl = el("dl", { class: "detail-block" });
    const fields = [
      ["id", full.id], ["data_type", full.data_type], ["title", full.title],
      ["summary", full.summary], ["tags", (full.tags || []).join(", ")],
      ["agents", (full.agents || []).join(", ")], ["classification", full.classification],
      ["status", full.status], ["domain", full.domain], ["version", full.version],
      ["valid_from", full.valid_from], ["valid_until", full.valid_until],
      ["created_at", fmtDate(full.created_at)], ["updated_at", fmtDate(full.updated_at)],
    ];
    fields.forEach(([k, v]) => {
      if (v == null || v === "") return;
      dl.appendChild(el("dt", {}, k));
      dl.appendChild(el("dd", {}, String(v)));
    });

    const sections = (full.content && full.content.sections) || [];
    if (sections.length > 0) {
      dl.appendChild(el("dt", {}, `sections (${sections.length})`));
      const ul = el("ul", { style: "margin: 0; padding-left: 18px; font-family: var(--mono); font-size: 12px;" });
      sections.slice(0, 10).forEach((s) =>
        ul.appendChild(el("li", {}, s.title || s.section_id || "(no title)"))
      );
      if (sections.length > 10) ul.appendChild(el("li", {}, `... ${sections.length - 10} more`));
      dl.appendChild(el("dd", {}, ul));
    }

    const figures = (full.content && full.content.figures) || [];
    const tables = (full.content && full.content.tables) || [];
    if (figures.length > 0 || tables.length > 0) {
      dl.appendChild(el("dt", {}, "콘텐츠"));
      dl.appendChild(el("dd", {}, `figures: ${figures.length} · tables: ${tables.length}`));
    }
    detailCell.appendChild(dl);
  } catch (err) {
    clear(detailCell);
    detailCell.appendChild(el("div", { class: "state error" }, "본문 로드 실패: " + err.message));
  }
}

LOADERS["catalog"] = loadCatalog;

// ============================================================================
// Section 3: 검색
// ============================================================================
async function runSearch() {
  const target = document.getElementById("search-result");
  const mode = document.getElementById("search-mode").value;
  const q = document.getElementById("search-q").value.trim();
  const limit = parseInt(document.getElementById("search-limit").value, 10) || 20;
  const tagsRaw = document.getElementById("search-tags").value.trim();

  setState(target, "", "검색 중...");
  try {
    const params = new URLSearchParams();
    params.set("mode", mode);
    params.set("limit", String(limit));
    if (mode === "tag") {
      const tags = tagsRaw.split(",").map((s) => s.trim()).filter(Boolean);
      if (tags.length === 0) {
        setState(target, "error", "tag 모드는 tags 입력 필수 (콤마 구분)");
        return;
      }
      tags.forEach((t) => params.append("tags", t));
    } else {
      if (!q) { setState(target, "error", `mode=${mode} 는 q 입력 필수`); return; }
      params.set("q", q);
    }
    const data = await apiFetch("/api/search?" + params.toString());
    renderSearchResult(target, data);
  } catch (err) {
    showError(target, err);
  }
}

function renderSearchResult(target, data) {
  clear(target);
  const items = data.items || [];
  target.appendChild(el("div", { class: "state" },
    `mode=${data.mode || "?"} · total=${data.total ?? items.length} · items=${items.length}`));
  if (items.length === 0) return;

  const table = el("table", { class: "data-table" });
  table.appendChild(el("thead", {}, el("tr", {}, [
    el("th", {}, "score"), el("th", {}, "record_id"),
    el("th", {}, "title"), el("th", {}, "snippet / summary"),
  ])));
  const tbody = el("tbody");
  items.forEach((it) => {
    const recId = it.record_id || it.id || "";
    const title = it.title || "";
    const snippet = it.snippet || it.summary || "";
    const score = (it.score != null) ? Number(it.score).toFixed(4) : "—";
    const tr = el("tr", { class: "clickable" });
    tr.appendChild(el("td", { class: "mono" }, score));
    tr.appendChild(el("td", { class: "mono" }, recId));
    tr.appendChild(el("td", {}, title));
    tr.appendChild(el("td", {}, snippet.slice(0, 200)));
    tr.addEventListener("click", () => jumpToRecord(recId));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  target.appendChild(table);
}

function jumpToRecord(recId) {
  if (!recId) return;
  document.querySelector('nav.tabs button[data-tab="catalog"]').click();
  const target = document.getElementById("cat-table-wrap");
  setState(target, "", `record ${recId} 로드 중...`);
  apiFetch("/api/records/" + encodeURIComponent(recId))
    .then((rec) => {
      CATALOG.total = 1; CATALOG.offset = 0;
      renderCatalog(target, [rec]);
      const firstTr = target.querySelector("table.data-table tbody tr");
      if (firstTr) toggleRecordDetail(firstTr, rec);
    })
    .catch((err) => showError(target, err));
}

// ============================================================================
// Section 4: 그룹 / 분류
// ============================================================================
async function runGroupsAuto() {
  const target = document.getElementById("groups-result");
  const q = document.getElementById("groups-q").value.trim();
  const nGroups = parseInt(document.getElementById("groups-n").value, 10) || 3;
  const topK = parseInt(document.getElementById("groups-topk").value, 10) || 50;
  if (!q) { setState(target, "error", "q 입력 필수"); return; }
  setState(target, "", "그룹화 중...");
  try {
    const data = await apiFetch("/api/groups/auto", {
      method: "POST",
      body: JSON.stringify({ q, n_groups: nGroups, top_k: topK }),
    });
    renderGroups(target, data);
  } catch (err) {
    showError(target, err);
  }
}

function renderGroups(target, data) {
  clear(target);
  const groups = data.groups || [];
  target.appendChild(el("div", { class: "state" },
    `query="${data.query || ""}" · total_records=${data.total_records || 0} · groups=${groups.length}`));
  if (groups.length === 0) return;
  const grid = el("div", { class: "group-grid" });
  groups.forEach((g) => {
    const card = el("div", { class: "group-card" });
    card.appendChild(el("div", { class: "label" }, g.label || "(no label)"));
    const meta = [
      `size: ${g.size || (g.records || []).length}`,
      g.common_domain ? `domain: ${g.common_domain}` : null,
      (g.common_tags || []).length > 0 ? `tags: ${(g.common_tags || []).join(", ")}` : null,
    ].filter(Boolean).join(" · ");
    card.appendChild(el("div", { class: "meta" }, meta));
    const ul = el("ul");
    (g.records || []).slice(0, 5).forEach((r) => {
      const li = el("li", {}, [
        el("span", { class: "tag" }, (r.score != null ? Number(r.score).toFixed(3) : "—")),
        " ",
        el("span", { class: "mono", style: "font-size:11px;" }, r.id || ""),
        el("br"),
        el("span", {}, r.title || ""),
      ]);
      li.addEventListener("click", () => jumpToRecord(r.id));
      ul.appendChild(li);
    });
    card.appendChild(ul);
    grid.appendChild(card);
  });
  target.appendChild(grid);
}

async function loadTaxonomy() {
  const tagBox = document.getElementById("tax-tags");
  const dtBox = document.getElementById("tax-dt");
  const agBox = document.getElementById("tax-agents");
  setState(tagBox, "", "로드 중...");
  setState(dtBox, "", "로드 중...");
  setState(agBox, "", "로드 중...");
  try {
    const [tags, dts, agents] = await Promise.all([
      apiFetch("/api/taxonomy/tags").catch((e) => ({ _err: e })),
      apiFetch("/api/taxonomy/data-types").catch((e) => ({ _err: e })),
      apiFetch("/api/taxonomy/agents").catch((e) => ({ _err: e })),
    ]);
    renderTagCloud(tagBox, tags);
    renderDataTypes(dtBox, dts);
    renderAgents(agBox, agents);
  } catch (err) {
    showError(tagBox, err);
  }
}

function renderTagCloud(target, payload) {
  clear(target);
  if (payload._err) { showError(target, payload._err); return; }
  const tags = payload.tags || payload.items || [];
  if (tags.length === 0) { setState(target, "", "(태그 없음)"); return; }
  const cloud = el("div", { class: "tag-cloud" });
  tags.slice(0, 80).forEach((t) => {
    const name = t.tag || t.name || (typeof t === "string" ? t : "?");
    const count = t.count != null ? t.count : (t.usage_count != null ? t.usage_count : 0);
    cloud.appendChild(el("span", { class: "tag-bubble" }, [
      name, el("span", { class: "count" }, "(" + count + ")"),
    ]));
  });
  target.appendChild(cloud);
}

function renderDataTypes(target, payload) {
  clear(target);
  if (payload._err) { showError(target, payload._err); return; }
  const items = payload.data_types || payload.items || [];
  const table = el("table", { class: "data-table" });
  table.appendChild(el("thead", {}, el("tr", {}, [
    el("th", {}, "data_type"), el("th", {}, "count"), el("th", {}, "description"),
  ])));
  const tbody = el("tbody");
  items.forEach((it) => {
    const tr = el("tr", {});
    tr.appendChild(el("td", { class: "mono" }, it.data_type || it.name || ""));
    tr.appendChild(el("td", {}, String(it.count != null ? it.count : "—")));
    tr.appendChild(el("td", {}, it.description || it.usage || ""));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  target.appendChild(table);
}

function renderAgents(target, payload) {
  clear(target);
  if (payload._err) { showError(target, payload._err); return; }
  const items = payload.agents || payload.items || [];
  const table = el("table", { class: "data-table" });
  table.appendChild(el("thead", {}, el("tr", {}, [
    el("th", {}, "agent_type"), el("th", {}, "record 수"), el("th", {}, "비고"),
  ])));
  const tbody = el("tbody");
  items.forEach((it) => {
    const tr = el("tr", {});
    tr.appendChild(el("td", { class: "mono" }, it.agent_type || it.name || ""));
    tr.appendChild(el("td", {}, String(it.record_count != null ? it.record_count : (it.count != null ? it.count : "—"))));
    tr.appendChild(el("td", {}, it.description || ""));
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  target.appendChild(table);
}

LOADERS["groups"] = loadTaxonomy;

// ============================================================================
// Section 5: API 가이드 (Interactive Explorer driven by /openapi.json)
// ============================================================================

// Friendly tag descriptions (한글 부연설명)
const TAG_LABELS = {
  records:   { ko: "레코드",      desc: "정형 record CRUD · 본문 / lineage / diff" },
  search:    { ko: "검색",         desc: "semantic · fts · tag · faceted" },
  discover:  { ko: "디스커버리",    desc: "허브 카탈로그 · 스키마 · 자연어 ask" },
  groups:    { ko: "의미 그룹",     desc: "자동 클러스터 · 단일 record cluster" },
  taxonomy:  { ko: "분류",         desc: "tag · data_type · agent 어휘" },
  agents:    { ko: "에이전트",      desc: "agent_type 등록 · record 매핑" },
  analytics: { ko: "분석",         desc: "분포 · timeline · cross-agent" },
  data:      { ko: "표 데이터",     desc: "DATA-* record 의 행/열/집계" },
  convert:   { ko: "변환",         desc: "Word/PPT/Excel/PDF → JSON 적재" },
  meta:      { ko: "메타",         desc: "공용 meta 스키마 / 검증" },
  jobs:      { ko: "잡",           desc: "백그라운드 임베딩 / 변환 잡" },
  auth:      { ko: "인증",         desc: "API key 발급 / 검증" },
  system:    { ko: "시스템",        desc: "헬스 · 진단" },
};

const TAG_ORDER = [
  "discover", "search", "records", "groups", "taxonomy",
  "data", "convert", "agents", "analytics", "meta",
  "jobs", "auth", "system",
];

const DOCS = {
  spec: null,        // raw openapi.json
  endpoints: [],     // flattened list
  filtered: [],
  selected: null,
};

async function initDocsExplorer() {
  const meta = document.getElementById("docs-meta");
  meta.textContent = "openapi.json 로드 중...";
  try {
    const spec = await apiFetch("/openapi.json");
    DOCS.spec = spec;
    DOCS.endpoints = flattenEndpoints(spec);
    DOCS.filtered = DOCS.endpoints.slice();
    meta.textContent = `${DOCS.endpoints.length} endpoints · ${
      Object.keys(groupByTag(DOCS.endpoints)).length
    } categories`;
    renderDocsSidebar();
    bindDocsSearch();
  } catch (err) {
    meta.textContent = "openapi.json 로드 실패: " + err.message;
  }
}

LOADERS["docs"] = initDocsExplorer;

// ============================================================================
// Section 5: 조직 관리 (team/group 마스터)
// ============================================================================
const ORG_STATE = { selectedTeam: null };

async function loadOrg() {
  await loadOrgTeams();
}

async function loadOrgTeams() {
  const tbody = document.getElementById("org-teams-body");
  clear(tbody);
  setState(tbody, "loading", "로드 중...");
  try {
    const teams = await apiFetch("/api/org/teams?include_inactive=true");
    clear(tbody);
    if (teams.length === 0) {
      tbody.innerHTML = '<tr><td colspan="5" style="opacity:.6; padding:8px;">(team 없음)</td></tr>';
      return;
    }
    for (const t of teams) {
      const tr = el("tr", { style: t.is_active ? "" : "opacity:.5; font-style:italic;" });
      tr.style.cursor = "pointer";
      tr.appendChild(el("td", {}, t.code));
      tr.appendChild(el("td", {}, t.name));
      tr.appendChild(el("td", { style: "text-align:right;" }, String(t.group_count)));
      tr.appendChild(el("td", { style: "text-align:right;" }, String(t.record_count)));
      const actions = el("td", { style: "text-align:right;" });
      const editBtn = el("button", { class: "btn ghost", "data-action": "edit" }, "수정");
      const delBtn = el("button", { class: "btn ghost", "data-action": "delete", style: "margin-left:4px;" }, "삭제");
      editBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        openOrgModal("team-edit", t);
      });
      delBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        deleteOrgTeam(t.code, t.record_count, t.group_count);
      });
      actions.appendChild(editBtn);
      actions.appendChild(delBtn);
      tr.appendChild(actions);
      tr.addEventListener("click", () => selectOrgTeam(t.code));
      tbody.appendChild(tr);
    }
  } catch (err) {
    clear(tbody);
    showError(tbody, err);
  }
}

async function selectOrgTeam(code) {
  ORG_STATE.selectedTeam = code;
  document.getElementById("org-groups-team-label").textContent = "(team=" + code + ")";
  document.getElementById("org-group-new").disabled = false;
  await loadOrgGroups(code);
}

async function loadOrgGroups(teamCode) {
  const tbody = document.getElementById("org-groups-body");
  clear(tbody);
  setState(tbody, "loading", "로드 중...");
  try {
    const groups = await apiFetch("/api/org/groups?team=" + encodeURIComponent(teamCode) + "&include_inactive=true");
    clear(tbody);
    if (groups.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" style="opacity:.6; padding:8px;">(group 없음)</td></tr>';
      return;
    }
    for (const g of groups) {
      const tr = el("tr", { style: g.is_active ? "" : "opacity:.5; font-style:italic;" });
      tr.appendChild(el("td", {}, g.code));
      tr.appendChild(el("td", {}, g.name));
      tr.appendChild(el("td", { style: "text-align:right;" }, String(g.record_count)));
      const actions = el("td", { style: "text-align:right;" });
      const editBtn = el("button", { class: "btn ghost", "data-action": "edit" }, "수정");
      const delBtn = el("button", { class: "btn ghost", "data-action": "delete", style: "margin-left:4px;" }, "삭제");
      editBtn.addEventListener("click", () => openOrgModal("group-edit", g));
      delBtn.addEventListener("click", () => deleteOrgGroup(g.team_code, g.code, g.record_count));
      actions.appendChild(editBtn);
      actions.appendChild(delBtn);
      tr.appendChild(actions);
      tbody.appendChild(tr);
    }
  } catch (err) {
    clear(tbody);
    showError(tbody, err);
  }
}

function openOrgModal(mode, data) {
  const modal = document.getElementById("org-modal");
  const title = document.getElementById("org-modal-title");
  const modeEl = document.getElementById("org-modal-mode");
  const codeEl = document.getElementById("org-modal-code");
  const nameEl = document.getElementById("org-modal-name");
  const descEl = document.getElementById("org-modal-description");
  const activeEl = document.getElementById("org-modal-active");
  const errEl = document.getElementById("org-modal-error");
  const teamCodeEl = document.getElementById("org-modal-team-code");

  errEl.style.display = "none";
  errEl.textContent = "";
  modeEl.value = mode;

  const titles = {
    "team-new": "신규 Team",
    "team-edit": "Team 수정",
    "group-new": "신규 Group (team=" + (ORG_STATE.selectedTeam || "?") + ")",
    "group-edit": "Group 수정",
  };
  title.textContent = titles[mode] || "편집";

  const isEdit = mode.endsWith("-edit");
  codeEl.disabled = isEdit; // code 는 신규에서만 수정 가능
  if (isEdit) {
    codeEl.value = data.code || "";
    nameEl.value = data.name || "";
    descEl.value = data.description || "";
    activeEl.checked = !!data.is_active;
    teamCodeEl.value = data.team_code || "";
  } else {
    codeEl.value = "";
    nameEl.value = "";
    descEl.value = "";
    activeEl.checked = true;
    teamCodeEl.value = mode === "group-new" ? (ORG_STATE.selectedTeam || "") : "";
  }
  modal.style.display = "flex";
}

function closeOrgModal() {
  document.getElementById("org-modal").style.display = "none";
}

async function submitOrgModal(e) {
  e.preventDefault();
  const mode = document.getElementById("org-modal-mode").value;
  const errEl = document.getElementById("org-modal-error");
  const code = document.getElementById("org-modal-code").value.trim();
  const name = document.getElementById("org-modal-name").value.trim();
  const description = document.getElementById("org-modal-description").value;
  const isActive = document.getElementById("org-modal-active").checked;
  const teamCode = document.getElementById("org-modal-team-code").value;

  try {
    if (mode === "team-new") {
      await apiFetch("/api/org/teams", {
        method: "POST",
        body: JSON.stringify({ code, name, description, is_active: isActive }),
      });
    } else if (mode === "team-edit") {
      await apiFetch("/api/org/teams/" + encodeURIComponent(code), {
        method: "PATCH",
        body: JSON.stringify({ name, description, is_active: isActive }),
      });
    } else if (mode === "group-new") {
      await apiFetch("/api/org/groups", {
        method: "POST",
        body: JSON.stringify({ team_code: teamCode, code, name, description, is_active: isActive }),
      });
    } else if (mode === "group-edit") {
      await apiFetch("/api/org/groups/" + encodeURIComponent(teamCode) + "/" + encodeURIComponent(code), {
        method: "PATCH",
        body: JSON.stringify({ name, description, is_active: isActive }),
      });
    }
    closeOrgModal();
    await loadOrgTeams();
    if (ORG_STATE.selectedTeam) await loadOrgGroups(ORG_STATE.selectedTeam);
  } catch (err) {
    errEl.textContent = err.message || String(err);
    errEl.style.display = "block";
  }
}

async function deleteOrgTeam(code, recordCount, groupCount) {
  if (recordCount > 0) {
    alert("records " + recordCount + "개가 참조 중 — 삭제 불가");
    return;
  }
  if (groupCount > 0) {
    alert("groups " + groupCount + "개가 종속 중 — 먼저 group 을 삭제하라");
    return;
  }
  if (!confirm("team '" + code + "' 삭제? (취소 불가)")) return;
  try {
    await apiFetch("/api/org/teams/" + encodeURIComponent(code), { method: "DELETE" });
    if (ORG_STATE.selectedTeam === code) {
      ORG_STATE.selectedTeam = null;
      document.getElementById("org-groups-team-label").textContent = "(team 선택)";
      document.getElementById("org-group-new").disabled = true;
      clear(document.getElementById("org-groups-body"));
    }
    await loadOrgTeams();
  } catch (err) {
    alert("삭제 실패: " + (err.message || err));
  }
}

async function deleteOrgGroup(teamCode, code, recordCount) {
  if (recordCount > 0) {
    alert("records " + recordCount + "개가 참조 중 — 삭제 불가");
    return;
  }
  if (!confirm("group '" + teamCode + "/" + code + "' 삭제?")) return;
  try {
    await apiFetch("/api/org/groups/" + encodeURIComponent(teamCode) + "/" + encodeURIComponent(code), { method: "DELETE" });
    await loadOrgGroups(teamCode);
    await loadOrgTeams();
  } catch (err) {
    alert("삭제 실패: " + (err.message || err));
  }
}

// ============================================================================
// Section 5: 분석
// ============================================================================
async function loadAnalytics() {
  await loadAnalyticsOverview();
  await loadAnalyticsUsage();
}

async function loadAnalyticsOverview() {
  const target = document.getElementById("analytics-overview");
  setState(target, "", "로드 중...");
  try {
    const dist = await apiFetch("/api/analytics/distribution");
    clear(target);

    const makeCard = (label, obj) => {
      const card = el("div", { class: "card" });
      card.appendChild(el("div", { class: "label" }, label));
      const ul = el("ul");
      const entries = Object.entries(obj || {});
      if (entries.length === 0) {
        ul.appendChild(el("li", {}, [el("span", {}, "(없음)"), el("span", {}, "0")]));
      } else {
        entries.sort((a, b) => b[1] - a[1]).slice(0, 10).forEach(([k, v]) =>
          ul.appendChild(el("li", {}, [el("span", {}, k || "(null)"), el("span", {}, String(v))]))
        );
      }
      card.appendChild(ul);
      return card;
    };
    target.appendChild(makeCard("data_type 분포", dist.by_type));
    target.appendChild(makeCard("team 분포", dist.by_division));
    target.appendChild(makeCard("group 분포", dist.by_team));
    target.appendChild(makeCard("연도 분포", dist.by_year));
  } catch (err) {
    showError(target, err);
  }
}

async function loadAnalyticsTimeline() {
  const target = document.getElementById("analytics-timeline");
  const yearInput = document.getElementById("analytics-year");
  const year = parseInt(yearInput.value, 10) || new Date().getFullYear();
  setState(target, "", "로드 중...");
  try {
    const data = await apiFetch("/api/analytics/timeline?year=" + year);
    clear(target);
    const monthly = data.monthly || [];
    if (monthly.every((m) => m.count === 0)) {
      setState(target, "", `${year}년 데이터 없음`);
      return;
    }
    const max = Math.max(...monthly.map((m) => m.count), 1);
    const MONTH_KO = ["1월","2월","3월","4월","5월","6월","7월","8월","9월","10월","11월","12월"];
    const wrap = el("div", { style: "display:flex; gap:4px; align-items:flex-end; height:80px;" });
    monthly.forEach((m) => {
      const pct = Math.round((m.count / max) * 100);
      const bar = el("div", {
        title: `${MONTH_KO[m.month - 1]}: ${m.count}건`,
        style: `flex:1; height:${pct}%; min-height:2px; background:var(--accent, #1f6feb); border-radius:2px 2px 0 0; cursor:default;`,
      });
      wrap.appendChild(bar);
    });
    target.appendChild(wrap);
    const labels = el("div", { style: "display:flex; gap:4px; margin-top:4px; font-size:10px; color:var(--text-muted, #888);" });
    MONTH_KO.forEach((lbl) => {
      labels.appendChild(el("div", { style: "flex:1; text-align:center;" }, lbl));
    });
    target.appendChild(labels);
    target.appendChild(el("div", { class: "state", style: "margin-top:6px; font-size:11px;" }, `${year}년 총 ${monthly.reduce((s, m) => s + m.count, 0)}건`));
  } catch (err) {
    showError(target, err);
  }
}

async function loadAnalyticsCommonTags() {
  const target = document.getElementById("analytics-tags");
  const agent = document.getElementById("analytics-tags-agent").value.trim();
  if (!agent) { setState(target, "error", "agent_type 입력 필수"); return; }
  setState(target, "", "로드 중...");
  try {
    const items = await apiFetch("/api/analytics/common-tags?agent=" + encodeURIComponent(agent) + "&limit=20");
    clear(target);
    if (items.length === 0) { setState(target, "", "(태그 없음)"); return; }
    const cloud = el("div", { class: "tag-cloud" });
    items.forEach((t) =>
      cloud.appendChild(el("span", { class: "tag-bubble" }, [
        t.tag, el("span", { class: "count" }, "(" + t.count + ")"),
      ]))
    );
    target.appendChild(cloud);
  } catch (err) {
    showError(target, err);
  }
}

async function loadAnalyticsCrossAgent() {
  const target = document.getElementById("analytics-cross");
  const raw = document.getElementById("analytics-cross-agents").value.trim();
  const agents = raw.split(",").map((s) => s.trim()).filter(Boolean);
  if (agents.length < 2) { setState(target, "error", "agent 2개 이상 콤마 구분 필요"); return; }
  setState(target, "", "로드 중...");
  try {
    const params = new URLSearchParams();
    agents.forEach((a) => params.append("agents", a));
    const data = await apiFetch("/api/analytics/cross-agent?" + params.toString());
    clear(target);
    target.appendChild(el("div", { class: "state" }, `공유 레코드: ${data.count}건 / agents=${data.agents?.join(", ")}`));
    if ((data.shared_records || []).length > 0) {
      const ul = el("ul");
      data.shared_records.slice(0, 10).forEach((r) => {
        const li = el("li", { class: "clickable" }, [
          el("span", { class: "mono", style: "font-size:11px;" }, r.id),
          " ", el("span", {}, r.title || ""),
        ]);
        li.addEventListener("click", () => jumpToRecord(r.id));
        ul.appendChild(li);
      });
      target.appendChild(ul);
    }
  } catch (err) {
    showError(target, err);
  }
}

async function loadAnalyticsUsage() {
  const target = document.getElementById("analytics-usage");
  setState(target, "", "로드 중...");
  try {
    const data = await apiFetch("/api/analytics/usage?limit=20");
    clear(target);
    const items = data.items || [];
    if (items.length === 0) { setState(target, "", "(접근 기록 없음)"); return; }
    const table = el("table", { class: "data-table" });
    table.appendChild(el("thead", {}, el("tr", {}, [
      el("th", {}, "record_id"), el("th", {}, "title"),
      el("th", {}, "data_type"), el("th", {}, "read_count"), el("th", {}, "last_accessed"),
    ])));
    const tbody = el("tbody");
    items.forEach((r) => {
      const tr = el("tr", { class: "clickable" });
      tr.appendChild(el("td", { class: "mono" }, r.id));
      tr.appendChild(el("td", {}, r.title || ""));
      tr.appendChild(el("td", {}, r.data_type || ""));
      tr.appendChild(el("td", {}, String(r.read_count || 0)));
      tr.appendChild(el("td", { class: "mono" }, fmtDate(r.last_accessed_at)));
      tr.addEventListener("click", () => jumpToRecord(r.id));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    target.appendChild(table);
  } catch (err) {
    showError(target, err);
  }
}

LOADERS["analytics"] = loadAnalytics;

LOADERS["org"] = loadOrg;

// ============================================================================
// Section 8: MCP 도구 (Wave-5 P2 — Dashboard Upload UI)
// ============================================================================
let _toolsSelectedFile = null;

function _toolsSetState(msg, kind) {
  const el = document.getElementById("tools-upload-state");
  if (!el) return;
  el.textContent = msg || "";
  el.style.color = kind === "err" ? "var(--vscode-errorForeground, #c33)"
                : kind === "ok" ? "var(--vscode-foreground, #393)"
                : "";
}

function _toolsRefreshGoBtn() {
  const btn = document.getElementById("tools-upload-go");
  const uploaderEl = document.getElementById("tools-uploader");
  const uploader = (uploaderEl && uploaderEl.value || "").trim();
  if (btn) btn.disabled = !(_toolsSelectedFile && uploader);
}

function _toolsAcceptFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".zip")) {
    _toolsSetState(`거절: ${file.name} (zip 만 허용)`, "err");
    _toolsSelectedFile = null;
    _toolsRefreshGoBtn();
    return;
  }
  _toolsSelectedFile = file;
  _toolsSetState(`선택: ${file.name} (${(file.size/1024).toFixed(1)} KB)`, "ok");
  _toolsRefreshGoBtn();
}

async function _toolsUpload() {
  if (!_toolsSelectedFile) return;
  const uploader = (document.getElementById("tools-uploader").value || "").trim();
  const dryRun = !!document.getElementById("tools-dryrun").checked;
  const btn = document.getElementById("tools-upload-go");
  btn.disabled = true;
  _toolsSetState(`업로드 중... (${_toolsSelectedFile.name}, ${dryRun ? "dry-run" : "real"})`);

  const fd = new FormData();
  fd.append("bundle", _toolsSelectedFile);
  fd.append("uploader", uploader);
  fd.append("dry_run", dryRun ? "true" : "false");

  try {
    const headers = {};
    const key = getApiKey();
    if (key) headers["X-API-Key"] = key;
    const resp = await fetch(BASE + "/api/mcp_tools/upload", {
      method: "POST",
      headers,
      body: fd,
    });
    const body = await resp.json();
    const pre = document.getElementById("tools-job-pre");
    const wrap = document.getElementById("tools-job-result");
    if (pre && wrap) {
      pre.textContent = JSON.stringify(body, null, 2);
      wrap.style.display = "";
    }
    if (resp.ok) {
      _toolsSetState(`성공: ${body.name || ""} v${body.version || "?"} (status=${body.status})`, "ok");
      _toolsSelectedFile = null;
      document.getElementById("tools-file").value = "";
      loadToolsList();
    } else {
      _toolsSetState(`실패: HTTP ${resp.status}`, "err");
    }
  } catch (e) {
    _toolsSetState(`업로드 에러: ${e.message || e}`, "err");
  } finally {
    _toolsRefreshGoBtn();
  }
}

async function loadToolsList() {
  const wrap = document.getElementById("tools-list-wrap");
  if (!wrap) return;
  setState(wrap, "", "로드 중...");
  try {
    const items = await apiFetch("/api/mcp_tools/");
    const list = Array.isArray(items) ? items : (items.tools || items.items || []);
    if (!list.length) {
      setState(wrap, "", "등록된 도구 없음 (위에서 zip 업로드).");
      return;
    }
    const table = el("table", { class: "data-table" });
    table.appendChild(el("thead", {}, el("tr", {}, [
      el("th", {}, "name"),
      el("th", {}, "version"),
      el("th", {}, "runtime"),
      el("th", {}, "description"),
      el("th", {}, "policy"),
      el("th", {}, "actions"),
    ])));
    const tbody = el("tbody");
    list.forEach((t) => {
      const m = t.manifest || t || {};
      const policyBits = [];
      const ra = (m.restrict_agents || []);
      const rt = (m.require_agent_tag || []);
      const xt = (m.exclude_agent_tag || []);
      if (ra.length) policyBits.push(`restrict=[${ra.join(", ")}]`);
      if (rt.length) policyBits.push(`require=[${rt.join(", ")}]`);
      if (xt.length) policyBits.push(`exclude=[${xt.join(", ")}]`);
      const tr = el("tr", {}, [
        el("td", { class: "mono" }, t.name || m.name || "?"),
        el("td", {}, "v" + (t.current_version || t.version || "?")),
        el("td", {}, m.runtime || "?"),
        el("td", { class: "desc" }, (m.description || "").slice(0, 200)),
        el("td", { class: "muted" }, policyBits.join(" · ") || "(open)"),
        el("td", {},
          el("a", {
            href: `/api/mcp_tools/${encodeURIComponent(t.name || m.name)}`,
            target: "_blank",
          }, "↗ detail")
        ),
      ]);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.innerHTML = "";
    wrap.appendChild(table);
  } catch (err) {
    showError(wrap, err);
  }
}

function initToolsTab() {
  const drop = document.getElementById("tools-drop");
  const fileInput = document.getElementById("tools-file");
  const uploaderInput = document.getElementById("tools-uploader");
  const goBtn = document.getElementById("tools-upload-go");
  const refreshBtn = document.getElementById("tools-refresh");
  if (!drop || !fileInput) return;
  drop.addEventListener("click", () => fileInput.click());
  drop.addEventListener("dragover", (e) => {
    e.preventDefault();
    drop.style.background = "rgba(80, 140, 220, 0.08)";
  });
  drop.addEventListener("dragleave", () => { drop.style.background = ""; });
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    drop.style.background = "";
    const f = e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) _toolsAcceptFile(f);
  });
  fileInput.addEventListener("change", () => {
    const f = fileInput.files && fileInput.files[0];
    if (f) _toolsAcceptFile(f);
  });
  if (uploaderInput) uploaderInput.addEventListener("input", _toolsRefreshGoBtn);
  if (goBtn) goBtn.addEventListener("click", _toolsUpload);
  if (refreshBtn) refreshBtn.addEventListener("click", loadToolsList);
  loadToolsList();
}

LOADERS["tools"] = initToolsTab;

function flattenEndpoints(spec) {
  const out = [];
  const paths = spec.paths || {};
  for (const [path, ops] of Object.entries(paths)) {
    for (const [method, op] of Object.entries(ops)) {
      if (!["get", "post", "patch", "delete", "put"].includes(method)) continue;
      if (!op || op.include_in_schema === false) continue;
      const tags = op.tags || ["misc"];
      out.push({
        id: method + " " + path,
        method: method.toUpperCase(),
        path,
        summary: op.summary || "",
        description: op.description || "",
        params: op.parameters || [],
        requestBody: op.requestBody || null,
        responses: op.responses || {},
        tag: tags[0],
      });
    }
  }
  return out;
}

function groupByTag(eps) {
  const groups = {};
  for (const ep of eps) {
    if (!groups[ep.tag]) groups[ep.tag] = [];
    groups[ep.tag].push(ep);
  }
  return groups;
}

function renderDocsSidebar() {
  const side = document.getElementById("docs-side");
  clear(side);
  const groups = groupByTag(DOCS.filtered);
  const tags = Object.keys(groups).sort((a, b) => {
    const ai = TAG_ORDER.indexOf(a), bi = TAG_ORDER.indexOf(b);
    return (ai === -1 ? 99 : ai) - (bi === -1 ? 99 : bi);
  });
  if (tags.length === 0) {
    side.appendChild(el("div", { class: "state", style: "margin: 16px;" }, "검색 결과 없음"));
    return;
  }
  tags.forEach((tag) => {
    const groupEl = el("div", { class: "docs-side-group" });
    const meta = TAG_LABELS[tag] || { ko: tag, desc: "" };
    const head = el("div", { class: "docs-side-group-head" }, [
      el("span", {}, meta.ko),
      el("span", { class: "count" }, String(groups[tag].length)),
      el("span", { class: "desc" }, meta.desc || ""),
    ]);
    groupEl.appendChild(head);
    groups[tag].forEach((ep) => {
      const row = el("div", {
        class: "docs-ep" + (DOCS.selected && DOCS.selected.id === ep.id ? " active" : ""),
      }, [
        el("span", { class: "method " + ep.method.toLowerCase() }, ep.method),
        el("span", { class: "docs-ep-path", title: ep.path }, ep.path),
      ]);
      row.addEventListener("click", () => selectEndpoint(ep));
      groupEl.appendChild(row);
    });
    side.appendChild(groupEl);
  });
}

function bindDocsSearch() {
  const input = document.getElementById("docs-search");
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    if (!q) DOCS.filtered = DOCS.endpoints.slice();
    else DOCS.filtered = DOCS.endpoints.filter((ep) =>
      ep.path.toLowerCase().includes(q)
      || ep.summary.toLowerCase().includes(q)
      || ep.tag.toLowerCase().includes(q)
      || ep.method.toLowerCase().includes(q)
    );
    renderDocsSidebar();
  });
}

function selectEndpoint(ep) {
  DOCS.selected = ep;
  // mark active in sidebar
  document.querySelectorAll(".docs-ep").forEach((n) =>
    n.classList.toggle("active", n.querySelector(".docs-ep-path")?.title === ep.path
      && n.querySelector(".method")?.textContent === ep.method)
  );
  renderDocsDetail(ep);
}

function renderDocsDetail(ep) {
  const detail = document.getElementById("docs-detail");
  clear(detail);

  // Head
  const pathHtml = ep.path.replace(/\{([^}]+)\}/g, '<span class="seg-var">{$1}</span>');
  const head = el("div", { class: "docs-detail-head" }, [
    el("span", { class: "method " + ep.method.toLowerCase() }, ep.method),
    el("div", { class: "docs-detail-path", html: pathHtml }),
    el("button", {
      class: "btn ghost",
      onclick: () => navigator.clipboard?.writeText(ep.method + " " + ep.path),
    }, "복사"),
  ]);
  detail.appendChild(head);

  if (ep.summary) detail.appendChild(el("div", { class: "docs-summary" }, ep.summary));
  if (ep.description) detail.appendChild(el("div", { class: "docs-desc", text: ep.description }));

  // Tag pill
  const tagMeta = TAG_LABELS[ep.tag] || { ko: ep.tag, desc: "" };
  detail.appendChild(el("div", { style: "margin-bottom: 10px;" }, [
    el("span", { class: "card", style: "display: inline-block; padding: 4px 10px; font-size: 11px; color: var(--text-muted);" }, [
      el("strong", { style: "color: var(--accent);" }, tagMeta.ko),
      "  · ",
      tagMeta.desc,
    ]),
  ]));

  // Parameters section
  const allParams = [
    ...(ep.params || []),
  ];
  if (allParams.length > 0) {
    const sec = el("div", { class: "docs-section" });
    sec.appendChild(el("h4", { class: "docs-section-h" }, [
      "파라미터",
      el("span", { class: "count" }, String(allParams.length)),
    ]));
    const tb = el("table", { class: "params-table" });
    tb.appendChild(el("thead", {}, el("tr", {}, [
      el("th", {}, "name"), el("th", {}, "in"), el("th", {}, "type"),
      el("th", {}, "required"), el("th", {}, "description"),
    ])));
    const tbody = el("tbody");
    allParams.forEach((p) => {
      const sch = p.schema || {};
      const enumStr = sch.enum ? ` (${sch.enum.join(" | ")})` : "";
      const desc = p.description || "";
      const tr = el("tr", {}, [
        el("td", {}, el("span", { class: "pname" }, p.name)),
        el("td", {}, el("span", { class: "pin" }, p.in)),
        el("td", {}, el("span", { class: "ptype" }, (sch.type || "string") + enumStr)),
        el("td", {}, p.required ? el("span", { class: "preq" }, "REQUIRED") : ""),
        el("td", { class: "pdesc" }, desc),
      ]);
      tbody.appendChild(tr);
    });
    tb.appendChild(tbody);
    sec.appendChild(tb);
    detail.appendChild(sec);
  }

  // Request body section
  if (ep.requestBody) {
    const sec = el("div", { class: "docs-section" });
    sec.appendChild(el("h4", { class: "docs-section-h" }, "요청 본문"));
    const ct = (ep.requestBody.content || {});
    const ctKey = Object.keys(ct)[0] || "application/json";
    sec.appendChild(el("div", { class: "state" },
      `Content-Type: ${ctKey} ${ep.requestBody.required ? "(required)" : ""}`));
    detail.appendChild(sec);
  }

  // Try-it section
  detail.appendChild(buildTryItSection(ep));

  // Response section (placeholder)
  const respSec = el("div", { class: "docs-section", id: "resp-sec" });
  respSec.appendChild(el("h4", { class: "docs-section-h" }, "응답"));
  respSec.appendChild(el("div", { class: "state" }, "[Try] 클릭 시 실시간 응답 표시"));
  detail.appendChild(respSec);
}

function buildTryItSection(ep) {
  const sec = el("div", { class: "docs-section" });
  sec.appendChild(el("h4", { class: "docs-section-h" }, "Try"));
  const form = el("div", { class: "try-form" });
  const grid = el("div", { class: "try-form-grid" });

  // Param inputs
  const inputs = {};
  (ep.params || []).forEach((p) => {
    const sch = p.schema || {};
    const id = "try-" + ep.method + "-" + ep.path + "-" + p.name;
    let inputEl;
    if (sch.enum) {
      inputEl = el("select", { id });
      sch.enum.forEach((v) => inputEl.appendChild(el("option", { value: v }, v)));
    } else if (sch.type === "integer" || sch.type === "number") {
      inputEl = el("input", {
        id, type: "number",
        placeholder: sch.default != null ? String(sch.default) : "",
      });
      if (sch.default != null) inputEl.value = String(sch.default);
    } else if (sch.type === "boolean") {
      inputEl = el("select", { id });
      ["false", "true"].forEach((v) => inputEl.appendChild(el("option", { value: v }, v)));
    } else {
      inputEl = el("input", {
        id, type: "text",
        placeholder: sch.default != null ? String(sch.default) : (p.description || ""),
      });
      if (sch.default != null) inputEl.value = String(sch.default);
    }
    inputs[p.name] = { p, el: inputEl };
    grid.appendChild(el("label", { class: "field" }, [
      el("span", { class: "field-label" }, [
        p.name,
        p.required ? el("span", { class: "preq", style: "margin-left: 4px;" }, "*") : null,
        el("span", { class: "pin", style: "margin-left: 6px; font-weight: 400;" }, p.in),
      ]),
      inputEl,
    ]));
  });

  if (Object.keys(inputs).length > 0) form.appendChild(grid);
  else form.appendChild(el("div", { class: "state", style: "margin: 0;" }, "(파라미터 없음)"));

  // Body editor (if requestBody)
  let bodyEditor = null;
  if (ep.requestBody) {
    form.appendChild(el("div", { style: "margin-top: 12px; font-size: 11px; color: var(--text-muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;" }, "request body (JSON)"));
    const example = guessExampleBody(ep);
    bodyEditor = el("textarea", { class: "body-editor", spellcheck: "false" }, example);
    form.appendChild(bodyEditor);
  }

  // Actions
  const status = el("span", { class: "try-form-status" }, "");
  const sendBtn = el("button", { class: "btn" }, ep.method + " 전송");
  const actions = el("div", { class: "try-form-actions" }, [sendBtn, status]);
  form.appendChild(actions);

  sendBtn.addEventListener("click", async () => {
    let pathFilled = ep.path;
    const queryParams = new URLSearchParams();
    let missing = null;
    for (const name of Object.keys(inputs)) {
      const { p, el: ie } = inputs[name];
      const val = ie.value.trim();
      if (!val) {
        if (p.required) { missing = name; break; }
        continue;
      }
      if (p.in === "path") {
        pathFilled = pathFilled.replace("{" + name + "}", encodeURIComponent(val));
      } else if (p.in === "query") {
        queryParams.append(name, val);
      } else if (p.in === "header") {
        // not supported in this UI for now
      }
    }
    if (missing) {
      status.innerHTML = '<span class="err">필수 파라미터 누락: ' + missing + "</span>";
      return;
    }
    const url = pathFilled + (queryParams.toString() ? "?" + queryParams.toString() : "");
    const opts = { method: ep.method };
    if (bodyEditor) {
      try { JSON.parse(bodyEditor.value); }
      catch (e) {
        status.innerHTML = '<span class="err">JSON 파싱 실패: ' + e.message + "</span>";
        return;
      }
      opts.body = bodyEditor.value;
    }
    sendBtn.disabled = true;
    status.textContent = "전송 중...";
    try {
      const r = await apiFetchRaw(url, opts);
      const cls = "s" + Math.floor(r.status / 100);
      status.innerHTML = `<span class="${r.ok ? "ok" : "err"}">${r.status}</span> · ${r.ms}ms`;
      renderResponse(r, url, cls);
    } catch (err) {
      status.innerHTML = '<span class="err">network error</span>';
      renderResponse({ status: 0, ok: false, body: err.message, ms: 0 }, url, "s5");
    } finally {
      sendBtn.disabled = false;
    }
  });

  sec.appendChild(form);
  return sec;
}

function renderResponse(r, url, cls) {
  const respSec = document.getElementById("resp-sec");
  if (!respSec) return;
  clear(respSec);
  respSec.appendChild(el("h4", { class: "docs-section-h" }, "응답"));
  const shell = el("div", { class: "resp-shell" });
  const head = el("div", { class: "resp-head" }, [
    el("span", { class: "status " + cls }, String(r.status || "ERR")),
    el("span", {}, url),
    el("span", {}, r.ms + "ms"),
    el("button", {
      class: "copy-btn",
      onclick: () => {
        const txt = typeof r.body === "string" ? r.body : JSON.stringify(r.body, null, 2);
        navigator.clipboard?.writeText(txt);
      },
    }, "📋 복사"),
  ]);
  shell.appendChild(head);
  const pre = el("pre", { class: "resp-body" });
  pre.innerHTML = highlightJson(r.body);
  shell.appendChild(pre);
  respSec.appendChild(shell);
}

function guessExampleBody(ep) {
  // Best-effort: pull example from spec or build a minimal stub.
  const ct = ep.requestBody?.content || {};
  const json = ct["application/json"];
  if (json) {
    if (json.example) return JSON.stringify(json.example, null, 2);
    const examples = json.examples;
    if (examples && typeof examples === "object") {
      const first = Object.values(examples)[0];
      if (first && first.value) return JSON.stringify(first.value, null, 2);
    }
    const sch = json.schema;
    if (sch) {
      const stub = stubFromSchema(sch, DOCS.spec);
      return JSON.stringify(stub, null, 2);
    }
  }
  return "{}";
}

function stubFromSchema(sch, spec, depth = 0) {
  if (!sch || depth > 4) return null;
  if (sch.$ref) {
    const ref = sch.$ref;
    if (ref.startsWith("#/components/schemas/")) {
      const name = ref.slice("#/components/schemas/".length);
      const resolved = (spec.components?.schemas || {})[name];
      if (resolved) return stubFromSchema(resolved, spec, depth + 1);
    }
    return null;
  }
  if (sch.example !== undefined) return sch.example;
  if (sch.default !== undefined) return sch.default;
  if (sch.type === "object" || sch.properties) {
    const out = {};
    const props = sch.properties || {};
    const required = sch.required || [];
    for (const [k, v] of Object.entries(props)) {
      if (depth > 0 && !required.includes(k) && Object.keys(out).length >= 4) continue;
      out[k] = stubFromSchema(v, spec, depth + 1);
    }
    return out;
  }
  if (sch.type === "array") {
    return [stubFromSchema(sch.items || {}, spec, depth + 1)];
  }
  if (sch.enum) return sch.enum[0];
  if (sch.type === "integer" || sch.type === "number") return 0;
  if (sch.type === "boolean") return false;
  return "";
}

// ---- LLM markdown loader (within details) ---------------------------------
async function loadAgentGuide() {
  const target = document.getElementById("docs-output");
  const size = document.getElementById("docs-size").value;
  setState(target, "", "로드 중...");
  try {
    const params = new URLSearchParams({ size });
    const text = await apiFetch("/api/docs/agent-guide?" + params.toString());
    const pre = el("pre", { class: "docs-output" });
    pre.textContent = typeof text === "string" ? text : JSON.stringify(text, null, 2);
    clear(target);
    target.appendChild(pre);
  } catch (err) {
    showError(target, err);
  }
}

// ---- quick links from empty-state ------------------------------------------
function bindDocsQuickLinks() {
  document.querySelectorAll(".docs-quicklinks [data-quick]").forEach((b) => {
    b.addEventListener("click", () => {
      const path = b.dataset.quick;
      const ep = DOCS.endpoints.find((e) => e.path === path && e.method === "GET");
      if (ep) selectEndpoint(ep);
    });
  });
}

// ============================================================================
// API key UI
// ============================================================================
function initApiKeyBox() {
  const input = document.getElementById("api-key-input");
  const btn = document.getElementById("api-key-save");
  const clearBtn = document.getElementById("api-key-clear");
  input.value = getApiKey();
  btn.addEventListener("click", () => {
    setApiKey(input.value.trim());
    btn.textContent = "✓ 저장됨";
    setTimeout(() => (btn.textContent = "저장"), 1200);
  });
  clearBtn.addEventListener("click", () => {
    input.value = "";
    setApiKey("");
    clearBtn.textContent = "✓ 삭제됨";
    setTimeout(() => (clearBtn.textContent = "삭제"), 1200);
  });
}

// ============================================================================
// Bootstrap
// ============================================================================
window.addEventListener("DOMContentLoaded", () => {
  initApiKeyBox();
  initTabs();

  document.getElementById("status-refresh").addEventListener("click", loadStatus);
  document.getElementById("cat-refresh").addEventListener("click", () => {
    CATALOG.offset = 0;
    loadCatalog();
  });
  document.getElementById("search-run").addEventListener("click", runSearch);
  document.getElementById("groups-run").addEventListener("click", runGroupsAuto);
  document.getElementById("groups-tax-refresh").addEventListener("click", loadTaxonomy);
  document.getElementById("docs-load").addEventListener("click", loadAgentGuide);
  bindDocsQuickLinks();

  // 분석 탭 이벤트
  const analyticsRefresh = document.getElementById("analytics-refresh");
  if (analyticsRefresh) analyticsRefresh.addEventListener("click", loadAnalytics);
  const analyticsTimelineRun = document.getElementById("analytics-timeline-run");
  if (analyticsTimelineRun) analyticsTimelineRun.addEventListener("click", loadAnalyticsTimeline);
  const analyticsTagsRun = document.getElementById("analytics-tags-run");
  if (analyticsTagsRun) analyticsTagsRun.addEventListener("click", loadAnalyticsCommonTags);
  const analyticsCrossRun = document.getElementById("analytics-cross-run");
  if (analyticsCrossRun) analyticsCrossRun.addEventListener("click", loadAnalyticsCrossAgent);

  // 조직 관리 탭 이벤트
  const orgRefresh = document.getElementById("org-refresh");
  if (orgRefresh) orgRefresh.addEventListener("click", loadOrg);
  const orgTeamNew = document.getElementById("org-team-new");
  if (orgTeamNew) orgTeamNew.addEventListener("click", () => openOrgModal("team-new", {}));
  const orgGroupNew = document.getElementById("org-group-new");
  if (orgGroupNew) orgGroupNew.addEventListener("click", () => openOrgModal("group-new", {}));
  const orgModalCancel = document.getElementById("org-modal-cancel");
  if (orgModalCancel) orgModalCancel.addEventListener("click", closeOrgModal);
  const orgModalForm = document.getElementById("org-modal-form");
  if (orgModalForm) orgModalForm.addEventListener("submit", submitOrgModal);

  // 첫 탭 자동 로드
  loadStatus();
  document.querySelector('nav.tabs button[data-tab="status"]').dataset.loaded = "1";

  // Enter 키로 검색 실행
  document.getElementById("search-q").addEventListener("keydown", (e) => {
    if (e.key === "Enter") runSearch();
  });
  document.getElementById("groups-q").addEventListener("keydown", (e) => {
    if (e.key === "Enter") runGroupsAuto();
  });
});
