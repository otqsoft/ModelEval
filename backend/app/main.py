"""FastAPI 服务：REST API + SSE 实时事件流 + 静态页面托管。"""
import asyncio
import json
import queue
import random
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db, report
from .engine import engine as eval_engine
from .gateway import AnthropicGateway, GatewayError, OpenAICompatGateway
from .config import DEFAULT_LIMITS, DEFAULT_MODEL_PARAMS, RUN_TERMINAL_STATUS, TASKS

# 活跃会话：cid -> {"stop": threading.Event}，用于中断流式回复生成
CHAT_SESSIONS = {}


class ChatStopped(Exception):
    """用户主动终止会话回复（经 on_event 抛出，穿透流式循环）。"""

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
app = FastAPI(title="ModelEval 模型评测平台")


@app.middleware("http")
async def no_cache_for_dev(request: Request, call_next):
    """开发工具场景：页面与静态资源禁用缓存，避免改版后浏览器新旧文件混搭。"""
    resp = await call_next(request)
    if request.url.path in ("/", "/report") or request.url.path.startswith("/static") \
            or request.url.path.startswith("/public"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


# ---------- 提供商 / 密钥 ----------

# ---------- 供应商 / 模型管理 ----------

def _serialize_model(m: dict) -> dict:
    return {
        "id": m["id"], "provider_id": m["provider_id"], "name": m["name"],
        "cache_hit_input_price": m["cache_hit_input_price"],
        "cache_miss_input_price": m["cache_miss_input_price"],
        "output_price": m["output_price"],
        "context_length": m["context_length"],
        "temperature": m["temperature"], "top_p": m["top_p"], "max_tokens": m["max_tokens"],
        "multimodal": bool(m["multimodal"]),
    }


@app.get("/api/providers")
def list_providers():
    rows = db.query("SELECT * FROM providers ORDER BY id")
    out = []
    for p in rows:
        models = db.query("SELECT * FROM models WHERE provider_id=? ORDER BY id", (p["id"],))
        out.append({
            "id": p["id"], "name": p["name"], "protocol": p["protocol"],
            "base_url": p["base_url"], "has_key": bool(p["api_key"]),
            "key_hint": (p["api_key"][-4:] if p["api_key"] else None),
            "models": [_serialize_model(m) for m in models],
        })
    return out


@app.post("/api/providers")
async def create_provider(request: Request):
    body = await request.json()
    pid = (body.get("id") or "").strip()
    name = (body.get("name") or "").strip()
    if not pid or not name:
        raise HTTPException(400, "id 与 name 必填")
    if db.query_one("SELECT id FROM providers WHERE id=?", (pid,)):
        raise HTTPException(400, f"供应商 id 已存在: {pid}")
    ts = db.now()
    db.execute(
        "INSERT INTO providers(id, name, protocol, base_url, api_key, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (pid, name, body.get("protocol") or "openai", body.get("base_url") or None,
         body.get("api_key") or None, ts, ts),
    )
    return {"ok": True, "id": pid}


@app.put("/api/providers/{pid}")
async def update_provider(pid: str, request: Request):
    row = db.query_one("SELECT * FROM providers WHERE id=?", (pid,))
    if not row:
        raise HTTPException(404, "供应商不存在")
    body = await request.json()
    db.execute(
        "UPDATE providers SET name=?, protocol=?, base_url=?, api_key=?, updated_at=? WHERE id=?",
        (body.get("name", row["name"]), body.get("protocol", row["protocol"]) or "openai",
         body.get("base_url", row["base_url"]) or None, body.get("api_key", row["api_key"]),
         db.now(), pid),
    )
    return {"ok": True}


@app.delete("/api/providers/{pid}")
def delete_provider(pid: str):
    db.execute("DELETE FROM models WHERE provider_id=?", (pid,))
    db.execute("DELETE FROM providers WHERE id=?", (pid,))
    return {"ok": True}


@app.get("/api/providers/{pid}/models")
def list_models(pid: str):
    return [_serialize_model(m) for m in db.query("SELECT * FROM models WHERE provider_id=? ORDER BY id", (pid,))]


