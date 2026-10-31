/* ModelEval 控制台逻辑 */
const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
let providers = [], tasks = [], currentRun = null, es = null;

// ---------- 通用 ----------
async function api(url, opts = {}) {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(`${r.status}: ${t}`);
  }
  return r.json();
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmtDur = (ms) => ms >= 1000 ? (ms / 1000).toFixed(1) + "s" : ms + "ms";

// ---------- 视图切换 ----------
$$(".tab").forEach((t) => t.onclick = () => {
  $$(".tab").forEach((x) => x.classList.toggle("active", x === t));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + t.dataset.view));
  if (t.dataset.view === "history") loadRuns();
  if (t.dataset.view === "templates") loadTemplates();
  if (t.dataset.view === "providers") loadProvidersAdmin();
});

// ---------- 初始化 ----------
async function init() {
  [providers, tasks] = await Promise.all([api("/api/providers"), api("/api/tasks")]);
  $("#f-task").innerHTML = tasks.map((t) => `<option value="${t.id}">${t.label}</option>`).join("");
  renderProviders();
  bindForm();
  window.cube3d = Cube3D.mount($("#cube-stage"), $("#cube3d"), $("#cube-label"));
}

function renderProviders() {
  $("#f-provider").innerHTML = providers.map((p) =>
    `<option value="${p.id}">${esc(p.name)}${p.protocol === "anthropic" ? " [A]" : ""}${p.has_key ? " ✔" : ""}</option>`).join("");
  onProviderChange();
}

function curProvider() { return providers.find((p) => p.id === $("#f-provider").value); }

function onProviderChange() {
  const p = curProvider();
  if (!p) return;
  const sel = $("#f-model");
  sel.innerHTML = (p.models || []).map((m) => `<option value="${esc(m.name)}">${esc(m.name)}</option>`).join("");
  if (p.models && p.models.length) sel.value = p.models[0].name;
  $("#f-baseurl").value = p.base_url || "";
  $("#key-hint").textContent = p.has_key ? `已保存（尾号 ${p.key_hint}）` : (p.id === "mock" ? "Mock 无需 Key" : "未保存 Key");
  $("#key-group").style.display = p.id === "mock" ? "none" : "";
  $("#provider-hint").textContent = p.base_url
    ? `默认端点：${p.base_url}（协议：${p.protocol === "anthropic" ? "Anthropic" : "OpenAI 兼容"}）`
    : "请在下方填写 Base URL";
  onModelChange();
}

// 选择模型后自动载入该模型的默认参数（temperature / top_p / max_tokens），允许用户在评测前修改
function onModelChange() {
  const p = curProvider();
  if (!p) return;
  const name = $("#f-model").value;
  const m = (p.models || []).find((x) => x.name === name);
  if (m) {
    $("#f-temperature").value = m.temperature;
    $("#f-top_p").value = m.top_p;
    $("#f-max_tokens").value = m.max_tokens;
    $("#model-info").textContent =
      `已载入模型参数 ｜ 上下文 ${fmtNum(m.context_length)}${m.multimodal ? " ｜ 多模态" : ""}`
      + ` ｜ 价(元/百万): 命中 ${m.cache_hit_input_price} / 未命中 ${m.cache_miss_input_price} / 输出 ${m.output_price}（可修改）`;
  } else {
    $("#model-info").textContent = (p.models && p.models.length)
      ? "（该模型无预置参数，使用默认值）"
      : "该供应商下暂无模型，可在「供应商管理」中添加";
  }
}

function bindForm() {
  $("#f-provider").onchange = onProviderChange;
  $("#f-model").onchange = onModelChange;
  $("#btn-save-key").onclick = async () => {
    const p = curProvider();
    await api(`/api/providers/${p.id}`, { method: "PUT", body: JSON.stringify({
      api_key: $("#f-apikey").value.trim(), base_url: $("#f-baseurl").value.trim() }) });
    $("#f-apikey").value = "";
    providers = await api("/api/providers");
    renderProviders();
  };

  $("#btn-start").onclick = () => startRun(false);
  $("#btn-debug").onclick = () => startRun(true);
  $("#btn-stop").onclick = () => currentRun && api(`/api/runs/${currentRun}/stop`, { method: "POST" });
  $("#btn-save-template").onclick = () => { $("#tpl-name").value = ""; $("#dlg-template").showModal(); };
  $("#tpl-cancel").onclick = () => $("#dlg-template").close();
  $("#tpl-save").onclick = saveTemplate;
}

