"""Task executor — runs a single scheduled task through a Claude tool loop.

Follows the same pattern as self_improvement/agent.py but is parameterised
by the ScheduledTask row (prompt, budget, allowed tools).
"""

import logging
from typing import Any, Dict, List, Optional

from ..llm import AGENT_TURN_MAX_TOKENS, recover_from_max_tokens, response_cost
from ..models import TokenUsage, get_role_model, supports_caching
from ..self_improvement.agent import mark_last_message_for_cache
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
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run the prompt through a Claude tool loop.

        Args:
            prompt: The user message to send to Claude.
            max_turns: Maximum API round-trips.
            max_input_tokens: Billable input token budget (see Budget).
            max_output_tokens: Output token budget.
            tools_allowed: Restrict to these tool names (None = all).
            model: Model override (None = the scheduler role default).

        Returns:
            Dict with keys: summary, turns_used, input_tokens (uncached),
                            output_tokens, cache_write_tokens, cache_read_tokens,
                            cost_usd, error (str|None).
        """
        model = model or get_role_model("scheduler")
        budget = Budget(
            max_turns=max_turns,
            max_actions=999,  # no action limit for scheduled tasks
            max_input_tokens=max_input_tokens,
            max_output_tokens=max_output_tokens,
            model=model,
        )

        # Build tool list (optionally filtered)
        if tools_allowed:
            tools = [
                t for t in self.tool_registry.get_anthropic_tools() if t["name"] in tools_allowed
            ]
        else:
            tools = self.tool_registry.get_anthropic_tools()

        messages: List[Dict[str, Any]] = [{"role": "user", "content": prompt}]

        agent_summary = ""

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

        last_stop = None
        error: Optional[str] = None
        try:
            while budget.can_continue:
                # Move the cache breakpoint to the tail of the growing history
                # so the large tool_result blocks are re-sent from cache.
                if cache_enabled:
                    mark_last_message_for_cache(messages)

                api_params: Dict[str, Any] = {
                    "model": model,
                    "max_tokens": AGENT_TURN_MAX_TOKENS,
                    "messages": messages,
                }
                if system_param:
                    api_params["system"] = system_param
                if tools:
                    api_params["tools"] = tools

                response = self.client.complete(**api_params)

                turn = TokenUsage.from_response(response)
                turn_cost = response_cost(model, response)
                budget.record_turn(turn, turn_cost)

                self.logger.info(
                    f"Scheduler executor: turn {budget.turns_used}/{budget.max_turns} "
                    f"(tokens in/out: {turn.input_tokens}/{turn.output_tokens}, cache "
                    f"write/read: {turn.cache_write_tokens}/{turn.cache_read_tokens}, "
                    f"${turn_cost:.4f})"
                )

                last_stop = response.stop_reason
                if last_stop == "max_tokens":
                    self.logger.warning("Scheduler executor: response cut off at max_tokens")
                    messages.extend(recover_from_max_tokens(response, AGENT_TURN_MAX_TOKENS))
                    continue

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
                        result_text = self.tool_registry.execute_tool(tool_name, block.input)
                    except Exception as e:
                        result_text = f"Error executing {tool_name}: {e}"
                        self.logger.error(f"Scheduler executor: tool error: {e}")

                    if not result_text or (
                        isinstance(result_text, str) and not result_text.strip()
                    ):
                        result_text = "[Empty result]"

                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_text,
                        }
                    )

                # Build assistant message content
                response_content = []
                for block in response.content:
                    if getattr(block, "type", "") == "text":
                        response_content.append({"type": "text", "text": block.text})
                    elif getattr(block, "type", "") == "tool_use":
                        response_content.append(
                            {
                                "type": "tool_use",
                                "id": block.id,
                                "name": block.name,
                                "input": block.input,
                            }
                        )

                # Tell the model where it stands in its budgets so it can pace
                # itself — without this it explores until the loop is cut off
                # mid-task with its work unfiled and no final summary.
                turns_left = budget.turns_remaining
                out_left = budget.max_output_tokens - budget.output_tokens_used
                in_left = budget.max_input_tokens - budget.billable_input_tokens
                if (
                    turns_left <= 3
                    or out_left < budget.max_output_tokens * 0.2
                    or in_left < budget.max_input_tokens * 0.2
                ):
                    notice = (
                        f"[Scheduler: nearly out of budget ({turns_left} turn(s), "
                        f"~{max(out_left, 0)} output and ~{max(round(in_left), 0)} input "
                        f"tokens left). Stop exploring — take "
                        f"your concluding actions NOW, then reply with your final summary "
                        f"(a reply without tool calls ends the run cleanly).]"
                    )
                else:
                    notice = (
                        f"[Scheduler: turn {budget.turns_used} of {budget.max_turns}; "
                        f"output tokens {budget.output_tokens_used} of "
                        f"{budget.max_output_tokens}.]"
                    )
                tool_results.append({"type": "text", "text": notice})

                messages.append({"role": "assistant", "content": response_content})
                messages.append({"role": "user", "content": tool_results})

            if not budget.can_continue:
                self.logger.info(f"Scheduler executor: stopped — {budget.stop_reason}")
            if last_stop == "max_tokens":
                raise RuntimeError("Run ended with its last response cut off at max_tokens")
        except Exception as e:
            error = str(e)

        return {
            "summary": agent_summary[:1000] if agent_summary else "",
            "turns_used": budget.turns_used,
            "input_tokens": budget.usage.input_tokens,
            "output_tokens": budget.usage.output_tokens,
            "cache_write_tokens": budget.usage.cache_write_tokens,
            "cache_read_tokens": budget.usage.cache_read_tokens,
            "cost_usd": budget.cost_usd,
            "error": error,
        }
