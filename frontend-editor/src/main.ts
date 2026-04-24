import './styles/editor.css';
import type { EditorState as AppState } from './types';
import { STORAGE_KEYS } from './types';
import { debounce, fetchWithTimeout, getAuthHeaders } from './utils';
import { checkAuth, handleLogin, showLogin, showEditor, showAdmin } from './auth';
import { initAdmin } from './admin/admin-page';
import * as api from './api';
import { createEditor, setEditorContent } from '@pkm/editor/createEditor';
import {
  filterAndPopulateFiles,
  buildJournalDates,
  toggleFilterControls,
  closeFilterControls,
} from './file-list';
import { initCalendar } from './calendar';
import { createSSEState, connectSSE, setupSSEReconnection } from './sse';
import {
  refreshAutoSaveSettings,
  cleanupAutoSaveTimers,
  scheduleAutoSave,
  performAutoSave,
} from './autosave';
import { showConflictModal, initConflictModal } from './conflict';
import { parseUrlParams, updateUrl, resolveUrlParams } from './url-params';

// ---------------------------------------------------------------------------
// Shared state
// ---------------------------------------------------------------------------
const state: AppState = {
  editorView: null,
  currentFile: null,
  isDirty: false,
  allFiles: [],
  currentFileMtime: null,
  conflictDetected: false,
  saveInProgress: false,
  pendingScrollLine: null,
  autoSaveTimeout: null,
  lastSaveTime: null,
  statusUpdateInterval: null,
  cachedAutoSaveEnabled: localStorage.getItem(STORAGE_KEYS.AUTO_SAVE_ENABLED) !== 'false',
  cachedAutoSaveDelay: parseInt(localStorage.getItem(STORAGE_KEYS.AUTO_SAVE_DELAY) || '2000'),
  navHistory: [],
  journalDates: new Map(),
  calendarMonth: new Date(),
};

const sseState = createSSEState();

// ---------------------------------------------------------------------------
// DOM elements
// ---------------------------------------------------------------------------
const editorContainer = document.getElementById('editor-container')!;
const fileSelector = document.getElementById('file-selector') as HTMLSelectElement;
const saveButton = document.getElementById('save-button') as HTMLButtonElement;
const refreshButton = document.getElementById('refresh-button') as HTMLButtonElement;
const editorStatus = document.getElementById('editor-status')!;
const sourceFilter = document.getElementById('source-filter') as HTMLSelectElement;
const typeFilter = document.getElementById('type-filter') as HTMLSelectElement;
const searchFilter = document.getElementById('search-filter') as HTMLInputElement;
const fileCount = document.getElementById('file-count')!;
const fileCountMobile = document.getElementById('file-count-mobile');
const currentFileDisplay = document.getElementById('current-file-display')!;
const currentFilePath = document.getElementById('current-file-path')!;
const navBackBtn = document.getElementById('nav-back-btn') as HTMLButtonElement;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function updateStatus(message: string, isError: boolean = false): void {
  editorStatus.textContent = message;
  editorStatus.style.color = isError ? '#ef4444' : 'inherit';
}

function updateFileDisplay(filepath: string | null): void {
  if (!filepath) {
    currentFileDisplay.classList.add('hidden');
    currentFilePath.textContent = '';
    return;
  }
  const fileInfo = state.allFiles.find((f) => f.full_path === filepath);
  currentFilePath.textContent = fileInfo ? fileInfo.path : filepath;
  currentFileDisplay.classList.remove('hidden');
}

function updateNavBackBtn(): void {
  navBackBtn.classList.toggle('hidden', state.navHistory.length === 0);
}

function pushNavHistory(): void {
  if (!state.currentFile) return;
  const line = state.editorView
    ? state.editorView.state.doc.lineAt(state.editorView.state.selection.main.head).number
    : undefined;
  state.navHistory.push({ path: state.currentFile, line });
  if (state.navHistory.length > 50) state.navHistory.shift();
  updateNavBackBtn();
}

function doFilterAndPopulate(): void {
  filterAndPopulateFiles(
    state.allFiles,
    sourceFilter,
    typeFilter,
    searchFilter,
    fileSelector,
    fileCount,
    fileCountMobile,
  );
}

