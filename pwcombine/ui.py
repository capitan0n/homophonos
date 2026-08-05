"""All terminal input/output lives here.

Every function either prints something or reads a line from the user
(or both). None of them call pactl_client or make decisions about
what's "correct" -- that belongs in combiner.py. This split is what
lets combiner.py be tested without a terminal attached.
"""

from __future__ import annotations

from pwcombine.models import Profile, Sink, SlaveSpec


def header(title: str) -> None:
    print()
    print(f"== {title} ==")


def info(message: str) -> None:
    print(message)


def warn(message: str) -> None:
    print(f"[!] {message}")


def error(message: str) -> None:
    print(f"[error] {message}")


def success(message: str) -> None:
    print(f"[ok] {message}")


def print_sink_table(sinks: list[Sink], default_sink_name: str | None = None) -> None:
    if not sinks:
        warn("No sinks found.")
        return

    for i, sink in enumerate(sinks, start=1):
        tags = []
        if sink.name == default_sink_name:
            tags.append("default")
        if sink.is_bluetooth:
            tags.append("bluetooth")
        if sink.is_combined:
            tags.append("combined")
        tag_str = f"  [{', '.join(tags)}]" if tags else ""
        print(f"  {i:>2}. {sink.display_label}{tag_str}")
        print(f"      ({sink.name})")


def prompt(message: str) -> str:
    try:
        return input(f"{message} ").strip()
    except EOFError:
        return ""


def confirm(message: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = prompt(f"{message} {suffix}").lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def select_indices(
    sinks: list[Sink],
    prompt_text: str = "Enter numbers separated by commas (e.g. 1,3)",
) -> list[Sink]:
    """Let the user pick multiple sinks from a numbered list.

    Returns an empty list if input was empty, unparseable, or out of
    range for every entry -- callers should treat that as "no
    selection" and re-prompt or bail out gracefully, not crash.
    """
    raw = prompt(prompt_text + ":")
    if not raw:
        return []

    chosen: list[Sink] = []
    seen_indices: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit():
            warn(f"Ignoring invalid entry: '{token}'")
            continue
        num = int(token)
        if num < 1 or num > len(sinks):
            warn(f"Ignoring out-of-range entry: {num}")
            continue
        if num in seen_indices:
            continue
        seen_indices.add(num)
        chosen.append(sinks[num - 1])

    return chosen


def prompt_latency_offsets(slaves: list[Sink]) -> list[SlaveSpec]:
    """Ask whether to set per-device latency offsets, and collect them.

    Bluetooth sinks typically need a *positive* offset (delay) applied
    to the non-Bluetooth sinks so they wait for BT's extra decode/
    transmit latency -- or a negative offset on the BT sink itself,
    depending on which is easier for the user to reason about. We
    just expose the raw offset per sink and let the user tune by ear.
    """
    header("Latency Tuning (optional)")
    info("If Bluetooth speakers sound out of sync or echo against your")
    info("other speakers, you can add a latency offset in microseconds.")
    info("Positive values delay that sink. Typical Bluetooth offsets: 40000-150000.")
    info("Leave blank to skip / use 0 for a sink.")

    specs: list[SlaveSpec] = []
    if not confirm("Set latency offsets now?", default=False):
        return [SlaveSpec(sink_name=s.name) for s in slaves]

    for sink in slaves:
        raw = prompt(f"  Offset for '{sink.display_label}' (us) [0]:")
        offset = 0
        if raw:
            try:
                offset = int(raw)
            except ValueError:
                warn(f"'{raw}' isn't a whole number, using 0 for this sink.")
                offset = 0
        specs.append(SlaveSpec(sink_name=sink.name, latency_offset_us=offset))

    return specs


def prompt_profile_name(default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    name = prompt(f"Profile name{suffix}:")
    return name or default


def print_profile_list(profiles: list[Profile]) -> None:
    if not profiles:
        warn("No saved profiles yet.")
        return
    for i, profile in enumerate(profiles, start=1):
        slave_names = ", ".join(s.sink_name for s in profile.slaves)
        print(f"  {i:>2}. {profile.name}  ->  [{slave_names}]")


def print_missing_warning(resolved) -> None:
    """resolved: combiner.ResolvedProfile -- kept untyped here to avoid
    a UI -> combiner import (UI should stay a leaf module)."""
    if resolved.missing:
        names = ", ".join(s.sink_name for s in resolved.missing)
        warn(f"These saved sinks are not currently available and will be skipped: {names}")


def main_menu() -> str:
    header("pw-combine")
    print("  1. Create a new combined sink")
    print("  2. Load a saved profile")
    print("  3. Save current selection as a profile")
    print("  4. List active sinks")
    print("  5. Unload a combined sink")
    print("  6. Manage saved profiles (view/delete)")
    print("  0. Exit")
    return prompt("Choose an option:")
