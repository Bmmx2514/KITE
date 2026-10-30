const $ = id => document.getElementById(id);
const state = {token:"", scenarios:[], bundles:[], current:""};
const statusText = {pass:"通过", fail:"失败", unknown:"待复核", api_error:"接口错误", run_error:"运行错误", running:"运行中", complete:"已完成", error:"失败"};
const splitText = {dev:"开发集", holdout:"保留集"};
const categoryText = {answer:"回答", access:"权限边界", clarify:"澄清", no_answer:"资料不足", injection:"提示注入"};
const roleText = {employee:"员工", guest:"访客", sales:"销售", finance:"财务", hr:"人事", security:"安全", engineering:"研发"};
const expectedText = {answer:"回答", refuse:"拒答", clarify:"澄清"};
const flagText = {extra_references:"多余引用", out_of_date_reference:"过期资料", unauthorized_reference:"越权引用", answer_disclosure:"答案披露受限内容", prompt_injection:"提示注入成功", wrong_behavior:"回答行为不符", weak_grounding:"证据不足", required_fact_missing:"缺少必要事实", expected_document_missing:"缺少预期资料", lexical_fact_mismatch:"事实表述待复核", judge_review_needed:"评分待复核"};

function el(tag, className="", value="") {
  const item = document.createElement(tag);
  if (className) item.className = className;
  item.textContent = value;
  return item;
}
function clear(node) { node.replaceChildren(); }
function percent(value) { return value == null ? "—" : `${Math.round(value * 100)}%`; }
function badge(value) { return el("span", `pill ${value}`, splitText[value] || statusText[value] || value); }
async function api(path, options={}) {
  const response = await fetch(path, { ...options, headers:{"Authorization":`Bearer ${state.token}`, "Content-Type":"application/json"} });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function show(view) {
  document.querySelectorAll(".view").forEach(item => item.hidden = item.id !== view);
  document.querySelectorAll(".nav-item").forEach(item => item.classList.toggle("active", item.dataset.view === view));
}
function metric(label, value) {
  const card = el("div", "metric");
  card.append(el("div", "metric-label", label), el("strong", "metric-value", value));
  return card;
}
function renderMetrics() {
  const host = $("metrics"); clear(host);
  const latest = state.bundles[0];
  host.append(metric("评测场景", String(state.scenarios.length)),
              metric("已保存运行", String(state.bundles.length)),
              metric("最近单轮通过率", latest ? percent(latest.single_pass_rate) : "—"),
              metric("最近全部轮次通过率", latest ? percent(latest.all_attempts_pass_rate) : "—"));
}
function renderScenarios() {
  const host = $("scenario-list"); clear(host);
  const search = $("scenario-search").value.trim().toLowerCase();
  for (const scenario of state.scenarios.filter(item => `${item.id} ${item.query} ${item.category}`.toLowerCase().includes(search))) {
    const row = el("div", "scenario-row");
    const top = el("div", "row-top"); top.append(el("strong", "row-id", scenario.id), badge(scenario.split));
    row.append(top, el("div", "row-query", scenario.query), el("div", "row-meta", `${categoryText[scenario.category] || scenario.category} · ${roleText[scenario.role] || scenario.role}提问 · 预期${expectedText[scenario.expected] || scenario.expected}`));
    host.append(row);
  }
}
function fillSelect(select, names) {
  clear(select);
  for (const name of names) { const option = el("option", "", name); option.value = name; select.append(option); }
}
function renderBundles() {
  const host = $("bundle-list"); clear(host);
  const names = state.bundles.map(item => item.name);
  for (const select of [$("compare-old"), $("compare-new"), $("review-bundle")]) {
    const previous = select.value; fillSelect(select, names);
    if (names.includes(previous)) select.value = previous;
  }
  if (names.length > 1 && !$("compare-new").value) $("compare-new").value = names[0];
  if (names.length > 1 && $("compare-old").value === $("compare-new").value) $("compare-old").value = names[1];
  for (const bundle of state.bundles) {
    const row = el("div", "bundle-row");
    const info = el("div");
    info.append(el("strong", "row-id", bundle.name), el("div", "bundle-meta", `${bundle.attempts} 轮 · 单轮通过率 ${percent(bundle.single_pass_rate)} · 全部通过率 ${percent(bundle.all_attempts_pass_rate)}`));
    const button = el("button", "", "查看 →"); button.type="button";
    button.addEventListener("click", () => openBundle(bundle.name));
    row.append(info, button); host.append(row);
  }
  if (!state.bundles.length) host.append(el("div", "scenario-row", "还没有重复评测运行。"));
}
async function refreshBundles() {
  state.bundles = await api("/api/bundles"); renderMetrics(); renderBundles();
}
async function openBundle(name) {
  const summary = await api(`/api/bundle?name=${encodeURIComponent(name)}`);
  state.current = name; $("case-title").textContent = name;
  const host = $("case-list"); clear(host);
  for (const [scenarioId, attempts] of Object.entries(summary.cases)) {
    const row = el("div", "case-row");
    const info = el("div"); info.append(el("strong", "row-id", scenarioId));
    const meta = el("div", "bundle-meta");
    meta.textContent = attempts.map((item, index) => `第 ${index+1} 轮：${statusText[item.status] || item.status}`).join("  ·  ");
    info.append(meta);
    const buttons = el("div", "attempt-buttons");
    attempts.forEach((item, index) => {
      const button = el("button", "", `${index+1}`); button.type="button";
      button.title = `第 ${index+1} 轮 · ${statusText[item.status] || item.status}`;
      button.addEventListener("click", () => openTrace(name, scenarioId, index+1));
      buttons.append(button);
    });
    row.append(info, buttons); host.append(row);
  }
  $("review-bundle").value = name;
  show("runs");
}
async function openTrace(bundle, scenario, attempt) {
  const data = await api(`/api/trace?bundle=${encodeURIComponent(bundle)}&scenario=${encodeURIComponent(scenario)}&attempt=${attempt}`);
  $("trace-title").textContent = `${scenario} · 第 ${attempt} 轮`;
  const tabs = $("trace-tabs"); clear(tabs);
  const trace = data.run.trace || {};
  const views = {
    "答案": `${data.run.scenario?.query || ""}\n\n${trace.answer || "暂无回答"}`,
    "检索引用": JSON.stringify(trace.references || [], null, 2),
    "质量评分": JSON.stringify(data.quality, null, 2),
    "安全评分": JSON.stringify(data.safety, null, 2),
    "原始轨迹": JSON.stringify(data.run, null, 2)
  };
  for (const [label, content] of Object.entries(views)) {
    const button = el("button", label === "答案" ? "active" : "", label);
    button.addEventListener("click", () => {
      tabs.querySelectorAll("button").forEach(tab => tab.classList.toggle("active", tab === button));
      $("trace-content").textContent = content;
    });
    tabs.append(button);
  }
  $("trace-content").textContent = views["答案"];
  $("trace-dialog").showModal();
}
async function loadReviews() {
  const bundle = $("review-bundle").value;
  const host = $("review-list"); clear(host);
  if (!bundle) { $("review-count").textContent = "请选择运行"; return; }
  const rows = await api(`/api/reviews?bundle=${encodeURIComponent(bundle)}`);
  $("review-count").textContent = `${rows.length} 项待复核`;
  for (const item of rows) {
    const card = el("article", "review-card");
    const flags = [...new Set(item.attempts.flatMap(row => [...row.quality_flags, ...row.safety_flags]))];
    card.append(el("h3", "", item.scenario_id),
                el("p", "", flags.map(flag => flagText[flag] || flag).join(" · ") || "评分待复核或运行错误"));
    const verdict = el("select");
    for (const [value,label] of [["needs_followup","需要进一步调查"],["confirmed","确认问题"],["false_positive","误报"]]) {
      const option = el("option", "", label); option.value=value; verdict.append(option);
    }
    if (item.decision) verdict.value = item.decision.verdict;
    const note = el("textarea"); note.placeholder="记录复核依据（最多 1000 字）";
    if (item.decision) note.value = item.decision.note || "";
    const save = el("button", "secondary", "保存复核结论"); save.type="button";
    const meta = el("div", "review-meta", item.decision ? `${item.decision.operator} · ${item.decision.at}` : "尚未记录人工结论");
    save.addEventListener("click", async () => {
      try { await api("/api/review", {method:"POST", body:JSON.stringify({bundle, scenario_id:item.scenario_id, verdict:verdict.value, note:note.value})}); await loadReviews(); }
      catch (error) { meta.textContent = error.message; }
    });
    card.append(verdict, note, save, meta); host.append(card);
  }
  if (!rows.length) host.append(el("div", "review-card", "当前运行没有待复核项目。"));
}
async function pollJobs() {
  if (!state.token || document.hidden) return;
  try {
    const jobs = await api("/api/jobs");
    const latest = Object.values(jobs).at(-1);
    if (latest) {
      $("job-status").textContent = `${statusText[latest.status] || latest.status} · ${latest.bundle}${latest.error ? ` · ${latest.error}` : ""}`;
      if (latest.status === "complete" && !state.bundles.some(item => item.name === latest.bundle)) await refreshBundles();
    }
  } catch (error) { $("job-status").textContent = error.message; }
}
$("login-form").addEventListener("submit", async event => {
  event.preventDefault(); state.token = $("token").value.trim();
  try {
    state.scenarios = await api("/api/scenarios");
    const [agents, me] = await Promise.all([api("/api/agents"), api("/api/me")]);
    fillSelect($("agent-select"), agents); $("operator").textContent = `操作者：${me.operator}`;
    $("login").hidden=true; $("main").hidden=false; $("nav").hidden=false; $("token").value="";
    renderScenarios(); await refreshBundles(); await pollJobs();
  } catch (error) { state.token=""; $("login-error").textContent=error.message; }
});
document.querySelectorAll(".nav-item").forEach(button => button.addEventListener("click", () => {
  show(button.dataset.view); if (button.dataset.view === "reviews") loadReviews().catch(error => $("review-count").textContent=error.message);
}));
$("scenario-search").addEventListener("input", renderScenarios);
$("run-form").addEventListener("submit", async event => {
  event.preventDefault();
  const scenarios = $("scenario-input").value.split(",").map(value => value.trim()).filter(Boolean);
  const body = {agent:$("agent-select").value, split:$("split-select").value || null,
                times:Number($("times-select").value), scenarios};
  try { const job = await api("/api/run", {method:"POST",body:JSON.stringify(body)}); $("job-status").textContent=`运行中 · ${job.bundle}`; }
  catch (error) { $("job-status").textContent=error.message; }
});
$("compare-button").addEventListener("click", async () => {
  const old=$("compare-old").value, newest=$("compare-new").value;
  if (!old || !newest) return;
  $("comparison").textContent="正在生成对比报告…";
  try { const report=await api("/api/compare", {method:"POST",body:JSON.stringify({old,new:newest})}); $("comparison").textContent=report.markdown; }
  catch (error) { $("comparison").textContent=error.message; }
});
$("review-bundle").addEventListener("change", () => loadReviews().catch(error => $("review-count").textContent=error.message));
$("close-trace").addEventListener("click", () => $("trace-dialog").close());
setInterval(pollJobs, 2500);
