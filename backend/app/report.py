"""评估报告：指标汇总（通用指标 + 任务自定义指标）、Markdown / Excel 导出。"""
import json
from collections import Counter

STOP_REASON_LABELS = {
    "solved": "任务成功（已还原）",
    "max_steps": "步数超限",
    "max_calls": "调用次数超限",
    "max_tokens": "token 超限",
    "api_error": "API 异常",
    "invalid_output": "无效输出",
    "stopped": "人工停止",
}


def build_summary(run: dict, cases: list) -> dict:
    total = len(cases)
    solved_cases = [c for c in cases if c["status"] == "success"]
    n_solved = len(solved_cases)

    agg = {k: sum(c[k] or 0 for c in cases)
           for k in ("calls", "prompt_tokens", "completion_tokens", "reasoning_tokens",
                     "total_tokens", "latency_ms", "moves_used")}
    stop_dist = Counter((c["stop_reason"] or "-") for c in cases)

    # 任务自定义指标聚合（从 score JSON 提取）
    task_metrics = {}
    scores = [json.loads(c["score"]) for c in cases if c["score"]]
    if scores:
        keys = set().union(*[s.keys() for s in scores])
        for k in keys:
            vals = [s[k] for s in scores if isinstance(s.get(k), (int, float))]
            if vals:
                task_metrics[k] = round(sum(vals) / len(vals), 3)

    return {
        "run_id": run["id"],
        "task": run["task"],
        "provider": run["provider"],
        "model": run["model"],
        "n_cases": run["n_cases"],
        "seed": run["seed"],
        "generic": {
            "total": total,
            "solved": n_solved,
            "success_rate": round(n_solved / total, 4) if total else 0,
            "total_calls": agg["calls"],
            "avg_calls": round(agg["calls"] / total, 2) if total else 0,
            "avg_moves": round(agg["moves_used"] / total, 2) if total else 0,
            "tokens": {
                "prompt": agg["prompt_tokens"],
                "completion": agg["completion_tokens"],
                "reasoning": agg["reasoning_tokens"],
                "total": agg["total_tokens"],
            },
            "avg_tokens_per_case": round(agg["total_tokens"] / total, 1) if total else 0,
            "avg_latency_ms": round(agg["latency_ms"] / total, 1) if total else 0,
        },
        "stop_distribution": dict(stop_dist),
        "task_metrics": task_metrics,
    }


def build_markdown(run: dict, cases: list, interactions_by_case: dict = None) -> str:
    s = build_summary(run, cases)
    g = s["generic"]
    lines = [
        f"# ModelEval 评估报告 · Run #{run['id']}",
        "",
        f"- 任务：{s['task']} ｜ 模型：`{s['provider']}/{s['model']}`",
        f"- 样例数：{g['total']}（seed={s['seed']}）",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 成功率 | {g['solved']}/{g['total']}（{g['success_rate']:.1%}） |",
        f"| 平均调用次数 | {g['avg_calls']} |",
        f"| 平均步数 | {g['avg_moves']} |",
        f"| token 总量（输入/输出/思考） | {g['tokens']['prompt']} / {g['tokens']['completion']} / {g['tokens']['reasoning']} |",
        f"| 平均单样例 token | {g['avg_tokens_per_case']} |",
        f"| 平均单样例调用耗时 | {g['avg_latency_ms']} ms |",
        "",
        "## 终止原因分布",
        "",
        "| 原因 | 次数 |",
        "|------|------|",
    ]
    for k, v in s["stop_distribution"].items():
        lines.append(f"| {STOP_REASON_LABELS.get(k, k)} | {v} |")
    if s["task_metrics"]:
        lines += ["", "## 任务自定义指标（均值）", "", "| 指标 | 均值 |", "|------|------|"]
        for k, v in s["task_metrics"].items():
            lines.append(f"| {k} | {v} |")
    lines += ["", "## 样例明细", "", "| # | 打乱序列 | 结果 | 终止原因 | 步数 | 调用 | token |",
              "|---|---------|------|---------|------|------|-------|"]
    for c in cases:
        lines.append(f"| {c['idx']} | `{c['scramble']}` | {'✅' if c['status']=='success' else '❌'} | "
                     f"{STOP_REASON_LABELS.get(c['stop_reason'], c['stop_reason'])} | {c['moves_used']} | "
                     f"{c['calls']} | {c['total_tokens']} |")

    failed = [c for c in cases if c["status"] != "success"]
    if failed:
        lines += ["", "## 失败案例", ""]
        for c in failed:
            lines += [
                f"### 案例 #{c['idx']}",
                f"- 终止原因：{STOP_REASON_LABELS.get(c['stop_reason'], c['stop_reason'])}",
                f"- 打乱序列：`{c['scramble']}`",
                f"- 已执行步数：{c['moves_used']}，最终状态：",
                "```", c["initial_state"], "```", "",
            ]
    return "\n".join(lines)


def build_excel(run: dict, cases: list, path: str, interactions_by_case: dict = None):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()

    s = build_summary(run, cases)
    ws = wb.active
    ws.title = "汇总"
    rows = [("评估报告", None), ("任务", s["task"]), ("模型", f"{s['provider']}/{s['model']}"),
            ("样例数", s["n_cases"]), ("seed", s["seed"])]
    g = s["generic"]
    rows += [("成功率", f"{g['solved']}/{g['total']} ({g['success_rate']:.1%})"),
             ("平均调用次数", g["avg_calls"]), ("平均步数", g["avg_moves"]),
             ("token 输入", g["tokens"]["prompt"]), ("token 输出", g["tokens"]["completion"]),
             ("token 思考", g["tokens"]["reasoning"]), ("token 合计", g["tokens"]["total"])]
    for k, v in s["stop_distribution"].items():
        rows.append((f"终止·{STOP_REASON_LABELS.get(k, k)}", v))
    for k, v in s["task_metrics"].items():
        rows.append((f"任务指标·{k}", v))
    for r in rows:
        ws.append(r)
    ws["A1"].font = Font(bold=True, size=14)

    ws2 = wb.create_sheet("样例明细")
    ws2.append(["#", "打乱序列", "结果", "终止原因", "步数", "调用次数", "输入token", "输出token",
                "思考token", "总token", "耗时ms", "任务评分"])
    for c in cases:
        ws2.append([c["idx"], c["scramble"], "成功" if c["status"] == "success" else "失败",
                    STOP_REASON_LABELS.get(c["stop_reason"], c["stop_reason"]), c["moves_used"],
                    c["calls"], c["prompt_tokens"], c["completion_tokens"], c["reasoning_tokens"],
                    c["total_tokens"], c["latency_ms"], c["score"]])

    ws3 = wb.create_sheet("交互明细")
    ws3.append(["case", "seq", "类型", "耗时ms", "请求（超32k截断）", "响应（超32k截断）"])
    for c in cases:
        for it in (interactions_by_case or {}).get(c["id"], []):
            ws3.append([c["idx"], it["seq"], it["kind"], it["latency_ms"],
                        (it["request"] or "")[:32000], (it["response"] or "")[:32000]])
    wb.save(path)
