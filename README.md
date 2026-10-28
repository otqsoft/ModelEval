# ModelEval 模型测试评估平台

对第三方 LLM（GLM / DeepSeek / Kimi / MiniMax / MiMo / 自定义 OpenAI 兼容端点）进行可插拔任务评测，默认内置**魔方还原**任务（思考 + 工具调用 + 状态校验器判分）。

## 快速开始

```bash
cd backend
pip install -r requirements.txt        # 或使用已有 venv
python selftest.py                     # 自测（魔方状态机 + Mock 全链路）
uvicorn app.main:app --port 8660
# 打开 http://127.0.0.1:8660
```

- 无 Key 演示：提供商选 **Mock（内置演示）** 直接点「开始评测」
- 真实评测：选择提供商 → 保存 API Key → 配置参数 → 开始

## 功能清单

- 插件化测评任务（BaseTask 五方法接口），魔方还原为默认实现
- 参数三组：模型原生（temperature/top_p/max_tokens）、调度约束（最大调用次数/累计token/工具开关/API超时/重试/限流间隔）、任务专属（最大还原步数/打乱步数）
- 每轮交互全量采集 + SSE 实时流式展示：messages、原始报文、思考链、token、工具入参返回、耗时
- 六类自动终止判定：成功/步数超限/调用超限/token超限/API异常/无效输出
- 报告：网页查看、Markdown / Excel / JSONL 导出、失败案例、A/B 对比
- 密钥仅存后端；配置模板保存/加载；单样例调试；随机种子可复现

详见 `docs/ARCHITECTURE.md`（架构 / 模块 / 数据库设计）与 `docs/REQUIREMENTS.md`（需求分析）。
