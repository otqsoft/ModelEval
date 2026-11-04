/* Stage2D：华容道 / 推箱子 实时动画舞台（挂载于 #cube-stage，覆盖 3D 魔方） */
window.Stage2D = (function () {
  const KLOTSKI_SHAPE = { "曹操": [2, 2], "关羽": [1, 2], "张飞": [2, 1], "赵云": [2, 1], "马超": [2, 1], "黄忠": [2, 1] };
  const KLOTSKI_COLOR = { "曹操": "var(--bad)", "关羽": "var(--ok)", "张飞": "var(--accent-2)", "赵云": "var(--accent-2)", "马超": "var(--accent-2)", "黄忠": "var(--accent-2)", "兵": "var(--text2)" };

  function mount(container) {
    let root = null, mode = null;
    let board = null;             // 棋盘容器
    let cell = 0, rows = 0, cols = 0;
    let pieceEls = {};            // klotski: name -> el
    let boxEls = {};              // sokoban: "r,c" -> el
    let staticLayer = null;
    let playToken = 0;            // 帧播放代次（新事件打断旧播放）
    let resizeHandler = null;

    /* 容器尺寸变化（窗口缩放/布局稳定）时重算棋盘，避免预览期尺寸失真 */
    if (window.ResizeObserver) {
      new ResizeObserver(() => { if (mode && board) layoutBoard(); }).observe(container);
    }

    /* ---------- 视角：等轴测角度 + 鼠标拖拽旋转（华容道/推箱子） ---------- */
    const view = { x: 55, z: 45 };
    function applyView() {
      if (board && (mode === "klotski" || mode === "sokoban")) {
        board.style.transform = `rotateX(${view.x}deg) rotateZ(${view.z}deg)`;
      }
    }
    let drag = null;
    container.addEventListener("pointerdown", (e) => {
      if (!mode) return;   // 仅 2D 任务舞台响应（魔方由 cube3d 自己处理）
      drag = { x: e.clientX, y: e.clientY, vx: view.x, vz: view.z };
      try { container.setPointerCapture(e.pointerId); } catch {}
      if (root) root.classList.add("s2d-dragging");
    });
    container.addEventListener("pointermove", (e) => {
      if (!drag) return;
      view.z = Math.max(-30, Math.min(120, drag.vz + (e.clientX - drag.x) * 0.4));
      view.x = Math.max(20, Math.min(85, drag.vx - (e.clientY - drag.y) * 0.3));
      applyView();
    });
    const endDrag = () => { drag = null; if (root) root.classList.remove("s2d-dragging"); };
    container.addEventListener("pointerup", endDrag);
    container.addEventListener("pointercancel", endDrag);

    function destroy() {
      if (root) root.remove();
      root = board = staticLayer = null;
      mode = null; pieceEls = {}; boxEls = {};
      playToken++;
      if (resizeHandler) { window.removeEventListener("resize", resizeHandler); resizeHandler = null; }
    }

    function layoutBoard() {
      if (!board || !rows) return;
      const w = container.clientWidth - 32, h = container.clientHeight - 72;
      cell = Math.max(28, Math.min(w / cols, h / rows, 96));
      if (mode === "sokoban") {
        /* 等轴测投影下棋盘占对角线空间，缩小避免溢出 */
        cell = Math.round(Math.min(cell, 64) * 0.8);
        board.style.setProperty("--wall-h", Math.round(cell * 0.55) + "px");
        board.style.setProperty("--box-h", Math.round(cell * 0.5) + "px");
      } else if (mode === "klotski") {
        /* 含 3D 砖墙围墙的 6x7 网格，等轴测占对角线空间 */
        cell = Math.round(Math.min(cell, 72) * 0.8);
        board.style.setProperty("--pc-h", Math.round(cell * 0.5) + "px");
        board.style.setProperty("--wall-h", Math.round(cell * 0.6) + "px");
      }
      board.style.width = cell * cols + "px";
      board.style.height = cell * rows + "px";
      applyView();
      if (mode === "klotski") {
        board.querySelectorAll(".s2d-cell").forEach((el) => {
          el.style.left = el._c * cell + "px"; el.style.top = el._r * cell + "px";
          el.style.width = cell + "px"; el.style.height = cell + "px";
        });
        for (const name in pieceEls) positionPiece(name, pieceEls[name]._r, pieceEls[name]._c);
        const tz = board.querySelector(".s2d-target");   // 游戏区 rows3-4 cols1-2（围墙偏移 +1）
        if (tz) {
          tz.style.left = 2 * cell + "px"; tz.style.top = 4 * cell + "px";
          tz.style.width = 2 * cell + "px"; tz.style.height = 2 * cell + "px";
        }
        const ex = board.querySelector(".s2d-exit");     // 出口：游戏区底边 cols1-2（围墙留出的缺口）
        if (ex) {
          ex.style.left = 2 * cell + "px"; ex.style.top = 6 * cell - 3 + "px";
          ex.style.width = 2 * cell + "px"; ex.style.height = "6px";
          ex.style.transform = "none";
        }
      } else if (mode === "sokoban") {
        board.querySelectorAll(".s2d-cell").forEach((el) => {
          el.style.left = el._c * cell + "px"; el.style.top = el._r * cell + "px";
          el.style.width = cell + "px"; el.style.height = cell + "px";
        });
        for (const k in boxEls) { const [r, c] = k.split(",").map(Number); placeBox(boxEls[k], r, c); }
        if (playerEl && playerEl._r != null) placePlayer(playerEl, playerEl._r, playerEl._c);
      }
    }

    function placeAt(el, r, c) {
      el.style.left = c * cell + "px";
      el.style.top = r * cell + "px";
      el._r = r; el._c = c;
    }

    /* 推箱子：箱子/玩家需要内缩留边并带尺寸（绝对定位元素默认 0x0） */
    function placeBox(el, r, c) {
      const pad = Math.max(3, cell * 0.1);
      el.style.left = c * cell + pad + "px";
      el.style.top = r * cell + pad + "px";
      el.style.width = cell - pad * 2 + "px";
      el.style.height = cell - pad * 2 + "px";
      el._r = r; el._c = c;
    }

    function placePlayer(el, r, c) {
      const pad = cell * 0.3;
      el.style.left = c * cell + pad + "px";
      el.style.top = r * cell + pad + "px";
      el.style.width = Math.max(6, cell - pad * 2) + "px";
      el.style.height = Math.max(6, cell - pad * 2) + "px";
      el._r = r; el._c = c;
    }

    function positionPiece(name, r, c) {
      const el = pieceEls[name];
      if (!el) return;
      const [h, w] = KLOTSKI_SHAPE[name] || [1, 1];
      const gap = 3;
      el.style.left = (c + 1) * cell + gap + "px";   // +1：外围砖墙占 1 格
      el.style.top = (r + 1) * cell + gap + "px";
      el.style.width = w * cell - gap * 2 + "px";
      el.style.height = h * cell - gap * 2 + "px";
      el.style.fontSize = Math.max(12, cell * (h > 1 || w > 1 ? 0.42 : 0.3)) + "px";
      el._r = r; el._c = c;
    }

    function ensureRoot() {
      if (!root) {
        root = document.createElement("div");
        root.className = "s2d-root";
        container.appendChild(root);
      }
      return root;
    }

    // ---------- 华容道 ----------
    /* 华容道公共骨架：标题（含步数）+ 3D 砖墙围墙棋盘 + 出口目标区 + 图例 */
    function buildKlotski(r) {
      r.innerHTML = `<div class="s2d-title">华容道 · 把曹操移到底部出口<span class="s2d-steps"></span></div>`;
      board = document.createElement("div");
      board.className = "s2d-board klotski";
      r.appendChild(board);
      /* 3D 砖墙围墙：包围 4x5 游戏区一圈（底边 cols1-2 留出缺口作出口） */
      for (let rr = -1; rr <= 5; rr++) {
        for (let cc = -1; cc <= 4; cc++) {
          if (rr !== -1 && rr !== 5 && cc !== -1 && cc !== 4) continue;   // 仅外圈
          if (rr === 5 && cc >= 1 && cc <= 2) continue;                   // 出口缺口
          const el = document.createElement("div");
          el.className = "s2d-cell wall brick";
          el._r = rr + 1; el._c = cc + 1;   // 棋盘坐标系（0..6 / 0..5）
          board.appendChild(el);
        }
      }
      const target = document.createElement("div");
      target.className = "s2d-target";
      board.appendChild(target);
      const exit = document.createElement("div");
      exit.className = "s2d-exit";
      board.appendChild(exit);
      const legend = document.createElement("div");
      legend.className = "s2d-legend";
      legend.innerHTML = `<span class="chip"><span class="sw sw-cao"></span>曹操</span>`
        + `<span class="chip"><span class="sw sw-target"></span>出口目标区（虚线框）</span>`
        + `<span class="chip"><span class="sw sw-exitbar"></span>出口</span>`
        + `<span class="chip">拖动棋盘可旋转视角</span>`;
      r.appendChild(legend);
    }

    function initKlotski() {
      destroy();
      mode = "klotski"; rows = 7; cols = 6;   // 含围墙一圈
      const r = ensureRoot();
      buildKlotski(r);
      resizeHandler = layoutBoard;
      window.addEventListener("resize", layoutBoard);
      setTimeout(layoutBoard, 0);
    }

    function applyKlotski(frame) {
      if (mode !== "klotski") initKlotski();
      for (const [name, r, c] of frame.cells) {
        if (!pieceEls[name]) {
          const el = document.createElement("div");
          const [h, w] = KLOTSKI_SHAPE[name] || [1, 1];
          el.className = "s2d-piece" + (name === "曹操" ? " cao" : "");
          el.style.setProperty("--pc", KLOTSKI_COLOR[name] || KLOTSKI_COLOR["兵"]);   // 顶面着色，侧面由 CSS color-mix 自动加深
          el.textContent = name.replace("兵", "兵");
          board.appendChild(el);
          pieceEls[name] = el;
        }
        positionPiece(name, r, c);
      }
    }

    // ---------- 推箱子 ----------
    /* 推箱子图例 */
    function addSokobanLegend(r) {
      const legend = document.createElement("div");
      legend.className = "s2d-legend";
      legend.innerHTML = `<span class="chip"><span class="sw sw-box"></span>箱子</span>`
        + `<span class="chip"><span class="sw sw-goal"></span>目标点</span>`
        + `<span class="chip"><span class="sw sw-player"></span>玩家</span>`
        + `<span class="chip"><span class="sw sw-wall"></span>障碍</span>`;
      r.appendChild(legend);
    }

    function initSokoban(grid) {
      destroy();
      mode = "sokoban";
      rows = grid.length; cols = Math.max(...grid.map((x) => x.length));
      const r = ensureRoot();
      r.innerHTML = `<div class="s2d-title">推箱子 · 把所有箱子推到目标点<span class="s2d-steps"></span></div>`;
      board = document.createElement("div");
      board.className = "s2d-board sokoban";
      r.appendChild(board);
      grid.forEach((line, ri) => {
        for (let ci = 0; ci < cols; ci++) {
          const ch = line[ci] || " ";
          if (ch === "#") {
            const el = document.createElement("div");
            el.className = "s2d-cell wall";
            el._r = ri; el._c = ci;
            board.appendChild(el);
          } else if (ch === "." ) {
            const el = document.createElement("div");
            el.className = "s2d-cell goal";
            el._r = ri; el._c = ci;
            board.appendChild(el);
          }
        }
      });
      playerEl = null; boxEls = {};
      addSokobanLegend(r);
      resizeHandler = layoutBoard;
      window.addEventListener("resize", layoutBoard);
      setTimeout(layoutBoard, 0);
    }

    let playerEl = null;

    function applySokoban(frame) {
      if (mode !== "sokoban") { /* 需要先 initSokoban，忽略（case_started 一定先到） */ return; }
      const [pr, pc] = frame.player;
      if (!playerEl) {
        playerEl = document.createElement("div");
        playerEl.className = "s2d-player";
        board.appendChild(playerEl);
      }
      placePlayer(playerEl, pr, pc);
      const newBoxes = {};
      for (const [r, c] of frame.boxes) newBoxes[r + "," + c] = true;
      for (const k in boxEls) {
        if (!newBoxes[k]) { boxEls[k].remove(); delete boxEls[k]; }
      }
      for (const k in newBoxes) {
        if (!boxEls[k]) {
          const [r, c] = k.split(",").map(Number);
          const el = document.createElement("div");
          el.className = "s2d-box";
          board.appendChild(el);
          boxEls[k] = el;
          placeBox(el, r, c);
        }
      }
      // 箱子与玩家 DOM 顺序保持在墙上层
      for (const k in boxEls) board.appendChild(boxEls[k]);
      if (playerEl) board.appendChild(playerEl);
    }

    // ---------- 对外接口 ----------
    /* 预览模式：切换任务下拉框时立即展示对应舞台（魔方返回 null 表示还原 3D 视图） */
    function preview(taskId) {
      destroy();
      if (taskId !== "klotski" && taskId !== "sokoban") return;
      mode = taskId;
      const r = ensureRoot();
      if (taskId === "klotski") {
        rows = 7; cols = 6;   // 含围墙一圈
        buildKlotski(r);
      } else {
        rows = 8; cols = 8;
        r.innerHTML = `<div class="s2d-title">推箱子 · 把所有箱子推到目标点<span class="s2d-steps"></span></div>`;
        board = document.createElement("div");
        board.className = "s2d-board sokoban";
        r.appendChild(board);
        addSokobanLegend(r);
      }
      const hint = document.createElement("div");
      hint.className = "s2d-hint";
      hint.textContent = "等待评测开始 · 将在此实时演示求解过程";
      r.appendChild(hint);
      resizeHandler = layoutBoard;
      window.addEventListener("resize", layoutBoard);
      setTimeout(layoutBoard, 0);
    }

    function setVisual(frame) {
      if (!frame) return;
      if (frame.task === "klotski") { if (mode !== "klotski") initKlotski(); applyKlotski(frame); }
      else if (frame.task === "sokoban") {
        if (frame.grid) initSokoban(frame.grid);   // 首帧带网格，重建墙/目标布局
        if (mode !== "sokoban") return;
        applySokoban(frame);
      }
    }

    function playFrames(frames, interval) {
      if (!frames || !frames.length) return;
      const token = ++playToken;
      let i = 0;
      const step = () => {
        if (token !== playToken) return;
        const f = frames[i++];
        if (!f) return;
        f.task === "klotski" ? applyKlotski(f) : applySokoban(f);
        const sc = root && root.querySelector(".s2d-steps");
        if (sc) sc.textContent = `　步数 ${i}/${frames.length}`;
        if (i < frames.length) setTimeout(step, interval || 120);
      };
      step();
    }

    function setDone(ok) {
      if (!root) return;
      const t = root.querySelector(".s2d-title");
      if (t) {
        t.innerHTML = ok
          ? '<span style="color:var(--ok)">✔ 任务完成！</span>'
          : '<span style="color:var(--bad)">✘ 未完成</span>';
      }
      if (board) {
        board.classList.toggle("done", !!ok);
        const cao = pieceEls["曹操"];
        if (cao) cao.classList.toggle("win", !!ok);   // 曹操到位高亮
      }
    }

    function reset() { destroy(); }

    return { setVisual, playFrames, setDone, reset, preview };
  }

  return { mount };
})();
