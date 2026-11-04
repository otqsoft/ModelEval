"""自测脚本：魔方状态机正确性 + Mock 网关全链路评测。运行: python selftest.py"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.cube import Cube, gen_scramble, inverse_moves, parse_moves  # noqa: E402
from app.engine import engine  # noqa: E402
from app import db  # noqa: E402
from app.config import DEFAULT_LIMITS, DEFAULT_MODEL_PARAMS, TASKS  # noqa: E402


def test_cube():
    c = Cube()
    assert c.is_solved(), "初始状态应为已还原"
    assert c.facelet_line("U") == "U" * 9 and c.facelet_line("F") == "F" * 9

    # 贴纸总数 54
    assert len(c.stickers) == 54, f"贴纸数应为54, 实际{len(c.stickers)}"

    # 转 U 再转 U' 应回到原状
    c.apply_token("U"); c.apply_token("U'")
    assert c.is_solved(), "U U' 应还原"

    # sexy move R U R' U' 重复 6 次应为恒等
    c = Cube()
    for _ in range(6):
        for t in ["R", "U", "R'", "U'"]:
            c.apply_token(t)
    assert c.is_solved(), "(R U R' U')x6 应为恒等"

    # 超级翻转：对每个面做 180 度后仍可还原
    c = Cube()
    for t in ["R2", "L2", "U2", "D2", "F2", "B2"]:
        c.apply_token(t)
    assert not c.is_solved()

    # 打乱 + 逆序列 = 还原
    rng_seed = 42
    sc = gen_scramble(20, __import__("random").Random(rng_seed))
    c = Cube()
    for t in sc:
        c.apply_token(t)
    assert not c.is_solved()
    for t in inverse_moves(sc):
        c.apply_token(t)
    assert c.is_solved(), "打乱+逆序列应还原"

    # 记谱解析
    ok, bad = parse_moves("R U2 R' F  x Q3")
    assert ok == ["R", "U2", "R'", "F"] and bad == ["x", "Q"], (ok, bad)

    # render 输出 6 行
    c = Cube()
    assert len(c.render().splitlines()) == 6
    print("[OK] 魔方状态机全部通过")


def test_engine_mock():
    cfg = {
        "task": "cube", "provider": "mock", "model": "mock-solver", "base_url": None,
        "model_params": DEFAULT_MODEL_PARAMS.copy(),
        "limits": DEFAULT_LIMITS.copy(),
        "task_params": TASKS["cube"]["params"].copy(),
        "n_cases": 2, "seed": 7,
    }
    run_id = engine.start_run(cfg)
    while True:
        run = db.query_one("SELECT status FROM runs WHERE id=?", (run_id,))
        if run["status"] in ("finished", "stopped", "error"):
            break
        time.sleep(0.2)
    cases = db.query("SELECT * FROM cases WHERE run_id=?", (run_id,))
    assert run["status"] == "finished", run
    assert len(cases) == 2 and all(c["status"] == "success" for c in cases), cases
    assert all(c["stop_reason"] == "solved" for c in cases)
    n_inter = db.query_one("SELECT COUNT(*) n FROM interactions WHERE run_id=?", (run_id,))["n"]
    assert n_inter >= 2, "应至少有 LLM+tool 交互记录"
    summary = db.query_one("SELECT summary FROM runs WHERE id=?", (run_id,))
    import json
    s = json.loads(summary["summary"])
    assert s["generic"]["success_rate"] == 1.0, s
    print(f"[OK] Mock 全链路通过: run={run_id}, 成功率 100%, 交互记录 {n_inter} 条")


def test_termination():
    """步数熔断：max_steps=3 且 mock 解法 20 步 → 应触发 max_steps 终止。"""
    cfg = {
        "task": "cube", "provider": "mock", "model": "mock-solver", "base_url": None,
        "model_params": DEFAULT_MODEL_PARAMS.copy(),
        "limits": {**DEFAULT_LIMITS, "max_calls": 20},
        "task_params": {**TASKS["cube"]["params"], "max_steps": 3, "scramble_length": 20},
        "n_cases": 1, "seed": 7,
    }
    run_id = engine.start_run(cfg)
    while True:
        run = db.query_one("SELECT status FROM runs WHERE id=?", (run_id,))
        if run["status"] in ("finished", "stopped", "error"):
            break
        time.sleep(0.2)
    c = db.query_one("SELECT * FROM cases WHERE run_id=?", (run_id,))
    assert c["status"] == "failed" and c["stop_reason"] == "max_steps", c
    print("[OK] 步数熔断终止判定通过")


def test_klotski():
    """华容道：打乱后 BFS 必有解，最优路径回放必须还原。"""
    import json as _json
    from app.tasks.klotski_task import KlotskiTask, bfs_optimal
    from app.tasks.base import TaskInvalidError
    t = KlotskiTask()
    for seed in range(2):
        case = t.create_case(seed, 42, {"max_steps": 400, "scramble_length": 30})
        dist, path = bfs_optimal(case["pieces"])
        assert dist and path, f"华容道 seed={seed} 应有最优解"
        r = t.execute_tool(case, "move_pieces", _json.dumps({"moves": [
            {"piece": p, "direction": d} for p, d in path]}, ensure_ascii=False))
        assert r["solved"], f"华容道最优路径回放应还原: seed={seed}"
        assert len(case["moves"]) == dist
    print("[OK] 华容道状态机 + BFS 求解全部通过")


def test_sokoban():
    """推箱子：内置关卡全部可解，最优路径回放必须全部归位。"""
    from app.tasks.sokoban_task import SokobanTask, LEVELS, _parse_level, _bfs_solve
    t = SokobanTask()
    for i, rows in enumerate(LEVELS):
        lv = _parse_level(rows)
        dist, path = _bfs_solve(lv, want_path=True)
        assert dist, f"推箱子关卡 {i+1} 不可解"
        case = {"level": lv, "player": lv["player"], "boxes": set(lv["boxes"]), "moves": [], "pushes": 0}
        t._apply(case, path)
        assert all(b in lv["goals"] for b in case["boxes"]), f"关卡 {i+1} 最优路径回放未归位"
    print("[OK] 推箱子关卡 + BFS 求解全部通过")


def _run_mock_task(task: str, n_cases: int = 1):
    cfg = {
        "task": task, "provider": "mock", "model": "mock-solver", "base_url": None,
        "model_params": DEFAULT_MODEL_PARAMS.copy(),
        "limits": {**DEFAULT_LIMITS, "max_calls": 20},
        "task_params": TASKS[task]["params"].copy(),
        "n_cases": n_cases, "seed": 7,
    }
    run_id = engine.start_run(cfg)
    while True:
        run = db.query_one("SELECT status FROM runs WHERE id=?", (run_id,))
        if run["status"] in ("finished", "stopped", "error"):
            break
        time.sleep(0.2)
    cases = db.query("SELECT * FROM cases WHERE run_id=?", (run_id,))
    assert run["status"] == "finished", run
    assert all(c["status"] == "success" for c in cases), (task, cases)
    return run_id, len(cases)


def test_mock_new_tasks():
    """Mock 网关在新任务上的全链路：参考解法一步到位。"""
    for task in ("klotski", "sokoban"):
        run_id, n = _run_mock_task(task)
        print(f"[OK] Mock 全链路通过 ({task}): run={run_id}, 成功率 100%")


if __name__ == "__main__":
    test_cube()
    test_engine_mock()
    test_termination()
    test_klotski()
    test_sokoban()
    test_mock_new_tasks()
    print("ALL TESTS PASSED")
