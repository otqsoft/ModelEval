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
    `<option value="${p.id}">${p.label}${p.has_key ? " ✔" : ""}</option>`).join("");
  onProviderChange();
}

function curProvider() { return providers.find((p) => p.id === $("#f-provider").value); }

function onProviderChange() {
  const p = curProvider();
  if (!p) return;
  $("#f-model").value = p.default_model || "";
  $("#model-list").innerHTML = p.models.map((m) => `<option value="${m}">`).join("");
  $("#f-baseurl").value = p.override_base_url || "";
  $("#key-hint").textContent = p.has_key ? `已保存（尾号 ${p.key_hint}）` : (p.id === "mock" ? "Mock 无需 Key" : "未保存 Key");
  $("#key-group").style.display = p.id === "mock" ? "none" : "";
  $("#provider-hint").textContent = p.base_url ? `默认端点：${p.base_url}` : "请在下方填写 Base URL";
}

function bindForm() {
  $("#f-provider").onchange = onProviderChange;
  $("#btn-save-key").onclick = async () => {
    const p = curProvider();
    await api(`/api/keys/${p.id}`, { method: "PUT", body: JSON.stringify({
      api_key: $("#f-apikey").value.trim(), base_url: $("#f-baseurl").value.trim() }) });
    $("#f-apikey").value = "";
    providers = await api("/api/providers");
    renderProviders();
  };

  $("#btn-start").onclick = () => startRun(false);
  $("#btn-debug").onclick = () => startRun(true);
  $("#btn-stop").onclick = () => currentRun && api(`/api/runs/${currentRun}/stop`, { method: "POST" });
  $("#btn-save-template").onclick = saveTemplate;
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
  const name = $("#f-template-name").value.trim();
  if (!name) return alert("请填写模板名称");
  await api("/api/configs", { method: "POST", body: JSON.stringify({ name, config: collectConfig() }) });
  $("#f-template-name").value = "";
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
async function loadRuns() {
  const runs = await api("/api/runs");
  const tb = $("#runs-table tbody");
  tb.innerHTML = runs.map((r) => {
    let rate = "-";
    try { if (r.summary) { const g = JSON.parse(r.summary).generic; rate = `${g.solved}/${g.total}`; } } catch {}
    return `<tr>
      <td>${r.id}</td><td>${esc(r.task)}</td><td class="link" onclick="openReport(${r.id})">${esc(r.provider)}/${esc(r.model)}</td>
      <td>${r.n_cases}</td><td>${badge(r.status)}</td><td>${rate}</td>
      <td>${esc(r.created_at)}</td>
      <td><span class="link" onclick="openReport(${r.id})">报告</span></td></tr>`;
  }).join("");
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

init().catch((e) => alert("初始化失败: " + e.message));
