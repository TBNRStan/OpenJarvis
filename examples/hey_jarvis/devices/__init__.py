"""Device adapters -- how phones, TVs, speakers and anything else reach Jarvis.

An adapter is a small class that receives the running assistant, so it reuses
the same brain, memory and voice as the microphone does:

    reply = assistant.respond("what is the weather")

Adding a device later means adding one module in this folder and enabling it in
``devices.json``:

    class MyDevice(DeviceAdapter):
        name = "my_device"

        def start(self) -> None: ...
        def stop(self) -> None: ...

    DEVICE = MyDevice

Nothing else in the app needs to change, and a device that fails to start never
takes the assistant down with it.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Optional

CONFIG_NAME = "devices.json"


class DeviceAdapter:
    """Base class for anything that can send text to Jarvis and hear a reply."""

    name = "device"

    def __init__(self, assistant: Any, options: Optional[dict[str, Any]] = None) -> None:
        self.assistant = assistant
        self.options = options or {}

    def start(self) -> None:
        """Begin listening for this device. Raise to report a configuration error."""

    def stop(self) -> None:
        """Release sockets, threads and handles. Must not raise."""

    def describe(self) -> str:
        return self.name


def config_path() -> Path:
    return Path(__file__).resolve().parent.parent / CONFIG_NAME


def load_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Read ``devices.json``; a missing file simply means no devices."""
    target = Path(path) if path is not None else config_path()
    if not target.exists():
        return {}
    return json.loads(target.read_text(encoding="utf-8"))


def load_devices(assistant: Any, path: Optional[Path] = None) -> list[DeviceAdapter]:
    """Start every enabled adapter, reporting failures without crashing."""
    try:
        config = load_config(path)
    except (OSError, ValueError) as exc:
        assistant.notify("error", f"devices.json could not be read: {exc}")
        return []

    adapters: list[DeviceAdapter] = []
    for name, options in config.items():
        if not isinstance(options, dict) or not options.get("enabled"):
            continue
        try:
            module = importlib.import_module(f"devices.{name}")
            device_class = getattr(module, "DEVICE")
            adapter = device_class(assistant, options)
            adapter.start()
        except Exception as exc:  # noqa: BLE001 - one bad device must not be fatal
            assistant.notify("error", f"Device '{name}' did not start: {exc}")
            continue
        adapters.append(adapter)
        assistant.notify("hint", f"Device connected: {adapter.describe()}")

    return adapters


def stop_devices(adapters: list[DeviceAdapter]) -> None:
    for adapter in adapters:
        try:
            adapter.stop()
        except Exception:  # noqa: BLE001 - shutdown must never raise
            pass
