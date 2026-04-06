"""Multi-LLM adapter using LiteLLM for non-Anthropic models.

Routes Claude models directly to the Anthropic SDK (preserving caching,
thinking, and native response format). All other models go through LiteLLM
and have their responses normalized to Anthropic-like objects so existing
call sites work unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

import litellm

from pkm_bridge.models import is_anthropic, supports_tools

logger = logging.getLogger(__name__)

# Suppress LiteLLM's verbose default logging
litellm.suppress_debug_info = True


# ---------------------------------------------------------------------------
# Response wrappers — mimic Anthropic SDK response shapes for non-Anthropic
# ---------------------------------------------------------------------------


@dataclass
class ContentBlock:
    """A single content block in a response (text or tool_use).

    Provides model_dump() for compatibility with serialize_message_content(),
    which uses that method to convert Anthropic SDK response objects to dicts.
    """
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        """Return a dict representation, matching the Anthropic SDK convention."""
        return asdict(self)


@dataclass
class Usage:
    """Token usage, matching Anthropic's attribute names."""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class LLMResponse:
    """Normalized response matching Anthropic's response shape."""
    content: list[ContentBlock] = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: Usage = field(default_factory=Usage)
    model: str = ""

    # Store the raw LiteLLM response for cost calculation
    _raw_response: Any = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# Format translation helpers
# ---------------------------------------------------------------------------


def _anthropic_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Convert Anthropic tool format to OpenAI function-calling format."""
    openai_tools = []
    for tool in tools:
        t = {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {}),
            },
        }
        openai_tools.append(t)
    return openai_tools


def _anthropic_messages_to_openai(
    messages: list[dict], system: str | list | None = None
) -> list[dict]:
    """Convert Anthropic-format message history to OpenAI format.

    Handles:
    - System prompt (string or structured blocks) → system message
    - Text content blocks → plain string content
    - tool_use blocks → tool_calls on assistant message
    - tool_result blocks → tool role messages
    """
    openai_msgs: list[dict] = []

    # System prompt
    if system:
        if isinstance(system, str):
            sys_text = system
        elif isinstance(system, list):
            # Anthropic structured blocks — concatenate text fields
            sys_text = "\n\n".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in system
            )
        else:
            sys_text = str(system)
        openai_msgs.append({"role": "system", "content": sys_text})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")

        if role == "assistant":
            openai_msg = _translate_assistant_message(content)
            openai_msgs.append(openai_msg)

        elif role == "user":
            if isinstance(content, list):
                # Split into tool_result blocks and other content blocks
                tool_results = []
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_results.append(block)
                    elif isinstance(block, dict):
                        text_parts.append(block.get("text", ""))
                    else:
                        text_parts.append(str(block))

                # Emit tool messages (OpenAI requires these directly after
                # the assistant message that produced the tool_calls)
                for tr in tool_results:
                    tr_content = tr.get("content", "")
                    # tool_result content can be a list of blocks; flatten to string
                    if isinstance(tr_content, list):
                        tr_content = "\n".join(
                            b.get("text", "") if isinstance(b, dict) else str(b)
                            for b in tr_content
                        )
                    openai_msgs.append({
                        "role": "tool",
                        "tool_call_id": tr.get("tool_use_id", ""),
                        "content": tr_content,
                    })

                # Emit any non-tool-result text as a user message
                combined_text = "\n".join(t for t in text_parts if t)
                if combined_text:
                    openai_msgs.append({"role": "user", "content": combined_text})
            else:
                openai_msgs.append({"role": "user", "content": content or ""})
        else:
            openai_msgs.append({"role": role, "content": content or ""})

    return openai_msgs


def _translate_assistant_message(content: Any) -> dict:
    """Translate an assistant message's content to OpenAI format."""
    if isinstance(content, str):
        return {"role": "assistant", "content": content}

    if not isinstance(content, list):
        return {"role": "assistant", "content": str(content)}

    text_parts = []
    tool_calls = []

    for block in content:
        if not isinstance(block, dict):
            text_parts.append(str(block))
            continue
        btype = block.get("type", "")
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {})),
                },
            })
        # Skip thinking blocks

    msg: dict[str, Any] = {"role": "assistant", "content": "\n".join(text_parts) or None}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _openai_response_to_llm_response(response: Any) -> LLMResponse:
    """Convert a LiteLLM/OpenAI response to our Anthropic-shaped LLMResponse."""
    choice = response.choices[0]
    message = choice.message

    content_blocks: list[ContentBlock] = []

    # Text content
    if message.content:
        content_blocks.append(ContentBlock(type="text", text=message.content))

    # Tool calls
    if message.tool_calls:
        for tc in message.tool_calls:
            try:
                args = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                args = {}
            content_blocks.append(ContentBlock(
                type="tool_use",
                id=tc.id,
                name=tc.function.name,
                input=args,
            ))

    # Map finish_reason
    finish = choice.finish_reason or "stop"
    stop_reason_map = {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "length": "max_tokens",
        "content_filter": "end_turn",
    }
    stop_reason = stop_reason_map.get(finish, "end_turn")

    # Usage
    raw_usage = getattr(response, "usage", None)
    usage = Usage(
        input_tokens=getattr(raw_usage, "prompt_tokens", 0) if raw_usage else 0,
        output_tokens=getattr(raw_usage, "completion_tokens", 0) if raw_usage else 0,
    )

    return LLMResponse(
        content=content_blocks if content_blocks else [ContentBlock(type="text", text="")],
        stop_reason=stop_reason,
        usage=usage,
        model=getattr(response, "model", ""),
        _raw_response=response,
    )


