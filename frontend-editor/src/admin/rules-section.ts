import type { LearnedRule } from '../types';
import { getLearnedRules, updateLearnedRule, deleteLearnedRule } from './admin-api';

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

function esc(s: string): string {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function createRuleCard(rule: LearnedRule, onRefresh: () => void): HTMLElement {
  const card = document.createElement('div');
  card.className = 'bg-gray-800 rounded-lg border border-gray-700 overflow-hidden';

  const typeBadge = `<span class="px-1.5 py-0.5 bg-gray-700 text-gray-300 rounded text-xs">${esc(rule.rule_type)}</span>`;
  const activeBadge = rule.is_active
    ? '<span class="px-1.5 py-0.5 bg-green-900 text-green-300 rounded text-xs">Active</span>'
    : '<span class="px-1.5 py-0.5 bg-gray-700 text-gray-400 rounded text-xs">Inactive</span>';

  // Truncate for summary line
  const summaryText = rule.rule_text.length > 120
    ? rule.rule_text.slice(0, 120) + '...'
    : rule.rule_text;

  card.innerHTML = `
    <div class="p-4">
      <div class="flex items-start justify-between gap-2 mb-1">
        <div class="flex items-center gap-2 flex-wrap">
          ${typeBadge}
          ${activeBadge}
          <span class="text-xs text-gray-500">conf: ${rule.confidence.toFixed(2)} | hits: ${rule.hit_count} | ${formatDate(rule.created_at)}</span>
        </div>
        <div class="flex gap-1 shrink-0">
          <button class="toggle-btn px-2 py-1 text-xs rounded ${
            rule.is_active ? 'bg-yellow-900/50 hover:bg-yellow-900 text-yellow-400' : 'bg-green-900/50 hover:bg-green-900 text-green-400'
          }">${rule.is_active ? 'Deactivate' : 'Activate'}</button>
          <button class="edit-btn px-2 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-xs">Edit</button>
          <button class="delete-btn px-2 py-1 bg-red-900/50 hover:bg-red-900 text-red-400 rounded text-xs">Delete</button>
        </div>
      </div>
      <p class="summary-text text-sm text-gray-200 mt-2">${esc(summaryText)}</p>
    </div>
    <details class="body-details">
      <summary class="px-4 py-2 text-xs text-gray-400 cursor-pointer hover:text-gray-300 bg-gray-800/50 border-t border-gray-700">
        Full rule text${rule.rule_data ? ' + data' : ''}
      </summary>
      <div class="px-4 py-3 border-t border-gray-700 bg-gray-900/50 space-y-2">
        <pre class="text-sm text-gray-300 whitespace-pre-wrap break-words">${esc(rule.rule_text)}</pre>
        ${rule.rule_data ? `<div class="mt-2"><span class="text-xs text-gray-500">Rule data:</span><pre class="text-xs text-gray-400 font-mono whitespace-pre-wrap mt-1">${esc(JSON.stringify(rule.rule_data, null, 2))}</pre></div>` : ''}
        ${rule.source_query_ids?.length ? `<div class="text-xs text-gray-500">Source queries: ${rule.source_query_ids.join(', ')}</div>` : ''}
      </div>
    </details>
    <div class="edit-form hidden border-t border-gray-700 p-4 bg-gray-900/50">
      <div class="space-y-3">
        <div>
          <label class="block text-xs text-gray-400 mb-1">Rule text</label>
          <textarea class="edit-rule-text w-full bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white h-32 resize-y">${esc(rule.rule_text)}</textarea>
        </div>
        <div class="flex gap-3">
          <div>
            <label class="block text-xs text-gray-400 mb-1">Confidence (0-1)</label>
            <input type="number" class="edit-confidence w-24 bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white" value="${rule.confidence}" min="0" max="1" step="0.05" />
          </div>
        </div>
        <div class="flex gap-2 items-center">
          <button class="save-btn px-3 py-1.5 bg-green-600 hover:bg-green-700 text-white rounded text-sm font-medium">Save</button>
          <button class="cancel-btn px-3 py-1.5 bg-gray-600 hover:bg-gray-500 text-white rounded text-sm font-medium">Cancel</button>
          <span class="edit-status text-sm text-gray-400"></span>
        </div>
      </div>
    </div>
  `;

  const editBtn = card.querySelector('.edit-btn') as HTMLButtonElement;
  const bodyDetails = card.querySelector('.body-details') as HTMLDetailsElement;
  const editForm = card.querySelector('.edit-form') as HTMLElement;
  const saveBtn = card.querySelector('.save-btn') as HTMLButtonElement;
  const cancelBtn = card.querySelector('.cancel-btn') as HTMLButtonElement;
  const editStatus = card.querySelector('.edit-status')!;

  // Toggle active
  card.querySelector('.toggle-btn')!.addEventListener('click', async () => {
    try {
      await updateLearnedRule(rule.id, { is_active: !rule.is_active });
      onRefresh();
    } catch (e) {
      alert(`Error: ${e instanceof Error ? e.message : e}`);
    }
  });

  // Edit
  editBtn.addEventListener('click', () => {
    bodyDetails.classList.add('hidden');
    editForm.classList.remove('hidden');
    editBtn.classList.add('hidden');
  });

  cancelBtn.addEventListener('click', () => {
    editForm.classList.add('hidden');
    bodyDetails.classList.remove('hidden');
    editBtn.classList.remove('hidden');
    editStatus.textContent = '';
  });

  saveBtn.addEventListener('click', async () => {
    const ruleText = (card.querySelector('.edit-rule-text') as HTMLTextAreaElement).value.trim();
    const confidence = parseFloat((card.querySelector('.edit-confidence') as HTMLInputElement).value);

    if (!ruleText) {
      editStatus.textContent = 'Rule text cannot be empty';
      return;
    }

    saveBtn.disabled = true;
    editStatus.textContent = 'Saving...';
    try {
      await updateLearnedRule(rule.id, { rule_text: ruleText, confidence });
      editStatus.textContent = 'Saved!';
      setTimeout(() => onRefresh(), 500);
    } catch (e) {
      editStatus.textContent = `Error: ${e instanceof Error ? e.message : e}`;
    } finally {
      saveBtn.disabled = false;
    }
  });

  // Delete
  card.querySelector('.delete-btn')!.addEventListener('click', async () => {
    if (!confirm(`Delete rule: "${rule.rule_text.slice(0, 80)}..."?`)) return;
    try {
      await deleteLearnedRule(rule.id);
      onRefresh();
    } catch (e) {
      alert(`Error: ${e instanceof Error ? e.message : e}`);
    }
  });

  return card;
}

export function initRulesSection(container: HTMLElement): void {
  container.innerHTML = `
    <h2 class="text-lg font-semibold text-white mb-4">Learned Rules</h2>
    <div id="rules-status" class="text-sm text-gray-400 mb-3"></div>
    <div id="rules-list" class="space-y-3 max-w-4xl"></div>
  `;

  const listEl = container.querySelector('#rules-list')!;
  const statusEl = container.querySelector('#rules-status')!;

  async function loadRules() {
    statusEl.textContent = 'Loading...';
    listEl.innerHTML = '';
    try {
      const rules = await getLearnedRules();
      statusEl.textContent = `${rules.length} rules (${rules.filter((r) => r.is_active).length} active)`;
      if (rules.length === 0) {
        listEl.innerHTML = '<p class="text-gray-500">No learned rules yet</p>';
        return;
      }
      for (const rule of rules) {
        listEl.appendChild(createRuleCard(rule, loadRules));
      }
    } catch (e) {
      statusEl.textContent = `Error: ${e instanceof Error ? e.message : e}`;
    }
  }

  loadRules();
}
