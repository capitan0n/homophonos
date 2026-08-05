"""Interactive entrypoint. Wires ui.py (I/O) to combiner.py (logic)
and config.py (persistence). pactl_client is only touched indirectly,
through combiner, except for the simple read-only listing calls.
"""

from __future__ import annotations

import sys

from pwcombine import combiner, config, pactl_client, ui
from pwcombine.models import Profile, SlaveSpec


def _load_live_sinks_or_die():
    """Fetch current sinks, printing a clear error and exiting on failure.

    Centralized here because nearly every menu action needs a fresh
    sink list, and we want identical error handling everywhere it's
    called rather than re-catching PactlError at each call site.
    """
    try:
        return pactl_client.list_sinks()
    except pactl_client.PactlError as exc:
        ui.error(str(exc))
        sys.exit(1)


def action_create_new(remember_last: dict) -> None:
    ui.header("Create a Combined Sink")
    live_sinks = _load_live_sinks_or_die()
    real_sinks = combiner.get_real_sinks(live_sinks)

    if len(real_sinks) < 2:
        ui.warn("Fewer than two non-combined sinks are currently available.")
        ui.warn("Connect another output device (e.g. power on a Bluetooth speaker) and try again.")
        return

    default_name = pactl_client.get_default_sink_name()
    ui.print_sink_table(real_sinks, default_sink_name=default_name)

    chosen = ui.select_indices(real_sinks, "Select sinks to combine (e.g. 1,2)")
    if len(chosen) < 2:
        ui.warn("Need at least two sinks selected -- nothing was created.")
        return

    slaves = ui.prompt_latency_offsets(chosen)

    display_name = ui.prompt("Name for this combined sink (shown in KDE) [Combined Output]:")
    display_name = display_name or "Combined Output"

    try:
        module_index, sink_name = combiner.create_combined_sink(display_name, slaves)
    except combiner.CombinerError as exc:
        ui.error(str(exc))
        return
    except pactl_client.PactlError as exc:
        ui.error(f"Failed to create combined sink: {exc}")
        return

    ui.success(f"Created '{display_name}' ({sink_name}, module #{module_index}) and set as default.")

    remember_last["slaves"] = slaves
    remember_last["combined_sink_name"] = sink_name

    if ui.confirm("Save this combination as a profile for next time?", default=True):
        action_save_profile(remember_last)


def action_load_profile(remember_last: dict) -> None:
    ui.header("Load a Saved Profile")
    profiles = config.load_profiles()
    profile_list = list(profiles.values())

    if not profile_list:
        ui.warn("No saved profiles yet. Create a combined sink first, then save it.")
        return

    ui.print_profile_list(profile_list)
    choice = ui.prompt("Enter a profile number to load:")
    if not choice.isdigit() or not (1 <= int(choice) <= len(profile_list)):
        ui.warn("No valid profile selected.")
        return

    profile = profile_list[int(choice) - 1]
    live_sinks = _load_live_sinks_or_die()
    resolved = combiner.resolve_profile(profile, live_sinks)

    ui.print_missing_warning(resolved)

    if not resolved.can_create:
        ui.error(
            f"None of the sinks saved in '{profile.name}' are currently connected. "
            "Nothing to combine."
        )
        return

    if len(resolved.available) < 2:
        only = resolved.available[0].sink_name
        ui.warn(
            f"Only one sink from '{profile.name}' is currently available ({only}). "
            "A combined sink needs at least two, so nothing was created."
        )
        return

    try:
        module_index, sink_name = combiner.create_combined_sink(
            profile.name, resolved.available
        )
    except (combiner.CombinerError, pactl_client.PactlError) as exc:
        ui.error(f"Failed to load profile: {exc}")
        return

    ui.success(f"Loaded '{profile.name}' as {sink_name} (module #{module_index}) and set as default.")
    remember_last["slaves"] = resolved.available
    remember_last["combined_sink_name"] = sink_name