# ---------------------------------------------------------------------------
# Main client
# ---------------------------------------------------------------------------


class LLMClient:
    """Unified LLM client: Anthropic-native for Claude, LiteLLM for everything else.

    All responses are returned in Anthropic's response shape so callers
    don't need to know which provider was used.
    """

    def __init__(self, anthropic_client: Any, config: Any | None = None):
        self.anthropic_client = anthropic_client
        self.config = config

    def complete(
        self,
        *,
        model: str,
        messages: list[dict],
        system: str | list | None = None,
        tools: list[dict] | None = None,
        max_tokens: int = 4096,
        thinking: dict | None = None,
        extra_headers: dict | None = None,
    ) -> Any:
        """Send a completion request, routing to the appropriate provider.

        For Claude models: passes through to the Anthropic SDK directly.
        For all others: translates to OpenAI format, calls LiteLLM, and
        wraps the response to match Anthropic's shape.

        Returns an object with .content, .stop_reason, .usage matching
        the Anthropic SDK response format.
        """
        if is_anthropic(model):
            return self._complete_anthropic(
                model=model,
                messages=messages,
                system=system,
                tools=tools,
                max_tokens=max_tokens,
                thinking=thinking,
                extra_headers=extra_headers,
            )
        else:
            return self._complete_litellm(
                model=model,
                messages=messages,
                system=system,
                tools=tools,
                max_tokens=max_tokens,
            )

    def _complete_anthropic(
        self,
        *,
        model: str,
        messages: list[dict],
        system: str | list | None,
        tools: list[dict] | None,
        max_tokens: int,
        thinking: dict | None,
        extra_headers: dict | None,
    ) -> Any:
        """Direct Anthropic SDK call — zero translation overhead."""
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system is not None:
            params["system"] = system
        if tools:
            params["tools"] = tools
        if extra_headers:
            params["extra_headers"] = extra_headers
        if thinking:
            params["thinking"] = thinking

        return self.anthropic_client.messages.create(**params)

    def _complete_litellm(
        self,
        *,
        model: str,
        messages: list[dict],
        system: str | list | None,
        tools: list[dict] | None,
        max_tokens: int,
    ) -> LLMResponse:
        """LiteLLM call with format translation."""
        # Translate messages (includes system prompt injection)
        openai_messages = _anthropic_messages_to_openai(messages, system=system)

        # Cap output tokens for non-Anthropic models to leave room for context
        capped_max_tokens = min(max_tokens, 4096)

        params: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": capped_max_tokens,
        }

        # Translate and add tools if model supports them
        if tools and supports_tools(model):
            params["tools"] = _anthropic_tools_to_openai(tools)
        elif tools:
            logger.info(f"Model {model} may not support tools — skipping tool params")

        logger.info(f"LiteLLM call to {model} (max_tokens={capped_max_tokens})")
        response = litellm.completion(**params)
        return _openai_response_to_llm_response(response)

    def get_completion_cost(self, response: Any, model: str) -> float | None:
        """Get cost for a completion. Returns None if unknown.

        For Anthropic models, callers should use models.get_anthropic_cost() instead
        since it handles cache tokens. This method is for non-Anthropic responses.
        """
        if isinstance(response, LLMResponse) and response._raw_response:
            try:
                return litellm.completion_cost(completion_response=response._raw_response)
            except Exception:
                return None
        return None
