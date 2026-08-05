"""Orchestration layer: turns user intent into pactl_client calls.

This module knows the *rules* (e.g. "a profile whose slave isn't
currently connected should warn, not crash") but performs no terminal
I/O itself -- that keeps it usable from the interactive UI, a future
scriptable CLI, or tests, without duplicating logic.
"""

from __future__ import annotations

from dataclasses import dataclass

from pwcombine import pactl_client
from pwcombine.models import Profile, Sink, SlaveSpec


class CombinerError(RuntimeError):
    """Raised for orchestration-level failures (as opposed to raw pactl errors)."""


@dataclass
class ResolvedProfile:
    """A profile checked against the currently live sinks.

    `available` are slaves that exist right now and can be combined;
    `missing` are ones from the saved profile that aren't currently
    present (e.g. a Bluetooth speaker that isn't powered on).
    """

    profile: Profile
    available: list[SlaveSpec]
    missing: list[SlaveSpec]

    @property
    def is_fully_available(self) -> bool:
        return not self.missing

    @property
    def can_create(self) -> bool:
        """A combined sink needs at least two live slaves to be meaningful.

        With fewer than two available, there's nothing to combine, so
        callers should warn rather than attempt creation.
        """
        return len(self.available) >= 2


def get_real_sinks(sinks: list[Sink]) -> list[Sink]:
    """Filter out sinks that are themselves combined sinks we created.

    Prevents a user from accidentally nesting a combined sink inside
    another one, which pactl allows but which tends to produce
    confusing routing and is never what someone wants from this tool.
    """
    return [s for s in sinks if not s.is_combined]


def find_existing_combined_sinks(sinks: list[Sink]) -> list[Sink]:
    return [s for s in sinks if s.is_combined]


def resolve_profile(profile: Profile, live_sinks: list[Sink]) -> ResolvedProfile:
    """Match a saved profile's slave names against currently live sinks."""
    live_names = {s.name for s in live_sinks}
    available = [s for s in profile.slaves if s.sink_name in live_names]
    missing = [s for s in profile.slaves if s.sink_name not in live_names]
    return ResolvedProfile(profile=profile, available=available, missing=missing)


def sink_name_from_display(display_name: str) -> str:
    """Turn a human label like 'PC + BT' into a valid pactl sink name.

    module-combine-sink's sink_name= must be a safe token (no spaces),
    while sink_properties=device.description can carry the pretty
    version for the desktop environment to show instead.
    """
    safe = "".join(c if c.isalnum() else "_" for c in display_name.strip().lower())
    safe = "_".join(filter(None, safe.split("_")))  # collapse repeated underscores
    if not safe:
        safe = "combo"
    return f"{pactl_client.COMBINED_SINK_PREFIX}{safe}"


def create_combined_sink(
    display_name: str,
    slaves: list[SlaveSpec],
    set_as_default: bool = True,
) -> tuple[int, str]:
    """Create a combined sink from the given slaves.

    Returns (module_index, sink_name) so the caller can offer to save
    a profile or report success. Raises CombinerError with a friendly
    message if there aren't enough slaves to combine.
    """
    if len(slaves) < 2:
        raise CombinerError(
            "Select at least two sinks to combine -- a single-sink "
            "'combination' wouldn't do anything module-combine-sink can't "
            "already do by just picking that sink directly."
        )

    sink_name = sink_name_from_display(display_name)
    slave_args = [s.to_pactl_arg() for s in slaves]

    module_index = pactl_client.load_combine_sink_module(
        sink_name=sink_name,
        display_name=display_name,
        slave_args=slave_args,
    )

    if set_as_default:
        # Best-effort: the combine sink was still created successfully
        # even if setting it as default fails for some reason, so we
        # don't roll back the module load over this.
        try:
            pactl_client.set_default_sink(sink_name)
        except pactl_client.PactlError as exc:
            raise CombinerError(
                f"Combined sink '{sink_name}' was created (module #{module_index}) "
                f"but could not be set as the default: {exc}"
            ) from exc

    return module_index, sink_name


def unload_combined_sink(sink: Sink) -> None:
    """Unload a combined sink's owning module.

    Raises CombinerError if the sink has no known owner module, which
    would mean it wasn't actually created by module-combine-sink.
    """
    if sink.module_index is None:
        raise CombinerError(
            f"'{sink.display_label}' has no owning module index -- it may not "
            "be a combined sink created by this tool, so it won't be unloaded."
        )
    pactl_client.unload_module(sink.module_index)


def apply_latency_offsets(slaves: list[SlaveSpec]) -> list[tuple[SlaveSpec, str]]:
    """Apply per-slave latency offsets to an already-running combined sink.

    Returns a list of (slave, error_message) for any that failed, so
    the caller can report partial failures instead of aborting on the
    first bad sink.
    """
    failures: list[tuple[SlaveSpec, str]] = []
    for slave in slaves:
        try:
            pactl_client.set_sink_latency_offset(slave.sink_name, slave.latency_offset_us)
        except pactl_client.PactlError as exc:
            failures.append((slave, str(exc)))
    return failures
