/* ModelEval 报告页逻辑 */
document.documentElement.dataset.theme = localStorage.getItem("modeleval-theme") || "dark";  // 与控制台主题保持一致
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const stopLabel = (r) => ({ solved: "任务成功", max_steps: "步数超限", max_calls: "调用超限", max_tokens: "token超限",
  api_error: "API异常", invalid_output: "无效输出", stopped: "人工停止" }[r] || (r || "-"));

async function api(url) { const r = await fetch(url); if (!r.ok) throw new Error(await r.text()); return r.json(); }

const runId = new URLSearchParams(location.search).get("run_id");

async function init() {
  if (!runId) { $("#r-title").textContent = "缺少 run_id 参数"; return; }
  const data = await api(`/api/runs/${runId}/report`);
  const s = data.summary, g = s.generic;
  $("#r-title").innerHTML =
    `Run #${s.run_id} · ${esc(s.task)} · <code>${esc(s.provider)}/${esc(s.model)}</code>` +
    ` <span class="badge ${data.run.status}">${{ running: "运行中", finished: "已完成", stopped: "已停止", error: "异常" }[data.run.status] || data.run.status}</span>`;
  ["md", "xlsx", "jsonl"].forEach((k) => {
    $("#exp-" + k).href = `/api/runs/${runId}/export/${k === "md" ? "markdown" : k}`;
  });

  $("#cards").innerHTML = [
    card("成功率", `${g.solved}/${g.total}`, `${(g.success_rate * 100).toFixed(1)}%`),
    card("平均调用次数", g.avg_calls),
    card("平均步数", g.avg_moves),
    card("token 合计", g.tokens.total, `↑${g.tokens.prompt} ↓${g.tokens.completion} 思考${g.tokens.reasoning}`),
    card("评测费用", "¥" + (+g.cost?.total || 0).toFixed(4),
      g.cost?.has_price ? `输入 ¥${(+g.cost.input).toFixed(4)} · 输出 ¥${(+g.cost.output).toFixed(4)}` : "模型未配置价格"),
    card("平均单样例 token", g.avg_tokens_per_case),
    card("平均调用耗时", g.avg_latency_ms + "ms"),
  ].join("");

  const dist = Object.entries(s.stop_distribution || {});
  const maxV = Math.max(1, ...dist.map(([, v]) => v));
  $("#stop-dist").innerHTML = dist.map(([k, v]) =>
    `<div class="bar-row"><span style="width:110px">${stopLabel(k)}</span>
     <div class="bar" style="width:${v / maxV * 60}%"><i></i></div><b>${v}</b></div>`).join("") || "-";

  const tm = Object.entries(s.task_metrics || {});
  $("#task-metrics").innerHTML = tm.length
    ? tm.map(([k, v]) => `<div class="bar-row"><span style="width:130px">${esc(k)}</span><b>${v}</b></div>`).join("")
    : "-";
  if (!tm.length) $("#task-metrics-box").style.opacity = .5;

  const tb = $("#cases-table tbody");
  tb.innerHTML = data.cases.map((c) => `<tr>
    <td>${c.idx}</td><td><code>${esc(c.scramble)}</code></td>
    <td>${c.status === "success" ? '<span style="color:var(--ok)">✅ 成功</span>' : '<span style="color:var(--bad)">❌ 失败</span>'}</td>
    <td>${stopLabel(c.stop_reason)}</td><td>${c.moves_used}</td><td>${c.calls}</td>
    <td>${c.prompt_tokens}/${c.completion_tokens}/${c.reasoning_tokens}</td>
    <td>${+(c.cost || 0).toFixed(4)}</td><td>${c.latency_ms}ms</td>
    <td><span class="link" onclick="toggleCase(${c.id}, this)">交互明细</span></td></tr>`).join("");

  // A/B 对比下拉
  const runs = await api("/api/runs");
  $("#r-compare").innerHTML = '<option value="">选择对比 Run</option>' +
    runs.filter((r) => r.id !== +runId && r.summary)
      .map((r) => `<option value="${r.id}">#${r.id} ${esc(r.provider)}/${esc(r.model)} ${esc(r.task)}</option>`).join("");
  $("#r-compare").onchange = renderCompare;
}

