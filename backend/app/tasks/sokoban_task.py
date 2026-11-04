"""推箱子测评任务：内置关卡集（按难度分级），模型通过上下左右移动把所有箱子推到目标点。

- 每次移动（含推箱）计 1 步
- 每个关卡预先用 BFS 验证可解并缓存最优步数与路径
- 关卡难度等级由任务参数 scramble_length 控制（1~N，越大越难）
"""
import random
from collections import deque

from .base import BaseTask, TaskInvalidError

# 关卡字符含义：# 墙  空地  $ 箱子  . 目标点  @ 玩家  * 箱子在目标点  + 玩家在目标点
LEVELS = [
    # 1. 单箱直推（内墙装饰，入门）
    ["######",
     "#    #",
     "# @$.#",
     "#  # #",
     "#    #",
     "######"],
    # 2. 单箱绕柱
    ["########",
     "#      #",
     "# @$ . #",
     "#  ##  #",
     "#      #",
     "########"],
    # 3. 双箱 + 内墙回廊（最优 20 步）
    ["########",
     "#  #   #",
     "# $ $  #",
     "#  ##  #",
     "# @ .. #",
     "#      #",
     "########"],
    # 4. 双箱跨舱室（隔墙通道，最优 26 步）
    ["#########",
     "#   #   #",
     "# $ # . #",
     "#   #   #",
     "## ### ##",
     "#   $   #",
     "#  .    #",
     "#   @   #",
     "#########"],
    # 5. 三箱 + 立柱交错（最优 36 步）
    ["##########",
     "#        #",
     "# $ # $  #",
     "#   #    #",
     "## ### ###",
     "#        #",
     "# $ ...  #",
     "#   @    #",
     "##########"],
    # 6. 三箱穿越墙隙（最优 54 步）
    ["#########",
     "#       #",
     "# $ $ $ #",
     "#   #   #",
     "## ### ##",
     "# . . . #",
     "#   @   #",
     "#########"],
    # 7. 四箱分置四角（最优 32 步）
    ["########",
     "#      #",
     "# $$ . #",
     "# @ #. #",
     "#  $ . #",
     "#  $  .#",
     "#      #",
     "########"],
    # 8. 四箱窄隙冻结阵（最优 24 步）
    ["#########",
     "#       #",
     "# $ $ $ #",
     "# .#.#. #",
     "#       #",
     "## ### ##",
     "# $  .  #",
     "#  @    #",
     "#########"],
    # 9. 四箱长途转运（最优 71 步）
    ["#########",
     "#   #   #",
     "# $ # $.#",
     "#   #   #",
     "## ### ##",
     "# $ $   #",
     "# . ..  #",
     "#   @   #",
     "#########"],
]

DIRS = {"up": (-1, 0), "down": (1, 0), "left": (0, -1), "right": (0, 1)}
DIR_ALIASES = {"u": "up", "d": "down", "l": "left", "r": "right",
               "up": "up", "down": "down", "left": "left", "right": "right",
               "上": "up", "下": "down", "左": "left", "右": "right"}

SYSTEM_PROMPT = """你是一个推箱子（Sokoban）求解智能体。

地图字符：# 墙、空格 地板、$ 箱子、. 目标点、@ 你（玩家）、* 已在目标点上的箱子。
规则：
1. 你每次可以上下左右移动一格；如果面前是箱子且箱子前方为空，则把箱子向前推一格；推入墙或另一个箱子视为非法移动。
2. 调用工具 move_player 执行移动，moves 参数为方向序列字符串，如 "DDRRU"（U/D/L/R 或 上/下/左/右）。
   每个字符计 1 步，总步数上限 {max_steps} 步，超限判负。
3. 把所有箱子都推到目标点上即成功，此时必须用 <answer>DDRRU...</answer> 的格式给出完整移动序列。
4. 箱子只能推不能拉，注意避免把箱子推到墙角死角。"""


