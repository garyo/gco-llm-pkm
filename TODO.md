# TODO List for gco-pkm-llm

## Phase 1: Initial Setup ⏳

### Environment Setup
- [ ] Copy `.env.example` to `.env`
- [ ] Add Anthropic API key to `.env`
- [ ] Set `ORG_DIR` to your org-agenda directory in `.env`
- [ ] Verify ripgrep is installed: `which rg`
- [ ] Verify Emacs is installed: `which emacs`

### Directory Structure
- [ ] Create `templates/` directory
- [ ] Create `skills/` directory
- [ ] Verify org files are accessible at configured path

## Phase 2: Core Implementation ⏳

### Server Development
- [ ] Test `pkm-bridge-server.py` runs without errors
- [ ] Fix any import/syntax errors
- [ ] Test Flask server starts: `python3 pkm-bridge-server.py`
- [ ] Verify server responds at http://localhost:8000
- [ ] Test basic HTML page loads

### Tool Implementation
- [ ] Test `execute_shell` with simple command: `ls`
- [ ] Test `execute_shell` with ripgrep: `rg -i "test"`
- [ ] Test `list_files` tool
- [ ] Test `read_skill` tool (after skills created)
- [ ] Add error handling for failed commands
- [ ] Add command timeout handling
- [ ] Verify command whitelist works (try blocked command)

### Claude API Integration
- [ ] Test basic query/response without tools
- [ ] Test tool calling works (check logs)
- [ ] Test tool result handling
- [ ] Test multi-turn conversations
- [ ] Verify session persistence across requests
- [ ] Test session history endpoint

## Phase 3: Skills Creation 🚧

### Basic Skills
- [ ] Create `skills/search-patterns.md`
  - [ ] Document ripgrep usage patterns
  - [ ] Include hashtag search examples
  - [ ] Include date-based search examples
  - [ ] Include multi-term search examples

- [ ] Create `skills/journal-navigation.md`
  - [ ] Document journal structure (Year > Month > Day)
  - [ ] Include examples of finding today's entry
  - [ ] Include examples of adding content
  - [ ] Document property drawer handling

- [ ] Create `skills/org-ql-queries.md`
  - [ ] Document org-ql query syntax
  - [ ] Include common query patterns
  - [ ] Show how to run via Emacs batch
  - [ ] Include examples with expected output

- [ ] Create `skills/emacs-batch.md`
  - [ ] Document Emacs batch mode basics
  - [ ] Show how to load packages (org-ql, org-ml)
  - [ ] Include file manipulation examples
  - [ ] Document error handling in batch mode

### Advanced Skills (Later)
- [ ] `org-roam-operations.md` - backlinks, nodes, graph queries
- [ ] `task-management.md` - TODO operations, scheduling
- [ ] `export-formats.md` - Converting org to other formats
- [ ] `git-operations.md` - Version control for notes

## Phase 4: Testing & Validation ⏳

### Basic Functionality Tests
- [ ] Search for a simple term (e.g., "emacs")
- [ ] Search with hashtag (e.g., "#music")
- [ ] List all org files
- [ ] Read a specific org file
- [ ] Read a skill file
- [ ] Test with no results (verify graceful handling)
- [ ] Test with very long output (verify truncation)

### Advanced Functionality Tests
- [ ] Run org-ql query via Emacs batch
- [ ] Add content to today's journal entry
- [ ] Search within date range
- [ ] Find TODO items
- [ ] Complex multi-step query requiring multiple tools
- [ ] Test conversation context (follow-up questions)

### Error Handling Tests
- [ ] Invalid command (not in whitelist)
- [ ] Command that fails (non-zero exit)
- [ ] Command timeout (hangs)
- [ ] Skill not found
- [ ] Emacs batch mode error
- [ ] Invalid org-ql syntax

### Integration Tests with Your Actual Data
- [ ] "What did I write about [topic you know you have notes on]?"
- [ ] "When was the last time I mentioned [person/place]?"
- [ ] "Show me my TODOs for [project]"
- [ ] "Add to today: [test note]"
- [ ] "Summarize my notes from last week"

## Phase 5: Web Interface Polish 🚧

### UI Improvements
- [ ] Add loading indicator when waiting for response
- [ ] Add markdown rendering for Claude's responses
- [ ] Add syntax highlighting for code blocks
- [ ] Add "clear conversation" button
- [ ] Add "new session" button
- [ ] Improve mobile responsive design
- [ ] Add keyboard shortcuts (Cmd+K to focus input)

### UX Enhancements
- [ ] Show typing indicator while Claude is thinking
- [ ] Auto-scroll to bottom on new messages
- [ ] Show tool execution in UI (optional, for debugging)
- [ ] Add example queries as quick buttons
- [ ] Save/export conversation feature
- [ ] Dark mode support

## Phase 6: Local Development Complete ✅

