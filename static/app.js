"use strict";

const IS_AUTHENTICATED = document.body.dataset.authenticated === "true";
const root = document.getElementById("view-root");
const state = {
  view: "overview",
  laws: [],
  rules: [],
  lawQuery: "",
  lawType: "",
  lawCategory: "",
  lawCategories: [],
  recentSearches: [],
  lawPage: 1,
  ruleQuery: "",
  rulePage: 1,
  historyKind: "",
  historyUuid: "",
  historyCommits: [],
  diffOlder: "",
  diffNewer: "",
  currentRevision: "",
  lawDisplayMode: "markdown",
  revisionDisplayMode: "markdown",
  lastDetail: null,
  lastRevisionData: null,
  lastSearch: ""
};

const pageTitles = {
  overview: {context: "ライブラリ", title: "概要"},
  laws: {context: "ライブラリ", title: "法令"},
  rules: {context: "ライブラリ", title: "ルール"},
  activity: {context: "リポジトリ", title: "変更履歴"},
  integrity: {context: "リポジトリ", title: "検証・Git"},
  search: {context: "検索", title: "検索結果"}
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}

function formatNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString("ja-JP") : "—";
}

function dateText(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("ja-JP", {
    year: "numeric", month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit"
  }).format(date);
}

function shortHash(value) {
  return String(value || "").slice(0, 8) || "—";
}

function setPage(view, titleOverride) {
  state.view = view;
  const config = pageTitles[view] || pageTitles.overview;
  const context = document.getElementById("page-context");
  const title = document.getElementById("page-title");
  if (context) context.textContent = config.context;
  if (title) title.textContent = titleOverride || config.title;

  const activeView = view === "search" ? "laws" : view;
  document.querySelectorAll("[data-view]").forEach(button => {
    const active = button.dataset.view === activeView;
    button.classList.toggle("active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

function setContent(markup) {
  root.innerHTML = markup;
  root.setAttribute("aria-busy", "false");
}

function loading(message) {
  root.setAttribute("aria-busy", "true");
  root.innerHTML = '<div class="loading-state"><span class="loader" aria-hidden="true"></span><p>' +
    esc(message || "読み込んでいます") + '</p></div>';
}

async function api(path) {
  const response = await fetch("/api/" + String(path).replace(/^\/+/, ""), {
    method: "GET",
    headers: {"Accept": "application/json"},
    credentials: "same-origin",
    cache: "no-store"
  });

  if (response.status === 401) {
    location.assign("/login");
    throw new Error("ログインセッションが終了しました。");
  }

  let payload;
  try {
    payload = await response.json();
  } catch (_) {
    throw new Error("サーバー応答を読み取れませんでした。");
  }

  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || "リクエストに失敗しました。");
  }
  return payload.result;
}

function connection(online, label, repo) {
  const card = document.getElementById("connection-card");
  const dot = document.getElementById("sidebar-status-dot");
  const status = document.getElementById("sidebar-status");
  const repository = document.getElementById("sidebar-repo");
  if (card) card.classList.toggle("is-offline", !online);
  if (dot) {
    dot.classList.toggle("online", !!online);
    dot.classList.toggle("offline", !online);
  }
  if (status) status.textContent = label;
  if (repository) repository.textContent = repo || "LawGit API";
}

function surface(title, subtitle, body, action, actionLabel, meta) {
  return '<section class="surface">' +
    '<div class="surface-header"><div class="surface-heading"><h2 class="surface-title">' + esc(title) + '</h2>' +
    (subtitle ? '<p class="surface-subtitle">' + esc(subtitle) + '</p>' : "") +
    '</div>' +
    (action ? '<button class="surface-link" type="button" data-action="' + esc(action) + '">' + esc(actionLabel || "すべて見る") + ' →</button>' :
      (meta ? '<span class="surface-meta">' + esc(meta) + '</span>' : "")) +
    '</div><div class="surface-body">' + body + '</div></section>';
}

function emptyState(title, detail, symbol) {
  return '<div class="empty-state"><div class="empty-mark" aria-hidden="true">' +
    esc(symbol || "—") + '</div><strong>' + esc(title) + '</strong><p>' +
    esc(detail || "") + '</p></div>';
}

function inlineMarkdown(value) {
  let text = esc(value);
  text = text.replace(/`([^`]+)`/g, "<code>$1</code>");
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, "<em>$1</em>");
  return text;
}

function splitMarkdownTableRow(line) {
  const value = line.trim();
  if (!value.startsWith("|") || !value.endsWith("|")) return null;
  const cells = [];
  let cell = "";
  let escaped = false;
  for (const char of value.slice(1, -1)) {
    if (escaped) {
      cell += char;
      escaped = false;
    } else if (char === "\\") {
      escaped = true;
    } else if (char === "|") {
      cells.push(cell.trim());
      cell = "";
    } else {
      cell += char;
    }
  }
  if (escaped) cell += "\\";
  cells.push(cell.trim());
  return cells;
}

function isMarkdownTableSeparator(line) {
  const cells = splitMarkdownTableRow(line);
  return !!cells && cells.length > 0 && cells.every(cell => /^:?-{3,}:?$/.test(cell));
}

function markdownToHtml(markdown, hideDocumentTitle = false) {
  const lines = String(markdown || "").replace(/\r\n?/g, "\n").split("\n");
  const html = [];
  let paragraph = [];
  let code = [];
  let inCode = false;
  let documentTitleSkipped = false;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    html.push('<p class="md-paragraph">' + paragraph.map(inlineMarkdown).join("<br>") + "</p>");
    paragraph = [];
  };
  const flushCode = () => {
    html.push('<pre class="md-code"><code>' + esc(code.join("\n")) + "</code></pre>");
    code = [];
    inCode = false;
  };

  for (let lineIndex = 0; lineIndex < lines.length; lineIndex++) {
    const line = lines[lineIndex];
    if (line.trimStart().startsWith("```")) {
      flushParagraph();
      if (inCode) flushCode();
      else inCode = true;
      continue;
    }
    if (inCode) {
      code.push(line);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      continue;
    }
    const tableHeader = splitMarkdownTableRow(line);
    if (tableHeader && lineIndex + 1 < lines.length && isMarkdownTableSeparator(lines[lineIndex + 1])) {
      flushParagraph();
      const tableRows = [];
      lineIndex += 2;
      while (lineIndex < lines.length) {
        const cells = splitMarkdownTableRow(lines[lineIndex]);
        if (!cells) {
          lineIndex--;
          break;
        }
        tableRows.push(cells);
        lineIndex++;
      }
      const header = '<thead><tr>' + tableHeader.map(cell => '<th>' + inlineMarkdown(cell) + '</th>').join("") + '</tr></thead>';
      const body = tableRows.length ?
        '<tbody>' + tableRows.map(row => '<tr>' +
          tableHeader.map((_, index) => '<td>' + inlineMarkdown(row[index] || "") + '</td>').join("") +
          '</tr>').join("") + '</tbody>' : "";
      html.push('<div class="md-table-wrap"><table class="md-table">' + header + body + '</table></div>');
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      const level = heading[1].length;
      if (hideDocumentTitle && level === 1 && !documentTitleSkipped) {
        documentTitleSkipped = true;
        continue;
      }
      html.push('<h' + level + ' class="md-heading md-heading-' + level + '">' + inlineMarkdown(heading[2]) + '</h' + level + '>');
      continue;
    }
    if (/^\s*---+\s*$/.test(line)) {
      flushParagraph();
      html.push('<hr class="md-rule">');
      continue;
    }
    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      html.push('<blockquote class="md-quote">' + inlineMarkdown(quote[1]) + '</blockquote>');
      continue;
    }
    const item = line.match(/^(\s*)(?:([-*+])\s+|([0-9０-９一二三四五六七八九十百千]+)[.)．]\s+)(.*)$/);
    if (item) {
      flushParagraph();
      const depth = Math.min(10, Math.floor(item[1].length / 2));
      const marker = item[3] ? item[3] + "." : "•";
      html.push('<div class="md-list-item md-list-depth-' + depth + '">' +
        '<span class="md-list-marker">' + esc(marker) + '</span>' +
        '<span>' + inlineMarkdown(item[4]) + '</span></div>');
      continue;
    }
    paragraph.push(line);
  }
  flushParagraph();
  if (inCode) flushCode();
  return html.join("");
}

