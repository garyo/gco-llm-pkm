# gco-pkm-llm: AI-Powered Personal Knowledge Management

Natural language access to your org-mode PKM system via Claude AI.

## What It Does

Provides conversational access to your entire org-mode knowledge base:
- **Search**: "What did I write about music this summer?"
- **Analyze**: "Show me all my active projects"
- **Add**: "Add to today: Had coffee with Sarah"
- **Discover**: "Find connections between my emacs and productivity notes"

Works from any device with a web browser - desktop or mobile.

## Quick Start

### Prerequisites

- Python 3.9+
- **uv** package manager ([install here](https://github.com/astral-sh/uv))
- Anthropic API key ([get one here](https://console.anthropic.com/))
- Your org files synced/accessible locally
- ripgrep installed (`brew install ripgrep` on macOS)
- Emacs with org-ql installed (for advanced queries)

### Installation

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone or navigate to project
cd ~/src/gco-pkm-llm

# Run setup script
./setup.sh

# Configure environment
cp .env.example .env
# Edit .env with your settings:
#   - Add your ANTHROPIC_API_KEY
#   - Set ORG_DIR to your org-agenda directory
```

Note: Dependencies are managed via PEP-723 inline metadata in the script. No separate virtual environment needed!

### Run Locally

**Production Mode** (single server):
```bash
# Build frontend (one time, or after UI changes)
cd frontend && bun run build && cd ..

# Start server (uv handles dependencies automatically)
./pkm-bridge-server.py

# Open browser to http://localhost:8000
```

**Development Mode** (dual servers with hot reload):
```bash
# Terminal 1: Start Flask backend
./pkm-bridge-server.py

# Terminal 2: Start Astro frontend (in frontend/ directory)
cd frontend && bun run dev

# Open browser to http://localhost:4321 (instant hot reload!)
```

**See `FRONTEND_DEVELOPMENT.md` for details on the modern Astro + Tailwind frontend.**

## Configuration

Edit `.env` file:

```bash
# Required
ANTHROPIC_API_KEY=sk-ant-your-key-here
ORG_DIR=/Users/you/Documents/org-agenda

# Optional
PORT=8000                # Server port
HOST=127.0.0.1          # Bind address (use 0.0.0.0 for network access)
SKILLS_DIR=./skills     # Location of skill files
```

## Usage Examples

### Simple Search

**You:** "What did I write about sailing?"

**Assistant:** [Uses ripgrep to search, shows relevant entries with dates]

### Date-Based Query

**You:** "Show me my journal entries from last week"

**Assistant:** [Searches org journals for recent entries, summarizes]

### TODO Management

**You:** "What tasks do I have for my PKM project?"

**Assistant:** [Uses org-ql to find TODOs tagged with project]

### Adding Content

**You:** "Add to today: Finished the bridge server implementation"

**Assistant:** [Uses Emacs batch mode to append to today's journal entry]

### Analysis

**You:** "How often have I mentioned health issues in the past month?"

**Assistant:** [Searches #health entries, counts, shows patterns]

## How It Works

```
Your Question
    ↓
Web Interface (Flask)
    ↓
Claude API with Tools
    ↓
execute_shell: runs ripgrep, emacs, find, etc.
read_skill: loads detailed instructions
list_files: browses directory
    ↓
Results formatted naturally
    ↓
You get answer
```

### The Skills System

Skills are markdown files in `skills/` that provide Claude with detailed instructions for complex tasks.

**Available Skills:**
- `search-patterns.md` - How to search effectively with ripgrep
- `journal-navigation.md` - Working with hierarchical journal structure  
- `org-ql-queries.md` - Constructing and running org-ql queries

**Claude automatically reads skills when needed.**

## Project Structure

```
gco-pkm-llm/
├── pkm-bridge-server.py   # Main server application
├── templates/
│   └── index.html         # Web interface
├── skills/
│   ├── search-patterns.md
│   ├── journal-navigation.md
│   └── org-ql-queries.md
├── requirements.txt
├── .env.example
├── PROJECT.md            # Architecture and design
├── TODO.md              # Detailed task list
└── README.md            # This file
```

## Development

### Testing Locally

```bash
# Start server in debug mode
./pkm-bridge-server.py

# In another terminal, test health endpoint
curl http://localhost:8000/health

# Try a query via API
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"message": "List all my org files", "session_id": "test"}'
```

### Adding New Skills

Create a new markdown file in `skills/`:

```bash
# Create new skill
cat > skills/my-custom-skill.md << 'EOF'
# My Custom Skill

Instructions for Claude on how to...
EOF

# Restart server to pick up new skill
# Claude will now be able to read this skill
```

### Checking Logs

Server logs show all tool executions:

```
[USER] What music did I see this summer?
[TOOL] read_skill with params: {'skill_name': 'search-patterns'}
[RESULT] # Search Patterns Skill...
[TOOL] execute_shell with params: {'command': 'rg -i "#music" .'}
[RESULT] journal.org:123:- Excellent #music concert...
[ASSISTANT] I found several music entries from summer 2025...
```

## Deployment (Future)

See `PROJECT.md` for detailed deployment instructions to Proxmox.

**Quick overview:**
1. Set up LXC container on Proxmox
2. Sync org files (NFS or Syncthing)
3. Configure reverse proxy with SSL
4. Set up systemd service
5. Access from anywhere

## Security Notes

**Current (Development):**
- Runs on localhost only
- Command whitelist prevents dangerous operations
- No authentication (local use only)

**Production (TODO):**
- Add basic auth or OAuth
- HTTPS via reverse proxy
- Rate limiting
- Audit logging

## Troubleshooting

### "ANTHROPIC_API_KEY environment variable must be set"
- Copy `.env.example` to `.env`
- Add your API key to `.env`

### "ORG_DIR does not exist"
- Set correct path in `.env`
- Use absolute path, not `~` shorthand

### "Command not allowed: some-command"
- Only whitelisted commands can run
- Edit `ALLOWED_COMMANDS` in `.env` to add more

### Search returns nothing
- Verify org files are at `ORG_DIR`
- Try simple test: "List all my org files"
- Check server logs for errors

### Emacs batch mode fails
- Ensure Emacs installed: `which emacs`
- Check org-ql loaded: `emacs --batch --eval "(require 'org-ql)"`
- May need to load full init.el

## API Endpoints

### GET /
Web interface

### POST /query
```json
{
  "message": "Your question",
  "session_id": "optional-session-id"
}
```

Returns:
```json
{
  "response": "Claude's answer",
  "session_id": "session-id"
}
```

### GET /sessions/:id/history
Get conversation history

### DELETE /sessions/:id
Clear conversation history

### GET /health
Server status and configuration

## Performance

**API Costs** (approximate):
- Simple search: ~$0.01
- Complex query with tools: ~$0.05
- Heavy usage (100 queries/day): ~$3-5/month

**Response Times:**
- Simple queries: 1-3 seconds
- Complex multi-tool: 5-10 seconds
- Depends on org file size and query complexity

## Roadmap

See `TODO.md` for detailed task list.

**Short term:**
- ✅ Core functionality working
- 🚧 Skills system
- 🚧 Web interface polish

**Medium term:**
- ⏳ Deploy to Proxmox
- ⏳ Production hardening
- ⏳ Mobile optimization

**Long term:**
- 💡 Voice input/output
- 💡 Proactive suggestions
- 💡 Integration with org-roam-ui

## Contributing

This is a personal project, but suggestions welcome!

- Report issues on GitHub (if/when public)
- Submit skill improvements
- Share interesting use cases

## License

TBD - Will be open source

## Related Projects

- [gco-pkm](https://github.com/...) - Emacs PKM configuration
- [org-ql](https://github.com/alphapapa/org-ql) - Query language for org
- [org-roam](https://github.com/org-roam/org-roam) - Roam-like PKM in org-mode

## Support

- Check `PROJECT.md` for architecture details
- See `TODO.md` for known issues
- Read skills/ for usage examples
- Search server logs for debugging

## Acknowledgments

Built with:
- [Anthropic Claude API](https://www.anthropic.com/)
- [Flask](https://flask.palletsprojects.com/)
- [org-mode](https://orgmode.org/)
- [ripgrep](https://github.com/BurntSushi/ripgrep)
