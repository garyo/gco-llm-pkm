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

export interface EditorState {
  editorView: EditorView | null;
  currentFile: string | null;
  allFiles: FileInfo[];
  pendingScrollLine: number | null;
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

export { STORAGE_KEYS } from '@pkm/editor/types';

export const MAX_RECONNECT_DELAY = 30000;
export const SSE_GLOBAL_KEY = '__sse_editor_connection__';

// --- Admin types ---

export interface OAuthStatus {
  connected: boolean;
  expired?: boolean;
  has_refresh_token?: boolean;
  auto_refreshable?: boolean;
  expires_at?: string | null;
}

export interface LearnedRule {
  id: number;
  rule_type: string;
  rule_text: string;
  rule_data: Record<string, unknown> | null;
  confidence: number;
  hit_count: number;
  is_active: boolean;
  source_query_ids: string[];
  created_at: string;
  updated_at: string;
}

export interface ScheduledTask {
  name: string;
  prompt: string;
  schedule_type: string;
  schedule_expr: string;
  enabled: boolean;
  max_turns: number;
  last_run?: string;
  next_run?: string;
}

export interface SystemPrompt {
  content: string;
  modified: number;
}

export interface Skill {
  name: string;
  type: 'shell' | 'recipe';
  description: string;
  trigger: string;
  tags: string[];
  created: string;
  last_used: string;
  use_count: number;
  body: string;
}

export type AdminTab = 'connections' | 'prompts' | 'rules' | 'tasks' | 'skills' | 'self-improve';