function diffToHtml(diff) {
  if (!diff) return emptyState("変更はありません", "選択した2つの版のMarkdown表示は同一です。", "=");
  return '<div class="diff-view">' + String(diff).split("\n").map(line => {
    let kind = "context";
    if (line.startsWith("+++ ") || line.startsWith("--- ")) kind = "file";
    else if (line.startsWith("@@")) kind = "hunk";
    else if (line.startsWith("+")) kind = "added";
    else if (line.startsWith("-")) kind = "removed";
    const marker = line.slice(0, 1);
    return '<div class="diff-line diff-' + kind + '"><span class="diff-marker">' +
      esc(kind === "added" ? "+" : kind === "removed" ? "−" : kind === "hunk" ? "↳" : kind === "file" ? marker : " ") +
      '</span><code>' + esc(kind === "added" || kind === "removed" || kind === "context" ? line.slice(1) : line) +
      '</code></div>';
  }).join("") + '</div>';
}

function statusRow(title, message, tone) {
  return '<div class="status-row ' + esc(tone || "") + '"><div><strong>' + esc(title) +
    '</strong><p>' + esc(message || "") + '</p></div></div>';
}

function metric(label, value, foot) {
  const display = typeof value === "number" ? formatNumber(value) :
    (value === null || value === undefined || value === "" ? "—" : String(value));
  return '<article class="metric-card"><div class="metric-label">' + esc(label) +
    '</div><div class="metric-value">' + esc(display) +
    '</div><div class="metric-foot">' + esc(foot || "") + '</div></article>';
}

function documentCard(item, kind) {
  const isLaw = kind === "law";
  const name = item.name || "名称未設定";
  const type = isLaw ? (item.type || "種別未設定") : (item.category || "general");
  const secondary = isLaw ? (item.law_number || item.e_gov_id || "法令番号なし") :
    (item.created || "作成日不明");
  const tail = isLaw ? (item.category || "") : shortHash(item.uuid);
  return '<button class="document-card" type="button" data-action="' + (isLaw ? "law" : "rule") +
    '" data-uuid="' + esc(item.uuid) + '">' +
    '<span class="document-symbol" aria-hidden="true">' + (isLaw ? "§" : "≡") + '</span>' +
    '<span class="document-main"><span class="document-name">' + esc(name) + '</span>' +
    '<span class="document-meta"><span class="pill neutral">' + esc(type) + '</span><span>' +
    esc(secondary) + '</span></span></span>' +
    (tail ? '<span class="document-tail">' + esc(tail) + '</span>' : "") +
    '<span class="document-arrow" aria-hidden="true">›</span></button>';
}

function documentList(items, kind, limit) {
  const list = (items || []).slice(0, limit || items.length);
  if (!list.length) {
    return emptyState(
      kind === "law" ? "法令がありません" : "ルールがありません",
      kind === "law" ? "法令データが登録されると、ここに表示されます。" :
        "LawGitにルールが登録されると、ここに表示されます。",
      kind === "law" ? "§" : "≡"
    );
  }
  return '<div class="document-list">' + list.map(item => documentCard(item, kind)).join("") + '</div>';
}

function historyRows(items, limit) {
  const commits = (items || []).slice(0, limit || items.length);
  if (!commits.length) return emptyState("変更履歴はありません", "Gitのコミットが追加されると、ここに表示されます。", "↗");
  return '<div class="history-list">' + commits.map(commit => {
    const files = (commit.files || []).map(file => file.path).filter(Boolean).slice(0, 3).join("、");
    const subject = commit.subject || commit.message || "コミット";
    const hash = commit.hash || "";
    return '<article class="history-item"><div class="history-rail"><span class="history-dot"></span></div>' +
      '<div class="history-main"><div class="history-title">' + esc(subject) + '</div>' +
      '<div class="history-meta mono">' + esc([shortHash(hash), commit.author, files].filter(Boolean).join(" · ")) + '</div></div>' +
      '<time class="history-time">' + esc(dateText(commit.date || commit.author_date || commit.committer_date)) + '</time></article>';
  }).join("") + '</div>';
}

function errorView(error) {
  setContent('<section class="error-panel"><h2>データを読み込めませんでした</h2><p>' +
    esc(error?.message || String(error)) +
    '</p><button class="action-button" type="button" data-action="refresh">再試行</button></section>');
}

function offlineView(message) {
  connection(false, "LawGit未接続", "接続待ち");
  setContent('<div class="view-stack">' +
    '<section class="welcome-panel"><div class="welcome-copy"><p class="eyebrow">接続状態</p>' +
    '<h2>LawGitに接続できません</h2><p>' + esc(message || "LawGit APIの起動状態を確認してください。") +
    '</p></div><div class="welcome-actions"><button class="action-button primary" type="button" data-action="refresh">再接続</button></div></section>' +
    statusRow("確認すること", "LawGit APIが起動しているか、.envのLAWGIT_REPOとLAWGIT_BINが正しいか確認してください。", "warning") +
    '</div>');
}

