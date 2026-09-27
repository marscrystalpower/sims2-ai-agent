"""Local read-only HTTP adapter for TS2Bridge's state and JSONL observations."""

import argparse
import json
import os
import re
import socket
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit
from restriction_alarm import RestrictionWatcher
from control_probe import DEFAULT_EXE
from preferences import snapshot as preference_snapshot
from chemistry import attach as attach_chemistry
from lot_session import LotSession
from continuity import Store, Continuity, Worker as ContinuityWorker, decision_summary, DEFAULT_DIRECTORY


CURSOR = re.compile(r"^([0-9a-f]+)\.([0-9a-f]+)\.(\d+)$")
MAX_EVENTS = 100
TREND_MOTIVES = ("hunger", "bladder", "energy")
ASPIRATION_BITS = {1: "romance", 2: "family", 4: "fortune",
                   16: "popularity", 32: "knowledge", 64: "grow_up",
                   128: "pleasure", 256: "grilled_cheese"}
# Each low-byte bit has now been matched with live directional pairs:
# going steady, enemy, engaged, married, and one-sided crush were
# checked together with the earlier crush/love/friend/best-friend pairs.
# Preserve the full raw word; fury and BFF are not inferred from it.
RELATION_FLAGS = ((1, "crush"), (2, "love"), (4, "engaged"),
                  (8, "married"), (16, "friend"), (32, "best_friend"),
                  (64, "going_steady"), (128, "enemy"))
FAMILY_RELATIONS = {1: "parent", 2: "child", 3: "sibling",
                    4: "grandparent", 5: "grandchild",
                    6: "aunt_or_uncle", 7: "niece_or_nephew",
                    8: "cousin", 9: "spouse"}
# Validated against one Sim through nonpregnant, invisible, half, full stages.
# Keep the other body bits (such as Fit) independent of pregnancy.
PREGNANCY_BITS = {8: "invisible", 4: "first_bump", 2: "second_bump"}
PREGNANCY_MASK = 8 | 4 | 2
BODY_TRAITS = ((1, "overweight"), (16, "fit"))
HOBBY_SLOTS = (
    ("0xCC", "cuisine"), ("0xCD", "arts_and_crafts"),
    ("0xCE", "film_and_literature"), ("0xCF", "sports"),
    ("0xD0", "games"), ("0xD1", "nature"),
    ("0xD2", "tinkering"), ("0xD3", "fitness"),
    ("0xD4", "science"), ("0xD5", "music_and_dance"),
)
HOBBY_NAMES = {int(slot, 16): name for slot, name in HOBBY_SLOTS}
INTEREST_SLOTS = (
    ("0x7C", "politics"), ("0x7D", "money"),
    ("0x7E", "environment"), ("0x7F", "crime"),
    ("0x80", "entertainment"), ("0x81", "culture"),
    ("0x82", "food"), ("0x83", "health"),
    ("0x84", "fashion"), ("0x85", "sports"),
    ("0x86", "paranormal"), ("0x87", "travel"),
    ("0x88", "work"), ("0x89", "weather"),
    ("0x8A", "animals"), ("0x8B", "school"),
    ("0x8C", "toys"), ("0x8D", "sci_fi"),
)


def body_traits(raw):
    if type(raw) is not int or not 0 <= raw <= 65535:
        return None
    return [name for bit, name in BODY_TRAITS if raw & bit]


class NeedTrendTracker:
    """Compare distinct lot samples across a recent observation window."""

    def __init__(self):
        self.lock = Lock()
        self.history = []
        self.trends = {}

    def update(self, household, when, fresh):
        with self.lock:
            if not fresh or when is None:
                self.history = []
                self.trends = {}
                return {}
            sample = {
                "time": when, "family": (household["currentFamily"],
                                          household.get('lotSessionId'), household.get('gamePid')),
                "sims": {(sim["nid"], sim["oid"]): sim for sim in household["sims"]},
            }
            if (self.history and self.history[-1]["time"] == when and
                    self.history[-1]["family"] == sample["family"]):
                return self.trends
            if (self.history and (when < self.history[-1]["time"] or
                                  self.history[-1]["family"] != sample["family"])):
                self.history = []
            self.history = [prior for prior in self.history
                            if 0 < (when - prior["time"]).total_seconds() <= 45]
            self.trends = {}
            for key, sim in sample["sims"].items():
                if sim.get("ghostFlagsRaw") != 0:
                    continue
                motives = {}
                for motive in TREND_MOTIVES:
                    # Look back beyond a single quiet 5-second interval. A
                    # different Sim can have started recovering meanwhile.
                    old_sample = next((prior for prior in self.history
                                       if key in prior["sims"] and
                                       prior["sims"][key].get("ghostFlagsRaw") == 0 and
                                       isinstance((prior["sims"][key].get("needsRaw") or {}).get(motive),
                                                  (int, float))), None)
                    if old_sample is None:
                        continue
                    before = (old_sample["sims"][key].get("needsRaw") or {}).get(motive)
                    after = (sim.get("needsRaw") or {}).get(motive)
                    if not isinstance(after, (int, float)):
                        continue
                    delta = round(after - before, 2)
                    motives[motive] = {
                        "direction": ("rising" if delta > 1 else
                                      "falling" if delta < -1 else "steady"),
                        "delta": delta,
                        "seconds": (when - old_sample["time"]).total_seconds(),
                    }
                if motives:
                    self.trends[key] = motives
            self.history.append(sample)
            return self.trends


