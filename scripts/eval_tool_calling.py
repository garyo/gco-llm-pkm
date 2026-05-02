#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "anthropic>=0.39.0",
#   "python-dotenv>=1.0.0",
#   "pyyaml>=6.0.2",
#   "sqlalchemy>=2.0.23",
#   "psycopg2-binary>=2.9.9",
#   "requests>=2.31.0",
#   "google-auth>=2.34.0",
#   "google-auth-oauthlib>=1.2.0",
#   "google-auth-httplib2>=0.2.0",
#   "google-api-python-client>=2.147.0",
#   "pgvector>=0.2.0",
#   "voyageai>=0.2.0",
#   "litellm>=1.50.0",
# ]
# ///
"""
Multi-model tool-calling testbed.

Runs a fixed set of scenarios end-to-end against the real PKM (org files,
TickTick, Gmail, Calendar, database) through the same LLMClient + ToolRegistry
the server uses. Dumps a per-run trace so you can compare how different models
plan tool calls, pick arguments, and recover from errors.

Usage:
  scripts/eval_tool_calling.py --model claude-haiku-4-5
  scripts/eval_tool_calling.py --model claude-haiku-4-5 --model openrouter/qwen/qwen3.6-plus
  scripts/eval_tool_calling.py --model openrouter/z-ai/glm-5.1 --scenario setlist-today
  scripts/eval_tool_calling.py --list-scenarios
  scripts/eval_tool_calling.py --list-models

Trace files go to traces/<timestamp>/<model-safe>__<scenario>.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Make sibling `pkm_bridge/` importable when running from scripts/
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from anthropic import Anthropic  # noqa: E402

from config.settings import Config  # noqa: E402
from pkm_bridge.llm import LLMClient  # noqa: E402
from pkm_bridge.models import get_available_models  # noqa: E402
from pkm_bridge.tools.base import BaseTool  # noqa: E402
from pkm_bridge.tools.registry import ToolRegistry  # noqa: E402
from pkm_bridge.tools.shell import ExecuteShellTool, WriteAndExecuteScriptTool  # noqa: E402
from pkm_bridge.tools.files import ListFilesTool  # noqa: E402
from pkm_bridge.tools.search_notes import SearchNotesTool  # noqa: E402
from pkm_bridge.tools.find_context import FindContextTool  # noqa: E402
from pkm_bridge.tools.open_file import OpenFileTool  # noqa: E402
from pkm_bridge.tools.skills import (  # noqa: E402
    SaveSkillTool, ListSkillsTool, UseSkillTool, NoteToSelfTool,
)
from pkm_bridge.tools.schedule_task import ScheduleTaskTool  # noqa: E402


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    id: str
    prompt: str
    expected_tools: list[str]   # pass if ANY of these is called at least once
    notes: str = ""


SCENARIOS: list[Scenario] = [
    Scenario(
        id="search-pkm-text",
        prompt="Find any notes in my PKM that mention 'sailboat'. Just show me a short summary.",
        expected_tools=["search_notes", "find_context", "semantic_search", "execute_shell"],
        notes="Plain-text search in org/logseq files.",
    ),
    Scenario(
        id="semantic-pkm",
        prompt="What have I been thinking about Gaussian splatting lately? Look in my notes.",
        expected_tools=["semantic_search", "search_notes", "find_context"],
        notes="Conceptual query — semantic_search is the right tool.",
    ),
    Scenario(
        id="search-gmail",
        prompt="Search my gmail for anything recent mentioning 'rehearsal' and summarize the top hit.",
        expected_tools=["google_gmail"],
        notes="Gmail tool. Requires google_gmail_oauth to be configured.",
    ),
    Scenario(
        id="list-ticktick",
        prompt="What's in my TickTick inbox right now? Just the task titles.",
        expected_tools=["ticktick_query"],
        notes="TickTick tool. Requires ticktick_oauth to be configured.",
    ),
    Scenario(
        id="calendar-today",
        prompt="What's on my calendar today?",
        expected_tools=["google_calendar"],
        notes="Google Calendar. Requires google_oauth to be configured.",
    ),
    Scenario(
        id="append-note",
        prompt=(
            "Append this note to today's org journal entry: "
            "'pkm-eval marker: tool-calling testbed ran'. "
            "Use whatever approach makes sense."
        ),
        expected_tools=["execute_shell", "write_and_execute_script"],
        notes="File mutation — creates a grep-able marker in today's journal.",
    ),
    Scenario(
        id="open-page",
        prompt="Open my main projects page in the editor — look for something like projects.org.",
        expected_tools=["open_file", "list_files", "search_notes"],
        notes="Open a file in the web editor (emits an SSE event; ignored in eval).",
    ),
    Scenario(
        id="setlist-today",
        prompt="Start a setlist for today.",
        expected_tools=[
            "search_notes", "find_context", "semantic_search", "list_files",
            "execute_shell", "write_and_execute_script", "open_file",
        ],
        notes=(
            "Deliberately underspecified. Good model should search for prior setlists, "
            "figure out the conventional format/location, and create a new one."
        ),
    ),
]


# ---------------------------------------------------------------------------
# Trace data model
# ---------------------------------------------------------------------------

@dataclass
class TraceTurn:
    turn: int
    kind: str                  # "llm" | "tool" | "error"
    text: str = ""
    tool_name: str = ""
    tool_input: dict = field(default_factory=dict)
    tool_output_head: str = ""
    tool_output_len: int = 0
    tool_error: bool = False
    duration_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""


@dataclass
class ScenarioRun:
    model: str
    scenario_id: str
    prompt: str
    expected_tools: list[str]
    tools_called: list[str] = field(default_factory=list)
    turns: list[TraceTurn] = field(default_factory=list)
    final_text: str = ""
    total_duration_s: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    passed: bool = False
    status: str = ""           # "ok" | "capped" | "error"
    error: str = ""


# ---------------------------------------------------------------------------
# Dry-run support — intercept mutations without stopping the model's plan
# ---------------------------------------------------------------------------

# Patterns in an execute_shell command that indicate a file mutation.
# Coarse but deliberately over-broad: in dry-run we prefer false positives
# (skipping a read by mistake) over false negatives (silently mutating files).
_SHELL_MUTATION_PATTERNS = [
    r"(?<![&0-9])>",      # stdout redirection (> or >>), not 2>&1/>&2/1>&2
    r"\btee\b",
    r"\bsed\s+-i\b",
    r"\brm\b",
    r"\brmdir\b",
    r"\bmv\b",
    r"\bcp\b",
    r"\btouch\b",
    r"\bmkdir\b",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bln\s+-",
    r"\brsync\b",
    r"\bgit\s+(add|commit|push|rm|mv|reset|checkout|clean)\b",
]
_SHELL_MUTATION_RE = re.compile("|".join(_SHELL_MUTATION_PATTERNS))


def _shell_is_mutation(params: dict) -> bool:
    cmd = params.get("command", "") or ""
    return bool(_SHELL_MUTATION_RE.search(cmd))


class DryRunTool(BaseTool):
    """Wraps another tool; returns a fake success string instead of executing
    when `is_mutation(params)` is truthy. Otherwise delegates through."""

    def __init__(self, wrapped: BaseTool, is_mutation=lambda _p: True):
        super().__init__(wrapped.logger)
        self._wrapped = wrapped
        self._is_mutation = is_mutation

    @property
    def name(self) -> str:
        return self._wrapped.name

    @property
    def description(self) -> str:
        return self._wrapped.description

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._wrapped.input_schema

    def execute(self, params: dict[str, Any], context: dict[str, Any] | None = None) -> str:
        if self._is_mutation(params):
            preview = json.dumps(params)[:300]
            return f"[DRY RUN] would execute {self.name}({preview}) — no side effect"
        return self._wrapped.execute(params, context=context or {})


def apply_dry_run(registry: ToolRegistry, logger: logging.Logger) -> None:
    """Replace mutating tools in the registry with dry-run wrappers."""
    # Always dry-run these — they write by definition
    ALWAYS_DRY = ("write_and_execute_script", "save_skill", "schedule_task")
    for name in ALWAYS_DRY:
        try:
            tool = registry.get_tool(name)
            registry.register(DryRunTool(tool, is_mutation=lambda _p: True))
        except KeyError:
            pass
    # Shell: pattern-detect writes
    try:
        shell = registry.get_tool("execute_shell")
        registry.register(DryRunTool(shell, is_mutation=_shell_is_mutation))
    except KeyError:
        pass
    logger.info("Dry-run mode: mutating tool calls will be stubbed")


# ---------------------------------------------------------------------------
# Tool registry builder — mirrors pkm-bridge-server.py, minus Flask/scheduler
# ---------------------------------------------------------------------------

def build_registry(config: Config, logger: logging.Logger) -> ToolRegistry:
    """Build a tool registry with every tool that the server would register,
    subject to which provider keys are present. Optional integrations silently
    drop out if not configured."""
    registry = ToolRegistry()

    # Shell + scripting
    registry.register(ExecuteShellTool(
        logger, config.dangerous_patterns, config.org_dir, config.logseq_dir
    ))
    registry.register(WriteAndExecuteScriptTool(
        logger, config.dangerous_patterns, config.org_dir, config.logseq_dir
    ))

    # File + search
    registry.register(ListFilesTool(logger, config.org_dir, config.logseq_dir))
    registry.register(SearchNotesTool(logger, config.org_dir, config.logseq_dir))
    registry.register(FindContextTool(logger, config.org_dir, config.logseq_dir))
    registry.register(OpenFileTool(logger, config.org_dir, config.logseq_dir))

    # TickTick (optional)
    try:
        from pkm_bridge.ticktick_oauth import TickTickOAuth
        from pkm_bridge.tools.ticktick import TickTickTool
        ticktick_oauth = TickTickOAuth()
        registry.register(TickTickTool(logger, ticktick_oauth))
        logger.info("TickTick tool registered")
    except (ValueError, ImportError) as e:
        logger.info(f"TickTick not configured: {e}")

    # Google Calendar (optional)
    try:
        from pkm_bridge.google_oauth import GoogleOAuth
        from pkm_bridge.tools.google_calendar import GoogleCalendarTool
        google_oauth = GoogleOAuth()
        registry.register(GoogleCalendarTool(logger, google_oauth))
        logger.info("Google Calendar tool registered")
    except (ValueError, ImportError) as e:
        logger.info(f"Google Calendar not configured: {e}")

    # Gmail (optional)
    try:
        from pkm_bridge.google_oauth import GoogleOAuth
        from pkm_bridge.tools.google_gmail import GoogleGmailTool
        gmail_oauth = GoogleOAuth(
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            redirect_uri_env="GOOGLE_GMAIL_REDIRECT_URI",
        )
        registry.register(GoogleGmailTool(logger, gmail_oauth))
        logger.info("Gmail tool registered")
    except (ValueError, ImportError) as e:
        logger.info(f"Gmail not configured: {e}")

    # Semantic search (optional — needs Voyage + DB)
    try:
        voyage_key = os.getenv("VOYAGE_API_KEY")
        if voyage_key:
            from pkm_bridge.context_retriever import ContextRetriever
            from pkm_bridge.embeddings.voyage_client import VoyageClient
            from pkm_bridge.tools.semantic_search import SemanticSearchTool
            voyage = VoyageClient(api_key=voyage_key)
            retriever = ContextRetriever(voyage)
            registry.register(SemanticSearchTool(logger, retriever))
            logger.info("Semantic search tool registered")
    except Exception as e:
        logger.info(f"Semantic search not configured: {e}")

    # Skills + notes + scheduling
    registry.register(SaveSkillTool(logger, config.org_dir, config.dangerous_patterns))
    registry.register(ListSkillsTool(logger, config.org_dir))
    registry.register(UseSkillTool(logger, config.org_dir, config.dangerous_patterns))
    registry.register(NoteToSelfTool(logger))
    registry.register(ScheduleTaskTool(logger))

    return registry


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _text_from_response(response: Any) -> str:
    """Extract concatenated text from an LLMResponse or Anthropic response."""
    parts = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            parts.append(getattr(block, "text", "") or "")
    return "".join(parts)


def run_scenario(
    *,
    llm_client: LLMClient,
    registry: ToolRegistry,
    model: str,
    scenario: Scenario,
    system_prompt: str | list,
    max_turns: int,
    logger: logging.Logger,
) -> ScenarioRun:
    """Drive one scenario through the real tool loop. Captures a trace."""
    result = ScenarioRun(
        model=model,
        scenario_id=scenario.id,
        prompt=scenario.prompt,
        expected_tools=scenario.expected_tools,
    )
    start_all = time.time()
    history: list[dict] = [{"role": "user", "content": scenario.prompt}]
    tools = registry.get_anthropic_tools()

    try:
        turn_idx = 0
        t0 = time.time()
        response = llm_client.complete(
            model=model,
            messages=history,
            system=system_prompt,
            tools=tools,
            max_tokens=4096,
        )
        turn_idx += 1
        usage = getattr(response, "usage", None)
        in_tok = getattr(usage, "input_tokens", 0) if usage else 0
        out_tok = getattr(usage, "output_tokens", 0) if usage else 0
        result.total_input_tokens += in_tok
        result.total_output_tokens += out_tok
        result.turns.append(TraceTurn(
            turn=turn_idx, kind="llm",
            text=_text_from_response(response)[:2000],
            duration_s=round(time.time() - t0, 3),
            input_tokens=in_tok, output_tokens=out_tok,
            stop_reason=getattr(response, "stop_reason", "") or "",
        ))

        while getattr(response, "stop_reason", "") == "tool_use" and turn_idx < max_turns:
            tool_results = []
            assistant_blocks = []
            for block in response.content:
                btype = getattr(block, "type", None)
                if btype == "tool_use":
                    tname = getattr(block, "name", "")
                    raw_input = getattr(block, "input", {}) or {}
                    # Defensive: some models emit non-dict tool args. Don't crash
                    # the scenario — record what we got and let the tool error.
                    tinput = raw_input if isinstance(raw_input, dict) else {}
                    tid = getattr(block, "id", "")
                    result.tools_called.append(tname)
                    t_tool = time.time()
                    try:
                        out = registry.execute_tool(tname, tinput, context={"session_id": None})
                    except Exception as e:
                        out = f"❌ Tool crashed: {e}"
                    is_err = isinstance(out, str) and out.startswith("❌")
                    result.turns.append(TraceTurn(
                        turn=turn_idx, kind="tool",
                        tool_name=tname,
                        tool_input=tinput if isinstance(raw_input, dict) else {"_raw": str(raw_input)[:200]},
                        tool_output_head=(out or "")[:1000],
                        tool_output_len=len(out or ""),
                        tool_error=is_err,
                        duration_s=round(time.time() - t_tool, 3),
                    ))
                    # Keep tool_result content short-ish for cheaper follow-up
                    tr_content = out if len(out) <= 8000 else out[:8000] + "\n\n[... truncated for eval]"
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tid,
                        "content": tr_content,
                    })
                    assistant_blocks.append({
                        "type": "tool_use", "id": tid, "name": tname, "input": dict(tinput),
                    })
                elif btype == "text":
                    txt = getattr(block, "text", "") or ""
                    if txt.strip():
                        assistant_blocks.append({"type": "text", "text": txt})

            history.append({"role": "assistant", "content": assistant_blocks})
            history.append({"role": "user", "content": tool_results})

            t0 = time.time()
            response = llm_client.complete(
                model=model,
                messages=history,
                system=system_prompt,
                tools=tools,
                max_tokens=4096,
            )
            turn_idx += 1
            usage = getattr(response, "usage", None)
            in_tok = getattr(usage, "input_tokens", 0) if usage else 0
            out_tok = getattr(usage, "output_tokens", 0) if usage else 0
            result.total_input_tokens += in_tok
            result.total_output_tokens += out_tok
            result.turns.append(TraceTurn(
                turn=turn_idx, kind="llm",
                text=_text_from_response(response)[:2000],
                duration_s=round(time.time() - t0, 3),
                input_tokens=in_tok, output_tokens=out_tok,
                stop_reason=getattr(response, "stop_reason", "") or "",
            ))

        result.final_text = _text_from_response(response)
        if turn_idx >= max_turns and getattr(response, "stop_reason", "") == "tool_use":
            result.status = "capped"
        else:
            result.status = "ok"

    except Exception as e:
        logger.error(f"[{model}/{scenario.id}] runner error: {e}")
        result.turns.append(TraceTurn(
            turn=len(result.turns) + 1, kind="error",
            text=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
        ))
        result.status = "error"
        result.error = f"{type(e).__name__}: {e}"

    result.total_duration_s = round(time.time() - start_all, 3)
    result.passed = any(t in result.expected_tools for t in result.tools_called)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _safe_name(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", s).strip("_")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", action="append", default=None,
                   help="Model ID to test (repeatable). Omit to see available models.")
    p.add_argument("--scenario", action="append", default=None,
                   help="Scenario id(s) to run (repeatable). Default: all.")
    p.add_argument("--max-turns", type=int, default=8,
                   help="Max LLM turns per scenario (safety cap). Default: 8.")
    p.add_argument("--dry-run", action="store_true",
                   help="Stub mutating tool calls (shell writes, script execution, "
                        "save_skill, schedule_task) instead of letting them run. "
                        "Reads still execute so the model sees real data.")
    p.add_argument("--out-dir", default=None,
                   help="Directory for trace dumps. Default: traces/<timestamp>/.")
    p.add_argument("--list-models", action="store_true", help="List available models and exit.")
    p.add_argument("--list-scenarios", action="store_true", help="List scenarios and exit.")
    p.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging.")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # Quiet down the noisier libraries unless -v
    if not args.verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logger = logging.getLogger("eval")

    if args.list_scenarios:
        for s in SCENARIOS:
            print(f"  {s.id:20s}  {s.prompt}")
            if s.notes:
                print(f"  {'':20s}  ({s.notes})")
        return 0

    # Load .env (then .env.local as overrides) before checking provider keys.
    # Differs slightly from Config(), which picks one or the other — here we
    # want the union so eval sees every key the user has anywhere.
    from dotenv import load_dotenv
    if Path(".env").exists():
        load_dotenv(".env")
    if Path(".env.local").exists():
        load_dotenv(".env.local", override=True)

    available = get_available_models()
    if args.list_models:
        for m in available:
            print(f"  {m['id']:50s}  {m['name']}  [{m['provider']}/{m['tier']}]")
        return 0

    if not args.model:
        print("No --model given. Available models:")
        for m in available:
            print(f"  {m['id']:50s}  {m['name']}  [{m['provider']}/{m['tier']}]")
        print("\nExample: scripts/eval_tool_calling.py --model claude-haiku-4-5 --scenario search-pkm-text")
        return 2

    # Filter scenarios
    scenarios = SCENARIOS
    if args.scenario:
        wanted = set(args.scenario)
        scenarios = [s for s in SCENARIOS if s.id in wanted]
        unknown = wanted - {s.id for s in SCENARIOS}
        if unknown:
            print(f"Unknown scenario id(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2

    # Setup
    config = Config()

    # DB is optional for most tools but needed for OAuth-backed ones and
    # semantic_search. If init fails, continue — tools that need it will error
    # on invocation, which is fine (the trace will show the error).
    try:
        from pkm_bridge.database import init_db
        init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.warning(f"Database init failed (some tools may error): {e}")

    anthropic = Anthropic(api_key=config.anthropic_api_key)
    llm_client = LLMClient(anthropic_client=anthropic, config=config)
    registry = build_registry(config, logger)
    if args.dry_run:
        apply_dry_run(registry, logger)
    logger.info(f"Registered {len(registry)} tools: {', '.join(registry.list_tools())}")

    # Structured system prompt blocks — same shape the server sends, so the
    # final block with today's date/time in the user's timezone is included.
    # The LLMClient accepts str | list; LiteLLM adapter concatenates blocks.
    tz_str = config.timezone.key if config.timezone is not None else None
    system_prompt = config.get_system_prompt_blocks(user_timezone=tz_str)

    out_dir = Path(args.out_dir) if args.out_dir else (
        REPO_ROOT / "traces" / datetime.now().strftime("%Y%m%d-%H%M%S")
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Trace dir: {out_dir}")

    # Run!
    runs: list[ScenarioRun] = []
    for model in args.model:
        for scenario in scenarios:
            print(f"\n=== [{model}] {scenario.id} ===")
            print(f"    prompt: {scenario.prompt}")
            run = run_scenario(
                llm_client=llm_client, registry=registry,
                model=model, scenario=scenario,
                system_prompt=system_prompt, max_turns=args.max_turns,
                logger=logger,
            )
            runs.append(run)

            tools_summary = ", ".join(run.tools_called) or "(none)"
            mark = "✅" if run.passed else ("⚠️ " if run.status == "capped" else "❌")
            print(
                f"    {mark} status={run.status}  turns={len(run.turns)}  "
                f"tools=[{tools_summary}]  "
                f"tokens={run.total_input_tokens}→{run.total_output_tokens}  "
                f"time={run.total_duration_s}s"
            )
            if run.status == "error":
                print(f"    error: {run.error}")
            if run.final_text:
                final_head = run.final_text.strip().splitlines()[0][:200] if run.final_text.strip() else ""
                print(f"    final: {final_head}")

            # Dump full trace
            trace_path = out_dir / f"{_safe_name(model)}__{scenario.id}.json"
            trace_path.write_text(json.dumps(asdict(run), indent=2, default=str), encoding="utf-8")

    # Summary table
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"{'model':42s} {'scenario':22s} {'status':7s} {'pass':5s} {'turns':5s} {'tools':6s} {'in→out tok':12s} {'time':6s}")
    for r in runs:
        print(
            f"{r.model[:42]:42s} {r.scenario_id[:22]:22s} {r.status:7s} "
            f"{('yes' if r.passed else 'no'):5s} {len(r.turns):5d} "
            f"{len(r.tools_called):6d} {f'{r.total_input_tokens}→{r.total_output_tokens}':12s} "
            f"{r.total_duration_s:6.1f}"
        )
    print(f"\nTraces: {out_dir}")
    return 0 if all(r.status != "error" for r in runs) else 1


if __name__ == "__main__":
    sys.exit(main())