function collectConfig(nCasesOverride) {
  return {
    task: $("#f-task").value,
    provider: $("#f-provider").value,
    model: $("#f-model").value.trim(),
    base_url: $("#f-baseurl").value.trim(),
    model_params: {
      temperature: +$("#f-temperature").value,
      top_p: +$("#f-top_p").value,
      max_tokens: +$("#f-max_tokens").value,
    },
    limits: {
      max_calls: +$("#f-max_calls").value,
      max_total_tokens: +$("#f-max_total_tokens").value,
      tool_enabled: $("#f-tool_enabled").value === "1",
      api_timeout: +$("#f-api_timeout").value,
      retry: +$("#f-retry").value,
      min_call_interval: +$("#f-min_call_interval").value,
    },
    task_params: {
      max_steps: +$("#f-max_steps").value,
      scramble_length: +$("#f-scramble_length").value,
    },
    n_cases: nCasesOverride || +$("#f-n_cases").value || 1,
    seed: $("#f-seed").value ? +$("#f-seed").value : null,
  };
}

async function startRun(debug) {
  const cfg = collectConfig(debug ? 1 : undefined);
  try {
    const res = await api("/api/runs", { method: "POST", body: JSON.stringify(cfg) });
    openStream(res.run_id, cfg.n_cases);
  } catch (e) { alert("启动失败: " + e.message); }
}

async function saveTemplate() {
  const name = $("#tpl-name").value.trim();
  if (!name) return alert("请填写模板名称");
  await api("/api/configs", { method: "POST", body: JSON.stringify({ name, config: collectConfig() }) });
  $("#dlg-template").close();
  alert("已保存模板: " + name);
}

