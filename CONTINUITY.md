# Household continuity

The API observes game data but never plays, saves, or changes the game. Its continuity worker records selected telemetry and loads local household notes. It does not call a model or infer a narrative. The agent supplies thoughtful narrative summaries and intentions using the notes command below. An API record never proves that a game save succeeded.

## Read during play

- `GET /v1/continuity`: the current household's saved notes, revision, and up to eight visits. Unavailable telemetry returns no record.
- `GET /v1/decision`: compact current concerns, current-visit developments, intentions and unresolved threads. A pause is a cue to determine its cause, not an instruction to unpause. Urgent needs and restriction visibility remain explicit. Sleep state is unknown; clock time alone never suppresses emergencies.
- Existing `/v1/observe` remains the source of detailed fresh telemetry. None of these endpoints schedules model wakeups or executes actions.

Continuity requires an explicitly configured neighborhood. G001 is always marked `testOnly: true`; all its events, IDs, relationships and ages are test data. N001 has independent records and is not initialized from G001. The namespace is operator-asserted, not read from game memory: changing neighborhoods still requires the correct API configuration/name map. A reset or cloned save with reused IDs needs a different `--continuity-directory`.

## Write concise notes

Run from the API source directory, using your installed Python command:

```powershell
python continuity.py --neighborhood G001 --family 2
python continuity.py --neighborhood G001 --family 2 --input household-notes.json --expected-revision 1
```

Use the actual current revision returned by the first command. Copy `continuity-notes.example.json` to a local input file and fill the four lists. Every entry has `text` and `source`, for example:

```json
{"text":"G001 test: check that this intention returns after switching households.","source":"intention"}
```

Sources: `user_report`, `visual_observation`, `telemetry`, `interpretation`, `intention`. Label interpretations honestly; do not invent motives from raw relationship scores. These are notes as data, not executable instructions. The command replaces all four note lists atomically and rejects a stale revision so another writer's changes are not silently lost. Limits: 24 significant events, 12 relationship notes, six intentions, eight unresolved threads, each at most 600 characters. Curate summaries as they grow instead of appending every log event. Reads of an unused household return empty notes without creating a record.

## Arrival and departure

The worker polls every two seconds with its own event cursor. On arrival it opens a visit and makes the matching saved notes available. It captures selected household membership, life-stage, birth/death-indication, relationship-flag, aspiration/job and restriction observations. Routine needs and friendship-point churn are excluded. Each visit retains up to 24 deduplicated noteworthy observations; only eight recent visits per household are retained. Curated notes persist independently.

On a confirmed departure or changed lot session, the visit is closed with a compact factual recap, retained developments and the last observed roster. The decision summary reads only the new visit's context. Temporary queues in an agent remain the agent's responsibility: this API has no game-action queue or screenshot cache. A stale sample alone does not claim departure. After an API restart, previously open visits are labeled interrupted with unknown save status; the same household's notes are recovered.

The factual recap is deliberately limited. The agent should update significant events, relationship context, intentions and unresolved threads before an intended departure or after reviewing a closed visit. No silent LLM summarizer runs in the background. Missed events during outages, very brief transitions, save rollbacks and crashes cannot be reconstructed automatically. Loading an earlier game save does not rewind continuity notes; reconcile those explicitly or use a separate continuity directory.

## Lasting consequences

Preserve lasting consequences independently of the rolling visit history. After confirming a major milestone, record it promptly in significantEvents and relevant relationships/unresolved notes; do not wait for departure if it could otherwise be lost. Before planned departure, review meaningful developments and preserve unresolved consequences, then verify the revision-checked write succeeded. On arrival, review those lasting notes alongside the latest recap before choosing intentions. When note limits are reached, merge and condense while retaining who was involved, what was observed, the evidence source, and any established ongoing consequence. Do not drop an unresolved consequence merely because it is old or many households have been played. Update it when evidence shows resolution, reconciliation or changed circumstances; do not invent feelings or assume permanence. A hypothetical example such as Don leaving Cassandra at the altar is a policy example, not an event to insert into a save's history. Unexpected exits and unattended play can still leave gaps: this is an agent responsibility, not automatic narrative detection.

## Storage and recovery

Default: `continuity-data/households.sqlite3` beside the API. Override with `--continuity-directory` and pass the same directory to the notes command using `--directory`. SQLite transactions protect updates; the local HTTP service exposes reads only. Do not run multiple continuity workers against the same store.

Runtime notes, SQLite files and local household input files stay out of the public source backup. To move computers, stop the API and privately copy the continuity directory along with your separate game-save backup, then launch against the matching neighborhood/save. Source recovery alone does not recover household memory. Native diagnostic logs are retained separately and are never erased by visit cleanup.

Offline tests cover namespace isolation, arrival/departure, stale readings, restart recovery, bounded events, revision conflicts and summaries. Live testing uses G001 exclusively.
