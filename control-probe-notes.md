# Control availability candidate probe

The first probe is ready as TS2Bridge-control-probe.py. It uses only Python's standard library and reads the running game's memory through a handle with PROCESS_VM_READ and PROCESS_QUERY_LIMITED_INFORMATION. It does not call game functions, inject code, change game memory, or modify saves. No ASI or API replacement is needed.

## Evidence and scope

Supported executable SHA-256: ee56ec6209aac4a7796370d3ca75a825809303393a036a0ba9a242701f2ab2b6. Expected image base: 0x00400000.

Static inspection of this exact executable found the nGameState registration for GetSaveLotEnabled: string VA 0x0122EB50 paired with wrapper 0x008FC5C0. That wrapper obtains the game-state object through 0x00799B65 and invokes vtable offset 0x64. The cached object pointer is stored at 0x014890C0. The observed vtable is 0x012AB328; slot 0x64 points to 0x00EE9FE2.

The save getter checks signed object field +0x84 > 0, but also evaluates other virtual methods and conditions involving bytes +0x68 and +0x69. Therefore +0x84 is a candidate gate, NOT the full save-availability result. The adjacent GetTransitionsEnabled wrapper invokes vtable offset 0x60; its method at 0x00EE9FD6 returns whether signed field +0x80 is greater than zero. The relationship of transitions to Build/Buy buttons still needs testing.

The probe reads just bytes +0x68/+0x69 and signed 32-bit fields +0x80/+0x84. It checks the PID's executable path, executable hash, expected vtable, method pointers, and getter byte signatures. It performs matching repeated narrow reads and checks the object pointer remains stable. This reduces races but is not an atomic snapshot. It fails closed on mismatches or inaccessible memory.

Fields buildModeAvailable, buyModeAvailable, and saveAvailable deliberately remain null. The separately fetched API context may be older than the memory sample. This is an experimental manual sampler, not a continuous watcher or emergency alarm.

The SimsWiki global-variable reference describes current game mode, not button availability: https://modthesims.info/wiki.php?title=Sims_2_Global . The offsets above were derived from the user's executable, not inferred from that table.

## Test sequence

Use a disposable test household. Capture the same household in four conditions, recording the visible Build/Buy and save-button states alongside each sample:

1. Ordinary Live mode without a special event.
2. Manual pause, without a special event.
3. One user-triggered event while Build/Buy/save are disabled. Pause after the restrictions take effect and identify the event.
4. After the event ends and normal controls return.

Optional follow-up: ordinary phone dialog, to distinguish modal restrictions from special events. Test separate event types later; one success is not universal emergency coverage.

Keep the API running. Tell the assistant when each condition is ready; the assistant can obtain the PID, run this sampler, and inspect the visible UI. No manual JSON export is required. Do not trigger an event until baseline states have been recorded.

## Running manually

Use a 64-bit Python on Windows. Obtain the current Sims2EP9RPC PID, then run:

    python TS2Bridge-control-probe.py --pid PID --label normal-live --output normal-live.json

Use different output filenames for each sample: the script refuses to overwrite evidence. If the API is unavailable, candidate readings are still returned with an API error. The default executable path matches the installed game. The PID must be refreshed after a restart.

## Verification completed

- Static disassembly identified registration, cache pointer, getter dispatch, and the relevant field reads.
- Read-only live access succeeded without changing the installed bridge.
- The complete guarded sampler succeeded and wrote control-probe-initial.json: bytes 0/0 and counters 1/1, API fresh and paused. This sample is labeled initial-unclassified because its UI state was not inspected.
- Initial module enumeration was denied by the environment; the delivered script instead verifies the executable using QueryFullProcessImageNameW and guarded image reads.
- Event correlation, manual-pause comparison, and recovery comparison remain to be performed.

## First fire comparison