// ---------- 实时流 ----------
function openStream(runId, nCases) {
  currentRun = runId;
  const feed = $("#feed");
  feed.innerHTML = "";
  $("#run-summary").classList.add("hidden");
  $("#run-badge").className = "badge running";
  $("#run-badge").textContent = "运行中 #" + runId;
  $("#run-badge").classList.remove("hidden");
  $("#btn-stop").classList.remove("hidden");
  $("#run-progress").classList.remove("hidden");
  setProgress(0, nCases);

  es = new EventSource(`/api/runs/${runId}/stream?after=0`);
  const cardMap = {}; // key: caseId:callSeq -> {el, preR, preC, preT}

  es.onmessage = (ev) => {
    let msg; try { msg = JSON.parse(ev.data); } catch { return; }
    const p = msg.payload || {};
    switch (msg.type) {
      case "case_started": {
        const d = document.createElement("div");
        d.className = "case-divider";
        d.id = `case-${p.case_id}`;
        d.innerHTML = `<b>案例 #${p.idx}</b><span class="scramble">${esc(p.scramble)}</span>
          <span class="hint">上限 ${p.max_steps} 步${p.optimal_len ? " · 参考最优 " + p.optimal_len + " 步" : ""}</span>
          <span class="result" id="case-r-${p.case_id}"></span>`;
        feed.appendChild(d);
        if (window.cube3d) window.cube3d.reset((p.scramble || "").split(/\s+/).filter(Boolean));
        break;
      }
      case "llm_start": {
        const key = `${p.case_id}:${p.call_seq}`;
        const el = document.createElement("div");
        el.className = "icard llm";
        el.innerHTML = `<div class="ihead"><span class="live-dot"></span><b>LLM 调用 #${p.call_seq}</b>
          <span>${esc(p.model || "")}</span><span class="lat"></span></div>
          <details><summary>请求 messages</summary><pre>${esc(JSON.stringify(p, null, 1)).slice(0, 0)}</pre></details>
          <details open><summary>思考链</summary><pre class="reasoning"></pre></details>
          <details open><summary>回复内容</summary><pre class="content"></pre></details>
          <details><summary>工具调用（流式）</summary><pre class="toolargs"></pre></details>
          <div class="usage-line"></div>`;
        feed.appendChild(el);
        feed.scrollTop = feed.scrollHeight;
        cardMap[key] = { el, preR: el.querySelector(".reasoning"), preC: el.querySelector(".content"),
          preT: el.querySelector(".toolargs"), lat: el.querySelector(".lat") };
        break;
      }
      case "llm_delta": {
        const key = `${p.case_id}:${p.call_seq}`;
        const c = cardMap[key];
        if (!c) break;
        if (p.field === "reasoning") { c.preR.textContent += p.text; autoScroll(c.preR); }
        else if (p.field === "content") { c.preC.textContent += p.text; autoScroll(c.preC); }
        else if (p.field === "tool_call") { c.preT.textContent += p.text; }
        break;
      }
      case "interaction": {
        if (p.kind === "llm") {
          const key = `${p.case_id}:${p.call_seq}`;
          const c = cardMap[key];
          if (c) {
            c.el.querySelector(".live-dot")?.remove();
            c.lat.textContent = fmtDur(p.latency_ms || 0);
            const det = c.el.querySelector("details");
            det.querySelector("pre").textContent = JSON.stringify(p.request, null, 1);
            c.el.querySelector(".usage-line").textContent =
              `token ↑${p.usage?.prompt_tokens ?? "-"} ↓${p.usage?.completion_tokens ?? "-"}`
              + (p.usage?.reasoning_tokens ? `（思考 ${p.usage.reasoning_tokens}）` : "")
              + ` ｜ finish=${p.finish_reason || "-"}`;
          } else {
            appendStaticInteraction(feed, p);
          }
        } else if (p.kind === "tool") {
          const el = document.createElement("div");
          el.className = "icard tool";
          const r = p.result || {};
          el.innerHTML = `<div class="ihead"><b>工具 turn_cube</b><span>${esc((r.applied || []).join(" "))}</span>
            ${r.solved ? '<span style="color:var(--ok)">已还原!</span>' : ""}${r.error ? `<span style="color:var(--bad)">${esc(r.error)}</span>` : ""}</div>
            <details><summary>入参 / 返回</summary><pre>${esc(JSON.stringify({ 入参: p.tool_call, 返回: r }, null, 1))}</pre></details>`;
          feed.appendChild(el);
          feed.scrollTop = feed.scrollHeight;
          if (window.cube3d && r.applied && r.applied.length) window.cube3d.apply(r.applied);
        }
        break;
      }
      case "llm_error": {
        const el = document.createElement("div");
        el.className = "icard error";
        el.innerHTML = `<div class="ihead"><b>API 异常</b><span style="color:var(--bad)">${esc(p.error)}</span></div>`;
        feed.appendChild(el);
        break;
      }
      case "llm_retry": {
        const el = document.createElement("div");
        el.className = "icard error";
        el.innerHTML = `<div class="ihead"><b>重试 #${p.attempt}</b><span>${esc(p.error)}（${p.backoff}s 后重试）</span></div>`;
        feed.appendChild(el);
        break;
      }
      case "moves_applied": {
        if (window.cube3d && p.moves && p.moves.length) window.cube3d.apply(p.moves);
        break;
      }
      case "case_finished": {
        const tag = $(`#case-r-${p.case_id}`);
        if (tag) {
          tag.textContent = `${p.solved ? "✅ 还原" : "❌ " + stopLabel(p.stop_reason)} · ${p.moves_used} 步`;
          tag.className = "result " + (p.solved ? "ok" : "bad");
        }
        if (window.cube3d) window.cube3d.setLabel(p.solved ? "✔ 已还原" : "✘ " + stopLabel(p.stop_reason), p.solved ? "ok" : "bad");
        break;
      }
      case "run_finished": {
        $("#run-badge").className = "badge " + (p.status === "finished" ? "finished" : p.status);
        $("#run-badge").textContent = p.status === "finished" ? "已完成" : (p.status === "stopped" ? "已停止" : "异常");
        $("#btn-stop").classList.add("hidden");
        const g = p.summary?.generic;
        if (g) {
          $("#run-summary").classList.remove("hidden");
          $("#run-summary").innerHTML =
            `成功率 <b>${g.solved}/${g.total}</b>（${(g.success_rate * 100).toFixed(0)}%）｜ 调用 ${g.total_calls} 次 ｜
             token ${g.tokens.total}（↑${g.tokens.prompt} ↓${g.tokens.completion} 思考${g.tokens.reasoning}）｜
             <a href="/report?run_id=${currentRun}" target="_blank">查看完整报告 →</a>`;
        }
        break;
      }
      case "run_done": es.close(); break;
      case "resync": es.close(); openStream(runId, nCases); break;
    }
  };
  es.onerror = () => { /* EventSource 自动重连 */ };
}

