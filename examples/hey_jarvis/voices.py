"""Voice backends for Jarvis -- both offline, no cloud service involved.

* **kokoro** -- neural text-to-speech. Its British male voices
  (``bm_george``, ``bm_lewis``) are the closest free approximation of the
  film's Jarvis: calm, low, British. Needs a one-time
  ``pip install kokoro soundfile`` (a few hundred MB, mostly torch).
* **sapi** -- Windows' own speech engine. Instant, always present, but
  noticeably robotic, and most PCs only ship American voices.

``speak(text, "auto")`` prefers kokoro and falls back to sapi, so a missing or
broken neural stack never leaves the assistant mute.

Changing the voice:
    HEY_JARVIS_KOKORO_VOICE=bm_lewis      (alternative British male)
    HEY_JARVIS_KOKORO_VOICE=am_michael    (American male)
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Optional

DEFAULT_KOKORO_VOICE = os.environ.get("HEY_JARVIS_KOKORO_VOICE", "bm_george")

# Windows SAPI voices worth preferring: British and calm first, then any male.
BRITISH_SAPI_KEYWORDS = ("george", "ryan", "thomas", "arthur", "james")
FALLBACK_SAPI_KEYWORDS = ("david", "mark", "guy", "male")

KOKORO_SAMPLE_RATE = 24000

_pipeline = None
_pipeline_lock = threading.Lock()


class VoiceError(RuntimeError):
    """Raised when no backend can speak the text."""


# ------------------------------------------------------------------ probing --


def sapi_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import win32com.client  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def kokoro_available() -> bool:
    try:
        import kokoro  # noqa: F401
        import soundfile  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def available() -> list[str]:
    """Backends usable on this machine, best first."""
    names: list[str] = []
    if kokoro_available():
        names.append("kokoro")
    if sapi_available():
        names.append("sapi")
    return names


def describe() -> str:
    names = available()
    if not names:
        return "none (install kokoro, or run on Windows for SAPI)"
    detail = ", ".join(names)
    if "kokoro" in names:
        detail += f" (voice {DEFAULT_KOKORO_VOICE})"
    return detail


# ------------------------------------------------------------------ speaking --


def speak(text: str, backend: str = "auto", voice: Optional[str] = None) -> str:
    """Speak ``text``; return the backend that did it.

    ``backend`` is ``auto``, ``kokoro`` or ``sapi``. With ``auto`` the first
    working backend wins, so a missing dependency degrades instead of failing.
    """
    if not text.strip():
        return ""

    order: list[str] = []
    if backend in ("auto", "kokoro"):
        order.append("kokoro")
    if backend in ("auto", "sapi"):
        order.append("sapi")

    errors: list[str] = []
    for name in order:
        try:
            if name == "kokoro":
                _speak_kokoro(text, voice or DEFAULT_KOKORO_VOICE)
            else:
                _speak_sapi(text)
            return name
        except Exception as exc:  # noqa: BLE001 - try the next backend
            errors.append(f"{name}: {exc}")

    raise VoiceError("; ".join(errors) or "no voice backend available")


def _get_pipeline():
    """Load Kokoro once: model loading is slow and must not repeat per reply."""
    global _pipeline
    with _pipeline_lock:
        if _pipeline is None:
            try:
                from kokoro import KPipeline
            except ImportError as exc:
                raise VoiceError(
                    "kokoro is not installed (run: pip install kokoro soundfile)"
                ) from exc
            # "b" selects British English, which is what makes the voice sound right.
            _pipeline = KPipeline(lang_code="b")
        return _pipeline


def _speak_kokoro(text: str, voice: str) -> None:
    import numpy as np
    import sounddevice as sd

    pipeline = _get_pipeline()
    chunks: list[np.ndarray] = []
    for _graphemes, _phonemes, audio in pipeline(text, voice=voice, speed=1.0):
        if audio is not None:
            chunks.append(np.asarray(audio, dtype=np.float32))
    if not chunks:
        raise VoiceError("kokoro produced no audio")

    sd.play(np.concatenate(chunks), KOKORO_SAMPLE_RATE)
    sd.wait()


def _speak_sapi(text: str) -> None:
    """Speak through Windows' built-in engine (blocking)."""
    import win32com.client

    voice = win32com.client.Dispatch("SAPI.SpVoice")
    try:
        tokens = voice.GetVoices()
        chosen = None
        for keywords in (BRITISH_SAPI_KEYWORDS, FALLBACK_SAPI_KEYWORDS):
            for index in range(tokens.Count):
                token = tokens.Item(index)
                description = str(token.GetDescription()).lower()
                if any(keyword in description for keyword in keywords):
                    chosen = token
                    break
            if chosen is not None:
                break
        if chosen is not None:
            voice.Voice = chosen
    except Exception:  # noqa: BLE001 - keep the system default voice
        pass

    voice.Rate = 0  # -10 (slow) .. 10 (fast)
    voice.Speak(text)
