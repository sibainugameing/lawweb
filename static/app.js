const state = {view:"overview",laws:[],rules:[],historyKind:"",historyUuid:""};
const root = document.getElementById("view-root");
const titles = {
  overview:["概要","OVERVIEW"], laws:["法令","LEGAL DOCUMENTS"],
  rules:["ルール","USER RULES"], activity:["変更履歴","ACTIVITY"],
  integrity:["検証・Git","INTEGRITY & GIT"], search:["検索結果","SEARCH"]
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function dateText(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) :
    new Intl.DateTimeFormat("ja-JP",{year:"numeric",month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}).format(date);
}
function hash(value) { return String(value || "").slice(0,8) || "—"; }
async function api(path) {
  const response = await fetch("/api/" + path.replace(/^\/+/,""), {
    method:"GET",headers:{"Accept":"application/json"},credentials:"same-origin",cache:"no-store"
  });
  if (response.status === 401) {
    location.assign("/login");
    throw new Error("ログインセッションが終了しました。");
  }
  let data;
  try { data = await response.json(); } catch (_) { throw new Error("サーバー応答を解析できません。"); }
  if (!response.ok || data.ok === false) throw new Error(data.error || "リクエストに失敗しました。");
  return data.result;
}
function header(view) {
  const value = titles[view] || titles.overview;
  document.getElementById("page-title").textContent = value[0];
  document.getElementById("breadcrumb-current").textContent = value[1];
  document.querySelectorAll(".nav-item").forEach(el => el.classList.toggle("active",el.dataset.view === view));
}
function empty(title, detail, symbol) {
  return '<div class="empty-state"><div class="empty-symbol">'+(symbol || "—")+'</div><strong>'+esc(title)+'</strong><p>'+esc(detail || "")+'</p></div>';
}
function panelTitle(title, subtitle, action, text) {
  return '<div class="panel-head"><div><h3 class="panel-title">'+esc(title)+'</h3>'+
    (subtitle?'<p class="panel-subtitle">'+esc(subtitle)+'</p>':"")+
    '</div>'+(action?'<button class="text-link" data-action="'+action+'">'+esc(text)+' →</button>':"")+'</div>';
}
function lawRows(items, limit) {
  const laws=(items || []).slice(0,limit || 20);
  if (!laws.length) return empty("法令がありません","法令データが登録されると、ここに表示されます。","▤");
  return laws.map(law =>
    '<div class="data-row"><div class="doc-icon">法</div><div class="row-main">'+
    '<button class="table-link row-title" data-action="open-law" data-uuid="'+esc(law.uuid)+'">'+esc(law.name || "名称未設定")+'</button>'+
    '<div class="row-meta"><span>'+esc(law.type || "種別未設定")+'</span><span>·</span><span>'+esc(law.law_number || law.e_gov_id || "番号なし")+'</span></div>'+
    '</div><span class="row-end">'+esc(law.category || "未分類")+'</span></div>'
  ).join("");
}
function ruleRows(items, limit) {
  const rules=(items || []).slice(0,limit || 20);
  if (!rules.length) return empty("ルールがありません","LawGitに登録されたルールがここに表示されます。","≡");
  return rules.map(rule =>
    '<div class="data-row"><div class="doc-icon">R</div><div class="row-main">'+
    '<button class="table-link row-title" data-action="open-rule" data-uuid="'+esc(rule.uuid)+'">'+esc(rule.name || "名称未設定")+'</button>'+
    '<div class="row-meta"><span>'+esc(rule.category || "general")+'</span><span>·</span><span class="mono">'+esc(hash(rule.uuid))+'</span></div>'+
    '</div><span class="row-end">'+esc(rule.created || "—")+'</span></div>'
  ).join("");
}
function activityRows(items, limit) {
  const commits=(items || []).slice(0,limit || 8);
  if (!commits.length) return empty("変更履歴はありません","Gitのコミットが追加されると、ここに表示されます。","↗");
  return commits.map(commit => {
    const files=(commit.files || []).map(f=>f.path).filter(Boolean).slice(0,3).join("、");
    return '<div class="activity-item"><div class="activity-rail"><i></i></div><div class="activity-main"><strong>'+
      esc(commit.subject || commit.message || "コミット")+'</strong><p>'+esc([hash(commit.hash),files].filter(Boolean).join(" · "))+
      '</p></div><span class="activity-time">'+esc(dateText(commit.date || commit.author_date || commit.committer_date))+'</span></div>';
  }).join("");
}
function connection(online,label,repo) {
  const dot=document.getElementById("sidebar-status-dot");
  dot.classList.toggle("online",!!online);
  dot.classList.toggle("offline",!online);
  document.getElementById("sidebar-status").textContent=label;
  document.getElementById("sidebar-repo").textContent=repo || "LawGit API";
}
function renderError(error) {
  root.innerHTML='<div class="status-banner offline"><span class="status-dot offline"></span><div><strong>データを取得できませんでした</strong><p>'+
    esc(error.message || String(error))+'</p></div></div><button class="button button-secondary" data-action="refresh">再試行</button>';
}
function offline(message) {
  connection(false,"LawGit未接続","接続待ち");
  root.innerHTML='<div class="status-banner offline"><span class="status-dot offline"></span><div><strong>LawGit APIに接続できません</strong><p>'+
    esc(message || "LawGit serveの起動状態を確認してください。")+'</p></div></div><section class="panel">'+
    panelTitle("接続設定","LawWebはLawGit APIを読み取り専用で利用します.")+
    '<div class="panel-body"><p class="notice">ターミナルでLawGit CLIとLAWGIT_REPOの設定を確認し、sh run.shを再実行してください。</p></div></section>';
}
async function loadOverview() {
  root.innerHTML='<div class="loading-state"><span class="loader"></span><p>LawGitの状態を読み込んでいます…</p></div>';
  try {
    const data=await api("overview");
    if (!data.connected) { offline(data.error); return; }
    const laws=data.laws?.laws || [], rules=data.rules?.rules || [];
    const lawCats=data.categories?.laws || [], ruleCats=data.categories?.rules || [];
    const commits=data.log?.commits || [], ping=data.ping || {};
    connection(true,"LawGitに接続済み",ping.root || "ローカルAPI");
    const cats=lawCats.slice(0,7).map(c=>'<span class="category-chip">'+esc(c.category || "未分類")+'<b>'+esc(c.count)+'</b></span>').join("");
    const errors=Object.keys(data.errors || {});
    root.innerHTML=
      '<div class="page-intro"><div><p class="eyebrow">LEGAL DOCUMENT WORKSPACE</p><p>法令とユーザー定義ルールを、LawGitのデータ・履歴とともに閲覧します。</p></div><span class="tag neutral">READ ONLY</span></div>'+
      '<div class="status-banner"><span class="status-dot online"></span><div><strong>LawGit API 接続中</strong><p>'+esc(ping.name || "LawGit")+' · v'+esc(ping.version || "—")+' · '+esc(ping.root || "リポジトリ情報なし")+'</p></div><span class="row-end connection-local">LOCAL</span></div>'+
      (errors.length?'<div class="notice notice-spaced">一部の情報を取得できませんでした: '+esc(errors.join("、"))+'</div>':"")+
      '<div class="stats-grid">'+
      '<div class="stat-card"><div class="stat-top"><span>登録法令</span><span class="stat-mark">§</span></div><div class="stat-value">'+laws.length.toLocaleString("ja-JP")+'</div><div class="stat-note">LawGit / laws</div></div>'+
      '<div class="stat-card"><div class="stat-top"><span>登録ルール</span><span class="stat-mark">≡</span></div><div class="stat-value">'+rules.length.toLocaleString("ja-JP")+'</div><div class="stat-note">LawGit / rules</div></div>'+
      '<div class="stat-card"><div class="stat-top"><span>法令カテゴリ</span><span class="stat-mark">▦</span></div><div class="stat-value">'+lawCats.length.toLocaleString("ja-JP")+'</div><div class="stat-note">分類済み・未分類を含む</div></div>'+
      '<div class="stat-card"><div class="stat-top"><span>ルールカテゴリ</span><span class="stat-mark">◇</span></div><div class="stat-value">'+ruleCats.length.toLocaleString("ja-JP")+'</div><div class="stat-note">ユーザー定義分類</div></div></div>'+
      '<div class="dashboard-grid"><section class="panel">'+panelTitle("法令ライブラリ","登録されている法令","go-laws","すべて表示")+'<div class="panel-body">'+lawRows(laws,5)+'</div></section>'+
      '<section class="panel">'+panelTitle("最近のGit履歴","最新のコミット","go-activity","履歴を見る")+'<div class="activity-list">'+activityRows(commits,5)+'</div></section>'+
      '<section class="panel">'+panelTitle("ユーザールール","登録されたルール（閲覧専用）","go-rules","すべて表示")+'<div class="panel-body">'+ruleRows(rules,4)+'</div></section>'+
      '<section class="panel"><div class="panel-head"><div><h3 class="panel-title">法令カテゴリ</h3><p class="panel-subtitle">カテゴリ別の登録件数</p></div></div><div class="panel-body">'+
      (cats?'<div class="category-list">'+cats+'</div>':empty("カテゴリがありません","分類情報はまだありません。","◇"))+'</div></section></div>';
  } catch(e) { offline(e.message); }
}
async function loadLaws() {
  root.innerHTML='<div class="loading-state"><span class="loader"></span><p>法令を読み込んでいます…</p></div>';
  try { const result=await api("laws"); state.laws=result.laws || []; renderLaws(); }
  catch(e) { renderError(e); }
}
function renderLaws() {
  header("laws");
  const laws=state.laws;
  root.innerHTML=
    '<div class="page-intro"><div><p class="eyebrow">LEGAL DOCUMENTS</p><p>LawGitに登録された法令を検索・参照します。XML原文、識別子、改正履歴を閲覧できます。</p></div><span class="tag neutral">READ ONLY</span></div>'+
    '<div class="section-toolbar"><h2>法令一覧 <span class="muted small">('+laws.length+')</span></h2><div class="filter-bar"><input class="field" id="law-filter" placeholder="法令名・番号で絞り込み…" aria-label="法令一覧を絞り込み"><select class="select-field" id="law-type"><option value="">すべての種別</option><option>法律</option><option>政令</option><option>省令</option><option>規則</option><option>条例</option></select></div></div>'+
    '<section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>法令名</th><th>種別</th><th>法令番号</th><th>カテゴリ</th><th>UUID</th></tr></thead><tbody id="law-table">'+
    laws.map(law=>'<tr data-q="'+esc([law.name,law.law_number,law.e_gov_id,law.uuid,law.category].join(" ").toLowerCase())+'" data-type="'+esc(law.type || "")+'"><td><button class="table-link table-primary" data-action="law" data-uuid="'+esc(law.uuid)+'">'+esc(law.name || "名称未設定")+'</button></td><td><span class="tag neutral">'+esc(law.type || "—")+'</span></td><td>'+esc(law.law_number || "—")+'</td><td>'+esc(law.category || "未分類")+'</td><td class="mono small">'+esc(hash(law.uuid))+'</td></tr>').join("")+
    '</tbody></table>'+(laws.length?"":empty("法令がありません","LawGitに法令データを登録してください。","§"))+'</div></section>';
  const q=document.getElementById("law-filter"), t=document.getElementById("law-type");
  function apply(){const query=q.value.trim().toLowerCase();document.querySelectorAll("#law-table tr").forEach(row=>row.hidden=!(row.dataset.q.includes(query)&&(!t.value||row.dataset.type===t.value)));}
  q.addEventListener("input",apply); t.addEventListener("change",apply);
}
async function openLaw(uuid) {
  state.view="laws";
  root.innerHTML='<div class="loading-state"><span class="loader"></span><p>法令の詳細を読み込んでいます…</p></div>';
  try {const data=await api("laws/"+encodeURIComponent(uuid));state.selected={kind:"law",uuid,data};renderLaw(data);}
  catch(e){renderError(e);}
}
function renderLaw(data) {
  const law=data.law || {}; header("laws");
  root.innerHTML=
    '<button class="back-link" data-action="go-laws">← 法令一覧に戻る</button>'+
    '<div class="detail-top"><div class="detail-icon">§</div><div class="detail-heading"><p class="eyebrow">LEGAL DOCUMENT</p><h2>'+esc(law.name || "名称未設定")+'</h2><p>'+esc(law.type || "種別未設定")+' · '+esc(law.law_number || "法令番号なし")+'</p></div><div class="detail-actions"><button class="button button-secondary" data-action="history" data-kind="law" data-uuid="'+esc(law.uuid)+'">変更履歴</button></div></div>'+
    '<div class="meta-grid"><div class="meta-cell"><span>UUID</span><strong class="mono">'+esc(law.uuid)+'</strong></div><div class="meta-cell"><span>e-Gov法令ID</span><strong>'+esc(law.e_gov_id || "—")+'</strong></div><div class="meta-cell"><span>カテゴリ</span><strong>'+esc(law.category || "未分類")+'</strong></div><div class="meta-cell"><span>バージョン</span><strong class="mono">'+esc(hash(law.version))+'</strong></div></div>'+
    '<div class="detail-grid"><section class="panel"><div class="code-label"><span>XML SOURCE</span><span>READ ONLY</span></div><pre class="code-block">'+esc(data.xml || "XMLデータがありません。")+'</pre></section>'+
    '<aside class="panel side-info"><h3>文書情報</h3><div class="info-line"><span>ファイルパス</span><strong class="mono">'+esc(law.path || "—")+'</strong></div><div class="info-line"><span>種別</span><strong>'+esc(law.type || "—")+'</strong></div><div class="info-line"><span>法令番号</span><strong>'+esc(law.law_number || "—")+'</strong></div><div class="info-line"><span>e-Gov ID</span><strong>'+esc(law.e_gov_id || "—")+'</strong></div><p class="notice notice-spaced">法令XMLの閲覧専用です。</p></aside></div>';
}
async function loadRules() {
  root.innerHTML='<div class="loading-state"><span class="loader"></span><p>ルールを読み込んでいます…</p></div>';
  try {const result=await api("rules");state.rules=result.rules || [];renderRules();}
  catch(e){renderError(e);}
}
function renderRules() {
  header("rules"); const rules=state.rules;
  root.innerHTML=
    '<div class="page-intro"><div><p class="eyebrow">USER-DEFINED RULES</p><p>LawGitに保存されたユーザー定義ルールを閲覧します。作成・編集・削除はこのWeb UIから行えません。</p></div><span class="tag neutral">READ ONLY</span></div>'+
    '<div class="section-toolbar"><h2>ルール一覧 <span class="muted small">('+rules.length+')</span></h2><div class="filter-bar"><input class="field" id="rule-filter" placeholder="名前・カテゴリで絞り込み…" aria-label="ルール一覧を絞り込み"></div></div>'+
    '<section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>ルール名</th><th>カテゴリ</th><th>作成日</th><th>UUID</th><th>バージョン</th></tr></thead><tbody id="rule-table">'+
    rules.map(rule=>'<tr data-q="'+esc([rule.name,rule.category,rule.uuid].join(" ").toLowerCase())+'"><td><button class="table-link table-primary" data-action="rule" data-uuid="'+esc(rule.uuid)+'">'+esc(rule.name || "名称未設定")+'</button></td><td><span class="tag">'+esc(rule.category || "general")+'</span></td><td>'+esc(rule.created || "—")+'</td><td class="mono small">'+esc(hash(rule.uuid))+'</td><td class="mono small">'+esc(hash(rule.version))+'</td></tr>').join("")+
    '</tbody></table>'+(rules.length?"":empty("ルールがありません","LawGitにルールデータが登録されると、ここに表示されます。","≡"))+'</div></section>';
  const filter=document.getElementById("rule-filter");
  filter.addEventListener("input",()=>{const q=filter.value.trim().toLowerCase();document.querySelectorAll("#rule-table tr").forEach(row=>row.hidden=!row.dataset.q.includes(q));});
}
async function openRule(uuid) {
  state.view="rules";
  root.innerHTML='<div class="loading-state"><span class="loader"></span><p>ルールを読み込んでいます…</p></div>';
  try {const data=await api("rules/"+encodeURIComponent(uuid));state.selected={kind:"rule",uuid,data};renderRule(data);}
  catch(e){renderError(e);}
}
function renderRule(rule) {
  header("rules");
  root.innerHTML=
    '<button class="back-link" data-action="go-rules">← ルール一覧に戻る</button>'+
    '<div class="detail-top"><div class="detail-icon">R</div><div class="detail-heading"><p class="eyebrow">USER-DEFINED RULE</p><h2>'+esc(rule.name || "名称未設定")+'</h2><p>'+esc(rule.category || "general")+' · '+esc(rule.created || "作成日不明")+'</p></div><div class="detail-actions"><button class="button button-secondary" data-action="history" data-kind="rule" data-uuid="'+esc(rule.uuid)+'">変更履歴</button></div></div>'+
    '<div class="meta-grid"><div class="meta-cell"><span>UUID</span><strong class="mono">'+esc(rule.uuid)+'</strong></div><div class="meta-cell"><span>カテゴリ</span><strong>'+esc(rule.category || "general")+'</strong></div><div class="meta-cell"><span>作成日</span><strong>'+esc(rule.created || "—")+'</strong></div><div class="meta-cell"><span>バージョン</span><strong class="mono">'+esc(hash(rule.version))+'</strong></div></div>'+
    '<section class="panel rule-body-panel"><div class="code-label"><span>MARKDOWN BODY</span><span>READ ONLY</span></div><pre class="code-block">'+esc(rule.body || "本文はありません。")+'</pre></section>';
}
async function openHistory(kind,uuid) {
  state.view=kind==="law"?"laws":"rules";
  root.innerHTML='<div class="loading-state"><span class="loader"></span><p>変更履歴を取得しています…</p></div>';
  try {
    const data=await api(kind+"s/"+encodeURIComponent(uuid)+"/history?limit=100");
    state.historyKind=kind; state.historyUuid=uuid; header(kind==="law"?"laws":"rules");
    const commits=data.commits || [];
    root.innerHTML=
      '<button class="back-link" data-action="back-detail">← 詳細に戻る</button>'+
      '<div class="page-intro"><div><p class="eyebrow">VERSION HISTORY</p><p>Gitで記録された変更履歴です。コミット時点の内容を参照できます。</p></div><span class="tag neutral">'+commits.length+' COMMITS</span></div>'+
      '<section class="panel">'+panelTitle("変更履歴","対象UUID: "+uuid)+'<div class="activity-list">'+
      (commits.length?commits.map(commit=>'<div class="activity-item"><div class="activity-rail"><i></i></div><div class="activity-main"><strong>'+esc(commit.subject || "コミット")+'</strong><p class="mono">'+esc(commit.hash || "")+' · '+esc(commit.author || "")+'</p><button class="text-link revision-link" data-action="revision" data-kind="'+kind+'" data-uuid="'+esc(uuid)+'" data-rev="'+esc(commit.hash)+'">この時点の内容を見る →</button></div><span class="activity-time">'+esc(dateText(commit.date))+'</span></div>').join(""):empty("履歴がありません","対象ファイルにGitコミットがまだありません。","↗"))+
      '</div></section>';
  } catch(e){renderError(e);}
}
async function openRevision(kind,uuid,rev) {
  state.view=kind==="law"?"laws":"rules";
  root.innerHTML='<div class="loading-state"><span class="loader"></span><p>過去の内容を取得しています…</p></div>';
  try {
    const data=await api(kind+"s/"+encodeURIComponent(uuid)+"/revisions/"+encodeURIComponent(rev));
    const content=data.xml || data.body || JSON.stringify(data,null,2);
    header(kind==="law"?"laws":"rules");
    root.innerHTML='<button class="back-link" data-action="back-history">← 変更履歴に戻る</button>'+
      '<div class="page-intro"><div><p class="eyebrow">HISTORICAL REVISION</p><p>コミット '+esc(rev)+' 時点の読み取り専用データです。</p></div><span class="tag gold">READ ONLY</span></div>'+
      '<section class="panel"><div class="code-label"><span>'+(kind==="law"?"XML REVISION":"RULE REVISION")+'</span><span class="mono">'+esc(hash(rev))+'</span></div><pre class="code-block">'+esc(content)+'</pre></section>';
  } catch(e){renderError(e);}
}
async function search(query) {
  state.view="search"; header("search");
  root.innerHTML='<div class="loading-state"><span class="loader"></span><p>検索しています…</p></div>';
  try {
    const data=await api("search?q="+encodeURIComponent(query)+"&limit=100");
    const hits=data.hits || [];
    root.innerHTML='<div class="page-intro"><div><p class="eyebrow">FULL-TEXT SEARCH</p><p>「'+esc(query)+'」の検索結果</p></div><span class="tag neutral">'+hits.length+' HITS</span></div>'+
      '<div class="search-results">'+(hits.length?hits.map(hit=>{
        const action=hit.kind==="law"?"law":"rule";
        return '<article class="search-hit"><div class="doc-icon">'+(hit.kind==="law"?"法":"R")+'</div><div class="row-main"><button class="table-link row-title" data-action="'+action+'" data-uuid="'+esc(hit.uuid)+'">'+esc(hit.name || "名称未設定")+'</button><div class="row-meta"><span class="tag neutral">'+esc(hit.kind || "—")+'</span><span>一致箇所: '+esc(hit.matched || "—")+'</span></div><p>'+esc(hit.snippet || "")+'</p></div></article>';
      }).join(""):empty("一致する文書はありません","別のキーワードで検索してください。","⌕"))+'</div>';
  } catch(e){renderError(e);}
}
async function loadActivity() {
  root.innerHTML='<div class="loading-state"><span class="loader"></span><p>Git履歴を読み込んでいます…</p></div>';
  try {
    const [log,changes]=await Promise.all([api("git/log?limit=50"),api("changes?limit=50")]);
    header("activity");
    const commits=log.commits || [];
    root.innerHTML='<div class="page-intro"><div><p class="eyebrow">GIT ACTIVITY</p><p>LawGitリポジトリに記録されたコミットと変更ファイルを閲覧します。</p></div><span class="tag neutral">'+commits.length+' RECENT COMMITS</span></div>'+
      '<div class="split-grid"><section class="panel">'+panelTitle("コミット履歴","新しい順")+'<div class="activity-list">'+activityRows(commits,50)+'</div></section>'+
      '<section class="panel"><div class="panel-head"><div><h3 class="panel-title">変更追従情報</h3><p class="panel-subtitle">同期用のheadと直近の変更</p></div></div><div class="side-info"><div class="info-line"><span>HEAD</span><strong class="mono">'+esc(hash(changes.head))+'</strong></div><div class="info-line"><span>変更コミット数</span><strong>'+esc((changes.commits || []).length)+'</strong></div><div class="info-line"><span>ポーリングAPI</span><strong>/api/v1/changes</strong></div><p class="notice notice-spaced">外部アプリはheadを保存し、次回からsinceパラメータを指定して差分を取得できます。</p></div></section></div>';
  } catch(e){renderError(e);}
}
async function loadIntegrity() {
  root.innerHTML='<div class="loading-state"><span class="loader"></span><p>リポジトリを検証しています…</p></div>';
  try {
    const [validation,status,log]=await Promise.all([api("validate"),api("git/status"),api("git/log?limit=8")]);
    header("integrity");
    root.innerHTML='<div class="page-intro"><div><p class="eyebrow">REPOSITORY INTEGRITY</p><p>法令・ルールの検証結果とGitワークツリーを閲覧します。</p></div><span class="tag neutral">READ ONLY</span></div>'+
      '<div class="stats-grid"><div class="stat-card"><div class="stat-top"><span>検証結果</span><span class="stat-mark">◇</span></div><div class="stat-value stat-word">'+(validation.valid?"VALID":"CHECK")+'</div><div class="stat-note">LawGit validate</div></div>'+
      '<div class="stat-card"><div class="stat-top"><span>法令数</span><span class="stat-mark">§</span></div><div class="stat-value">'+esc(validation.laws ?? "—")+'</div><div class="stat-note">Validation result</div></div>'+
      '<div class="stat-card"><div class="stat-top"><span>ルール数</span><span class="stat-mark">≡</span></div><div class="stat-value">'+esc(validation.rules ?? "—")+'</div><div class="stat-note">Validation result</div></div>'+
      '<div class="stat-card"><div class="stat-top"><span>コミット</span><span class="stat-mark">↗</span></div><div class="stat-value">'+((log.commits || []).length)+'</div><div class="stat-note">直近の履歴取得件数</div></div></div>'+
      '<div class="split-grid"><section class="panel"><div class="panel-head"><div><h3 class="panel-title">検証レポート</h3><p class="panel-subtitle">LawGit API / validate</p></div><span class="tag '+(validation.valid?"":"gold")+'">'+(validation.valid?"VALID":"REVIEW")+'</span></div><pre class="json-preview">'+esc(JSON.stringify(validation,null,2))+'</pre></section>'+
      '<section class="panel"><div class="panel-head"><div><h3 class="panel-title">Gitワークツリー</h3><p class="panel-subtitle">git status --porcelain</p></div></div><pre class="json-preview">'+esc(JSON.stringify(status,null,2))+'</pre></section></div>';
  } catch(e){renderError(e);}
}
async function navigate(view) {
  state.view=view;
  if(view==="overview") await loadOverview();
  else if(view==="laws") await loadLaws();
  else if(view==="rules") await loadRules();
  else if(view==="activity") await loadActivity();
  else if(view==="integrity") await loadIntegrity();
}
document.addEventListener("click",async event=>{
  const button=event.target.closest("[data-action]");
  if(!button) return;
  const action=button.dataset.action;
  if(action==="go-laws"){await navigate("laws");return;}
  if(action==="go-rules"){await navigate("rules");return;}
  if(action==="go-activity"){await navigate("activity");return;}
  if(action==="refresh"){await navigate(state.view==="search"?"overview":state.view);return;}
  if(action==="law"){await openLaw(button.dataset.uuid);return;}
  if(action==="rule"){await openRule(button.dataset.uuid);return;}
  if(action==="history"){await openHistory(button.dataset.kind,button.dataset.uuid);return;}
  if(action==="revision"){await openRevision(button.dataset.kind,button.dataset.uuid,button.dataset.rev);return;}
  if(action==="back-detail"){
    if(state.historyKind==="law") await openLaw(state.historyUuid); else await openRule(state.historyUuid);
    return;
  }
  if(action==="back-history"){await openHistory(state.historyKind,state.historyUuid);return;}
});
document.querySelectorAll(".nav-item").forEach(button=>button.addEventListener("click",()=>navigate(button.dataset.view)));
document.getElementById("refresh-button").addEventListener("click",()=>navigate(state.view));
document.getElementById("search-form").addEventListener("submit",event=>{
  event.preventDefault();
  const query=document.getElementById("global-search").value.trim();
  if(query) search(query);
});
loadOverview();
