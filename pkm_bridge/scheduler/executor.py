"""Task executor — runs a single scheduled task through a Claude tool loop.

Follows the same pattern as self_improvement/agent.py but is parameterised
by the ScheduledTask row (prompt, budget, allowed tools).
"""

import logging
from typing import Any, Dict, List, Optional

from ..models import get_role_model, supports_caching
from ..self_improvement.budget import Budget


class TaskExecutor:
    """Run a scheduled task by sending its prompt to an LLM with tools."""

    def __init__(
        self,
        llm_client,
        tool_registry,
        logger: logging.Logger,
        system_prompt: str = "",
    ):
        self.client = llm_client
        self.tool_registry = tool_registry
        self.logger = logger
        self.system_prompt = system_prompt

    def execute(
        self,
        prompt: str,
        *,
        max_turns: int = 10,
        max_input_tokens: int = 200_000,
        max_output_tokens: int = 10_000,
        tools_allowed: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Run the prompt through a Claude tool loop.

        Args:
            prompt: The user message to send to Claude.
            max_turns: Maximum API round-trips.
            max_input_tokens: Input token budget.
            max_output_tokens: Output token budget.
            tools_allowed: Restrict to these tool names (None = all).

        Returns:
            Dict with keys: summary, input_tokens, output_tokens, turns_used,
                            error (str|None).
        """
        budget = Budget(
            max_turns=max_turns,
            max_actions=999,  # no action limit for scheduled tasks
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
        )

        # Build tool list (optionally filtered)
        if tools_allowed:
            tools = [
                t for t in self.tool_registry.get_anthropic_tools()
                if t["name"] in tools_allowed
            ]
        else:
            tools = self.tool_registry.get_anthropic_tools()

        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": prompt}
        ]

        agent_summary = ""

        model = get_role_model("scheduler")
        # Cache the static parts (system prompt + tools) so multi-turn loops
        # only pay full price for the system/tools on the first call.
        cache_enabled = supports_caching(model)
        if cache_enabled and self.system_prompt:
            system_param: Any = [
                {
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        else:
            system_param = self.system_prompt
        if cache_enabled and tools:
            tools[-1]["cache_control"] = {"type": "ephemeral"}

        try:
            while budget.can_continue:
                api_params: Dict[str, Any] = {
                    "model": model,
                    "max_tokens": 4096,
                    "messages": messages,
                }
                if system_param:
                    api_params["system"] = system_param
                if tools:
                    api_params["tools"] = tools
                if cache_enabled:
                    api_params["extra_headers"] = {
                        "anthropic-beta": "prompt-caching-2024-07-31"
                    }

                response = self.client.complete(**api_params)

                input_tokens = getattr(response.usage, "input_tokens", 0)
                output_tokens = getattr(response.usage, "output_tokens", 0)
                budget.record_turn(input_tokens, output_tokens)

                self.logger.info(
                    f"Scheduler executor: turn {budget.turns_used}/{budget.max_turns} "
                    f"(tokens: {input_tokens}+{output_tokens})"
                )

                # No tool use → done
                if response.stop_reason != "tool_use":
                    for block in response.content:
                        if getattr(block, "type", "") == "text":
                            agent_summary += block.text
                    break

                # Process tool calls
                tool_results = []
                for block in response.content:
                    if getattr(block, "type", None) != "tool_use":
                        continue

                    tool_name = block.name
                    self.logger.info(f"Scheduler executor: calling {tool_name}")

                    try:
                        result_text = self.tool_registry.execute_tool(
                            tool_name, block.input
                        )
                    except Exception as e:
                        result_text = f"Error executing {tool_name}: {e}"
                        self.logger.error(f"Scheduler executor: tool error: {e}")

                    if not result_text or (isinstance(result_text, str) and not result_text.strip()):
                        result_text = "[Empty result]"

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })

                # Build assistant message content
                response_content = []
                for block in response.content:
                    if getattr(block, "type", "") == "text":
                        response_content.append({"type": "text", "text": block.text})
                    elif getattr(block, "type", "") == "tool_use":
                        response_content.append({
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        })

                messages.append({"role": "assistant", "content": response_content})
                messages.append({"role": "user", "content": tool_results})

            if not budget.can_continue:
                self.logger.info(f"Scheduler executor: stopped — {budget.stop_reason}")

        except Exception as e:
            return {
                "summary": agent_summary[:1000] if agent_summary else "",
                "input_tokens": budget.input_tokens_used,
                "output_tokens": budget.output_tokens_used,
                "turns_used": budget.turns_used,
                "error": str(e),
            }

        return {
            "summary": agent_summary[:1000] if agent_summary else "",
            "input_tokens": budget.input_tokens_used,
            "output_tokens": budget.output_tokens_used,
            "turns_used": budget.turns_used,
            "error": None,
        }
