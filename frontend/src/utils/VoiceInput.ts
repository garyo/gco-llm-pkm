/**
 * VoiceInput - Web Speech API wrapper for voice transcription
 * Optimized for mobile use with single-shot recording
 */

// Type definitions for Web Speech API
interface SpeechRecognitionEvent extends Event {
  results: SpeechRecognitionResultList;
  resultIndex: number;
}

interface SpeechRecognitionErrorEvent extends Event {
  error: string;
  message: string;
}

interface SpeechRecognition extends EventTarget {
  continuous: boolean;
  interimResults: boolean;
  lang: string;
  maxAlternatives: number;
  start(): void;
  stop(): void;
  abort(): void;
  onstart: ((this: SpeechRecognition, ev: Event) => any) | null;
  onend: ((this: SpeechRecognition, ev: Event) => any) | null;
  onerror: ((this: SpeechRecognition, ev: SpeechRecognitionErrorEvent) => any) | null;
  onresult: ((this: SpeechRecognition, ev: SpeechRecognitionEvent) => any) | null;
}

declare var SpeechRecognition: {
  prototype: SpeechRecognition;
  new(): SpeechRecognition;
};

declare var webkitSpeechRecognition: {
  prototype: SpeechRecognition;
  new(): SpeechRecognition;
};

export interface VoiceInputConfig {
  language?: string;
  continuous?: boolean;
  interimResults?: boolean;
  onTranscript?: (text: string, isFinal: boolean) => void;
  onStart?: () => void;
  onEnd?: () => void;
  onError?: (error: string) => void;
}

export class VoiceInput {
  private recognition: SpeechRecognition | null = null;
  private isRecording = false;
  private intentionalStop = false;
  private autoRestarting = false;
  private config: Required<VoiceInputConfig>;

  constructor(config: VoiceInputConfig = {}) {
    this.config = {
      language: config.language || 'en-US',
      continuous: config.continuous ?? false,
      interimResults: config.interimResults ?? true,
      onTranscript: config.onTranscript || (() => {}),
      onStart: config.onStart || (() => {}),
      onEnd: config.onEnd || (() => {}),
      onError: config.onError || (() => {}),
    };

    this.initialize();
  }

  private initialize(): void {
    if (!this.isSupported()) {
      return;
    }

    const SpeechRecognitionAPI = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    this.recognition = new SpeechRecognitionAPI();

    if (this.recognition) {
      this.recognition.continuous = this.config.continuous;
      this.recognition.interimResults = this.config.interimResults;
      this.recognition.lang = this.config.language;
      this.recognition.maxAlternatives = 1;

      this.recognition.onstart = () => {
        this.isRecording = true;
        if (this.autoRestarting) {
          this.autoRestarting = false;
          return; // Skip callback on auto-restart (avoids beep/UI reset)
        }
        this.config.onStart();
      };

      this.recognition.onend = () => {
        // If continuous mode and the user didn't explicitly stop,
        // auto-restart to keep listening through pauses in speech.
        if (this.config.continuous && !this.intentionalStop && this.isRecording) {
          try {
            this.autoRestarting = true;
            this.recognition?.start();
            return; // Don't fire onEnd — we're still listening
          } catch {
            // Fall through to normal end if restart fails
          }
        }
        this.isRecording = false;
        this.intentionalStop = false;
        this.config.onEnd();
      };

      this.recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
        // In continuous mode, no-speech is expected during pauses — ignore it
        if (event.error === 'no-speech' && this.config.continuous && this.isRecording) {
          return;
        }

        let errorMessage = 'Unknown error occurred';

        switch (event.error) {
          case 'no-speech':
            errorMessage = 'No speech detected. Please try again.';
            break;
          case 'audio-capture':
            errorMessage = 'No microphone found. Check your device settings.';
            break;
          case 'not-allowed':
            errorMessage = 'Microphone permission denied. Enable in browser settings.';
            break;
          case 'network':
            errorMessage = 'Network error. Check your connection.';
            break;
          case 'aborted':
            errorMessage = 'Recording was stopped.';
            break;
          default:
            errorMessage = `Speech recognition error: ${event.error}`;
        }

        this.config.onError(errorMessage);
      };

      this.recognition.onresult = (event: SpeechRecognitionEvent) => {
        let interimTranscript = '';
        let finalTranscript = '';

        for (let i = event.resultIndex; i < event.results.length; i++) {
          const transcript = event.results[i][0].transcript;
          if (event.results[i].isFinal) {
            finalTranscript += transcript + ' ';
          } else {
            interimTranscript += transcript;
          }
        }

        // Send final results first (higher priority)
        if (finalTranscript) {
          this.config.onTranscript(finalTranscript.trim(), true);
        }

        // Send interim results separately (only if we don't have final)
        if (interimTranscript && !finalTranscript) {
          this.config.onTranscript(interimTranscript.trim(), false);
        }
      };
    }
  }

  public isSupported(): boolean {
    return 'SpeechRecognition' in window || 'webkitSpeechRecognition' in window;
  }

  public start(): void {
    if (!this.recognition || this.isRecording) {
      return;
    }

    try {
      this.recognition.start();
    } catch (error) {
      this.config.onError('Failed to start recording. Please try again.');
    }
  }

  public stop(): void {
    if (!this.recognition || !this.isRecording) {
      return;
    }

    this.intentionalStop = true;
    this.recognition.stop();
  }

  public toggle(): void {
    if (this.isRecording) {
      this.stop();
    } else {
      this.start();
    }
  }

  public isActive(): boolean {
    return this.isRecording;
  }

  public destroy(): void {
    if (this.recognition) {
      this.stop();
      this.recognition.onstart = null;
      this.recognition.onend = null;
      this.recognition.onerror = null;
      this.recognition.onresult = null;
      this.recognition = null;
    }
  }
}