def _parse_level(rows: list) -> dict:
    walls, goals, boxes = set(), set(), set()
    player = None
    for r, line in enumerate(rows):
        for c, ch in enumerate(line):
            if ch == "#":
                walls.add((r, c))
            elif ch == "$":
                boxes.add((r, c))
            elif ch == ".":
                goals.add((r, c))
            elif ch == "*":
                boxes.add((r, c)); goals.add((r, c))
            elif ch == "@":
                player = (r, c)
            elif ch == "+":
                player = (r, c); goals.add((r, c))
    return {"walls": frozenset(walls), "goals": frozenset(goals),
            "boxes": frozenset(boxes), "player": player,
            "h": len(rows), "w": max(len(x) for x in rows)}


def _dead_squares(level: dict) -> frozenset:
    """静态角落死锁格：非目标点且横向/纵向均被墙夹死的格子。
    箱子被推入此类格子后永远无法再移动（只能推不能拉），判定不可达，用于 BFS 剪枝。"""
    dead = set()
    walls, goals = level["walls"], level["goals"]
    for r in range(level["h"]):
        for c in range(level["w"]):
            if (r, c) in walls or (r, c) in goals:
                continue
            vblocked = (r - 1, c) in walls or (r + 1, c) in walls
            hblocked = (r, c - 1) in walls or (r, c + 1) in walls
            if vblocked and hblocked:
                dead.add((r, c))
    return frozenset(dead)


def _frozen_dead(nb: tuple, nboxes: frozenset, walls: frozenset, goals: frozenset) -> bool:
    """冻结死锁检测：推入 nb 后，若包含 nb 的某个 2x2 区域被墙/箱完全填满，
    且其中存在未上目标的箱子，则该区域永远无法再移动（互相卡死），判定不可达。"""
    for dr in (-1, 0):
        for dc in (-1, 0):
            block = ((nb[0] + dr, nb[1] + dc), (nb[0] + dr + 1, nb[1] + dc),
                     (nb[0] + dr, nb[1] + dc + 1), (nb[0] + dr + 1, nb[1] + dc + 1))
            if all(p in walls or p in nboxes for p in block):
                if any(p in nboxes and p not in goals for p in block):
                    return True
    return False


def _bfs_solve(level: dict, want_path: bool = False):
    """BFS 求最少步数（普通移动计步）。返回 (dist, path)；不可解返回 (None, None)。
    剪枝：箱子不可推入静态死锁格，也不可形成 2x2 冻结死锁。"""
    dead = _dead_squares(level)
    start = (level["player"], level["boxes"])
    if all(b in level["goals"] for b in start[1]):
        return 0, []
    walls, goals = level["walls"], level["goals"]
    queue = deque([start])
    # visited: (player, boxes) -> (prev_player, prev_boxes, move)
    parent = {start: None}
    while queue:
        player, boxes = queue.popleft()
        occupied = boxes | walls
        for d, (dr, dc) in DIRS.items():
            np = (player[0] + dr, player[1] + dc)
            if np in walls:
                continue
            if np in boxes:
                nb = (np[0] + dr, np[1] + dc)
                if nb in occupied or nb in dead:
                    continue
                nboxes = frozenset(boxes - {np} | {nb})
                if _frozen_dead(nb, nboxes, walls, goals):
                    continue
            else:
                nboxes = boxes
            nstate = (np, nboxes)
            if nstate in parent:
                continue
            parent[nstate] = (player, boxes, d)
            if all(b in goals for b in nboxes):
                path = []
                cur = nstate
                while parent[cur] is not None:
                    path.append(parent[cur][2])
                    cur = parent[cur][:2]
                path.reverse()
                return len(path), path if want_path else None
            queue.append(nstate)
    return None, None


_SOLVE_CACHE: dict = {}


