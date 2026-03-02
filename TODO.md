# TODO — PKM Bridge

Current and planned work items. See PROJECT.md for architecture overview.

## Dual Interface Maintenance

- [ ] Keep system prompts aligned on core PKM behavior (search strategy, file safety, note creation)
- [ ] Test both interfaces when changing tools or backend logic
- [ ] Ensure new skills work correctly in both custom app and MCP contexts
- [ ] Monitor self-improvement agent changes for cross-interface impact

## Tool & Search Quality

- [ ] Improve semantic search ranking and chunk quality
- [ ] Better handling of date-range queries across both org and Logseq
- [ ] Reduce unnecessary tool calls in common workflows
- [ ] Add tool usage analytics to identify patterns and failures

## Embeddings / RAG

- [ ] Tune chunking strategy for better retrieval precision
- [ ] Add embedding coverage monitoring (% of notes indexed)
- [ ] Explore re-ranking retrieved chunks before injection

## Self-Improvement Agent

- [ ] Review and consolidate accumulated skills for redundancy
- [ ] Add conversation quality metrics the agent can track
- [ ] Improve feedback signal detection from implicit user behavior

## Frontend / UX

- [ ] Editor SPA: collaborative editing awareness (external file changes)
- [ ] Mobile responsiveness improvements
- [ ] Session management UX (list, rename, delete sessions)

## Infrastructure

- [ ] Health check endpoint improvements (check both :8000 and :8001)
- [ ] Structured logging for better debugging
- [ ] Backup strategy for PostgreSQL data
- [ ] Monitor Docker resource usage

## Future Ideas

- [ ] Calendar event creation from natural language
- [ ] Cross-note link suggestions based on embeddings
- [ ] Automated daily/weekly summaries
- [ ] Org-roam graph integration
- [ ] Push notifications for reminders
