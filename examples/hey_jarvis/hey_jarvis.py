#!/usr/bin/env python3
"""hey_jarvis.py -- an always-listening, fully local desktop assistant.

Sleep -> hears "Hey Jarvis" -> records until you stop talking -> transcribes
locally -> asks a local model -> speaks the answer aloud.

Nothing is uploaded: wake word, transcription, the model and the voice all run
on this PC. Only a local Ollama server is contacted, over 127.0.0.1.

Run `python hey_jarvis.py --selftest` first: it checks the microphone, the wake
word model, the speech model and the local LLM server without listening to you.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

import voices
from devices import load_devices, stop_devices

# ---------------------------------------------------------------- constants --

SAMPLE_RATE = 16000
BLOCK = 1280  # 80 ms at 16 kHz -- the frame size openWakeWord expects
BLOCK_SECONDS = BLOCK / SAMPLE_RATE

WAKE_MODEL = "hey_jarvis"
DEFAULT_THRESHOLD = 0.5

SILENCE_TAIL_SECONDS = 1.2  # stop recording after this much quiet

# Speech recognition model. "base.en" is the fastest; "small.en" and
# "medium.en" are markedly better with accents and quiet speech, at the cost of
# a larger one-time download and slower transcription.
DEFAULT_STT_MODEL = os.environ.get("HEY_JARVIS_STT", "small.en")
MAX_UTTERANCE_SECONDS = 15.0
NO_SPEECH_TIMEOUT = 6.0
WAKE_COOLDOWN_SECONDS = 0.8

DEFAULT_OLLAMA = "http://127.0.0.1:11434"
PREFERRED_MODELS = ("qwen3.5:2b", "qwen3.5:4b")

SYSTEM_PROMPT = (
    "You are Jarvis, a calm and precise voice assistant running on the user's "
    "own computer. Your replies are spoken aloud, so use plain conversational "
    "prose: no markdown, no bullet points, no code blocks unless explicitly "
    "asked for. Keep most answers to one to three sentences. If you do not know "
    "something, say so briefly instead of guessing."
)

IDLE = "Sleeping"
LISTENING = "Listening"
THINKING = "Thinking"
SPEAKING = "Speaking"
PAUSED = "Paused"


# ------------------------------------------------------------------- helpers --


def rms(frame: np.ndarray) -> float:
    """Root-mean-square level of an int16 audio frame."""
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(frame.astype(np.float64)))))


def to_float_audio(frames: list[np.ndarray]) -> np.ndarray:
    """Concatenate int16 frames into the float32 mono array Whisper wants."""
    if not frames:
        return np.zeros(0, dtype=np.float32)
    audio = np.concatenate(frames).astype(np.float32) / 32768.0
    return np.clip(audio, -1.0, 1.0)


def normalize_audio(audio: np.ndarray, target_peak: float = 0.9) -> np.ndarray:
    """Lift quiet recordings to a consistent level.

    Recognition quality drops sharply when the speaker is far from the mic or
    speaks softly, which hits non-native speakers hardest. Scaling to a
    consistent peak removes that variable. Near-silence is left untouched so
    noise is not amplified into words.
    """
    if audio.size == 0:
        return audio
    peak = float(np.max(np.abs(audio)))
    if peak < 0.02:
        return audio
    return np.clip(audio * (target_peak / peak), -1.0, 1.0)


def check_ollama(url: str, timeout: float = 3.0) -> tuple[bool, list[str], str]:
    """Return (reachable, model names, message) for a local Ollama server."""
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - dependency guard
        return False, [], f"requests is not installed: {exc}"

    try:
        response = requests.get(f"{url}/api/tags", timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - any transport error is the message
        return False, [], f"cannot reach {url} ({type(exc).__name__}: {exc})"

    names = [str(m.get("name", "")) for m in payload.get("models", []) if m.get("name")]
    if not names:
        return True, [], f"{url} is running but has no models yet"
    return True, names, f"{url} is running with {len(names)} model(s)"


def pick_model(available: list[str], preferred: Optional[str]) -> str:
    """Choose the model to talk to: explicit choice, then a preferred size."""
    if preferred:
        return preferred
    for candidate in PREFERRED_MODELS:
        if candidate in available:
            return candidate
    return available[0] if available else (preferred or PREFERRED_MODELS[0])


# ---------------------------------------------------------------- assistant --


class Assistant:
    """Owns the microphone, the wake word model, the brain and the voice."""

    def __init__(
        self,
        notify: Optional[Callable[[str, str], None]] = None,
        threshold: float = DEFAULT_THRESHOLD,
        ollama_url: str = DEFAULT_OLLAMA,
        model: Optional[str] = None,
        stt_model: Optional[str] = None,
        voice_backend: str = "auto",
        voice_name: Optional[str] = None,
    ) -> None:
        self.notify = notify or (lambda kind, text: None)
        self.threshold = threshold
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model
        self.stt_model = stt_model or DEFAULT_STT_MODEL
        self.voice_backend = voice_backend
        self.voice_name = voice_name
        self._voice_used = ""

        self._frames: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=200)
        self._typed: "queue.Queue[str]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stream = None
        self._wake = None
        self._whisper = None
        self._running = False
        self._paused = threading.Event()
        self._state = IDLE
        self._state_lock = threading.Lock()
        self._noise_floor = 250.0
        self._history: list[dict[str, str]] = []
        self._speak_lock = threading.Lock()
        self._respond_lock = threading.Lock()

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Open the microphone and begin listening in a worker thread."""
        self._load_wake_model()
        self._open_stream()
        self._running = True
        self._thread = threading.Thread(target=self._run, name="jarvis-audio", daemon=True)
        self._thread.start()
        self._set_state(IDLE)

    def stop(self) -> None:
        self._running = False
        try:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        except Exception:  # noqa: BLE001 - shutdown must never raise
            pass
        self._stream = None

    def pause(self, paused: bool) -> None:
        if paused:
            self._paused.set()
            self._set_state(PAUSED)
        else:
            self._paused.clear()
            self._set_state(IDLE)

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def submit_text(self, text: str) -> None:
        """Queue a typed message, for when speaking is inconvenient."""
        text = (text or "").strip()
        if text:
            self._typed.put(text)

    # -- state --------------------------------------------------------------

    def _set_state(self, state: str) -> None:
        with self._state_lock:
            if self._state == state:
                return
            self._state = state
        self.notify("state", state)

    def _get_state(self) -> str:
        with self._state_lock:
            return self._state

    # -- setup --------------------------------------------------------------

    def _load_wake_model(self) -> None:
        from openwakeword.model import Model
        from openwakeword.utils import download_models

        try:
            self._wake = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
        except Exception:  # noqa: BLE001 - first run: fetch the pretrained model
            download_models(model_names=[WAKE_MODEL])
            self._wake = Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")

    def _open_stream(self) -> None:
        import sounddevice as sd

        def callback(indata, frames, time_info, status):  # noqa: ANN001 - PortAudio API
            if status:
                # Underruns are common and harmless; never raise from the audio thread.
                pass
            try:
                self._frames.put_nowait(indata[:, 0].copy())
            except queue.Full:
                pass

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=BLOCK,
            callback=callback,
        )
        self._stream.start()

    def _load_whisper(self):
        from faster_whisper import WhisperModel

        if self._whisper is None:
            self.notify(
                "hint",
                f"Loading speech recognition '{self.stt_model}' (first run downloads it)...",
            )
            self._whisper = WhisperModel(
                self.stt_model, device="cpu", compute_type="int8"
            )
        return self._whisper

    # -- main loop ----------------------------------------------------------

    def _run(self) -> None:
        while self._running:
            typed = self._drain_typed()
            if typed:
                self._respond_to(typed)
                continue

            try:
                frame = self._frames.get(timeout=0.25)
            except queue.Empty:
                continue

            if self._paused.is_set() or self._get_state() != IDLE:
                continue

            level = rms(frame)
            # Track the room's noise floor so quiet rooms still trigger.
            self._noise_floor = 0.97 * self._noise_floor + 0.03 * level

            score = float(self._wake.predict(frame)[WAKE_MODEL])
            if score < self.threshold:
                continue

            utterance = self._record_utterance()
            self._wake.reset()
            if utterance is None:
                self.notify("hint", "Heard my name but no question.")
                time.sleep(WAKE_COOLDOWN_SECONDS)
                continue

            text = self._transcribe(utterance)
            if not text:
                self.notify("hint", "I could not make out any words.")
                time.sleep(WAKE_COOLDOWN_SECONDS)
                continue

            self._respond_to(text)
            time.sleep(WAKE_COOLDOWN_SECONDS)

    def _drain_typed(self) -> Optional[str]:
        try:
            return self._typed.get_nowait()
        except queue.Empty:
            return None

    def _record_utterance(self) -> Optional[list[np.ndarray]]:
        """Record from the live stream until the speaker stops talking."""
        self._set_state(LISTENING)
        self.notify("hint", "")

        captured: list[np.ndarray] = []
        started = False
        silent_blocks = 0
        started_at = time.monotonic()

        silence_needed = int(SILENCE_TAIL_SECONDS / BLOCK_SECONDS)
        max_blocks = int(MAX_UTTERANCE_SECONDS / BLOCK_SECONDS)

        while self._running:
            try:
                frame = self._frames.get(timeout=0.5)
            except queue.Empty:
                if time.monotonic() - started_at > NO_SPEECH_TIMEOUT:
                    self._set_state(IDLE)
                    return None
                continue

            threshold = max(350.0, self._noise_floor * 2.5)
            level = rms(frame)

            if level > threshold:
                started = True
                silent_blocks = 0
            elif started:
                silent_blocks += 1

            if started:
                captured.append(frame)

            if started and silent_blocks >= silence_needed:
                break

            if len(captured) >= max_blocks:
                break

            if not started and time.monotonic() - started_at > NO_SPEECH_TIMEOUT:
                self._set_state(IDLE)
                return None

        self._set_state(THINKING)
        if len(to_float_audio(captured)) < SAMPLE_RATE * 0.25:
            return None
        return captured

    def _transcribe(self, frames: list[np.ndarray]) -> str:
        audio = normalize_audio(to_float_audio(frames))
        model = self._load_whisper()
        segments, _info = model.transcribe(
            audio,
            language="en",
            vad_filter=True,
            beam_size=5,  # more search per word: better on unfamiliar accents
            condition_on_previous_text=False,  # one utterance, no drift from earlier ones
        )
        return " ".join(segment.text.strip() for segment in segments).strip()

    # -- brain --------------------------------------------------------------

    def respond(self, text: str, speak: bool = True) -> str:
        """One full turn -- think, remember, optionally speak -- and return the reply.

        Device adapters call this so they share exactly the same pipeline as the
        microphone: same history, same model, same voice.
        """
        text = (text or "").strip()
        if not text:
            return ""

        with self._respond_lock:
            self._set_state(THINKING)
            self.notify("user", text)

            self._history.append({"role": "user", "content": text})
            self._history = self._history[-12:]

            try:
                reply = self._ask_model()
            except Exception as exc:  # noqa: BLE001 - surface it, keep listening
                self.notify("error", f"Local model unavailable: {exc}")
                return ""

            if reply:
                self._history.append({"role": "assistant", "content": reply})
                self.notify("assistant", reply)
                if speak:
                    self._set_state(SPEAKING)
                    self._speak(reply)

            self._set_state(PAUSED if self._paused.is_set() else IDLE)
            return reply

    def _respond_to(self, text: str) -> None:
        self.respond(text)

    def _ask_model(self) -> str:
        import requests

        available = self._available_models()
        model = pick_model(available, self.model)
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *self._history],
            "stream": False,
        }
        response = requests.post(f"{self.ollama_url}/api/chat", json=payload, timeout=180)
        response.raise_for_status()
        message = response.json().get("message", {})
        return str(message.get("content", "")).strip()

    def _available_models(self) -> list[str]:
        reachable, names, message = check_ollama(self.ollama_url)
        if not reachable:
            raise RuntimeError(
                f"{message}. Start Ollama, then run: ollama pull {PREFERRED_MODELS[0]}"
            )
        if not names:
            raise RuntimeError("Ollama is running but has no models. Run: ollama pull qwen3.5:2b")
        return names

    # -- voice --------------------------------------------------------------

    def _speak(self, text: str) -> None:
        """Read the reply aloud with the best available local voice."""
        if not text.strip():
            return
        with self._speak_lock:
            try:
                backs = voices.speak(text, backend=self.voice_backend, voice=self.voice_name)
                if backs and backs != self._voice_used:
                    self._voice_used = backs
                    self.notify("hint", f"Voice: {backs}")
            except Exception as exc:  # noqa: BLE001 - audio device may be busy
                self.notify("error", f"Could not speak the reply: {exc}")