def solve_level(rows: tuple):
    """带缓存的关卡求解（create_case 每案例调用，避免重复 BFS 同一关卡）。"""
    key = tuple(rows)
    if key not in _SOLVE_CACHE:
        _SOLVE_CACHE[key] = _bfs_solve(_parse_level(list(rows)), want_path=True)
    return _SOLVE_CACHE[key]


def _frame(player, boxes) -> dict:
    """生成一帧动画数据：玩家与箱子坐标。"""
    return {"task": "sokoban", "player": [player[0], player[1]],
            "boxes": sorted([r, c] for r, c in boxes)}


def _render(level: dict, player, boxes) -> str:
    lines = []
    for r in range(level["h"]):
        row = []
        for c in range(level["w"]):
            p = (r, c)
            if p in level["walls"]:
                row.append("#")
            elif p == player:
                row.append("+" if p in level["goals"] else "@")
            elif p in boxes:
                row.append("*" if p in level["goals"] else "$")
            elif p in level["goals"]:
                row.append(".")
            else:
                row.append(" ")
        lines.append("".join(row).rstrip() or " ")
    return "\n".join(lines)


class SokobanTask(BaseTask):
    name = "sokoban"
    label = "推箱子"

    def create_case(self, idx: int, seed: int, params: dict) -> dict:
        rng = random.Random(f"modeleval:{seed}:{idx}")
        diff = max(1, min(len(LEVELS), int(params.get("scramble_length", 3))))
        # 难度分级：每个难度档固定对应一关（1~9 越大越难），保证同难度可复现
        level_rows = LEVELS[diff - 1]
        level = _parse_level(level_rows)
        dist, _ = solve_level(level_rows)
        if dist is None:   # 兜底：理论上不会发生（关卡均预验证可解）
            raise ValueError(f"推箱子关卡不可解: {level_rows}")
        return {
            "idx": idx, "seed": seed, "params": params,
            "scramble": f"关卡 {LEVELS.index(level_rows) + 1}（难度 {diff}）",
            "level_rows": level_rows, "level": level,
            "level_idx": LEVELS.index(level_rows),
            "player": level["player"],
            "boxes": set(level["boxes"]),
            "moves": [], "pushes": 0,
            "optimal_len": dist,
        }

    def render_state(self, case: dict) -> str:
        return _render(case["level"], case["player"], case["boxes"])

    def visual_state(self, case: dict) -> dict:
        """前端 2D 舞台首帧：玩家与箱子坐标 + 关卡网格（墙/目标）。"""
        f = _frame(case["player"], case["boxes"])
        f["grid"] = case["level_rows"]
        return f

    def build_messages(self, case: dict) -> list:
        p = case["params"]
        sys = SYSTEM_PROMPT.replace("{max_steps}", str(p["max_steps"]))
        return [
            {"role": "system", "content": sys},
            {"role": "user", "content": (
                f"当前地图（{case['level']['h']} 行）：\n{self.render_state(case)}\n\n"
                "请把所有箱子（$/*）推到目标点（.）上。调用 move_player 工具执行移动。"
            )},
        ]

    def tools_spec(self) -> list:
        return [{
            "type": "function",
            "function": {
                "name": "move_player",
                "description": "让玩家按方向序列移动（可推箱子），返回移动后的最新地图",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "moves": {
                            "type": "string",
                            "description": '方向序列，如 "DDRRU"（U/D/L/R 或 上/下/左/右）',
                        }
                    },
                    "required": ["moves"],
                },
            },
        }]

    def _parse_moves(self, raw) -> list:
        if isinstance(raw, (list, tuple)):
            raw = "".join(str(x) for x in raw)
        s = str(raw).strip()
        s = s.replace("上", "U").replace("下", "D").replace("左", "L").replace("右", "R")
        s = s.replace("up", "U").replace("down", "D").replace("left", "L").replace("right", "R")
        s = s.replace(",", "").replace(" ", "").upper()
        out = []
        for ch in s:
            if ch in "UDLR":
                out.append({"u": "up", "d": "down", "l": "left", "r": "right"}[ch.lower()])
            elif ch:
                raise TaskInvalidError(f"非法方向字符: {ch!r}")
        if not out:
            raise TaskInvalidError("方向序列为空")
        if len(out) > 100:
            raise TaskInvalidError("单次调用移动数过多（上限 100）")
        return out

    def _apply(self, case: dict, dirs: list):
        level = case["level"]
        # 预校验（原子性：任一步非法则整体拒绝，不产生任何副作用）
        p, b = case["player"], set(case["boxes"])
        for d in dirs:
            dr, dc = DIRS[d]
            np = (p[0] + dr, p[1] + dc)
            if np in level["walls"]:
                raise TaskInvalidError(f"非法移动 {d}（撞墙）")
            if np in b:
                nb = (np[0] + dr, np[1] + dc)
                if nb in b or nb in level["walls"]:
                    raise TaskInvalidError(f"非法移动 {d}（箱子被阻挡）")
                b.remove(np); b.add(nb)
            p = np
        # 正式执行 + 逐步动画帧采集
        player, boxes = case["player"], set(case["boxes"])
        frames = []
        for d in dirs:
            dr, dc = DIRS[d]
            np = (player[0] + dr, player[1] + dc)
            if np in boxes:
                nb = (np[0] + dr, np[1] + dc)
                boxes.remove(np)
                boxes.add(nb)
                case["pushes"] += 1
            player = np
            case["moves"].append(d)
            frames.append(_frame(player, boxes))
        case["player"], case["boxes"] = player, boxes
        return frames

    def _is_solved_state(self, case: dict) -> bool:
        return all(b in case["level"]["goals"] for b in case["boxes"])

    def execute_tool(self, case: dict, name: str, arguments: str) -> dict:
        if name != "move_player":
            raise TaskInvalidError(f"未知工具: {name}")
        try:
            import json as _json
            args = _json.loads(arguments or "{}")
        except Exception:
            raise TaskInvalidError(f"工具参数不是合法 JSON: {arguments!r}")
        dirs = self._parse_moves(args.get("moves", ""))
        frames = self._apply(case, dirs)
        solved = self._is_solved_state(case)
        return {"ok": True, "applied": [d[0].upper() for d in dirs],
                "solved": solved, "pushes": case["pushes"],
                "state": "SOLVED" if solved else self.render_state(case),
                "frames": frames}

    def handle_final_text(self, case: dict, text: str) -> dict:
        import re
        m = re.search(r"<answer>(.*?)</answer>", text or "", re.S | re.I)
        source = m.group(1) if m else (text or "")
        try:
            dirs = self._parse_moves(source)
        except TaskInvalidError as e:
            return {"status": "invalid", "detail": str(e)}
        result = self.execute_tool(case, "move_player", '{"moves": "%s"}' % "".join(d[0].upper() for d in dirs))
        if result["solved"]:
            return {"status": "solved", "moves": result["applied"]}
        feedback = (
            f"你给出的移动已执行（{len(result['applied'])} 步），但仍有箱子未归位。当前地图：\n"
            f"{self.render_state(case)}\n请继续（可继续调用 move_player 工具）。"
        )
        return {"status": "continue", "feedback": feedback, "moves": result["applied"]}

    def is_solved(self, case: dict) -> bool:
        return self._is_solved_state(case)

    def score(self, case: dict, solved: bool) -> dict:
        s = {"solved": solved, "moves_used": len(case["moves"]),
             "pushes": case["pushes"], "level_idx": case["level_idx"] + 1}
        if case.get("optimal_len"):
            s["optimal_len"] = case["optimal_len"]
            s["efficiency"] = round(case["optimal_len"] / len(case["moves"]), 3) if solved and case["moves"] else None
        return s

    def solution_for(self, case: dict) -> str:
        level = case["level"]
        _dist, path = _bfs_solve(level, want_path=True)
        if not path:
            return ""
        return "".join(d[0].upper() for d in path)