function categorySurface(categories, kind) {
  const list = categories || [];
  if (!list.length) return emptyState("カテゴリがありません", "分類情報はまだありません。");
  return '<div class="document-list">' + list.slice(0, 8).map(category =>
    '<div class="document-card category-row"><span class="document-symbol" aria-hidden="true">#</span>' +
    '<span class="document-main"><span class="document-name">' + esc(category.category || "未分類") +
    '</span><span class="document-meta">' + esc(kind === "law" ? "法令カテゴリ" : "ルールカテゴリ") +
    '</span></span><span class="document-tail">' + esc(formatNumber(category.count)) + ' 件</span></div>'
  ).join("") + '</div>';
}

async function loadOverview() {
  setPage("overview");
  loading("概要を読み込んでいます");
  try {
    const data = await api(IS_AUTHENTICATED ? "overview" : "public/overview");
    if (!data.connected) {
      offlineView(data.error);
      return;
    }

    const ping = data.ping || {};
    const laws = data.laws?.laws || [];
    const lawCategories = data.categories?.laws || [];
    const errors = Object.keys(data.errors || {});
    connection(true, "接続済み", ping.root || "LawGit API");

    if (!IS_AUTHENTICATED) {
      state.lawCategories = lawCategories;
      const publicIntro = '<section class="welcome-panel public-search-welcome"><div class="welcome-copy">' +
        '<p class="eyebrow">法令ライブラリ</p><h2>言葉から、法令を探す。</h2>' +
        '<p>法令名・法令番号だけでなく、条文の中にある語句からも検索できます。気になる法令を開いて本文を読めます。</p>' +
        '</div><div class="welcome-actions"><a class="action-button primary" href="/register">アカウントを作成</a>' +
        '<a class="action-button" href="/login">ログイン</a></div></section>';
      const quick = '<div class="quick-links">' +
        '<button class="quick-link" type="button" data-action="focus-search"><span class="quick-link-mark">⌕</span>' +
        '<span class="quick-link-copy"><strong>全文検索</strong><small>条文の中の言葉から探す</small></span><span class="quick-link-arrow">›</span></button>' +
        '<button class="quick-link" type="button" data-action="go-laws"><span class="quick-link-mark">§</span>' +
        '<span class="quick-link-copy"><strong>法令一覧</strong><small>種別・カテゴリから探す</small></span><span class="quick-link-arrow">›</span></button>' +
        '<div class="quick-link quick-link-static"><span class="quick-link-mark">↗</span>' +
        '<span class="quick-link-copy"><strong>読み取り専用</strong><small>文書の変更・削除はできません</small></span></div></div>';
      const metrics = '<div class="metrics-grid">' +
        metric("公開法令", laws.length, "閲覧できる法令") +
        metric("カテゴリ", lawCategories.length, "法令の分類") +
        '</div>';
      const recent = surface("法令", "本文まで読める公開法令", documentList(laws, "law", 6), "go-laws", "法令一覧");
      setContent('<div class="view-stack">' + publicIntro + quick + metrics + renderRecentSearches() +
        (errors.length ? statusRow("一部の情報を取得できません", errors.join("、"), "warning") : "") +
        recent + '</div>');
      return;
    }

    const rules = data.rules?.rules || [];
    const commits = data.log?.commits || [];
    const ruleCategories = data.categories?.rules || [];
    const intro = '<section class="welcome-panel"><div class="welcome-copy">' +
      '<p class="eyebrow">ライブラリ</p><h2>法令とルールを、ひとつの場所で確認する</h2>' +
      '<p>法令本文、ユーザー定義ルール、改正履歴を横断して閲覧できます。</p></div>' +
      '<div class="welcome-actions"><button class="action-button primary" type="button" data-action="go-laws">法令を探す</button>' +
      '<button class="action-button" type="button" data-action="go-rules">ルールを見る</button></div></section>';
    const metrics = '<div class="metrics-grid">' +
      metric("法令", laws.length, "登録文書") +
      metric("ルール", rules.length, "ユーザー定義") +
      metric("法令カテゴリ", lawCategories.length, "分類数") +
      metric("ルールカテゴリ", ruleCategories.length, "分類数") + '</div>';
    const quick = '<div class="quick-links">' +
      '<button class="quick-link" type="button" data-action="go-laws"><span class="quick-link-mark">§</span>' +
      '<span class="quick-link-copy"><strong>法令を探す</strong><small>一覧・種別・番号から</small></span><span class="quick-link-arrow">›</span></button>' +
      '<button class="quick-link" type="button" data-action="go-rules"><span class="quick-link-mark">≡</span>' +
      '<span class="quick-link-copy"><strong>ルールを見る</strong><small>ユーザー定義文書</small></span><span class="quick-link-arrow">›</span></button>' +
      '<button class="quick-link" type="button" data-action="go-activity"><span class="quick-link-mark">↗</span>' +
      '<span class="quick-link-copy"><strong>変更履歴</strong><small>Gitの記録を確認</small></span><span class="quick-link-arrow">›</span></button></div>';
    const grids = '<div class="content-grid">' +
      surface("最近の法令", "登録されている法令", documentList(laws, "law", 5), "go-laws", "一覧を見る") +
      surface("最近の変更", "Gitコミット", historyRows(commits, 5), "go-activity", "履歴を見る") +
      surface("ルール", "登録されたユーザー定義ルール", documentList(rules, "rule", 5), "go-rules", "一覧を見る") +
      surface("カテゴリ", "法令の分類", categorySurface(lawCategories, "law"), null, null, formatNumber(lawCategories.length) + " 種") +
      '</div>';
    setContent('<div class="view-stack">' + intro + metrics + quick +
      (errors.length ? statusRow("一部の情報を取得できません", errors.join("、"), "warning") : "") +
      grids + '</div>');
  } catch (error) {
    offlineView(error.message);
  }
}

function pagination(page, totalPages, action) {
  if (totalPages <= 1) return "";
  const previous = Math.max(1, page - 1);
  const next = Math.min(totalPages, page + 1);
  return '<nav class="pagination" aria-label="ページ送り">' +
    '<button class="page-button" type="button" data-action="' + action + '" data-page="' + previous + '"' +
    (page === 1 ? " disabled" : "") + '>前へ</button>' +
    '<span class="page-range">' + page + ' / ' + totalPages + '</span>' +
    '<button class="page-button" type="button" data-action="' + action + '" data-page="' + next + '"' +
    (page === totalPages ? " disabled" : "") + '>次へ</button></nav>';
}