# ----------------------------------------------------------------------- ui --


class Window:
    """A small always-on-top window with the conversation and a status line."""

    def __init__(self, assistant: Assistant) -> None:
        import tkinter as tk
        from tkinter import scrolledtext

        self.assistant = assistant
        self.root = tk.Tk()
        self.root.title("Jarvis")
        self.root.geometry("560x460")
        self.root.minsize(420, 320)
        self.root.configure(bg="#101418")

        self.status = tk.Label(
            self.root,
            text=IDLE + "  -  say \"Hey Jarvis\"",
            anchor="w",
            padx=14,
            pady=10,
            bg="#101418",
            fg="#7fd1ff",
            font=("Segoe UI", 11),
        )
        self.status.pack(fill="x")

        self.log = scrolledtext.ScrolledText(
            self.root,
            wrap="word",
            bg="#0b0e11",
            fg="#e6edf3",
            insertbackground="#e6edf3",
            relief="flat",
            padx=12,
            pady=10,
            font=("Segoe UI", 10),
        )
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        self.log.configure(state="disabled")
        self.log.tag_configure("user", foreground="#9ad7ff")
        self.log.tag_configure("assistant", foreground="#e6edf3")
        self.log.tag_configure("hint", foreground="#8b949e")
        self.log.tag_configure("error", foreground="#ff8f8f")

        entry_row = tk.Frame(self.root, bg="#101418")
        entry_row.pack(fill="x", padx=10, pady=(0, 8))

        self.entry = tk.Entry(
            entry_row,
            bg="#0b0e11",
            fg="#e6edf3",
            insertbackground="#e6edf3",
            relief="flat",
            font=("Segoe UI", 10),
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=5, padx=(2, 6))
        self.entry.bind("<Return>", self._send)
        self.entry.insert(0, "Type here if speaking is awkward...")
        self.entry.bind("<FocusIn>", self._clear_placeholder)

        self.pause_button = tk.Button(
            entry_row,
            text="Pause",
            command=self._toggle_pause,
            bg="#1b2229",
            fg="#e6edf3",
            relief="flat",
            padx=12,
        )
        self.pause_button.pack(side="left", padx=2)

        buttons = tk.Frame(self.root, bg="#101418")
        buttons.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(
            buttons,
            text="Quit",
            command=self._quit,
            bg="#1b2229",
            fg="#e6edf3",
            relief="flat",
            padx=12,
        ).pack(side="right")

    # -- ui callbacks -------------------------------------------------------

    def _clear_placeholder(self, _event=None) -> None:  # noqa: ANN001
        if self.entry.get().startswith("Type here"):
            self.entry.delete(0, "end")

    def _send(self, _event=None) -> None:  # noqa: ANN001
        text = self.entry.get().strip()
        if not text or text.startswith("Type here"):
            return
        self.entry.delete(0, "end")
        self.assistant.submit_text(text)

    def _toggle_pause(self) -> None:
        paused = not self.assistant.paused
        self.assistant.pause(paused)
        self.pause_button.configure(text="Resume" if paused else "Pause")

    def _quit(self) -> None:
        self.assistant.stop()
        self.root.quit()
        self.root.destroy()

    # -- assistant -> ui ----------------------------------------------------

    def show_message(self, kind: str, text: str) -> None:
        """Append a line to the conversation (called from worker threads)."""
        if kind in ("user", "assistant") and not text:
            return
        self.root.after(0, lambda: self._append(kind, text))

    def show_state(self, state: str) -> None:
        label = state + '  -  say "Hey Jarvis"' if state == IDLE else state
        self.root.after(0, lambda: self.status.configure(text=label))

    def _append(self, kind: str, text: str) -> None:
        if not text:
            return
        prefix = {"user": "You: ", "assistant": "Jarvis: ", "error": "!", "hint": ""}.get(kind, "")
        self.log.configure(state="normal")
        self.log.insert("end", f"{prefix}{text}\n", kind if kind in ("user", "assistant", "hint", "error") else "assistant")
        self.log.see("end")
        self.log.configure(state="disabled")

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.mainloop()