// ---------------------------------------------------------------------------
// Core operations
// ---------------------------------------------------------------------------
async function loadFileList(): Promise<void> {
  try {
    state.allFiles = await api.loadFileList(state);
    state.journalDates = buildJournalDates(state.allFiles);
    updateStatus(`Loaded ${state.allFiles.length} files`);
    doFilterAndPopulate();
  } catch (e: unknown) {
    const err = e instanceof Error ? e : new Error(String(e));
    if (err.name === 'AbortError') return;
    updateStatus(`Error loading files: ${err.message}`, true);
    console.error('Failed to load file list:', e);
  }
}

const debouncedLoadFileList = debounce(loadFileList, 2000);

async function loadFile(filepath: string): Promise<void> {
  if (state.isDirty && state.currentFile) {
    if (!confirm('You have unsaved changes. Discard them?')) {
      fileSelector.value = state.currentFile;
      return;
    }
  }

  cleanupAutoSaveTimers(state);
  state.lastSaveTime = null;
  state.conflictDetected = false;

  try {
    updateStatus('Loading...');
    const data = await api.loadFile(filepath);

    if (state.editorView) state.editorView.destroy();
    state.editorView = createEditor(editorContainer, filepath, onDocChanged);
    setEditorContent(state.editorView, data.content, filepath, state.pendingScrollLine);
    state.pendingScrollLine = null;

    state.currentFile = filepath;
    state.currentFileMtime = data.modified || null;
    state.isDirty = false;
    saveButton.disabled = true;

    updateFileDisplay(filepath);
    updateUrl(filepath);

    // Ensure file is visible in selector
    const existing = Array.from(fileSelector.options).find((opt) => opt.value === filepath);
    if (!existing) {
      const option = document.createElement('option');
      option.value = filepath;
      option.textContent = filepath.split('/').pop() || filepath;
      fileSelector.insertBefore(option, fileSelector.options[1]);
    }
    fileSelector.value = filepath;

    const size = (data.size / 1024).toFixed(1);
    const fileType = filepath.endsWith('.org') ? 'Org' : 'Markdown';
    updateStatus(`Loaded ${data.path} (${size} KB, ${fileType})`);

    closeFilterControls();
  } catch (e: unknown) {
    const err = e instanceof Error ? e : new Error(String(e));
    if (err.name === 'AbortError') return;
    updateStatus(`Error loading file: ${err.message}`, true);
    updateFileDisplay(null);
    console.error('Failed to load file:', e);
  }
}

async function saveFile(force: boolean = false): Promise<void> {
  if (!state.editorView || !state.currentFile) return;
  state.saveInProgress = true;

  try {
    saveButton.disabled = true;
    updateStatus('Saving...');
    const content = state.editorView.state.doc.toString();

    const data = await api.saveFile(state.currentFile, content, state.currentFileMtime, force);

    if (data.status === 409) {
      state.conflictDetected = true;
      updateStatus('File changed on disk - conflicts need resolution', true);
      showConflictModal(state, null);
      return;
    }

    state.currentFileMtime = data.modified;
    state.isDirty = false;
    const size = (data.size / 1024).toFixed(1);
    updateStatus(`Saved ${data.path} (${size} KB)`);

    setTimeout(() => {
      if (!state.isDirty) saveButton.disabled = true;
    }, 500);
  } catch (e: unknown) {
    const err = e instanceof Error ? e : new Error(String(e));
    if (err.name === 'AbortError') {
      updateStatus('Save timed out, verifying...', true);
      const content = state.editorView!.state.doc.toString();
      const verified = await api.verifySaveCompleted(state.currentFile!, content);
      if (verified) {
        updateStatus('Saved (response was slow)');
        state.isDirty = false;
        saveButton.disabled = true;
        state.currentFileMtime = Date.now() / 1000;
      } else {
        updateStatus('Save timed out. Verify file before closing!', true);
        saveButton.disabled = false;
      }
    } else {
      updateStatus(`Error saving file: ${err.message}`, true);
      saveButton.disabled = false;
    }
  } finally {
    state.saveInProgress = false;
  }
}

