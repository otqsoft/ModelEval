# ModelEval 模型测试评估平台 — 需求分析与优化建议

> 版本 v0.1（需求讨论稿）｜ 2026-10-26

---

## 一、原始需求梳理

| # | 原始需求 | 我的理解和补充 |
|---|---------|--------------|
| 1 | 测试项目插件化，默认魔方还原 | 定义统一的「测评任务」抽象接口，魔方是第一个实现 |
| 2 | 模型选择 + 参数（调用次数 / token / 工具 / 步数） | 拆成两层：模型参数（temperature 等）+ 运行限制（熔断参数） |
| 3 | 实时输出完整输入输出、思考链、token 量 | 需要统一各家的 reasoning 字段差异，走流式 + 全量落库 |
| 4 | API 调用 GLM / DeepSeek / Kimi / MiniMax / MiMo | 全部兼容 OpenAI 协议，做统一 Provider 适配层 |
| 5 | 详细评估报告 | 成功率、步数效率、token 成本、失败归因，支持多次运行汇总 |

---

## 二、优化完善后的功能需求

### F1 测评任务插件化

抽象出 `TestProject` 接口，任何新测试项目实现以下 5 个方法即可接入：

```python
class TestProject(ABC):
    def init_case(self, seed: int, difficulty: str) -> Case        # 生成一个测试用例（含随机种子，保证可复现）
    def build_messages(self, case: Case) -> list[Message]          # 生成发给模型的初始 prompt
    def parse_action(self, text: str, tool_calls) -> Action | None # 解析模型回复为合法动作（非法则计入格式错误）
    def apply_action(self, case: Case, action: Action) -> StepResult # 执行动作、更新状态、判断是否达成
    def score(self, case: Case, trajectory: Trajectory) -> Score   # 终局评分
```

**魔方还原（默认项目）的细化设计：**

- **打乱生成**：随机打乱 N 步（难度分档：入门 8 步 / 标准 20 步 / 困难 25 步），记录打乱序列作为可复现凭证；支持 2x2 / 3x3
- **两种交互模式（建议都做）**：
  - `single-shot`：一次把打乱公式给模型，模型一次性输出完整还原序列，系统逐步模拟验证 —— 实现简单，适合批量跑分
  - `agent-step`：多轮交互，模型每步输出一个转动（支持 tool call 返回当前状态），系统反馈新状态 —— 更接近真实 agent 能力测试
- **记谱校验**：只接受标准记谱 `U D L R F B`（含 `'` 和 `2` 后缀），非法记谱不计步并记为格式错误
- **成功判定**：机械验证（模拟器逐步应用转动后检查六面同色），不依赖 LLM 判断 —— 这是魔方项目作为首个测评的最大优势：**结果客观、零评审成本**
- **预留扩展项目**：24 点、数独、迷宫导航、GSM8K 式数学题、代码生成（带单测验证）等，接口按上面 5 个方法套即可

### F2 模型与运行参数配置

分两组，避免混在一起：

| 组 | 参数 | 说明 |
|----|------|------|
| 模型参数 | provider / base_url / api_key / model 名 / temperature / top_p / system prompt / 是否流式 | api_key 加密存储，界面上只显示尾 4 位 |
| 运行限制 | max_calls（最大 API 调用次数）、max_total_tokens（token 总量熔断）、tool_enabled、max_steps（最大交互步数）、单次调用超时、估算费用熔断 | 任一触发即终止该次运行，标记终止原因 |

补充建议：

- **配置 Profile**：参数组合可保存命名模板（如 "DeepSeek-R1 严格模式"），复用
- **多次运行（run × N）**：模型输出有随机性，同一配置自动跑 N 次（默认 5）汇总成功率，而不是单次定结论
- **随机种子 + 配置快照**：每次 run 记录 seed、完整请求配置，保证可复现

### F3 过程记录与实时输出

- 每次模型调用记录一条完整记录：
  - 请求：messages 全文、tools 定义、参数
  - 响应：content、**思考链**、tool_calls、finish_reason
  - 用量：prompt_tokens / completion_tokens / **reasoning_tokens（思考链 token）** / 耗时