Normal Live mode (control-probe-normal-live.json), manual pause without an event (control-probe-manual-pause.json), and an active manually paused kitchen fire (control-probe-fire-paused.json) all returned offset68Byte=0, offset69Byte=0, offset80Signed=1, offset84Signed=1. All API context samples were fresh. The fire screenshot visibly showed flames and grey Build/Buy buttons; saving being unavailable was reported by the user, not independently inspected in Options. The game was left paused and no emergency action was taken.

Result: these four fields do NOT distinguish this fire from normal play. Do not use them as an alarm. Further investigation must trace other conditions in the save getter or the actual Build/Buy enable checks. The existing getter analysis already showed +0x84 is only one gate, not the complete availability result. A successful capture of unchanged values is a negative candidate result, not a successful event detector.

## Second probe: dedicated Build/Buy restriction list

TS2Bridge-control-probe-v2.py preserves the first readings and adds a guarded read of the object manager's Build/Buy restriction vector. control-probe-v2-fire-paused.json recorded buildBuyBlockerCountRaw=1 and buildBuyRestrictionActiveCandidate=true. API context was fresh, manually paused, Monday 19:37, family 3. No game control, game-memory write, installation change, or save action occurred.

Static trace for this exact executable:

- Generic SimAntics dispatcher 0x94C3E0 uses switch table 0x957C48. Operand 0x0A (case 0x94D3C5) obtains the object's manager through virtual slot +0xFC, then calls manager slot +0x10C. Operand 0x0B (case 0x94D3FF) calls manager slot +0x110.
- Root cache 0x14890A0 points to vtable 0x12AB0B8. Slot +0x68 is accessor 0x4A4835, returning root+0x78, the manager pointer. Manager vtable is 0x121D138.
- Manager +0x10C -> 0x7CE130 adds a blocker; +0x110 -> 0x7C7AC0 removes one. Vector metadata is at manager+0x107C (begin/end/capacity).
- Manager +0x114 -> 0x7D2FD0 returns whether begin != end. In the fire sample there was one four-byte entry.
- v2 validates root and manager vtables, accessor and getter signatures, method pointers, vector bounds/alignment, and repeated metadata/pointer stability. It does not dereference blocker entries. Reads are not atomic.

This is a substantially stronger candidate than the unchanged high-level game-state counters. It is not yet an empirically validated event detector: the old normal and manual-pause samples did not include this list. Next capture must be after the user ends the fire and confirms Build/Buy return, preferably manually paused to hold pause state constant. Expected candidate result is count zero. Then test ordinary Live mode and a phone dialog; other event types require their own tests. A positive count indicates a restriction, not the identity or severity of an emergency. Public availability fields remain null.

Saving trace, unfinished: generic operand 0x28 at 0x951FB9 posts message 0xCD91ECE9 with Temp0, followed by message 0xED9217DE. Handler branch 0xEF6CDC modifies [esi+0x7C]; its exact interface-to-object offset has not been resolved. Do not assume this is nGameState+0x7C or expose it without further tracing. This trace is retained for later investigation; current v2 does not sample it.

## Fire recovery comparison

User reported the fire out and game manually paused. At 2026-09-27T02:47:23.999490Z, control-probe-v2-fire-out-paused.json recorded buildBuyBlockerCountRaw=0 and buildBuyRestrictionActiveCandidate=false, versus count=1 during the paused fire. PID remained 22480, family remained 3, and fresh API context confirmed gamePaused=true at 20:18. The four original game-state fields stayed 0/0/1/1.

This paired observation supports the restriction-list candidate for this fire and shows manual pause alone does not keep it active. Build/Buy and save availability were not visually verified during this recovery sample; fire resolution and pause were user-reported, with pause also confirmed by API. No game input or memory writes were performed. The result does not establish coverage of other emergencies or rule out non-emergency blockers.

Recommended next control test: ordinary phone dialog, to check whether it also adds a blocker. A future watcher should trigger a screenshot once when the count changes from zero to positive, inspect the scene to classify the cause, and avoid repeated alerts while the same restriction persists. This is a proposed policy only; no watcher or API integration has been installed.

