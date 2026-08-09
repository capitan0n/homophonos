"""All interaction with `pactl` lives in this module.

Nothing else in the package should call subprocess directly. That
keeps the rest of the codebase testable (mock this module) and means
there's exactly one place that knows about pactl's CLI quirks.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Optional

from pwcombine.models import Sink

PACTL_BIN = "pactl"

# Combined sinks created by this tool are named with this prefix so we
# can reliably tell them apart from real hardware sinks later (e.g.
# when deciding what's safe to offer as "slaves" for a new combo).
COMBINED_SINK_PREFIX = "combined_"


class PactlError(RuntimeError):
    """Raised when a pactl invocation fails or returns unusable output."""


def _escape_property_value(value: str) -> str:
    r"""Escape a string for use inside a pactl module property value.

    The value ends up inside sink_properties=device.description="<value>".
    The module-argument property parser treats a double quote as the end
    of the quoted value and backslash as an escape char, so a raw " or \
    in a user-chosen label would either truncate the description or break
    parsing entirely. We backslash-escape both so the label survives
    verbatim. Newlines are stripped because a property value is a single
    logical line and an embedded newline can desync the parser.
    """
    cleaned = value.replace("\r", " ").replace("\n", " ")
    # Order matters: escape backslashes first so we don't double-escape
    # the backslashes we insert in front of quotes on the next step.
    cleaned = cleaned.replace("\\", "\\\\").replace('"', '\\"')
    return cleaned


def _run(args: list[str], timeout: float = 10.0) -> subprocess.CompletedProcess:
    """Run a pactl command and return the completed process.

    Raises PactlError on missing binary, timeout, or non-zero exit.
    Centralizing this means every caller gets the same error handling.
    """
    try:
        result = subprocess.run(
            [PACTL_BIN, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise PactlError(
            "'pactl' was not found on PATH. Is pipewire-pulse/pulseaudio-utils installed?"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise PactlError(f"pactl timed out running: {' '.join(args)}") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip() or "(no stderr output)"
        raise PactlError(f"pactl {' '.join(args)} failed: {stderr}")

    return result


def is_available() -> bool:
    """Cheap check used at startup to fail fast with a clear message."""
    return shutil.which(PACTL_BIN) is not None


def list_sinks() -> list[Sink]:
    """Return all sinks currently known to the audio server.

    Uses `pactl -f json list sinks` and parses structured JSON rather
    than scraping the human-readable text format, so we're not at the
    mercy of formatting changes across pactl versions.
    """
    result = _run(["-f", "json", "list", "sinks"])

    try:
        raw_sinks = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise PactlError(
            "Could not parse pactl JSON output. Your pactl version may not "
            "support '-f json' (needs pipewire-pulse or pulseaudio >= 15)."
        ) from exc

    sinks: list[Sink] = []
    for entry in raw_sinks:
        try:
            index = int(entry["index"])
            name = entry["name"]
        except (KeyError, TypeError, ValueError):
            # Skip malformed entries rather than crashing the whole
            # listing -- one weird sink shouldn't take down the tool.
            continue

        description = entry.get("description", name)
        driver = entry.get("driver", "")
        is_combined = name.startswith(COMBINED_SINK_PREFIX) or driver.endswith(
            "module-combine-sink.c"
        )
        owner_module = entry.get("owner_module")
        module_index = None
        if owner_module is not None:
            try:
                module_index = int(owner_module)
            except (TypeError, ValueError):
                module_index = None
            else:
                # PulseAudio/PipeWire report "no owning module" as the
                # 32-bit sentinel 4294967295 (2**32 - 1), not null. Treat
                # it as absent so we never try to `unload-module` it.
                if module_index == 0xFFFFFFFF:
                    module_index = None

        sinks.append(
            Sink(
                index=index,
                name=name,
                description=description,
                driver=driver,
                is_combined=is_combined,
                module_index=module_index,
            )
        )

    return sinks


def get_default_sink_name() -> Optional[str]:
    """Return the name of the current default sink, or None if unknown."""
    result = _run(["-f", "json", "info"])
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return info.get("default_sink_name")


def set_default_sink(sink_name: str) -> None:
    _run(["set-default-sink", sink_name])


def load_combine_sink_module(
    sink_name: str,
    display_name: str,
    member_sink_names: list[str],
) -> int:
    """Load module-combine-sink and return the new module's index.

    `member_sink_names` is a plain list of existing sink names to
    combine. On PipeWire's module-combine-sink the argument is
    `sinks=` (comma-separated), NOT PulseAudio's legacy `slaves=`, and
    it does NOT accept an inline `name:offset` per-sink latency syntax.
    Per-sink latency compensation is applied separately afterwards via
    set_sink_latency_offset(), so callers must pass bare sink names
    here.
    """
    if not member_sink_names:
        raise PactlError("Cannot create a combined sink with zero member sinks.")

    sinks_value = ",".join(member_sink_names)
    # Each key=value pair is its own argv element -- pactl (like the
    # underlying module loader) splits module arguments on whitespace,
    # so a description containing spaces must stay inside one argv
    # item, quoted for the property parser, not split across items.
    # The label is user-chosen free text, so its quotes/backslashes are
    # escaped first; otherwise a " in the name would truncate the value.
    safe_description = _escape_property_value(display_name)
    args = [
        "load-module",
        "module-combine-sink",
        f"sink_name={sink_name}",
        f'sink_properties=device.description="{safe_description}"',
        f"sinks={sinks_value}",
    ]

    result = _run(args)
    # pactl prints the new module index on stdout.
    output = result.stdout.strip()
    try:
        return int(output)
    except ValueError as exc:
        raise PactlError(
            f"module-combine-sink loaded but returned unexpected output: {output!r}"
        ) from exc


def unload_module(module_index: int) -> None:
    _run(["unload-module", str(module_index)])


def set_sink_latency_offset(sink_name: str, offset_us: int) -> None:
    """Adjust a sink's latency offset without recreating the combine sink.

    NOTE (needs verification on real PipeWire hardware): `pactl` exposes
    set-sink-latency-offset, but on PipeWire the more reliably-effective
    control for Bluetooth desync is often set-port-latency-offset on the
    device's bluez_card.* + port, which acts at the card/port level. If
    per-sink offsets turn out to have no audible effect on your setup,
    that's the alternative to try. This is called out here rather than
    silently assumed because it can't be confirmed without live audio.
    """
    _run(["set-sink-latency-offset", sink_name, str(offset_us)])
