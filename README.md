# pw-combine

An interactive terminal tool for creating and managing PipeWire/PulseAudio
**combined sinks** — so you can play audio out of your internal speakers and
a Bluetooth speaker (or several) at the same time.

Built for Manjaro + KDE Plasma running PipeWire, driven entirely through
`pactl`. Pure Python standard library — no third-party dependencies.

## Why

`module-combine-sink` has always been able to do this, but wiring it up by
hand means:

- Finding each sink's exact internal name (`bluez_output.AA_BB_CC...`)
- Building a `pactl load-module module-combine-sink slaves=...` command by hand
- Remembering to `set-default-sink` afterwards
- Redoing all of this from scratch every time you reconnect your speakers
- Guessing at latency offsets when Bluetooth audio drifts out of sync

`pw-combine` wraps that whole workflow in a menu.

## Requirements

- Python 3.9+
- `pactl` on PATH (comes with `pipewire-pulse` or PulseAudio ≥ 15, both of
  which support `pactl -f json ...`)

No `pip install` needed — everything used is in the standard library.

## Usage

Run straight from a checkout:

```bash
./pw-combine
```

Or install it so `pw-combine` is on your PATH (still zero third-party
dependencies — this only registers the entry point):

```bash
pip install --user .
pw-combine
```

You'll get a menu:

```
== pw-combine ==
  1. Create a new combined sink
  2. Load a saved profile
  3. Save current selection as a profile
  4. List active sinks
  5. Unload a combined sink
  6. Retune latency on the last combined sink
  7. Manage saved profiles (view/delete)
  0. Exit
```

### Typical first run

1. Choose **1** to create a new combined sink.
2. Pick two or more sinks from the numbered list (e.g. `1,2` for your
   internal speakers plus a Bluetooth speaker).
3. Optionally tune per-sink latency offsets if Bluetooth audio sounds out of
   sync (see below).
4. Give it a friendly name — this is what shows up in KDE's audio settings.
5. It's created and set as your system default output immediately.
6. You'll be offered the chance to save this as a named profile for next time.

### Loading a saved profile later

Choose **2**, pick the profile by number. If a saved Bluetooth speaker isn't
currently connected, you'll get a clear warning and the tool will combine
whatever *is* available (as long as at least two sinks remain) rather than
failing outright.

### Cleaning up

Choose **5** to unload a combined sink. This runs `pactl unload-module` on
the module that owns it — the combined sink disappears immediately and
whatever was default before reverts through PipeWire's normal fallback
behavior.

## Fixing Bluetooth echo / desync

Bluetooth speakers add real decode + transmit latency that your internal
speakers don't have, so combining them naively often produces an echo or
a slightly-out-of-sync effect. When creating a combined sink, say yes to
latency tuning and set a positive **microsecond** offset on the sink(s) that
are arriving *early* — usually your wired/internal output — so they wait for
the Bluetooth path to catch up. Common starting points are in the
40,000–150,000 µs range; nudge by ear.

You can also adjust this later without recreating the whole combined sink:
choose menu option **6** to retune the latency of the combined sink you last
created or loaded this session. It shows each slave's current offset as the
default so you can nudge one value and press Enter to leave the rest alone.
Under the hood this just calls `pactl set-sink-latency-offset <sink-name>
<microseconds>` per slave, which you can also run directly if you prefer.

## Where profiles are stored

`~/.config/pw-combine/profiles.json` (or under `$XDG_CONFIG_HOME` if set).
Plain JSON, safe to hand-edit or back up. Each profile stores the slave sink
names and their latency offsets, not the combined sink's runtime state.

## Architecture

The codebase is split so each concern is testable and replaceable on its own:

```
pwcombine/
  models.py        Sink / SlaveSpec / Profile dataclasses. No logic.
  pactl_client.py   Every subprocess call to pactl lives here, isolated.
  config.py         JSON profile persistence (load/save/delete).
  combiner.py       Orchestration: resolves profiles against live sinks,
                    builds combine-sink args, applies latency. No I/O.
  ui.py             All print()/input(). No pactl calls, no decisions.
  cli.py            Wires ui.py + combiner.py + config.py into the menu loop.
tests/
  test_parsing.py   pactl JSON parsing + combiner logic, mocked subprocess.
  test_config.py    Profile save/load/delete round-trips, temp config dir.
  test_cli_e2e.py   Full interactive menu flow, scripted stdin + mocked pactl.
```

`pactl_client.py` is the only module that touches `subprocess`. Everything
above it works against `Sink`/`Profile` objects, which means `combiner.py`
and `ui.py` can be exercised in tests without a real audio server —
`tests/` mocks `subprocess.run` with recorded/synthetic JSON instead.

### Design notes

- **JSON over text scraping**: `pactl -f json list sinks` is parsed with
  `json.loads`, not regexed. Malformed or unexpected entries are skipped
  individually rather than crashing the whole listing.
- **Combined sinks are self-identifying**: created sinks get a
  `combined_` name prefix and are also detected via their driver string
  (`module-combine-sink.c`), so the tool can tell real hardware sinks apart
  from ones it created — this prevents accidentally nesting combined sinks.
- **Missing devices degrade gracefully**: loading a profile where a
  Bluetooth speaker isn't currently paired/connected warns about the
  specific missing sink and proceeds with whatever's left, rather than
  failing the whole operation.
- **Atomic config writes**: profile saves write to a temp file and
  `rename()` over the real one, so an interrupted write can't corrupt
  previously saved profiles.

## Running the tests

```bash
python3 -m unittest discover -s tests -v
```

32 tests, no live PipeWire/pactl required — subprocess calls are mocked
with realistic recorded JSON so the parsing and orchestration logic is
exercised for real.
