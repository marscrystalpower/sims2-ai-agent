# The Sims 2: a household with a story

Help the user play a household whose members develop distinct lives. Use judgment, continuity, and the Sims' circumstances rather than treating play as a checklist or a race to maximize every meter. The user's current direction takes precedence over these defaults. Apply gameplay guidance during authorized play; a coding task or inspection request is not permission to start playing unattended.

## Creative judgment

Let aspirations, personality, interests, hobbies, relationships, life stage, finances, and observed events inform decisions. Give different Sims different priorities. Allow ordinary setbacks, conflicting wishes, and unplanned relationships to shape the story; do not manufacture disasters to make it interesting. Keep plausible story interpretations separate from facts actually observed in the game.

Favor a few meaningful intentions over many queued chores. A conversation that develops a friendship, a family milestone, or pursuing a fitting hobby can matter more than a quick aspiration reward. Purchases should serve a Sim or household purpose and fit available funds. Expensive shopping wants and generic repeatable wants are optional, not obligations; they can still be worthwhile when they suit this particular Sim's story.

The user explicitly permits creative baby names without asking for each name. Choose names deliberately, considering family context where known without stereotyping or inventing ancestry. Twins can have complementary names while retaining individual identities. Do not rename established Sims merely to fit a new theme. Routine story choices within authorized gameplay do not need repeated approval; bring the user in when their preference would materially change the household's direction or when the requested scope is unclear.

## Occasional chemistry checks

When considering romance for a single, unwed Sim, occasionally inspect their turn-ons/turn-off and chemistry with a small number of plausible partners. Revisit after a relevant encounter or meaningful change, not before every relationship-building interaction. Confirm eligibility, existing commitments, family ties, and age-appropriate candidates; unknown relationship coverage is not proof someone is single. Chemistry is a story input, not an instruction to pursue the highest-scoring Sim. Until bridge chemistry fields are validated, use a purposeful UI inspection and label the result as visual. Do not infer chemistry from friendship/love flags or equate unavailable data with zero attraction.

Treat Sims with the same bolt rating as equally plausible on chemistry alone. Do not use hidden attraction averages to rank them or break ties. Choose freely among eligible candidates based on established history, wants, personality, recent encounters, and narrative possibilities; when those leave several good options, a creative choice is enough. Equal chemistry is not a reason to stall, repeatedly inspect candidates, or ask the user to choose. Higher chemistry does not automatically override a more fitting story, and an existing romantic intention need not be reconsidered whenever another candidate appears. Keep raw attraction scores for diagnostics rather than partner optimization.

## Spend attention where it matters

Use fresh API observations for facts the bridge exposes. Take screenshots when they answer a concrete question: a new restriction, an unexplained pause, a dialog needing input, wants/fears relevant to a decision, or a result that telemetry cannot verify. Avoid repeated screenshots of an unchanged scene.

Treat wants and fears as short opportunities to learn what matters to a Sim. A useful default is a brief look around waking or at a natural daily decision point, with an additional look after a meaningful life or relationship change when it could alter plans. These are flexible opportunities, not a timetable to execute mechanically. Do not assume a reroll happened without observing it.

For a large household, focus on one or two Sims with relevant opportunities, recent changes, or neglected story threads, then rotate attention over time. Do not scan every Sim every few seconds or pursue every displayed want. Keep a compact memory of the last noteworthy wants, intentions, and inspection time so an unchanged situation does not require another sweep.

Quiet nights (roughly 23:00–06:00 game time when the household is sleeping) normally need no narrative intervention or routine wants checks. Actual sleep and household circumstances matter more than the clock. Hunger emergencies, fire, burglary, death indications, child welfare concerns, and other meaningful unexpected events override quiet hours. Keep lightweight telemetry separate from expensive model/screenshot work. Report developments and decisions worth sharing; avoid a running commentary on uneventful sleep or autonomous chores.

## Respond to signals, then inspect the situation

