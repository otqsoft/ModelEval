"""华容道测评任务：4x5 棋盘，经典「横刀立马」开局，模型通过滑块移动把曹操送到底部出口。

- 每个滑块每格移动计 1 步（2x2/2x1 滑块整体滑动）
- 打乱：从经典布局做 N 步随机游走（保证可达可解）
- 最优解：同型滑块等价化后的 BFS（横刀立马状态空间约 2.6 万，毫秒级）
"""
import json
import random
from collections import deque

from .base import BaseTask, TaskInvalidError

# 经典「横刀立马」布局：name -> (row, col, h, w, 显示字符)
CLASSIC_LAYOUT = {
    "曹操": (0, 1, 2, 2, "曹"),
    "关羽": (2, 1, 1, 2, "关"),
    "张飞": (0, 0, 2, 1, "张"),
    "赵云": (0, 3, 2, 1, "赵"),
    "马超": (3, 0, 2, 1, "马"),
    "黄忠": (3, 3, 2, 1, "黄"),
    "兵1": (2, 0, 1, 1, "兵"),
    "兵2": (2, 3, 1, 1, "兵"),
    "兵3": (3, 1, 1, 1, "兵"),
    "兵4": (3, 2, 1, 1, "兵"),
}
ROWS, COLS = 5, 4
TYPE_OF = {"曹操": "C", "关羽": "H", "张飞": "V", "赵云": "V", "马超": "V", "黄忠": "V",
           "兵1": "S", "兵2": "S", "兵3": "S", "兵4": "S"}
DIRS = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}
DIR_ALIASES = {"up": "up", "down": "down", "left": "left", "right": "right",
               "u": "up", "d": "down", "l": "left", "r": "right",
               "上": "up", "下": "down", "左": "left", "右": "right"}

SYSTEM_PROMPT = """你是一个华容道求解智能体。棋盘为 {rows} 行 x {cols} 列，经典「横刀立马」开局：
- 曹操：2x2 大方块，目标是移动到棋盘底部中央出口（占据第 4~5 行、第 2~3 列，即左上角到达 row=3, col=1）。
- 关羽：2x1 横条；张飞/赵云/马超/黄忠：1x2 竖条；兵1~兵4：1x1 小方块。
- 所有滑块只能整体上下左右滑动一格，不能重叠、不能越界、不能斜移。

规则：
1. 调用工具 move_pieces 执行移动，moves 参数为 JSON 数组，每项 {{"piece": "滑块名", "direction": "up|down|left|right"}}，
   也可用中文方向（上/下/左/右）。每个滑动计 1 步，总步数上限 {max_steps} 步，超限判负。
2. 每次调用会返回移动后的最新棋盘状态。
3. 曹操到达底部中央出口即成功，此时必须用 <answer>[{{"piece": "曹操", "direction": "down"}}, ...]</answer>
   的格式给出你的完整移动序列。"""


def _occupancy(pieces: dict) -> dict:
    """返回 (row, col) -> piece_name 的占用表。"""
    occ = {}
    for name, (r, c, h, w, _ch) in pieces.items():
        for i in range(h):
            for j in range(w):
                occ[(r + i, c + j)] = name
    return occ


def _type_board(pieces: dict) -> tuple:
    """同型等价化棋盘（BFS 去重用）：C=曹操 H=横条 V=竖条 S=兵 .=空。"""
    occ = _occupancy(pieces)
    return tuple(TYPE_OF[occ[(r, c)]] if (r, c) in occ else "." for r in range(ROWS) for c in range(COLS))


def _is_goal(pieces: dict) -> bool:
    r, c, _, _, _ = pieces["曹操"]
    return (r, c) == (3, 1)


def _slide(pieces: dict, name: str, direction: str):
    """尝试滑动一格，返回新布局；非法返回 None。"""
    r, c, h, w, ch = pieces[name]
    dr, dc = DIRS[direction]
    nr, nc = r + dr, c + dc
    if nr < 0 or nc < 0 or nr + h > ROWS or nc + w > COLS:
        return None
    occ = _occupancy(pieces)
    for name2 in occ.values():
        if name2 == name:
            continue
        r2, c2, h2, w2, _ = pieces[name2]
        if not (nr + h <= r2 or r2 + h2 <= nr or nc + w <= c2 or c2 + w2 <= nc):
            return None
    new = {k: list(v) for k, v in pieces.items()}
    new[name][0], new[name][1] = nr, nc
    return {k: tuple(v) for k, v in new.items()}


