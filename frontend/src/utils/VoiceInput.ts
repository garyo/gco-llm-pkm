/**
 * VoiceInput - VAD + server-side Whisper transcription
 *
 * Uses @ricky0123/vad-web for client-side voice activity detection
 * and POSTs audio segments to /transcribe for Whisper STT.
 * The mic stays open via getUserMedia for the entire session --
 * no start/stop cycles, no beeps, no duplicate text.
 */

import { MicVAD } from '@ricky0123/vad-web';

export type VoiceStatus = 'listening' | 'transcribing';

export interface VoiceInputConfig {
  language?: string;
  onTranscript?: (text: string) => void;
  onStart?: () => void;
  onEnd?: () => void;
  onError?: (error: string) => void;
  onStatusChange?: (status: VoiceStatus) => void;
  getAuthHeaders?: () => HeadersInit;
}

/**
 * Encode a Float32Array of 16 kHz mono PCM samples as a WAV file blob.
 */
function encodeWAV(samples: Float32Array): Blob {
  const sampleRate = 16000;
  const numChannels = 1;
  const bitsPerSample = 16;
  const byteRate = sampleRate * numChannels * (bitsPerSample / 8);
  const blockAlign = numChannels * (bitsPerSample / 8);
  const dataLength = samples.length * (bitsPerSample / 8);
  const buffer = new ArrayBuffer(44 + dataLength);
  const view = new DataView(buffer);

  // RIFF header
  writeString(view, 0, 'RIFF');
  view.setUint32(4, 36 + dataLength, true);
  writeString(view, 8, 'WAVE');

  // fmt sub-chunk
  writeString(view, 12, 'fmt ');
  view.setUint32(16, 16, true);        // sub-chunk size
  view.setUint16(20, 1, true);         // PCM format
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitsPerSample, true);

  // data sub-chunk
  writeString(view, 36, 'data');
  view.setUint32(40, dataLength, true);

  // Convert float samples to 16-bit PCM
  let offset = 44;
  for (let i = 0; i < samples.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
  }

  return new Blob([buffer], { type: 'audio/wav' });
}

function writeString(view: DataView, offset: number, str: string) {
  for (let i = 0; i < str.length; i++) {
    view.setUint8(offset + i, str.charCodeAt(i));
  }
}

export class VoiceInput {
  private vad: MicVAD | null = null;
  private active = false;
  private config: Required<VoiceInputConfig>;

  constructor(config: VoiceInputConfig = {}) {
    this.config = {
      language: config.language || 'en',
      onTranscript: config.onTranscript || (() => {}),
      onStart: config.onStart || (() => {}),
      onEnd: config.onEnd || (() => {}),
      onError: config.onError || (() => {}),
      onStatusChange: config.onStatusChange || (() => {}),
      getAuthHeaders: config.getAuthHeaders || (() => ({})),
    };
  }

  public isSupported(): boolean {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  }

  public async start(): Promise<void> {
    if (this.active) return;

    try {
      this.vad = await MicVAD.new({
        // Serve all VAD/ONNX assets from /vad/ (copied by vite-plugin-static-copy)
        baseAssetPath: '/vad/',
        onnxWASMBasePath: '/vad/',

        positiveSpeechThreshold: 0.3,
        negativeSpeechThreshold: 0.25,
        minSpeechMs: 300,
        preSpeechPadMs: 300,
        redemptionMs: 800,

        onSpeechStart: () => {
          // Speech detected -- no visible change needed
        },

        onSpeechEnd: async (audio: Float32Array) => {
          // Skip very short segments (noise/clicks) -- 0.3s at 16kHz
          if (audio.length < 4800) return;

          this.config.onStatusChange('transcribing');

          try {
            const wav = encodeWAV(audio);
            const form = new FormData();
            form.append('audio', wav, 'audio.wav');
            form.append('language', this.config.language);

            // Extract only the Authorization header (FormData sets its own Content-Type)
            const allHeaders = this.config.getAuthHeaders();
            const headers: HeadersInit = {};
            if (allHeaders && typeof allHeaders === 'object') {
              const auth = (allHeaders as Record<string, string>)['Authorization'];
              if (auth) {
                (headers as Record<string, string>)['Authorization'] = auth;
              }
            }

            const resp = await fetch('/transcribe', {
              method: 'POST',
              headers,
              body: form,
            });

            if (!resp.ok) {
              const err = await resp.json().catch(() => ({ error: resp.statusText }));
              throw new Error(err.error || `HTTP ${resp.status}`);
            }

            const data = await resp.json();
            const text = (data.text || '').trim();
            if (text) {
              this.config.onTranscript(text);
            }
          } catch (e: any) {
            console.error('Transcription failed:', e);
            this.config.onError(`Transcription error: ${e.message}`);
          } finally {
            if (this.active) {
              this.config.onStatusChange('listening');
            }
          }
        },

        onVADMisfire: () => {
          // Speech was too short -- ignore
        },
      });

      this.vad.start();
      this.active = true;
      this.config.onStart();
      this.config.onStatusChange('listening');
    } catch (e: any) {
      this.active = false;
      let msg = 'Failed to start voice input.';
      if (e.name === 'NotAllowedError') {
        msg = 'Microphone permission denied. Enable in browser settings.';
      } else if (e.name === 'NotFoundError') {
        msg = 'No microphone found. Check your device settings.';
      }
      this.config.onError(msg);
    }
  }

  public async stop(): Promise<void> {
    if (!this.active) return;
    this.active = false;

    if (this.vad) {
      this.vad.destroy();
      this.vad = null;
    }

    this.config.onEnd();
  }

  public async toggle(): Promise<void> {
    if (this.active) {
      await this.stop();
    } else {
      await this.start();
    }
  }

  public isActive(): boolean {
    return this.active;
  }

  public destroy(): void {
    this.stop();
  }
}
