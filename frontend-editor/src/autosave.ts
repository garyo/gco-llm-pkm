import type { EditorState } from './types';
import { STORAGE_KEYS } from './types';
import * as api from './api';

export function refreshAutoSaveSettings(state: EditorState): void {
  state.cachedAutoSaveEnabled =
    localStorage.getItem(STORAGE_KEYS.AUTO_SAVE_ENABLED) !== 'false';
  state.cachedAutoSaveDelay = parseInt(
    localStorage.getItem(STORAGE_KEYS.AUTO_SAVE_DELAY) || '2000',
  );
}

export function cleanupAutoSaveTimers(state: EditorState): void {
  if (state.autoSaveTimeout) {
    clearTimeout(state.autoSaveTimeout);
    state.autoSaveTimeout = null;
  }
  if (state.statusUpdateInterval) {
    clearInterval(state.statusUpdateInterval);
    state.statusUpdateInterval = null;
  }
}

export function scheduleAutoSave(
  state: EditorState,
  updateStatus: (msg: string, isError?: boolean) => void,
  performAutoSave: () => Promise<void>,
): void {
  if (!state.cachedAutoSaveEnabled || !state.isDirty || !state.currentFile || state.conflictDetected)
    return;

  if (state.autoSaveTimeout) clearTimeout(state.autoSaveTimeout);

  const seconds = (state.cachedAutoSaveDelay / 1000).toFixed(0);
  updateStatus(`Modified \u2022 Auto-saving in ${seconds}s...`);

  state.autoSaveTimeout = setTimeout(
    () => performAutoSave(),
    state.cachedAutoSaveDelay,
  ) as unknown as number;
}

export function startStatusUpdateTimer(
  state: EditorState,
  updateStatus: (msg: string, isError?: boolean) => void,
): void {
  if (state.statusUpdateInterval) clearInterval(state.statusUpdateInterval);

  state.statusUpdateInterval = setInterval(() => {
    if (!state.isDirty && state.lastSaveTime && !state.conflictDetected) {
      const secondsAgo = Math.floor((Date.now() - state.lastSaveTime) / 1000);
      if (secondsAgo < 60) {
        updateStatus(`Saved ${secondsAgo}s ago`);
      } else {
        updateStatus(`Saved ${Math.floor(secondsAgo / 60)}m ago`);
      }
    }
  }, 5000) as unknown as number;
}

export async function performAutoSave(
  state: EditorState,
  updateStatus: (msg: string, isError?: boolean) => void,
  showConflictModal: (mtime: number | null) => void,
): Promise<void> {
  if (!state.editorView || !state.currentFile || !state.isDirty) return;
  if (state.conflictDetected) {
    updateStatus('Auto-save paused (conflict detected)', true);
    return;
  }

  state.saveInProgress = true;
  const saveButton = document.getElementById('save-button') as HTMLButtonElement;

  try {
    updateStatus('Auto-saving...');
    const content = state.editorView.state.doc.toString();

    const data = await api.saveFile(state.currentFile, content, state.currentFileMtime);

    if (data.status === 409) {
      state.conflictDetected = true;
      updateStatus('File changed on disk - conflicts need resolution', true);
      showConflictModal(null);
      return;
    }

    state.currentFileMtime = data.modified;
    state.isDirty = false;
    state.lastSaveTime = Date.now();
    if (saveButton) saveButton.disabled = true;

    const size = (data.size / 1024).toFixed(1);
    updateStatus(`Auto-saved (${size} KB)`);
    startStatusUpdateTimer(state, updateStatus);
  } catch (e: unknown) {
    const err = e instanceof Error ? e : new Error(String(e));
    if (err.name === 'AbortError') {
      updateStatus('Auto-save timed out, verifying...', true);
      const content = state.editorView!.state.doc.toString();
      const verified = await api.verifySaveCompleted(state.currentFile, content);
      if (verified) {
        updateStatus('Auto-saved (response was slow)');
        state.isDirty = false;
        state.lastSaveTime = Date.now();
        if (saveButton) saveButton.disabled = true;
        startStatusUpdateTimer(state, updateStatus);
      } else {
        updateStatus('Auto-save timed out. Manual save recommended.', true);
      }
    } else {
      updateStatus(`Auto-save failed: ${err.message}`, true);
      console.error('Auto-save error:', err);
    }
  } finally {
    state.saveInProgress = false;
  }
}
