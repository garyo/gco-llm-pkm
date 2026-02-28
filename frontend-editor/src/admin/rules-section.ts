import type { LearnedRule } from '../types';
import { getLearnedRules, updateLearnedRule, deleteLearnedRule } from './admin-api';

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

function createRuleRow(rule: LearnedRule, onRefresh: () => void): HTMLElement {
  const row = document.createElement('tr');
  row.className = 'border-b border-gray-700 hover:bg-gray-800/50';
  row.innerHTML = `
    <td class="px-3 py-2 text-xs">
      <span class="px-1.5 py-0.5 rounded bg-gray-700 text-gray-300">${rule.rule_type}</span>
    </td>
    <td class="px-3 py-2 text-sm text-gray-200 max-w-md truncate" title="${rule.rule_text.replace(/"/g, '&quot;')}">${rule.rule_text}</td>
    <td class="px-3 py-2 text-sm text-gray-400 text-center">${rule.confidence.toFixed(2)}</td>
    <td class="px-3 py-2 text-sm text-gray-400 text-center">${rule.hit_count}</td>
    <td class="px-3 py-2 text-center">
      <button class="toggle-btn text-xs px-2 py-0.5 rounded ${
        rule.is_active ? 'bg-green-900 text-green-300' : 'bg-gray-700 text-gray-400'
      }">${rule.is_active ? 'Active' : 'Inactive'}</button>
    </td>
    <td class="px-3 py-2 text-xs text-gray-500">${formatDate(rule.created_at)}</td>
    <td class="px-3 py-2 text-center">
      <button class="delete-btn text-xs px-2 py-0.5 rounded bg-red-900/50 text-red-400 hover:bg-red-900">Delete</button>
    </td>
  `;

  row.querySelector('.toggle-btn')!.addEventListener('click', async () => {
    try {
      await updateLearnedRule(rule.id, { is_active: !rule.is_active });
      onRefresh();
    } catch (e) {
      alert(`Error: ${e instanceof Error ? e.message : e}`);
    }
  });

  row.querySelector('.delete-btn')!.addEventListener('click', async () => {
    if (!confirm(`Delete rule: "${rule.rule_text.slice(0, 80)}..."?`)) return;
    try {
      await deleteLearnedRule(rule.id);
      onRefresh();
    } catch (e) {
      alert(`Error: ${e instanceof Error ? e.message : e}`);
    }
  });

  return row;
}

export function initRulesSection(container: HTMLElement): void {
  container.innerHTML = `
    <h2 class="text-lg font-semibold text-white mb-4">Learned Rules</h2>
    <div id="rules-status" class="text-sm text-gray-400 mb-3"></div>
    <div class="overflow-x-auto">
      <table class="w-full text-left">
        <thead>
          <tr class="border-b border-gray-600 text-xs text-gray-400 uppercase">
            <th class="px-3 py-2">Type</th>
            <th class="px-3 py-2">Rule</th>
            <th class="px-3 py-2 text-center">Conf.</th>
            <th class="px-3 py-2 text-center">Hits</th>
            <th class="px-3 py-2 text-center">Status</th>
            <th class="px-3 py-2">Created</th>
            <th class="px-3 py-2 text-center">Actions</th>
          </tr>
        </thead>
        <tbody id="rules-tbody"></tbody>
      </table>
    </div>
  `;

  const tbody = container.querySelector('#rules-tbody')!;
  const statusEl = container.querySelector('#rules-status')!;

  async function loadRules() {
    statusEl.textContent = 'Loading...';
    tbody.innerHTML = '';
    try {
      const rules = await getLearnedRules();
      statusEl.textContent = `${rules.length} rules (${rules.filter((r) => r.is_active).length} active)`;
      for (const rule of rules) {
        tbody.appendChild(createRuleRow(rule, loadRules));
      }
      if (rules.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="px-3 py-4 text-center text-gray-500">No learned rules yet</td></tr>';
      }
    } catch (e) {
      statusEl.textContent = `Error: ${e instanceof Error ? e.message : e}`;
    }
  }

  loadRules();
}