def bfs_optimal(pieces: dict):
    """BFS 求最少步数与路径（含同型滑块等价剪枝）。返回 (dist, moves)，不可达返回 (None, None)。"""
    start = dict(pieces)
    if _is_goal(start):
        return 0, []
    # 队列元素: (布局, 父节点索引, (piece, direction))
    nodes = [(start, -1, None)]
    seen = {_type_board(start)}
    i = 0
    while i < len(nodes):
        cur, parent, mv = nodes[i]
        i += 1
        for name in cur:
            for d in DIRS:
                nxt = _slide(cur, name, d)
                if nxt is None:
                    continue
                tb = _type_board(nxt)
                if tb in seen:
                    continue
                seen.add(tb)
                nodes.append((nxt, i - 1, (name, d)))
                if _is_goal(nxt):
                    # 回溯路径（nodes[0] 为起点，无移动）
                    path = [(name, d)]
                    idx = len(nodes) - 1
                    while True:
                        p = nodes[idx][1]
                        if p is None or p <= 0:
                            break
                        path.append(nodes[p][2])
                        idx = p
                    return len(path), list(reversed(path))
    return None, None


def _frame(pieces: dict) -> dict:
    """生成一帧动画数据：[[name, row, col], ...]（形状/颜色由前端静态表定义）。"""
    return {"task": "klotski", "cells": [[n, v[0], v[1]] for n, v in pieces.items()]}


def render_board(pieces: dict, empty_char: str = "·") -> str:
    occ = _occupancy(pieces)
    lines = []
    for r in range(ROWS):
        cells = []
        for c in range(COLS):
            name = occ.get((r, c))
            cells.append(pieces[name][4] if name else empty_char)
        lines.append(" ".join(cells))
    return "\n".join(lines)


