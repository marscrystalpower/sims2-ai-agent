import copy
from datetime import datetime, timezone
import unittest
from continuity import Store, Continuity, Worker, decision_summary, validate_notes


def observation(family=2, session='visit-a', fresh=True, source='lot', events=None):
    return {'fresh': fresh, 'nextCursor': 'cursor',
            'household': {'currentFamily': family if fresh else None, 'lotSessionId': session if fresh else None,
                          'lotSessionStartedUtc': datetime.now(timezone.utc).isoformat(),
                          'sourceStatus': source, 'gameHour': 2, 'gameMinute': 0, 'gamePaused': True,
                          'sims': [{'nid': 1, 'identity': {'firstName': 'Test', 'lastName': 'Sim'},
                                    'ageStage': 'adult', 'hungerBand': 'urgent'}] if fresh else []},
            'changes': events or [], 'controlRestriction': {'status': 'ok', 'restrictionActive': False}}


def notes(text):
    return {'significantEvents': [], 'relationships': [],
            'intentions': [{'text': text, 'source': 'intention'}], 'unresolved': []}


class ContinuityTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.addCleanup(self.store.db.close)
        self.memory = Continuity(self.store, 'G001')

    def test_neighborhood_and_household_isolation(self):
        self.store.write_notes('G001', 2, notes('G001 test intention'), 0)
        self.store.write_notes('N001', 2, notes('Pleasantview intention'), 0)
        self.memory.ingest(observation())
        self.assertTrue(self.memory.view(observation())['record']['testOnly'])
        self.assertEqual(self.store.read('G001', 3)['notes']['intentions'], [])
        self.assertEqual(self.store.read('N001', 2)['notes']['intentions'][0]['text'], 'Pleasantview intention')
        self.assertFalse(self.store.read('N001', 2)['testOnly'])

    def test_departure_rearrival_and_stale_samples(self):
        self.store.write_notes('G001', 2, notes('Keep this intention'), 0)
        self.memory.ingest(observation())
        self.memory.ingest(observation(fresh=False))
        self.assertIsNone(self.store.read('G001', 2)['visits'][0]['endedUtc'])
        self.memory.ingest(observation(fresh=False, source='unavailable'))
        record = self.store.read('G001', 2)
        self.assertEqual(record['visits'][0]['endReason'], 'left_lot_save_unknown')
        self.assertIsNone(record['visits'][0]['saveConfirmed'])
        self.memory.ingest(observation(3, 'visit-b'))
        self.assertEqual(self.memory.view(observation(3, 'visit-b'))['record']['notes']['intentions'], [])
        self.memory.ingest(observation(2, 'visit-c'))
        self.assertEqual(self.memory.view(observation(2, 'visit-c'))['record']['notes']['intentions'][0]['text'], 'Keep this intention')

    def test_restart_recovers_open_visit_without_claiming_save(self):
        self.memory.ingest(observation())
        self.store.write_notes('G001', 2, notes('Survives restart'), 0)
        restored = Store(':memory:')
        self.addCleanup(restored.db.close)
        self.store.db.backup(restored.db)
        memory = Continuity(restored, 'G001')
        memory.ingest(observation(session='new-process-visit'))
        record = restored.read('G001', 2)
        old = next(v for v in record['visits'] if v['id'] == 'visit-a')
        self.assertEqual(old['endReason'], 'observer_restarted_save_unknown')
        self.assertIsNone(old['saveConfirmed'])
        self.assertEqual(record['notes']['intentions'][0]['text'], 'Survives restart')

    def test_conflicting_writes_do_not_overwrite_notes(self):
        self.store.write_notes('G001', 2, notes('first'), 0)
        with self.assertRaises(ValueError):
            self.store.write_notes('G001', 2, notes('stale writer'), 0)
        self.assertEqual(self.store.read('G001', 2)['notes']['intentions'][0]['text'], 'first')
        with self.assertRaises(ValueError):
            self.store.read('../N001', 2)
        invalid = notes('x'); invalid['intentions'] *= 7
        with self.assertRaises(ValueError):
            validate_notes(invalid)

    def test_meaningful_events_deduplicated_and_bounded(self):
        event = {'kind': 'age_raw_changed', 'nid': 1, 'observation': {'sampledUtc': '2026-01-01T00:00:00Z'}}
        obs = observation(events=[event, {'kind': 'hunger_warning'}])
        self.memory.ingest(obs); self.memory.ingest(obs)
        self.assertEqual(len(self.store.read('G001', 2)['visits'][0]['developments']), 1)
        for i in range(30):
            event = copy.deepcopy(event); event['observation']['sampledUtc'] = str(i)
            self.memory.ingest(observation(events=[event]))
        self.assertEqual(len(self.store.read('G001', 2)['visits'][0]['developments']), 24)
        for i in range(10):
            self.memory.ingest(observation(session='next-' + str(i)))
        self.assertEqual(len(self.store.read('G001', 2)['visits']), 8)

    def test_decision_summary_uses_current_context_only(self):
        self.store.write_notes('G001', 2, notes('test intention'), 0)
        obs = observation(); self.memory.ingest(obs)
        summary = decision_summary(obs, self.memory.view(obs))
        self.assertTrue(summary['testOnly'])
        self.assertFalse(summary['autoPlay'])
        self.assertEqual(summary['sleepState'], 'unknown')
        self.assertIn('urgent_need', [c['kind'] for c in summary['concerns']])
        stale = observation(fresh=False)
        summary = decision_summary(stale, self.memory.view(stale))
        self.assertEqual(summary['intentions'], [])
        self.assertEqual(summary['concerns'][0]['kind'], 'telemetry_unavailable')

    def test_worker_failure_does_not_advance_cursor(self):
        worker = Worker(self.memory, lambda cursor: (_ for _ in ()).throw(ValueError('failed')))
        worker.tick()
        self.assertIsNone(worker.cursor)
        self.assertEqual(self.memory.view(observation())['status'], 'unavailable')


if __name__ == '__main__':
    unittest.main()
