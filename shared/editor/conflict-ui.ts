import type { SaveController } from './save-state';
import type { FileApi } from './file-api';

type Status = (msg: string, isError?: boolean) => void;

const REASON_TEXT: Record<string, string> = {
  stale: 'This file was changed on disk while you were editing.',
  overlap: 'Someone else edited the same lines you did; the changes could not be merged automatically.',
  base_unknown: 'This file changed on disk and the version you started from is no longer available to merge against.',
};

/**
 * Conflict modal + persistent banner. The modal asks for a decision; closing it
 * without one leaves the banner up (autosave is paused) so the state is never
 * invisible.
 */
export function initConflictUI(saver: SaveController, fileApi: FileApi, updateStatus: Status) {
  const modal = document.getElementById('conflict-modal')!;
  const banner = document.getElementById('conflict-banner')!;
  const reason = document.getElementById('conflict-reason')!;
  const localTime = document.getElementById('conflict-local-time')!;
  const remoteTime = document.getElementById('conflict-remote-time')!;
  const editMerged = document.getElementById('conflict-edit-merged')!;

  const fmt = (t: number | null | undefined) => (t ? new Date(t * 1000).toLocaleTimeString() : 'unknown');

  function showModal(): void {
    const info = saver.conflict;
    if (!info) return;
    reason.textContent = REASON_TEXT[info.reason] ?? REASON_TEXT.stale;
    localTime.textContent = fmt(saver.baseMtime);
    remoteTime.textContent = fmt(info.theirs?.modified);
    editMerged.classList.toggle('hidden', !info.merged);
    modal.classList.remove('hidden');
  }

  function hideModal(): void {
    modal.classList.add('hidden');
  }

  /** Reflect the controller's conflict state; called from the controller's onConflict. */
  function refresh(): void {
    if (saver.conflict) {
      banner.classList.remove('hidden');
      showModal();
    } else {
      banner.classList.add('hidden');
      hideModal();
    }
  }

  const on = (id: string, fn: () => void) => document.getElementById(id)?.addEventListener('click', fn);

  on('conflict-keep-mine', () => void saver.keepMine());
  on('conflict-keep-theirs', () => void saver.keepTheirs());
  on('conflict-backup-and-reload', async () => {
    if (await createBackup(saver, fileApi, updateStatus)) await saver.keepTheirs();
  });
  on('conflict-edit-merged', () => saver.editMerged());
  on('conflict-cancel', () => {
    hideModal();
    saver.dismissConflict();
  });
  on('conflict-resolve', showModal);
  modal.addEventListener('click', (e) => {
    if (e.target !== e.currentTarget) return;
    hideModal();
    saver.dismissConflict();
  });

  return { refresh };
}

/** Write the editor's current text to a timestamped sibling file. */
async function createBackup(saver: SaveController, fileApi: FileApi, updateStatus: Status): Promise<boolean> {
  const path = saver.path;
  if (!path) return false;

  const timestamp = new Date().toISOString().replace(/:/g, '-').split('.')[0];
  const fileName = path.slice(path.lastIndexOf('/') + 1);
  const hasExtension = fileName.includes('.') && !fileName.startsWith('.');
  const backupPath = hasExtension
    ? path.replace(/(\.\w+)$/, `.backup-${timestamp}$1`)
    : `${path}.backup-${timestamp}`;
  try {
    updateStatus('Creating backup...');
    const result = await fileApi.save(backupPath, saver.text());
    if (result.status === 'conflict') {
      updateStatus('Backup path already exists', true);
      return false;
    }
    updateStatus(`Backup created: ${backupPath.split('/').pop()}`);
    return true;
  } catch (e) {
    updateStatus(`Failed to create backup: ${(e as Error).message}`, true);
    return false;
  }
}