function renderLawList() {
  setPage("laws");
  const query = state.lawQuery.trim().toLocaleLowerCase("ja-JP");
  const filtered = state.laws.filter(law => {
    const text = [law.name, law.law_number, law.e_gov_id, law.category, law.uuid, law.type].join(" ").toLocaleLowerCase("ja-JP");
    return (!query || text.includes(query)) &&
      (!state.lawType || law.type === state.lawType) &&
      (!state.lawCategory || law.category === state.lawCategory);
  });
  const pageSize = 20;
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  state.lawPage = Math.min(Math.max(1, state.lawPage), pages);
  const start = (state.lawPage - 1) * pageSize;
  const pageItems = filtered.slice(start, start + pageSize);
  const types = [...new Set(state.laws.map(law => law.type).filter(Boolean))].sort((a, b) => a.localeCompare(b, "ja"));
  const select = '<select id="law-type-filter" class="control filter-select" aria-label="法令種別">' +
    '<option value="">すべての種別</option>' + types.map(type =>
      '<option value="' + esc(type) + '"' + (state.lawType === type ? " selected" : "") + '>' + esc(type) + '</option>'
    ).join("") + '</select>' +
    '<select id="law-category-filter" class="control filter-select" aria-label="法令カテゴリ">' +
    '<option value="">すべてのカテゴリ</option>' + state.lawCategories.map(item => item.category).filter(Boolean).map(category =>
      '<option value="' + esc(category) + '"' + (state.lawCategory === category ? " selected" : "") + '>' + esc(category) + '</option>'
    ).join("") + '</select>';
  const list = pageItems.length ? documentList(pageItems, "law") :
    emptyState("該当する法令がありません", "検索語や種別フィルターを変更してください。", "⌕");
  const count = '<p class="list-count"><strong>' + formatNumber(filtered.length) + '</strong> 件 / 全 ' +
    formatNumber(state.laws.length) + ' 件</p>';
  const markup = '<div class="view-stack"><div class="view-heading"><div class="view-heading-main">' +
    '<p class="eyebrow">ライブラリ / 法令</p><h2>法令一覧</h2><p>' +
    (IS_AUTHENTICATED ? "法令の本文・XML原文・改正履歴を確認できます。" :
      "法令本文まで閲覧できます。上部検索から条文全文も検索できます。") +
    '</p></div><span class="readonly-note">閲覧専用</span></div>' +
    '<div class="list-toolbar">' + count + '<div class="filter-controls">' +
    '<input id="law-filter" class="control filter-input" type="search" value="' + esc(state.lawQuery) +
    '" placeholder="法令名・番号・IDで絞り込む" aria-label="法令一覧を絞り込む">' + select +
    '</div></div>' + surface("検索結果", "該当する法令", list, null, null, filtered.length + " 件") +
    pagination(state.lawPage, pages, "law-page") + '</div>';
  setContent(markup);
}

async function loadLaws() {
  setPage("laws");
  loading("法令一覧を読み込んでいます");
  try {
    const result = await api(IS_AUTHENTICATED ? "laws" : "public/laws");
    state.laws = result.laws || [];
    renderLawList();
  } catch (error) {
    errorView(error);
  }
}

async function openLaw(uuid) {
  loading("法令の詳細を読み込んでいます");
  try {
    const data = await api((IS_AUTHENTICATED ? "laws/" : "public/laws/") + encodeURIComponent(uuid));
    state.lastDetail = {kind: "law", uuid, data};
    renderLaw(data);
  } catch (error) {
    errorView(error);
  }
}

function metadataItem(label, value, mono) {
  return '<div class="metadata-item"><span>' + esc(label) + '</span><strong' +
    (mono ? ' class="mono"' : '') + '>' + esc(value || "—") + '</strong></div>';
}

function infoSurface(title, rows) {
  return '<section class="surface"><div class="surface-header"><h3 class="surface-title">' + esc(title) +
    '</h3></div><div class="info-list">' + rows.map(row =>
      '<div class="info-row"><span>' + esc(row[0]) + '</span><strong' +
      (row[2] ? ' class="mono"' : '') + '>' + esc(row[1] || "—") + '</strong></div>'
    ).join("") + '</div></section>';
}

function detailHeader(kind, title, subtitle, actions) {
  return '<div class="detail-heading-block"><div class="detail-heading-main">' +
    '<p class="eyebrow">' + (kind === "law" ? "法令" : "ルール") + '</p><h2>' + esc(title) +
    '</h2><p>' + esc(subtitle || "") + '</p></div><div class="detail-actions">' + actions + '</div></div>';
}

function renderLaw(data) {
  const law = data.law || {};
  setPage("laws", "法令詳細");
  const back = '<button class="detail-back" type="button" data-action="back-to-laws">← 法令一覧に戻る</button>';
  const actions = IS_AUTHENTICATED ?
    '<button class="action-button" type="button" data-action="history" data-kind="law" data-uuid="' + esc(law.uuid) + '">改正履歴・差分</button>' : "";
  const header = detailHeader("law", law.name || "名称未設定",
    [law.type, law.law_number].filter(Boolean).join(" · "), actions);
  const metadata = '<div class="metadata-grid">' +
    metadataItem("法令番号", law.law_number) +
    metadataItem("e-Gov法令ID", law.e_gov_id, true) +
    metadataItem("種別", law.type) +
    metadataItem("カテゴリ", law.category) +
    (IS_AUTHENTICATED ? metadataItem("UUID", law.uuid, true) + metadataItem("バージョン", shortHash(law.version), true) : "") +
    '</div>';
  const aside = infoSurface("文書情報", [
    ["ファイル", IS_AUTHENTICATED ? law.path : "ログイン後に表示"],
    ["法令番号", law.law_number],
    ["e-Gov ID", law.e_gov_id],
    ["種別", law.type],
    ["カテゴリ", law.category]
  ]);
  let primary;
  if (IS_AUTHENTICATED) {
    const mode = state.lawDisplayMode || "markdown";
    const tabs = '<div class="reader-tabs" role="tablist" aria-label="法令本文の表示形式">' +
      '<button class="reader-tab ' + (mode === "markdown" ? "active" : "") + '" type="button" role="tab" aria-selected="' +
      (mode === "markdown" ? "true" : "false") + '" data-action="law-display-mode" data-mode="markdown">整形表示</button>' +
      '<button class="reader-tab ' + (mode === "xml" ? "active" : "") + '" type="button" role="tab" aria-selected="' +
      (mode === "xml" ? "true" : "false") + '" data-action="law-display-mode" data-mode="xml">XML原文</button></div>';
    let content;
    if (mode === "xml") {
      content = '<pre class="source-content">' + esc(data.xml || "XMLデータがありません。") + '</pre>';
    } else if (data.markdown) {
      content = '<article class="markdown-document">' + markdownToHtml(data.markdown, true) + '</article>';
    } else {
      content = '<div class="reader-error">' + esc(data.markdown_error || "Markdownに変換できませんでした。XML原文を確認してください。") + '</div>' +
        '<pre class="source-content">' + esc(data.xml || "XMLデータがありません。") + '</pre>';
    }
    primary = '<section class="source-panel law-reader"><div class="reader-panel-header">' +
      '<div><strong>法令本文</strong><span>XML構造をMarkdownとして表示</span></div>' + tabs +
      '</div>' + content + '</section>';
  } else {
    const body = data.markdown ?
      '<article class="markdown-document">' + markdownToHtml(data.markdown, true) + '</article>' :
      '<div class="reader-error">' + esc(data.markdown_error || "この法令の本文を整形できませんでした。") + '</div>';
    primary = '<section class="source-panel law-reader"><div class="reader-panel-header">' +
      '<div><strong>法令本文</strong><span>公開されている本文の整形表示</span></div>' +
      '<span class="readonly-note">ログイン不要</span></div>' + body + '</section>';
  }
  setContent('<div class="view-stack">' + back + header + metadata +
    '<div class="detail-layout"><div class="detail-primary">' + primary + '</div>' +
    '<aside class="detail-aside">' + aside + '</aside></div></div>');
}

