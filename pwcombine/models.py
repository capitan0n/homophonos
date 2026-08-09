"""Plain data models shared across pw-combine.

Kept dependency-free (stdlib dataclasses only) so this module can be
imported by parsing, UI, and orchestration code without creating
import cycles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Sink:
    """A single PipeWire/PulseAudio sink, as reported by pactl.

    `name` is the stable identifier (e.g. "bluez_output.AA_BB_CC.1")
    used on the command line; `description` is the human-readable
    label shown in the UI (e.g. "WH-1000XM4").
    """

    index: int
    name: str
    description: str
    driver: str = ""
    is_combined: bool = False  # True if this sink was made by module-combine-sink
    module_index: Optional[int] = None  # owning module, if it's a combine sink

    @property
    def display_label(self) -> str:
        """Short label for menus: description falls back to name."""
        return self.description.strip() or self.name

    @property
    def is_bluetooth(self) -> bool:
        return self.name.startswith("bluez_")


@dataclass
class SlaveSpec:
    """One slave sink inside a combined-sink profile, plus its tuning."""

    sink_name: str
    # Per-sink latency offset, in microseconds. Positive values delay a
    # sink relative to the others -- useful for compensating Bluetooth's
    # extra decode/transmit latency.
    #
    # NOTE: on PipeWire this is applied via `pactl set-sink-latency-offset`
    # AFTER the combined sink exists, not as an inline `sinks=name:offset`
    # argument (which PipeWire's module-combine-sink does not support).
    latency_offset_us: int = 0


@dataclass
class Profile:
    """A saved, named combination of slave sinks."""

    name: str
    slaves: list[SlaveSpec] = field(default_factory=list)
    combined_sink_name: str = ""  # e.g. "combined_pc_plus_bt"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "combined_sink_name": self.combined_sink_name,
            "slaves": [
                {"sink_name": s.sink_name, "latency_offset_us": s.latency_offset_us}
                for s in self.slaves
            ],
        }

    @staticmethod
    def from_dict(data: dict) -> "Profile":
        slaves = [
            SlaveSpec(
                sink_name=s["sink_name"],
                latency_offset_us=int(s.get("latency_offset_us", 0)),
            )
            for s in data.get("slaves", [])
        ]
        return Profile(
            name=data["name"],
            slaves=slaves,
            combined_sink_name=data.get("combined_sink_name", ""),
        )