async function refreshFile(): Promise<void> {
  await loadFileList();
  if (!state.currentFile) return;
  if (state.isDirty) {
    if (!confirm('You have unsaved changes. Reloading will discard them. Continue?')) return;
  }
  await loadFile(state.currentFile);
}

async function refreshFileInPlace(): Promise<boolean> {
  if (!state.currentFile) return false;
  const res = await fetchWithTimeout(
    `/api/file/${encodeURIComponent(state.currentFile)}`,
    { headers: getAuthHeaders() },
    5000,
  );
  if (!res.ok) return false;
  const data = await res.json();
  if (state.currentFileMtime && data.modified && data.modified <= state.currentFileMtime) return false;
  if (state.editorView) {
    const currentContent = state.editorView.state.doc.toString();
    if (data.content !== currentContent) {
      state.editorView.dispatch({
        changes: { from: 0, to: state.editorView.state.doc.length, insert: data.content },
      });
    }
    state.currentFileMtime = data.modified || null;
    state.isDirty = false;
    saveButton.disabled = true;
    return true;
  }
  return false;
}

async function refreshAfterResume(): Promise<void> {
  try {
    await loadFileList();
  } catch (e) {
    console.error('Failed to refresh file list after resume:', e);
  }

  if (!state.currentFile) return;

  if (state.isDirty) {
    try {
      const res = await fetchWithTimeout(
        `/api/file/${encodeURIComponent(state.currentFile)}`,
        { headers: getAuthHeaders() },
        5000,
      );
      if (res.ok) {
        const data = await res.json();
        if (data.modified && state.currentFileMtime && data.modified > state.currentFileMtime) {
          state.conflictDetected = true;
          updateStatus('File changed on disk - conflicts need resolution', true);
          if (state.cachedAutoSaveEnabled) showConflictModal(state, data.modified);
        }
      }
    } catch (e) {
      console.error('Failed to check file mtime after resume:', e);
    }
    return;
  }

  try {
    const updated = await refreshFileInPlace();
    if (updated) updateStatus(`Refreshed ${state.currentFile!.split('/').pop()}`);
  } catch (e) {
    console.error('Failed to refresh file after resume:', e);
  }
}

function onDocChanged(): void {
  state.isDirty = true;
  updateStatus('Modified (unsaved)');
  saveButton.disabled = false;
  scheduleAutoSave(state, updateStatus, () =>
    performAutoSave(state, updateStatus, (mtime) => showConflictModal(state, mtime)),
  );
}

// ---------------------------------------------------------------------------
// Today's journal
// ---------------------------------------------------------------------------
async function openTodayJournal(): Promise<void> {
  const today = new Date();
  const yyyy = today.getFullYear();
  const mm = String(today.getMonth() + 1).padStart(2, '0');
  const dd = String(today.getDate()).padStart(2, '0');
  const dateStr = `${yyyy}-${mm}-${dd}`;
  const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const dayName = dayNames[today.getDay()];

  const orgJ = state.allFiles.find(
    (f) => f.type === 'journal' && f.dir === 'org' && f.name.includes(dateStr),
  );
  const logseqJ = state.allFiles.find(
    (f) => f.type === 'journal' && f.dir === 'logseq' && f.name.includes(dateStr.replace(/-/g, '_')),
  );
  const todayFile = orgJ || logseqJ;

  if (todayFile) {
    fileSelector.value = todayFile.full_path;
    await loadFile(todayFile.full_path);
  } else {
    await createTodayJournal(dateStr, dayName);
  }
}

async function createTodayJournal(dateStr: string, dayName: string): Promise<void> {
  updateStatus("Creating today's journal...");
  const uuid = crypto.randomUUID().toUpperCase();
  const template = `#+title: ${dateStr}\n\n* <${dateStr} ${dayName}>\n:PROPERTIES:\n:ID:       ${uuid}\n:END:\n`;
  const filepath = `org:journals/${dateStr}.org`;

  try {
    const result = await api.createFile(filepath, template);
    if (result.status === 'exists') {
      updateStatus("Opening today's journal");
    } else {
      updateStatus("Created today's journal");
      await loadFileList();
    }
    fileSelector.value = filepath;
    await loadFile(filepath);
  } catch (e: unknown) {
    const err = e instanceof Error ? e : new Error(String(e));
    updateStatus(`Error creating journal: ${err.message}`, true);
  }
}