async function loadRules() {
  setPage("rules");
  loading("ルール一覧を読み込んでいます");
  try {
    const result = await api("rules");
    state.rules = result.rules || [];
    renderRuleList();
  } catch (error) {
    errorView(error);
  }
}

function renderRuleList() {
  setPage("rules");
  const query = state.ruleQuery.trim().toLocaleLowerCase("ja-JP");
  const filtered = state.rules.filter(rule => {
    const text = [rule.name, rule.category, rule.uuid, rule.created].join(" ").toLocaleLowerCase("ja-JP");
    return !query || text.includes(query);
  });
  const pageSize = 20;
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  state.rulePage = Math.min(Math.max(1, state.rulePage), pages);
  const pageItems = filtered.slice((state.rulePage - 1) * pageSize, state.rulePage * pageSize);
  const list = pageItems.length ? documentList(pageItems, "rule") :
    emptyState("該当するルールがありません", "検索語を変更してください。", "⌕");
  const count = '<p class="list-count"><strong>' + formatNumber(filtered.length) + '</strong> 件 / 全 ' +
    formatNumber(state.rules.length) + ' 件</p>';
  setContent('<div class="view-stack"><div class="view-heading"><div class="view-heading-main">' +
    '<p class="eyebrow">ライブラリ / ルール</p><h2>ルール一覧</h2>' +
    '<p>LawGitに保存されたユーザー定義ルールを閲覧します。</p></div><span class="readonly-note">閲覧専用</span></div>' +
    '<div class="list-toolbar">' + count + '<div class="filter-controls">' +
    '<input id="rule-filter" class="control filter-input" type="search" value="' + esc(state.ruleQuery) +
    '" placeholder="名前・カテゴリ・UUIDで絞り込む" aria-label="ルール一覧を絞り込む"></div></div>' +
    surface("検索結果", "登録されたルール", list, null, null, filtered.length + " 件") +
    pagination(state.rulePage, pages, "rule-page") + '</div>');
}

async function openRule(uuid) {
  loading("ルールの詳細を読み込んでいます");
  try {
    const data = await api("rules/" + encodeURIComponent(uuid));
    state.lastDetail = {kind: "rule", uuid, data};
    renderRule(data);
  } catch (error) {
    errorView(error);
  }
}

function renderRule(rule) {
  setPage("rules", "ルール詳細");
  const back = '<button class="detail-back" type="button" data-action="back-to-rules">← ルール一覧に戻る</button>';
  const actions = '<button class="action-button" type="button" data-action="history" data-kind="rule" data-uuid="' +
    esc(rule.uuid) + '">変更履歴を見る</button>';
  const header = detailHeader("rule", rule.name || "名称未設定",
    [rule.category || "general", rule.created || "作成日不明"].join(" · "), actions);
  const metadata = '<div class="metadata-grid">' +
    metadataItem("UUID", rule.uuid, true) +
    metadataItem("カテゴリ", rule.category || "general") +
    metadataItem("作成日", rule.created) +
    metadataItem("バージョン", shortHash(rule.version), true) +
    '</div>';
  const body = '<section class="source-panel"><div class="source-panel-header"><strong>ルール本文</strong>' +
    '<span>Markdown / 読み取り専用</span></div><article class="markdown-document">' +
    markdownToHtml(rule.body || "本文はありません。") + '</article></section>';
  setContent('<div class="view-stack">' + back + header + metadata +
    '<div class="detail-layout"><div class="detail-primary">' + body + '</div>' +
    '<aside class="detail-aside">' + infoSurface("文書情報", [
      ["UUID", rule.uuid], ["カテゴリ", rule.category || "general"], ["作成日", rule.created],
      ["バージョン", shortHash(rule.version)]
    ]) + '</aside></div></div>');
}

function historySelect(label, id, selected, commits) {
  const options = commits.map(commit => {
    const hash = commit.hash || "";
    const subject = commit.subject || commit.message || "コミット";
    const date = dateText(commit.date || commit.author_date || commit.committer_date);
    return '<option value="' + esc(hash) + '"' + (hash === selected ? " selected" : "") + '>' +
      esc(shortHash(hash) + " · " + date + " · " + subject) + '</option>';
  }).join("");
  return '<label class="diff-select-label"><span>' + esc(label) + '</span><select class="control diff-select" id="' +
    esc(id) + '">' + options + '</select></label>';
}

