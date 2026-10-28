"""3x3 魔方状态机：基于三维坐标旋转实现，内置状态校验器。

贴纸表示：每个贴纸 = (位置坐标 pos, 法向量 normal, 颜色 color)
- 坐标系：x 右(R)，y 上(U)，z 前(F)，各分量 ∈ {-1, 0, 1}
- 贴纸存在于 pos[i] == normal[i] == ±1 的面上；颜色由初始法向决定
- 转动 = 对该层所有贴纸的位置与法向做 90° 旋转（数学保证正确性）
"""
import random
import re

# 面 -> (旋转轴, 层坐标, 旋转方向)；方向 +1 = 绕轴右手 +90°
# U/D/R/L/F/B 均为“面对该面看顺时针”
MOVES = {
    "U": ("y", 1, -1), "D": ("y", -1, 1),
    "R": ("x", 1, -1), "L": ("x", -1, 1),
    "F": ("z", 1, -1), "B": ("z", -1, 1),
}
FACE_ORDER = ["U", "R", "F", "D", "L", "B"]
TOKEN_RE = re.compile(r"^([URFDLB])(2|')?$")


def _rot(v, axis, d):
    x, y, z = v
    if axis == "x":  # d=+1: (x,-z,y)  d=-1: (x,z,-y)
        return (x, z, -y) if d < 0 else (x, -z, y)
    if axis == "y":  # d=+1: (z,y,-x)  d=-1: (-z,y,x)
        return (-z, y, x) if d < 0 else (z, y, -x)
    # z 轴: d=+1: (-y,x,z)  d=-1: (y,-x,z)
    return (y, -x, z) if d < 0 else (-y, x, z)


class Cube:
    def __init__(self):
        self.stickers = []  # [ [pos, normal, color_letter], ... ]
        for x in (-1, 0, 1):
            for y in (-1, 0, 1):
                for z in (-1, 0, 1):
                    for i, s in ((0, 1), (0, -1), (1, 1), (1, -1), (2, 1), (2, -1)):
                        pos = (x, y, z)
                        if pos[i] != s:
                            continue
                        normal = [0, 0, 0]
                        normal[i] = s
                        # 颜色字母 = 初始法向对应的面: +x=R +y=U +z=F -x=L -y=D -z=B
                        color = {(0, 1): "R", (1, 1): "U", (2, 1): "F",
                                 (0, -1): "L", (1, -1): "D", (2, -1): "B"}[(i, s)]
                        self.stickers.append([pos, tuple(normal), color])

    def clone(self) -> "Cube":
        c = Cube.__new__(Cube)
        c.stickers = [list(s) for s in self.stickers]
        return c

    def apply_token(self, token: str):
        m = TOKEN_RE.match(token)
        if not m:
            raise ValueError(f"非法转动记号: {token!r}")
        face, suffix = m.group(1), m.group(2)
        axis, layer, d = MOVES[face]
        times = 2 if suffix == "2" else 1
        dd = -d if suffix == "'" else d
        for _ in range(times):
            for st in self.stickers:
                if st[0][{"x": 0, "y": 1, "z": 2}[axis]] == layer:
                    st[0] = _rot(st[0], axis, dd)
                    st[1] = _rot(st[1], axis, dd)

    def is_solved(self) -> bool:
        groups = {}
        for _, normal, color in self.stickers:
            if groups.setdefault(normal, color) != color:
                return False
        return True

    def facelet_line(self, face: str) -> str:
        """每个面 9 个贴纸颜色，按行序（从左到右、从上到下，面对该面看）。"""
        view = {
            # (origin, colv, rowv)：面对该面看，从左到右、从上到下
            "U": ((-1, 1, -1), (1, 0, 0), (0, 0, 1)),    # 俯视，B 在上
            "D": ((-1, -1, 1), (1, 0, 0), (0, 0, -1)),   # 仰视，F 在上
            "F": ((-1, 1, 1), (1, 0, 0), (0, -1, 0)),
            "B": ((1, 1, -1), (-1, 0, 0), (0, -1, 0)),
            "R": ((1, 1, 1), (0, 0, -1), (0, -1, 0)),
            "L": ((-1, 1, -1), (0, 0, 1), (0, -1, 0)),
        }[face]
        origin, colv, rowv = view
        out = []
        for r in range(3):
            for c in range(3):
                pos = tuple(origin[j] + colv[j] * c + rowv[j] * r for j in range(3))
                for st in self.stickers:
                    if tuple(st[0]) == pos and st[1] == {"U": (0, 1, 0), "D": (0, -1, 0), "R": (1, 0, 0),
                                                         "L": (-1, 0, 0), "F": (0, 0, 1), "B": (0, 0, -1)}[face]:
                        out.append(st[2])
                        break
        return "".join(out)

    def render(self) -> str:
        names = {"U": "上", "D": "下", "L": "左", "R": "右", "F": "前", "B": "后"}
        lines = []
        for f in FACE_ORDER:
            lines.append(f"{f}({names[f]}): {self.facelet_line(f)}")
        return "\n".join(lines)


def parse_moves(text: str):
    """从文本解析转动序列。返回 (合法token列表, 非法token列表)。"""
    if not text:
        return [], []
    candidates = re.findall(r"[A-Za-z]['2]?", text)
    ok, bad = [], []
    for t in candidates:
        if TOKEN_RE.match(t):
            ok.append(t)
        else:
            bad.append(t)
    return ok, bad


def gen_scramble(n: int, rng: random.Random) -> list:
    moves, last = [], None
    while len(moves) < n:
        face = rng.choice(FACE_ORDER)
        if face == last:
            continue
        last = face
        moves.append(face + rng.choice(["", "'", "2"]))
    return moves


def inverse_moves(moves: list) -> list:
    out = []
    for t in reversed(moves):
        m = TOKEN_RE.match(t)
        face, suffix = m.group(1), m.group(2)
        if suffix == "2":
            out.append(face + "2")
        elif suffix == "'":
            out.append(face)
        else:
            out.append(face + "'")
    return out


def optimal_solution(cube: Cube):
    """kociemba 两阶段算法求参考最优解（可选依赖；结果经模拟验证，失败则放弃）。"""
    try:
        import kociemba
    except ImportError:
        return None
    # 转为 kociemba 标准面序 URFDLB 的颜色串
    order = "".join(cube.facelet_line(f) for f in FACE_ORDER)
    try:
        sol = kociemba.solve(order)
        check = cube.clone()
        for t in sol.split():
            check.apply_token(t)
        if check.is_solved():
            return sol
    except Exception:
        pass
    return None
