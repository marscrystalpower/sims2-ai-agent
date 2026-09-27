"""Print a read-only stream of TS2Bridge household observations from the local API."""

import argparse
import json
import time
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import urlopen


def get_page(base, after):
    url = base.rstrip("/") + "/v1/changes"
    if after is not None:
        url += "?after=" + quote(after, safe="")
    with urlopen(url, timeout=5) as response:
        return json.load(response)


def description(change):
    event = change["observation"]
    who = "NID %s" % change["nid"]
    identity = change.get("identity")
    if identity:
        who = "%s %s (%s; operator-selected %s)" % (
            identity["firstName"], identity["lastName"], who, identity["neighborhood"])
    kind = change["kind"]
    if kind in ("household_joined", "household_left", "family_number_changed"):
        detail = "%s; family %s -> %s" % (
            kind.replace("_", " "), event["previousFamilyNumber"], event["familyNumber"])
    elif kind == "new_household_baby_observed":
        detail = "new household baby observed (birth/adoption not determined)"
    elif kind == "job_level_raw_changed":
        detail = "job level %s -> %s (job action not determined)" % (
            event.get("previousJobLevelRaw"), event.get("jobLevelRaw"))
    elif kind == "job_performance_changed":
        detail = "job performance raw %s -> %s" % (
            event.get("previousJobPerformanceRaw"), event.get("jobPerformanceRaw"))
    elif kind == "age_raw_changed":
        detail = "age raw %s -> %s (cause not determined)" % (
            event.get("previousAgeRaw"), event.get("ageRaw"))
    elif kind in ("school_grade_changed", "school_grade_raw_changed"):
        detail = "school grade raw %s -> %s" % (
            event.get("previousSchoolGradeRaw"), event.get("schoolGradeRaw"))
    elif kind == "ghost_flags_raw_changed":
        detail = "ghost routing flags changed to %s (death not determined)" % event["ghostFlagsRaw"]
    elif kind == "household_death_indicated":
        detail = "possible household death (ghost flags 0 -> 1; cause unknown)"
    elif kind == "aspiration_bit_added":
        added = change["addedAspiration"]
        detail = "aspiration bit added: %s (%s); raw %s -> %s" % (
            added["type"] or "unlabeled", added["bit"],
            event["previousAspirationRaw"], event["aspirationRaw"])
    elif kind == "aspiration_raw_changed":
        detail = "aspiration raw %s -> %s" % (
            event.get("previousAspirationRaw"), event.get("aspirationRaw"))
    elif kind == "lifetime_benefit_spent_changed":
        detail = "lifetime benefit points spent %s -> %s (perk unknown)" % (
            event.get("previousLifetimeBenefitSpentRaw"),
            (event.get("lifetimeAspirationRaw") or {}).get("spent"))
    elif kind in ("hunger_warning", "hunger_urgent", "hunger_warning_recovered",
                  "hunger_urgent_recovered"):
        detail = "%s; raw hunger %.2f" % (kind.replace("_", " "), event["hunger"])
    elif kind in ("bladder_warning", "bladder_urgent", "bladder_warning_recovered",
                  "bladder_urgent_recovered"):
        detail = "%s; raw bladder %.2f" % (
            kind.replace("_", " "), event["needsRaw"]["bladder"])
    elif kind in ("energy_warning", "energy_urgent", "energy_warning_recovered",
                  "energy_urgent_recovered"):
        detail = "%s; raw energy %.2f" % (
            kind.replace("_", " "), event["needsRaw"]["energy"])
    else:
        detail = "%s; hunger %.2f" % (kind.replace("_", " "), event["hunger"])
    return "%s game %02d:%02d  %s: %s" % (
        event["sampledUtc"], event["gameHour"], event["gameMinute"], who, detail)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8765")
    parser.add_argument("--from-start", action="store_true",
                        help="print the current event file history before watching for new events")
    parser.add_argument("--interval", type=float, default=3)
    args = parser.parse_args()
    if args.interval < 0.5:
        parser.error("interval must be at least 0.5 seconds")
    cursor = None
    starting = True
    print("Connecting to the local TS2Bridge API. Ctrl+C stops this reader.", flush=True)
    try:
        while True:
            try:
                page = get_page(args.base, cursor)
            except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
                print("API unavailable: %s" % error, flush=True)
                time.sleep(args.interval)
                continue
            if page.get("reset"):
                print("Event file replaced; starting at its new end.", flush=True)
                starting = True
            if args.from_start or not starting:
                for change in page["changes"]:
                    print(description(change), flush=True)
            cursor = page["nextCursor"]
            if page["scannedEvents"] < 100:
                if starting:
                    print("Watching for new observations.", flush=True)
                starting = False
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
