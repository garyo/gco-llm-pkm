# gco-pkm-llm: LLM Bridge Server for Personal Knowledge Management

## Project Overview

This is a bridge server that connects Claude AI to your org-mode Personal Knowledge Management system. It provides natural language access to your entire knowledge base from any device.

## Architecture

```
Your Devices (Web Browser, Mobile)
    ↓ HTTPS
Web Interface (Flask app)
    ↓
Claude API (with tools)
    ↓ execute_shell, read_skill, list_files
Local Org Files + Tools (ripgrep, Emacs, git)
```

### Key Components

1. **Flask Bridge Server** (`pkm-bridge-server.py`)
   - Exposes web interface and API endpoints
   - Manages conversation sessions
   - Provides tools to Claude API
   - Executes shell commands safely

2. **Claude API Integration**
   - Uses Anthropic's messages API with tools
   - Tool use loop for multi-step operations
   - Persistent conversation context per session

3. **Skills System** (`skills/`)
   - Markdown files with detailed instructions
   - Claude reads skills on-demand for complex tasks
   - Extensible without code changes

4. **Web Interface** (`templates/index.html`)
   - Responsive, mobile-friendly chat UI
   - Session persistence
   - Clean, minimal design

## Current Status

**Completed:**
- ✅ Architecture designed
- ✅ Core server implementation planned
- ✅ Skills system designed
- ✅ Tool definitions created

**In Progress:**
- 🚧 Initial implementation
- 🚧 Testing on local dev machine
- 🚧 Creating example skills

**Not Started:**
- ⏳ Deployment to Proxmox
- ⏳ Production hardening
- ⏳ Authentication/security
- ⏳ Advanced features

## File Structure

```
~/src/gco-pkm-llm/
├── PROJECT.md              # This file
├── README.md               # Setup and usage instructions
├── TODO.md                 # Detailed task list
├── pkm-bridge-server.py    # Main server application
├── requirements.txt        # Python dependencies
├── .env.example           # Environment variables template
├── templates/
│   └── index.html         # Web interface
└── skills/
    ├── org-ql-queries.md      # Org-QL query construction
    ├── journal-navigation.md   # Journal structure operations
    ├── search-patterns.md      # Effective ripgrep usage
    └── emacs-batch.md         # Emacs batch mode operations
```

## Technology Stack

- **Backend:** Python 3.9+ with Flask
  - Note: use uv. Always use PEP-723 for dependencies, and start with `#!/usr/bin/env -S uv run --script`
- **AI:** Anthropic Claude API (Sonnet 4)
- **Tools:** ripgrep, Emacs (batch mode), git
- **Frontend:** Vanilla HTML/CSS/JS (no build step)
- **Deployment:** Initially local, then Proxmox LXC container

## Configuration

### Environment Variables

```bash
ANTHROPIC_API_KEY=sk-ant-...        # Required: Your Anthropic API key
ORG_DIR=/path/to/org-files          # Required: Location of org files
SKILLS_DIR=/path/to/skills          # Optional: Defaults to ./skills
PORT=8000                            # Optional: Server port
ALLOWED_COMMANDS=rg,emacs,find,cat  # Optional: Whitelisted commands
```

### Org Files Setup

The server expects access to your synced org files. On dev machine:
- Use actual org-agenda directory path
- Example: `ORG_DIR=~/Documents/org-agenda`

On Proxmox (future):
- NFS mount from desktop, or
- Syncthing sync to container, or
- Direct git clone/pull

## Security Considerations

**Current (Dev Mode):**
- No authentication
- Localhost only (127.0.0.1)
- Command whitelist for safety

**Production (Future):**
- Add basic auth or OAuth
- HTTPS via reverse proxy
- Rate limiting
- Command audit logging
- User sessions with timeouts

## Development Workflow

1. **Local Testing:**
   ```bash
   cd ~/src/gco-pkm-llm
   cp .env.example .env
   # Edit .env with your settings
   pip install -r requirements.txt
   python3 pkm-bridge-server.py
   # Visit http://localhost:8000
   ```

