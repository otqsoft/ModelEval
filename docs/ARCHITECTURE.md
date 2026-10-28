# ModelEval 架构设计

> 版本 v1.0 ｜ 2026-10-26

## 1. 整体架构

```
┌────────────────────────────────────────────────────────┐
│  前端控制台 (原生 HTML/JS, FastAPI 静态托管)             │
│  配置表单 · SSE 实时监控 · 报告页(汇总/明细/A-B对比/导出) │
└───────────────▲────────────────────────────────────────┘
                │ REST + SSE (/api/*)
┌───────────────┴────────────────────────────────────────┐
│  FastAPI 服务层 (main.py)                               │
│  providers/keys · configs · runs · events · exports     │
├────────────────────────────────────────────────────────┤
│  评测引擎 Engine (engine.py)                            │
│  任务编排 · 终止判定(6类) · 全量采集 · 限流/重试/熔断     │
├──────────────────────┬─────────────────────────────────┤
│  任务插件层 (tasks/)  │  模型网关 (gateway.py)           │
│  BaseTask 统一接口    │  OpenAI 兼容统一协议              │
│  ├ cube 魔方还原      │  GLM/DeepSeek/Kimi/MiniMax/MiMo  │
│  └ (新任务即插即用)    │  思考链归一 · 流式 · usage · Mock │
├──────────────────────┴─────────────────────────────────┤
│  魔方状态机 (cube.py)：坐标旋转实现 + 校验器 + kociemba   │
├────────────────────────────────────────────────────────┤
│  持久化 (db.py, SQLite WAL) + 报告 (report.py)          │
└────────────────────────────────────────────────────────┘
```

## 2. 模块划分

| 模块 | 文件 | 职责 |
|------|------|------|
| 服务层 | `app/main.py` | REST API、SSE 事件流、静态托管、参数校验 |
| 评测引擎 | `app/engine.py` | 用例循环、终止判定、计数熔断、事件发布、落库 |
| 模型网关 | `app/gateway.py` | 统一 OpenAI 兼容调用；思考链字段归一（`reasoning_content`/`reasoning` → `reasoning`）；流式 delta 回调；重试/超时；Mock 网关 |
| 任务插件 | `app/tasks/base.py` | `BaseTask` 五方法接口：`create_case / build_messages / tools_spec / execute_tool / handle_final_text`（+ `is_solved / score / solution_for`） |
| 魔方任务 | `app/tasks/cube_task.py` | 打乱生成、prompt 构造、`turn_cube` 工具、记谱校验、评分 |
| 魔方状态机 | `app/cube.py` | 54 贴纸三维坐标模型，转动=坐标旋转（数学保证正确）；状态校验器；打乱/逆序列；kociemba 参考最优解（可选依赖） |
| 持久化 | `app/db.py` | SQLite（WAL），见下表设计 |
| 报告 | `app/report.py` | 通用指标+任务指标汇总；Markdown/Excel 导出 |
| 配置 | `app/config.py` | Provider 预设、默认参数、任务注册表 |

**新增测评任务** = 实现 `BaseTask` + 在 `config.TASKS` 登记一行，引擎/日志/报告/前端零改动。

**新增模型** = Provider 预设加一条（或前端选"自定义"填 base_url），无需代码。

## 3. 数据库设计（SQLite）

| 表 | 关键字段 | 说明 |
|----|---------|------|
| `api_keys` | provider(PK), api_key, base_url | 密钥仅存后端；前端只显示尾 4 位 |
| `config_templates` | name(UQ), config(JSON) | 评测配置模板快照 |
| `runs` | id, task, provider, model, **config_snapshot**(JSON), n_cases, seed, status, summary(JSON) | 每次评测运行；snapshot 保证可复现 |
| `cases` | id, run_id, idx, scramble, initial_state, status, **stop_reason**, moves_used, calls, prompt/completion/reasoning/total_tokens, latency_ms, score(JSON) | 单样例结果 |
| `interactions` | id, run_id, case_id, seq, kind(llm/tool), **request(JSON), response(JSON)**, latency_ms | 每轮交互完整原始请求/响应，不截断 |
| `events` | id, run_id, seq, case_id, type, payload | 生命周期事件（SSE 持久化部分），支持回放 |

**终止原因枚举**：`solved / max_steps / max_calls / max_tokens / api_error / invalid_output / stopped`

## 4. 关键流程

1. `POST /api/runs` → 参数归一（默认值合并/类型校验）→ 建运行 → 后台线程启动
2. 每个用例：`create_case`（seed 可复现）→ 引擎循环：
   - 前置检查：stop / max_calls / max_total_tokens / max_steps / 限流间隔
   - `gateway.chat()`（流式，delta 实时推送 SSE）→ 全量落库 `interactions`
   - 有 `tool_calls` → 执行 `turn_cube` → 结果回传模型继续；连续 3 次非法 → `invalid_output`
   - 无工具的文本回复 → 解析 `<answer>` 步骤 → 应用/校验 → 未还原则带状态反馈继续
   - 执行后检查：还原→成功（但**步数超上限仍判失败**）；连续 invalid → 终止
3. 结束：任务评分 + 汇总指标 → `runs.summary` → 前端报告页

## 5. 采集与实时

- 每轮 LLM 调用保存：完整 messages、完整原始 chunk 报文（`raw`）、思考链全文、tool_calls、usage（输入/输出/思考 token）、耗时
- SSE 流（内存 deque，事件上限 8000）实时推送 delta 与生命周期事件；刷新页面走 REST 回放
- 导出：Markdown / Excel（汇总+样例+交互）/ JSONL（原始交互）

## 6. 非功能

- 密钥只存后端 SQLite，接口不回传明文（仅尾 4 位提示）
- Mock 网关：零 Key 全链路演示与自测（`selftest.py`）
- 限流：`min_call_interval` 调用间隔；熔断：调用次数/token/费用维度可扩展
- A/B 对比：报告页选择两个 run 按通用指标对比（预留扩展至用例级配对）
