/** Select `path` in the file dropdown, adding an option for it when the current filters hide it. */
export function showInSelector(select: HTMLSelectElement, path: string): void {
  if (!Array.from(select.options).some((opt) => opt.value === path)) {
    const option = document.createElement('option');
    option.value = path;
    option.textContent = path.split('/').pop() || path;
    // After the "Select a file..." placeholder. Use a direct child: `options[1]`
    // may sit inside an <optgroup>, and insertBefore would throw.
    select.insertBefore(option, select.children[1] ?? null);
  }
  select.value = path;
}