## Phone-menu control comparison

At 2026-09-27T02:55:30.812667Z, control-probe-v2-phone-menu-paused.json recorded buildBuyBlockerCountRaw=0 and buildBuyRestrictionActiveCandidate=false. The user reported the phone menu open and game paused; fresh API context independently confirmed gamePaused=true, family 3, at 20:22. PID remained 22480. The original fields remained 0/0/1/1. No screenshot was taken for this sample; menu presence is operator-reported. No game input or game-memory writes occurred.

Comparison: paused fire=1; paused after fire=0; paused phone menu=0. This supports distinguishing the tested fire restriction from the tested phone dialog. It does not establish all-dialog or all-emergency coverage. Keep separate watcher reasons: a restriction transition can request scene inspection; a pause/menu condition may request interaction independently. No watcher has been installed. The phone menu was left untouched.

## Burglar event comparison

At 2026-09-27T02:59:36.038844Z, control-probe-v2-burglar-paused.json recorded buildBuyBlockerCountRaw=1 and buildBuyRestrictionActiveCandidate=true. The user reported a forced burglar, paused game, grey Build/Buy controls, and unavailable saving. Fresh API context confirmed gamePaused=true, family 3, at 01:52; PID remained 22480. The original candidate fields remained 0/0/1/1. Event identity and control appearance are operator-reported, not independently captured in this sample. The probe performed no game input or game-memory writes.

The tested fire and burglar both produced one blocker, while fire recovery and the phone menu produced zero. This supports a common restriction signal suitable for requesting scene inspection, including during overnight quiet hours. It does not identify the event or prove general emergency coverage, and it does not directly expose save availability. A burglary recovery sample is still needed: after the event ends and controls return, manually pause again and sample to verify the count returns to zero. No watcher or installed bridge/API changes have been made.

## Burglary recovery comparison

At 2026-09-27T03:01:02.608032Z, control-probe-v2-burglar-ended-paused.json recorded buildBuyBlockerCountRaw=0 and buildBuyRestrictionActiveCandidate=false, down from one during the burglary. User reported the burglar apprehended, game paused, and normal appearance restored. Fresh API context confirmed gamePaused=true, family 3, at 03:24; PID remained 22480. Original candidate fields stayed 0/0/1/1. Normal control appearance is operator-reported; no screenshot or direct save-availability check was performed. The probe made no game input or memory writes.

Both tested events now have paired active/recovery evidence: fire 1 -> 0 and burglary 1 -> 0, with pause held true in all samples. The tested phone menu remained zero. This supports an experimental restriction-transition trigger: on zero -> positive, request scene inspection even during quiet hours; suppress duplicate wakeups for the same continuing restriction; rearm after returning to zero. This is not an event classifier, universal emergency detector, or proof of save availability. Other event types and simultaneous blockers remain untested. No watcher or bridge/API integration is installed. Next implementation step can expose the guarded count and transition reason without treating unknown or failed reads as zero.

## Alarm API implemented and running

The external Python implementation is in outputs/alarm-api. The updated adapter runs on 127.0.0.1:8766; the installed API on 8765 and ASI are unchanged. Start-AlarmApi.ps1 selects the single running game PID and launches the updated API. It must be relaunched after a game restart. The background sampler runs every two seconds independently of HTTP clients, stores up to 256 episode alarms in memory, and exposes /v1/alarms with a separate per-client cursor. /v1/observe includes the current restriction and latest alarm. Consumers deduplicate by ID and bypass quiet hours for new alarms; no Codex wakeup or screenshot automation was created.

Six unit/HTTP integration tests passed, including synthetic event replay, deduplication, rearming, failures, startup during a restriction, household changes, stale data and cursor retention/reset. Live verification returned status=ok, blockerCount=0, alarmEnabled=true, and fresh household data. The verification is saved in outputs/alarm-api/live-alarm-verification.json. No new in-game emergency was triggered to test the live worker. Next user-triggered event can validate background detection end-to-end. See the package README for restart, API consumption and limitations.

