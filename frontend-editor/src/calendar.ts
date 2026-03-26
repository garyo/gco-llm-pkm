import type { EditorState } from './types';

/** Render the calendar grid for the current month. */
export function renderCalendar(state: EditorState): void {
  const grid = document.getElementById('cal-days-grid');
  const label = document.getElementById('cal-month-label');
  if (!grid || !label) return;

  const year = state.calendarMonth.getFullYear();
  const month = state.calendarMonth.getMonth();
  label.textContent = state.calendarMonth.toLocaleString('default', {
    month: 'long',
    year: 'numeric',
  });

  const firstDay = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const today = new Date();

  let html = '';
  for (let i = 0; i < firstDay; i++) html += '<span></span>';

  for (let d = 1; d <= daysInMonth; d++) {
    const dateKey = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    const entry = state.journalDates.get(dateKey);
    const isToday =
      today.getFullYear() === year && today.getMonth() === month && today.getDate() === d;

    const classes = ['py-0.5', 'rounded', 'cursor-pointer', 'hover:bg-blue-100'];
    if (entry) classes.push('underline', 'font-medium', 'text-blue-700');
    else classes.push('text-gray-400');
    if (isToday) classes.push('bg-blue-50', 'ring-1', 'ring-blue-400');

    html += `<span class="${classes.join(' ')}" data-date="${dateKey}">${d}</span>`;
  }

  grid.innerHTML = html;
}

/** Initialize calendar popup event handlers. */
export function initCalendar(
  state: EditorState,
  onDateClick: (dateKey: string, entry: { path: string; dir: string } | undefined) => void,
): void {
  const toggleBtn = document.getElementById('calendar-toggle-btn');
  const toggleBtnMobile = document.getElementById('calendar-toggle-btn-mobile');
  const popup = document.getElementById('calendar-popup');
  const prevBtn = document.getElementById('cal-prev-month');
  const nextBtn = document.getElementById('cal-next-month');
  const grid = document.getElementById('cal-days-grid');

  if (!popup) return;

  const handleToggle = (e: Event) => {
    e.stopPropagation();
    const isHidden = popup.classList.contains('hidden');
    if (isHidden) {
      state.calendarMonth = new Date();
      renderCalendar(state);
      popup.classList.remove('hidden');
    } else {
      popup.classList.add('hidden');
    }
  };

  toggleBtn?.addEventListener('click', handleToggle);
  toggleBtnMobile?.addEventListener('click', handleToggle);

  prevBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    state.calendarMonth.setMonth(state.calendarMonth.getMonth() - 1);
    renderCalendar(state);
  });

  nextBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    state.calendarMonth.setMonth(state.calendarMonth.getMonth() + 1);
    renderCalendar(state);
  });

  grid?.addEventListener('click', (e) => {
    const target = e.target as HTMLElement;
    const dateKey = target.dataset?.date;
    if (!dateKey) return;

    popup.classList.add('hidden');
    const entry = state.journalDates.get(dateKey);
    onDateClick(dateKey, entry);
  });

  document.addEventListener('click', (e) => {
    if (
      !popup.classList.contains('hidden') &&
      !popup.contains(e.target as Node) &&
      e.target !== toggleBtn &&
      e.target !== toggleBtnMobile &&
      !toggleBtnMobile?.contains(e.target as Node) &&
      !toggleBtn?.contains(e.target as Node)
    ) {
      popup.classList.add('hidden');
    }
  });
}
