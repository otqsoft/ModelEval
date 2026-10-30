# ModelEval 模型测试评估平台

对第三方 LLM（GLM / DeepSeek / Kimi / MiniMax / MiMo / 自定义 OpenAI 兼容端点）进行**可插拔任务评测**，默认内置**魔方还原**任务（思考 + 工具调用 + 状态校验器判分）。

> 定位：单机工具型平台，零运维（FastAPI + SQLite），前端为原生 HTML/JS。

---

## ✨ 核心特性

- **插件化测评任务**：统一 `BaseTask` 五方法接口，新增任务即插即用（魔方还原为默认实现）。
- **多模型统一管理**：全部基于 OpenAI 兼容协议，内置 6 家 Provider 预设 + 自定义端点。
- **三层参数体系**：
  - 模型原生（temperature / top_p / max_tokens）
  - 调度约束（最大调用次数 / 累计 token / 工具开关 / API 超时 / 重试 / 限流间隔）
  - 任务专属（最大还原步数 / 打乱步数）
- **全量过程采集 + SSE 实时流**：messages、原始报文、思考链、token、工具入参返回、耗时，逐轮无损落库。
- **六类自动终止判定**：成功 / 步数超限 / 调用超限 / token 超限 / API 异常 / 无效输出。
- **多格式报告**：网页查看、Markdown / Excel / JSONL 导出、失败案例归因、A/B 对比。
- **安全与可复现**：密钥仅存后端（接口只返回尾 4 位）；配置模板保存/加载；随机种子保证可复现。

---

## 🔌 内置模型 Provider

| Provider | 默认 base_url | 预置模型 |
|----------|---------------|----------|
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | glm-4.6 / 4.5 / 4-plus / 4-air / 4-flash |
| DeepSeek | `https://api.deepseek.com/v1` | deepseek-chat / deepseek-reasoner |
| Kimi（月之暗面） | `https://api.moonshot.cn/v1` | kimi-k2-0905-preview / moonshot-v1-8k/32k/128k |
| MiniMax | `https://api.minimaxi.com/v1` | MiniMax-M2 / MiniMax-Text-01 / abab6.5s-chat |
| 小米 MiMo | 自定义端点 | MiMo-7B-RL |
| 自定义（OpenAI 兼容） | 界面填写 | 任意 |
| Mock（内置演示） | — | mock-solver（无需 Key） |

> `base_url` 可在界面覆盖（用于代理或私有化端点）。新增模型无需改代码，填 base_url + model 即可。

---

## 🚀 快速开始

```bash
cd backend
uv sync                               # 按 pyproject.toml 创建/同步虚拟环境（生成 uv.lock）
uv run python selftest.py             # 自测（魔方状态机 + Mock 全链路 + 熔断判定）
uv run app.py                         # 启动服务（默认 127.0.0.1:8660）
# 打开 http://127.0.0.1:8660
```

> 也支持 `uv run uvicorn app.main:app --port 8660`，或 `uv run app.py --port 9000 --host 0.0.0.0 --reload`（热重载开发模式）。

使用方式：

- **无 Key 演示**：提供商选 **Mock（内置演示）** 直接点「开始评测」。
- **真实评测**：选择提供商 → 保存 API Key → 配置参数 → 开始。

---

## ⚙️ 评测参数

**模型原生参数**（默认值）

| 参数 | 默认 | 说明 |
|------|------|------|
| temperature | 0.7 | 采样温度 |
| top_p | 1.0 | 核采样概率 |
| max_tokens | 4096 | 单次生成上限 |

**调度约束**（默认值）

| 参数 | 默认 | 说明 |
|------|------|------|
| max_calls | 20 | 最大模型调用次数 |
| max_total_tokens | 200000 | 全链路累计 token 上限 |
| tool_enabled | true | 是否开启工具调用 |
| api_timeout | 120 | 单次 API 超时（秒） |
| retry | 2 | 失败重试次数 |
| min_call_interval | 0.0 | 相邻调用最小间隔（秒，限流防超额） |

**任务专属参数（魔方还原）**

| 参数 | 默认 | 说明 |
|------|------|------|
| max_steps | 50 | 最大还原步数 |
| scramble_length | 20 | 打乱步数（难度） |

---

## 📊 报告与导出

- 网页报告：`/report` 页面，支持汇总 / 明细 / A-B 对比。
- 导出接口：
  - `GET /api/runs/{id}/export/markdown` — Markdown
  - `GET /api/runs/{id}/export/excel` — Excel（汇总 + 样例 + 交互）
  - `GET /api/runs/{id}/export/jsonl` — 原始交互 JSONL

---

## 📁 项目结构

```
model-eval/
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── main.py          # REST API + SSE + 静态托管
│   │   ├── engine.py        # 评测引擎（编排/终止判定/限流重试）
│   │   ├── gateway.py        # 模型网关（OpenAI 兼容统一协议 + Mock）
│   │   ├── cube.py           # 魔方状态机（坐标旋转 + 校验器）
│   │   ├── db.py             # SQLite（WAL）持久化
│   │   ├── report.py        # 报告汇总 + 导出
│   │   ├── config.py         # Provider 预设 / 默认参数 / 任务注册
│   │   └── tasks/            # 任务插件层
│   │       ├── base.py       # BaseTask 五方法接口
│   │       └── cube_task.py  # 魔方还原实现
│   ├── data/                # SQLite 数据库与导出的报告文件
│   ├── app.py               # 便捷启动入口（uv run app.py）
│   ├── pyproject.toml       # uv 项目配置（依赖声明 + uv.lock）
│   ├── requirements.txt     # 依赖清单（与 pyproject 保持一致）
│   └── selftest.py          # 自测脚本
├── static/                  # 原生 HTML/JS 前端
│   ├── index.html / app.js / style.css   # 控制台
│   └── report.html / report.js / cube3d.js  # 报告页 + 魔方可视化
├── docs/
│   ├── ARCHITECTURE.md      # 架构 / 模块 / 数据库设计
│   └── REQUIREMENTS.md      # 需求分析与优化建议
└── README.md
```

---

## 🧩 二次开发

**新增测评任务**

1. 在 `backend/app/tasks/` 实现 `BaseTask` 五方法接口（`create_case` / `build_messages` / `tools_spec` / `execute_tool` / `handle_final_text`，含 `is_solved` / `score` / `solution_for`）。
2. 在 `backend/app/config.py` 的 `TASKS` 登记一行。
3. 引擎 / 日志 / 报告 / 前端零改动即可接入。

**新增模型 Provider**

在 `config.py` 的 `PROVIDERS` 加一条预设（或前端选「自定义」填 base_url + model），无需改代码。

**启用解法效率指标（可选）**

```bash
pip install kociemba>=1.2    # 安装后自动启用，作为参考最优解对比
```

---

## 🧪 自测

```bash
python selftest.py
```

覆盖三类断言：魔方状态机正确性（坐标旋转恒等、打乱+逆序还原、记谱解析）、Mock 网关全链路（成功率 100%）、步数熔断终止判定。全部通过输出 `ALL TESTS PASSED`。

---

## 📚 文档

- `docs/ARCHITECTURE.md` — 整体架构、模块职责、数据库表设计、关键流程、采集与实时、非功能设计。
- `docs/REQUIREMENTS.md` — 需求梳理、功能细化、技术方案选型、分期实施建议。

---

## 📄 License

见 [LICENSE](LICENSE)。