- **各 provider 思考链字段不统一，需在适配层归一**：DeepSeek-R1 的 `reasoning_content`、GLM 的 `thinking`、Kimi k-series 的 `reasoning_content` 等 → 统一为 `reasoning` 字段
- 实时推送：后端通过 **SSE** 推给前端，控制台页面流式滚动展示「请求 → 思考 → 回复 → 状态变化」
- 全量落库：SQLite（起步够用）+ 原始 JSONL 归档，轨迹可回放（时间轴形式回看每一步）

### F4 模型接入层

| 提供商 | 接入方式 | 备注 |
|--------|---------|------|
| GLM（智谱） | OpenAI 兼容 `open.bigmodel.cn/api/paas/v4` | thinking 字段需归一 |
| DeepSeek | OpenAI 兼容 | reasoner 型号有 reasoning_content |
| Kimi（月之暗面） | OpenAI 兼容 `api.moonshot.cn` | |
| MiniMax | OpenAI 兼容 | |
| MiMo（小米） | OpenAI 兼容 | 确认其开放平台 endpoint |

统一做成 OpenAI-compatible Provider，新增模型 = 加一条 base_url + model 配置，**不写新代码**。额外建议：预置 provider 模板 + 连通性测试按钮（填完 key 一键验证可用）。

### F5 评估报告

**单次 run 报告：**
- 结论：成功 / 失败 + 终止原因
- 过程统计：步数、调用次数、token 三项分解（输入 / 输出 / 思考）、总耗时、估算费用
- 完整轨迹回放

**汇总报告（N 次运行）：**
- 成功率、平均/最短/最长完成步数、平均调用次数、平均 token 与费用
- **解法效率**：模型解法步数 vs 最优解步数（Kociemba 算法求参考最优解）的比值
- **失败归因分布**：超步数上限 / 输出格式错误 / 解法错误（未还原）/ API 报错 / token 熔断 —— 归因比单纯的"失败"有价值得多

**横向对比报告：**
- 多模型同题对比（同一批打乱用例跑多个模型），雷达图 / 柱状图对比成功率与成本
- 导出：HTML（默认，可打印）、Markdown；报告内嵌轨迹明细

---

## 三、建议技术方案

| 层 | 选型 | 理由 |
|----|------|------|
| 前端 | Vue 3 + TS + Element Plus | 你的主力栈；魔方状态用 Canvas/CSS 3D 渲染可视化 |
| 后端 | FastAPI + SQLite | 单机工具型项目，零运维；后续要多人用再换 MySQL |
| 实时推送 | SSE | 单向推送够用，比 WebSocket 简单 |
| 模型调用 | openai SDK（多 provider 复用）| 统一协议，流式 + usage 字段现成 |
| 魔方模拟 | 自写 3x3 状态机（facelet 表示）+ kociemba 库求最优解 | 模拟器必须自研以保证记谱校验逻辑可控 |

## 四、分期实施建议

1. **M1（核心闭环）**：魔方 single-shot 模式 + DeepSeek 单模型 + 参数配置 + 全量调用记录 + 基础报告（成功率/token）—— 先跑通"能测、能看、有报告"
2. **M2**：agent-step 交互模式 + tool call + SSE 实时控制台 + 思考链归一 + 多 provider 接入
3. **M3**：多次运行汇总 + 失败归因 + 多模型横向对比 + 报告导出 + 魔方可视化回放
4. **M4**：第二个测评项目接入（验证插件化接口）、配置 Profile、费用熔断

## 五、需要老板确认的问题

1. 魔方交互模式：先做 single-shot 还是直接上 agent-step？（建议 M1 先 single-shot）
2. 部署形态：本地单机工具，还是要多人使用的 Web 服务？（影响后端选型和鉴权）
3. 后端用 FastAPI（Python，推荐）还是 GoFrame（Go）？
4. 评估是否要接"工具调用"测试（如让模型调用「查询当前魔方状态」工具）——这是对 agent 能力的关键区分项
