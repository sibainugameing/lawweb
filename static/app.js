const state = {
  view: "overview",
  overview: null,
  laws: [],
  rules: [],
  selected: null,
  query: "",
  historyKind: "",
  historyUuid: ""
};

const root = document.getElementById("view-root");
const modalRoot = document.getElementById("modal-root");
const toastRoot = document.getElementById("toast-root");
const titles = {
  overview: ["概要", "OVERVIEW"],
  laws: ["法令", "LEGAL DOCUMENTS"],
  rules: ["ルール", "USER RULES"],
  activity: ["変更履歴", "ACTIVITY"],
  integrity: ["検証・Git", "INTEGRITY & GIT"],
  search: ["検索結果", "SEARCH"]
};

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, function (ch) {
    return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch];
  });
}
function formatDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return new Intl.DateTimeFormat("ja-JP", {year:"numeric",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}).format(d);
}
function shortHash(value) { return String(value || "").slice(0, 8) || "—"; }
function showToast(message, error) {
  const el = document.createElement("div");
  el.className = "toast" + (error ? " error" : "");
  el.textContent = message;
  toastRoot.appendChild(el);
  window.setTimeout(function () { el.remove(); }, 4200);
}
async function api(path, options) {
  const opt = options || {};
  const method = (opt.method || "GET").toUpperCase();
  const headers = {"Accept":"application/json"};
  if (opt.body !== undefined) headers["Content-Type"] = "application/json";
  if (["POST","PUT","PATCH","DELETE"].includes(method)) headers["X-Lawweb-Request"] = "1";
  const response = await fetch("/api/" + path.replace(/^\/+/, ""), {
    method: method,
    headers: headers,
    body: opt.body === undefined ? undefined : JSON.stringify(opt.body),
    cache: "no-store"
  });
  let payload;
  try { payload = await response.json(); }
  catch (_) { throw new Error("サーバーからJSON以外の応答を受信しました。"); }
  if (!response.ok || payload.ok === false) {
    const error = new Error(payload.error || "リクエストに失敗しました。");
    error.status = response.status;
    throw error;
  }
  return payload.result;
}
function setHeader(view) {
  const item = titles[view] || titles.overview;
  document.getElementById("page-title").textContent = item[0];
  document.getElementById("breadcrumb-current").textContent = item[1];
  document.querySelectorAll(".nav-item").forEach(function (el) {
    el.classList.toggle("active", el.dataset.view === view);
  });
}
function emptyState(title, detail, symbol) {
  return '<div class="empty-state"><div class="empty-symbol">' + (symbol || "—") + '</div><strong>' +
    escapeHtml(title) + '</strong><p>' + escapeHtml(detail || "") + '</p></div>';
}
function panelHead(title, subtitle, action, actionText) {
  return '<div class="panel-head"><div><h3 class="panel-title">' + title + '</h3>' +
    (subtitle ? '<p class="panel-subtitle">' + subtitle + '</p>' : '') +
    '</div>' + (action ? '<button class="text-link" data-action="' + action + '">' + actionText + ' →</button>' : '') + '</div>';
}
function lawRows(laws, limit) {
  const list = (laws || []).slice(0, limit || 20);
  if (!list.length) return emptyState("法令がありません", "法令データが登録されると、ここに表示されます。", "▤");
  return list.map(function (law) {
    return '<div class="data-row"><div class="doc-icon">法</div><div class="row-main">' +
      '<button class="table-link row-title" data-action="open-law" data-uuid="' + escapeHtml(law.uuid) + '">' + escapeHtml(law.name || "名称未設定") + '</button>' +
      '<div class="row-meta"><span>' + escapeHtml(law.type || "種別未設定") + '</span><span>·</span><span>' + escapeHtml(law.law_number || law.e_gov_id || "番号なし") + '</span></div>' +
      '</div><span class="row-end">' + escapeHtml(law.category || "未分類") + '</span></div>';
  }).join("");
}
function ruleRows(rules, limit) {
  const list = (rules || []).slice(0, limit || 20);
  if (!list.length) return emptyState("ルールがありません", "ユーザー定義ルールを作成すると、ここで管理できます。", "≡");
  return list.map(function (rule) {
    return '<div class="data-row"><div class="doc-icon">R</div><div class="row-main">' +
      '<button class="table-link row-title" data-action="open-rule" data-uuid="' + escapeHtml(rule.uuid) + '">' + escapeHtml(rule.name || "名称未設定") + '</button>' +
      '<div class="row-meta"><span>' + escapeHtml(rule.category || "general") + '</span><span>·</span><span class="mono">' + escapeHtml(shortHash(rule.uuid)) + '</span></div>' +
      '</div><span class="row-end">' + escapeHtml(rule.created || "—") + '</span></div>';
  }).join("");
}
function activityRows(commits, limit) {
  const list = (commits || []).slice(0, limit || 8);
  if (!list.length) return emptyState("変更履歴はありません", "Gitにコミットが追加されると、ここに表示されます。", "↗");
  return list.map(function (commit) {
    const files = (commit.files || []).map(function (file) { return file.path; }).filter(Boolean).slice(0, 3).join("、");
    const meta = [shortHash(commit.hash), files].filter(Boolean).join(" · ");
    return '<div class="activity-item"><div class="activity-rail"><i></i></div><div class="activity-main"><strong>' +
      escapeHtml(commit.subject || commit.message || "コミット") + '</strong><p>' + escapeHtml(meta) + '</p></div><span class="activity-time">' +
      escapeHtml(formatDate(commit.date || commit.author_date || commit.committer_date)) + '</span></div>';
  }).join("");
}
function updateConnection(connected, label, repo) {
  const dot = document.getElementById("sidebar-status-dot");
  dot.classList.toggle("online", !!connected);
  dot.classList.toggle("offline", !connected);
  document.getElementById("sidebar-status").textContent = label;
  document.getElementById("sidebar-repo").textContent = repo || "LawGit API";
}
function renderOverview(data) {
  state.overview = data;
  const laws = (data.laws && data.laws.laws) || [];
  const rules = (data.rules && data.rules.rules) || [];
  const lawCategories = (data.categories && data.categories.laws) || [];
  const ruleCategories = (data.categories && data.categories.rules) || [];
  const logs = (data.log && data.log.commits) || [];
  const ping = data.ping || {};
  updateConnection(true, "LawGitに接続済み", ping.root || "ローカルAPI");
  const errors = Object.keys(data.errors || {});
  const categories = lawCategories.slice(0, 7).map(function (c) {
    return '<span class="category-chip">' + escapeHtml(c.category || "未分類") + '<b>' + escapeHtml(c.count) + '</b></span>';
  }).join("");
  root.innerHTML =
    '<div class="page-intro"><div><p class="eyebrow">LEGAL DOCUMENT WORKSPACE</p><p>法令とユーザー定義ルールを、LawGitのデータ・履歴・Gitと一体で管理します。</p></div>' +
    '<div class="tag ' + (ping.auto_commit ? "" : "neutral") + '">自動コミット ' + (ping.auto_commit ? "ON" : "OFF") + '</div></div>' +
    '<div class="status-banner"><span class="status-dot online"></span><div><strong>LawGit API 接続中</strong><p>' +
    escapeHtml(ping.name || "LawGit") + ' · v' + escapeHtml(ping.version || "—") + ' · ' + escapeHtml(ping.root || "リポジトリ情報なし") +
    '</p></div><span class="row-end" style="margin-left:auto">LOCAL</span></div>' +
    (errors.length ? '<div class="notice" style="margin-bottom:16px">一部の情報を取得できませんでした: ' + escapeHtml(errors.join("、")) + '</div>' : '') +
    '<div class="stats-grid">' +
      '<div class="stat-card"><div class="stat-top"><span>登録法令</span><span class="stat-mark">§</span></div><div class="stat-value">' + laws.length.toLocaleString("ja-JP") + '</div><div class="stat-note">LawGit / laws</div></div>' +
      '<div class="stat-card"><div class="stat-top"><span>ユーザールール</span><span class="stat-mark">≡</span></div><div class="stat-value">' + rules.length.toLocaleString("ja-JP") + '</div><div class="stat-note">LawGit / rules</div></div>' +
      '<div class="stat-card"><div class="stat-top"><span>法令カテゴリ</span><span class="stat-mark">▦</span></div><div class="stat-value">' + lawCategories.length.toLocaleString("ja-JP") + '</div><div class="stat-note">分類済み・未分類を含む</div></div>' +
      '<div class="stat-card"><div class="stat-top"><span>ルールカテゴリ</span><span class="stat-mark">◇</span></div><div class="stat-value">' + ruleCategories.length.toLocaleString("ja-JP") + '</div><div class="stat-note">ユーザー定義分類</div></div>' +
    '</div>' +
    '<div class="dashboard-grid">' +
      '<section class="panel">' + panelHead("法令ライブラリ", "登録されている法令の一覧", "go-laws", "すべて表示") + '<div class="panel-body">' + lawRows(laws, 5) + '</div></section>' +
      '<section class="panel">' + panelHead("最近のGit履歴", "最新のコミット", "go-activity", "履歴を見る") + '<div class="activity-list">' + activityRows(logs, 5) + '</div></section>' +
      '<section class="panel">' + panelHead("ユーザールール", "自分で作成・管理するルール", "go-rules", "すべて表示") + '<div class="panel-body">' + ruleRows(rules, 4) + '</div></section>' +
      '<section class="panel"><div class="panel-head"><div><h3 class="panel-title">法令カテゴリ</h3><p class="panel-subtitle">カテゴリ別の登録件数</p></div></div><div class="panel-body">' +
        (categories ? '<div class="category-list">' + categories + '</div>' : emptyState("カテゴリがありません", "分類情報はまだありません。", "◇")) +
      '</div></section>' +
    '</div>';
}
function renderOffline(message, details) {
  updateConnection(false, "LawGit未接続", "接続待ち");
  root.innerHTML = '<div class="status-banner offline"><span class="status-dot offline"></span><div><strong>LawGit APIに接続できません</strong><p>' +
    escapeHtml(message || "LawGit serveの起動状態を確認してください。") + '</p></div></div>' +
    '<section class="panel"><div class="panel-head"><div><h3 class="panel-title">接続設定を確認</h3><p class="panel-subtitle">LawWebはLawGit APIをバックエンドとして利用します。</p></div></div><div class="panel-body" style="padding-top:16px">' +
    '<ol class="setup-list"><li>LawGit CLIがPATHから実行できることを確認します。</li><li>プロジェクト直下の .env にLAWGIT_REPOのパスを設定します。</li><li>sh run.sh を再実行します。</li></ol>' +
    (details ? '<pre class="json-preview">' + escapeHtml(details) + '</pre>' : '') +
    '<button class="button button-secondary" style="margin-top:15px" data-action="refresh">再接続</button></div></section>';
}
async function loadOverview() {
  root.innerHTML = '<div class="loading-state"><span class="loader"></span><p>LawGitの状態を読み込んでいます…</p></div>';
  try {
    const data = await api("overview");
    if (!data.connected) { renderOffline(data.error, data.details); return; }
    renderOverview(data);
  } catch (error) {
    renderOffline(error.message);
  }
}
async function loadLaws() {
  root.innerHTML = '<div class="loading-state"><span class="loader"></span><p>法令を読み込んでいます…</p></div>';
  try {
    const data = await api("laws");
    state.laws = data.laws || [];
    renderLaws();
  } catch (error) { renderError(error); }
}
function renderLaws() {
  setHeader("laws");
  const laws = state.laws || [];
  root.innerHTML =
    '<div class="page-intro"><div><p class="eyebrow">LEGAL DOCUMENTS</p><p>LawGitに登録された法令を検索・参照します。法令のXML原文、識別子、変更履歴を確認できます。</p></div><button class="button button-primary" data-action="create-law">＋ 法令を登録</button></div>' +
    '<div class="section-toolbar"><h2>法令一覧 <span class="muted small">(' + laws.length + ')</span></h2><div class="filter-bar"><input class="field" id="law-local-filter" placeholder="法令名・番号で絞り込み…" aria-label="法令一覧を絞り込み"><select class="select-field" id="law-type-filter"><option value="">すべての種別</option><option>法律</option><option>政令</option><option>省令</option><option>規則</option><option>条例</option></select></div></div>' +
    '<section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>法令名</th><th>種別</th><th>法令番号</th><th>カテゴリ</th><th>UUID</th></tr></thead><tbody id="law-table-body">' +
    laws.map(function (law) {
      return '<tr data-search="' + escapeHtml([law.name,law.law_number,law.e_gov_id,law.uuid,law.category].join(" ").toLowerCase()) + '" data-type="' + escapeHtml(law.type || "") + '">' +
      '<td><button class="table-link table-primary" data-action="open-law" data-uuid="' + escapeHtml(law.uuid) + '">' + escapeHtml(law.name || "名称未設定") + '</button></td>' +
      '<td><span class="tag neutral">' + escapeHtml(law.type || "—") + '</span></td><td>' + escapeHtml(law.law_number || "—") + '</td><td>' + escapeHtml(law.category || "未分類") + '</td><td class="mono small">' + escapeHtml(shortHash(law.uuid)) + '</td></tr>';
    }).join("") + '</tbody></table>' +
    (laws.length ? '' : emptyState("法令がありません", "右上の「法令を登録」から新しい法令を作成できます。", "§")) +
    '</div></section>';
  const filter = document.getElementById("law-local-filter");
  const type = document.getElementById("law-type-filter");
  function apply() {
    const q = filter.value.trim().toLowerCase();
    const t = type.value;
    document.querySelectorAll("#law-table-body tr").forEach(function (row) {
      row.hidden = !(row.dataset.search.includes(q) && (!t || row.dataset.type === t));
    });
  }
  filter.addEventListener("input", apply);
  type.addEventListener("change", apply);
}
async function openLaw(uuid) {
  root.innerHTML = '<div class="loading-state"><span class="loader"></span><p>法令の詳細を読み込んでいます…</p></div>';
  try {
    const data = await api("laws/" + encodeURIComponent(uuid));
    state.selected = {kind:"law", uuid:uuid, data:data};
    renderLawDetail(data);
  } catch (error) { renderError(error); }
}
function renderLawDetail(data) {
  const law = data.law || {};
  setHeader("laws");
  root.innerHTML =
    '<button class="back-link" data-action="go-laws">← 法令一覧に戻る</button>' +
    '<div class="detail-top"><div class="detail-icon">§</div><div class="detail-heading"><p class="eyebrow">LEGAL DOCUMENT</p><h2>' + escapeHtml(law.name || "名称未設定") + '</h2><p>' + escapeHtml(law.type || "種別未設定") + ' · ' + escapeHtml(law.law_number || "法令番号なし") + '</p></div>' +
    '<div class="detail-actions"><button class="button button-secondary" data-action="history" data-kind="law" data-uuid="' + escapeHtml(law.uuid) + '">変更履歴</button></div></div>' +
    '<div class="meta-grid">' +
      '<div class="meta-cell"><span>UUID</span><strong class="mono">' + escapeHtml(law.uuid) + '</strong></div>' +
      '<div class="meta-cell"><span>e-Gov法令ID</span><strong>' + escapeHtml(law.e_gov_id || "—") + '</strong></div>' +
      '<div class="meta-cell"><span>カテゴリ</span><strong>' + escapeHtml(law.category || "未分類") + '</strong></div>' +
      '<div class="meta-cell"><span>バージョン</span><strong class="mono">' + escapeHtml(shortHash(law.version)) + '</strong></div>' +
    '</div><div class="detail-grid"><section class="panel"><div class="code-label"><span>XML SOURCE</span><span>READ ONLY</span></div><pre class="code-block">' + escapeHtml(data.xml || "XMLデータがありません。") + '</pre></section>' +
    '<aside class="panel side-info"><h3>文書情報</h3><div class="info-line"><span>ファイルパス</span><strong class="mono">' + escapeHtml(law.path || "—") + '</strong></div><div class="info-line"><span>種別</span><strong>' + escapeHtml(law.type || "—") + '</strong></div><div class="info-line"><span>法令番号</span><strong>' + escapeHtml(law.law_number || "—") + '</strong></div><div class="info-line"><span>e-Gov ID</span><strong>' + escapeHtml(law.e_gov_id || "—") + '</strong></div><p class="notice" style="margin-top:15px">このAPIでは法令の新規登録と参照、履歴・過去版の取得に対応しています。法令本文の更新UIは未実装です。</p></aside></div>';
}
async function loadRules() {
  root.innerHTML = '<div class="loading-state"><span class="loader"></span><p>ルールを読み込んでいます…</p></div>';
  try {
    const data = await api("rules");
    state.rules = data.rules || [];
    renderRules();
  } catch (error) { renderError(error); }
}
function renderRules() {
  setHeader("rules");
  const rules = state.rules || [];
  root.innerHTML =
    '<div class="page-intro"><div><p class="eyebrow">USER-DEFINED RULES</p><p>独自の規則・運用ルールをMarkdownで作成し、バージョン管理します。更新時は取得済みバージョンを送信して競合を検出します。</p></div><button class="button button-primary" data-action="create-rule">＋ ルールを作成</button></div>' +
    '<div class="section-toolbar"><h2>ルール一覧 <span class="muted small">(' + rules.length + ')</span></h2><div class="filter-bar"><input class="field" id="rule-local-filter" placeholder="名前・カテゴリで絞り込み…" aria-label="ルール一覧を絞り込み"></div></div>' +
    '<section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>ルール名</th><th>カテゴリ</th><th>作成日</th><th>UUID</th><th>バージョン</th></tr></thead><tbody id="rule-table-body">' +
    rules.map(function (rule) {
      return '<tr data-search="' + escapeHtml([rule.name,rule.category,rule.uuid].join(" ").toLowerCase()) + '">' +
      '<td><button class="table-link table-primary" data-action="open-rule" data-uuid="' + escapeHtml(rule.uuid) + '">' + escapeHtml(rule.name || "名称未設定") + '</button></td>' +
      '<td><span class="tag">' + escapeHtml(rule.category || "general") + '</span></td><td>' + escapeHtml(rule.created || "—") + '</td><td class="mono small">' + escapeHtml(shortHash(rule.uuid)) + '</td><td class="mono small">' + escapeHtml(shortHash(rule.version)) + '</td></tr>';
    }).join("") + '</tbody></table>' +
    (rules.length ? '' : emptyState("ルールがありません", "「ルールを作成」から独自のルールを登録してください。", "≡")) +
    '</div></section>';
  const filter = document.getElementById("rule-local-filter");
  filter.addEventListener("input", function () {
    const q = filter.value.trim().toLowerCase();
    document.querySelectorAll("#rule-table-body tr").forEach(function (row) { row.hidden = !row.dataset.search.includes(q); });
  });
}
async function openRule(uuid) {
  root.innerHTML = '<div class="loading-state"><span class="loader"></span><p>ルールを読み込んでいます…</p></div>';
  try {
    const data = await api("rules/" + encodeURIComponent(uuid));
    state.selected = {kind:"rule", uuid:uuid, data:data};
    renderRuleEditor(data);
  } catch (error) { renderError(error); }
}
function renderRuleEditor(rule) {
  setHeader("rules");
  root.innerHTML =
    '<button class="back-link" data-action="go-rules">← ルール一覧に戻る</button>' +
    '<div class="page-intro"><div><p class="eyebrow">RULE EDITOR</p><p>変更内容はLawGit APIへ送信されます。取得時のversionを使い、別のアプリで先に変更されていた場合は409競合として保存を止めます。</p></div>' +
    '<div class="detail-actions"><button class="button button-secondary" data-action="history" data-kind="rule" data-uuid="' + escapeHtml(rule.uuid) + '">履歴</button><button class="button button-danger" data-action="delete-rule" data-uuid="' + escapeHtml(rule.uuid) + '">削除</button></div></div>' +
    '<div class="editor-layout"><section class="panel form-panel"><form id="edit-rule-form" data-uuid="' + escapeHtml(rule.uuid) + '" data-version="' + escapeHtml(rule.version || "") + '">' +
      '<div class="form-grid"><div class="form-field"><label for="edit-rule-name">ルール名</label><input class="form-control" id="edit-rule-name" name="name" required value="' + escapeHtml(rule.name || "") + '"></div>' +
      '<div class="form-field"><label for="edit-rule-category">カテゴリ</label><input class="form-control" id="edit-rule-category" name="category" value="' + escapeHtml(rule.category || "general") + '"></div></div>' +
      '<div class="form-field"><label for="edit-rule-body">本文（Markdown）</label><textarea class="form-control" id="edit-rule-body" name="body" spellcheck="false">' + escapeHtml(rule.body || "") + '</textarea><small>Markdown形式の本文をそのまま保存します。</small></div>' +
      '<div class="form-field"><label for="edit-rule-commit">コミットメッセージ（任意）</label><input class="form-control" id="edit-rule-commit" name="commit_message" placeholder="空欄ならLawGitの既定メッセージを使用"></div>' +
      '<div class="form-actions"><button type="button" class="button button-secondary" data-action="go-rules">キャンセル</button><button type="submit" class="button button-primary">変更を保存</button></div>' +
    '</form></section><aside class="panel side-info"><h3>ルール情報</h3><div class="info-line"><span>UUID</span><strong class="mono">' + escapeHtml(rule.uuid) + '</strong></div><div class="info-line"><span>作成日</span><strong>' + escapeHtml(rule.created || "—") + '</strong></div><div class="info-line"><span>ファイル</span><strong class="mono">' + escapeHtml(rule.path || "—") + '</strong></div><div class="info-line"><span>Version</span><strong class="mono">' + escapeHtml(shortHash(rule.version)) + '</strong></div><p class="notice" style="margin-top:15px">競合が発生した場合は、最新の内容を再取得して差分を確認してから編集してください。</p></aside></div>';
}
async function openHistory(kind, uuid) {
  root.innerHTML = '<div class="loading-state"><span class="loader"></span><p>変更履歴を取得しています…</p></div>';
  try {
    const data = await api(kind + "s/" + encodeURIComponent(uuid) + "/history?limit=100");
    state.historyKind = kind;
    state.historyUuid = uuid;
    setHeader(kind === "law" ? "laws" : "rules");
    const commits = data.commits || [];
    root.innerHTML =
      '<button class="back-link" data-action="back-detail">← 詳細に戻る</button>' +
      '<div class="page-intro"><div><p class="eyebrow">VERSION HISTORY</p><p>Gitで記録された変更履歴です。コミット時点の内容を参照できます。</p></div><span class="tag neutral">' + commits.length + ' COMMITS</span></div>' +
      '<section class="panel">' + panelHead("変更履歴", "対象UUID: " + escapeHtml(uuid)) + '<div class="activity-list">' +
      (commits.length ? commits.map(function (commit) {
        return '<div class="activity-item"><div class="activity-rail"><i></i></div><div class="activity-main"><strong>' + escapeHtml(commit.subject || "コミット") + '</strong><p class="mono">' + escapeHtml(commit.hash || "") + ' · ' + escapeHtml(commit.author || "") + '</p><button class="text-link" style="margin-top:6px" data-action="revision" data-kind="' + kind + '" data-uuid="' + escapeHtml(uuid) + '" data-rev="' + escapeHtml(commit.hash) + '">この時点の内容を見る →</button></div><span class="activity-time">' + escapeHtml(formatDate(commit.date)) + '</span></div>';
      }).join("") : emptyState("履歴がありません", "対象ファイルにGitコミットがまだありません。", "↗")) +
      '</div></section>';
  } catch (error) { renderError(error); }
}
async function openRevision(kind, uuid, rev) {
  root.innerHTML = '<div class="loading-state"><span class="loader"></span><p>過去の内容を取得しています…</p></div>';
  try {
    const data = await api(kind + "s/" + encodeURIComponent(uuid) + "/revisions/" + encodeURIComponent(rev));
    const content = data.xml || data.body || JSON.stringify(data, null, 2);
    setHeader(kind === "law" ? "laws" : "rules");
    root.innerHTML = '<button class="back-link" data-action="back-history">← 変更履歴に戻る</button>' +
      '<div class="page-intro"><div><p class="eyebrow">HISTORICAL REVISION</p><p>コミット ' + escapeHtml(rev) + ' 時点の読み取り専用データです。</p></div><span class="tag gold">READ ONLY</span></div>' +
      '<section class="panel"><div class="code-label"><span>' + (kind === "law" ? "XML REVISION" : "RULE REVISION") + '</span><span class="mono">' + escapeHtml(shortHash(rev)) + '</span></div><pre class="code-block">' + escapeHtml(content) + '</pre></section>';
  } catch (error) { renderError(error); }
}
async function showSearch(query) {
  state.view = "search";
  state.query = query;
  setHeader("search");
  root.innerHTML = '<div class="loading-state"><span class="loader"></span><p>検索しています…</p></div>';
  try {
    const data = await api("search?q=" + encodeURIComponent(query) + "&limit=100");
    const hits = data.hits || [];
    root.innerHTML = '<div class="page-intro"><div><p class="eyebrow">FULL-TEXT SEARCH</p><p>「' + escapeHtml(query) + '」の検索結果</p></div><span class="tag neutral">' + hits.length + ' HITS</span></div>' +
      '<div class="search-results">' + (hits.length ? hits.map(function (hit) {
        const action = hit.kind === "law" ? "open-law" : "open-rule";
        return '<article class="search-hit"><div class="doc-icon">' + (hit.kind === "law" ? "法" : "R") + '</div><div class="row-main"><button class="table-link row-title" data-action="' + action + '" data-uuid="' + escapeHtml(hit.uuid) + '">' + escapeHtml(hit.name || "名称未設定") + '</button><div class="row-meta"><span class="tag neutral">' + escapeHtml(hit.kind || "—") + '</span><span>一致箇所: ' + escapeHtml(hit.matched || "—") + '</span></div><p>' + escapeHtml(hit.snippet || "") + '</p></div></article>';
      }).join("") : emptyState("一致する文書はありません", "別のキーワードで検索してください。", "⌕")) + '</div>';
  } catch (error) { renderError(error); }
}
async function loadActivity() {
  root.innerHTML = '<div class="loading-state"><span class="loader"></span><p>Git履歴を読み込んでいます…</p></div>';
  try {
    const [log, changeData] = await Promise.all([api("git/log?limit=50"), api("changes?limit=50")]);
    setHeader("activity");
    const commits = log.commits || [];
    root.innerHTML = '<div class="page-intro"><div><p class="eyebrow">GIT ACTIVITY</p><p>LawGitリポジトリに記録されたコミットと変更ファイルを確認します。</p></div><span class="tag neutral">' + commits.length + ' RECENT COMMITS</span></div>' +
      '<div class="split-grid"><section class="panel">' + panelHead("コミット履歴", "新しい順") + '<div class="activity-list">' + activityRows(commits, 50) + '</div></section>' +
      '<section class="panel"><div class="panel-head"><div><h3 class="panel-title">変更追従情報</h3><p class="panel-subtitle">同期用のheadと直近の変更</p></div></div><div class="side-info"><div class="info-line"><span>HEAD</span><strong class="mono">' + escapeHtml(shortHash(changeData.head)) + '</strong></div><div class="info-line"><span>変更コミット数</span><strong>' + escapeHtml((changeData.commits || []).length) + '</strong></div><div class="info-line"><span>ポーリングAPI</span><strong>/api/v1/changes</strong></div><p class="notice" style="margin-top:14px">外部アプリはheadを保存し、次回からsinceパラメータを指定して差分を取得できます。</p></div></section></div>';
  } catch (error) { renderError(error); }
}
async function loadIntegrity() {
  root.innerHTML = '<div class="loading-state"><span class="loader"></span><p>リポジトリを検証しています…</p></div>';
  try {
    const [validation, status, log] = await Promise.all([api("validate"), api("git/status"), api("git/log?limit=8")]);
    setHeader("integrity");
    root.innerHTML = '<div class="page-intro"><div><p class="eyebrow">REPOSITORY INTEGRITY</p><p>法令・ルールの検証結果とGitワークツリーの状態を確認します。</p></div><button class="button button-secondary" data-action="refresh-integrity">再検証</button></div>' +
      '<div class="stats-grid"><div class="stat-card"><div class="stat-top"><span>検証結果</span><span class="stat-mark">◇</span></div><div class="stat-value" style="font-size:21px">' + (validation.valid ? "VALID" : "CHECK") + '</div><div class="stat-note">LawGit validate</div></div>' +
      '<div class="stat-card"><div class="stat-top"><span>法令数</span><span class="stat-mark">§</span></div><div class="stat-value">' + escapeHtml(validation.laws ?? "—") + '</div><div class="stat-note">Validation result</div></div>' +
      '<div class="stat-card"><div class="stat-top"><span>ルール数</span><span class="stat-mark">≡</span></div><div class="stat-value">' + escapeHtml(validation.rules ?? "—") + '</div><div class="stat-note">Validation result</div></div>' +
      '<div class="stat-card"><div class="stat-top"><span>コミット</span><span class="stat-mark">↗</span></div><div class="stat-value">' + ((log.commits || []).length) + '</div><div class="stat-note">直近の履歴取得件数</div></div></div>' +
      '<div class="split-grid"><section class="panel"><div class="panel-head"><div><h3 class="panel-title">検証レポート</h3><p class="panel-subtitle">LawGit API / validate</p></div><span class="tag ' + (validation.valid ? "" : "gold") + '">' + (validation.valid ? "VALID" : "REVIEW") + '</span></div><pre class="json-preview">' + escapeHtml(JSON.stringify(validation, null, 2)) + '</pre></section>' +
      '<section class="panel"><div class="panel-head"><div><h3 class="panel-title">Gitワークツリー</h3><p class="panel-subtitle">git status --porcelain</p></div></div><pre class="json-preview">' + escapeHtml(JSON.stringify(status, null, 2)) + '</pre></section></div>' +
      '<section class="panel" style="margin-top:15px"><div class="panel-head"><div><h3 class="panel-title">一括コミット</h3><p class="panel-subtitle">laws/ と rules/ を検証してからコミットします。</p></div></div><div class="panel-body" style="padding-top:15px"><form id="commit-form"><div class="form-field"><label for="commit-message">コミットメッセージ</label><input class="form-control" id="commit-message" name="message" required placeholder="例: 法令データを更新"></div><div class="form-actions"><button class="button button-primary" type="submit">検証してコミット</button></div></form></div></section>';
  } catch (error) { renderError(error); }
}
function renderError(error) {
  root.innerHTML = '<div class="status-banner offline"><span class="status-dot offline"></span><div><strong>データを取得できませんでした</strong><p>' + escapeHtml(error.message || String(error)) + '</p></div></div><button class="button button-secondary" data-action="refresh">再試行</button>';
}
function openModal(title, body) {
  modalRoot.innerHTML = '<div class="modal-backdrop" data-action="close-modal"><section class="modal-card" role="dialog" aria-modal="true" aria-label="' + escapeHtml(title) + '">' +
    '<div class="modal-head"><h2>' + escapeHtml(title) + '</h2><button class="modal-close" data-action="close-modal" aria-label="閉じる">×</button></div><div class="modal-body">' + body + '</div></section></div>';
}
function closeModal() { modalRoot.innerHTML = ""; }
function lawModal() {
  openModal("法令を登録", '<form id="create-law-form"><div class="form-field"><label for="law-name">法令名 *</label><input class="form-control" id="law-name" name="name" required placeholder="例: 民法"></div>' +
    '<div class="form-grid"><div class="form-field"><label for="law-type">種別 *</label><select class="form-control" id="law-type" name="type" required><option value="法律">法律</option><option value="政令">政令</option><option value="省令">省令</option><option value="規則">規則</option><option value="条例">条例</option><option value="その他">その他</option></select></div>' +
    '<div class="form-field"><label for="law-category">カテゴリ</label><input class="form-control" id="law-category" name="category" placeholder="例: 民事"></div></div>' +
    '<div class="form-field"><label for="law-number">法令番号</label><input class="form-control" id="law-number" name="law_number" placeholder="例: 明治二十九年法律第八十九号"></div>' +
    '<div class="form-field"><label for="law-egov">e-Gov法令ID</label><input class="form-control" id="law-egov" name="e_gov_id" placeholder="例: 129AC0000000089"></div>' +
    '<div class="form-field"><label for="law-commit">コミットメッセージ（任意）</label><input class="form-control" id="law-commit" name="commit_message" placeholder="LawGitの既定メッセージを使う場合は空欄"></div>' +
    '<p class="notice">このフォームはLawGitの law.create に対応します。法令本文XMLの取り込み・編集は別途Importerなどで行ってください。</p><div class="modal-footer"><button type="button" class="button button-secondary" data-action="close-modal">キャンセル</button><button type="submit" class="button button-primary">登録する</button></div></form>');
}
function ruleModal() {
  openModal("ルールを作成", '<form id="create-rule-form"><div class="form-field"><label for="new-rule-name">ルール名 *</label><input class="form-control" id="new-rule-name" name="name" required placeholder="例: 文書命名規則"></div>' +
    '<div class="form-field"><label for="new-rule-category">カテゴリ</label><input class="form-control" id="new-rule-category" name="category" value="general"></div>' +
    '<div class="form-field"><label for="new-rule-body">本文（Markdown）</label><textarea class="form-control" id="new-rule-body" name="body" placeholder="# ルール&#10;- ..."></textarea></div>' +
    '<div class="form-field"><label for="new-rule-commit">コミットメッセージ（任意）</label><input class="form-control" id="new-rule-commit" name="commit_message" placeholder="LawGitの既定メッセージを使う場合は空欄"></div>' +
    '<div class="modal-footer"><button type="button" class="button button-secondary" data-action="close-modal">キャンセル</button><button type="submit" class="button button-primary">作成する</button></div></form>');
}
function formObject(form) {
  const data = {};
  new FormData(form).forEach(function (value, key) {
    if (String(value).trim() !== "") data[key] = String(value);
  });
  return data;
}
async function handleSubmit(event) {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  if (!["create-law-form","create-rule-form","edit-rule-form","commit-form"].includes(form.id)) return;
  event.preventDefault();
  const submit = form.querySelector('button[type="submit"]');
  if (submit) { submit.disabled = true; submit.textContent = "処理中…"; }
  try {
    if (form.id === "create-law-form") {
      await api("laws", {method:"POST", body:formObject(form)});
      closeModal(); showToast("法令を登録しました。"); await loadLaws();
    } else if (form.id === "create-rule-form") {
      await api("rules", {method:"POST", body:formObject(form)});
      closeModal(); showToast("ルールを作成しました。"); await loadRules();
    } else if (form.id === "edit-rule-form") {
      const payload = formObject(form);
      payload.category = document.getElementById("edit-rule-category").value;
      payload.body = document.getElementById("edit-rule-body").value;
      payload.if_version = form.dataset.version;
      await api("rules/" + encodeURIComponent(form.dataset.uuid), {method:"PUT", body:payload});
      showToast("ルールを更新しました。"); await openRule(form.dataset.uuid);
    } else if (form.id === "commit-form") {
      const payload = formObject(form);
      await api("git/commit", {method:"POST", body:payload});
      showToast("コミットを実行しました。"); await loadIntegrity();
    }
  } catch (error) {
    if (error.status === 409) showToast("競合を検出しました。最新データを再取得し、変更内容を確認してください。", true);
    else showToast(error.message || "処理に失敗しました。", true);
    if (submit) { submit.disabled = false; submit.textContent = form.id === "edit-rule-form" ? "変更を保存" : "再試行"; }
  }
}
async function deleteRule(uuid) {
  if (!state.selected || state.selected.kind !== "rule") return;
  if (!window.confirm("このルールを削除します。Git履歴はLawGitに残ります。続けますか？")) return;
  try {
    const version = state.selected.data.version || "";
    await api("rules/" + encodeURIComponent(uuid) + "?if_version=" + encodeURIComponent(version), {method:"DELETE"});
    showToast("ルールを削除しました。");
    await loadRules();
  } catch (error) {
    if (error.status === 409) showToast("他のアプリで更新されています。再取得してから削除してください。", true);
    else showToast(error.message, true);
  }
}
async function navigate(view) {
  state.view = view;
  setHeader(view);
  if (view === "overview") await loadOverview();
  else if (view === "laws") await loadLaws();
  else if (view === "rules") await loadRules();
  else if (view === "activity") await loadActivity();
  else if (view === "integrity") await loadIntegrity();
}
document.addEventListener("click", async function (event) {
  const button = event.target.closest("[data-action]");
  if (!button) return;
  const action = button.dataset.action;
  if (action === "close-modal") { if (event.target === button || button.classList.contains("modal-close")) closeModal(); return; }
  if (action === "go-laws") { await navigate("laws"); return; }
  if (action === "go-rules") { await navigate("rules"); return; }
  if (action === "go-activity") { await navigate("activity"); return; }
  if (action === "refresh") { await navigate(state.view === "search" ? "overview" : state.view); return; }
  if (action === "refresh-integrity") { await loadIntegrity(); return; }
  if (action === "create-law") { lawModal(); return; }
  if (action === "create-rule") { ruleModal(); return; }
  if (action === "open-law") { await openLaw(button.dataset.uuid); return; }
  if (action === "open-rule") { await openRule(button.dataset.uuid); return; }
  if (action === "history") { await openHistory(button.dataset.kind, button.dataset.uuid); return; }
  if (action === "revision") { await openRevision(button.dataset.kind, button.dataset.uuid, button.dataset.rev); return; }
  if (action === "delete-rule") { await deleteRule(button.dataset.uuid); return; }
  if (action === "back-detail") {
    if (state.historyKind === "law") await openLaw(state.historyUuid); else await openRule(state.historyUuid);
    return;
  }
  if (action === "back-history") { await openHistory(state.historyKind, state.historyUuid); return; }
  if (action === "go-laws-from-search") { await navigate("laws"); return; }
  if (action === "go-rules-from-search") { await navigate("rules"); return; }
});
document.addEventListener("submit", handleSubmit);
document.querySelectorAll(".nav-item").forEach(function (button) {
  button.addEventListener("click", function () { navigate(button.dataset.view); });
});
document.getElementById("create-button").addEventListener("click", function () {
  openModal("新規作成", '<div class="panel" style="box-shadow:none"><button class="nav-item" style="width:100%;color:#314454" data-action="create-law"><span class="nav-icon">§</span><span>法令を登録</span></button><button class="nav-item" style="width:100%;color:#314454" data-action="create-rule"><span class="nav-icon">≡</span><span>ルールを作成</span></button></div>');
});
document.getElementById("refresh-button").addEventListener("click", function () { navigate(state.view); });
document.getElementById("search-form").addEventListener("submit", function (event) {
  event.preventDefault();
  const query = document.getElementById("global-search").value.trim();
  if (query) showSearch(query);
});
loadOverview();
