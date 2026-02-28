import type { FileInfo } from './types';

/** Filter, sort, and populate the file selector dropdown. */
export function filterAndPopulateFiles(
  allFiles: FileInfo[],
  sourceFilter: HTMLSelectElement,
  typeFilter: HTMLSelectElement,
  searchFilter: HTMLInputElement,
  fileSelector: HTMLSelectElement,
  fileCount: HTMLElement,
  fileCountMobile: HTMLElement | null,
): void {
  const sourceValue = sourceFilter.value;
  const typeValue = typeFilter.value;
  const searchValue = searchFilter.value.toLowerCase();

  const filtered = allFiles.filter((f) => {
    if (sourceValue !== 'all' && f.dir !== sourceValue) return false;
    if (typeValue !== 'all' && f.type !== typeValue) return false;
    if (
      searchValue &&
      !f.name.toLowerCase().includes(searchValue) &&
      !f.path.toLowerCase().includes(searchValue)
    )
      return false;
    return true;
  });

  const journals = filtered.filter((f) => f.type === 'journal');
  const pages = filtered.filter((f) => f.type === 'page');
  const other = filtered.filter((f) => f.type === 'other');

  journals.sort((a, b) => b.modified - a.modified);
  pages.sort((a, b) => a.name.localeCompare(b.name));
  other.sort((a, b) => a.name.localeCompare(b.name));

  const sorted = [...journals, ...pages, ...other];

  fileSelector.innerHTML = '<option value="">Select a file...</option>';

  const groups: [string, FileInfo[]][] = [
    ['Org Journals', sorted.filter((f) => f.dir === 'org' && f.type === 'journal')],
    ['Org Pages', sorted.filter((f) => f.dir === 'org' && f.type === 'page')],
    ['Org Other', sorted.filter((f) => f.dir === 'org' && f.type === 'other')],
    ['Logseq Journals', sorted.filter((f) => f.dir === 'logseq' && f.type === 'journal')],
    ['Logseq Pages', sorted.filter((f) => f.dir === 'logseq' && f.type === 'page')],
    ['Logseq Other', sorted.filter((f) => f.dir === 'logseq' && f.type === 'other')],
  ];

  for (const [label, files] of groups) {
    if (files.length === 0) continue;
    const group = document.createElement('optgroup');
    group.label = `${label} (${files.length})`;
    for (const file of files) {
      const option = document.createElement('option');
      option.value = file.full_path;
      option.textContent = file.path;
      group.appendChild(option);
    }
    fileSelector.appendChild(group);
  }

  const countText = `${sorted.length} file${sorted.length !== 1 ? 's' : ''}`;
  fileCount.textContent = countText;
  if (fileCountMobile) fileCountMobile.textContent = countText;
}

/** Build the journal date lookup map for the calendar. */
export function buildJournalDates(
  allFiles: FileInfo[],
): Map<string, { path: string; dir: string }> {
  const map = new Map<string, { path: string; dir: string }>();
  for (const f of allFiles) {
    if (f.type !== 'journal') continue;
    const m = f.name.match(/(\d{4})[-_](\d{2})[-_](\d{2})/);
    if (!m) continue;
    const dateKey = `${m[1]}-${m[2]}-${m[3]}`;
    if (!map.has(dateKey) || f.dir === 'org') {
      map.set(dateKey, { path: f.full_path, dir: f.dir });
    }
  }
  return map;
}

/** Toggle mobile filter controls visibility. */
export function toggleFilterControls(): void {
  const filterControls = document.getElementById('filter-controls');
  const toggleIcon = document.getElementById('filter-toggle-icon');
  if (!filterControls || !toggleIcon) return;

  const isHidden = filterControls.classList.contains('hidden');
  if (isHidden) {
    filterControls.classList.remove('hidden');
    filterControls.classList.add('flex');
    toggleIcon.style.transform = 'rotate(90deg)';
  } else {
    filterControls.classList.add('hidden');
    filterControls.classList.remove('flex');
    toggleIcon.style.transform = 'rotate(0deg)';
  }
}

/** Close mobile filter controls. */
export function closeFilterControls(): void {
  const filterControls = document.getElementById('filter-controls');
  const toggleIcon = document.getElementById('filter-toggle-icon');
  if (!filterControls || !toggleIcon) return;

  if (!filterControls.classList.contains('hidden')) {
    filterControls.classList.add('hidden');
    filterControls.classList.remove('flex');
    toggleIcon.style.transform = 'rotate(0deg)';
  }
}