@app.post("/api/providers/{pid}/models")
async def create_model(pid: str, request: Request):
    if not db.query_one("SELECT id FROM providers WHERE id=?", (pid,)):
        raise HTTPException(404, "供应商不存在")
    b = await request.json()
    name = (b.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "模型名称必填")
    ts = db.now()
    mid = db.execute(
        "INSERT INTO models(provider_id, name, cache_hit_input_price, cache_miss_input_price, "
        "output_price, context_length, temperature, top_p, max_tokens, multimodal, created_at, updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, name, float(b.get("cache_hit_input_price") or 0), float(b.get("cache_miss_input_price") or 0),
         float(b.get("output_price") or 0), int(b.get("context_length") or 1000000),
         float(b.get("temperature", 0.7)), float(b.get("top_p", 1.0)), int(b.get("max_tokens", 4096)),
         int(bool(b.get("multimodal"))), ts, ts),
    )
    return {"ok": True, "id": mid}


@app.put("/api/models/{mid}")
async def update_model(mid: int, request: Request):
    row = db.query_one("SELECT * FROM models WHERE id=?", (mid,))
    if not row:
        raise HTTPException(404, "模型不存在")
    b = await request.json()
    db.execute(
        "UPDATE models SET name=?, cache_hit_input_price=?, cache_miss_input_price=?, output_price=?, "
        "context_length=?, temperature=?, top_p=?, max_tokens=?, multimodal=?, updated_at=? WHERE id=?",
        (b.get("name", row["name"]), float(b.get("cache_hit_input_price", row["cache_hit_input_price"]) or 0),
         float(b.get("cache_miss_input_price", row["cache_miss_input_price"]) or 0),
         float(b.get("output_price", row["output_price"]) or 0),
         int(b.get("context_length", row["context_length"]) or 1000000),
         float(b.get("temperature", row["temperature"])), float(b.get("top_p", row["top_p"])),
         int(b.get("max_tokens", row["max_tokens"])), int(bool(b.get("multimodal", row["multimodal"]))),
         db.now(), mid),
    )
    return {"ok": True}


@app.delete("/api/models/{mid}")
def delete_model(mid: int):
    db.execute("DELETE FROM models WHERE id=?", (mid,))
    return {"ok": True}


# ---------- 任务 ----------

@app.get("/api/tasks")
def list_tasks():
    return [{"id": k, "label": v["label"], "params": v["params"]} for k, v in TASKS.items()]


# ---------- 配置模板 ----------

@app.get("/api/configs")
def list_configs():
    rows = db.query("SELECT * FROM config_templates ORDER BY id DESC")
    for r in rows:
        r["config"] = json.loads(r["config"])
    return rows


@app.post("/api/configs")
async def save_config(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    config = body.get("config")
    if not name or not config:
        raise HTTPException(400, "缺少 name 或 config")
    existing = db.query_one("SELECT id FROM config_templates WHERE name=?", (name,))
    if existing:
        db.execute("UPDATE config_templates SET config=?, updated_at=? WHERE id=?",
                   (json.dumps(config, ensure_ascii=False), db.now(), existing["id"]))
        return {"ok": True, "id": existing["id"]}
    cid = db.execute("INSERT INTO config_templates(name, config, created_at, updated_at) VALUES(?,?,?,?)",
                     (name, json.dumps(config, ensure_ascii=False), db.now(), db.now()))
    return {"ok": True, "id": cid}


@app.delete("/api/configs/{cid}")
def delete_config(cid: int):
    db.execute("DELETE FROM config_templates WHERE id=?", (cid,))
    return {"ok": True}


# ---------- 运行 ----------

def _sanitize(body: dict) -> dict:
    task = body.get("task", "cube")
    if task not in TASKS:
        raise HTTPException(400, f"未知任务: {task}")
    provider = body.get("provider", "")
    if not db.query_one("SELECT id FROM providers WHERE id=?", (provider,)):
        raise HTTPException(400, f"未知提供商: {provider}")
    model = (body.get("model") or "").strip()
    if not model:
        first = db.query_one("SELECT name FROM models WHERE provider_id=? ORDER BY id LIMIT 1", (provider,))
        model = first["name"] if first else ""
    if not model:
        raise HTTPException(400, "缺少模型名称")
    mp = {**DEFAULT_MODEL_PARAMS, **(body.get("model_params") or {})}
    limits = {**DEFAULT_LIMITS, **(body.get("limits") or {})}
    tp = {**TASKS[task]["params"], **(body.get("task_params") or {})}
    try:
        n_cases = max(1, min(100, int(body.get("n_cases", 1))))
        seed = int(body.get("seed") or random.randint(1, 999999))
        mp["temperature"] = float(mp["temperature"])
        mp["top_p"] = float(mp["top_p"])
        mp["max_tokens"] = int(mp["max_tokens"])
        limits["max_calls"] = int(limits["max_calls"])
        limits["max_total_tokens"] = int(limits["max_total_tokens"])
        limits["tool_enabled"] = bool(limits["tool_enabled"])
        limits["api_timeout"] = float(limits["api_timeout"])
        limits["retry"] = int(limits["retry"])
        limits["min_call_interval"] = float(limits.get("min_call_interval", 0))
        tp["max_steps"] = int(tp["max_steps"])
        tp["scramble_length"] = int(tp["scramble_length"])
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"参数类型错误: {e}")
    return {"task": task, "provider": provider, "model": model,
            "base_url": (body.get("base_url") or "").strip() or None,
            "model_params": mp, "limits": limits, "task_params": tp,
            "n_cases": n_cases, "seed": seed}