### Documentation
- [ ] Write usage guide in README.md
- [ ] Document all environment variables
- [ ] Add troubleshooting section
- [ ] Include example queries
- [ ] Document skills system

### Code Quality
- [ ] Add docstrings to all functions
- [ ] Add type hints
- [ ] Add basic logging throughout
- [ ] Clean up any TODO comments in code
- [ ] Test with different Python versions (3.9, 3.10, 3.11)

### Performance
- [ ] Profile API response times
- [ ] Optimize long-running queries
- [ ] Add caching for frequently-accessed skills
- [ ] Consider streaming responses (long answers)

## Phase 7: Deployment Preparation ⏳

### Containerization
- [ ] Create Dockerfile
- [ ] Test Docker build
- [ ] Create docker-compose.yml
- [ ] Document container setup

### Proxmox Setup
- [ ] Create LXC container on Proxmox
- [ ] Set up Syncthing or NFS for org files
- [ ] Install dependencies in container
- [ ] Test file access in container
- [ ] Configure networking

### Systemd Service
- [ ] Create systemd service file
- [ ] Test service start/stop/restart
- [ ] Configure auto-start on boot
- [ ] Set up log rotation
- [ ] Test service after reboot

### Reverse Proxy
- [ ] Configure Nginx on Internet server
- [ ] Set up SSL with Let's Encrypt
- [ ] Test HTTPS access from external network
- [ ] Configure fail2ban for security
- [ ] Set up monitoring/alerting

## Phase 8: Production Hardening ⏳

### Security
- [ ] Add basic authentication (password or token)
- [ ] Implement rate limiting
- [ ] Add CSRF protection
- [ ] Audit log all commands executed
- [ ] Set up firewall rules
- [ ] Review command whitelist
- [ ] Consider adding OAuth2

### Reliability
- [ ] Add health check endpoint
- [ ] Implement graceful shutdown
- [ ] Add request timeout handling
- [ ] Set up monitoring (uptime, errors)
- [ ] Configure backup for conversation history
- [ ] Test crash recovery

### Performance
- [ ] Add Redis for session storage (scale beyond memory)
- [ ] Implement response caching for repeated queries
- [ ] Optimize Emacs batch mode startup time
- [ ] Add connection pooling if needed
- [ ] Load test with multiple concurrent users

## Future Features (Phase 9+) 💡

### Enhanced Capabilities
- [ ] Voice input support (Web Speech API)
- [ ] Voice output (text-to-speech)
- [ ] Export conversation as org file
- [ ] Scheduled queries (daily digest email)
- [ ] Webhook support for external triggers
- [ ] Multi-user support with separate contexts

### Advanced AI Features
- [ ] Proactive suggestions based on patterns
- [ ] Auto-tagging of new entries
- [ ] Duplicate detection
- [ ] Broken link detection and repair
- [ ] Generate Maps of Content automatically
- [ ] Suggest connections between notes

### Mobile Native App
- [ ] React Native wrapper
- [ ] iOS app with Shortcuts integration
- [ ] Android app with Widget
- [ ] Offline mode with sync
- [ ] Push notifications

### Integrations
- [ ] org-roam-ui graph visualization
- [ ] Obsidian import/export
- [ ] Logseq import/export
- [ ] Zotero integration
- [ ] Calendar integration (Google Cal, etc.)
- [ ] Task manager integration (Todoist, etc.)

## Current Priority

**RIGHT NOW:** Focus on Phase 2-3
1. Get server running locally
2. Test basic tool execution
3. Create initial skills
4. Verify Claude can use tools effectively

**NEXT:** Phase 4-5
1. Thorough testing with real data
2. Polish web interface
3. Make it daily-driver quality

**LATER:** Phases 6-8
1. Deploy to Proxmox
2. Production hardening
3. Security and reliability

## Testing Checklist for Each Phase

After completing each phase, verify:
- [ ] No Python errors/exceptions
- [ ] Server starts and responds
- [ ] Claude API calls work
- [ ] Tools execute correctly
- [ ] Web UI is usable
- [ ] Logs show expected behavior
- [ ] Can answer at least 3 different query types

## Notes

- Start simple: Get "search for term" working before complex org-ql
- Test incrementally: Don't write everything before testing
- Skills are easier to change than code: Prefer adding skills over code features
- Mobile testing early: Check responsive design frequently
- Security matters: Even in local dev, test command whitelist

## Success Criteria

**Phase 1-5 Complete When:**
- Can search notes from web UI on dev machine
- Can add content to journal
- Can run org-ql queries
- Conversation context works
- Mobile browser works well

**Ready for Deployment When:**
- All above plus production hardening
- Security measures in place
- Reliable error handling
- Good documentation

**Production Success When:**
- Daily use from mobile without friction
- More convenient than opening Emacs
- Becomes primary search interface
- Can trust it with important captures
