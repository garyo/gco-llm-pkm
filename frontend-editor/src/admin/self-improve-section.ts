import { triggerSelfImprove, getSelfImproveLog, getSelfImproveMemory } from './admin-api';

export function initSelfImproveSection(container: HTMLElement): void {
  container.innerHTML = `
    <h2 class="text-lg font-semibold text-white mb-4">Self-Improvement Agent</h2>

    <div class="max-w-3xl space-y-6">
      <!-- Trigger -->
      <div class="bg-gray-800 rounded-lg p-4 border border-gray-700">
        <div class="flex items-center gap-3">
          <button id="si-run-btn" class="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded font-medium text-sm">Run Now</button>
          <span id="si-run-status" class="text-sm text-gray-400"></span>
        </div>
        <p class="text-xs text-gray-500 mt-2">Analyzes recent feedback and interactions, updates learned rules and agent memory.</p>
      </div>

      <!-- Log -->
      <div>
        <h3 class="text-sm font-semibold text-gray-300 mb-2">Run Log</h3>
        <div id="si-log" class="bg-gray-800 rounded-lg border border-gray-700 p-4 text-sm text-gray-300 font-mono whitespace-pre-wrap max-h-96 overflow-auto">
          Loading...
        </div>
      </div>

      <!-- Memory -->
      <div>
        <h3 class="text-sm font-semibold text-gray-300 mb-2">Agent Memory</h3>
        <div id="si-memory" class="bg-gray-800 rounded-lg border border-gray-700 p-4 text-sm text-gray-300 font-mono whitespace-pre-wrap max-h-96 overflow-auto">
          Loading...
        </div>
      </div>
    </div>
  `;

  const runBtn = container.querySelector('#si-run-btn') as HTMLButtonElement;
  const runStatus = container.querySelector('#si-run-status')!;
  const logEl = container.querySelector('#si-log')!;
  const memoryEl = container.querySelector('#si-memory')!;

  runBtn.addEventListener('click', async () => {
    runBtn.disabled = true;
    runStatus.textContent = 'Running... (this may take a minute)';
    try {
      const result = await triggerSelfImprove();
      runStatus.textContent = result.message || 'Completed!';
      loadLog();
      loadMemory();
    } catch (e) {
      runStatus.textContent = `Error: ${e instanceof Error ? e.message : e}`;
    } finally {
      runBtn.disabled = false;
    }
  });

  async function loadLog() {
    try {
      const data = await getSelfImproveLog() as Record<string, unknown>;
      if (data.last_run) {
        const run = data.last_run as Record<string, unknown>;
        const lines: string[] = [];
        if (run.started_at) lines.push(`Started: ${run.started_at}`);
        if (run.completed_at) lines.push(`Completed: ${run.completed_at}`);
        if (run.status) lines.push(`Status: ${run.status}`);
        if (run.summary) lines.push(`\nSummary:\n${run.summary}`);
        if (run.rules_created != null) lines.push(`Rules created: ${run.rules_created}`);
        if (run.rules_updated != null) lines.push(`Rules updated: ${run.rules_updated}`);
        logEl.textContent = lines.join('\n');
      } else {
        logEl.textContent = JSON.stringify(data, null, 2);
      }

      // Show history if available
      if (data.recent_runs && Array.isArray(data.recent_runs) && (data.recent_runs as unknown[]).length > 0) {
        const history = (data.recent_runs as Array<Record<string, unknown>>)
          .map((r) => `${r.started_at || '?'} - ${r.status || '?'}`)
          .join('\n');
        logEl.textContent += `\n\n--- History ---\n${history}`;
      }
    } catch (e) {
      logEl.textContent = `Error loading log: ${e instanceof Error ? e.message : e}`;
    }
  }

  async function loadMemory() {
    try {
      const data = await getSelfImproveMemory();
      if (typeof data === 'object' && data !== null) {
        const entries = Object.entries(data as Record<string, unknown>);
        if (entries.length === 0) {
          memoryEl.textContent = 'No memory files found.';
        } else {
          memoryEl.textContent = entries
            .map(([name, content]) => `=== ${name} ===\n${content}`)
            .join('\n\n');
        }
      } else {
        memoryEl.textContent = String(data);
      }
    } catch (e) {
      memoryEl.textContent = `Error loading memory: ${e instanceof Error ? e.message : e}`;
    }
  }

  loadLog();
  loadMemory();
}