## Grim Reaper / save-only restriction (2026-09-27)

User reported a dying Sim with Grim Reaper present, saving disabled, Build/Buy accessible, and game paused. The game had restarted: current PID 20052, previously 22480. Guarded sample control-probe-v2-grim-paused.json at 03:15:31Z returned Build/Buy blocker count 0, nGameState+0x80=0, +0x84=0, bytes +0x68/+0x69=0. API fresh, paused, family 3, mode raw 3, 16:06. UI state is operator-reported, not screenshot-verified here.

Static recheck of GetSaveLotEnabled at 0xEE9FE2 shows the signed +0x84 <= 0 branch at 0xEEA030/0xEEA036 necessarily returns false. A positive counter alone does not prove saving is available, because other conditions remain. This save gate may complement the Build/Buy list: prior fire/burglary samples had counters 1/1 but list count 1; the death sample has counters 0/0 and list count 0. Require recovery to positive after the event before integrating the additional gate. Also compare ordinary modes/dialogs to characterize false positives. Existing Build/Buy-only alarm cannot cover this death scenario.

Independent bridge log evidence: at 03:13:51Z NID23 changed ghostFlagsRaw from 0 to 1 with matchesCurrentFamily and wasHouseholdMemberThisLot true. The current API maps this event to household_death_indicated when consumed using the bridge event cursor. Hunger urgent was logged earlier. This death indication should remain an independent inspection reason.

Operational issue: port8766 alarm worker remained pinned to PID22480 after game restart; it reports unknown (WinError87), not a false zero. A guarded attempt to stop its verified launcher PID8896/process tree was denied by Windows. A replacement launch attempted the same port and could not take over. No new successful alarm deployment or save-gate integration occurred during this inspection. Manual restart of the original alarm service remains required. Current game was not controlled, resumed, or written to.

## Grim recovery and service reset retry

At 2026-09-27T03:19:29.765343Z, control-probe-v2-grim-ended-paused.json recorded save counter +0x84=1 and transition counter +0x80=1, both recovered from zero during Grim's presence. Build/Buy blockers remained zero. Fresh API confirms paused, family 3, game mode raw 0, 16:49, PID20052. User reported Grim departed; restored Save UI was not independently verified. This paired sample supports a separate save-gate restriction detector, pending integration; no claim that all reasons saving is disabled are covered.

User requested a service reset retry after closing its tab. Verified original launcher PID8896, powershell, start time 2026-09-26 20:05:02. Retried taskkill /PID 8896 /T /F; Windows again returned Access denied. Port8766 still reports unknown against the old PID. Browser-tab closure did not reset the server. No successful reset or additional launch was performed in this turn. User must stop the original service process tree and relaunch Start-AlarmApi.ps1 to reconnect it. Game untouched.

## Save alarm integrated into source

Updated outputs/alarm-api/restriction_alarm.py to independently track Build/Buy and save-gate restrictions, preserving unknown readings and emitting new reason transitions without repeat alarms. Updated /v1/observe and /v1/alarms with alarmVersion=2 and save-gate fields. Eight tests passed, including recorded Grim samples and overlapping reasons. Live one-shot verification saved to outputs/alarm-api/live-save-alarm-verification.json. Running port8766 still has the old code until user restarts their launcher; previous process-management attempts were denied. No game changes performed.

## Restart verified, duplicate listener resolved

At 03:27Z, netstat showed two Python listeners on 127.0.0.1:8766: PID8544 (started20:16:06) and PID5364 (started20:26:54). Responses initially came from the old alarm session. Verified and stopped only old PID8544 successfully using Stop-Process. The remaining listener PID5364 reports alarmVersion=2, status=ok, blockerCount=0, saveEnableCounterRaw=1, saveGateRestricted=false. Evidence saved in alarm-api/restarted-v2-verification.json. No user restart is needed for save-trigger activation.

