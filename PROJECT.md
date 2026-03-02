# gco-pkm-llm: LLM Bridge for Personal Knowledge Management

## Project Overview

AI-powered bridge connecting Claude to org-mode and Logseq Personal Knowledge Management
files, Google Calendar, and TickTick. Provides natural language access to your entire
knowledge base from any device, via two complementary interfaces.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Docker Container                         │
│                                                                 │
│  ┌──────────────────────┐     ┌──────────────────────────────┐  │
│  │  Custom Web App :8000│     │  MCP Server :8001            │  │
│  │  (Flask + Astro UI)  │     │  (Claude.ai integration)     │  │
│  │  pkm.oberbrunner.com │     │  mcp.oberbrunner.com         │  │
│  └──────────┬───────────┘     └──────────────┬───────────────┘  │
│             │                                │                  │
│             └──────────┬─────────────────────┘                  │
│                        ▼                                        │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │               Shared Backend (pkm_bridge/)              │    │
│  │  Tools │ Auth │ Embeddings │ Voice │ Self-Improvement    │    │
│  └──────────────────────┬──────────────────────────────────┘    │
│                         ▼                                       │
│  ┌─────────────┐  ┌──────────┐  ┌────────────┐  ┌──────────┐  │
│  │ PostgreSQL   │  │ Org Files│  │ Logseq     │  │ External │  │
│  │ + pgvector   │  │ (r/w)    │  │ Files (r)  │  │ APIs     │  │
│  └─────────────┘  └──────────┘  └────────────┘  └──────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              ▲
                        Traefik Proxy
                     (HTTPS termination)
```

### Two Interfaces

| | Custom Web App | Claude.ai MCP |
|---|---|---|
| **URL** | pkm.oberbrunner.com | mcp.oberbrunner.com |
| **AI model** | Anthropic API (server key) | User's Claude subscription |
| **Frontend** | Astro + Editor SPA | Claude.ai desktop/mobile |
| **RAG** | Auto-injected embeddings | Must call semantic_search |
| **Voice** | VAD + Whisper | Claude.ai native |
| **Checkboxes** | Interactive (TickTick + file) | Not available |
| **System prompt** | `config/system_prompt.txt` | `config/system_prompt_mcp.txt` |

Both share: `pkm_bridge/` modules, PostgreSQL, tools, skills, learned rules.

## Technology Stack

- **Backend**: Python 3.11+ / Flask (PEP-723 inline deps, run with `uv`)
- **MCP Server**: Python MCP SDK on port 8001
- **Frontend**: Astro 5 + TypeScript + Tailwind CSS 4 (built with Bun)
- **Editor**: Vite + TypeScript standalone SPA (`frontend-editor/`)
- **Database**: PostgreSQL 16 + pgvector (semantic embeddings via Voyage AI)
- **AI**: Anthropic Claude API (tools/conversation), Voyage AI (embeddings)
- **Voice**: Client-side VAD (`@ricky0123/vad-web`) + Groq Whisper transcription
- **Deployment**: Docker multi-stage build behind Traefik reverse proxy
- **Tools**: ripgrep, fd, Emacs batch mode, git

## Key Components

### Tool System (`pkm_bridge/tools/`)
Auto-registering tool classes extending `BaseTool`. Tools are shared between both
interfaces. Includes: shell execution, note search (regex + semantic), file operations,
calendar (Google Cal), task management (TickTick), skills system.

### Embeddings / RAG Pipeline (`pkm_bridge/embeddings/`)
Voyage AI embeddings with incremental updates via APScheduler (hourly). Notes are chunked
and stored in PostgreSQL with pgvector for semantic search. Auto-injected into custom app
queries; MCP users call `semantic_search` explicitly.

### Skills System (`.pkm/skills/`)
Reusable procedures stored as `.md` (recipes), `.sh`, or `.py` files. Claude reads skills
on-demand for complex tasks. Created/managed by the self-improvement agent or manually.

### Self-Improvement Agent (`pkm_bridge/self_improvement/`)
Runs periodically (daily). Inspects conversations, feedback, and tool logs. Creates/updates
skills and learned rules. Maintains its own persistent memory across runs.

### Learned Rules (database)
Behavioral rules extracted from user corrections and patterns. Injected into system prompts
at query time. Managed by the self-improvement agent.

## Deployment

Docker container on `docker-server`, deployed via `./DEPLOY.sh` (SSH + docker compose).
Traefik handles HTTPS termination and routing to both services.

## Current Status

The system is in daily production use via both interfaces. Core features are stable:
search, note creation, calendar, TickTick, voice input, semantic search, self-improvement.

Active development focuses on improving tool quality, prompt refinement, and maintaining
feature parity between the two interfaces where appropriate.

## Resources

- **Anthropic API**: https://docs.anthropic.com/
- **MCP Protocol**: https://modelcontextprotocol.io/
- **Project docs**: `Documentation/` directory
