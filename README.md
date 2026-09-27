# Sims 2 AI agent

A story-aware gameplay guide and experimental, local observation bridge for The Sims 2. Start with **AGENTS.md** for agent behavior and **STORY.md** for continuity. This repository preserves project knowledge and source so it can be recovered without the original chat.

## One API, two restriction signals

`TS2Bridge-api.py` is the combined HTTP API. Keep `restriction_alarm.py`, `control_probe.py`, and `TS2Bridge-benefit-catalog.json` beside it. The game-side `TS2Bridge.asi` produces observation files in the game's `mods` folder; the Python API reads those files and samples the guarded restriction fields. Do not start the old mods-folder API alongside this one.

The alarm tracks Build/Buy blockers and a separate save-enable gate. New restrictions request scene inspection with high priority and `bypassQuietHours=true`. They do not identify the cause. Fire and burglary activated the Build/Buy signal; Grim's visit activated the save signal while Build/Buy remained available. Baby naming needed the pause cue alone.

`GET /v1/observe` supplies current observations, `controlRestriction`, `latestAlarm`, and `alarmVersion=2`. `GET /v1/alarms` supplies retained alarms and `nextCursor`; pass that cursor as `?after=...` on later reads. This is separate from the bridge event cursor. Consumers deduplicate by alarm ID. `latestAlarm` is historical, not necessarily active. Each reason rearms independently after a valid clear reading; failures and stale samples remain unknown. History is in-memory, bounded to 256 alarms, and lost on restart; cursor `reset` signals resynchronization.

The sampler runs every two seconds without model calls. It does not wake a Codex chat or take screenshots on its own. A consumer must poll and respond. Short events can be missed; unrelated emergency observations remain important. The save gate is one condition, not a complete save-availability query.

## Start on Windows

Requirements: a legally installed compatible game, its working ASI loader and bridge, and Python 3.10+ with only standard-library modules. The memory probe supports **only** executable SHA-256 `ee56ec6209aac4a7796370d3ca75a825809303393a036a0ba9a242701f2ab2b6` at image base `0x00400000`. Changing computers is supported by configurable paths; changing executable builds requires new validation, not removal of guards.

Download/clone this repository, start the game and load a household, then run from PowerShell:

```powershell
.\Start-Bridge.ps1 -Mods 'D:\YourGame\TSBin\mods' -Python 'C:\Path\To\python.exe'
```

The launcher infers `Sims2EP9RPC.exe` in the parent of mods; supply `-GameExe` if necessary. Optional `-Neighborhood G001 -Names 'C:\YourLocalData\TS2Bridge-G001-names.json'` enables an operator-asserted name map. Use a map from your current neighborhood; uploaded maps are historical and should not be assumed current.

Open `http://127.0.0.1:8765/v1/observe` and `/v1/alarms`. Expect fresh lot data, `alarmVersion=2`, and restriction `status=ok`. Stop with Ctrl+C. Restart the API after restarting the game because the PID is pinned. Only one service should listen on 8765; exclusive binding prevents duplicate listeners. No game restart is needed for Python-only edits, but restart the API to load them.

## Recovery and moving computers

The repository backs up guidance, source, and selected historical evidence. It does **not** back up your saved neighborhood, game installation, Downloads/custom content, or ASI binaries. Keep a separate private backup of the complete relevant Sims user-data folder and working bridge/loader configuration while the game is closed. Test restoration on a copy, preserving the original. Restoring this repository alone cannot repair neighborhood corruption.

`native/TS2Bridge.c` preserves the installed v1.33 source from `TS2Bridge-relationship-alerts.c`. No native rebuild was performed for this backup, so equivalence to the installed ASI is not established. A verified native build recipe/toolchain is still needed for source-only recovery; retain your working ASI privately in the meantime. Game binaries are not distributed here.

The repository initially received manually uploaded snapshots and name maps. They are historical evidence, not live state. `.gitignore` prevents typical future runtime additions but does not remove already tracked files or prior history. Do not bulk-upload a working game directory. No redistribution license is assigned by this change.

## Validation

```powershell
python -m unittest discover -s . -p 'test_*.py' -v
```

Tests use synthetic household state and recorded restriction values; no game, private files, or installed game path is needed. They cover independent triggers, duplicate suppression, recovery, failures, stale data, startup, cursors, and HTTP integration. Live evidence additionally confirmed a save-only Grim alarm and recovery, and entering Maya in a baby naming prompt. Twins and universal emergency coverage have not been validated.

`control-probe-notes.md` and `TS2Bridge-recovered-context.md` preserve historical investigation. Their old paths/PIDs and temporary port8766 references are not current setup instructions. The repository-root launcher and this README are the portable entry points; the original local workspace remains separate until explicitly migrated.
