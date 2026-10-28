"""FastAPI 服务：REST API + SSE 实时事件流 + 静态页面托管。"""
import asyncio
import json
import random
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db, report
from .engine import engine as eval_engine
from .config import DEFAULT_LIMITS, DEFAULT_MODEL_PARAMS, PROVIDERS, RUN_TERMINAL_STATUS, TASKS

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"
app = FastAPI(title="ModelEval 模型评测平台")


# ---------- 提供商 / 密钥 ----------

@app.get("/api/providers")
def list_providers():
    keys = {r["provider"]: r for r in db.query("SELECT * FROM api_keys")}
    out = []
    for pid, p in PROVIDERS.items():
        out.append({
            "id": pid, "label": p["label"], "base_url": p["base_url"],
            "models": p["models"], "default_model": p["model"],
            "has_key": pid in keys,
            "key_hint": (keys[pid]["api_key"][-4:] if pid in keys else None),
            "override_base_url": keys.get(pid, {}).get("base_url") or None,
        })
    return out


@app.put("/api/keys/{provider}")
async def save_key(provider: str, request: Request):
    body = await request.json()
    if provider not in PROVIDERS:
        raise HTTPException(400, "未知提供商")
    db.execute(
        "INSERT INTO api_keys(provider, api_key, base_url, updated_at) VALUES(?,?,?,?)"
        " ON CONFLICT(provider) DO UPDATE SET api_key=excluded.api_key,"
        " base_url=excluded.base_url, updated_at=excluded.updated_at",
        (provider, body.get("api_key", ""), body.get("base_url") or None, db.now()),
    )
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
    if provider not in PROVIDERS:
        raise HTTPException(400, f"未知提供商: {provider}")
    model = (body.get("model") or "").strip() or PROVIDERS[provider]["model"]
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


# ---------- 静态页面 ----------

@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/report")
def report_page():
    return FileResponse(STATIC_DIR / "report.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