function renderHistoryPage(kind, uuid, commits) {
  const detail = state.lastDetail?.data || {};
  const name = kind === "law" ? (detail.law?.name || "法令") : (detail.name || "ルール");
  setPage(kind === "law" ? "laws" : "rules", kind === "law" ? "改正履歴" : "変更履歴");

  const rows = commits.length ? '<div class="history-list">' + commits.map(commit => {
    const rev = commit.hash || "";
    return '<article class="history-item"><div class="history-rail"><span class="history-dot"></span></div>' +
      '<div class="history-main"><div class="history-title">' + esc(commit.subject || commit.message || "コミット") + '</div>' +
      '<div class="history-meta">' + esc([shortHash(rev), commit.author].filter(Boolean).join(" · ")) + '</div>' +
      '<button class="surface-link history-action" type="button" data-action="revision" data-kind="' + esc(kind) +
      '" data-uuid="' + esc(uuid) + '" data-rev="' + esc(rev) + '">この版を読む →</button></div>' +
      '<time class="history-time">' + esc(dateText(commit.date || commit.author_date || commit.committer_date)) +
      '</time></article>';
  }).join("") + '</div>' : emptyState("変更履歴はありません", "この文書に対応するGitコミットはありません。", "↗");

  let comparePanel = "";
  if (kind === "law") {
    if (commits.length >= 2) {
      if (!state.diffOlder || !commits.some(c => c.hash === state.diffOlder)) state.diffOlder = commits[1].hash;
      if (!state.diffNewer || !commits.some(c => c.hash === state.diffNewer)) state.diffNewer = commits[0].hash;
      comparePanel = '<section class="surface diff-picker"><div class="surface-header"><div class="surface-heading">' +
        '<h3 class="surface-title">2つの版を比較</h3><p class="surface-subtitle">古い版と新しい版を選択すると、整形済みMarkdownの差分を表示します。</p>' +
        '</div><span class="pill neutral">MARKDOWN DIFF</span></div><div class="diff-picker-body">' +
        historySelect("比較元（古い版）", "diff-older-select", state.diffOlder, commits) +
        historySelect("比較先（新しい版）", "diff-newer-select", state.diffNewer, commits) +
        '<button class="action-button primary" type="button" data-action="compare-revisions">差分を見る</button></div></section>';
    } else {
      comparePanel = '<section class="surface"><div class="surface-body">' +
        '<div class="note-box">差分を見るには、同じ法令の履歴が2件以上必要です。</div></div></section>';
    }
  }

  setContent('<div class="view-stack"><button class="detail-back" type="button" data-action="back-detail">← 文書詳細に戻る</button>' +
    '<div class="view-heading"><div class="view-heading-main"><p class="eyebrow">履歴 / ' + esc(kind === "law" ? "法令" : "ルール") +
    '</p><h2>' + esc(name) + (kind === "law" ? " の改正履歴" : " の変更履歴") +
    '</h2><p>' + (kind === "law" ? "改正前後の本文をMarkdownとして比較できます。" : "Gitで記録された変更を新しい順に表示しています。") +
    '</p></div><span class="readonly-note">' + formatNumber(commits.length) + ' 件</span></div>' +
    comparePanel + '<section class="surface"><div class="surface-header"><div class="surface-heading">' +
    '<h3 class="surface-title">コミット履歴</h3><p class="surface-subtitle">新しい順</p></div></div>' +
    '<div class="surface-body flush">' + rows + '</div></section></div>');
}

async function openHistory(kind, uuid) {
  state.historyKind = kind;
  state.historyUuid = uuid;
  loading("変更履歴を読み込んでいます");
  try {
    const data = await api(kind + "s/" + encodeURIComponent(uuid) + "/history?limit=100");
    state.historyCommits = data.commits || [];
    if (!state.diffOlder || !state.historyCommits.some(commit => commit.hash === state.diffOlder)) {
      state.diffOlder = state.historyCommits[1]?.hash || "";
    }
    if (!state.diffNewer || !state.historyCommits.some(commit => commit.hash === state.diffNewer)) {
      state.diffNewer = state.historyCommits[0]?.hash || "";
    }
    renderHistoryPage(kind, uuid, state.historyCommits);
  } catch (error) {
    errorView(error);
  }
}

async function openRevision(kind, uuid, revision) {
  loading("過去の版を読み込んでいます");
  try {
    const data = await api(kind + "s/" + encodeURIComponent(uuid) +
      "/revisions/" + encodeURIComponent(revision));
    state.currentRevision = revision;
    state.lastRevisionData = data;
    state.revisionDisplayMode = "markdown";
    renderRevision(kind, revision, data);
  } catch (error) {
    errorView(error);
  }
}

function renderRevision(kind, revision, data) {
  setPage(kind === "law" ? "laws" : "rules", "過去の版");
  const back = '<button class="detail-back" type="button" data-action="back-history">← 履歴に戻る</button>';
  if (kind === "law") {
    const mode = state.revisionDisplayMode || "markdown";
    const tabs = '<div class="reader-tabs" role="tablist" aria-label="過去版の表示形式">' +
      '<button class="reader-tab ' + (mode === "markdown" ? "active" : "") + '" type="button" role="tab" aria-selected="' +
      (mode === "markdown" ? "true" : "false") + '" data-action="revision-display-mode" data-mode="markdown">整形表示</button>' +
      '<button class="reader-tab ' + (mode === "xml" ? "active" : "") + '" type="button" role="tab" aria-selected="' +
      (mode === "xml" ? "true" : "false") + '" data-action="revision-display-mode" data-mode="xml">XML原文</button></div>';
    let content;
    if (mode === "xml") {
      content = '<pre class="source-content">' + esc(data.xml || "XMLデータがありません。") + '</pre>';
    } else if (data.markdown) {
      content = '<article class="markdown-document">' + markdownToHtml(data.markdown, true) + '</article>';
    } else {
      content = '<div class="reader-error">' + esc(data.markdown_error || "Markdownに変換できませんでした。") + '</div>' +
        '<pre class="source-content">' + esc(data.xml || "XMLデータがありません。") + '</pre>';
    }
    setContent('<div class="view-stack">' + back +
      '<div class="view-heading"><div class="view-heading-main"><p class="eyebrow">過去の版</p>' +
      '<h2>コミット ' + esc(shortHash(revision)) + '</h2><p>このコミット時点の法令本文です。</p></div>' +
      '<span class="readonly-note">読み取り専用</span></div>' +
      '<section class="source-panel law-reader"><div class="reader-panel-header"><div><strong>法令本文</strong>' +
      '<span>' + esc(revision) + '</span></div>' + tabs + '</div>' + content + '</section></div>');
    return;
  }

  const ruleBody = data.body || "";
  setContent('<div class="view-stack">' + back +
    '<div class="view-heading"><div class="view-heading-main"><p class="eyebrow">過去の版</p>' +
    '<h2>コミット ' + esc(shortHash(revision)) + '</h2><p>このコミット時点のルール本文です。</p></div>' +
    '<span class="readonly-note">読み取り専用</span></div>' +
    '<section class="source-panel"><div class="source-panel-header"><strong>ルール本文</strong>' +
    '<span class="mono">' + esc(revision) + '</span></div>' +
    '<article class="markdown-document">' + markdownToHtml(ruleBody) + '</article></section></div>');
}

