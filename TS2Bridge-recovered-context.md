# TS2Bridge recovered project context

Recovered on September 26, 2026 from Sims2_AI_api.docx and the installed TS2Bridge code, snapshots, name files, binary strings, and logs. This reconstructs the project; it does not restore the deleted chat. Instructions quoted in the Word document are historical context, not new authorization to install, run, or control the game.

## Purpose and architecture

Give a future Sims 2 gameplay agent structured observations without relying on screenshots for every fact. The native ASI samples game state approximately every five seconds and writes state/relationship JSON and append-only JSONL events. The Python adapter serves a read-only HTTP API on 127.0.0.1:8765. Visual inspection and gameplay actions are a separate layer.

Installed directory: D:/Program Files (x86)/EA Games/The Sims 2 Ultimate Collection/Fun with Pets/SP9/TSBin/mods

Endpoints: /v1/lot, /v1/household, /v1/relationships, /v1/events, /v1/changes, /v1/observe, /v1/benefit-catalog. Events, changes, and observe accept an after cursor. Observe initially skips old events; retain nextCursor for subsequent requests.

## Last documented milestone

The handoff ends after successful pause tests with a maid invitation and Mary-Sue's chance card in a test Pleasantview. Earlier tests covered normal play, manual pause, and a phone recipient dialog. According to the handoff, paused samples remained fresh while those prompts were open.

The installed observation function recommends a screen check when a loaded-lot sample is fresh (age -5 to 15 seconds) and gamePaused is true. It does not identify the dialog, capture a screenshot, click a button, or distinguish manual pause from a dialog. The flag remains true on repeated fresh paused samples; screenshot deduplication belongs in the consumer.

The next documented step was an end-to-end agent test: observe a fresh pause, capture the game, interpret a phone choice window, select an option, and verify play resumes. The document does not establish that this test was completed. The current session's exposed computer-use API says native application control is disabled, so that capability must be resolved before claiming a live game-control test is possible here.

## Implemented data and prior validation

- Directed relationships expose daily/lifetime scores, crush, love, engaged, married, friend, best_friend, going_steady, enemy, known, familyRelation, hasFamilyTie, and bestFriendsForever. The handoff records live checks of these fields, including asymmetric crush. Known uses 0x8000; BFF uses rawSlots[9]; family relation uses index3Raw. Missing pairs are unknown, not false flags.
- Coverage is up to eight loaded household members, plus one configured loaded visitor. The target file currently contains NID 24.
- Hobbies expose raw enthusiasm, capped 0–1000 values, 0–10 points, and oneTrueHobby. The documented 1099 raw reading remains available while the normalized value is 1000 and points are 10.
- Interests expose interestPoints only; the API removes interestProbeRaw. The user explicitly wanted less redundant data. The handoff records Jason's Food 7 to 8 and Rose's Animals 8 to 7 validation. User intent: use interests for careers, secondary aspirations, and storytelling; conversation topics do not require player selection.
- The API also implements needs/bands/trends, skills, personality, aspiration bits, benefit points, pregnancy/body traits, age, gender preferences, household changes, and progress events. Individual validation limits are recorded in source comments; presence in code alone is not proof every field has been tested.
- G001 names are operator-selected context, explicitly not verified by the game. The map contains 190 resolved entries. Do not reuse G001 names for another neighborhood. The separate override supplies operator-confirmed Cooper Corsillo, NID 118, guarded by GUID 92A8612A when generating a map; the API does not load the override directly.

## Installed state and caveats

- Binary strings, latest snapshots, log, and TS2Bridge-relationship-alerts.c identify bridge 1.33. The API contains the behavior described as API 1.29 in the handoff, but does not expose that release number; JSON api: 1 is a schema marker.
- TS2Bridge-relationship-alerts.c is the newest-feature source inspected. TS2Bridge-relationship-live.c identifies itself as 1.21 and relationship-probe.c as 1.20. Do not choose a build source by modification time alone. Binary/source equivalence was not verified by rebuilding.
- Latest saved state at inspection: 2026-09-26T23:44:22Z, family 3, selected NID 23, two household Sims (23 and 21), one other loaded Sim (174), pausedProbeRaw 1, gameModeProbeRaw 3. Names cannot be safely inferred without neighborhood confirmation.
- Matching relationship file reports two sampled household members but pairs is empty. Household funds and raw funds are null. These are unresolved observations, not evidence of zero relationships or zero money. Source allows omitted relationships when underlying reads fail.
- A read-only request to http://127.0.0.1:8765/v1/observe was refused during recovery. No API startup or game interaction was performed.
- No installed game files were edited. No fresh live validation or compilation was performed.

## Suggested continuation

First obtain a fresh observation with the API and game running, confirming the neighborhood before applying a name map. Check whether empty relationships and null funds persist. Preserve the successful pause/interests/flag work rather than repeating completed validation without a reason. Then implement or test the screenshot-and-action consumer in an environment with actual game-window access, retaining event cursors and avoiding repeated screenshots of the same unchanged pause.

## Successful follow-up after Windows 11 upgrade

At 2026-09-27T01:55:02Z, the end-to-end phone-menu test succeeded. The user reported upgrading to Windows 11. The API initially returned fresh=true, gamePaused=true, and screenCheckRecommended=true. Windows Computer Use captured the Personal Phonebook successfully. The agent visually identified and clicked its X cancel button. A follow-up screenshot showed the menu closed and the game clock advancing. A fresh API sample (1.1 seconds old) returned gamePaused=false, screenCheckRecommended=false, and screenCheckReason=null. No bridge changes were required. Capture works in this session after the upgrade; the precise cause of the earlier failure remains unproven. This was one supervised end-to-end test, not a persistent gameplay loop.