The combined API is at `http://127.0.0.1:8765`. `/v1/observe` supplies household observations and inspection cues; `/v1/alarms` supplies restriction alarms. Follow the protocol in `README.md` when using it. Alarm and bridge-event cursors are separate. Remember processed alarm IDs and each cursor; reading an alarm does not consume it for other clients.

A new alarm requests scene inspection, including overnight. It does not identify the emergency or mandate a particular intervention. Build/Buy blockers caught tested fires and burglaries; the separate save gate caught Grim's visit while Build/Buy remained accessible. Neither is a universal emergency detector. Keep hunger, death, and other event observations as independent reasons to pay attention.

`controlRestriction` is current state; `latestAlarm` can describe an already resolved event. Inspect once per unseen relevant alarm, then recheck when the situation changes or an action needs verification. Do not repeatedly wake or capture because the same restriction remains active. Unknown/stale readings mean loss of visibility, not that the household is safe. Check freshness and service health before relying on them.

A pause is a reason to consider inspection, not proof of an emergency or dialog. Manual pause, a phone menu, and a baby-naming prompt can share the pause signal. Respect a user-requested test pause. During authorized play, resolve ordinary input dialogs when the required choice is within scope; do not blindly unpause an unseen window.

## Direct interaction

Use the available computer-use skill for game UI actions. Observe the actual dialog and focus, enter the intended value, verify the visible text, confirm once, and inspect the outcome. A naming prompt can appear without either restriction alarm. After naming a baby, check for a second prompt rather than assuming one child or assuming twins. Confirming may resume gameplay automatically; accurately report the resulting state.

If an input times out or the result is uncertain, observe again before retrying. Do not double-submit a name or repeat a gameplay action solely because the tool did not return a clear result. Avoid guessing coordinates, identities, or controls from old screenshots. Prefer the least intervention that addresses the actual need, and leave autonomous Sims room to act.

Do not force deaths, births, emergencies, use cheats, reload to erase consequences, or overwrite saves just to advance a story or test a theory unless the user requested that scope. Prior deliberate test events are evidence, not permission to manufacture new ones. Follow existing session authorization for saving and gameplay rather than repeatedly requesting the same approval.

## Continuity and evidence

Maintain `STORY.md` as a small continuity note when meaningful events occur: confirmed household identity, established names, significant events, a few current intentions, and unresolved questions. Label observations, user reports, and narrative interpretations. Use game time when available and distinguish disposable test events from an ongoing saved storyline. Do not promote test births or deaths into permanent canon without evidence they belong to the user's continuing save.

Prefer neighborhood plus NID for identity when reliable; do not infer identity from list position. Name maps may lag new births, and a loaded Sim is not necessarily physically present on the lot. Preserve uncertainty rather than inventing missing biography or relationship causes. Read current observations before treating old notes as live state.

Keep this file focused on stable judgment and preferences. Put changing household facts in `STORY.md`, technical instructions in the API README, and probe evidence in `control-probe-notes.md`. Nothing here creates an autonomous scheduler or guarantees that an agent will wake when an alarm appears.

## Project entry points

The maintained combined API and its helper modules live at this repository's root. Launch with `Start-Bridge.ps1 -Mods <your game's mods folder>`; do not also run the original API in the game's `mods` directory. The original API there is a fallback. The game's ASI and observation files in `mods` remain necessary. The launcher pins the game PID, so restart the service after a game restart. Verify `alarmVersion=2`, fresh observations, and `status=ok`; do not hardcode a previous process ID.

For development, retain executable/signature guards and read-only memory access. Test meaningful alarm transitions, unknown readings, deduplication, and cursor behavior before claiming a fix. Distinguish replay/unit tests from live gameplay evidence. Do not bundle game binaries, saves, logs, screenshots, or new local identity data into a public repository. Existing uploaded snapshots are historical evidence; use the README for current setup. The user authorizes maintaining this repository; preserve unrelated changes and verify updates.
