/* Cube3D：CSS 3D 魔方组件
 * 数学模型与后端 cube.py 一致（x右 y上 z前，右手系）；
 * 渲染时做 y 轴翻转换到 CSS 坐标系，转动 = 4x4 矩阵左乘，浏览器自动插值动画。
 * 用法: const c = Cube3D.mount(stageEl, cubeEl, labelEl); c.reset(scramble); c.apply(moves);
 */
(function () {
  const S = 32;      // cubie 间距(px)
  const HALF = 15;   // cubie 半宽
  const DUR = 300;   // 单步动画时长
  const COLORS = { U: "#f1f5f9", D: "#fbbf24", F: "#22c55e", B: "#3b82f6", R: "#ef4444", L: "#f97316" };
  // 与后端 MOVES 一致：轴索引(0=x,1=y,2=z)、层坐标、右手旋转角(度)
  const MOVES = {
    U: { axis: 1, layer: 1, angle: -90 }, D: { axis: 1, layer: -1, angle: 90 },
    R: { axis: 0, layer: 1, angle: -90 }, L: { axis: 0, layer: -1, angle: 90 },
    F: { axis: 2, layer: 1, angle: -90 }, B: { axis: 2, layer: -1, angle: 90 },
  };
  const FACE_DEFS = [
    { n: [0, 1, 0], key: "U", t: "rotateX(90deg)" },
    { n: [0, -1, 0], key: "D", t: "rotateX(-90deg)" },
    { n: [0, 0, 1], key: "F", t: "" },
    { n: [0, 0, -1], key: "B", t: "rotateY(180deg)" },
    { n: [1, 0, 0], key: "R", t: "rotateY(90deg)" },
    { n: [-1, 0, 0], key: "L", t: "rotateY(-90deg)" },
  ];

  let rootEl = null, labelEl = null, cubies = [];
  let rx = -24, ry = -34, gen = 0, queue = Promise.resolve();

  // ---------- 矩阵工具 ----------
  const I = () => [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
  function rotMat(axis, deg) {
    const a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
    if (axis === 0) return [[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]];
    if (axis === 1) return [[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]];
    return [[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
  }
  function mul(A, B) {
    const C = [];
    for (let i = 0; i < 4; i++) { C[i] = []; for (let j = 0; j < 4; j++) { let v = 0; for (let k = 0; k < 4; k++) v += A[i][k] * B[k][j]; C[i][j] = v; } }
    return C;
  }
  function rotVec(M, v) {
    return [M[0][0] * v[0] + M[0][1] * v[1] + M[0][2] * v[2],
            M[1][0] * v[0] + M[1][1] * v[1] + M[1][2] * v[2],
            M[2][0] * v[0] + M[2][1] * v[1] + M[2][2] * v[2]];
  }
  // 模型坐标(y上) -> CSS坐标(y下)：共轭 F*M*F
  const FLIP = [[1, 0, 0, 0], [0, -1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];
  function toCss(M) {
    const C = mul(mul(FLIP, M), FLIP), a = [];
    for (let j = 0; j < 4; j++) for (let i = 0; i < 4; i++) a.push(+C[i][j].toFixed(5));
    return "matrix3d(" + a.join(",") + ")";
  }

  // ---------- 构建 ----------
  function build(container) {
    container.innerHTML = "";
    cubies = [];
    for (let x = -1; x <= 1; x++) for (let y = -1; y <= 1; y++) for (let z = -1; z <= 1; z++) {
      if (!x && !y && !z) continue;
      const el = document.createElement("div");
      el.className = "cubie";
      const p0 = [x, y, z];
      for (const f of FACE_DEFS) {
        const axis = f.n[0] !== 0 ? 0 : (f.n[1] !== 0 ? 1 : 2);
        const d = document.createElement("div");
        const sticker = p0[axis] === f.n[axis];
        d.className = "face" + (sticker ? "" : " inner");
        if (sticker) d.style.background = COLORS[f.key];
        d.style.transform = (f.t ? f.t + " " : "") + "translateZ(" + HALF + "px)";
        el.appendChild(d);
      }
      const M = I(); M[0][3] = x * S; M[1][3] = y * S; M[2][3] = z * S;
      el.style.transform = toCss(M);
      container.appendChild(el);
      cubies.push({ el, M, p: p0 });
    }
  }

  function setPivot() {
    const pv = document.getElementById("cube-pivot");
    if (pv) pv.style.transform = "scale(1.18) rotateX(" + rx + "deg) rotateY(" + ry + "deg)";
  }
  function setLabel(t, cls) {
    if (!labelEl) return;
    labelEl.textContent = t;
    labelEl.className = cls || "";
  }

  // ---------- 转动 ----------
  function parseToken(t) { const m = /^([URFDLB])(2|')?$/.exec(t || ""); return m ? [m[1], m[2] || ""] : null; }

  function doMove(token, instant) {
    return new Promise((resolve) => {
      const pt = parseToken(token);
      if (!pt) return resolve();
      const [face, suffix] = pt, mv = MOVES[face];
      const base = mv.angle * (suffix === "'" ? -1 : 1);
      const angle = suffix === "2" ? base * 2 : base;
      const R = rotMat(mv.axis, angle);
      const moved = [];
      for (const c of cubies) {
        if (c.p[mv.axis] === mv.layer) {
          c.M = mul(R, c.M);
          // 坐标恒为整数(-1/0/1)，取整消除浮点误差，保证层归属判断可靠
          c.p = rotVec(R, c.p).map(Math.round);
          moved.push(c);
        }
      }
      if (instant) {
        rootEl.classList.add("instant");
        moved.forEach((c) => c.el.style.transform = toCss(c.M));
        void rootEl.offsetWidth;
        rootEl.classList.remove("instant");
        resolve();
        return;
      }
      setLabel(face + (suffix || ""));
      moved.forEach((c) => c.el.style.transform = toCss(c.M));
      setTimeout(resolve, DUR + 20);
    });
  }

  function apply(tokens, instant) {
    const myGen = gen;
    (tokens || []).forEach((t) => {
      queue = queue.then(() => myGen === gen ? doMove(t, instant) : null).catch(() => {});
    });
  }

  function reset(scrambleTokens) {
    gen++;
    queue = Promise.resolve();
    build(rootEl);
    setLabel("打乱中…");
    apply(scrambleTokens, true);
    setTimeout(() => { if (labelEl && labelEl.textContent === "打乱中…") setLabel("等待模型操作"); }, 400);
  }

  // ---------- 拖拽视角 ----------
  function enableDrag(stage) {
    let drag = null;
    stage.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, rx, ry }; stage.setPointerCapture(e.pointerId); });
    stage.addEventListener("pointermove", (e) => {
      if (!drag) return;
      ry = drag.ry + (e.clientX - drag.x) * 0.5;
      rx = Math.max(-80, Math.min(80, drag.rx - (e.clientY - drag.y) * 0.5));
      setPivot();
    });
    stage.addEventListener("pointerup", () => drag = null);
    stage.addEventListener("pointercancel", () => drag = null);
  }

  function mount(stageEl, cubeEl, label) {
    rootEl = cubeEl;
    labelEl = label;
    enableDrag(stageEl);
    build(cubeEl);
    setPivot();
    return { reset, apply, setLabel };
  }

  window.Cube3D = { mount };
})();
