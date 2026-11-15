# Hey Jarvis — always-listening local assistant

A small Windows desktop app: it sleeps until it hears **"Hey Jarvis"**, records
until you stop talking, transcribes locally, asks a local model, and speaks the
answer with a system voice.

- Wake word, speech recognition, model and voice all run on this PC.
- No browser, no cloud, no account.
- Only local traffic: Ollama on `127.0.0.1:11434`.

## Setup (once)

```powershell
cd examples\hey_jarvis
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

The script finds Python 3.10–3.13 (installs 3.13 via winget if needed), builds a
private `.venv`, installs the dependencies, downloads the pretrained "hey
jarvis" model, and runs a self-test.

You also need a local model server: install [Ollama](https://ollama.com/download)
for Windows, then pull a model once.

```powershell
ollama pull qwen3.5:2b
```

## Run

```powershell
.\run.ps1            # window + tray icon, no console
.\run.ps1 -Console   # same, but keeps a console for error output
```

Say **"Hey Jarvis"**, wait for `Listening`, then ask your question. You can also
type in the window's text box. The tray icon can pause listening or quit.

## Checks and tuning

```powershell
.\.venv\Scripts\python.exe hey_jarvis.py --selftest      # devices, models, server
.\.venv\Scripts\python.exe hey_jarvis.py --threshold 0.6 # stricter wake word
.\.venv\Scripts\python.exe hey_jarvis.py --model qwen3.5:4b --no-ui
```

`--threshold` (default `0.5`) is how confident the wake-word model must be.
Raise it if the app wakes on background speech; lower it if it ignores you.

## The voice

The reply voice defaults to Windows' SAPI engine: instant and always present,
but robotic, and most PCs only ship American voices.

For something much closer to the film's Jarvis -- calm, low, British -- install
the optional neural voice (one-time download of a few hundred MB, runs on CPU):

```powershell
powershell -ExecutionPolicy Bypass -File .\install_voice.ps1
```

It installs `kokoro`, prints the available backends, then speaks a test line so
you can hear it before launching the app. `run.ps1` then picks it up
automatically: `--voice auto` prefers the neural voice and falls back to SAPI if
anything is missing, so a broken voice stack never leaves the assistant mute.

Other voices, via `HEY_JARVIS_KOKORO_VOICE` (`bm_george` is the default):

| Value | Voice |
| --- | --- |
| `bm_george` | British male, measured -- closest to the film |
| `bm_lewis` | British male, deeper |
| `bm_fable` | British male, softer |
| `am_michael` | American male |
| `af_heart` | American female |

```powershell
$env:HEY_JARVIS_KOKORO_VOICE = "bm_lewis"
.\run.ps1
```

Force one engine explicitly with `--voice sapi` or `--voice kokoro`.

If `pip` reports **"no matching distribution found for kokoro"**, the Python in
`.venv` is newer than 3.12 and the neural voice cannot be installed at all.
Rebuild the environment with a supported Python:

```powershell
powershell -ExecutionPolicy Bypass -File .\rebuild_venv.ps1
```

Replies are cleaned before they are spoken — markdown, links and emoji are
removed rather than pronounced — and synthesised one sentence at a time, because
neural voices drift on long passages. The default pace is slightly under
conversational (`0.96`); adjust it with `--voice-speed` (0.9 is calmer, 1.0 is
the model default):

```powershell
.\run.ps1 --voice-speed 0.92
```

To hear a voice without starting the whole assistant:

```powershell
.\.venv\Scripts\python.exe hey_jarvis.py --say "Good evening, sir. All systems are online."
```

Add `--voice xtts --voice-sample .\my_takes` to audition the custom voice, or
`--voice kokoro` for the built-in British one.

### Your own voice

A third backend clones a voice from a local recording: XTTS-v2 running on your
CPU. Use a recording you have the rights to — your own voice, or someone who has
given you permission.

```powershell
powershell -ExecutionPolicy Bypass -File .\install_custom_voice.ps1 -Sample my_voice.wav
```

A clean 20–30 second recording with one speaker and no background noise works best.
Any format the machine can decode (wav, mp3, flac) is converted to mono 24 kHz
automatically — even an mp3 that Windows is mistakenly labelling as a wav.

Several takes can be combined into one voice: point `-Sample` at a folder, or
give a comma-separated list. They are joined into a single reference (capped at
60 seconds), which clones more steadily than one short clip.
The file stays on this PC; nothing is uploaded. Afterwards:

```powershell
$env:HEY_JARVIS_VOICE_SAMPLE = "$PWD\my_voice.wav"
.\run.ps1                       # auto now prefers your sample
.\run.ps1 --voice xtts          # or force it
```

Two caveats: the XTTS-v2 model licence is **non-commercial** (personal use only),
and generation is slower than Kokoro on CPU. Film or broadcast audio of a real
person is copyrighted separately from their spoken permission, so keep to
audio you recorded yourself.

## If it mishears you

Accents, quiet speech and distance from the microphone are the usual causes.
Three knobs, in order of impact:

```powershell
# 1. Bigger recognition model: much better with accents, ~500 MB, slower
.\venv\Scripts\python.exe hey_jarvis.py --stt medium.en

# 2. Harder accents still, or noisy rooms (largest download, slowest)
.\venv\Scripts\python.exe hey_jarvis.py --stt large-v3

# 3. Wake word too eager or too deaf
.\venv\Scripts\python.exe hey_jarvis.py --threshold 0.65
```

Available sizes: `tiny.en`, `base.en`, `small.en` (the default), `medium.en`,
`large-v3`. The `.en` variants are English-only and more accurate for English;
`large-v3` is multilingual but several times slower on CPU. Quiet recordings are
normalised automatically before transcription, so speaking softly no longer
degrades accuracy as much as it used to.

Set it once for good with an environment variable instead of a flag:

```powershell
$env:HEY_JARVIS_STT = "medium.en"
```

## Connecting devices later

Devices plug in through `devices/` and are switched on in `devices.json`. Each
adapter receives the running assistant, so anything a device sends reuses the
same model, memory and voice as the microphone.

The included `http_bridge` adapter is the general-purpose door: any device that
can make a web request can use it — a phone shortcut, a TV app, a smart speaker,
a home-automation hub, a script on another machine.

```json
{
  "http_bridge": { "enabled": true, "host": "127.0.0.1", "port": 8765, "auth_token": "", "speak": true }
}
```

```powershell
curl.exe -s -X POST http://127.0.0.1:8765/ask -H "Content-Type: application/json" -d '{"text":"what can you do"}'
```

To reach it from your phone, set `"host": "0.0.0.0"` **and** a long
`"auth_token"`. The bridge refuses to start on a network address without a
token, and clients send it as `Authorization: Bearer <token>` or
`X-Jarvis-Token: <token>`. Replies are spoken on the PC unless `"speak": false`.

A new device type (Bluetooth audio, TV casting, a phone app) is one module in
`devices/` exposing `DEVICE = YourAdapter`, enabled by name in `devices.json`. A
device that fails to start is reported in the window and never takes the
assistant down.

## Files

| File | Purpose |
| --- | --- |
| `hey_jarvis.py` | The app: wake word, recording, transcription, model call, speech, UI |
| `setup.ps1` | One-time environment setup and self-test |
| `run.ps1` | Launcher (`-Console` to keep a console) |
| `requirements.txt` | Python dependencies |

Private data stays inside this folder: `.venv/` (dependencies) and the model
caches under your user profile (`~/.cache/huggingface` for Whisper,
`~/.cache/openwakeword` for the wake-word models).
