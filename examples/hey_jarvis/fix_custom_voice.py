#!/usr/bin/env python3
"""fix_custom_voice.py -- repair the custom-voice engine.

The custom voice (XTTS via coqui-tts) fails to load when ``torchcodec`` is
installed for a different PyTorch build than the one present:

    Could not load this library: ...\\torchcodec\\libtorchcodec_core4.dll

Every torchcodec release is compiled against one exact PyTorch version, so the
DLL name changes and nothing matches. PyTorch 2.8 and older decode audio through
torchaudio instead and never load torchcodec, so this pins that pair, removes
torchcodec, verifies the engine imports, then speaks a test line.

    python fix_custom_voice.py --check
    python fix_custom_voice.py --sample .\\my_takes --accept-terms

Everything here is plain subprocess work, so the outcome does not depend on
PowerShell's error handling.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

TORCH_VERSION = "2.8.0"
ENGINE_VERSION = "0.28.0"


def probe(module: str) -> str:
    """Version of an importable module, or a plain marker if it is not there."""
    try:
        imported = __import__(module)
    except Exception:  # noqa: BLE001 - any import failure means "unusable"
        return "not installed"
    return str(getattr(imported, "__version__", "unknown"))


def show_versions() -> None:
    print("==> Installed now")
    for module in ("torch", "torchaudio", "torchcodec", "TTS"):
        print(f"    {module:<11} {probe(module)}")


def pip(*args: str) -> int:
    command = [sys.executable, "-m", "pip", *args]
    print(f"==> pip {' '.join(args)}", flush=True)
    return subprocess.call(command)


def engine_imports() -> bool:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import torch, torchaudio; from TTS.api import TTS; "
            "print('    torch', torch.__version__, '- engine loaded')",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print(result.stdout.strip())
        return True
    output = (result.stderr or result.stdout).strip().splitlines()
    print("    engine failed to import:", flush=True)
    for line in output[-6:]:
        print("      " + line)
    return False


def speak_test(sample: str, accept_terms: bool) -> int:
    if sample:
        os.environ["HEY_JARVIS_VOICE_SAMPLE"] = str(Path(sample).resolve())
    if accept_terms:
        # Same effect as answering "y": consent to the non-commercial CPML.
        os.environ["COQUI_TOS_AGREED"] = "1"

    code = (
        "import voices\n"
        "print('    engine:', voices.xtts_status())\n"
        "print('    spoken with:', voices.speak("
        "'Good evening. All systems are online and ready.', backend='xtts'))\n"
    )
    print("==> Speaking a test line in the custom voice", flush=True)
    result = subprocess.run([sys.executable, "-c", code], text=True)
    if result.returncode != 0:
        print()
        print("The custom voice could not speak. The message above names the cause.")
        return result.returncode
    print()
    print("Done. Use it with:")
    print(f'  .\\run.ps1 --voice xtts --voice-sample "{sample or ".<sample folder>"}"')
    return 0


def repair(sample: str, accept_terms: bool) -> int:
    show_versions()

    print()
    print("==> Removing torchcodec and the engine")
    pip("uninstall", "-y", "torchcodec", "coqui-tts", "coqui-tts-trainer")

    print()
    print(f"==> Installing PyTorch {TORCH_VERSION} (no torchcodec needed)")
    if pip("install", f"torch=={TORCH_VERSION}", f"torchaudio=={TORCH_VERSION}") != 0:
        print("PyTorch could not be installed. The lines above say why.")
        return 1

    print()
    print("==> Installing the voice engine")
    if pip("install", f"coqui-tts=={ENGINE_VERSION}", "transformers<5", "soundfile") != 0:
        print("coqui-tts could not be installed. The lines above say why.")
        return 1

    print()
    show_versions()

    torch_version = probe("torch")
    if not torch_version.startswith(TORCH_VERSION):
        print()
        print(
            f"PyTorch {torch_version} is installed, but {TORCH_VERSION} is required. "
            "Nothing else is worth doing until that sticks; run this again."
        )
        return 1

    print()
    print("==> Checking the engine imports")
    if not engine_imports():
        return 1

    print()
    return speak_test(sample, accept_terms)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Repair the custom-voice engine.")
    parser.add_argument(
        "--sample",
        default="",
        help="recording or folder of recordings to use as the voice",
    )
    parser.add_argument(
        "--accept-terms",
        action="store_true",
        help="accept the XTTS-v2 model licence (CPML, non-commercial)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="only print what is installed, change nothing",
    )
    args = parser.parse_args(argv)

    if not os.environ.get("HEY_JARVIS_VOICE_SAMPLE") and not args.sample:
        parser.error("give --sample (a recording or a folder of recordings)")

    if args.check:
        show_versions()
        print()
        engine_ok = engine_imports()
        return 0 if engine_ok else 1

    return repair(args.sample, args.accept_terms)


if __name__ == "__main__":
    raise SystemExit(main())