@app.post("/api/runs")
async def start_run(request: Request):
    cfg = _sanitize(await request.json())
    run_id = eval_engine.start_run(cfg)
    return {"ok": True, "run_id": run_id}


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: int):
    eval_engine.stop(run_id)
    return {"ok": True}


@app.get("/api/runs")
def list_runs():
    return db.query("SELECT id, task, provider, model, n_cases, seed, status, summary, error, created_at, finished_at"
                    " FROM runs ORDER BY id DESC LIMIT 200")


@app.get("/api/runs/{run_id}")
def get_run(run_id: int):
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not run:
        raise HTTPException(404, "运行不存在")
    cases = db.query("SELECT * FROM cases WHERE run_id=? ORDER BY idx", (run_id,))
    return {"run": run, "cases": cases}


@app.get("/api/runs/{run_id}/interactions")
def get_interactions(run_id: int, case_id: int = None):
    if case_id:
        rows = db.query("SELECT id, run_id, case_id, seq, kind, request, response, latency_ms, created_at"
                        " FROM interactions WHERE run_id=? AND case_id=? ORDER BY id", (run_id, case_id))
    else:
        rows = db.query("SELECT id, run_id, case_id, seq, kind, request, response, latency_ms, created_at"
                        " FROM interactions WHERE run_id=? ORDER BY id", (run_id,))
    return rows


@app.get("/api/runs/{run_id}/events")
def get_events(run_id: int, after: int = 0):
    rows = db.query("SELECT seq, case_id, type, payload, created_at FROM events"
                    " WHERE run_id=? AND seq>? ORDER BY seq", (run_id, after))
    for r in rows:
        r["payload"] = json.loads(r["payload"])
    return rows


@app.get("/api/runs/{run_id}/stream")
async def stream_run(run_id: int, after: int = 0):
    rs = eval_engine.state(run_id)
    run = db.query_one("SELECT status FROM runs WHERE id=?", (run_id,))
    if not run:
        raise HTTPException(404, "运行不存在")

    async def gen():
        if rs is None:
            # 服务重启后无内存流：回退为持久化事件一次性输出
            for ev in get_events(run_id, after):
                yield f"data: {json.dumps({'seq': ev['seq'], 'type': ev['type'], 'case_id': ev['case_id'], 'payload': ev['payload'], 'ts': ev['created_at']}, ensure_ascii=False)}\n\n"
            yield "data: {\"type\": \"run_done\"}\n\n"
            return
        idx = after
        while True:
            evs = rs.snapshot_from(idx)
            for ev in evs:
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                idx = ev["seq"]
            cur = db.query_one("SELECT status FROM runs WHERE id=?", (run_id,))
            if not evs and cur and cur["status"] in RUN_TERMINAL_STATUS:
                yield "data: {\"type\": \"run_done\"}\n\n"
                return
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- 报告与导出 ----------

