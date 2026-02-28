import {
  getScheduledTasks,
  createScheduledTask,
  toggleScheduledTask,
  deleteScheduledTask,
  runScheduledTaskNow,
  getScheduledTaskRuns,
  getScheduledTaskBudget,
} from './admin-api';

interface TaskRow {
  id: number;
  name: string;
  prompt: string;
  schedule_type: string;
  schedule_expr: string;
  enabled: boolean;
  max_turns: number;
  last_run_at?: string | null;
  next_run_at?: string | null;
}

function renderTaskRow(task: TaskRow, onRefresh: () => void): HTMLElement {
  const row = document.createElement('tr');
  row.className = 'border-b border-gray-700 hover:bg-gray-800/50';
  row.innerHTML = `
    <td class="px-3 py-2 text-sm text-gray-200 font-medium">${task.name}</td>
    <td class="px-3 py-2 text-xs text-gray-400">${task.schedule_type}: ${task.schedule_expr}</td>
    <td class="px-3 py-2 text-center">
      <button class="toggle-btn text-xs px-2 py-0.5 rounded ${
        task.enabled ? 'bg-green-900 text-green-300' : 'bg-gray-700 text-gray-400'
      }">${task.enabled ? 'Enabled' : 'Disabled'}</button>
    </td>
    <td class="px-3 py-2 text-xs text-gray-500">${task.last_run_at ? new Date(task.last_run_at).toLocaleString() : '-'}</td>
    <td class="px-3 py-2 text-xs text-gray-500">${task.next_run_at ? new Date(task.next_run_at).toLocaleString() : '-'}</td>
    <td class="px-3 py-2">
      <div class="flex gap-1">
        <button class="run-btn text-xs px-2 py-0.5 rounded bg-blue-900 text-blue-300 hover:bg-blue-800">Run Now</button>
        <button class="delete-btn text-xs px-2 py-0.5 rounded bg-red-900/50 text-red-400 hover:bg-red-900">Delete</button>
      </div>
    </td>
  `;

  row.querySelector('.toggle-btn')!.addEventListener('click', async () => {
    try {
      await toggleScheduledTask(task.id);
      onRefresh();
    } catch (e) {
      alert(`Error: ${e instanceof Error ? e.message : e}`);
    }
  });

  row.querySelector('.run-btn')!.addEventListener('click', async () => {
    try {
      await runScheduledTaskNow(task.id);
      alert(`Task "${task.name}" triggered.`);
      onRefresh();
    } catch (e) {
      alert(`Error: ${e instanceof Error ? e.message : e}`);
    }
  });

  row.querySelector('.delete-btn')!.addEventListener('click', async () => {
    if (!confirm(`Delete task "${task.name}"?`)) return;
    try {
      await deleteScheduledTask(task.id);
      onRefresh();
    } catch (e) {
      alert(`Error: ${e instanceof Error ? e.message : e}`);
    }
  });

  return row;
}

function renderCreateForm(container: HTMLElement, onRefresh: () => void): void {
  const form = document.createElement('div');
  form.className = 'bg-gray-800 rounded-lg p-4 border border-gray-700 max-w-2xl mt-4';
  form.innerHTML = `
    <h3 class="text-sm font-semibold text-white mb-3">Create Task</h3>
    <div class="grid gap-3">
      <input id="task-name" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white" placeholder="Task name" />
      <textarea id="task-prompt" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white h-20 resize-y" placeholder="Prompt for Claude"></textarea>
      <div class="flex gap-2">
        <select id="task-schedule-type" class="bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white">
          <option value="cron">Cron</option>
          <option value="interval">Interval</option>
        </select>
        <input id="task-schedule-expr" class="flex-1 bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white" placeholder="0 */6 * * * or 6h" />
        <input id="task-max-turns" type="number" value="5" min="1" max="50" class="w-20 bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white" placeholder="Turns" />
      </div>
      <div class="flex gap-2">
        <button id="task-create-btn" class="px-4 py-1.5 bg-green-600 hover:bg-green-700 text-white rounded text-sm font-medium">Create</button>
        <span id="task-create-status" class="text-sm text-gray-400 self-center"></span>
      </div>
    </div>
  `;

  const createBtn = form.querySelector('#task-create-btn') as HTMLButtonElement;
  const statusEl = form.querySelector('#task-create-status')!;

  createBtn.addEventListener('click', async () => {
    const name = (form.querySelector('#task-name') as HTMLInputElement).value.trim();
    const prompt = (form.querySelector('#task-prompt') as HTMLTextAreaElement).value.trim();
    const scheduleType = (form.querySelector('#task-schedule-type') as HTMLSelectElement).value;
    const scheduleExpr = (form.querySelector('#task-schedule-expr') as HTMLInputElement).value.trim();
    const maxTurns = parseInt((form.querySelector('#task-max-turns') as HTMLInputElement).value) || 5;

    if (!name || !prompt || !scheduleExpr) {
      statusEl.textContent = 'Fill in all fields';
      return;
    }

    createBtn.disabled = true;
    statusEl.textContent = 'Creating...';
    try {
      await createScheduledTask({
        name, prompt,
        schedule_type: scheduleType,
        schedule_expr: scheduleExpr,
        max_turns: maxTurns,
      });
      statusEl.textContent = 'Created!';
      (form.querySelector('#task-name') as HTMLInputElement).value = '';
      (form.querySelector('#task-prompt') as HTMLTextAreaElement).value = '';
      (form.querySelector('#task-schedule-expr') as HTMLInputElement).value = '';
      onRefresh();
    } catch (e) {
      statusEl.textContent = `Error: ${e instanceof Error ? e.message : e}`;
    } finally {
      createBtn.disabled = false;
    }
  });

  container.appendChild(form);
}

