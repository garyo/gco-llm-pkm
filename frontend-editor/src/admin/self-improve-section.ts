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

  function formatRunEntry(run: Record<string, unknown>): string {
    const lines: string[] = [];
    if (run.started_at) lines.push(`Started:  ${run.started_at}`);
    if (run.completed_at) lines.push(`Finished: ${run.completed_at}`);
    if (run.trigger) lines.push(`Trigger:  ${run.trigger}`);
    if (run.turns_used != null) lines.push(`Turns:    ${run.turns_used}`);
    if (run.input_tokens != null || run.output_tokens != null) {
      lines.push(`Tokens:   ${run.input_tokens ?? '?'} in / ${run.output_tokens ?? '?'} out`);
    }
    if (run.error) lines.push(`Error:    ${run.error}`);
    if (run.summary) lines.push(`\nSummary:\n${run.summary}`);
    if (run.actions_summary) {
      const actions = run.actions_summary;
      if (typeof actions === 'string') {
        lines.push(`\nActions:\n${actions}`);
      } else if (typeof actions === 'object') {
        lines.push(`\nActions:\n${JSON.stringify(actions, null, 2)}`);
      }
    }
    return lines.join('\n');
  }

  async function loadLog() {
    try {
      const data = await getSelfImproveLog() as Record<string, unknown>;
      const parts: string[] = [];

      // Most recent run: prefer first entry from recent_runs (from DB, always current)
      // over last_run (in-memory, stale after server restart)
      const recentRuns = (data.recent_runs && Array.isArray(data.recent_runs))
        ? data.recent_runs as Array<Record<string, unknown>>
        : [];
      const latestRun = recentRuns.length > 0
        ? recentRuns[0]
        : (data.last_run && typeof data.last_run === 'object')
          ? data.last_run as Record<string, unknown>
          : null;

      if (latestRun) {
        parts.push('=== Last Run ===');
        parts.push(formatRunEntry(latestRun));
      }

      // Feedback stats
      if (data.feedback_stats && typeof data.feedback_stats === 'object') {
        const stats = data.feedback_stats as Record<string, unknown>;
        const statLines = Object.entries(stats)
          .map(([k, v]) => `  ${k}: ${v}`)
          .join('\n');
        if (statLines) {
          parts.push('\n=== Feedback Stats ===');
          parts.push(statLines);
        }
      }

      // Recent runs history (skip first since it's already shown above)
      if (recentRuns.length > 1) {
        parts.push('\n=== Recent Runs ===');
        for (const run of recentRuns.slice(1)) {
          const time = run.started_at || '?';
          const trigger = run.trigger ? ` (${run.trigger})` : '';
          const turns = run.turns_used != null ? `, ${run.turns_used} turns` : '';
          const err = run.error ? ` [ERROR: ${String(run.error).slice(0, 80)}]` : '';
          const summary = run.summary ? ` - ${String(run.summary).slice(0, 120)}` : '';
          parts.push(`  ${time}${trigger}${turns}${err}${summary}`);
        }
      }

      logEl.textContent = parts.length > 0 ? parts.join('\n') : 'No run data available.';
    } catch (e) {
      logEl.textContent = `Error loading log: ${e instanceof Error ? e.message : e}`;
    }
  }

  /** Reverse dated ## sections so newest appears first. */
  function reverseDateSections(text: string): string {
    // Split on ## headings that start with a date (e.g. "## 2026-02-15 ...")
    const parts = text.split(/(?=^## \d{4}-\d{2}-\d{2})/m);
    if (parts.length <= 2) return text; // 0-1 dated sections, nothing to reverse
    // parts[0] is the preamble (e.g. "# Title\n\n"), rest are dated sections
    const preamble = parts[0];
    const sections = parts.slice(1);
    return preamble + sections.reverse().join('');
  }

  async function loadMemory() {
    try {
      const data = await getSelfImproveMemory() as Record<string, unknown>;
      // API returns {"memory": {"category_name": "file_content", ...}}
      const memory = (data.memory && typeof data.memory === 'object')
        ? data.memory as Record<string, unknown>
        : data;
      const entries = Object.entries(memory);
      if (entries.length === 0) {
        memoryEl.textContent = 'No memory files found.';
      } else {
        memoryEl.textContent = entries
          .map(([name, content]) => {
            let text = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
            if (typeof content === 'string') {
              text = reverseDateSections(text);
            }
            return `=== ${name} ===\n${text}`;
          })
          .join('\n\n');
      }
    } catch (e) {
      memoryEl.textContent = `Error loading memory: ${e instanceof Error ? e.message : e}`;
    }
  }

  loadLog();
  loadMemory();
}