function appendStaticInteraction(feed, p) {
  const el = document.createElement("div");
  el.className = "icard llm";
  el.innerHTML = `<div class="ihead"><b>LLM 调用 #${p.call_seq}</b><span>${fmtDur(p.latency_ms || 0)}</span></div>
    <details><summary>请求 messages</summary><pre>${esc(JSON.stringify(p.request, null, 1))}</pre></details>
    <details><summary>思考链</summary><pre class="reasoning">${esc(p.reasoning)}</pre></details>
    <details open><summary>回复内容</summary><pre>${esc(p.content)}</pre></details>
    <div class="usage-line">token ↑${p.usage?.prompt_tokens ?? "-"} ↓${p.usage?.completion_tokens ?? "-"}</div>`;
  feed.appendChild(el);
}

function autoScroll(pre) { pre.scrollTop = pre.scrollHeight; }
function setProgress(done, total) {
  $("#run-progress-bar").style.width = (total ? (done / total * 100) : 0) + "%";
  $("#run-progress-text").textContent = `${done}/${total} 样例`;
}
function stopLabel(r) {
  return { max_steps: "步数超限", max_calls: "调用超限", max_tokens: "token超限",
    api_error: "API异常", invalid_output: "无效输出", stopped: "人工停止" }[r] || r;
}

// ---------- 历史 ----------
const fmtCost = (c) => (c && c.has_price) ? "¥" + (+c.total || 0).toFixed(4) : "—";

async function loadRuns() {
  const runs = await api("/api/runs");
  const tb = $("#runs-table tbody");
  tb.innerHTML = runs.map((r) => {
    let rate = "-", cost = "—";
    try {
      if (r.summary) {
        const g = JSON.parse(r.summary).generic;
        rate = `${g.solved}/${g.total}`;
        cost = fmtCost(g.cost);
      }
    } catch {}
    return `<tr class="tree-provider">
      <td><span class="tree-toggle link" onclick="toggleRun(${r.id}, this)">+</span> #${r.id}</td>
      <td>${esc(r.task)}</td><td>${esc(r.provider)}/${esc(r.model)}</td>
      <td>${r.n_cases}</td><td>${badge(r.status)}</td><td>${rate}</td><td>${cost}</td>
      <td>${esc(r.created_at)}</td>
      <td><span class="link" onclick="openReport(${r.id})">报告</span></td></tr>
    <tr class="case-row" id="run-children-${r.id}" style="display:none"><td colspan="9"><span class="hint">加载中…</span></td></tr>`;
  }).join("") || `<tr><td colspan="9" class="empty">暂无评测记录</td></tr>`;
}

