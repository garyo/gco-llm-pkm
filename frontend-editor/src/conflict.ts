import type { ConflictAction, EditorState } from './types';
import * as api from './api';

export function showConflictModal(state: EditorState, remoteMtime: number | null): void {
  const modal = document.getElementById('conflict-modal')!;
  const localTime = document.getElementById('conflict-local-time')!;
  const remoteTime = document.getElementById('conflict-remote-time')!;

  if (state.currentFileMtime) {
    localTime.textContent = new Date(state.currentFileMtime * 1000).toLocaleTimeString();
  } else {
    localTime.textContent = 'Unknown';
  }

  remoteTime.textContent = remoteMtime
    ? new Date(remoteMtime * 1000).toLocaleTimeString()
    : 'Newer than local';

  modal.classList.remove('hidden');
}

export function hideConflictModal(): void {
  document.getElementById('conflict-modal')!.classList.add('hidden');
}

export async function resolveConflict(
  action: ConflictAction,
  state: EditorState,
  saveFileFn: (force?: boolean) => Promise<void>,
  loadFileFn: (path: string) => Promise<void>,
  updateStatus: (msg: string, isError?: boolean) => void,
): Promise<void> {
  hideConflictModal();
  state.conflictDetected = false;

  if (action === 'save-mine') {
    updateStatus('Saving your version...');
    await saveFileFn(true);
  } else if (action === 'reload-remote') {
    if (state.currentFile) await loadFileFn(state.currentFile);
  } else if (action === 'backup-and-reload') {
    await createBackup(state, updateStatus);
    if (state.currentFile) await loadFileFn(state.currentFile);
  }
}

async function createBackup(
  state: EditorState,
  updateStatus: (msg: string, isError?: boolean) => void,
): Promise<void> {
  if (!state.editorView || !state.currentFile) return;

  const content = state.editorView.state.doc.toString();
  const timestamp = new Date().toISOString().replace(/:/g, '-').split('.')[0];

  const lastSlash = state.currentFile.lastIndexOf('/');
  const fileName = lastSlash >= 0 ? state.currentFile.substring(lastSlash + 1) : state.currentFile;
  const hasExtension = fileName.includes('.') && !fileName.startsWith('.');

  const backupPath = hasExtension
    ? state.currentFile.replace(/(\.\w+)$/, `.backup-${timestamp}$1`)
    : `${state.currentFile}.backup-${timestamp}`;

  try {
    updateStatus('Creating backup...');
    const data = await api.saveFile(backupPath, content, null, true);
    if (data.status === undefined || data.status !== 409) {
      const fname = backupPath.split('/').pop() || 'backup';
      updateStatus(`Backup created: ${fname}`);
    }
  } catch (e: unknown) {
    const err = e instanceof Error ? e : new Error(String(e));
    if (err.name === 'AbortError') {
      updateStatus('Backup timed out, verifying...', true);
      const exists = await api.checkBackupExists(backupPath);
      if (exists) {
        updateStatus(`Backup created: ${backupPath.split('/').pop()}`);
      } else {
        updateStatus('Backup creation timed out.', true);
      }
    } else {
      console.error('Failed to create backup:', err);
      updateStatus('Failed to create backup', true);
    }
  }
}

/** Wire up conflict modal button handlers. */
export function initConflictModal(
  state: EditorState,
  saveFileFn: (force?: boolean) => Promise<void>,
  loadFileFn: (path: string) => Promise<void>,
  updateStatus: (msg: string, isError?: boolean) => void,
): void {
  document.getElementById('conflict-save-mine')?.addEventListener('click', () =>
    resolveConflict('save-mine', state, saveFileFn, loadFileFn, updateStatus)
  );
  document.getElementById('conflict-reload-remote')?.addEventListener('click', () =>
    resolveConflict('reload-remote', state, saveFileFn, loadFileFn, updateStatus)
  );
  document.getElementById('conflict-backup-and-reload')?.addEventListener('click', () =>
    resolveConflict('backup-and-reload', state, saveFileFn, loadFileFn, updateStatus)
  );
  document.getElementById('conflict-cancel')?.addEventListener('click', hideConflictModal);

  // Close on backdrop click
  document.getElementById('conflict-modal')?.addEventListener('click', (e) => {
    if (e.target === e.currentTarget) hideConflictModal();
  });
}