@app.get("/api/runs/{run_id}/report")
def get_report(run_id: int):
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    if not run:
        raise HTTPException(404, "运行不存在")
    cases = db.query("SELECT * FROM cases WHERE run_id=? ORDER BY idx", (run_id,))
    return {"run": run, "summary": json.loads(run["summary"]) if run["summary"] else report.build_summary(run, cases),
            "cases": cases}


@app.get("/api/runs/{run_id}/export/markdown")
def export_markdown(run_id: int):
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    cases = db.query("SELECT * FROM cases WHERE run_id=? ORDER BY idx", (run_id,))
    return PlainTextResponse(report.build_markdown(run, cases), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f"attachment; filename=report_run{run_id}.md"})


@app.get("/api/runs/{run_id}/export/excel")
def export_excel(run_id: int):
    run = db.query_one("SELECT * FROM runs WHERE id=?", (run_id,))
    cases = db.query("SELECT * FROM cases WHERE run_id=? ORDER BY idx", (run_id,))
    interactions = {}
    for it in db.query("SELECT * FROM interactions WHERE run_id=? ORDER BY id", (run_id,)):
        interactions.setdefault(it["case_id"], []).append(it)
    import os
    path = os.path.join(db.DATA_DIR, f"report_run{run_id}.xlsx")
    report.build_excel(run, cases, path)
    return FileResponse(path, filename=f"report_run{run_id}.xlsx")


@app.get("/api/runs/{run_id}/export/jsonl")
def export_jsonl(run_id: int):
    lines = []
    for it in db.query("SELECT * FROM interactions WHERE run_id=? ORDER BY id", (run_id,)):
        lines.append(json.dumps(it, ensure_ascii=False))
    return PlainTextResponse("\n".join(lines), media_type="application/x-ndjson; charset=utf-8",
                             headers={"Content-Disposition": f"attachment; filename=run{run_id}_interactions.jsonl"})


# ---------- 模型排行 ----------

@app.get("/api/leaderboard")
def model_leaderboard():
    """按 (provider, model) 聚合全部已完成评测的 summary，输出多维度排行。"""
    runs = db.query("SELECT provider, model, summary FROM runs"
                    " WHERE status IN ('finished','stopped') AND summary IS NOT NULL")
    groups = {}
    for r in runs:
        try:
            s = json.loads(r["summary"])
        except Exception:
            continue
        g = s.get("generic") or {}
        if not g.get("total"):
            continue
        key = (r["provider"], r["model"])
        a = groups.setdefault(key, {"runs": 0, "cases": 0, "solved": 0, "calls": 0,
                                    "moves": 0, "tokens": 0, "latency": 0, "cost": 0.0})
        a["runs"] += 1
        a["cases"] += g["total"]
        a["solved"] += g.get("solved", 0)
        a["calls"] += g.get("total_calls", 0)
        a["moves"] += (s.get("task_metrics") or {}).get("moves_used", 0) * g["total"]
        t = g.get("tokens") or {}
        a["tokens"] += t.get("total", 0)
        a["latency"] += g.get("avg_latency_ms", 0) * g["total"]
        a["cost"] += (g.get("cost") or {}).get("total", 0) or 0

    rows = []
    for (provider, model), a in groups.items():
        cases = a["cases"] or 1
        rows.append({
            "provider": provider, "model": model, "runs": a["runs"], "cases": a["cases"],
            "success_rate": round(a["solved"] / cases, 4),
            "avg_calls": round(a["calls"] / cases, 2),
            "avg_moves": round(a["moves"] / cases, 1),
            "avg_tokens": round(a["tokens"] / cases, 1),
            "avg_latency_ms": round(a["latency"] / cases, 1),
            "avg_cost": round(a["cost"] / cases, 6),
        })

    def best(key):
        vals = [x[key] for x in rows if x[key] > 0]
        return min(vals) if vals else None

    b_calls, b_tokens = best("avg_calls"), best("avg_tokens")
    b_lat, b_cost = best("avg_latency_ms"), best("avg_cost")
    for r in rows:
        comp = lambda v, b: round(b / v * 10, 1) if (b and v > 0) else 0.0
        r["sr_score"] = round(r["success_rate"] * 60, 1)
        r["calls_score"] = comp(r["avg_calls"], b_calls)
        r["token_score"] = comp(r["avg_tokens"], b_tokens)
        r["speed_score"] = comp(r["avg_latency_ms"], b_lat)
        r["cost_score"] = comp(r["avg_cost"], b_cost)
        r["score"] = round(r["sr_score"] + r["calls_score"] + r["token_score"]
                           + r["speed_score"] + r["cost_score"], 1)
    rows.sort(key=lambda x: -x["score"])
    return rows


