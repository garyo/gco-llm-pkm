import { getOAuthStatus, disconnectOAuth } from './admin-api';

interface ServiceConfig {
  key: string;
  label: string;
  description: string;
}

const SERVICES: ServiceConfig[] = [
  { key: 'ticktick', label: 'TickTick', description: 'Task management' },
  { key: 'google-calendar', label: 'Google Calendar', description: 'Calendar events' },
  { key: 'google-gmail', label: 'Google Gmail', description: 'Email reading (read-only)' },
];

function createCard(service: ServiceConfig): HTMLElement {
  const card = document.createElement('div');
  card.className = 'bg-gray-800 rounded-lg p-4 border border-gray-700';
  card.innerHTML = `
    <div class="flex items-center justify-between mb-2">
      <div>
        <h3 class="text-white font-medium">${service.label}</h3>
        <p class="text-gray-400 text-sm">${service.description}</p>
      </div>
      <span class="status-badge px-2 py-0.5 rounded text-xs font-medium bg-gray-600 text-gray-300">Loading...</span>
    </div>
    <div class="flex items-center gap-2 mt-3">
      <span class="expires-text text-xs text-gray-500"></span>
      <span class="flex-1"></span>
      <button class="connect-btn hidden px-3 py-1 bg-blue-600 hover:bg-blue-700 text-white rounded text-sm font-medium">Connect</button>
      <button class="disconnect-btn hidden px-3 py-1 bg-red-600 hover:bg-red-700 text-white rounded text-sm font-medium">Disconnect</button>
    </div>
  `;

  const badge = card.querySelector('.status-badge')!;
  const expiresText = card.querySelector('.expires-text')!;
  const connectBtn = card.querySelector('.connect-btn') as HTMLButtonElement;
  const disconnectBtn = card.querySelector('.disconnect-btn') as HTMLButtonElement;

  const returnTo = `/editor/?page=admin`;

  connectBtn.addEventListener('click', () => {
    window.location.href = `/auth/${service.key}/authorize?return_to=${encodeURIComponent(returnTo)}`;
  });

  disconnectBtn.addEventListener('click', async () => {
    if (!confirm(`Disconnect ${service.label}?`)) return;
    try {
      await disconnectOAuth(service.key);
      refresh();
    } catch (e) {
      alert(`Error: ${e instanceof Error ? e.message : e}`);
    }
  });

  async function refresh() {
    try {
      const status = await getOAuthStatus(service.key);
      if (status.connected) {
        badge.textContent = status.expired ? 'Expired' : 'Connected';
        badge.className = `status-badge px-2 py-0.5 rounded text-xs font-medium ${
          status.expired ? 'bg-yellow-900 text-yellow-300' : 'bg-green-900 text-green-300'
        }`;
        if (status.expires_at) {
          expiresText.textContent = `Expires: ${new Date(status.expires_at).toLocaleString()}`;
        }
        connectBtn.classList.add('hidden');
        disconnectBtn.classList.remove('hidden');
      } else {
        badge.textContent = 'Disconnected';
        badge.className = 'status-badge px-2 py-0.5 rounded text-xs font-medium bg-gray-600 text-gray-300';
        expiresText.textContent = '';
        connectBtn.classList.remove('hidden');
        disconnectBtn.classList.add('hidden');
      }
    } catch {
      badge.textContent = 'Error';
      badge.className = 'status-badge px-2 py-0.5 rounded text-xs font-medium bg-red-900 text-red-300';
      connectBtn.classList.remove('hidden');
      disconnectBtn.classList.add('hidden');
    }
  }

  refresh();
  return card;
}

export function initOAuthSection(container: HTMLElement): void {
  container.innerHTML = '<h2 class="text-lg font-semibold text-white mb-4">Service Connections</h2>';
  const grid = document.createElement('div');
  grid.className = 'grid gap-4 max-w-2xl';
  for (const svc of SERVICES) {
    grid.appendChild(createCard(svc));
  }
  container.appendChild(grid);
}
