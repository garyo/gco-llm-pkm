import type { SSEState } from './types';
import { MAX_RECONNECT_DELAY, SSE_GLOBAL_KEY } from './types';

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const win: Record<string, any> = window as any;

export function createSSEState(): SSEState {
  return {
    eventSource: null,
    lastEventTime: 0,
    reconnectAttempts: 0,
    lastResumeRefresh: 0,
  };
}

function updateConnectionStatus(status: 'connecting' | 'connected' | 'disconnected'): void {
  const indicator = document.getElementById('sse-status');
  if (!indicator) return;

  indicator.classList.remove('bg-gray-400', 'bg-green-500', 'bg-red-500');
  switch (status) {
    case 'connecting':
      indicator.classList.add('bg-gray-400');
      indicator.title = 'Connecting to real-time updates...';
      break;
    case 'connected':
      indicator.classList.add('bg-green-500');
      indicator.title = 'Real-time updates active';
      break;
    case 'disconnected':
      indicator.classList.add('bg-red-500');
      indicator.title = 'Connection lost - reconnecting...';
      break;
  }
}

export function connectSSE(
  sse: SSEState,
  onFileChanged: (data: { path: string; mtime: number }) => void,
  onOpenFile: (data: { path: string }) => void,
): void {
  const globalES = win[SSE_GLOBAL_KEY] as EventSource | null;
  if (globalES && globalES.readyState !== EventSource.CLOSED) {
    sse.eventSource = globalES;
    return;
  }

  if (sse.eventSource && sse.eventSource.readyState !== EventSource.CLOSED) return;

  try {
    updateConnectionStatus('connecting');
    const es = new EventSource('/api/events');
    sse.eventSource = es;
    win[SSE_GLOBAL_KEY] = es;

    es.onopen = () => {
      sse.reconnectAttempts = 0;
      sse.lastEventTime = Date.now();
      updateConnectionStatus('connected');
    };

    es.onmessage = (event) => {
      sse.lastEventTime = Date.now();
      try {
        const message = JSON.parse(event.data);
        const { type, data } = message;
        if (type === 'file_changed') onFileChanged(data);
        else if (type === 'open_file') onOpenFile(data);
      } catch (e) {
        console.error('Failed to parse SSE event:', e);
      }
    };

    es.onerror = () => {
      updateConnectionStatus('disconnected');
      if (es.readyState === EventSource.CLOSED) {
        win[SSE_GLOBAL_KEY] = null;
        const delay = Math.min(1000 * Math.pow(2, sse.reconnectAttempts), MAX_RECONNECT_DELAY);
        sse.reconnectAttempts++;
        sse.eventSource = null;
        setTimeout(() => connectSSE(sse, onFileChanged, onOpenFile), delay);
      }
    };
  } catch (e) {
    console.error('Failed to connect SSE:', e);
    updateConnectionStatus('disconnected');
    win[SSE_GLOBAL_KEY] = null;
  }
}

export function setupSSEReconnection(
  sse: SSEState,
  onFileChanged: (data: { path: string; mtime: number }) => void,
  onOpenFile: (data: { path: string }) => void,
  onResume: () => void,
): void {
  function checkAndReconnect(): boolean {
    const staleDuration = sse.lastEventTime > 0 ? Date.now() - sse.lastEventTime : -1;
    let needsRefresh = false;

    if (!sse.eventSource || sse.eventSource.readyState === EventSource.CLOSED) {
      win[SSE_GLOBAL_KEY] = null;
      sse.reconnectAttempts = 0;
      connectSSE(sse, onFileChanged, onOpenFile);
      needsRefresh = true;
    } else if (sse.eventSource.readyState === EventSource.CONNECTING) {
      sse.eventSource.close();
      sse.eventSource = null;
      win[SSE_GLOBAL_KEY] = null;
      sse.reconnectAttempts = 0;
      connectSSE(sse, onFileChanged, onOpenFile);
      needsRefresh = true;
    } else if (sse.eventSource.readyState === EventSource.OPEN && staleDuration > 45000) {
      sse.eventSource.close();
      sse.eventSource = null;
      win[SSE_GLOBAL_KEY] = null;
      sse.reconnectAttempts = 0;
      connectSSE(sse, onFileChanged, onOpenFile);
      needsRefresh = true;
    }

    return needsRefresh;
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      const needsRefresh = checkAndReconnect();
      if (needsRefresh && Date.now() - sse.lastResumeRefresh > 2000) {
        sse.lastResumeRefresh = Date.now();
        onResume();
      }
    }
  });

  window.addEventListener('focus', () => {
    const needsRefresh = checkAndReconnect();
    if (needsRefresh && Date.now() - sse.lastResumeRefresh > 2000) {
      sse.lastResumeRefresh = Date.now();
      onResume();
    }
  });

  // Stale connection monitor
  setInterval(() => {
    if (sse.eventSource?.readyState === EventSource.OPEN && sse.lastEventTime > 0) {
      const staleDuration = Date.now() - sse.lastEventTime;
      if (staleDuration > 60000) {
        updateConnectionStatus('disconnected');
        sse.eventSource.close();
        sse.eventSource = null;
        win[SSE_GLOBAL_KEY] = null;
        sse.reconnectAttempts = 0;
        connectSSE(sse, onFileChanged, onOpenFile);
      }
    }
  }, 30000);
}