// ---------------------------------------------------------------------------
// SSE file change handler
// ---------------------------------------------------------------------------
function handleFileChanged(data: { path: string; mtime: number }): void {
  debouncedLoadFileList();
  if (!state.currentFile) return;

  const currentRelPath = state.currentFile.includes(':')
    ? state.currentFile.split(':', 2)[1]
    : state.currentFile;
  if (!data.path.endsWith(currentRelPath)) return;

  if (state.saveInProgress) return;

  if (state.isDirty) {
    state.conflictDetected = true;
    updateStatus('File changed on disk - conflicts need resolution', true);
    if (state.cachedAutoSaveEnabled) showConflictModal(state, data.mtime);
    return;
  }

  if (state.currentFileMtime && data.mtime <= state.currentFileMtime) return;

  refreshFileInPlace().catch((e) => console.error('Failed to refresh file after change:', e));
}

function handleOpenFile(data: { path: string }): void {
  if (!data.path) return;
  fileSelector.value = data.path;
  loadFile(data.path);
}

// ---------------------------------------------------------------------------
// Navigation history
// ---------------------------------------------------------------------------
function navBack(): void {
  const entry = state.navHistory.pop();
  updateNavBackBtn();
  if (!entry) return;
  state.pendingScrollLine = entry.line ?? null;
  loadFile(entry.path);
}

// Listen for editor:navigate events from org-links
window.addEventListener('editor:navigate', ((e: CustomEvent) => {
  const { path, line } = e.detail || {};
  if (!path) return;
  pushNavHistory();
  if (line) state.pendingScrollLine = line;
  fileSelector.value = path;
  loadFile(path);
}) as EventListener);

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------
navBackBtn.addEventListener('click', navBack);
saveButton.addEventListener('click', () => saveFile());
refreshButton.addEventListener('click', refreshFile);
sourceFilter.addEventListener('change', doFilterAndPopulate);
typeFilter.addEventListener('change', doFilterAndPopulate);
searchFilter.addEventListener('input', doFilterAndPopulate);
fileSelector.addEventListener('change', () => {
  const filepath = fileSelector.value;
  if (filepath) loadFile(filepath);
});

document.getElementById('today-button')?.addEventListener('click', openTodayJournal);
document.getElementById('today-button-desktop')?.addEventListener('click', openTodayJournal);
document.getElementById('filter-toggle')?.addEventListener('click', toggleFilterControls);

// Keyboard shortcuts
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 's') {
    e.preventDefault();
    if (state.currentFile && state.isDirty) saveFile();
  }
  if ((e.metaKey || e.ctrlKey) && e.key === 'r') {
    e.preventDefault();
    if (state.currentFile) refreshFile();
  }
  if (e.altKey && e.key === 'ArrowLeft' && state.navHistory.length > 0) {
    e.preventDefault();
    navBack();
  }
});

// localStorage settings sync from other tabs
window.addEventListener('storage', (e) => {
  if (e.key === STORAGE_KEYS.AUTO_SAVE_ENABLED || e.key === STORAGE_KEYS.AUTO_SAVE_DELAY) {
    refreshAutoSaveSettings(state);
  }
});

// Conflict modal
initConflictModal(state, saveFile, loadFile, updateStatus);

// Calendar
initCalendar(state, (dateKey, entry) => {
  if (entry) {
    pushNavHistory();
    fileSelector.value = entry.path;
    loadFile(entry.path);
  } else {
    const [yyyy, mm, dd] = dateKey.split('-');
    const date = new Date(parseInt(yyyy), parseInt(mm) - 1, parseInt(dd));
    const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    createTodayJournal(dateKey, dayNames[date.getDay()]);
  }
});