window.toggleRun = async function (runId, el) {
  const row = document.getElementById("run-children-" + runId);
  if (!row) return;
  if (row.style.display !== "none") { row.style.display = "none"; el.textContent = "+"; return; }
  el.textContent = "-";
  if (!row.dataset.loaded) {
    const data = await api(`/api/runs/${runId}`);
    const cases = data.cases || [];
    row.firstElementChild.innerHTML = cases.length ? miniCaseTable(cases) : '<span class="hint">无样例数据</span>';
    row.dataset.loaded = "1";
  }
  row.style.display = "";
};

function miniCaseTable(cases) {
  return `<table class="tbl inner">
    <thead><tr><th>样例</th><th>状态</th><th>终止原因</th><th>步数</th><th>调用</th>
      <th>token ↑/↓/思考</th><th>费用(元)</th><th>打乱序列</th></tr></thead>
    <tbody>${cases.map((c) => `<tr>
      <td>#${c.idx}</td>
      <td>${c.status === "success" ? '<span style="color:var(--ok)">✅</span>' : '<span style="color:var(--bad)">❌</span>'}</td>
      <td>${stopLabel(c.stop_reason)}</td>
      <td>${c.moves_used}</td><td>${c.calls}</td>
      <td>${c.prompt_tokens}/${c.completion_tokens}/${c.reasoning_tokens}</td>
      <td>${(+c.cost || 0).toFixed(4)}</td>
      <td class="hint">${esc(c.scramble)}</td></tr>`).join("")}</tbody></table>`;
}
window.openReport = (id) => window.open(`/report?run_id=${id}`);
function badge(s) { return `<span class="badge ${s}">${{ running: "运行中", finished: "已完成", stopped: "已停止", error: "异常" }[s] || s}</span>`; }

// ---------- 模板 ----------
async function loadTemplates() {
  const tpls = await api("/api/configs");
  const tb = $("#tpl-table tbody");
  tb.innerHTML = tpls.map((t) => {
    const c = t.config || {};
    return `<tr><td>${esc(t.name)}</td><td>${esc(c.task || "")}</td><td>${esc(c.provider || "")}</td>
      <td>${esc(c.model || "")}</td><td>${c.n_cases || ""}</td><td>${esc(t.updated_at)}</td>
      <td><span class="link" onclick='loadTpl(${JSON.stringify(JSON.stringify(t.config))},${t.id})'>加载</span>
      ｜<span class="link" onclick='delTpl(${t.id})'>删除</span></td></tr>`;
  }).join("");
}
window.loadTpl = function (cfgJson, id) {
  const c = JSON.parse(cfgJson);
  $("#f-task").value = c.task || "cube";
  $("#f-provider").value = c.provider || "";
  onProviderChange();
  $("#f-model").value = c.model || "";
  $("#f-baseurl").value = c.base_url || "";
  const mp = c.model_params || {}, lm = c.limits || {}, tp = c.task_params || {};
  $("#f-temperature").value = mp.temperature ?? 0.7;
  $("#f-top_p").value = mp.top_p ?? 1;
  $("#f-max_tokens").value = mp.max_tokens ?? 4096;
  $("#f-max_calls").value = lm.max_calls ?? 20;
  $("#f-max_total_tokens").value = lm.max_total_tokens ?? 200000;
  $("#f-tool_enabled").value = lm.tool_enabled ? "1" : "0";
  $("#f-api_timeout").value = lm.api_timeout ?? 120;
  $("#f-retry").value = lm.retry ?? 2;
  $("#f-min_call_interval").value = lm.min_call_interval ?? 0;
  $("#f-max_steps").value = tp.max_steps ?? 50;
  $("#f-scramble_length").value = tp.scramble_length ?? 20;
  $("#f-n_cases").value = c.n_cases ?? 3;
  $("#f-seed").value = c.seed ?? "";
  $$(".tab")[0].click();
};
window.delTpl = async function (id) {
  if (!confirm("确认删除该模板？")) return;
  await api(`/api/configs/${id}`, { method: "DELETE" });
  loadTemplates();
};

// ---------- 供应商 / 模型管理（树形表 + 对话框） ----------
const expanded = new Set();
let editingModelId = null;
let modelProviderId = null;   // 对话框中正在编辑的模型所属供应商