async function compareRevisions() {
  const older = state.diffOlder;
  const newer = state.diffNewer;
  if (!older || !newer || older === newer) {
    setContent('<div class="view-stack">' + statusRow("比較できません", "異なる2つの版を選択してください。", "warning") +
      '<button class="detail-back" type="button" data-action="back-history">← 履歴に戻る</button></div>');
    return;
  }
  loading("Markdownの差分を計算しています");
  try {
    const result = await api("laws/" + encodeURIComponent(state.historyUuid) +
      "/diff?older=" + encodeURIComponent(older) + "&newer=" + encodeURIComponent(newer));
    setPage("laws", "改正差分");
    const diffMarkup = diffToHtml(result.diff || "");
    setContent('<div class="view-stack"><button class="detail-back" type="button" data-action="back-history">← 改正履歴に戻る</button>' +
      '<div class="view-heading"><div class="view-heading-main"><p class="eyebrow">法令 / 改正差分</p>' +
      '<h2>変更箇所</h2><p>XMLをMarkdownに変換した後の行単位差分です。構造や本文の変更を確認できます。</p></div>' +
      '<span class="readonly-note">整形済みMarkdown</span></div>' +
      '<div class="diff-summary"><div><strong class="diff-add-count">+' + formatNumber(result.additions) + '</strong><span>追加</span></div>' +
      '<div><strong class="diff-remove-count">−' + formatNumber(result.deletions) + '</strong><span>削除</span></div>' +
      '<div class="diff-version"><span>比較元</span><strong class="mono">' + esc(shortHash(older)) + '</strong></div>' +
      '<div class="diff-version"><span>比較先</span><strong class="mono">' + esc(shortHash(newer)) + '</strong></div></div>' +
      '<section class="surface"><div class="surface-header"><div class="surface-heading">' +
      '<h3 class="surface-title">Markdown差分</h3><p class="surface-subtitle">緑：追加 / 赤：削除 / 灰：文脈</p></div></div>' +
      '<div class="diff-container">' + diffMarkup + '</div></section></div>');
  } catch (error) {
    errorView(error);
  }
}

function renderRecentSearches() {
  if (!state.recentSearches.length) {
    return '<section class="recent-searches"><div class="recent-search-heading">検索例</div>' +
      '<div class="search-chips"><button type="button" data-action="search-term" data-query="民法">民法</button>' +
      '<button type="button" data-action="search-term" data-query="個人情報">個人情報</button>' +
      '<button type="button" data-action="search-term" data-query="道路交通">道路交通</button></div></section>';
  }
  return '<section class="recent-searches"><div class="recent-search-heading">このセッションの最近の検索</div>' +
    '<div class="search-chips">' + state.recentSearches.map(query =>
      '<button type="button" data-action="search-term" data-query="' + esc(query) + '">' + esc(query) + '</button>'
    ).join("") + '</div></section>';
}

async function search(query) {
  const value = String(query || "").trim();
  if (!value) return;
  state.lastSearch = value;
  state.recentSearches = [value, ...state.recentSearches.filter(item => item !== value)].slice(0, 6);
  setPage("search");
  loading("法令を検索しています");
  try {
    const endpoint = IS_AUTHENTICATED ? "search" : "public/search";
    const data = await api(endpoint + "?q=" + encodeURIComponent(value) + "&limit=" + (IS_AUTHENTICATED ? "100" : "50"));
    const hits = (data.hits || []).filter(hit => IS_AUTHENTICATED || hit.kind === "law");
    const rows = hits.length ? '<div class="search-results">' + hits.map(hit => {
      return '<article class="search-hit"><span class="document-symbol" aria-hidden="true">§</span>' +
        '<div class="document-main"><button class="document-name table-link" type="button" data-action="law" data-uuid="' +
        esc(hit.uuid) + '">' + esc(hit.name || "名称未設定") + '</button>' +
        '<div class="document-meta"><span class="pill neutral">法令</span>' +
        (hit.matched ? '<span>一致箇所: ' + esc(hit.matched) + '</span>' : '') + '</div>' +
        '<p class="search-snippet">' + esc(hit.snippet || "") + '</p></div>' +
        '<button class="search-open" type="button" data-action="law" data-uuid="' + esc(hit.uuid) + '">本文を読む →</button></article>';
    }).join("") + '</div>' : emptyState("一致する法令はありません", "別の語句や法令番号で検索してください。", "⌕");
    const truncation = data.truncated ?
      '<p class="search-truncated">結果が多いため一部を表示しています。検索語を追加して絞り込んでください。</p>' : "";
    const guestHint = !IS_AUTHENTICATED ?
      '<p class="search-scope-note">検索対象：公開法令の本文・法令名・法令番号。ユーザー定義ルールは検索対象に含みません。</p>' : "";
    setContent('<div class="view-stack"><div class="view-heading"><div class="view-heading-main">' +
      '<p class="eyebrow">全文検索</p><h2>「' + esc(value) + '」の検索結果</h2>' +
      '<p>法令本文から一致箇所を探しています。</p></div>' +
      '<span class="readonly-note">' + formatNumber(hits.length) + ' 件</span></div>' +
      renderRecentSearches() + guestHint + truncation + rows + '</div>');
  } catch (error) {
    errorView(error);
  }
}

async function loadActivity() {
  setPage("activity");
  loading("変更履歴を読み込んでいます");
  try {
    const [log, changes] = await Promise.all([
      api("git/log?limit=50"), api("changes?limit=50")
    ]);
    const commits = log.commits || [];
    const head = changes.head || "—";
    const changesCount = (changes.commits || []).length;
    const summary = '<div class="metadata-grid">' +
      metadataItem("HEAD", shortHash(head), true) +
      metadataItem("差分コミット", formatNumber(changesCount)) +
      '</div>';
    setContent('<div class="view-stack"><div class="view-heading"><div class="view-heading-main">' +
      '<p class="eyebrow">リポジトリ</p><h2>変更履歴</h2>' +
      '<p>LawGitのGitコミットを新しい順に確認できます。</p></div>' +
      '<span class="readonly-note">' + formatNumber(commits.length) + ' 件を表示</span></div>' +
      '<div class="content-grid"><section class="surface"><div class="surface-header">' +
      '<div class="surface-heading"><h3 class="surface-title">コミット</h3><p class="surface-subtitle">直近の記録</p></div></div>' +
      '<div class="surface-body flush">' + historyRows(commits, 50) + '</div></section>' +
      '<div class="view-stack">' + surface("変更追従情報", "APIから取得した差分情報", summary, null, null, "LawGit") +
      statusRow("読み取り専用", "この画面からコミットや文書の変更はできません。") + '</div></div></div>');
  } catch (error) {
    errorView(error);
  }
}

