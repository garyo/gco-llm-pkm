import { getSystemPrompt, saveSystemPrompt } from './admin-api';

type PromptType = 'web' | 'mcp';

export function initPromptsSection(container: HTMLElement): void {
  container.innerHTML = `
    <h2 class="text-lg font-semibold text-white mb-4">System Prompts</h2>
    <div class="flex gap-2 mb-4">
      <button class="prompt-tab px-3 py-1 rounded text-sm font-medium bg-blue-600 text-white" data-type="web">Web UI Prompt</button>
      <button class="prompt-tab px-3 py-1 rounded text-sm font-medium bg-gray-700 text-gray-300 hover:bg-gray-600" data-type="mcp">MCP Prompt</button>
    </div>
    <div id="prompt-editor" class="max-w-4xl">
      <textarea id="prompt-textarea" class="w-full h-96 bg-gray-800 text-gray-100 border border-gray-600 rounded-lg p-3 font-mono text-sm resize-y focus:outline-none focus:ring-2 focus:ring-blue-500" spellcheck="false"></textarea>
      <div class="flex items-center gap-3 mt-3">
        <button id="prompt-save-btn" class="px-4 py-2 bg-green-600 hover:bg-green-700 text-white rounded font-medium text-sm">Save</button>
        <span id="prompt-status" class="text-sm text-gray-400"></span>
        <span class="flex-1"></span>
        <span id="prompt-mtime" class="text-xs text-gray-500"></span>
      </div>
    </div>
  `;

  const textarea = container.querySelector('#prompt-textarea') as HTMLTextAreaElement;
  const saveBtn = container.querySelector('#prompt-save-btn') as HTMLButtonElement;
  const statusEl = container.querySelector('#prompt-status')!;
  const mtimeEl = container.querySelector('#prompt-mtime')!;

  let currentType: PromptType = 'web';
  let currentMtime: number | undefined;
  let originalContent = '';

  async function loadPrompt(type: PromptType) {
    currentType = type;
    statusEl.textContent = 'Loading...';
    try {
      const data = await getSystemPrompt(type);
      textarea.value = data.content;
      originalContent = data.content;
      currentMtime = data.modified;
      mtimeEl.textContent = `Modified: ${new Date(data.modified * 1000).toLocaleString()}`;
      statusEl.textContent = '';
    } catch (e) {
      statusEl.textContent = `Error: ${e instanceof Error ? e.message : e}`;
    }
  }

  // Tab switching
  container.querySelectorAll('.prompt-tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      const type = btn.getAttribute('data-type') as PromptType;
      container.querySelectorAll('.prompt-tab').forEach((b) => {
        const isActive = b.getAttribute('data-type') === type;
        b.className = `prompt-tab px-3 py-1 rounded text-sm font-medium ${
          isActive ? 'bg-blue-600 text-white' : 'bg-gray-700 text-gray-300 hover:bg-gray-600'
        }`;
      });
      loadPrompt(type);
    });
  });

  // Save
  saveBtn.addEventListener('click', async () => {
    const content = textarea.value;
    if (content === originalContent) {
      statusEl.textContent = 'No changes to save';
      return;
    }
    saveBtn.disabled = true;
    statusEl.textContent = 'Saving...';
    try {
      const result = await saveSystemPrompt(currentType, content, currentMtime);
      currentMtime = result.modified;
      originalContent = content;
      mtimeEl.textContent = `Modified: ${new Date(result.modified * 1000).toLocaleString()}`;
      statusEl.textContent = 'Saved!';
    } catch (e) {
      statusEl.textContent = `Error: ${e instanceof Error ? e.message : e}`;
    } finally {
      saveBtn.disabled = false;
    }
  });

  // Ctrl+S shortcut within textarea
  textarea.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 's') {
      e.preventDefault();
      saveBtn.click();
    }
  });

  loadPrompt('web');
}
