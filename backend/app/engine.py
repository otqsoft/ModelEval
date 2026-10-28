"""评测引擎：任务编排、终止判定、全量采集、实时事件流（内存流 + 持久化）。"""
import json
import threading
import time
import traceback
from collections import deque

from . import db, gateway, report
from .config import DEFAULT_LIMITS, DEFAULT_MODEL_PARAMS, TASKS, RUN_TERMINAL_STATUS
from .tasks import create_task, TaskInvalidError

MAX_STREAM_EVENTS = 8000  # 内存事件流上限（超出后客户端需走 REST 回放）


class RunState:
    """一次运行在内存中的实时状态（事件流 + 停止标记）。"""

    def __init__(self):
        self.events = deque(maxlen=MAX_STREAM_EVENTS)
        self.seq = 0
        self.stop = False
        self.lock = threading.Lock()

    def snapshot_from(self, after: int):
        with self.lock:
            return [e for e in self.events if e["seq"] > after]

    def first_seq(self) -> int:
        with self.lock:
            return self.events[0]["seq"] if self.events else 0


class Engine:
    def __init__(self):
        self._runs = {}  # run_id -> RunState

    def state(self, run_id: int):
        return self._runs.get(run_id)

    def stop(self, run_id: int):
        rs = self._runs.get(run_id)
        if rs:
            rs.stop = True

    # ---------- 事件 ----------

    def _emit(self, rs, run_id, ev_type, payload, case_id=None, persist=True):
        with rs.lock:
            rs.seq += 1
            ev = {"seq": rs.seq, "run_id": run_id, "case_id": case_id,
                  "type": ev_type, "payload": payload, "ts": db.now()}
        rs.events.append(ev)
        if persist:
            db.execute(
                "INSERT INTO events(run_id, seq, case_id, type, payload, created_at) VALUES(?,?,?,?,?,?)",
                (run_id, ev["seq"], case_id, ev_type, json.dumps(payload, ensure_ascii=False), ev["ts"]),
            )
        return ev

    # ---------- 运行入口 ----------

    def start_run(self, cfg: dict) -> int:
        run_id = db.execute(
            "INSERT INTO runs(task, provider, model, config_snapshot, n_cases, seed, status, created_at)"
            " VALUES(?,?,?,?,?,?, 'running', ?)",
            (cfg["task"], cfg["provider"], cfg["model"], json.dumps(cfg, ensure_ascii=False),
             cfg["n_cases"], cfg["seed"], db.now()),
        )
        rs = RunState()
        self._runs[run_id] = rs
        threading.Thread(target=self._worker, args=(run_id, cfg), daemon=True).start()
        return run_id

    def _worker(self, run_id: int, cfg: dict):
        rs = self._runs[run_id]
        try:
            self._emit(rs, run_id, "run_started", {
                "task": cfg["task"], "provider": cfg["provider"], "model": cfg["model"],
                "n_cases": cfg["n_cases"], "seed": cfg["seed"],
            })
            task = create_task(cfg["task"])
            statuses = []
            for idx in range(cfg["n_cases"]):
                if rs.stop:
                    break
                statuses.append(self._run_case(rs, run_id, task, cfg, idx))
            solved = sum(1 for s in statuses if s == "success")
            total = len(statuses) or 1
            cases = db.query("SELECT * FROM cases WHERE run_id=?", (run_id,))
            summary = report.build_summary(db.query_one("SELECT * FROM runs WHERE id=?", (run_id,)), cases)
            status = "stopped" if rs.stop else "finished"
            db.execute("UPDATE runs SET status=?, summary=?, finished_at=? WHERE id=?",
                       (status, json.dumps(summary, ensure_ascii=False), db.now(), run_id))
            self._emit(rs, run_id, "run_finished", {"status": status, "summary": summary})
        except Exception as e:
            traceback.print_exc()
            db.execute("UPDATE runs SET status='error', error=?, finished_at=? WHERE id=?",
                       (str(e), db.now(), run_id))
            self._emit(rs, run_id, "run_error", {"error": str(e)})

    # ---------- 单案例执行 ----------

    def _run_case(self, rs, run_id, task, cfg, idx) -> str:
        limits, tp = cfg["limits"], cfg["task_params"]
        case_data = task.create_case(idx, cfg["seed"], tp)
        case_id = db.execute(
            "INSERT INTO cases(run_id, idx, scramble, initial_state, created_at) VALUES(?,?,?,?,?)",
            (run_id, idx, case_data["scramble"], case_data["cube"].render(), db.now()),
        )
        self._emit(rs, run_id, "case_started", {
            "case_id": case_id, "idx": idx, "scramble": case_data["scramble"],
            "state": case_data["cube"].render(),
            "max_steps": tp["max_steps"], "optimal_len": case_data.get("optimal_len"),
        }, case_id=case_id)

        # 每次调用的实时事件回调（绑定 run/case 上下文）
        call_no = {"n": 0}

        def on_gw_event(ev_type, payload):
            if ev_type == "llm_start":
                call_no["n"] += 1
                payload["call_seq"] = call_no["n"]
            payload.update({"case_id": case_id, "call_seq": call_no["n"]})
            self._emit(rs, run_id, ev_type, payload, case_id=case_id, persist=False)

        try:
            gw = gateway.build_gateway(cfg, solution=task.solution_for(case_data), on_event=on_gw_event)
        except gateway.GatewayError as e:
            self._finish_case(rs, run_id, case_id, case_data, task, {}, "api_error", False, str(e))
            return "failed"

        msgs = task.build_messages(case_data)
        tools = task.tools_spec() if limits["tool_enabled"] else None
        counters = {"calls": 0, "prompt": 0, "completion": 0, "reasoning": 0, "latency": 0,
                    "invalid_streak": 0, "seq": 0}
        stop_reason, solved, err_detail = None, False, None

        while True:
            if rs.stop:
                stop_reason = "stopped"
                break
            if counters["calls"] >= limits["max_calls"]:
                stop_reason = "max_calls"
                break
            if counters["prompt"] + counters["completion"] >= limits["max_total_tokens"]:
                stop_reason = "max_tokens"
                break
            if len(case_data["moves"]) >= tp["max_steps"]:
                stop_reason = "max_steps"
                break
            if limits.get("min_call_interval") and counters["calls"]:
                time.sleep(limits["min_call_interval"])

            # ---- 调用模型 ----
            try:
                resp = gw.chat(msgs, tools)
            except gateway.GatewayError as e:
                stop_reason, err_detail = "api_error", str(e)
                db.execute(
                    "INSERT INTO interactions(run_id, case_id, seq, kind, request, response, latency_ms, created_at)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    (run_id, case_id, counters["seq"] + 1, "llm", json.dumps(msgs, ensure_ascii=False),
                     json.dumps({"error": err_detail}, ensure_ascii=False), 0, db.now()),
                )
                self._emit(rs, run_id, "llm_error", {"case_id": case_id, "error": err_detail}, case_id=case_id)
                break

            counters["calls"] += 1
            counters["seq"] += 1
            u = resp.get("usage") or {}
            counters["prompt"] += u.get("prompt_tokens", 0)
            counters["completion"] += u.get("completion_tokens", 0)
            counters["reasoning"] += u.get("reasoning_tokens", 0)
            counters["latency"] += resp.get("latency_ms", 0)

            req_snapshot = json.dumps(msgs, ensure_ascii=False)
            resp_snapshot = json.dumps(resp, ensure_ascii=False)
            db.execute(
                "INSERT INTO interactions(run_id, case_id, seq, kind, request, response, latency_ms, created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (run_id, case_id, counters["seq"], "llm", req_snapshot, resp_snapshot,
                 resp.get("latency_ms", 0), db.now()),
            )
            self._emit(rs, run_id, "interaction", {
                "kind": "llm", "seq": counters["seq"], "case_id": case_id,
                "call_seq": call_no["n"], "content": resp["content"], "reasoning": resp["reasoning"],
                "tool_calls": resp["tool_calls"], "usage": u, "finish_reason": resp["finish_reason"],
                "latency_ms": resp["latency_ms"], "raw": resp["raw"],
            }, case_id=case_id, persist=False)

            # ---- 处理响应 ----
            if resp["tool_calls"]:
                msgs.append({
                    "role": "assistant",
                    "content": resp["content"] or None,
                    "tool_calls": [
                        {"id": tc.get("id") or f"call_{counters['seq']}_{i}", "type": "function",
                         "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                        for i, tc in enumerate(resp["tool_calls"])
                    ],
                })
                last_result = {}
                for i, tc in enumerate(resp["tool_calls"]):
                    try:
                        result = task.execute_tool(case_data, tc["name"], tc["arguments"])
                        counters["invalid_streak"] = 0
                    except TaskInvalidError as e:
                        result = {"ok": False, "error": str(e)}
                        counters["invalid_streak"] += 1
                    counters["seq"] += 1
                    db.execute(
                        "INSERT INTO interactions(run_id, case_id, seq, kind, request, response, latency_ms, created_at)"
                        " VALUES(?,?,?,?,?,?,?,?)",
                        (run_id, case_id, counters["seq"], "tool",
                         json.dumps(tc, ensure_ascii=False), json.dumps(result, ensure_ascii=False),
                         0, db.now()),
                    )
                    self._emit(rs, run_id, "interaction", {
                        "kind": "tool", "seq": counters["seq"], "case_id": case_id,
                        "tool_call": tc, "result": result,
                    }, case_id=case_id, persist=False)
                    msgs.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id") or f"call_{counters['seq']}_{i}",
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                    last_result = result

                if counters["invalid_streak"] >= 3:
                    stop_reason = "invalid_output"
                    break
                if len(case_data["moves"]) > tp["max_steps"]:
                    stop_reason = "max_steps"
                    break
                if last_result.get("solved") or task.is_solved(case_data):
                    solved = True
                    break
                continue

            # 无工具调用：处理文本回复
            outcome = task.handle_final_text(case_data, resp["content"])
            if outcome["status"] == "invalid":
                counters["invalid_streak"] += 1
                if counters["invalid_streak"] >= 2:
                    stop_reason = "invalid_output"
                    break
                msgs.append({"role": "assistant", "content": resp["content"]})
                msgs.append({"role": "user", "content": f"你的输出无法解析：{outcome.get('detail', '')}。请用规定格式重试。"})
                continue
            counters["invalid_streak"] = 0
            if outcome.get("moves"):
                self._emit(rs, run_id, "moves_applied",
                           {"case_id": case_id, "moves": outcome["moves"]}, case_id=case_id, persist=False)
            if outcome["status"] == "solved":
                if len(case_data["moves"]) > tp["max_steps"]:
                    stop_reason = "max_steps"
                    break
                solved = True
                break
            msgs.append({"role": "assistant", "content": resp["content"]})
            msgs.append({"role": "user", "content": outcome["feedback"]})
            if len(case_data["moves"]) >= tp["max_steps"]:
                stop_reason = "max_steps"
                break

        status = "success" if solved else "failed"
        self._finish_case(rs, run_id, case_id, case_data, task, counters,
                          "solved" if solved else stop_reason, solved, err_detail)
        return status

    def _finish_case(self, rs, run_id, case_id, case_data, task, counters, stop_reason, solved, err_detail=None):
        score = task.score(case_data, solved)
        db.execute(
            "UPDATE cases SET status=?, stop_reason=?, moves_used=?, calls=?, prompt_tokens=?,"
            " completion_tokens=?, reasoning_tokens=?, total_tokens=?, latency_ms=?, score=?, finished_at=?"
            " WHERE id=?",
            ("success" if solved else "failed", stop_reason,
             len(case_data["moves"]), counters.get("calls", 0), counters.get("prompt", 0),
             counters.get("completion", 0), counters.get("reasoning", 0),
             counters.get("prompt", 0) + counters.get("completion", 0), counters.get("latency", 0),
             json.dumps(score, ensure_ascii=False), db.now(), case_id),
        )
        self._emit(rs, run_id, "case_finished", {
            "case_id": case_id, "idx": case_data["idx"], "solved": solved,
            "stop_reason": stop_reason, "moves_used": len(case_data["moves"]),
            "moves": case_data["moves"], "score": score, "error": err_detail,
        }, case_id=case_id)


engine = Engine()