export function initTasksSection(container: HTMLElement): void {
  container.innerHTML = `
    <h2 class="text-lg font-semibold text-white mb-4">Scheduled Tasks</h2>
    <div id="tasks-budget" class="text-sm text-gray-400 mb-3"></div>
    <div id="tasks-status" class="text-sm text-gray-400 mb-3"></div>
    <div class="overflow-x-auto">
      <table class="w-full text-left">
        <thead>
          <tr class="border-b border-gray-600 text-xs text-gray-400 uppercase">
            <th class="px-3 py-2">Name</th>
            <th class="px-3 py-2">Schedule</th>
            <th class="px-3 py-2 text-center">Status</th>
            <th class="px-3 py-2">Last Run</th>
            <th class="px-3 py-2">Next Run</th>
            <th class="px-3 py-2">Actions</th>
          </tr>
        </thead>
        <tbody id="tasks-tbody"></tbody>
      </table>
    </div>
    <div id="tasks-create-form"></div>
    <div id="tasks-runs" class="mt-6"></div>
  `;

  const tbody = container.querySelector('#tasks-tbody')!;
  const statusEl = container.querySelector('#tasks-status')!;
  const budgetEl = container.querySelector('#tasks-budget')!;
  const runsContainer = container.querySelector('#tasks-runs')!;
  const createFormContainer = container.querySelector('#tasks-create-form') as HTMLElement;

  async function loadTasks() {
    statusEl.textContent = 'Loading...';
    tbody.innerHTML = '';
    try {
      const tasks = (await getScheduledTasks()) as TaskRow[];
      statusEl.textContent = `${tasks.length} tasks`;
      for (const task of tasks) {
        tbody.appendChild(renderTaskRow(task, loadTasks));
      }
      if (tasks.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" class="px-3 py-4 text-center text-gray-500">No scheduled tasks</td></tr>';
      }
    } catch (e) {
      statusEl.textContent = `Error: ${e instanceof Error ? e.message : e}`;
    }
  }

  async function loadBudget() {
    try {
      const budget = (await getScheduledTaskBudget()) as Record<string, unknown>;
      if (budget && typeof budget === 'object') {
        const used = budget.used_today ?? 0;
        const limit = budget.daily_limit ?? '?';
        budgetEl.textContent = `Budget: ${used}/${limit} runs today`;
      }
    } catch {
      // Budget endpoint may not exist yet; ignore
    }
  }

  async function loadRuns() {
    try {
      const runs = (await getScheduledTaskRuns()) as Array<Record<string, unknown>>;
      if (!runs || runs.length === 0) {
        runsContainer.innerHTML = '';
        return;
      }
      const recent = runs.slice(0, 10);
      runsContainer.innerHTML = `
        <details class="bg-gray-800 rounded-lg border border-gray-700">
          <summary class="px-4 py-2 text-sm font-medium text-gray-300 cursor-pointer hover:text-white">
            Recent Runs (${runs.length})
          </summary>
          <div class="px-4 pb-3 space-y-1">
            ${recent
              .map(
                (r) => `
              <div class="flex gap-3 text-xs text-gray-400 py-1 border-b border-gray-700/50">
                <span class="font-medium text-gray-300">${r.task_name || '?'}</span>
                <span>${r.started_at ? new Date(r.started_at as string).toLocaleString() : '-'}</span>
                <span class="${r.status === 'completed' ? 'text-green-400' : r.status === 'error' ? 'text-red-400' : 'text-yellow-400'}">${r.status || '?'}</span>
              </div>
            `,
              )
              .join('')}
          </div>
        </details>
      `;
    } catch {
      // Runs endpoint may not exist; ignore
    }
  }

  renderCreateForm(createFormContainer, loadTasks);
  loadTasks();
  loadBudget();
  loadRuns();
}
