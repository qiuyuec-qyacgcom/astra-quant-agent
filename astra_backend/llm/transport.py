"""单次请求的构建、发送与响应解析（传输层）。

纯函数，不读任何模块级常量；TRANSIENT_MARKERS 随本模块一起迁出。
对 llm_manager 内**会读常量**的函数（init_llm_config / get_active_llm_runtime 等）
没有任何依赖，故可直接搬迁而不破坏测试注入接缝。
结构优化阶段 2（B4）。
"""
from __future__ import annotations
from astra_backend.llm.policy import (RESPONSE_START_TIMEOUT_SECONDS, REASONING_ONLY_TIMEOUT_SECONDS, QWEN_DIRECT_RECOVERY_TIMEOUT_SECONDS, QWEN_DIRECT_RECOVERY_MAX_TOKENS)

import re
import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from astra_backend.llm.capabilities import _detect_reasoning_type
from astra_backend.llm.providers import _join_api_path

import http.client
from astra_backend import analysis_capture



# Transient upstream faults (gateway route flaps, bot/rate shields, 5xx) must not
# silently degrade a trading or self-evolution cycle into NO_CHANGE. Retry with backoff.
# 注：unknown provider / model_not_found 已移出瞬时名单（2026-09-09 事件复盘）——
# 网关不认识该模型是轮内持续故障，原地重试只会白白烧掉请求窗口，按硬故障立即换模型。
TRANSIENT_MARKERS = (
    "upstream", "temporarily unavailable", "overloaded", "rate limit",
    "too many requests", "capacity", "busy", "bad gateway", "gateway timeout",
)


class _LLMTransientError(Exception):
    """可重试错误：瞬时 HTTP、超时、连接层异常（拒绝/重置/DNS/TLS）、坏响应体、空正文。

    """

    def __init__(self, message: str, timed_out: bool = False, reasoning_only: bool = False, fail_over_now: bool = False):
        super().__init__(message)
        self.timed_out = timed_out
        self.reasoning_only = reasoning_only
        self.fail_over_now = fail_over_now or timed_out


class _LLMHardError(Exception):
    """不可重试错误（对该模型）：认证失败、404、参数被拒等——直接切换下一个回退模型。"""


def _is_transient_http(code: int, body: str) -> bool:
    low = (body or "").lower()
    if code in (408, 409, 425, 429, 500, 502, 503, 504):
        return True
    if code in (400, 401, 402, 403) and any(m in low for m in TRANSIENT_MARKERS):
        return True
    return False