2. **Iteration:**
   - Add/modify skills in `skills/`
   - Test queries through web interface
   - Check server logs for tool execution
   - Refine prompts and skills

3. **Deployment:**
   - Create Proxmox LXC container
   - Set up file sync (NFS/Syncthing)
   - Configure reverse proxy with SSL
   - Set up systemd service
   - Test from mobile device

## Key Design Decisions

### Why Tools Instead of Hardcoded Functions?

**Flexibility:** Can extend capabilities by adding skills, not code.

**Example:** Want to add org-roam graph queries?
- Add `org-roam-operations.md` skill
- Claude learns to use it automatically
- No server code changes needed

### Why Shell Execution?

**Full Power:** Leverage existing tools (ripgrep, Emacs, git) without reimplementing.

**Example:** org-ql queries require Emacs with org-ql loaded. Running Emacs in batch mode is simpler than parsing org-mode in Python.

**Safety:** Whitelist prevents dangerous operations (rm, dd, etc.)

### Why Stateful Sessions?

**Context:** Claude remembers ongoing conversations and projects.

**Example:**
- User: "What music did I see this summer?"
- Claude: [searches, shows results]
- User: "When was the first one?"
- Claude: [remembers context, answers specifically]

### Why Skills Over System Prompt?

**Scalability:** System prompt has token limits. Skills loaded on-demand.

**Maintainability:** Skills are independent markdown files, easy to edit/version.

**Discoverability:** Claude can list available skills and read when needed.

## Integration with Existing gco-pkm

This bridge server complements your existing Emacs gco-pkm setup:

**Emacs (Desktop):**
- Primary authoring environment
- Full org-mode features
- gco-pkm commands and workflows

**Bridge Server (Anywhere):**
- Quick searches from mobile
- Natural language queries
- Voice input friendly
- Adding quick captures

**They share the same org files via sync.**

## Example Interactions

**Search:**
```
You: What did I write about emacs PKM?
Claude: [reads search-patterns.md, runs rg, formats results]
```

**Add Content:**
```
You: Add to today: Had coffee with Sarah, discussed API design
Claude: [reads journal-navigation.md, runs Emacs batch, confirms]
```

**Complex Query:**
```
You: Show me all active project TODOs sorted by priority
Claude: [reads org-ql-queries.md, constructs query, runs via Emacs, formats]
```

**Analysis:**
```
You: Summarize my work on the PKM project this month
Claude: [searches, aggregates, synthesizes natural summary]
```

## Future Enhancements

**Short Term:**
- [ ] Voice input support (Web Speech API)
- [ ] Export conversation as org file
- [ ] Scheduled queries (daily summary)
- [ ] Multiple org directory support

**Medium Term:**
- [ ] Multi-user support with auth
- [ ] Mobile app wrapper (React Native / Flutter)
- [ ] Offline mode with cached responses
- [ ] Integration with org-roam-ui graph

**Long Term:**
- [ ] Proactive notifications (based on calendar, TODOs)
- [ ] Voice assistant integration (Siri, Google Assistant)
- [ ] AI-suggested connections between notes
- [ ] Automated MOC (Map of Content) generation

## Resources

- **Anthropic API Docs:** https://docs.anthropic.com/
- **org-ql:** https://github.com/alphapapa/org-ql
- **ripgrep:** https://github.com/BurntSushi/ripgrep
- **Your gco-pkm config:** ~/.config/emacs/lisp/gco-pkm*.el

## Notes for Claude Code

When working on this project:

1. **Test carefully:** Shell execution is powerful but needs validation
2. **Skills first:** Add skills before adding code features
3. **Log everything:** Print tool executions for debugging
4. **Start simple:** Get basic search working before complex queries
5. **Mobile matters:** Test responsive design early

The goal is a production-ready personal AI assistant with full access to your knowledge base, accessible from anywhere.