class Tray:
    """Optional tray icon: show the window, pause listening, or quit."""

    def __init__(self, window: Window, assistant: Assistant) -> None:
        self.window = window
        self.assistant = assistant
        self.icon = None

    def start(self) -> None:
        import pystray
        from PIL import Image, ImageDraw

        image = Image.new("RGB", (64, 64), "#101418")
        draw = ImageDraw.Draw(image)
        draw.ellipse((12, 12, 52, 52), fill="#7fd1ff")

        menu = pystray.Menu(
            pystray.MenuItem("Show Jarvis", lambda: self.window.root.after(0, self.window.root.deiconify), default=True),
            pystray.MenuItem(
                "Pause listening",
                lambda: self.window.root.after(0, self.window._toggle_pause),
            ),
            pystray.MenuItem("Quit", lambda: self.window.root.after(0, self.window._quit)),
        )
        self.icon = pystray.Icon("jarvis", image, "Jarvis", menu)
        threading.Thread(target=self.icon.run, name="jarvis-tray", daemon=True).start()

    def stop(self) -> None:
        if self.icon is not None:
            try:
                self.icon.stop()
            except Exception:  # noqa: BLE001
                pass


# ----------------------------------------------------------------- selftest --


def selftest(model: Optional[str], stt_model: Optional[str] = None) -> int:
    """Check every dependency and device without listening to anything."""
    failures = 0
    speech_model = stt_model or DEFAULT_STT_MODEL

    def report(ok: bool, label: str, detail: str = "") -> None:
        nonlocal failures
        failures += 0 if ok else 1
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {label}" + (f" -- {detail}" if detail else ""))

    try:
        import sounddevice as sd

        devices = sd.query_devices()
        inputs = [d for d in devices if int(d.get("max_input_channels", 0)) > 0]
        default_in = sd.query_devices(kind="input").get("name") if inputs else ""
        report(bool(inputs), "Microphone", f"{len(inputs)} input device(s); default: {default_in}")
    except Exception as exc:  # noqa: BLE001
        report(False, "Microphone", f"{type(exc).__name__}: {exc}")

    try:
        from openwakeword.model import Model

        Model(wakeword_models=[WAKE_MODEL], inference_framework="onnx")
        report(True, "Wake word model ('hey jarvis')")
    except Exception as exc:  # noqa: BLE001
        report(False, "Wake word model", f"{type(exc).__name__}: {exc}")

    try:
        import faster_whisper  # noqa: F401

        report(True, "Speech recognition (faster-whisper)", f"model: {speech_model}")
    except Exception as exc:  # noqa: BLE001
        report(False, "Speech recognition", f"{type(exc).__name__}: {exc}")

    report(bool(voices.available()), "Voice output", voices.describe())

    reachable, names, message = check_ollama(DEFAULT_OLLAMA)
    if reachable and names:
        report(True, "Local model server", message + f"; will use {pick_model(names, model)}")
    else:
        report(False, "Local model server", message + " -- install Ollama and run: ollama pull qwen3.5:2b")

    try:
        config = json.loads(
            (Path(__file__).resolve().parent / "devices.json").read_text(encoding="utf-8")
        )
        enabled = [
            name
            for name, options in config.items()
            if isinstance(options, dict) and options.get("enabled")
        ]
        detail = f"{len(enabled)} enabled ({', '.join(enabled)})" if enabled else "none enabled (devices.json)"
        report(True, "Device adapters", detail)
    except Exception as exc:  # noqa: BLE001
        report(False, "Device adapters", f"{type(exc).__name__}: {exc}")

    print()
    if failures:
        print(f"{failures} check(s) failed. Fix those, then run this again.")
    else:
        print("All checks passed. Start the assistant with: run.ps1")
    return 1 if failures else 0