def _parse_llm_response(target_format: str, res_json: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    if not isinstance(res_json, dict):
        raise _LLMTransientError("LLM 响应格式错误：根节点不是对象")
    if res_json.get("error"):
        raise _LLMTransientError(f"LLM 上游返回错误：{str(res_json['error'])[:220]}")
    content = ""
    reasoning_content = ""
    raw_usage = res_json.get("usage", {}) if isinstance(res_json, dict) else {}
    usage = dict(raw_usage) if isinstance(raw_usage, dict) else {}

    # Protocol 1: Claude Messages Response
    if target_format == "claude_messages":
        text_chunks = [c.get("text", "") for c in res_json.get("content", []) if c.get("type") == "text"]
        thinking_chunks = [c.get("thinking", "") for c in res_json.get("content", []) if c.get("type") == "thinking"]
        content = "".join(text_chunks).strip()
        reasoning_content = "\n".join(thinking_chunks).strip()
        if not usage:
            usage = {
                "total_tokens": res_json.get("usage", {}).get("input_tokens", 0) + res_json.get("usage", {}).get("output_tokens", 0)
            }

    # Protocol 2: OpenAI Responses Response
    elif target_format == "openai_responses":
        content = str(res_json.get("output_text") or "").strip()
        if not content:
            for item in res_json.get("output", []):
                if item.get("type") == "message":
                    for part in item.get("content", []):
                        if part.get("type") == "output_text" or "text" in part:
                            content += str(part.get("text", ""))
                elif item.get("type") == "reasoning":
                    reasoning_content += str(item.get("content") or item.get("summary") or "")
        content = content.strip()
        reasoning_content = reasoning_content.strip()

    # Protocol 3: OpenAI Chat Completions Response
    else:
        choices = res_json.get("choices") or [{}]
        msg = choices[0].get("message") or {}
        content = str(msg.get("content") or "").strip()
        reasoning_content = str(msg.get("reasoning_content") or "").strip()

    # 规范化提取各厂商 Prompt Caching 缓存命中指标（OpenAI, DeepSeek, Claude, Gemini, Qwen）
    #
    # 三种形态都要认（2026-09-29 实测）：
    #   1) Chat Completions：usage.prompt_tokens_details.cached_tokens（且**无命中时该字段整段省略**）
    #   2) Responses：usage.input_tokens_details.cached_tokens（**始终存在**，0 或 N）—— 本仓生产
    #      切到 /responses 后靠它把"上游说 0"与"上游什么都没说"分开；
    #   3) Anthropic Messages：usage.cache_read_input_tokens / cache_creation_input_tokens；
    #      DeepSeek：prompt_cache_hit_tokens；Gemini 原生：cached_content_token_count。
    prompt_details = usage.get("prompt_tokens_details", {}) if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    input_details = usage.get("input_tokens_details", {}) if isinstance(usage.get("input_tokens_details"), dict) else {}
    # ⚠️ 不能用 `a or b or c` 串：**显式的 0 是"上报了 0"，不是"没上报"**。
    # Anthropic 在无命中时给的正是 `cache_read_input_tokens: 0`，用 or 会把"命中 0"
    # 误判成"上游没上报"，于是又回到"不可判定 ≠ 0"的反面。
    _candidates = (
        prompt_details.get("cached_tokens") if "cached_tokens" in prompt_details else None,
        input_details.get("cached_tokens") if "cached_tokens" in input_details else None,
        usage.get("prompt_cache_hit_tokens"),
        usage.get("cache_read_input_tokens"),
        usage.get("cached_content_token_count"),
        usage.get("cached_tokens"),
    )
    cached_tokens = next((value for value in _candidates if value is not None), None)
    #: 是否**真的上报过**缓存指标：False ⇒ 上游对本响应只字未提（不可判定 ≠ 0）。
    usage["cache_reported"] = cached_tokens is not None
    if cached_tokens is not None:
        try:
            usage["cached_tokens"] = int(cached_tokens)
        except (TypeError, ValueError):
            pass
    cache_creation = usage.get("cache_creation_input_tokens")
    if cache_creation is None and isinstance(usage.get("cache_creation"), dict):
        c_dict = usage.get("cache_creation")
        cache_creation = sum(int(v) for v in c_dict.values() if isinstance(v, (int, float)))
    if cache_creation is not None:
        try:
            usage["cache_creation_tokens"] = int(cache_creation)
        except (TypeError, ValueError):
            pass
    if usage.get("prompt_cache_miss_tokens") is not None:
        try:
            usage["cache_miss_tokens"] = int(usage["prompt_cache_miss_tokens"])
        except (TypeError, ValueError):
            pass

    # 2026 缓存效能归一化：计算命中率 (0.0% ~ 100.0%)
    input_t = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
    if isinstance(input_t, (int, float)) and input_t > 0 and usage.get("cached_tokens") is not None:
        usage["cache_hit_ratio"] = round(float(usage["cached_tokens"]) / float(input_t) * 100.0, 1)

    # 截断显性化：Responses 用 status/incomplete_details，Chat 用 finish_reason=length。
    # 只做标注、不改控制流 —— 硬门禁仍是决策层的 JSON 校验。
    if target_format == "openai_responses":
        status = str(res_json.get("status") or "").strip().lower()
        usage["truncated"] = bool(status and status not in ("completed", "success"))
    else:
        finish = str((res_json.get("choices") or [{}])[0].get("finish_reason") or "").strip().lower()
        usage["truncated"] = finish == "length"

    return content, reasoning_content, usage


def build_request_spec(
    model: str,
    messages: List[Dict[str, str]],
    base_url: str,
    api_key: str = "",
    api_format: str = "openai_chat",
    reasoning_effort: str = "high",
    temperature: Optional[float] = 0.2,
    response_format: Optional[Dict[str, Any]] = None,
    reasoning_type: str = "auto",
    max_tokens: Optional[int] = None,
    stream: bool = False,
    enable_thinking: Optional[bool] = None,
    api_path: str = "",
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """Build endpoint URL, headers, and request payload according to the specific API protocol format."""
    cleaned_url = base_url.rstrip("/")
    # 「API 路径」字段生效：标准路径由协议格式决定；仅非标准自定义路径覆盖之。
    custom_path = str(api_path or "").strip()
    if custom_path and not custom_path.startswith("/"):
        custom_path = "/" + custom_path
    if custom_path in ("/chat/completions", "/messages", "/responses", "/v1/chat/completions", "/v1/messages", "/v1/responses"):
        custom_path = ""
    m_lower = model.lower()
    rtype = reasoning_type if reasoning_type != "auto" else _detect_reasoning_type(model)
    effort = (reasoning_effort or "auto").strip().lower()

    # 2026 会话亲和性标识（Session Affinity）：防止反代或中转集群轮询不同 Key 打散服务端显存 KV Cache
    first_sys = next((str(m.get("content") or "") for m in messages if m.get("role") == "system"), "")
    if not first_sys and messages:
        first_sys = str(messages[0].get("content") or "")
    affinity_seed = f"{model}:{first_sys[:300]}"
    affinity_id = f"astra-{hashlib.sha256(affinity_seed.encode('utf-8')).hexdigest()[:16]}"

    # Protocol 1: Anthropic Claude Messages API
    if api_format == "claude_messages":
        endpoint = _join_api_path(cleaned_url, custom_path or "/messages")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AstraQuant/8.3 (Claude-Messages)",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
            "X-Session-ID": affinity_id,
            "X-Astra-Affinity": affinity_id,
            "X-Channel-Affinity": affinity_id,
        }
        if api_key:
            headers["x-api-key"] = api_key

        # Separate system message
        system_chunks = [m["content"] for m in messages if m.get("role") == "system"]
        chat_messages = [{"role": m["role"], "content": m["content"]} for m in messages if m.get("role") != "system"]

        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens if max_tokens is not None else 4096,
            "messages": chat_messages,
        }
        if system_chunks:
            sys_combined = "\n\n".join(system_chunks)
            # 2026 Claude Prompt Caching: >=1000 字符长系统提示词注入 ephemeral 缓存断点
            if len(sys_combined) >= 1000:
                cache_ctrl: Dict[str, Any] = {"type": "ephemeral"}
                # 2026 扩展缓存 TTL：支持 1h 扩展生命周期，避免 15 分钟交易周期击穿 5m 短缓存
                ttl_pref = os.getenv("ASTRA_LLM_CACHE_TTL", "1h").strip().lower()
                if ttl_pref in ("1h", "1hour", "extended"):
                    cache_ctrl["ttl"] = "1h"
                payload["system"] = [
                    {
                        "type": "text",
                        "text": sys_combined,
                        "cache_control": cache_ctrl,
                    }
                ]
            else:
                payload["system"] = sys_combined

        if effort in ("max", "xhigh", "high", "medium", "low"):
            budget_map = {
                "max": 64000,
                "xhigh": 32000,
                "high": 16000,
                "medium": 8000,
                "low": 2048,
            }
            budget = budget_map[effort]
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            payload["max_tokens"] = budget + (max_tokens if max_tokens is not None else 4096)
        elif effort == "none":
            payload["thinking"] = {"type": "disabled"}
            if temperature is not None:
                payload["temperature"] = temperature
        else:
            if temperature is not None:
                payload["temperature"] = temperature

        return endpoint, headers, payload

    # Protocol 2: OpenAI Responses API (/responses)
    elif api_format == "openai_responses":
        endpoint = _join_api_path(cleaned_url, custom_path or "/responses")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AstraQuant/8.3 (OpenAI-Responses)",
            "X-Session-ID": affinity_id,
            "X-Astra-Affinity": affinity_id,
            "X-Channel-Affinity": affinity_id,
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: Dict[str, Any] = {
            "model": model,
            "input": messages,
            "user": affinity_id,
        }
        if response_format and response_format.get("type") == "json_object":
            payload["text"] = {"format": {"type": "json_object"}}
        if effort in ("max", "xhigh", "high", "medium", "low", "minimal"):
            payload["reasoning"] = {"effort": effort}

        return endpoint, headers, payload

    # Protocol 3: OpenAI Chat Completions (/chat/completions, Default)
    else:
        endpoint = _join_api_path(cleaned_url, custom_path or "/chat/completions")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AstraQuant/8.3 (OpenAI-Chat)",
            "X-Session-ID": affinity_id,
            "X-Astra-Affinity": affinity_id,
            "X-Channel-Affinity": affinity_id,
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "user": affinity_id,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}

        # Temperature handling for reasoning models vs normal models
        is_reasoning_model = (
            rtype in ("deepseek_reasoner", "standard_effort")
            or m_lower.startswith(("o1", "o3", "o4", "gpt-5", "gpt-6", "chatgpt-6"))
            or "gpt-5" in m_lower or "gpt-6" in m_lower or "chatgpt-6" in m_lower
            or "deepseek-v4" in m_lower or "v4.1" in m_lower
            or "reasoner" in m_lower
            or "-r1" in m_lower
            or "qwen3" in m_lower or "qwen-3" in m_lower or "qwq" in m_lower
            or "kimi-k" in m_lower or "glm-5" in m_lower
        )
        if not is_reasoning_model:
            if temperature is not None:
                payload["temperature"] = temperature
        else:
            if "gemini" in m_lower and temperature is not None:
                payload["temperature"] = temperature

        # Standard reasoning effort parameter (supports max, xhigh, high, medium, low, minimal, none)
        if rtype == "standard_effort" or (rtype == "auto" and (
            "gemini" in m_lower or "qwen3" in m_lower or "qwen-3" in m_lower or "qwq" in m_lower
            or m_lower.startswith(("o1", "o3", "o4", "gpt-5", "gpt-6", "chatgpt-6"))
            or "gpt-5" in m_lower or "gpt-6" in m_lower or "chatgpt-6" in m_lower
            or "deepseek-v4" in m_lower or "v4.1" in m_lower
            or "kimi-k" in m_lower or "glm-5" in m_lower
        )):
            if effort in ("max", "xhigh", "high", "medium", "low", "minimal"):
                payload["reasoning_effort"] = effort
            elif effort == "none":
                payload["reasoning_effort"] = "none"

        # Qwen3-compatible gateways use this switch to disable the thinking
        # channel. Only add it for an explicit recovery request so ordinary
        # configured calls retain their provider defaults.
        if enable_thinking is not None and (
            "qwen3" in m_lower or "qwen-3" in m_lower
        ):
            payload["enable_thinking"] = bool(enable_thinking)

        if response_format and rtype != "deepseek_reasoner":
            payload["response_format"] = response_format

        return endpoint, headers, payload