function fmtNum(n) {
  n = +n || 0;
  if (n >= 1000000) return (n / 1000000) + "M";
  if (n >= 1000) return (n / 1000) + "K";
  return String(n);
}

async function loadProvidersAdmin() {
  providers = await api("/api/providers");
  providers.forEach((p) => { if (!expanded.has(p.id)) expanded.add(p.id); });
  renderProvidersTable();
  renderProviders();   // 同步刷新评测页的供应商/模型下拉
}

const protocolBadge = (p) => p.protocol === "anthropic"
  ? '<span class="badge running">Anthropic</span>'
  : '<span class="badge finished">OpenAI</span>';

function renderProvidersTable() {
  const tb = $("#providers-table tbody");
  if (!providers.length) {
    tb.innerHTML = `<tr><td colspan="10" class="empty">暂无供应商，点击右上角「添加供应商」</td></tr>`;
    return;
  }
  let html = "";
  for (const p of providers) {
    const open = expanded.has(p.id);
    const models = p.models || [];
    html += `<tr class="tree-provider">
      <td><span class="tree-toggle link" onclick="toggleTree('${p.id}')">${open ? "-" : "+"}</span>
        <b>${esc(p.name)}</b> <span class="hint-inline">${esc(p.id)}</span></td>
      <td>${protocolBadge(p)}</td>
      <td class="hint">${esc(p.base_url || "-")}</td>
      <td>${p.has_key ? "✔ " + esc(p.key_hint || "") : "—"}</td>
      <td class="hint">${models.length ? "共 " + models.length + " 个模型" : "—"}</td>
      <td class="hint" colspan="3"></td>
      <td></td>
      <td><span class="link" onclick="addModelFor('${p.id}')">+模型</span> ｜
        <span class="link" onclick="editProvider('${p.id}')">编辑</span> ｜
        <span class="link" onclick="delProvider('${p.id}')">删除</span></td>
    </tr>`;
    if (open) {
      html += models.map((m) => `<tr class="tree-model">
        <td class="model-name">  ${esc(m.name)}</td>
        <td class="hint">—</td><td class="hint">—</td><td class="hint">—</td>
        <td>${fmtNum(m.context_length)}</td>
        <td>${m.cache_hit_input_price}</td><td>${m.cache_miss_input_price}</td><td>${m.output_price}</td>
        <td>${m.multimodal ? "是" : "否"}</td>
        <td><span class="link" onclick="editModel(${m.id})">编辑</span> ｜ <span class="link" onclick="delModel(${m.id})">删除</span></td>
      </tr>`).join("");
      if (!models.length) {
        html += `<tr class="tree-model"><td class="model-name hint">  暂无模型，点上方「+模型」添加</td>
          <td class="hint">—</td><td class="hint">—</td><td class="hint">—</td><td class="hint">—</td>
          <td class="hint">—</td><td class="hint">—</td><td class="hint">—</td><td class="hint">—</td><td></td></tr>`;
      }
    }
  }
  tb.innerHTML = html;
}

window.toggleTree = (id) => {
  expanded.has(id) ? expanded.delete(id) : expanded.add(id);
  renderProvidersTable();
};

/* ---- 供应商对话框 ---- */
window.editProvider = (id) => {
  const p = providers.find((x) => x.id === id);
  if (!p) return;
  $("#dlg-provider-title").textContent = "编辑供应商";
  $("#pf-id").value = p.id; $("#pf-id").disabled = true;
  $("#pf-name").value = p.name;
  $("#pf-protocol").value = p.protocol || "openai";
  $("#pf-baseurl").value = p.base_url || "";
  $("#pf-apikey").value = "";
  $("#dlg-provider").showModal();
};

window.delProvider = async (id) => {
  if (!confirm(`确认删除供应商 ${id} 及其下所有模型？`)) return;
  await api(`/api/providers/${id}`, { method: "DELETE" });
  loadProvidersAdmin();
};

