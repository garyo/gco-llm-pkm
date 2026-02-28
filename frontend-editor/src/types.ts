import type { EditorView } from '@codemirror/view';

export interface FileInfo {
  full_path: string;
  path: string;
  name: string;
  dir: 'org' | 'logseq';
  type: 'journal' | 'page' | 'other';
  modified: number;
  size: number;
}

export type ConflictAction = 'save-mine' | 'reload-remote' | 'backup-and-reload';

export interface EditorState {
  editorView: EditorView | null;
  currentFile: string | null;
  isDirty: boolean;
  allFiles: FileInfo[];
  currentFileMtime: number | null;
  conflictDetected: boolean;
  saveInProgress: boolean;
  pendingScrollLine: number | null;
  autoSaveTimeout: number | null;
  lastSaveTime: number | null;
  statusUpdateInterval: number | null;
  cachedAutoSaveEnabled: boolean;
  cachedAutoSaveDelay: number;
  navHistory: { path: string; line?: number }[];
  journalDates: Map<string, { path: string; dir: string }>;
  calendarMonth: Date;
}

export interface SSEState {
  eventSource: EventSource | null;
  lastEventTime: number;
  reconnectAttempts: number;
  lastResumeRefresh: number;
}

export const STORAGE_KEYS = {
  AUTH_TOKEN: 'pkm-authToken',
  AUTO_SAVE_ENABLED: 'pkm-autoSave',
  AUTO_SAVE_DELAY: 'pkm-autoSaveDelay',
} as const;

export const MAX_RECONNECT_DELAY = 30000;
export const SSE_GLOBAL_KEY = '__sse_editor_connection__';