def build_chat_payload(
    model: str,
    messages: List[Dict[str, str]],
    reasoning_effort: str = "high",
    temperature: Optional[float] = 0.2,
    response_format: Optional[Dict[str, Any]] = None,
    reasoning_type: str = "auto",
    max_tokens: Optional[int] = None,
    enable_thinking: Optional[bool] = None,
) -> Dict[str, Any]:
    """Compatibility wrapper for standard chat payload generation."""
    _, _, payload = build_request_spec(
        model=model,
        messages=messages,
        base_url="https://api.openai.com/v1",
        api_format="openai_chat",
        reasoning_effort=reasoning_effort,
        temperature=temperature,
        response_format=response_format,
        reasoning_type=reasoning_type,
        max_tokens=max_tokens,
        enable_thinking=enable_thinking,
    )
    return payload


def _attempt_llm_call(
    cand: Dict[str, Any],
    messages: List[Dict[str, str]],
    temperature: Optional[float],
    response_format: Optional[Dict[str, Any]],
    effective_timeout: float,
    *,
    attempt: int = 0,
    candidate_index: int = 0,
    enable_thinking: Optional[bool] = None,
    max_tokens: Optional[int] = None,
    response_start_timeout: Optional[float] = None,
    reasoning_only_timeout: Optional[float] = None,
) -> Tuple[str, str, Dict[str, Any], int]:
    """单次请求一个模型；失败时抛 _LLMTransientError（可重试）或 _LLMHardError（换模型）。"""
    response_start_timeout = RESPONSE_START_TIMEOUT_SECONDS if response_start_timeout is None else response_start_timeout
    reasoning_only_timeout = REASONING_ONLY_TIMEOUT_SECONDS if reasoning_only_timeout is None else reasoning_only_timeout
    endpoint, headers, payload = build_request_spec(
        model=cand["model"],
        messages=messages,
        base_url=cand["base_url"],
        api_key=cand.get("api_key", ""),
        api_format=cand.get("api_format", "openai_chat"),
        reasoning_effort=cand.get("reasoning_effort", "high"),
        temperature=temperature,
        response_format=response_format,
        reasoning_type=cand.get("reasoning_type", "auto"),
        api_path=cand.get("api_path", ""),
        stream=cand.get("api_format", "openai_chat") == "openai_chat",
        enable_thinking=enable_thinking,
        max_tokens=max_tokens,
    )

    t0 = time.perf_counter()
    deadline = t0 + effective_timeout
    target_format = cand.get("api_format", "openai_chat")
    progress: Dict[str, Any] = {"stage": "response_headers", "received_bytes": 0}
    seen_payloads = set()
    adaptation = 0

    def timeout_error(exc: BaseException) -> _LLMTransientError:
        reasoning_only = bool(
            progress.get("reasoning_only_timeout")
            or (
                progress.get("reasoning_chars")
                and progress.get("read_deadline") is not None
                and time.perf_counter() >= float(progress["read_deadline"])
                and not progress.get("content_chars")
            )
        )
        if progress.get("content_chars"):
            detail = "已收到部分答案，但未完成"
        elif reasoning_only or progress.get("reasoning_chars"):
            watchdog = progress.get("reasoning_watchdog_seconds")
            if watchdog:
                detail = f"仅思考看门狗 {float(watchdog):.0f}s 到期，已收到思考内容但未收到最终答案"
            else:
                detail = "已收到思考内容，但未收到最终答案"
        elif progress["stage"] == "response_headers":
            watchdog = progress.get("response_start_watchdog_seconds")
            if watchdog:
                detail = f"响应头看门狗 {float(watchdog):.0f}s 到期（可能是网络、网关排队或模型处理延迟）"
            else:
                detail = "等待连接或响应头超时（可能是网络、网关排队或模型处理延迟）"
        else:
            detail = "读取响应体超时"
        elapsed = time.perf_counter() - t0
        analysis_capture.emit("llm.error", {
            "attempt": attempt, "candidate_index": candidate_index,
            "model": cand["model"], "timeout_seconds": effective_timeout,
            "elapsed_seconds": round(elapsed, 3), **progress, "error": str(exc),
        }, "timeout")
        return _LLMTransientError(
            f"LLM 请求超时（模型 {cand['model']}，本模型预算 {effective_timeout:.0f}s，"
            f"已等待 {elapsed:.1f}s）：{detail}", timed_out=True,
            reasoning_only=reasoning_only,
        )

    while True:
        seen_payloads.add(json.dumps(payload, sort_keys=True))
        try:
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError("request deadline exceeded")
            progress = {"stage": "response_headers", "received_bytes": 0}
            req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers)
            try:
                transport_timeout = remaining
                # The short response-start watchdog is only a transport
                # timeout. Once headers arrive, body reads use the candidate
                # deadline (and the reasoning-only watchdog below).
                if target_format == "openai_chat" and response_start_timeout > 0:
                    transport_timeout = min(remaining, response_start_timeout)
                    if transport_timeout < remaining:
                        progress["response_start_watchdog_seconds"] = transport_timeout
                resp_handle = analysis_capture.llm_request(
                    req, transport_timeout, urllib.request.urlopen,
                    attempt=attempt, candidate_index=candidate_index, fallback=adaptation > 0,
                    adaptation=adaptation, api_format=target_format,
                )
            except urllib.error.HTTPError as exc:
                try:
                    progress["stage"] = "error_body"
                    err_b = b"".join(_response_chunks(exc, deadline, progress)).decode("utf-8", errors="replace")
                finally:
                    exc.close()
                analysis_capture.emit("llm.error", {
                    "attempt": attempt, "candidate_index": candidate_index,
                    "http_status": exc.code, "body": err_b,
                }, "failed")
                adapted = _adapt_chat_payload(payload, err_b) if exc.code == 400 and target_format == "openai_chat" else None
                if adapted is not None and json.dumps(adapted, sort_keys=True) not in seen_payloads:
                    analysis_capture.emit("llm.parameter_adaptation", {
                        "model": cand["model"], "attempt": attempt, "candidate_index": candidate_index,
                        "removed_parameters": sorted(set(payload) - set(adapted)),
                        "added_parameters": sorted(set(adapted) - set(payload)),
                    })
                    payload = adapted
                    adaptation += 1
                    continue
                error_type = _LLMTransientError if _is_transient_http(exc.code, err_b) else _LLMHardError
                if error_type is _LLMTransientError and exc.code == 504:
                    raise _LLMTransientError(f"LLM 网关返回 HTTP 504（模型 {cand['model']}）：{err_b[:280]}", fail_over_now=True) from exc
                raise error_type(f"LLM 网关返回 HTTP {exc.code}（模型 {cand['model']}）：{err_b[:280]}") from exc

            with resp_handle as resp:
                progress["stage"] = "response_body"
                res_json = _read_llm_response(resp, target_format, deadline, progress, effective_timeout, reasoning_only_timeout)
            latency_ms = int((time.perf_counter() - t0) * 1000)
            analysis_capture.emit("llm.response", {"response": res_json, "latency_ms": latency_ms}, "received")
            content, reasoning, usage = _parse_llm_response(target_format, res_json)
            content = _validate_llm_answer(res_json, content, reasoning, response_format, cand["model"])
            return content, reasoning, usage, latency_ms
        except (TimeoutError, socket.timeout) as exc:
            raise timeout_error(exc) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise timeout_error(exc) from exc
            raise _LLMTransientError(f"LLM 连接层异常（模型 {cand['model']}）：{str(reason or exc)[:220]}") from exc
        except (ValueError, OSError, http.client.HTTPException) as exc:
            raise _LLMTransientError(f"LLM 响应读取或解析失败（模型 {cand['model']}）：{str(exc)[:200]}") from exc


