import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from lot_session import LotSession
from restriction_alarm import AlarmState

spec = importlib.util.spec_from_file_location('transition_api', Path(__file__).with_name('TS2Bridge-api.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class TransitionTests(unittest.TestCase):
    def test_trends_reset_on_same_family_new_visit(self):
        tracker = api.NeedTrendTracker()
        now = datetime.now(timezone.utc)
        sim = {'nid': 1, 'oid': 2, 'ghostFlagsRaw': 0, 'needsRaw': {'hunger': 50}}
        household = {'currentFamily': 3, 'lotSessionId': 'first', 'gamePid': 1, 'sims': [sim]}
        tracker.update(household, now, True)
        newer = dict(household, lotSessionId='second', sims=[dict(sim, needsRaw={'hunger': 10})])
        self.assertEqual(tracker.update(newer, now+timedelta(seconds=5), True), {})

    def test_session_lifecycle(self):
        tracker = LotSession()
        state = {'pid': 1, 'currentFamily': 3, 'status': 'lot', 'sampledUtc': '2026-01-01T00:00:00Z'}
        first = tracker.update(state, True)
        self.assertEqual(first, tracker.update(state, True))
        self.assertEqual(tracker.update(state, False), (None, None))
        self.assertEqual(first, tracker.update(state, True))
        tracker.update({'status': 'unavailable'}, False)
        self.assertNotEqual(first, tracker.update(state, True))
        old = tracker.update(state, True)
        self.assertNotEqual(old, tracker.update(dict(state, pid=2), True))

    def test_stale_and_neighborhood_hide_sims(self):
        from io import StringIO
        state = {'pid': 1, 'status': 'lot', 'currentFamily': 3,
                 'sampledUtc': (datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat(),
                 'selectedNid': 21, 'sims': [{'nid': 21}], 'householdFunds': 100}
        with patch.object(Path, 'open', return_value=StringIO(json.dumps(state))):
            result = api.household_snapshot(Path('.'))
        self.assertFalse(result['fresh'])
        self.assertEqual(result['sims'], [])
        self.assertIsNone(result['currentFamily'])
        self.assertIsNone(result['selectedNid'])
        self.assertIsNone(result['householdFunds'])
        with patch.object(Path, 'open', return_value=StringIO('{"status":"unavailable"}')):
            self.assertEqual(api.lot_snapshot(Path('.'))['sims'], [])

    def test_alarm_exit_and_reentry(self):
        alarm = AlarmState()
        alarm.update(1, 3)
        cursor = alarm.snapshot()['nextCursor']
        alarm.leave_lot()
        result = alarm.snapshot(cursor)
        self.assertTrue(result['reset'])
        self.assertEqual(result['alarms'], [])
        self.assertIsNone(result['latestAlarm'])
        self.assertIsNone(result['controlRestriction']['restrictionActive'])
        alarm.update(0, 4)
        self.assertFalse(alarm.snapshot()['controlRestriction']['restrictionActive'])

    def test_observe_filters_previous_visit_events(self):
        now = datetime.now(timezone.utc)
        household = {'status': 'lot', 'sampledUtc': now.isoformat(), 'currentFamily': 4,
                     'gamePid': 2, 'lotSessionStartedUtc': (now-timedelta(seconds=2)).isoformat(),
                     'gamePaused': True, 'sims': []}
        def event(family, pid, seconds):
            return {'observation': {'currentFamily': family, 'pid': pid,
                    'sampledUtc': (now-timedelta(seconds=seconds)).isoformat()}}
        page = {'changes': [event(3,2,0), event(4,1,0), event(4,2,10), event(4,2,0)],
                'scannedEvents': 4, 'nextCursor': 'x', 'reset': False, 'historySkipped': False}
        with patch.object(api, 'household_snapshot', return_value=household), patch.object(api, 'relationship_snapshot', return_value={}), patch.object(api, 'change_page', return_value=page):
            result = api.observation(Path('.'), None)
        self.assertEqual(len(result['changes']), 1)


if __name__ == '__main__':
    unittest.main()


