"""全局配置：内置 Provider 预设、任务注册、默认参数。"""

# 内置模型提供商预设（全部 OpenAI 兼容协议）
# base_url 可在界面上覆盖（如使用代理或私有化端点）
PROVIDERS = {
    "glm": {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4.6", "glm-4.5", "glm-4-plus", "glm-4-air", "glm-4-flash"],
        "model": "glm-4.6",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "model": "deepseek-chat",
    },
    "kimi": {
        "label": "Kimi（月之暗面）",
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2-0905-preview", "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "model": "kimi-k2-0905-preview",
    },
    "minimax": {
        "label": "MiniMax",
        "base_url": "https://api.minimaxi.com/v1",
        "models": ["MiniMax-M2", "MiniMax-Text-01", "abab6.5s-chat"],
        "model": "MiniMax-M2",
    },
    "mimo": {
        "label": "小米 MiMo（自定义端点）",
        "base_url": "",
        "models": ["MiMo-7B-RL"],
        "model": "MiMo-7B-RL",
    },
    "custom": {
        "label": "自定义（OpenAI 兼容）",
        "base_url": "",
        "models": [],
        "model": "",
    },
    "mock": {
        "label": "Mock（内置演示，无需 Key）",
        "base_url": "",
        "models": ["mock-solver"],
        "model": "mock-solver",
    },
}

# 模型原生参数默认值
DEFAULT_MODEL_PARAMS = {"temperature": 0.7, "top_p": 1.0, "max_tokens": 4096}

# 评测调度约束参数默认值
DEFAULT_LIMITS = {
    "max_calls": 20,            # 最大模型调用次数
    "max_total_tokens": 200000,  # 全链路累计 token 上限
    "tool_enabled": True,        # 是否开启工具调用
    "api_timeout": 120,          # 单次 API 超时（秒）
    "retry": 2,                  # 失败重试次数
    "min_call_interval": 0.0,    # 相邻两次调用的最小间隔（秒），限流防超额
}

# 任务注册表：新增任务在此登记，并实现 tasks/base.py 的 BaseTask 接口
TASKS = {
    "cube": {
        "label": "魔方还原",
        "params": {
            "max_steps": 50,        # 任务专属：最大还原步数
            "scramble_length": 20,  # 打乱步数（难度）
        },
    },
}

RUN_TERMINAL_STATUS = ("finished", "stopped", "error")