// ---------------------------------------------------------------------------
// Tab reuse via BroadcastChannel
// ---------------------------------------------------------------------------
const TAB_CHANNEL = 'pkm-editor-tab';

/** Try to hand off file open to an existing editor tab. Returns true if handled. */
function tryHandoffToExistingTab(params: import('./url-params').UrlParams): boolean {
  // Only hand off editor file opens (not admin, not empty)
  if (params.page === 'admin' || (!params.file && !params.id)) return false;

  const bc = new BroadcastChannel(TAB_CHANNEL);
  let handled = false;

  bc.onmessage = (e) => {
    if (e.data?.type === 'ack') {
      handled = true;
      // Existing tab will handle it; close this tab
      window.close();
      // Fallback if window.close() is blocked (e.g. not opened by script):
      // show a brief message then redirect to editor without params
      setTimeout(() => {
        document.body.innerHTML = `
          <div style="display:flex;align-items:center;justify-content:center;height:100vh;background:#111827;color:#9ca3af;font-family:sans-serif">
            <p>File opened in existing editor tab. <a href="?" style="color:#60a5fa">Open new editor</a></p>
          </div>`;
      }, 300);
    }
  };

  // Ask existing tabs to open the file
  bc.postMessage({ type: 'open', file: params.file, id: params.id, line: params.line });

  // Give existing tab 200ms to respond
  setTimeout(() => {
    if (!handled) {
      // No existing tab responded; proceed normally in this tab
      bc.close();
    }
  }, 200);

  // We can't block, so return false and let init() proceed.
  // If an ack arrives within 200ms, the tab will close itself.
  return false;
}

/** Listen for file-open requests from other tabs. */
function listenForTabHandoff(): void {
  const bc = new BroadcastChannel(TAB_CHANNEL);
  bc.onmessage = async (e) => {
    if (e.data?.type !== 'open') return;
    const { file, id, line } = e.data;

    // Resolve the target file
    let targetPath: string | null = null;
    let targetLine: number | null = line ?? null;

    if (file) {
      targetPath = file;
    } else if (id) {
      const result = await api.resolveOrgId(id);
      if (result) {
        targetPath = result.path;
        targetLine = result.line ?? line ?? null;
      }
    }

    if (targetPath) {
      // Acknowledge so the new tab can close
      bc.postMessage({ type: 'ack' });

      // Focus this window and open the file
      window.focus();
      pushNavHistory();
      state.pendingScrollLine = targetLine;
      fileSelector.value = targetPath;
      await loadFile(targetPath);
    }
  };
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
async function init(): Promise<void> {
  const params = parseUrlParams();
  const isAdminPage = params.page === 'admin';

  // Try to hand off to existing editor tab (non-blocking)
  tryHandoffToExistingTab(params);

  // Check auth
  const authed = await checkAuth();
  if (!authed) {
    showLogin();

    // Wire up login form
    document.getElementById('login-form')!.addEventListener('submit', async (e) => {
      e.preventDefault();
      const passwordInput = document.getElementById('password') as HTMLInputElement;
      const errorDiv = document.getElementById('login-error')!;

      const result = await handleLogin(passwordInput.value);
      if (result.ok) {
        errorDiv.classList.add('hidden');
        if (isAdminPage) {
          showAdmin();
          initAdmin();
        } else {
          showEditor();
          await bootstrap();
        }
      } else {
        errorDiv.textContent = result.error || 'Login failed';
        errorDiv.classList.remove('hidden');
      }
    });
    return;
  }

  if (isAdminPage) {
    showAdmin();
    initAdmin();
  } else {
    showEditor();
    await bootstrap();
  }
}

async function bootstrap(): Promise<void> {
  await loadFileList();

  // Listen for file-open requests from other tabs
  listenForTabHandoff();

  // SSE
  connectSSE(sseState, handleFileChanged, handleOpenFile);
  setupSSEReconnection(sseState, handleFileChanged, handleOpenFile, refreshAfterResume);

  // Handle URL params
  const params = parseUrlParams();
  const target = await resolveUrlParams(params);
  if (target) {
    state.pendingScrollLine = target.line;
    await loadFile(target.path);
  }
}

init();
