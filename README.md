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


## On-demand preference decoder

GET /v1/preferences?nid=255 reads one loaded Sim using --alarm-pid/--alarm-exe. Restart the API after code updates; no additional service or routine scan is needed. Each request reads anew. The sampler verifies the supported executable hash, signatures and roster/identity stability, using read-only memory access. Missing Sims, unsupported builds or failed reads return503 status=unavailable rather than empty preferences. sampledUtc marks completion, not an atomic game tick.

Responses include rawWords (B6/B7/B8/B9/C9/CA), turnOns/turnOffs entries with label/bank/mask/validation, unknownBits, warnings and fullyLiveChecked. live_checked means checked against a user-reported panel in that role on this build; static_candidate means inferred from installed tooltip order and assignment-bank boundaries. Unknown bits and unexpected counts remain explicit. Zero words are not proof of no preferences, especially for children/babies. No age eligibility or romantic suitability is inferred.

The34 candidate names include extra expansion storage. Robots turn-off is live-checked in the third bank; third-bank turn-ons and other unobserved labels remain provisional. Mod overrides are not validated. Mutual chemistry bolts are not implemented by this endpoint. Use occasional purposeful checks under AGENTS.md; hidden attraction scores must not rank partners.

Validation:11 decoder/alarm/HTTP tests passed, covering four reported panels, unknown bits, role-specific validation, invalid requests, and read failures. A direct live read also matched NID255's Underwear/Hard Worker/Robots panel.

## Compact hobby summaries

Sim summaries in /v1/lot, /v1/household and /v1/observe retain hobbyEnthusiasmPoints (solid points0–10) and oneTrueHobby. Redundant hobbyEnthusiasmRaw/hobbyEnthusiasmValue and internal hobbyEnthusiasmProbeRaw/predestinedHobbyProbeRaw are omitted. Native bridge files still retain their probe values for diagnostics. API restart required after this source change. Verified against live household input: Patricia cuisine1, music_and_dance2, oneTrueHobby music_and_dance; omitted fields absent across returned household Sims.

## Mutual chemistry estimates

Relationship pairs in /v1/relationships and /v1/observe now include mutualChemistry: status estimated/unknown, category poor/none/mild/medium/strong (or null), bolts -1/0/1/2/3 (or null), and validation/source or unknown reason. No aggregate numerical score is exposed. Original rawSlots remain available for diagnostics.

Formula traced from installed global BHAV114 and BCON115: truncate directional average toward zero; if either score is strictly below-25 and averaged score is strictly above0, subtract50. Categories use <=-25, <=0, <35, <90 and >=90. Operator meanings cross-checked with https://modthesims.info/wiki.php?title=Operator . One/two/three/poor UI examples match; zero category and exact threshold transitions have not been live-validated. These are cache-derived estimates, not direct UI readings; effective mod overrides, cache recalculation and all attraction eligibility conditions are not verified.

Only fresh supported bridge1.33 pairs are considered. Missing/duplicate directions, suspicious slot layout (including observed shifted slot9 value74), unknown acquaintance/family information, family ties, or missing/incompatible age context return unknown. Unknown is not zero chemistry or proof of romantic eligibility. Known adult/elder pairs and teen/teen pairs can receive estimates, but this does not establish availability, orientation compatibility or absence of commitments. Uncovered visitors may remain unknown. Stale relationship snapshots retain existing behavior of returning no pairs with fresh=false.

14 tests passed, including four observed pair examples, formula edges, missing data, suspicious layout and eligibility guards, plus preference/alarm/HTTP regression. Live relationship_snapshot confirms NID255/Jules27 poor with status estimated. Restart the API to activate this source change. Chemistry is story context, not a mandate or numerical partner ranking.

## Household visits and unavailable data

Live lot/household responses now expose fresh, sourceStatus, sampleAgeSeconds, gamePid, lotSessionId and lotSessionStartedUtc. Stale or between-lot readings return status unavailable, empty Sims and null active family/selection/funds. Relationships retain fresh=false with no pairs when unavailable. Preferences require a fresh, matching game PID and a currently loaded Sim before and after the memory read.

The background watcher observes departures every2 seconds. A confirmed exit, a family change, or a game PID change retires the active visit; re-entering starts a new lotSessionId. Very brief unobserved transitions cannot be guaranteed. Missing/stale samples alone are not proof of departure. Need trends already reset on unavailable data or family changes. /v1/observe filters changes to the active family, process and visit start. /v1/events and /v1/changes remain historical diagnostics; consumers must not replay them as current action requests.

Alarm retention is now per active household visit: confirmed exit/family change discards its in-memory alarms and resets the alarm cursor. Transient read failures within a visit still preserve deduplication. An old cursor reports reset=true. Native logs are not deleted or truncated. Store concise story continuity by neighborhood and family separately, and retire the old visit's action queue/inspection cache when lotSessionId changes. The API does not itself store story notes or confirm saves. Across different neighborhoods the operator must restart with the corresponding name map; family and NID alone are not globally unique.

Live transition test: family3 -> neighborhood -> Greenman family2. Neighborhood returned no Sims/pairs and unknown restriction; Greenman returned only Daisy/Rose/Jason. Stale/process/visit guards are covered by offline tests. Restarting the game requires restarting the API to bind its new PID.
