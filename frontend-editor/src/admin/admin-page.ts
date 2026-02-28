import type { AdminTab } from '../types';
import { initOAuthSection } from './oauth-section';
import { initPromptsSection } from './prompts-section';
import { initRulesSection } from './rules-section';
import { initTasksSection } from './tasks-section';
import { initSelfImproveSection } from './self-improve-section';

const contentEl = () => document.getElementById('admin-content')!;

const tabInitializers: Record<AdminTab, (container: HTMLElement) => void> = {
  connections: initOAuthSection,
  prompts: initPromptsSection,
  rules: initRulesSection,
  tasks: initTasksSection,
  'self-improve': initSelfImproveSection,
};

function switchTab(tab: AdminTab): void {

  // Update tab button styles
  document.querySelectorAll('.admin-tab').forEach((btn) => {
    const isActive = btn.getAttribute('data-tab') === tab;
    btn.classList.toggle('text-white', isActive);
    btn.classList.toggle('border-blue-500', isActive);
    btn.classList.toggle('text-gray-400', !isActive);
    btn.classList.toggle('border-transparent', !isActive);
  });

  // Render tab content
  const container = contentEl();
  container.innerHTML = '';
  tabInitializers[tab](container);
}

export function initAdmin(): void {
  // Wire tab buttons
  document.querySelectorAll('.admin-tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      const tab = btn.getAttribute('data-tab') as AdminTab;
      if (tab) switchTab(tab);
    });
  });

  // Default tab
  switchTab('connections');
}