Added ExclusiveHTTPServer (SO_EXCLUSIVEADDRUSE on Windows; no address reuse) in source to prevent later duplicate binds. This additional binding safeguard takes effect on the next service restart; current PID5364 already has version2 detection but predates the binding change. Existing eight tests still pass. Game untouched.

## Single-service consolidation prepared

The updated outputs/alarm-api/TS2Bridge-api.py contains the entire original API plus the background alarm. Changed Start-AlarmApi.ps1 default to port8765 and README to identify it as the main service. The mods API remains an unmodified fallback; the ASI and data files remain required there.

Migration attempt verified listeners: PID18940 Python313 on8765, started20:10:57; PID5364 bundled Python on8766, started20:26:54. Stop-Process for PID18940 returned Access denied; execution stopped before touching PID5364 or starting another server. Both existing services therefore remain as before. User must stop old port8765 service and current port8766 service, then launch the updated launcher once. Until then the active combined API remains on8766. No game process or files in mods were changed.

## Consolidation verified active

At 2026-09-27T03:32:43Z, /v1/alarms and /v1/observe on port8765 both reported alarmVersion=2. Observation fresh=true, alarmEnabled=true; controlRestriction status=ok, blockerCount=0, saveEnableCounterRaw=1, saveGateRestricted=false. netstat showed one listener on8765 (PID12976) and none on8766. Single combined service is now active. Saved combined-service-verification.json. Main launcher remains outputs/alarm-api/Start-AlarmApi.ps1; mods API is fallback only.

## Live save-only alarm test passed (active phase)

User reported Grim present and game paused. Running combined API on8765 automatically recorded save_gate_restriction_detected at 2026-09-27T03:34:27.905286Z, session6e55f951e4bf4fe782ffa61b634e2591 sequence1, detectedOnInitialSample=false. It carried priority=high, bypassQuietHours=true, action=inspect_scene, cause=unknown. Current save counter=0 with Build/Buy blockers=0. Fresh /v1/observe confirmed gamePaused=true and screenCheckReason=save_gate_restriction. A later cursor read after several worker cycles returned zero new alarms, same sequence, and continued active restriction. Saved grim-live-alarm-first.json and grim-live-alarm-repeat.json under outputs/alarm-api. No screenshot or game input was performed. This validates automatic detection, retention and duplicate suppression for this live save-only event; recovery/rearming is still pending user ending the event.

## Live save-only alarm recovery verified

After user reported Grim departed, /v1/alarms at 2026-09-27T03:35:58Z returned status=ok, saveEnableCounterRaw=1, saveGateRestricted=false, restrictionActive=false, reasons=[], and Build/Buy blockers=0. Cursor remained sequence1 with no additional alarms and reset=false. Fresh /v1/observe confirmed gamePaused=true and screenCheckReason=game_paused, no longer save_gate_restriction. Saved outputs/alarm-api/grim-live-alarm-recovery.json. The live event now verifies detection, duplicate suppression, and recovery. The state machine rearms on this valid clear sample; a subsequent live activation has not yet been tested (covered by unit tests). latestAlarm intentionally retains the historical event and must not be confused with an active restriction. Game untouched.

## Baby naming input test

User authorizes the agent to choose baby names creatively for story purposes, without asking for each name. Observed Patricia's baby-girl naming dialog with selected default text Baby Girl. API was fresh and paused, with Build/Buy blockers=0 and save counter=1: this is a pause inspection case, not a restriction alarm. Entered Maya using computer-use text input; screenshot verified exact text, then clicked the checkmark once. Subsequent screenshot confirmed the dialog closed, Patricia holding the baby, and gameplay resumed (clock progressed). No second naming dialog was visible in that follow-up. This validates the single-baby text-entry flow, not twins. Installed name-map identity was not independently verified or changed.