def action_save_profile(remember_last: dict) -> None:
    ui.header("Save Profile")
    slaves: list[SlaveSpec] | None = remember_last.get("slaves")

    if not slaves:
        ui.warn("Nothing to save yet -- create a combined sink first (option 1).")
        return

    default_name = remember_last.get("combined_sink_name", "").replace(
        pactl_client.COMBINED_SINK_PREFIX, ""
    ) or "my_profile"
    name = ui.prompt_profile_name(default=default_name)
    if not name:
        ui.warn("Profile name cannot be empty -- not saved.")
        return

    profile = Profile(
        name=name,
        slaves=slaves,
        combined_sink_name=remember_last.get("combined_sink_name", ""),
    )
    try:
        config.save_profile(profile)
    except config.ConfigError as exc:
        ui.error(str(exc))
        return

    ui.success(f"Saved profile '{name}'.")


def action_list_sinks() -> None:
    ui.header("Active Sinks")
    live_sinks = _load_live_sinks_or_die()
    default_name = pactl_client.get_default_sink_name()
    ui.print_sink_table(live_sinks, default_sink_name=default_name)


def action_unload_combined(remember_last: dict) -> None:
    ui.header("Unload a Combined Sink")
    live_sinks = _load_live_sinks_or_die()
    combined = combiner.find_existing_combined_sinks(live_sinks)

    if not combined:
        ui.warn("No combined sinks are currently loaded.")
        return

    ui.print_sink_table(combined)
    choice = ui.prompt("Enter a number to unload:")
    if not choice.isdigit() or not (1 <= int(choice) <= len(combined)):
        ui.warn("No valid sink selected.")
        return

    target = combined[int(choice) - 1]
    try:
        combiner.unload_combined_sink(target)
    except (combiner.CombinerError, pactl_client.PactlError) as exc:
        ui.error(str(exc))
        return

    ui.success(f"Unloaded '{target.display_label}'.")
    if remember_last.get("combined_sink_name") == target.name:
        remember_last.clear()


def action_manage_profiles() -> None:
    ui.header("Saved Profiles")
    profiles = config.load_profiles()
    profile_list = list(profiles.values())
    ui.print_profile_list(profile_list)

    if not profile_list:
        return

    if ui.confirm("Delete a profile?", default=False):
        choice = ui.prompt("Enter a number to delete:")
        if choice.isdigit() and 1 <= int(choice) <= len(profile_list):
            target = profile_list[int(choice) - 1]
            config.delete_profile(target.name)
            ui.success(f"Deleted profile '{target.name}'.")
        else:
            ui.warn("No valid profile selected.")


def run() -> int:
    if not pactl_client.is_available():
        ui.error(
            "'pactl' was not found. Install it via your PipeWire/PulseAudio "
            "utilities package (e.g. 'pipewire-pulse' on Manjaro) and ensure "
            "it's on your PATH."
        )
        return 1

    # Tracks the most recently created/loaded combination in-memory so
    # "save as profile" (option 3) has something to work with without
    # forcing the user to reselect sinks immediately after creation.
    remember_last: dict = {}

    actions = {
        "1": lambda: action_create_new(remember_last),
        "2": lambda: action_load_profile(remember_last),
        "3": lambda: action_save_profile(remember_last),
        "4": action_list_sinks,
        "5": lambda: action_unload_combined(remember_last),
        "6": action_manage_profiles,
    }

    while True:
        choice = ui.main_menu()
        if choice == "0" or choice.lower() in ("q", "quit", "exit"):
            ui.info("Goodbye.")
            return 0

        action = actions.get(choice)
        if action is None:
            ui.warn("Not a valid option.")
            continue

        try:
            action()
        except KeyboardInterrupt:
            print()
            ui.warn("Interrupted.")
        except pactl_client.PactlError as exc:
            # Belt-and-suspenders: individual actions already catch
            # their own PactlErrors, but this guards against any path
            # that doesn't, so one bad pactl call never crashes the
            # whole interactive session.
            ui.error(str(exc))


def main() -> None:
    try:
        sys.exit(run())
    except KeyboardInterrupt:
        print()
        sys.exit(130)


if __name__ == "__main__":
    main()
