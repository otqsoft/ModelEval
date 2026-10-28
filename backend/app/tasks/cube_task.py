"""魔方还原测评任务：模型通过思考 + 调用 turn_cube 工具完成还原。"""
import json
import random

from ..cube import Cube, gen_scramble, inverse_moves, parse_moves, optimal_solution
from .base import BaseTask, TaskInvalidError

SYSTEM_PROMPT = """你是一个魔方求解智能体，目标：把给定的 3x3 魔方恢复到六面纯色状态。

转动记谱（标准 Singmaster 记谱）：
- U/D/L/R/F/B 分别表示 上/下/左/右/前/后 面顺时针转动 90 度（面对该面看）
- 后缀 ' 表示逆时针（如 R'），后缀 2 表示 180 度（如 U2）

状态表示：6 个面各 9 个贴纸，从左到右、从上到下给出，字母表示该位置贴纸属于哪个初始面。还原完成时每个面的 9 个字母完全相同。

规则：
1. 你可以调用工具 turn_cube 执行转动，并获取转动后的最新状态。
2. 每一步转动都计入总步数，总步数上限为 {max_steps} 步，达到上限将判定失败。
3. 想清楚再动手，尽量用较少的步数完成还原。
4. 完成还原后，必须用 <answer>U R' F2 ...</answer> 的格式给出你的完整还原步骤。"""

TOOL_SPEC = [{
    "type": "function",
    "function": {
        "name": "turn_cube",
        "description": "对当前魔方执行一组转动，返回转动后的最新状态",
        "parameters": {
            "type": "object",
            "properties": {
                "moves": {
                    "type": "string",
                    "description": "转动序列，空格分隔，如 \"R U R' U2\"",
                }
            },
            "required": ["moves"],
        },
    },
}]


class CubeTask(BaseTask):
    name = "cube"
    label = "魔方还原"

    def create_case(self, idx: int, seed: int, params: dict) -> dict:
        rng = random.Random(f"modeleval:{seed}:{idx}")
        scramble = gen_scramble(int(params.get("scramble_length", 20)), rng)
        cube = Cube()
        for t in scramble:
            cube.apply_token(t)
        opt = optimal_solution(cube)
        return {
            "idx": idx,
            "seed": seed,
            "params": params,
            "scramble": " ".join(scramble),
            "scramble_list": scramble,
            "cube": cube,
            "moves": [],  # 已执行的转动（还原步骤）
            "optimal_len": len(opt.split()) if opt else None,
        }

    def build_messages(self, case: dict) -> list:
        max_steps = case["params"]["max_steps"]
        return [
            {"role": "system", "content": SYSTEM_PROMPT.replace("{max_steps}", str(max_steps))},
            {"role": "user", "content": (
                f"打乱序列（供参考）：{case['scramble']}\n"
                f"当前魔方状态：\n{case['cube'].render()}\n\n"
                "请开始还原。你可以调用 turn_cube 工具逐步执行转动。"
            )},
        ]

    def tools_spec(self) -> list:
        return TOOL_SPEC

    def _apply(self, case: dict, tokens: list):
        for t in tokens:
            case["cube"].apply_token(t)
            case["moves"].append(t)

    def execute_tool(self, case: dict, name: str, arguments: str) -> dict:
        if name != "turn_cube":
            raise TaskInvalidError(f"未知工具: {name}")
        try:
            args = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            raise TaskInvalidError(f"工具参数不是合法 JSON: {arguments!r}")
        tokens, bad = parse_moves(str(args.get("moves", "")))
        if bad or not tokens:
            raise TaskInvalidError(f"非法转动记号: {bad or '空序列'}")
        self._apply(case, tokens)
        solved = case["cube"].is_solved()
        return {
            "ok": True,
            "applied": tokens,
            "solved": solved,
            "state": "SOLVED" if solved else case["cube"].render(),
        }

    def handle_final_text(self, case: dict, text: str) -> dict:
        # 优先提取 <answer>...</answer>，否则从全文解析
        import re
        m = re.search(r"<answer>(.*?)</answer>", text or "", re.S)
        source = m.group(1) if m else (text or "")
        tokens, bad = parse_moves(source)
        if bad or not tokens:
            return {"status": "invalid", "detail": f"无法从回复中解析出合法转动序列（非法记号: {bad or '无'}）"}
        self._apply(case, tokens)
        if case["cube"].is_solved():
            return {"status": "solved", "moves": tokens}
        feedback = (
            f"你给出的步骤已执行（{len(tokens)} 步），但魔方尚未还原。当前状态：\n"
            f"{case['cube'].render()}\n请继续（可继续调用 turn_cube 工具）。"
        )
        return {"status": "continue", "feedback": feedback, "moves": tokens}

    def is_solved(self, case: dict) -> bool:
        return case["cube"].is_solved()

    def score(self, case: dict, solved: bool) -> dict:
        moves = len(case["moves"])
        s = {
            "solved": solved,
            "moves_used": moves,
            "scramble_length": len(case["scramble_list"]),
            "extra_moves": max(0, moves - len(case["scramble_list"])),
        }
        if case.get("optimal_len"):
            s["optimal_len"] = case["optimal_len"]
            s["efficiency"] = round(case["optimal_len"] / moves, 3) if solved and moves else None
        return s

    def solution_for(self, case: dict) -> str:
        return " ".join(inverse_moves(case["scramble_list"]))
