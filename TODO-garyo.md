# TODO List for gco-pkm-llm

# Front end
- [x] Show markdown nicely in web client (convert to html, maybe dynamically)
- [x] Load skills properly (they're pseudo-skills, not uploaded to Anthropic)
- [x] Login/Auth
- [x] Remove "skills", move into system prompt
- [ ] Enhance org journal add to allow inline tasks (no leading "- ", instead 15x *)
- [x] Notes search tool should have sep cases for logseq journals, pages, org pages, and org journals (needs emacs or parser)
  - [x] Maybe I should store my org notes using the same scheme as logseq, one file per day! Then I'd just need a context/recent view within Emacs.
- [x] Add a full note-editing interface
- [x] Connect to ticktick
- [ ] Connect to Google calendar
- [x] Auto-refresh in editor
- [x] Session management in db?
- [ ] Emacs on server doesn't work (used to create org notes): would need a bunch of stuff from my Emacs config. Make it work without that.
  Now that I'm using file-per-day, it shouldn't be hard. I see Claude does figure it out.
- [ ] Allow for editing custom user/system prompts in the app (store in db).