$("#btn-add-provider").onclick = () => {
  $("#dlg-provider-title").textContent = "添加供应商";
  $("#pf-id").value = ""; $("#pf-id").disabled = false;
  $("#pf-name").value = ""; $("#pf-protocol").value = "openai";
  $("#pf-baseurl").value = ""; $("#pf-apikey").value = "";
  $("#dlg-provider").showModal();
};
$("#pf-cancel").onclick = () => $("#dlg-provider").close();
$("#pf-save").onclick = async () => {
  const id = $("#pf-id").value.trim();
  const body = {
    id, name: $("#pf-name").value.trim(), protocol: $("#pf-protocol").value,
    base_url: $("#pf-baseurl").value.trim(), api_key: $("#pf-apikey").value.trim(),
  };
  if (!body.name) return alert("请填写名称");
  const isEdit = !!providers.find((p) => p.id === id);
  try {
    if (isEdit) await api(`/api/providers/${id}`, { method: "PUT", body: JSON.stringify(body) });
    else await api(`/api/providers`, { method: "POST", body: JSON.stringify(body) });
    $("#dlg-provider").close();
    loadProvidersAdmin();
  } catch (e) { alert("保存失败: " + e.message); }
};

/* ---- 模型对话框 ---- */
window.addModelFor = (pid) => {
  editingModelId = null; modelProviderId = pid;
  const p = providers.find((x) => x.id === pid) || {};
  $("#mf-provider").textContent = p.name || pid;
  $("#mf-name").value = ""; $("#mf-ch").value = 0; $("#mf-cm").value = 0; $("#mf-co").value = 0;
  $("#mf-ctx").value = 1000000; $("#mf-temp").value = 0.7; $("#mf-topp").value = 1.0;
  $("#mf-maxtok").value = 4096; $("#mf-multi").value = "0";
  $("#dlg-model").showModal();
};

function findModel(id) {
  for (const p of providers) {
    const m = (p.models || []).find((x) => x.id === id);
    if (m) return { p, m };
  }
  return null;
}

window.editModel = (id) => {
  const hit = findModel(id);
  if (!hit) return;
  editingModelId = id; modelProviderId = hit.p.id;
  $("#mf-provider").textContent = hit.p.name;
  $("#mf-name").value = hit.m.name;
  $("#mf-ch").value = hit.m.cache_hit_input_price; $("#mf-cm").value = hit.m.cache_miss_input_price;
  $("#mf-co").value = hit.m.output_price; $("#mf-ctx").value = hit.m.context_length;
  $("#mf-temp").value = hit.m.temperature; $("#mf-topp").value = hit.m.top_p;
  $("#mf-maxtok").value = hit.m.max_tokens; $("#mf-multi").value = hit.m.multimodal ? "1" : "0";
  $("#dlg-model").showModal();
};

$("#mf-cancel").onclick = () => $("#dlg-model").close();
$("#mf-save").onclick = async () => {
  const body = {
    name: $("#mf-name").value.trim(),
    cache_hit_input_price: +$("#mf-ch").value,
    cache_miss_input_price: +$("#mf-cm").value,
    output_price: +$("#mf-co").value,
    context_length: +$("#mf-ctx").value || 1000000,
    temperature: +$("#mf-temp").value,
    top_p: +$("#mf-topp").value,
    max_tokens: +$("#mf-maxtok").value,
    multimodal: +$("#mf-multi").value === 1,
  };
  if (!body.name) return alert("请填写模型名称");
  try {
    if (editingModelId) await api(`/api/models/${editingModelId}`, { method: "PUT", body: JSON.stringify(body) });
    else await api(`/api/providers/${modelProviderId}/models`, { method: "POST", body: JSON.stringify(body) });
    $("#dlg-model").close();
    loadProvidersAdmin();
  } catch (e) { alert("保存失败: " + e.message); }
};

window.delModel = async (id) => {
  if (!confirm("确认删除该模型？")) return;
  await api(`/api/models/${id}`, { method: "DELETE" });
  loadProvidersAdmin();
};

init().catch((e) => alert("初始化失败: " + e.message));