need_trend_tracker = NeedTrendTracker()
lot_session = LotSession()
configured_game_pid = None


def load_name_context(neighborhood, path):
    if neighborhood is None and path is None:
        return None
    if not neighborhood or not path:
        raise ValueError("--neighborhood and --names must be given together")
    mapping = json.loads(path.read_text(encoding="utf-8"))
    if mapping.get("neighborhood") != neighborhood:
        raise ValueError("name map neighborhood does not match --neighborhood")
    names = {}
    for entry in mapping["resolved"]:
        nid = entry["nid"]
        if not isinstance(nid, int) or nid <= 0 or nid in names:
            raise ValueError("invalid or duplicate NID in name map")
        description = entry.get("description")
        if description is not None and (not isinstance(description, str) or
                                        len(description) > 4096):
            raise ValueError("invalid description in name map")
        names[nid] = {"firstName": entry["firstName"], "lastName": entry["lastName"],
                      "description": description or None}
    return {"code": neighborhood, "verifiedByGame": False, "names": names}


def identity(nid, context):
    if context is None or nid not in context["names"]:
        return None
    return dict(context["names"][nid], neighborhood=context["code"],
                verifiedByGame=False)


def lot_snapshot(mods, context=None):
    with (mods / "TS2Bridge-state.json").open("r", encoding="utf-8") as file:
        state = json.load(file)
    # Native schema1 state omits pid (event records include it).
    if state.get('pid') is None:
        state['pid'] = configured_game_pid
    age = None
    try:
        when = datetime.fromisoformat(state['sampledUtc'].replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - when).total_seconds()
    except (KeyError, TypeError, ValueError):
        pass
    fresh = (state.get('status') == 'lot' and type(state.get('currentFamily')) is int and
             age is not None and -5 <= age <= 15)
    session_id, session_start = lot_session.update(state, fresh)
    source_status = state.get('status')
    if not fresh:
        state = dict(state, status='unavailable', sims=[], currentFamily=None,
                     selectedNid=None, gameHour=None, gameMinute=None,
                     householdFunds=None, fundsProbeRaw=None)
    current = state.get("currentFamily") if state.get("status") == "lot" else None
    paused_raw = state.get("pausedProbeRaw")
    sims = []
    for sim in state.get("sims", []):
        view = dict(sim)
        view["loaded"] = True
        view["inCurrentHousehold"] = current is not None and sim["familyNumber"] == current
        enthusiasm = sim.get("hobbyEnthusiasmProbeRaw")
        valid_hobbies = (view["inCurrentHousehold"] and
                         isinstance(enthusiasm, dict) and
                         all(type(enthusiasm.get(slot)) is int and
                             0 <= enthusiasm[slot] <= 2000 for slot, _ in HOBBY_SLOTS))
        view["hobbyEnthusiasmPoints"] = (
            {name: min(10, enthusiasm[slot] // 100) for slot, name in HOBBY_SLOTS}
            if valid_hobbies else None
        )
        predestined = sim.get("predestinedHobbyProbeRaw")
        view["oneTrueHobby"] = (
            HOBBY_NAMES.get(predestined)
            if view["inCurrentHousehold"] and type(predestined) is int else None
        )
        # Keep the agent-facing hobby summary compact; native files retain probes.
        for field in ("hobbyEnthusiasmProbeRaw", "predestinedHobbyProbeRaw",
                      "hobbyEnthusiasmRaw", "hobbyEnthusiasmValue"):
            view.pop(field, None)
        interests = sim.get("interestProbeRaw")
        valid_interests = (
            view["inCurrentHousehold"] and isinstance(interests, dict) and
            all(type(interests.get(slot)) is int and
                0 <= interests[slot] <= 2000 for slot, _ in INTEREST_SLOTS)
        )
        # Matched against three visible Interests panels; the 700 -> 800
        # Food and 800 -> 700 Animals changes tracked 7 -> 8 and 8 -> 7.
        view["interestPoints"] = (
            {name: min(10, interests[slot] // 100)
             for slot, name in INTEREST_SLOTS}
            if valid_interests else None
        )
        view.pop("interestProbeRaw", None)
        # The simulator also loads non-present Sims (e.g. phone callers).
        view["interactiveOnLot"] = None
        # Person-data field 65 uses 0/1 in live memory. The GZPS character
        # files use 2/1 instead; never apply their encoding to this raw field.
        gender_raw = sim.get("genderRaw")
        view["gameGender"] = (
            "male" if gender_raw == 0 else "female" if gender_raw == 1 else None
        ) if view["inCurrentHousehold"] and type(gender_raw) is int else None
        body_flags = sim.get("bodyFlagsRaw")
        view["bodyTraits"] = body_traits(body_flags) if view["inCurrentHousehold"] else None
        pregnancy_bits = (
            body_flags & PREGNANCY_MASK
            if view["inCurrentHousehold"] and type(body_flags) is int
            and 0 <= body_flags <= 65535 else None
        )
        view["isPregnant"] = bool(pregnancy_bits) if pregnancy_bits is not None else None
        view["pregnancyStage"] = PREGNANCY_BITS.get(pregnancy_bits)
        preference = sim.get("genderPreferenceRaw")
        view["genderPreference"] = (
            {name: round(preference[name] / 100, 2) for name in ("male", "female")}
            if isinstance(preference, dict) and all(
                type(preference.get(name)) is int and -1000 <= preference[name] <= 1000
                for name in ("male", "female")) else None
        )
        # Sim Description age codes; baby/toddler and teen/adult/elder were
        # also checked against live household transitions in this build.
        view["ageStage"] = (
            {1: "baby", 2: "toddler", 3: "child",
             16: "teen", 19: "adult", 51: "elder"}.get(sim.get("ageRaw"))
            if view["inCurrentHousehold"] else None
        )
        # The skill panel's Cooking 3 -> 4 matched raw 362 -> 405 in a
        # live test. Preserve the raw values for partial progress and keep
        # other named fields provisional until each is checked in game.
        skills = sim.get("skillsRaw")
        view["skillLevels"] = (
            {name: value // 100 if type(value) is int and 0 <= value <= 1000
             else None for name, value in skills.items()}
            if view["inCurrentHousehold"] and isinstance(skills, dict) else None
        )
        personality = sim.get("personalityRaw")
        view["personalityPoints"] = (
            {name: value // 100 if type(value) is int and 0 <= value <= 1000
             else None for name, value in personality.items()}
            if view["inCurrentHousehold"] and isinstance(personality, dict) else None
        )
        # Exact single-bit values can name an aspiration. Composite values
        # remain ambiguous about which aspiration is primary.
        view["aspirationType"] = (
            ASPIRATION_BITS.get(sim.get("aspirationRaw"))
            if view["inCurrentHousehold"] else None
        )
        raw_aspiration = sim.get("aspirationRaw")
        view["activeAspirations"] = (
            [{"bit": bit, "type": label} for bit, label in ASPIRATION_BITS.items()
             if raw_aspiration & bit]
            if view["inCurrentHousehold"] and type(raw_aspiration) is int and
            0 <= raw_aspiration <= 0x1ff else None
        )
        # The live three-stage test confirmed 0 -> 1 -> 2 spent points.
        lta = sim.get("lifetimeAspirationRaw") or {}
        unlocked, spent = lta.get("unlock"), lta.get("spent")
        view["benefitPoints"] = (
            {"unlocked": unlocked, "spent": spent, "unspent": unlocked - spent}
            if view["inCurrentHousehold"] and type(unlocked) is int and
            type(spent) is int and 0 <= spent <= unlocked <= 16 else None
        )
        view["hungerBand"] = (
            "urgent" if sim["hunger"] < -30 else
            "warning" if sim["hunger"] < 0 else "ok"
        ) if view["inCurrentHousehold"] and sim.get("ghostFlagsRaw") == 0 else None
        bladder = (sim.get("needsRaw") or {}).get("bladder")
        view["bladderBand"] = (
            "urgent" if bladder < -40 else
            "warning" if bladder < -20 else "ok"
        ) if view["inCurrentHousehold"] and sim.get("ghostFlagsRaw") == 0 and (
            isinstance(bladder, (int, float)) and not isinstance(bladder, bool)) else None
        energy = (sim.get("needsRaw") or {}).get("energy")
        view["energyBand"] = (
            "urgent" if energy < -40 else
            "warning" if energy < 0 else "ok"
        ) if view["inCurrentHousehold"] and sim.get("ghostFlagsRaw") == 0 and (
            isinstance(energy, (int, float)) and not isinstance(energy, bool)) else None
        view["identity"] = identity(sim["nid"], context)
        sims.append(view)
    return {
        "api": 1,
        "sourceSchema": state.get("schema"),
        "bridge": state.get("bridge"),
        "sampledUtc": state.get("sampledUtc"),
        "status": state.get("status"),
        "sourceStatus": source_status,
        "fresh": fresh,
        "sampleAgeSeconds": round(age, 1) if age is not None else None,
        "gamePid": state.get('pid'),
        "lotSessionId": session_id,
        "lotSessionStartedUtc": session_start,
        "gameHour": state.get("gameHour"),
        "gameMinute": state.get("gameMinute"),
        "pausedProbeRaw": state.get("pausedProbeRaw") if (
            current is not None and type(state.get("pausedProbeRaw")) is int) else None,
        "gamePaused": bool(paused_raw) if current is not None and
        type(paused_raw) is int and paused_raw in (0, 1) else None,
        "gameModeProbeRaw": state.get("gameModeProbeRaw") if (
            current is not None and type(state.get("gameModeProbeRaw")) is int) else None,
        "currentFamily": current,
        "householdFunds": state.get("householdFunds") if (
            current is not None and type(state.get("householdFunds")) is int) else None,
        "fundsProbeRaw": state.get("fundsProbeRaw") if (
            current is not None and type(state.get("fundsProbeRaw")) is int) else None,
        "selectedNid": state.get("selectedNid"),
        "neighborhoodContext": ({"code": context["code"], "verifiedByGame": False}
                                if context else None),
        "sims": sims,
    }


def household_snapshot(mods, context=None):
    lot = lot_snapshot(mods, context)
    lot["otherLoadedCount"] = sum(not sim["inCurrentHousehold"] for sim in lot["sims"])
    lot["sims"] = [sim for sim in lot["sims"] if sim["inCurrentHousehold"]]
    return lot


def relationship_snapshot(mods, context=None, household=None):
    """Show current household pairs and an explicitly targeted loaded visitor."""
    if household is None:
        household = household_snapshot(mods, context)
    try:
        with (mods / "TS2Bridge-relationships.json").open("r", encoding="utf-8") as file:
            raw = json.load(file)
    except FileNotFoundError:
        return {"api": 1, "status": "unavailable", "fresh": False, "pairs": [],
                "configuredTargetNid": None, "targetLoaded": False,
                "coverage": "all_loaded_household_members_up_to_8_plus_configured_visitor"}
    age = None
    aligned = False
    try:
        sample = datetime.fromisoformat(raw["sampledUtc"].replace("Z", "+00:00"))
        state_sample = datetime.fromisoformat(household["sampledUtc"].replace("Z", "+00:00"))
        age = round((datetime.now(timezone.utc) - sample).total_seconds(), 1)
        aligned = abs((sample - state_sample).total_seconds()) <= 10
    except (KeyError, TypeError, ValueError):
        pass
    fresh = (raw.get("status") == "lot" and household["status"] == "lot" and
             raw.get("currentFamily") == household["currentFamily"] and
             aligned and age is not None and -5 <= age <= 15)
    pairs = []
    target = raw.get("configuredTargetNid")
    target = target if type(target) is int and target > 0 else None
    household_nids = {sim["nid"] for sim in household["sims"]}
    lot = lot_snapshot(mods, context) if fresh and target else None
    target_loaded = bool(lot and any(
        sim["nid"] == target and not sim["inCurrentHousehold"] and
        sim["familyNumber"] not in (0, 32767) for sim in lot["sims"]))
    coverage = ("all_loaded_household_members_up_to_8_plus_configured_visitor"
                if type(raw.get("sampledHouseholdCount")) is int else
                "first_two_loaded_household_members_plus_configured_visitor")
    if fresh:
        for pair in raw.get("pairs", []):
            bits = pair.get("stateBitsRaw")
            if type(bits) is not int:
                continue
            viewer, other = pair.get("viewerNid"), pair.get("otherNid")
            if not ((viewer in household_nids and other in household_nids) or
                    (target_loaded and {viewer, other} & household_nids and
                     target in (viewer, other))):
                continue
            view = dict(pair)
            view["stateFlags"] = [name for bit, name in RELATION_FLAGS if bits & bit]
            # Known is in the high byte; the family tie is a separate
            # directional relationship code, not one of the eight state bits.
            view["known"] = bool(bits & 0x8000)
            family_code = pair.get("index3Raw")
            view["familyRelation"] = (
                FAMILY_RELATIONS.get(family_code) if type(family_code) is int else None
            )
            view["hasFamilyTie"] = (
                family_code in FAMILY_RELATIONS if type(family_code) is int else None
            )
            # The final SREL slot is a separate BFF boolean. Both sides
            # of the observed BFF pair have 1; earlier non-BFF pairs have 0.
            slots = pair.get("rawSlots")
            bff = slots[9] if isinstance(slots, list) and len(slots) > 9 else None
            view["bestFriendsForever"] = bool(bff) if type(bff) is int and bff in (0, 1) else None
            view["viewerIdentity"] = identity(pair["viewerNid"], context)
            view["otherIdentity"] = identity(pair["otherNid"], context)
            view["viewerInCurrentHousehold"] = viewer in household_nids
            view["otherInCurrentHousehold"] = other in household_nids
            pairs.append(view)
    attach_chemistry(pairs, household['sims'], supported=household.get('bridge') == '1.33')
    return {"api": 1, "status": raw.get("status"), "sampledUtc": raw.get("sampledUtc"),
            "currentFamily": raw.get("currentFamily"), "sampleAgeSeconds": age,
            "fresh": fresh, "pairs": pairs,
            "configuredTargetNid": target, "targetLoaded": target_loaded,
            "sampledHouseholdCount": raw.get("sampledHouseholdCount"),
            "coverageTruncated": raw.get("coverageTruncated"),
            "coverage": coverage}


def event_page(mods, after, from_now=False):
    path = mods / "TS2Bridge-events.jsonl"
    try:
        file = path.open("rb")
    except FileNotFoundError:
        return {"api": 1, "events": [], "nextCursor": None, "reset": bool(after),
                "historySkipped": bool(from_now)}
    with file:
        meta = os.fstat(file.fileno())
        identity = (meta.st_dev, meta.st_ino)
        start = 0
        reset = False
        if after:
            match = CURSOR.fullmatch(after)
            if not match:
                raise ValueError("invalid cursor")
            prior_identity = (int(match[1], 16), int(match[2], 16))
            start = int(match[3])
            if prior_identity != identity or start > meta.st_size:
                start = 0
                reset = True
        skip_history = from_now and (after is None or reset)
        if skip_history:
            # Position at the end of the last complete JSONL record. If the
            # writer is in the middle of a record, let the next request read it.
            start = meta.st_size
            while start:
                block_start = max(0, start - 4096)
                file.seek(block_start)
                block = file.read(start - block_start)
                newline = block.rfind(b"\n")
                if newline >= 0:
                    start = block_start + newline + 1
                    break
                start = block_start
        file.seek(start)
        events = []
        for _ in range(0 if skip_history else MAX_EVENTS):
            line = file.readline()
            if not line or not line.endswith(b"\n"):
                break  # Do not consume a JSONL record still being written.
            events.append(json.loads(line))
            start = file.tell()
        next_cursor = "%x.%x.%d" % (identity[0], identity[1], start)
    return {"api": 1, "events": events, "nextCursor": next_cursor, "reset": reset,
            "historySkipped": skip_history}


def change_page(mods, after, context=None, from_now=False):
    page = event_page(mods, after, from_now=from_now)
    changes = []
    for event in page["events"]:
        kind = event["type"]
        if kind == "household_funds_changed":
            changes.append({"kind": kind, "nid": None, "identity": None,
                            "currentFamily": event.get("currentFamily"),
                            "previousHouseholdFunds": event.get("previousHouseholdFunds"),
                            "householdFunds": event.get("householdFunds"),
                            "delta": event.get("delta"), "observation": event})
            continue
        added_aspiration = None
        if kind == "family_number_changed":
            old, new, current = (event["previousFamilyNumber"],
                                 event["familyNumber"], event["currentFamily"])
            kind = "household_joined" if new == current else (
                "household_left" if old == current else "family_number_changed")
        elif kind == "job_level_increased":
            kind = "job_level_raw_changed"  # 0 -> 1 can be taking a job.
        elif kind == "ghost_flags_raw_changed" and (
            event.get("previousGhostFlagsRaw") == 0 and event.get("ghostFlagsRaw") == 1 and
            event.get("matchesCurrentFamily") is True and
            event.get("wasHouseholdMemberThisLot") is True
        ):
            kind = "household_death_indicated"  # Inferred, never a confirmed cause.
        elif kind == "aspiration_raw_changed":
            old, new = event.get("previousAspirationRaw"), event.get("aspirationRaw")
            if (type(old) is int and type(new) is int and old > 0 and
                old & (old - 1) == 0 and new & old == old):
                added = new & ~old
                if added > 0 and added & (added - 1) == 0:
                    kind = "aspiration_bit_added"
                    added_aspiration = {"bit": added, "type": ASPIRATION_BITS.get(added)}
        elif kind not in ("job_level_raw_changed", "job_performance_changed",
                          "age_raw_changed",
                          "school_grade_changed", "school_grade_raw_changed",
                          "ghost_flags_raw_changed", "new_household_baby_observed",
                          "hunger_below_40", "hunger_recovered_50",
                          "hunger_warning", "hunger_urgent",
                          "hunger_urgent_recovered", "hunger_warning_recovered",
                          "bladder_warning", "bladder_urgent",
                          "bladder_urgent_recovered", "bladder_warning_recovered",
                          "energy_warning", "energy_urgent",
                          "energy_urgent_recovered", "energy_warning_recovered",
                          "lifetime_benefit_spent_changed",
                          "relationship_daily_drop", "relationship_daily_rise",
                          "relationship_flag_removed", "body_flags_raw_changed"):
            continue
        change = {"kind": kind, "nid": event["nid"],
                  "identity": identity(event["nid"], context),
                  "observation": event}
        if added_aspiration is not None:
            change["addedAspiration"] = added_aspiration
        if kind in ("relationship_daily_drop", "relationship_daily_rise",
                    "relationship_flag_removed"):
            change["otherNid"] = event["otherNid"]
            change["otherIdentity"] = identity(event["otherNid"], context)
            if kind == "relationship_flag_removed":
                lost = event.get("lostKnownFlagBits", 0)
                change["lostKnownFlags"] = [name for bit, name in RELATION_FLAGS
                                            if type(lost) is int and lost & bit]
        if kind == "body_flags_raw_changed":
            before, after = event.get("previousBodyFlagsRaw"), event.get("bodyFlagsRaw")
            if type(before) is int and type(after) is int:
                before_pregnant, after_pregnant = bool(before & PREGNANCY_MASK), bool(after & PREGNANCY_MASK)
                transitions = []
                if not before_pregnant and after_pregnant:
                    transitions.append("pregnancy_started")
                elif before_pregnant and not after_pregnant:
                    transitions.append("pregnancy_ended")
                elif (before & PREGNANCY_MASK) != (after & PREGNANCY_MASK):
                    transitions.append("pregnancy_stage_changed")
                for bit, name in BODY_TRAITS:
                    if bool(before & bit) != bool(after & bit):
                        transitions.append(("became_" if after & bit else "no_longer_") + name)
                change["bodyTransitions"] = transitions
                change["previousPregnancyStage"] = PREGNANCY_BITS.get(before & PREGNANCY_MASK)
                change["pregnancyStage"] = PREGNANCY_BITS.get(after & PREGNANCY_MASK)
                change["previousBodyTraits"] = body_traits(before)
                change["bodyTraits"] = body_traits(after)
        changes.append(change)
    return {"api": 1, "changes": changes, "scannedEvents": len(page["events"]),
            "nextCursor": page["nextCursor"], "reset": page["reset"],
            "historySkipped": page["historySkipped"]}


def observation(mods, after, context=None, alarm=None):
    household = household_snapshot(mods, context)
    relationships = relationship_snapshot(mods, context, household)
    changes = change_page(mods, after, context, from_now=True)
    sampled = household.get("sampledUtc")
    age = None
    when = None
    if isinstance(sampled, str):
        try:
            when = datetime.fromisoformat(sampled.replace("Z", "+00:00"))
            if when.tzinfo is not None:
                age = round((datetime.now(timezone.utc) - when).total_seconds(), 1)
        except ValueError:
            pass
    fresh = household["status"] == "lot" and age is not None and -5 <= age <= 15
    # Diagnostic event endpoints retain history; observations are visit-scoped.
    started = household.get('lotSessionStartedUtc')
    def current_change(change):
        event = change.get('observation') or {}
        if not fresh or event.get('currentFamily') != household.get('currentFamily'):
            return False
        if household.get('gamePid') is not None and event.get('pid') != household['gamePid']:
            return False
        if started:
            try:
                return datetime.fromisoformat(event['sampledUtc'].replace('Z', '+00:00')) >= datetime.fromisoformat(started.replace('Z', '+00:00'))
            except (KeyError, TypeError, ValueError):
                return False
        return True
    changes['changes'] = [change for change in changes['changes'] if current_change(change)]
    # A pause is actionable as a visual inspection cue, not evidence of a
    # dialog: manual pause and the phonebook both set the same game global.
    screen_check = fresh and household["gamePaused"] is True
    alarm_snapshot = alarm.state.snapshot() if alarm else None
    restriction = alarm_snapshot['controlRestriction'] if alarm_snapshot else None
    if restriction and (not fresh or restriction.get('currentFamily') != household['currentFamily']):
        restriction = {'status': 'unknown', 'restrictionActive': None,
                       'blockerCount': None, 'saveEnableCounterRaw': None,
                       'saveGateRestricted': None, 'error': 'No aligned active household'}
    restriction_active = bool(restriction and restriction['restrictionActive'] is True)
    trends = need_trend_tracker.update(household, when, fresh)
    for sim in household["sims"]:
        sim["needTrends"] = trends.get((sim["nid"], sim["oid"]), {})
    return {
        "api": 1,
        "household": household,
        "relationships": relationships,
        "changes": changes["changes"],
        "scannedEvents": changes["scannedEvents"],
        "nextCursor": changes["nextCursor"],
        "reset": changes["reset"],
        "historySkipped": changes["historySkipped"],
        "sampleAgeSeconds": age,
        "fresh": fresh,
        "screenCheckRecommended": screen_check or restriction_active,
        "screenCheckReason": (('save_gate_restriction' if restriction.get('saveGateRestricted')
                               else 'build_buy_restriction') if restriction_active else
                              "game_paused" if screen_check else None),
        "alarmVersion": alarm_snapshot['alarmVersion'] if alarm_snapshot else None,
        "controlRestriction": restriction,
        "latestAlarm": (alarm_snapshot['latestAlarm'] if alarm_snapshot and fresh and
                        alarm_snapshot['latestAlarm'] and
                        alarm_snapshot['latestAlarm'].get('currentFamily') == household['currentFamily'] else None),
        "alarmCursor": alarm_snapshot['nextCursor'] if alarm_snapshot else None,
        "alarmEnabled": alarm is not None,
    }


class ExclusiveHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class ApiHandler(BaseHTTPRequestHandler):
    continuity = None
    preference_pid = None
    preference_exe = DEFAULT_EXE
    alarm = None
    mods = None
    name_context = None
    benefit_catalog = None

    def reply(self, status, body):
        data = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # A local browser page must not use DNS rebinding to read the bridge.
        if self.headers.get("Host") not in ("127.0.0.1:%d" % self.server.server_port,
                                            "localhost:%d" % self.server.server_port):
            self.reply(400, {"error": "local host required"})
            return
        url = urlsplit(self.path)
        try:
            if url.path in ('/v1/continuity', '/v1/decision') and not url.query:
                if self.continuity is None:
                    self.reply(503, {'status': 'unavailable', 'error': 'Continuity needs an explicit neighborhood'})
                else:
                    obs = observation(self.mods, None, self.name_context, self.alarm)
                    memory = self.continuity.view(obs)
                    self.reply(200, memory if url.path == '/v1/continuity' else decision_summary(obs, memory))
            elif url.path == '/v1/preferences':
                params = parse_qs(url.query, keep_blank_values=True)
                if (set(params) != {'nid'} or len(params['nid']) != 1 or
                        not params['nid'][0].isascii() or not params['nid'][0].isdigit() or
                        not 1 <= int(params['nid'][0]) <= 65535):
                    raise ValueError('Expected one nid between 1 and 65535')
                if self.preference_pid is None:
                    self.reply(503, {'status': 'unavailable', 'error': 'Game PID not configured'})
                else:
                    try:
                        before = lot_snapshot(self.mods, self.name_context)
                        nid = int(params['nid'][0])
                        if (not before['fresh'] or before.get('gamePid') != self.preference_pid or
                                not any(sim['nid'] == nid for sim in before['sims'])):
                            raise ValueError('Requested Sim is not in the current fresh lot context')
                        result = preference_snapshot(self.preference_pid, self.preference_exe, int(params['nid'][0]))
                        after = lot_snapshot(self.mods, self.name_context)
                        if (not after['fresh'] or before['lotSessionId'] != after['lotSessionId'] or
                                not any(sim['nid'] == nid for sim in after['sims'])):
                            raise ValueError('Lot context changed during preference read')
                        result['lotSessionId'] = after['lotSessionId']
                    except (ValueError, OSError) as error:
                        self.reply(503, {'status': 'unavailable', 'error': str(error)})
                    else:
                        self.reply(200, result)
            elif url.path == "/v1/alarms":
                params = parse_qs(url.query, keep_blank_values=True)
                if (set(params) - {'after'} or len(params.get('after', [])) > 1 or
                        params.get('after') == ['']):
                    raise ValueError('invalid query')
                if self.alarm is None:
                    self.reply(503, {'error': 'Restriction alarm not enabled'})
                else:
                    self.reply(200, self.alarm.state.snapshot(params.get('after', [None])[0]))
            elif url.path == "/v1/benefit-catalog" and not url.query:
                self.reply(200, self.benefit_catalog)
            elif url.path in ("/v1/lot", "/v1/household") and not url.query:
                result = (lot_snapshot(self.mods, self.name_context) if url.path == "/v1/lot"
                          else household_snapshot(self.mods, self.name_context))
                self.reply(200, result)
            elif url.path == "/v1/relationships" and not url.query:
                self.reply(200, relationship_snapshot(self.mods, self.name_context))
            elif url.path in ("/v1/events", "/v1/changes", "/v1/observe"):
                params = parse_qs(url.query, keep_blank_values=True)
                if (set(params) - {"after"} or len(params.get("after", [])) > 1 or
                    params.get("after") == [""]):
                    raise ValueError("invalid query")
                cursor = params.get("after", [None])[0]
                self.reply(200, event_page(self.mods, cursor) if url.path == "/v1/events"
                           else change_page(self.mods, cursor, self.name_context)
                           if url.path == "/v1/changes"
                           else observation(self.mods, cursor, self.name_context, self.alarm))
            else:
                self.reply(404, {"error": "unknown endpoint"})
        except (ValueError, json.JSONDecodeError) as error:
            self.reply(400, {"error": str(error)})
        except (FileNotFoundError, OSError):
            self.reply(503, {"error": "bridge state unavailable"})


def main():
    global configured_game_pid
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mods", type=Path, default=Path(__file__).resolve().parent,
                        help="folder containing TS2Bridge-state.json and TS2Bridge-events.jsonl")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--neighborhood", help="operator-asserted neighborhood, e.g. G001")
    parser.add_argument("--names", type=Path, help="name map for --neighborhood")
    parser.add_argument('--alarm-pid', type=int, help='Enable read-only restriction alarm for this game PID')
    parser.add_argument('--alarm-exe', type=Path, default=DEFAULT_EXE)
    parser.add_argument('--continuity-directory', type=Path, default=DEFAULT_DIRECTORY,
                        help='Local household memory directory; use a different directory for a reset/cloned save')
    args = parser.parse_args()
    configured_game_pid = args.alarm_pid
    if not args.mods.is_dir():
        parser.error("mods directory does not exist")
    try:
        ApiHandler.name_context = load_name_context(args.neighborhood, args.names)
        ApiHandler.benefit_catalog = json.loads(
            (Path(__file__).resolve().parent / "TS2Bridge-benefit-catalog.json")
            .read_text(encoding="utf-8"))
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        parser.error(str(error))
    ApiHandler.mods = args.mods
    ApiHandler.preference_pid = args.alarm_pid
    ApiHandler.preference_exe = args.alarm_exe
    server = ExclusiveHTTPServer(("127.0.0.1", args.port), ApiHandler)
    if args.alarm_pid is not None:
        ApiHandler.alarm = RestrictionWatcher(args.mods, args.alarm_pid, args.alarm_exe,
                                            context_observer=lot_session.update)
        ApiHandler.alarm.start()
    continuity_worker = None
    if args.neighborhood:
        ApiHandler.continuity = Continuity(Store(args.continuity_directory / 'households.sqlite3'), args.neighborhood)
        continuity_worker = ContinuityWorker(ApiHandler.continuity,
            lambda cursor: observation(args.mods, cursor, ApiHandler.name_context, ApiHandler.alarm))
        continuity_worker.start()
    print("TS2Bridge local API: http://127.0.0.1:%d/v1/lot" % server.server_port,
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if continuity_worker:
            continuity_worker.stop()
        if ApiHandler.alarm:
            ApiHandler.alarm.stop()
        server.server_close()


if __name__ == "__main__":
    main()
