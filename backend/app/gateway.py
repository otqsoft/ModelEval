"""统一模型 API 网关：屏蔽各提供商差异，统一流式输出/思考链/usage 采集。

- 全部基于 OpenAI 兼容协议（openai SDK + 各家 base_url）
- 思考链字段归一：reasoning_content（DeepSeek/GLM/Kimi 等）或 reasoning 统一为 reasoning
- 完整采集：原始 chunk 报文、messages、tool_calls、usage、耗时
- 失败重试（连接错误/限流/5xx）、可配置超时
"""
import json
import time
import random
import openai
from openai import OpenAI

from .config import PROVIDERS


class GatewayError(Exception):
    """模型调用最终失败（重试耗尽或不可重试错误）。"""


def _retryable(e: Exception) -> bool:
    return isinstance(
        e,
        (
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.APITimeoutError,
            openai.InternalServerError,
        ),
    )


def _norm_usage(u) -> dict:
    if not u:
        return {}
    d = {
        "prompt_tokens": getattr(u, "prompt_tokens", 0) or 0,
        "completion_tokens": getattr(u, "completion_tokens", 0) or 0,
        "total_tokens": getattr(u, "total_tokens", 0) or 0,
    }
    det = getattr(u, "completion_tokens_details", None)
    rt = getattr(det, "reasoning_tokens", None) if det else None
    if rt:
        d["reasoning_tokens"] = rt
    pd = getattr(u, "prompt_tokens_details", None)
    ct = getattr(pd, "cached_tokens", None) if pd else None
    if ct:
        d["cached_tokens"] = ct
    return d


def _estimate_tokens(messages: list, resp: dict) -> dict:
    """provider 未返回 usage 时粗估（约 3 字符/token），并标记 estimated。"""
    pt = sum(len(str(m.get("content") or "")) + len(json.dumps(m.get("tool_calls") or "", ensure_ascii=False)) for m in messages) // 3
    ct = (len(resp.get("content") or "") + len(resp.get("reasoning") or "")) // 3
    ct += sum(len(t.get("arguments") or "") for t in resp.get("tool_calls") or []) // 3
    return {
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": pt + ct,
        "estimated": True,
    }