function card(k, v, sub) {
  return `<div class="card"><div class="v">${esc(v)}</div><div class="k">${esc(k)}${sub ? " · " + esc(sub) : ""}</div></div>`;
}

async function renderCompare() {
  const idB = $("#r-compare").value;
  if (!idB) { $("#compare-box").classList.add("hidden"); return; }
  const a = (await api(`/api/runs/${runId}/report`)).summary;
  const b = (await api(`/api/runs/${idB}/report`)).summary;
  const rows = [
    ["模型", `${a.provider}/${a.model}`, `${b.provider}/${b.model}`],
    ["成功率", pct(a.generic.success_rate), pct(b.generic.success_rate)],
    ["平均调用次数", a.generic.avg_calls, b.generic.avg_calls],
    ["平均步数", a.generic.avg_moves, b.generic.avg_moves],
    ["token 合计", a.generic.tokens.total, b.generic.tokens.total],
    ["其中思考 token", a.generic.tokens.reasoning, b.generic.tokens.reasoning],
    ["评测费用", "¥" + (+a.generic.cost?.total || 0).toFixed(4), "¥" + (+b.generic.cost?.total || 0).toFixed(4)],
    ["平均单样例耗时", a.generic.avg_latency_ms + "ms", b.generic.avg_latency_ms + "ms"],
  ];
  $("#compare-box").classList.remove("hidden");
  $("#compare-body").innerHTML = `<table class="tbl"><thead><tr><th>指标</th>
    <th>A: Run#${a.run_id}</th><th>B: Run#${b.run_id}</th></tr></thead><tbody>` +
    rows.map((r) => `<tr><td>${r[0]}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td></tr>`).join("") + "</tbody></table>";
}
const pct = (x) => (x * 100).toFixed(1) + "%";

window.toggleCase = async function (caseId, el) {
  const exist = $("#detail-" + caseId);
  if (exist) { exist.remove(); return; }
  el.textContent = "加载中…";
  const items = await api(`/api/runs/${runId}/interactions?case_id=${caseId}`);
  const box = document.createElement("div");
  box.id = "detail-" + caseId;
  box.style.cssText = "margin-top:8px;display:flex;flex-direction:column;gap:6px";
  box.innerHTML = items.map((it) => {
    const req = JSON.parse(it.request || "null"), resp = JSON.parse(it.response || "null");
    if (it.kind === "llm") {
      const u = resp?.usage || {};
      return `<div class="icard llm"><div class="ihead"><b>LLM 调用 #${it.seq}</b><span>${it.latency_ms}ms</span>
        <span>token ↑${u.prompt_tokens ?? "-"} ↓${u.completion_tokens ?? "-"}${u.reasoning_tokens ? "（思考" + u.reasoning_tokens + "）" : ""}</span></div>
        <details><summary>请求 messages</summary><pre>${esc(JSON.stringify(req, null, 1))}</pre></details>
        <details><summary>思考链</summary><pre class="reasoning">${esc(resp?.reasoning)}</pre></details>
        <details open><summary>回复内容</summary><pre>${esc(resp?.content)}</pre></details>
        ${resp?.tool_calls?.length ? `<details open><summary>工具调用</summary><pre>${esc(JSON.stringify(resp.tool_calls, null, 1))}</pre></details>` : ""}
        <details><summary>原始响应报文</summary><pre>${esc(JSON.stringify(resp?.raw, null, 1))}</pre></details></div>`;
    }
    return `<div class="icard tool"><div class="ihead"><b>工具调用 #${it.seq}</b></div>
      <pre>${esc(JSON.stringify({ 入参: req, 返回: resp }, null, 1))}</pre></div>`;
  }).join("");
  el.textContent = "收起明细";
  $("#case-detail").appendChild(box);
};

init().catch((e) => { $("#r-title").textContent = "加载失败: " + e.message; });
