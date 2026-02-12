"""Multi-turn self-improvement agent loop.

Mirrors the tool-loop pattern from pkm-bridge-server.py but uses meta-tools
for inspecting and modifying the system itself. Runs within a strict budget.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..tools.registry import ToolRegistry
from .budget import Budget
from .filesystem import ensure_pkm_structure, get_runs_dir
from .meta_tools import create_action_tools, create_inspection_tools
from .prompt import build_system_prompt, gather_run_stats

AGENT_MODEL = "claude-sonnet-4-5-20250929"

# Action tool names that consume the write budget
ACTION_TOOL_NAMES = frozenset({
    "write_skill", "delete_skill", "manage_rules",
    "propose_amendment", "write_memory",
})


class SelfImprovementAgent:
    """Multi-turn agent that inspects and improves the PKM system.

    Uses Claude with meta-tools in a loop, similar to the main /query endpoint
    but focused on system self-improvement rather than user queries.
    """

    def __init__(
        self,
        anthropic_client,
        logger: logging.Logger,
        config,
        *,
        max_turns: int = 15,
        max_actions: int = 10,
        max_input_tokens: int = 150_000,
        max_output_tokens: int = 20_000,
    ):
        self.client = anthropic_client
        self.logger = logger
        self.config = config
        self.org_dir = Path(config.org_dir).expanduser()
        self.system_prompt_path = (
            Path(__file__).parent.parent.parent / "config" / "system_prompt.txt"
        )

        self.default_budget_params = {
            "max_turns": max_turns,
            "max_actions": max_actions,
            "max_input_tokens": max_input_tokens,
            "max_output_tokens": max_output_tokens,
        }

        self.last_run_result: Optional[Dict[str, Any]] = None

    def _setup_tools(self) -> ToolRegistry:
        """Create a ToolRegistry with all meta-tools."""
        registry = ToolRegistry()

        for tool in create_inspection_tools(self.logger, self.org_dir, self.system_prompt_path):
            registry.register(tool)

        for tool in create_action_tools(self.logger, self.org_dir):
            registry.register(tool)

        return registry

    def _write_run_file(
        self,
        run_file: Path,
        budget: Budget,
        actions_log: List[str],
        agent_summary: str,
        error: Optional[str] = None,
    ) -> None:
        """Write the run log markdown file to .pkm/runs/."""
        now = datetime.utcnow()
        lines = [
            f"# Self-Improvement Run — {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            f"**Trigger**: {'manual' if self._current_trigger == 'manual' else 'scheduled'}",
            f"**Budget used**: {json.dumps(budget.summary())}",
            "",
        ]

        if error:
            lines.extend(["## Error", "", f"```\n{error}\n```", ""])

        if actions_log:
            lines.append("## Actions Taken")
            lines.append("")
            for action in actions_log:
                lines.append(f"- {action}")
            lines.append("")

        if agent_summary:
            lines.extend(["## Agent Summary", "", agent_summary, ""])

        run_file.write_text("\n".join(lines), encoding="utf-8")

    def _save_run_to_db(
        self,
        started_at: datetime,
        budget: Budget,
        actions_log: List[str],
        agent_summary: str,
        run_file_name: str,
        error: Optional[str] = None,
    ) -> None:
        """Save run metadata to AgentRunLog table."""
        try:
            from ..database import get_db
            from ..db_repository import AgentRunLogRepository

            db = get_db()
            try:
                AgentRunLogRepository.create(
                    db,
                    started_at=started_at,
                    completed_at=datetime.utcnow(),
                    trigger=self._current_trigger,
                    turns_used=budget.turns_used,
                    input_tokens=budget.input_tokens_used,
                    output_tokens=budget.output_tokens_used,
                    actions_summary=[{"description": a} for a in actions_log],
                    summary=agent_summary,
                    error=error,
                    run_file=run_file_name,
                )
            finally:
                db.close()
        except Exception as e:
            self.logger.warning(f"SI Agent: failed to save run to DB: {e}")

    def _collect_action_logs(self, registry: ToolRegistry) -> List[str]:
        """Collect action logs from all write tools."""
        logs: List[str] = []
        for tool_name in registry.list_tools():
            tool = registry.get_tool(tool_name)
            if hasattr(tool, "_run_log"):
                logs.extend(tool._run_log)
        return logs

    def run(self, trigger: str = "scheduled") -> Dict[str, Any]:
        """Execute the self-improvement agent.

        Args:
            trigger: 'scheduled' or 'manual'.

        Returns:
            Summary dict with run results.
        """
        self._current_trigger = trigger
        started_at = datetime.utcnow()
        self.logger.info(f"SI Agent: starting ({trigger})")

        result: Dict[str, Any] = {
            "started_at": started_at.isoformat(),
            "trigger": trigger,
            "error": None,
        }

        # Ensure .pkm/ directory structure exists
        ensure_pkm_structure(self.org_dir)

        # Set up budget
        budget = Budget(**self.default_budget_params)

        # Set up tools
        registry = self._setup_tools()

        # Gather run stats and build system prompt
        try:
            run_stats = gather_run_stats(self.org_dir)
        except Exception as e:
            self.logger.warning(f"SI Agent: failed to gather stats: {e}")
            run_stats = {}

        system_prompt = build_system_prompt(self.org_dir, budget, run_stats)

        # Initial message to kick off the agent
        messages: List[Dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    "Please review the PKM system's recent activity and make improvements. "
                    "Start by reading your memory from previous runs, then inspect feedback, "
                    "conversations, and tool logs. Act on what you find, and always save your "
                    "observations to memory before finishing."
                ),
            }
        ]

        tools = registry.get_anthropic_tools()
        agent_summary = ""

        try:
            # Agent loop
            while budget.can_continue:
                api_params = {
                    "model": AGENT_MODEL,
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "messages": messages,
                    "tools": tools,
                }

                response = self.client.messages.create(**api_params)

                # Track token usage
                input_tokens = getattr(response.usage, "input_tokens", 0)
                output_tokens = getattr(response.usage, "output_tokens", 0)
                budget.record_turn(input_tokens, output_tokens)

                self.logger.info(
                    f"SI Agent: turn {budget.turns_used}/{budget.max_turns} "
                    f"(tokens: {input_tokens}+{output_tokens})"
                )

                # If no tool use, we're done
                if response.stop_reason != "tool_use":
                    # Extract final text as agent summary
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
                    tool_input = block.input
                    self.logger.info(f"SI Agent: calling {tool_name}")

                    # Check action budget
                    is_action = tool_name in ACTION_TOOL_NAMES
                    if is_action and not budget.can_act:
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": (
                                f"Action budget exhausted "
                                f"({budget.actions_used}/{budget.max_actions}). "
                                "You can still use inspection tools "
                                "or finish with write_memory."
                            ),
                        })
                        continue

                    # Execute the tool
                    try:
                        result_text = registry.execute_tool(tool_name, tool_input)
                    except Exception as e:
                        result_text = f"Error executing {tool_name}: {e}"
                        self.logger.error(f"SI Agent: tool error: {e}")

                    if is_action:
                        budget.record_action()

                    # Ensure result is never empty
                    is_empty = not result_text or (
                        isinstance(result_text, str) and not result_text.strip()
                    )
                    if is_empty:
                        result_text = "[Empty result]"

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })

                # Build message content for the response (serialize like the main server)
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

            # Check if we stopped due to budget
            if not budget.can_continue:
                self.logger.info(f"SI Agent: stopped — {budget.stop_reason}")

        except Exception as e:
            result["error"] = str(e)
            self.logger.error(f"SI Agent: failed: {e}", exc_info=True)

        # Collect action logs from tools
        actions_log = self._collect_action_logs(registry)

        # Write run file
        now = datetime.utcnow()
        run_file_name = now.strftime("%Y-%m-%d-%H%M") + ".md"
        runs_dir = get_runs_dir(self.org_dir)
        run_file = runs_dir / run_file_name

        self._write_run_file(
            run_file, budget, actions_log, agent_summary, result.get("error")
        )

        # Save to DB
        self._save_run_to_db(
            started_at, budget, actions_log, agent_summary,
            run_file_name, result.get("error"),
        )

        # Mark feedback as processed
        try:
            from ..database import get_db
            from ..db_repository import QueryFeedbackRepository

            db = get_db()
            try:
                unprocessed = QueryFeedbackRepository.get_unprocessed(db)
                if unprocessed:
                    feedback_ids = [fb.id for fb in unprocessed]
                    QueryFeedbackRepository.mark_processed(db, feedback_ids)
                    self.logger.info(f"SI Agent: marked {len(feedback_ids)} feedback as processed")
            finally:
                db.close()
        except Exception as e:
            self.logger.warning(f"SI Agent: failed to mark feedback: {e}")

        # Populate result
        result["completed_at"] = datetime.utcnow().isoformat()
        result["budget"] = budget.summary()
        result["actions"] = actions_log
        result["summary"] = agent_summary[:500] if agent_summary else ""
        result["run_file"] = run_file_name

        self.last_run_result = result
        self.logger.info(
            f"SI Agent: complete — {budget.turns_used} turns, "
            f"{len(actions_log)} actions, "
            f"{budget.input_tokens_used}+{budget.output_tokens_used} tokens"
        )

        return result