class OpenAICompatGateway:
    """OpenAI 兼容网关，适用于 GLM / DeepSeek / Kimi / MiniMax / MiMo / 自定义端点。"""

    def __init__(self, provider: str, model: str, api_key: str, base_url: str,
                 model_params: dict, on_event=None, timeout: float = 120, retry: int = 2):
        self.provider = provider
        self.model = model
        self.model_params = model_params
        self.on_event = on_event  # 回调 on_event(type, payload)
        self.timeout = timeout
        self.retry = retry
        self.client = OpenAI(
            api_key=api_key or "EMPTY",
            base_url=base_url or PROVIDERS.get(provider, {}).get("base_url"),
            timeout=timeout,
            max_retries=0,
        )

    def _emit(self, ev_type, payload):
        if self.on_event:
            self.on_event(ev_type, payload)

    def chat(self, messages: list, tools: list = None) -> dict:
        last_err = None
        for attempt in range(self.retry + 1):
            try:
                return self._once(messages, tools)
            except Exception as e:
                last_err = e
                if _retryable(e) and attempt < self.retry:
                    self._emit("llm_retry", {
                        "attempt": attempt + 1,
                        "error": f"{type(e).__name__}: {e}",
                        "backoff": 2 ** attempt,
                    })
                    time.sleep(2 ** attempt)
                    continue
                raise GatewayError(f"{type(e).__name__}: {e}") from e
        raise GatewayError(str(last_err))

    def _once(self, messages: list, tools: list) -> dict:
        t0 = time.time()
        self._emit("llm_start", {"model": self.model})

        kwargs = dict(
            model=self.model,
            messages=messages,
            stream=True,
            temperature=self.model_params.get("temperature"),
            top_p=self.model_params.get("top_p"),
            max_tokens=self.model_params.get("max_tokens") or None,
        )
        if tools:
            kwargs["tools"] = tools
        kwargs = {k: v for k, v in kwargs.items() if v is not None}

        raw_chunks = []
        content_parts, reasoning_parts = [], []
        tool_slots = {}  # index -> {id, name, arguments}
        usage = None
        finish_reason = None

        try:
            stream = self.client.chat.completions.create(**kwargs, stream_options={"include_usage": True})
        except openai.BadRequestError:
            # 部分 provider 不支持 stream_options，降级重试
            kwargs.pop("stream_options", None)
            stream = self.client.chat.completions.create(**kwargs)

        for chunk in stream:
            dump = chunk.model_dump(exclude_none=False)
            raw_chunks.append(dump)
            if getattr(chunk, "usage", None):
                usage = _norm_usage(chunk.usage)
            for choice in (chunk.choices or []):
                delta = choice.delta
                if getattr(choice, "finish_reason", None):
                    finish_reason = choice.finish_reason
                if not delta:
                    continue
                rc = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if rc:
                    reasoning_parts.append(rc)
                    self._emit("llm_delta", {"field": "reasoning", "text": rc})
                if delta.content:
                    content_parts.append(delta.content)
                    self._emit("llm_delta", {"field": "content", "text": delta.content})
                for tc in (delta.tool_calls or []):
                    slot = tool_slots.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    if getattr(tc, "id", None):
                        slot["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn:
                        if getattr(fn, "name", None):
                            slot["name"] = fn.name
                        if getattr(fn, "arguments", None):
                            slot["arguments"] += fn.arguments
                            self._emit("llm_delta", {"field": "tool_call", "text": fn.arguments})

        latency_ms = int((time.time() - t0) * 1000)
        tool_calls = [tool_slots[i] for i in sorted(tool_slots)]
        resp = {
            "content": "".join(content_parts),
            "reasoning": "".join(reasoning_parts),
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "usage": usage,
            "latency_ms": latency_ms,
            "raw": raw_chunks,  # 完整原始返回报文
            "provider": self.provider,
            "model": self.model,
        }
        if not usage:
            resp["usage"] = _estimate_tokens(messages, resp)
        return resp


class MockGateway:
    """内置演示网关：按预设解法模拟流式返回，用于自测与界面演示，无需 API Key。"""

    def __init__(self, solution: str, on_event=None):
        self.solution = solution
        self.on_event = on_event
        self._calls = 0

    def _emit(self, ev_type, payload):
        if self.on_event:
            self.on_event(ev_type, payload)

    def _fake_stream(self, field, text):
        for i in range(0, len(text), 8):
            self._emit("llm_delta", {"field": field, "text": text[i:i + 8]})
            time.sleep(0.01)

    def chat(self, messages: list, tools: list = None) -> dict:
        t0 = time.time()
        self._calls += 1
        self._emit("llm_start", {"model": "mock-solver"})
        if self._calls == 1 and tools:
            self._fake_stream("reasoning", "模拟思考：直接给出打乱序列的逆序列即可还原。")
            tc = {"id": f"call_mock_{self._calls}", "name": "turn_cube", "arguments": '{"moves": "%s"}' % self.solution}
            self._fake_stream("tool_call", tc["arguments"])
            resp = {"content": "", "reasoning": "模拟思考：直接给出打乱序列的逆序列即可还原。",
                    "tool_calls": [tc], "finish_reason": "tool_calls",
                    "usage": {"prompt_tokens": 300, "completion_tokens": 120, "total_tokens": 420},
                    "latency_ms": int((time.time() - t0) * 1000),
                    "raw": [{"mock": True, "calls": self._calls}], "provider": "mock", "model": "mock-solver"}
        else:
            text = f"<answer>{self.solution}</answer>"
            self._fake_stream("content", text)
            resp = {"content": text, "reasoning": "", "tool_calls": [], "finish_reason": "stop",
                    "usage": {"prompt_tokens": 300, "completion_tokens": 60, "total_tokens": 360},
                    "latency_ms": int((time.time() - t0) * 1000),
                    "raw": [{"mock": True, "calls": self._calls}], "provider": "mock", "model": "mock-solver"}
        return resp


class AnthropicGateway:
    """Anthropic Messages API 网关（SSE 流式）。

    引擎传入的是 OpenAI 格式的 messages / tools，本类内部归一为 Anthropic 格式，
    返回与 OpenAICompatGateway 一致的 dict 形状（content/reasoning/tool_calls/usage/...），
    供引擎与前端复用。未能从 Anthropic 拿到 usage 时退化为粗略估算。
    """

    DEFAULT_BASE = "https://api.anthropic.com/v1"

    def __init__(self, provider, model, api_key, base_url=None, model_params=None,
                 on_event=None, timeout=120, retry=2):
        self.provider = provider
        self.model = model
        self.api_key = api_key or "EMPTY"
        self.base_url = (base_url or self.DEFAULT_BASE).rstrip("/")
        self.model_params = model_params or {}
        self.on_event = on_event
        self.timeout = timeout
        self.retry = retry

    def _emit(self, ev_type, payload):
        if self.on_event:
            self.on_event(ev_type, payload)

    # ---- OpenAI 格式消息 -> Anthropic 格式 ----
    @staticmethod
    def _to_anthropic(messages, tools):
        system_parts, conv = [], []
        for m in messages:
            role = m.get("role")
            content = m.get("content")
            if role == "system":
                system_parts.append(content or "")
                continue
            if role == "tool":
                conv.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id"),
                        "content": m.get("content") or "",
                    }],
                })
                continue
            if role == "assistant" and m.get("tool_calls"):
                blocks = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in m["tool_calls"]:
                    try:
                        inp = json.loads(tc.get("arguments") or "{}")
                    except Exception:
                        inp = {"_raw": tc.get("arguments")}
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id") or f"tu_{len(blocks)}",
                        "name": tc["name"],
                        "input": inp,
                    })
                conv.append({"role": "assistant", "content": blocks})
                continue
            conv.append({"role": role, "content": content or ""})
        system = "\n\n".join(system_parts) if system_parts else None
        anth_tools = None
        if tools:
            anth_tools = []
            for t in tools:
                fn = t.get("function") or t
                anth_tools.append({
                    "name": fn.get("name"),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
                })
        return system, conv, anth_tools

    def chat(self, messages, tools=None):
        last_err = None
        for attempt in range(self.retry + 1):
            try:
                return self._once(messages, tools)
            except Exception as e:
                last_err = e
                if attempt < self.retry and isinstance(e, (urllib.error.URLError, TimeoutError, GatewayError)):
                    self._emit("llm_retry", {"attempt": attempt + 1, "error": str(e), "backoff": 2 ** attempt})
                    time.sleep(2 ** attempt)
                    continue
                raise GatewayError(f"{type(e).__name__}: {e}") from e
        raise GatewayError(str(last_err))

    def _once(self, messages, tools):
        system, conv, anth_tools = self._to_anthropic(messages, tools)
        body = {
            "model": self.model,
            "max_tokens": int(self.model_params.get("max_tokens") or 4096),
            "messages": conv,
            "stream": True,
        }
        if system:
            body["system"] = system
        if anth_tools:
            body["tools"] = anth_tools
        if self.model_params.get("temperature") is not None:
            body["temperature"] = self.model_params["temperature"]
        if self.model_params.get("top_p") is not None:
            body["top_p"] = self.model_params["top_p"]
        body = {k: v for k, v in body.items() if v is not None}

        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        data = json.dumps(body).encode("utf-8")
        url = self.base_url + "/messages"
        self._emit("llm_start", {"model": self.model})
        t0 = time.time()

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")[:500]
            raise GatewayError(f"HTTP {e.code}: {detail}")

        content_parts, tool_blocks = [], {}
        usage, finish_reason = None, None

        for raw in resp:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload in ("[DONE]", ""):
                continue
            try:
                ev = json.loads(payload)
            except Exception:
                continue
            etype = ev.get("type")
            if etype == "content_block_start":
                block = ev.get("content_block", {})
                if block.get("type") == "tool_use":
                    tool_blocks[block["index"]] = {"id": block.get("id"), "name": block.get("name"), "partial": ""}
            elif etype == "content_block_delta":
                delta = ev.get("delta", {})
                dt = delta.get("type")
                if dt == "text_delta":
                    text = delta.get("text", "")
                    content_parts.append(text)
                    self._emit("llm_delta", {"field": "content", "text": text})
                elif dt == "input_json_delta":
                    idx = ev.get("index", 0)
                    tool_blocks.setdefault(idx, {"id": f"tu_{idx}", "name": "", "partial": ""})
                    tool_blocks[idx]["partial"] += delta.get("partial_json", "")
            elif etype == "message_delta":
                u = ev.get("usage", {})
                if u:
                    usage = {
                        "prompt_tokens": u.get("input_tokens", 0),
                        "completion_tokens": u.get("output_tokens", 0),
                        "total_tokens": u.get("input_tokens", 0) + u.get("output_tokens", 0),
                    }
                    cr = u.get("cache_read_input_tokens")
                    if cr:
                        usage["cached_tokens"] = cr
                finish_reason = ev.get("delta", {}).get("stop_reason")

        latency_ms = int((time.time() - t0) * 1000)
        tool_calls = [
            {"id": b["id"], "name": b["name"], "arguments": b["partial"]}
            for _, b in sorted(tool_blocks.items())
        ]

        api_resp = {
            "content": "".join(content_parts),
            "reasoning": "",
            "tool_calls": tool_calls,
            "finish_reason": finish_reason,
            "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "latency_ms": latency_ms,
            "raw": [],
            "provider": self.provider,
            "model": self.model,
        }
        if not usage:
            api_resp["usage"] = _estimate_tokens(messages, api_resp)
        return api_resp


def build_gateway(run_cfg: dict, solution: str = "", on_event=None):
    """根据运行配置构造网关实例。"""
    limits = run_cfg["limits"]
    provider = run_cfg["provider"]
    if provider == "mock":
        return MockGateway(solution=solution, on_event=on_event)
    from . import db
    row = db.query_one("SELECT * FROM providers WHERE id=?", (provider,))
    if not row:
        raise GatewayError(f"未知提供商: {provider}")
    if not row["api_key"]:
        raise GatewayError(f"提供商 {provider} 未配置 API Key")
    base_url = run_cfg.get("base_url") or (row["base_url"] if row["base_url"] else None)
    common = dict(
        provider=provider,
        model=run_cfg["model"],
        api_key=row["api_key"],
        base_url=base_url,
        model_params=run_cfg["model_params"],
        on_event=on_event,
        timeout=limits.get("api_timeout", 120),
        retry=limits.get("retry", 2),
    )
    if (row["protocol"] or "openai") == "anthropic":
        return AnthropicGateway(**common)
    return OpenAICompatGateway(**common)
