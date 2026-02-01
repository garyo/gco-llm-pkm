"""
Speech-to-text client wrapping Groq/OpenAI Whisper API.

Uses the HTTP API directly with requests (no extra dependencies).
"""

import os
import logging
from typing import BinaryIO

import requests

logger = logging.getLogger(__name__)


class STTClient:
    """Client for server-side speech-to-text via Groq or OpenAI Whisper API."""

    def __init__(self) -> None:
        self.provider = os.getenv("STT_PROVIDER", "groq").lower()

        if self.provider == "groq":
            self.api_key = os.getenv("GROQ_API_KEY", "")
            self.api_url = "https://api.groq.com/openai/v1/audio/transcriptions"
            self.model = "whisper-large-v3-turbo"
        elif self.provider == "openai":
            self.api_key = os.getenv("OPENAI_API_KEY", "")
            self.api_url = "https://api.openai.com/v1/audio/transcriptions"
            self.model = "whisper-1"
        else:
            raise ValueError(f"Unknown STT_PROVIDER: {self.provider!r} (expected 'groq' or 'openai')")

        if not self.api_key:
            key_name = "GROQ_API_KEY" if self.provider == "groq" else "OPENAI_API_KEY"
            raise ValueError(f"{key_name} not set (required for STT_PROVIDER={self.provider})")

        logger.info(f"STT client initialized: provider={self.provider}, model={self.model}")

    def transcribe(self, audio_file: BinaryIO, language: str = "en") -> str:
        """Transcribe audio to text.

        Args:
            audio_file: File-like object containing WAV audio data.
            language: ISO 639-1 language code (default: "en").

        Returns:
            Transcribed text string.

        Raises:
            requests.HTTPError: If the API returns an error.
        """
        resp = requests.post(
            self.api_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            files={"file": ("audio.wav", audio_file, "audio/wav")},
            data={
                "model": self.model,
                "language": language,
                "response_format": "json",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("text", "").strip()