class KlotskiTask(BaseTask):
    name = "klotski"
    label = "华容道"

    def create_case(self, idx: int, seed: int, params: dict) -> dict:
        rng = random.Random(f"modeleval:{seed}:{idx}")
        pieces = {k: (v[0], v[1], v[2], v[3], v[4]) for k, v in CLASSIC_LAYOUT.items()}
        n_scramble = max(1, int(params.get("scramble_length", 30)))
        applied = 0
        guard = 0
        while applied < n_scramble and guard < n_scramble * 50:
            guard += 1
            name = rng.choice(list(pieces))
            d = rng.choice(list(DIRS))
            nxt = _slide(pieces, name, d)
            if nxt is None or _is_goal(nxt):
                continue
            pieces = nxt
            applied += 1
        dist, _path = bfs_optimal(pieces)
        return {
            "idx": idx, "seed": seed, "params": params,
            "scramble": f"横刀立马开局 + {applied} 步随机打乱",
            "scramble_steps": applied,
            "pieces": pieces,
            "moves": [],
            "optimal_len": dist,
        }

    def render_state(self, case: dict) -> str:
        return render_board(case["pieces"])

    def visual_state(self, case: dict) -> dict:
        """前端 2D 舞台首帧：各滑块左上角坐标。"""
        return _frame(case["pieces"])

    def build_messages(self, case: dict) -> list:
        p = case["params"]
        sys = SYSTEM_PROMPT.format(rows=ROWS, cols=COLS, max_steps=p["max_steps"])
        pos = "\n".join(
            f"- {n}（{v[2]}x{v[3]}，左上角 row={v[0]}, col={v[1]}）"
            for n, v in case["pieces"].items())
        return [
            {"role": "system", "content": sys},
            {"role": "user", "content": (
                f"当前棋盘（{ROWS} 行 x {COLS} 列，· 为空格）：\n{self.render_state(case)}\n\n"
                f"滑块清单：\n{pos}\n\n"
                "请开始求解。调用 move_pieces 工具执行移动，目标是把曹操移动到底部中央出口。"
            )},
        ]

    def tools_spec(self) -> list:
        return [{
            "type": "function",
            "function": {
                "name": "move_pieces",
                "description": "对华容道棋盘执行一组滑块移动，返回移动后的最新状态",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "moves": {
                            "type": "string",
                            "description": 'JSON 数组字符串，如 \'[{"piece": "曹操", "direction": "down"}]\'，'
                                           "direction 可用 up/down/left/right 或 上/下/左/右",
                        }
                    },
                    "required": ["moves"],
                },
            },
        }]

    def _parse_moves(self, raw) -> list:
        """解析移动列表：JSON 数组或 '曹操:下,兵1:上' 简写。返回 [(piece, direction), ...]。"""
        if isinstance(raw, (list, tuple)):
            items = raw
        else:
            s = str(raw).strip()
            try:
                items = json.loads(s)
                if not isinstance(items, list):
                    raise ValueError
            except (json.JSONDecodeError, ValueError):
                items = []
                for part in s.replace("，", ",").replace("；", ";").replace(";", ",").split(","):
                    part = part.strip()
                    if not part:
                        continue
                    for sep in (":", "：", "-", " "):
                        if sep in part:
                            a, b = part.split(sep, 1)
                            items.append({"piece": a.strip(), "direction": b.strip()})
                            break
                    else:
                        raise TaskInvalidError(f"无法解析移动项: {part!r}")
        out = []
        for it in items:
            if not isinstance(it, dict) or "piece" not in it or "direction" not in it:
                raise TaskInvalidError(f"移动项格式错误: {it!r}")
            piece = str(it["piece"]).strip()
            d = DIR_ALIASES.get(str(it["direction"]).strip().lower())
            if d is None:
                raise TaskInvalidError(f"非法方向: {it['direction']!r}")
            out.append((piece, d))
        if not out:
            raise TaskInvalidError("移动序列为空")
        if len(out) > 200:
            raise TaskInvalidError("单次调用移动数过多（上限 200）")
        return out

    def execute_tool(self, case: dict, name: str, arguments: str) -> dict:
        if name != "move_pieces":
            raise TaskInvalidError(f"未知工具: {name}")
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            raise TaskInvalidError(f"工具参数不是合法 JSON: {arguments!r}")
        moves = self._parse_moves(args.get("moves", ""))
        # 先整体校验再执行（原子性）
        pieces = dict(case["pieces"])
        for piece, d in moves:
            if piece not in pieces:
                raise TaskInvalidError(f"未知滑块: {piece!r}")
            nxt = _slide(pieces, piece, d)
            if nxt is None:
                raise TaskInvalidError(f"非法移动: {piece} {d}（被阻挡或越界）")
            pieces = nxt
        orig = dict(case["pieces"])   # 移动前布局（动画帧起点）
        case["pieces"] = pieces
        frames = []
        cur = dict(orig)
        for piece, d in moves:
            cur = _slide(cur, piece, d)
            frames.append(_frame(cur))
            case["moves"].append(f"{piece}:{d}")
        solved = _is_goal(pieces)
        return {"ok": True, "applied": [f"{p}:{d}" for p, d in moves],
                "solved": solved, "state": "SOLVED" if solved else self.render_state(case),
                "frames": frames}

    def handle_final_text(self, case: dict, text: str) -> dict:
        import re
        m = re.search(r"<answer>(.*?)</answer>", text or "", re.S)
        source = m.group(1) if m else (text or "")
        try:
            moves = self._parse_moves(source)
        except TaskInvalidError as e:
            return {"status": "invalid", "detail": str(e)}
        result = self.execute_tool(case, "move_pieces", json.dumps({"moves": [
            {"piece": p, "direction": d} for p, d in moves]}, ensure_ascii=False))
        if result["solved"]:
            return {"status": "solved", "moves": result["applied"]}
        feedback = (
            f"你给出的移动已执行（{len(result['applied'])} 步），但曹操尚未到达出口。当前棋盘：\n"
            f"{self.render_state(case)}\n请继续（可继续调用 move_pieces 工具）。"
        )
        return {"status": "continue", "feedback": feedback, "moves": result["applied"]}

    def is_solved(self, case: dict) -> bool:
        return _is_goal(case["pieces"])

    def score(self, case: dict, solved: bool) -> dict:
        moves = len(case["moves"])
        s = {"solved": solved, "moves_used": moves, "scramble_steps": case.get("scramble_steps", 0)}
        if case.get("optimal_len"):
            s["optimal_len"] = case["optimal_len"]
            s["efficiency"] = round(case["optimal_len"] / moves, 3) if solved and moves else None
        return s

    def solution_for(self, case: dict) -> str:
        _dist, path = bfs_optimal(case["pieces"])
        if not path:
            return ""
        return json.dumps([{"piece": p, "direction": d} for p, d in path], ensure_ascii=False)