# --------------------------------------------------------------------- main --


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Always-listening local 'Hey Jarvis' assistant.")
    parser.add_argument("--selftest", action="store_true", help="check dependencies and devices, then exit")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD, help="wake word sensitivity (0-1)")
    parser.add_argument("--model", default=None, help="Ollama model name to use")
    parser.add_argument(
        "--stt",
        default=None,
        help=f"speech recognition model (default {DEFAULT_STT_MODEL}; try medium.en for harder accents)",
    )
    parser.add_argument(
        "--voice",
        default="auto",
        choices=("auto", "kokoro", "sapi"),
        help="voice engine: auto (best available), kokoro (neural British voice), sapi (Windows built-in)",
    )
    parser.add_argument("--no-ui", action="store_true", help="run in the console instead of a window")
    args = parser.parse_args(argv)

    if args.selftest:
        return selftest(args.model, args.stt)

    if args.no_ui:
        assistant = Assistant(
            notify=lambda kind, text: print(f"{kind}: {text}" if text else kind, flush=True),
            threshold=args.threshold,
            model=args.model,
            stt_model=args.stt,
            voice_backend=args.voice,
        )
        assistant.start()
        adapters = load_devices(assistant)
        print('Listening. Say "Hey Jarvis". Press Ctrl+C to quit.')
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            stop_devices(adapters)
            assistant.stop()
        return 0

    try:
        import tkinter  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"A graphical window is unavailable ({exc}). Re-run with --no-ui.", file=sys.stderr)
        return 1

    placeholder = Assistant(
        notify=lambda kind, text: None,
        threshold=args.threshold,
        model=args.model,
        stt_model=args.stt,
        voice_backend=args.voice,
    )
    window = Window(placeholder)
    assistant = Assistant(
        notify=_make_notifier(window),
        threshold=args.threshold,
        model=args.model,
        stt_model=args.stt,
        voice_backend=args.voice,
    )
    window.assistant = assistant

    try:
        tray = Tray(window, assistant)
        tray.start()
    except Exception:  # noqa: BLE001 - tray is a convenience, not a requirement
        tray = None

    adapters = []
    try:
        assistant.start()
        adapters = load_devices(assistant)
    except Exception as exc:  # noqa: BLE001
        window.show_message("error", f"Could not start listening: {exc}")
        window.show_message("hint", "Run setup.ps1, then `python hey_jarvis.py --selftest`.")

    window.run()
    if tray is not None:
        tray.stop()
    stop_devices(adapters)
    assistant.stop()
    return 0


def _make_notifier(window: Window) -> Callable[[str, str], None]:
    def notify(kind: str, text: str) -> None:
        if kind == "state":
            window.show_state(text)
        else:
            window.show_message(kind, text)

    return notify


if __name__ == "__main__":
    raise SystemExit(main())