def _response_chunks(resp, deadline: float, progress: Dict[str, Any]):
    """Bound the entire body read, including an upstream that keeps sending heartbeats."""
    read1 = getattr(resp, "read1", None)
    while True:
        active_deadline = min(
            deadline,
            float(progress.get("read_deadline", deadline) or deadline),
        )
        remaining = active_deadline - time.perf_counter()
        if remaining <= 0:
            raise TimeoutError("request deadline exceeded")
        # urllib exposes a socket timeout, not a total deadline. Recalculate it
        # before each read so headers + body + heartbeats share the same budget.
        raw = getattr(getattr(resp, "fp", None), "raw", None)
        sock = getattr(raw, "_sock", None)
        if sock is not None:
            sock.settimeout(remaining)
        chunk = read1(8192) if callable(read1) else resp.read()
        if chunk:
            progress["received_bytes"] = progress.get("received_bytes", 0) + len(chunk)
        if time.perf_counter() >= active_deadline:
            raise TimeoutError("request deadline exceeded")
        if not chunk:
            return
        yield chunk
        if not callable(read1):
            return


def _read_llm_response(resp, target_format: str, deadline: float, progress: Dict[str, Any], effective_timeout: float, reasoning_only_timeout: Optional[float] = None) -> Dict[str, Any]:
    """Accept ordinary JSON or Chat Completions SSE, preserving usage and reasoning."""
    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    usage: Dict[str, Any] = {}
    finish_reason = None
    done = False
    data_lines: List[bytes] = []
    reasoning_deadline: Optional[float] = None

    def consume_event():
        nonlocal usage, finish_reason, done, reasoning_deadline
        if not data_lines:
            return
        data = b"\n".join(data_lines).strip()
        data_lines.clear()
        if data == b"[DONE]":
            done = True
            return
        event = json.loads(data.decode("utf-8"))
        if not isinstance(event, dict):
            raise ValueError("SSE event is not an object")
        if event.get("error"):
            raise _LLMTransientError(f"LLM 流式响应错误：{str(event['error'])[:220]}")
        if isinstance(event.get("usage"), dict):
            usage.update(event["usage"])
        for choice in event.get("choices") or []:
            if choice.get("index", 0) != 0:
                continue
            delta = choice.get("delta") or choice.get("message") or {}
            text = delta.get("content") or ""
            reasoning = delta.get("reasoning_content") or ""
            if text:
                content_parts.append(text)
                progress["content_chars"] = progress.get("content_chars", 0) + len(text)
                # The watchdog only covers the reasoning-only phase. Once the
                # model starts its final answer, restore the candidate's full
                # body deadline so a long JSON answer is not cut off at 60s.
                if reasoning_deadline is not None:
                    reasoning_deadline = None
                    progress.pop("read_deadline", None)
                    progress.pop("reasoning_watchdog_seconds", None)
            if reasoning:
                reasoning_parts.append(reasoning)
                progress["reasoning_chars"] = progress.get("reasoning_chars", 0) + len(reasoning)
                # A long explicit model budget must not be silently shortened
                # by the legacy fixed thinking watchdog.
                watchdog = (REASONING_ONLY_TIMEOUT_SECONDS if reasoning_only_timeout is None else reasoning_only_timeout) if effective_timeout <= 60.0 else 0.0
                if (
                    reasoning_deadline is None
                    and watchdog > 0
                    and not content_parts
                ):
                    reasoning_deadline = min(
                        deadline,
                        time.perf_counter() + watchdog,
                    )
                    progress["read_deadline"] = reasoning_deadline
                    progress["reasoning_watchdog_seconds"] = watchdog
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

    def consume_line(line: bytes):
        line = line.rstrip(b"\r")
        if not line:
            consume_event()
        elif line.startswith(b"data:"):
            data_lines.append(line[5:].lstrip(b" "))

    pending = b""
    is_sse = None
    sse_prefixes = (b":", b"data:", b"event:", b"id:", b"retry:")
    for chunk in _response_chunks(resp, deadline, progress):
        if (
            reasoning_deadline is not None
            and not content_parts
            and time.perf_counter() >= reasoning_deadline
        ):
            progress["reasoning_only_timeout"] = True
            raise TimeoutError("reasoning-only watchdog exceeded")
        pending += chunk
        if is_sse is None:
            if b"\xef\xbb\xbf".startswith(pending) and len(pending) < 3:
                continue
            pending = pending.removeprefix(b"\xef\xbb\xbf")
            prefix = pending.lstrip()
            if not prefix:
                continue
            # Some compatible gateways omit
            # Content-Type, or ignore stream=true and send a complete JSON body.
            if target_format == "openai_chat" and any(field.startswith(prefix) for field in sse_prefixes):
                continue  # a field name may be split across socket reads
            is_sse = target_format == "openai_chat" and prefix.startswith(sse_prefixes)
        if is_sse:
            while b"\n" in pending and not done:
                line, pending = pending.split(b"\n", 1)
                consume_line(line)
            if done:
                break
    if (
        reasoning_deadline is not None
        and not content_parts
        and time.perf_counter() >= reasoning_deadline
    ):
        progress["reasoning_only_timeout"] = True
        raise TimeoutError("reasoning-only watchdog exceeded")
    if not is_sse:
        return json.loads(pending.decode("utf-8"))
    if not done:
        if pending:
            consume_line(pending)
        consume_event()
    if not done and not finish_reason:
        raise _LLMTransientError("LLM 流式响应中断：未收到结束标记，不能使用未完成答案")
    return {
        "choices": [{"index": 0, "message": {
            "content": "".join(content_parts), "reasoning_content": "".join(reasoning_parts),
        }, "finish_reason": finish_reason}],
        "usage": usage,
    }


