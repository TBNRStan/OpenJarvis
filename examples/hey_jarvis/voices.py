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
XTTS_SAMPLE_RATE = 24000
SAMPLE_ENV = "HEY_JARVIS_VOICE_SAMPLE"

_pipeline = None
_xtts_model = None
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


def xtts_available() -> bool:
    """XTTS-v2 speaks in a voice cloned from a local sample (non-commercial licence)."""
    try:
        import TTS  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def sample_path() -> Optional[str]:
    """Path to the recording used by the custom voice, if one is configured."""
    configured = os.environ.get(SAMPLE_ENV, "").strip()
    return configured or None


def available() -> list[str]:
    """Backends usable on this machine, best first."""
    names: list[str] = []
    if xtts_available() and sample_path():
        names.append("xtts")
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
    if "xtts" in names:
        detail += f" (custom sample: {sample_path()})"
    return detail


# ------------------------------------------------------------------ speaking --


def speak(
    text: str,
    backend: str = "auto",
    voice: Optional[str] = None,
    sample: Optional[str] = None,
) -> str:
    """Speak ``text``; return the backend that did it.

    ``backend`` is ``auto``, ``kokoro`` or ``sapi``. With ``auto`` the first
    working backend wins, so a missing dependency degrades instead of failing.
    """
    if not text.strip():
        return ""

    configured_sample = sample or sample_path()
    order: list[str] = []
    if backend == "xtts" or (backend == "auto" and configured_sample):
        order.append("xtts")
    if backend in ("auto", "kokoro"):
        order.append("kokoro")
    if backend in ("auto", "sapi"):
        order.append("sapi")

    errors: list[str] = []
    for name in order:
        try:
            if name == "xtts":
                _speak_xtts(text, configured_sample)
            elif name == "kokoro":
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


AUDIO_SUFFIXES = (".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus")
SAMPLE_GAP_SECONDS = 0.25
SAMPLE_LIMIT_SECONDS = 60.0


def _sample_inputs(sample: Optional[str]) -> list[str]:
    """Resolve a file, a comma-separated list, or a folder into audio paths."""
    value = sample or sample_path()
    if not value:
        raise VoiceError(
            f"no voice sample configured (set {SAMPLE_ENV} to a file or folder)"
        )

    parts = [piece.strip().strip('"') for piece in value.split(",") if piece.strip()]
    paths: list[str] = []
    for part in parts:
        if not os.path.exists(part):
            raise VoiceError(f"voice sample not found: {part}")
        if os.path.isdir(part):
            found = sorted(
                os.path.join(part, name)
                for name in os.listdir(part)
                if name.lower().endswith(AUDIO_SUFFIXES)
            )
            if not found:
                raise VoiceError(f"no audio files in folder: {part}")
            paths.extend(found)
        else:
            paths.append(part)
    return paths


def _load_audio(path: str, target_rate: int):
    """Decode one file to mono float32 at the target rate."""
    import numpy as np
    import soundfile as sf

    try:
        data, rate = sf.read(path, dtype="float32", always_2d=True)
    except Exception as exc:  # noqa: BLE001
        raise VoiceError(
            f"could not read {os.path.basename(path)} as audio ({exc}). "
            "Re-save it as a wav (Audacity: File > Export > Export as WAV)."
        ) from exc

    if data.size == 0:
        raise VoiceError(f"{os.path.basename(path)} is silent or empty")

    audio = data.mean(axis=1)
    if rate != target_rate:
        count = int(round(len(audio) * target_rate / rate))
        if count <= 1:
            raise VoiceError(f"{os.path.basename(path)} is too short to use")
        source = np.arange(len(audio))
        audio = np.interp(
            np.linspace(0, len(audio) - 1, count), source, audio
        ).astype(np.float32)
    return audio


def prepare_sample(
    sample: Optional[str] = None, target_rate: int = XTTS_SAMPLE_RATE
) -> str:
    """Turn one or more recordings into a single mono wav XTTS can use.

    Recordings arrive as mp3 data behind a .wav name, as stereo, at 48 kHz, at
    very low level, or as several takes. Decode them all, downmix, resample,
    join them into one reference and write a clean 16-bit mono file next to the
    first input. Silent or unreadable files are rejected with a usable message.
    """
    try:
        import numpy as np
        import soundfile as sf
    except ImportError as exc:  # pragma: no cover - depends on the machine
        raise VoiceError("soundfile is not installed (run: pip install soundfile)") from exc

    paths = _sample_inputs(sample)
    if len(paths) == 1 and paths[0].lower().endswith(".24k.wav"):
        return paths[0]

    gap = np.zeros(int(SAMPLE_GAP_SECONDS * target_rate), dtype=np.float32)
    clips = []
    for path in paths:
        clip = _load_audio(path, target_rate)
        peak = float(np.max(np.abs(clip))) if len(clip) else 0.0
        if peak <= 1e-6:
            raise VoiceError(
                f"{os.path.basename(path)} is silent — record again with the "
                "microphone unmuted, and check it plays back before retrying"
            )
        clips.append(clip)

    audio = np.concatenate(
        [piece for clip in clips for piece in (clip, gap)]
    )[: int(SAMPLE_LIMIT_SECONDS * target_rate)]

    peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
    if peak <= 1e-6:
        raise VoiceError("the voice sample is silent or empty")
    if peak < 0.05:
        audio = audio * (0.9 / peak)
    elif peak > 1.0:
        audio = audio / peak

    if len(paths) == 1:
        prepared = os.path.splitext(paths[0])[0] + ".24k.wav"
    else:
        prepared = os.path.join(os.path.dirname(paths[0]) or ".", "voice.24k.wav")
    sf.write(prepared, audio, target_rate, subtype="PCM_16")
    return prepared


def _get_xtts():
    """Load XTTS-v2 once; the model is large and slow to initialise."""
    global _xtts_model
    with _pipeline_lock:
        if _xtts_model is None:
            try:
                from TTS.api import TTS
            except ImportError as exc:
                raise VoiceError(
                    "XTTS is not installed (run: pip install coqui-tts soundfile)"
                ) from exc
            _xtts_model = TTS(
                model_name="tts_models/multilingual/multi-dataset/xtts_v2"
            ).to("cpu")
        return _xtts_model


def _speak_xtts(text: str, sample: Optional[str] = None) -> None:
    """Speak in the voice of a local recording you have the rights to use."""
    prepared = prepare_sample(sample)

    import numpy as np
    import sounddevice as sd

    model = _get_xtts()
    wav = model.tts(text=text, speaker_wav=prepared, language="en")
    if not wav:
        raise VoiceError("XTTS produced no audio")

    sd.play(np.asarray(wav, dtype=np.float32), XTTS_SAMPLE_RATE)
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