async function loadIntegrity() {
  setPage("integrity");
  loading("リポジトリを検証しています");
  try {
    const [validation, gitStatus, log] = await Promise.all([
      api("validate"), api("git/status"), api("git/log?limit=8")
    ]);
    const valid = !!validation.valid;
    const metrics = '<div class="metrics-grid">' +
      metric("検証結果", valid ? "正常" : "確認が必要", "LawGit validate") +
      metric("法令", validation.laws, "検証対象") +
      metric("ルール", validation.rules, "検証対象") +
      metric("最近のコミット", (log.commits || []).length, "直近8件を取得") + '</div>';
    const validationJson = '<pre class="source-content">' + esc(JSON.stringify(validation, null, 2)) + '</pre>';
    const statusJson = '<pre class="source-content">' + esc(JSON.stringify(gitStatus, null, 2)) + '</pre>';
    setContent('<div class="view-stack"><div class="view-heading"><div class="view-heading-main">' +
      '<p class="eyebrow">リポジトリ</p><h2>検証・Git</h2>' +
      '<p>法令・ルールの検証結果とGitワークツリーの状態を確認できます。</p></div>' +
      '<span class="pill ' + (valid ? "success" : "warning") + '">' + (valid ? "検証正常" : "要確認") + '</span></div>' +
      metrics + '<div class="content-grid">' +
      surface("検証結果", "LawGit validate", validationJson) +
      surface("Gitワークツリー", "git status", statusJson) + '</div></div>');
  } catch (error) {
    errorView(error);
  }
}

async function navigate(view) {
  if (view === "overview") return loadOverview();
  if (view === "laws") return loadLaws();
  if (view === "rules" && IS_AUTHENTICATED) return loadRules();
  if (view === "activity" && IS_AUTHENTICATED) return loadActivity();
  if (view === "integrity" && IS_AUTHENTICATED) return loadIntegrity();
  return loadOverview();
}

document.addEventListener("click", async event => {
  const nav = event.target.closest("[data-view]");
  if (nav) {
    event.preventDefault();
    await navigate(nav.dataset.view);
    return;
  }

  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;

  if (action === "go-laws") return navigate("laws");
  if (action === "focus-search") {
    const field = document.getElementById("global-search");
    if (field) field.focus();
    return;
  }
  if (action === "search-term") return search(button.dataset.query || "");
  if (action === "go-rules") return navigate("rules");
  if (action === "go-activity") return navigate("activity");
  if (action === "refresh") {
    if (state.view === "search") return search(state.lastSearch);
    if (state.view === "laws") return loadLaws();
    if (state.view === "rules") return loadRules();
    if (state.view === "activity") return loadActivity();
    if (state.view === "integrity") return loadIntegrity();
    return loadOverview();
  }
  if (action === "law") return openLaw(button.dataset.uuid);
  if (action === "rule") return openRule(button.dataset.uuid);
  if (action === "history") return openHistory(button.dataset.kind, button.dataset.uuid);
  if (action === "revision") return openRevision(button.dataset.kind, button.dataset.uuid, button.dataset.rev);
  if (action === "compare-revisions") return compareRevisions();
  if (action === "law-display-mode") {
    state.lawDisplayMode = button.dataset.mode === "xml" ? "xml" : "markdown";
    if (state.lastDetail?.kind === "law") return renderLaw(state.lastDetail.data);
  }
  if (action === "revision-display-mode") {
    state.revisionDisplayMode = button.dataset.mode === "xml" ? "xml" : "markdown";
    if (state.lastRevisionData) return renderRevision("law", state.currentRevision, state.lastRevisionData);
  }
  if (action === "back-to-laws") return loadLaws();
  if (action === "back-to-rules") return loadRules();
  if (action === "back-detail") {
    if (state.historyKind === "law") return openLaw(state.historyUuid);
    return openRule(state.historyUuid);
  }
  if (action === "back-history") return openHistory(state.historyKind, state.historyUuid);
  if (action === "law-page") {
    state.lawPage = Number(button.dataset.page) || 1;
    return renderLawList();
  }
  if (action === "rule-page") {
    state.rulePage = Number(button.dataset.page) || 1;
    return renderRuleList();
  }
});

const composingFilters = new Set();

function updateFilter(id, value, position) {
  if (id === "law-filter") {
    state.lawQuery = value;
    state.lawPage = 1;
    renderLawList();
  } else if (id === "rule-filter") {
    state.ruleQuery = value;
    state.rulePage = 1;
    renderRuleList();
  } else {
    return;
  }

  const field = document.getElementById(id);
  if (field) {
    field.focus();
    if (position !== null && position !== undefined) field.setSelectionRange(position, position);
  }
}

document.addEventListener("compositionstart", event => {
  if (event.target && (event.target.id === "law-filter" || event.target.id === "rule-filter")) {
    composingFilters.add(event.target.id);
  }
});

document.addEventListener("compositionend", event => {
  const field = event.target;
  if (!field || !composingFilters.has(field.id)) return;
  composingFilters.delete(field.id);
  updateFilter(field.id, field.value, field.selectionStart);
});

document.addEventListener("input", event => {
  const field = event.target;
  if (!field || (field.id !== "law-filter" && field.id !== "rule-filter")) return;
  if (event.isComposing || composingFilters.has(field.id)) return;
  updateFilter(field.id, field.value, field.selectionStart);
});

document.addEventListener("change", event => {
  if (event.target.id === "law-type-filter") {
    state.lawType = event.target.value;
    state.lawPage = 1;
    renderLawList();
  } else if (event.target.id === "diff-older-select") {
    state.diffOlder = event.target.value;
  } else if (event.target.id === "diff-newer-select") {
    state.diffNewer = event.target.value;
  } else if (event.target.id === "law-category-filter") {
    state.lawCategory = event.target.value;
    state.lawPage = 1;
    renderLawList();
  }
});

const searchForm = document.getElementById("search-form");
if (searchForm) {
  searchForm.addEventListener("submit", async event => {
    event.preventDefault();
    const field = document.getElementById("global-search");
    await search(field.value);
    field.blur();
  });
}

const refreshButton = document.getElementById("refresh-button");
if (refreshButton) {
  refreshButton.addEventListener("click", async () => {
    const refreshAction = root.querySelector('[data-action="refresh"]');
    if (refreshAction) refreshAction.click();
    else if (state.view === "laws") await loadLaws();
    else if (state.view === "rules") await loadRules();
    else if (state.view === "activity") await loadActivity();
    else if (state.view === "integrity") await loadIntegrity();
    else if (state.view === "search") await search(state.lastSearch);
    else await loadOverview();
  });
}

loadOverview();
