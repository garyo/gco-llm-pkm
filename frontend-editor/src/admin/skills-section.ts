import { getSkills, updateSkill, deleteSkill } from './admin-api';
import type { Skill } from '../types';

export function initSkillsSection(container: HTMLElement): void {
  container.innerHTML = `
    <h2 class="text-lg font-semibold text-white mb-4">Saved Skills</h2>
    <div id="skills-status" class="text-sm text-gray-400 mb-3"></div>
    <div id="skills-list" class="space-y-3 max-w-4xl"></div>
  `;

  const statusEl = container.querySelector('#skills-status')!;
  const listEl = container.querySelector('#skills-list')!;

  async function loadSkills() {
    statusEl.textContent = 'Loading...';
    listEl.innerHTML = '';
    try {
      const skills = await getSkills();
      statusEl.textContent = `${skills.length} skill(s)`;
      if (skills.length === 0) {
        listEl.innerHTML = '<p class="text-gray-500">No saved skills found.</p>';
        return;
      }
      for (const skill of skills) {
        listEl.appendChild(renderSkillCard(skill, loadSkills));
      }
    } catch (e) {
      statusEl.textContent = `Error: ${e instanceof Error ? e.message : e}`;
    }
  }

  loadSkills();
}

function renderSkillCard(skill: Skill, onRefresh: () => void): HTMLElement {
  const card = document.createElement('div');
  card.className = 'bg-gray-800 rounded-lg border border-gray-700 overflow-hidden';

  const tags = (skill.tags || []).map(t => `<span class="px-1.5 py-0.5 bg-gray-700 text-gray-300 rounded text-xs">${esc(t)}</span>`).join(' ');
  const typeBadge = skill.type === 'shell'
    ? '<span class="px-1.5 py-0.5 bg-blue-900 text-blue-300 rounded text-xs font-medium">shell</span>'
    : '<span class="px-1.5 py-0.5 bg-purple-900 text-purple-300 rounded text-xs font-medium">recipe</span>';

  card.innerHTML = `
    <div class="p-4">
      <div class="flex items-start justify-between gap-2 mb-2">
        <div class="flex items-center gap-2 flex-wrap">
          <h3 class="text-white font-medium">${esc(skill.name)}</h3>
          ${typeBadge}
          ${tags}
        </div>
        <div class="flex gap-1 shrink-0">
          <button class="edit-btn px-2 py-1 bg-gray-700 hover:bg-gray-600 text-gray-300 rounded text-xs">Edit</button>
          <button class="delete-btn px-2 py-1 bg-red-900/50 hover:bg-red-900 text-red-400 rounded text-xs">Delete</button>
        </div>
      </div>
      <p class="text-gray-400 text-sm mb-1">${esc(skill.description)}</p>
      ${skill.trigger ? `<p class="text-gray-500 text-xs mb-1">Trigger: ${esc(skill.trigger)}</p>` : ''}
      <div class="text-xs text-gray-500 flex gap-3">
        <span>Uses: ${skill.use_count}</span>
        ${skill.last_used ? `<span>Last used: ${skill.last_used}</span>` : ''}
        ${skill.created ? `<span>Created: ${skill.created}</span>` : ''}
      </div>
    </div>
    <details class="body-details">
      <summary class="px-4 py-2 text-xs text-gray-400 cursor-pointer hover:text-gray-300 bg-gray-800/50 border-t border-gray-700">
        Show content
      </summary>
      <div class="body-view px-4 py-3 border-t border-gray-700 bg-gray-900/50">
        <pre class="text-sm text-gray-300 font-mono whitespace-pre-wrap break-words max-h-96 overflow-auto">${esc(skill.body)}</pre>
      </div>
    </details>
    <div class="edit-form hidden border-t border-gray-700 p-4 bg-gray-900/50">
      <div class="space-y-3">
        <div>
          <label class="block text-xs text-gray-400 mb-1">Description</label>
          <input class="edit-description w-full bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white" value="${attr(skill.description)}" />
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">Trigger</label>
          <input class="edit-trigger w-full bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white" value="${attr(skill.trigger)}" />
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">Tags (comma-separated)</label>
          <input class="edit-tags w-full bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white" value="${attr((skill.tags || []).join(', '))}" />
        </div>
        <div>
          <label class="block text-xs text-gray-400 mb-1">Content</label>
          <textarea class="edit-body w-full bg-gray-700 border border-gray-600 rounded px-3 py-1.5 text-sm text-white font-mono h-48 resize-y">${esc(skill.body)}</textarea>
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
  const deleteBtn = card.querySelector('.delete-btn') as HTMLButtonElement;
  const bodyDetails = card.querySelector('.body-details') as HTMLDetailsElement;
  const editForm = card.querySelector('.edit-form') as HTMLElement;
  const saveBtn = card.querySelector('.save-btn') as HTMLButtonElement;
  const cancelBtn = card.querySelector('.cancel-btn') as HTMLButtonElement;
  const editStatus = card.querySelector('.edit-status')!;

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
    const description = (card.querySelector('.edit-description') as HTMLInputElement).value.trim();
    const trigger = (card.querySelector('.edit-trigger') as HTMLInputElement).value.trim();
    const tagsStr = (card.querySelector('.edit-tags') as HTMLInputElement).value.trim();
    const body = (card.querySelector('.edit-body') as HTMLTextAreaElement).value;
    const tags = tagsStr ? tagsStr.split(',').map(t => t.trim()).filter(Boolean) : [];

    saveBtn.disabled = true;
    editStatus.textContent = 'Saving...';
    try {
      await updateSkill(skill.name, { description, trigger, tags, body });
      editStatus.textContent = 'Saved!';
      setTimeout(() => onRefresh(), 500);
    } catch (e) {
      editStatus.textContent = `Error: ${e instanceof Error ? e.message : e}`;
    } finally {
      saveBtn.disabled = false;
    }
  });

  deleteBtn.addEventListener('click', async () => {
    if (!confirm(`Delete skill "${skill.name}"?`)) return;
    try {
      await deleteSkill(skill.name);
      onRefresh();
    } catch (e) {
      alert(`Error: ${e instanceof Error ? e.message : e}`);
    }
  });

  return card;
}

function esc(s: string): string {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function attr(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