def _validate_llm_answer(res_json: Dict[str, Any], content: str, reasoning: str,
                         response_format: Optional[Dict[str, Any]], model: str) -> str:
    choices = res_json.get("choices") or [{}]
    finish = choices[0].get("finish_reason") or res_json.get("stop_reason")
    if finish in ("length", "max_tokens") or res_json.get("status") == "incomplete":
        raise _LLMTransientError(f"模型 {model} 输出被截断（{finish or 'incomplete'}），未获得完整答案")
    if not content:
        detail = "仅返回思考内容，没有最终答案" if reasoning else "返回空正文"
        raise _LLMTransientError(
            f"模型 {model} {detail}", reasoning_only=bool(reasoning)
        )
    if response_format and response_format.get("type") in ("json_object", "json_schema"):
        # Normalize fenced JSON for all callers and retry malformed/truncated
        # answers inside this chain. The unnormalized answer remains in the archive.
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE).strip()
        try:
            value = json.loads(text)
        except ValueError as exc:
            raise _LLMTransientError(f"模型 {model} 未返回有效 JSON 答案") from exc
        if response_format.get("type") == "json_object" and not isinstance(value, dict):
            raise _LLMTransientError(f"模型 {model} JSON 答案必须为对象")
        return text
    return content


def _adapt_chat_payload(payload: Dict[str, Any], error_body: str) -> Optional[Dict[str, Any]]:
    """Change only a named rejected parameter, preserving the other request settings."""
    low = error_body.lower()
    adapted = dict(payload)
    for key in (
        "stream_options", "response_format", "temperature", "reasoning_effort",
        "enable_thinking", "max_tokens", "stream",
    ):
        if key in payload and re.search(r"\b" + key + r"\b", low):
            adapted.pop(key)
            if key == "stream":
                adapted.pop("stream_options", None)
            return adapted
    return None
