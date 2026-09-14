// Minimal, editor-scoped types and constants shared by both editor consumers
// (the standalone SPA and the chat-app's embedded editor). Intentionally does
// not include app-level state — each consumer owns its own state shape.

export const STORAGE_KEYS = {
  AUTH_TOKEN: 'pkm-authToken',
  AUTO_SAVE_ENABLED: 'pkm-autoSave',
  AUTO_SAVE_DELAY: 'pkm-autoSaveDelay',
} as const;
