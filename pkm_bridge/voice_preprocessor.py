"""Voice transcription preprocessing.

Cleans up voice transcriptions by removing disfluencies, false starts,
and self-corrections to improve LLM comprehension.
"""

from anthropic import Anthropic
from typing import Optional
import logging

# Configuration
ENABLE_VOICE_PREPROCESSING = True  # Enable/disable voice preprocessing
PREPROCESSING_MODEL = "claude-haiku-4-5"  # Fast and cheap model
PREPROCESSING_MAX_TOKENS = 500  # Max tokens for cleaned output

logger = logging.getLogger(__name__)


class VoicePreprocessor:
    """Preprocesses voice transcriptions to clean up disfluencies."""

    def __init__(self, anthropic_client: Anthropic):
        """Initialize the preprocessor.

        Args:
            anthropic_client: Initialized Anthropic client
        """
        self.client = anthropic_client

    def preprocess(self, transcription: str) -> str:
        """Clean up a voice transcription.

        Removes disfluencies, false starts, and self-corrections while
        preserving the user's intent.

        Args:
            transcription: Raw voice transcription text

        Returns:
            Cleaned text, or original if preprocessing fails
        """
        if not ENABLE_VOICE_PREPROCESSING:
            return transcription

        # Don't preprocess very short messages
        if len(transcription) < 50:
            return transcription

        try:
            logger.debug(f"Preprocessing voice transcription: {transcription[:100]}...")

            system_prompt = """You clean up voice transcriptions by removing disfluencies and false starts while preserving the speaker's intent.

Output ONLY the cleaned text. No explanations or commentary."""

            user_prompt = f"""Clean up this voice transcription by:
- Removing false starts (e.g., "I want to... no wait, I mean...")
- Removing filler words (um, uh, like)
- Fixing self-corrections (keeping the corrected version)
- Preserving the speaker's final intent

Transcription:
{transcription}

Cleaned version:"""

            response = self.client.messages.create(
                model=PREPROCESSING_MODEL,
                max_tokens=PREPROCESSING_MAX_TOKENS,
                system=system_prompt,
                messages=[{
                    "role": "user",
                    "content": user_prompt
                }]
            )

            cleaned = response.content[0].text.strip()

            # Basic sanity check: cleaned version shouldn't be way longer
            if len(cleaned) > len(transcription) * 1.5:
                logger.warning("Preprocessed text is significantly longer, using original")
                return transcription

            # Log the cleaning with both versions for review
            reduction = 100 - (100 * len(cleaned) // len(transcription)) if len(transcription) > 0 else 0
            logger.info(f"🎤 Voice preprocessing ({reduction}% reduction):")
            logger.info(f"   Original: {transcription}")
            logger.info(f"   Cleaned:  {cleaned}")

            return cleaned

        except Exception as e:
            logger.error(f"Voice preprocessing failed: {e}")
            # Return original on error
            return transcription

    def should_preprocess(self, message: str, is_voice: bool) -> bool:
        """Determine if a message should be preprocessed.

        Args:
            message: The user's message
            is_voice: Whether this is a voice transcription

        Returns:
            True if preprocessing should be applied
        """
        if not ENABLE_VOICE_PREPROCESSING:
            return False

        if not is_voice:
            return False

        # Skip very short messages
        if len(message) < 50:
            return False

        return True