# ---------- 模型会话（右侧抽屉，流式输出） ----------

@app.post("/api/chat")
async def chat(request: Request):
    """模型会话：SSE 流式转发。复用网关层（openai/anthropic 协议），仅允许已配置 API Key 的供应商。

    事件：chat_delta {field: content|reasoning, text} → chat_done {usage, latency_ms, finish_reason}
          → chat_error {error} → chat_close。
    """
    body = await request.json()
    pid = (body.get("provider") or "").strip()
    row = db.query_one("SELECT * FROM providers WHERE id=?", (pid,))
    if not row:
        raise HTTPException(400, f"未知提供商: {pid}")
    if not row["api_key"]:
        raise HTTPException(400, f"提供商 {pid} 未配置 API Key")
    model = (body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "缺少模型名称")
    messages = body.get("messages") or []
    if not messages:
        raise HTTPException(400, "缺少会话消息")

    # 参数默认取该模型配置，允许请求体覆盖
    mrow = db.query_one(
        "SELECT temperature, top_p, max_tokens FROM models WHERE provider_id=? AND name=?", (pid, model))
    mp = dict(DEFAULT_MODEL_PARAMS)
    if mrow:
        mp = {"temperature": mrow["temperature"], "top_p": mrow["top_p"], "max_tokens": mrow["max_tokens"]}
    mp.update(body.get("model_params") or {})

    cid = body.get("chat_id") or uuid.uuid4().hex
    ses = CHAT_SESSIONS[cid] = {"stop": threading.Event()}
    q: queue.Queue = queue.Queue()

    def on_event(t, p):
        if ses["stop"].is_set():
            raise ChatStopped()   # 在流式循环内抛出，立即中断后续生成
        q.put({"type": t, "payload": p})

    common = dict(provider=pid, model=model, api_key=row["api_key"],
                  base_url=row["base_url"] or None, model_params=mp,
                  on_event=on_event, timeout=120, retry=1)
    gw = AnthropicGateway(**common) if (row["protocol"] or "openai") == "anthropic" else OpenAICompatGateway(**common)

    def run():
        try:
            resp = gw.chat(messages)
            q.put({"type": "chat_done", "payload": {
                "content": resp.get("content"), "usage": resp.get("usage"),
                "latency_ms": resp.get("latency_ms"), "finish_reason": resp.get("finish_reason")}})
        except GatewayError as e:
            if isinstance(getattr(e, "__cause__", None), ChatStopped):
                q.put({"type": "chat_stopped", "payload": {}})
            else:
                q.put({"type": "chat_error", "payload": {"error": str(e)}})
        except Exception as e:
            q.put({"type": "chat_error", "payload": {"error": f"{type(e).__name__}: {e}"}})
        q.put(None)   # 结束哨兵
        CHAT_SESSIONS.pop(cid, None)

    threading.Thread(target=run, daemon=True).start()

    def gen():
        while True:
            ev = q.get()
            if ev is None:
                break
            t = ev["type"]
            if t == "llm_delta":
                ev["type"] = "chat_delta"
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            elif t in ("chat_done", "chat_error", "chat_stopped"):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        yield 'data: {"type": "chat_close"}\n\n'

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/chat/stop/{cid}")
def stop_chat(cid: str):
    """终止指定会话的流式回复生成。"""
    ses = CHAT_SESSIONS.get(cid)
    if not ses:
        return {"ok": False, "detail": "会话不存在或已结束"}
    ses["stop"].set()
    return {"ok": True}


# ---------- 静态页面 ----------

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/report")
def report_page():
    return FileResponse(STATIC_DIR / "report.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/public", StaticFiles(directory=str(STATIC_DIR / "public")), name="public")
