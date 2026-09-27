import unittest
from restriction_alarm import AlarmState


class AlarmTests(unittest.TestCase):
    def test_grim_recorded_samples(self):
        state = AlarmState()
        # Recorded scalar readings, stripped of machine and household metadata.
        for raw in ({'buildBuyBlockerCountRaw': 0, 'offset84Signed': 0},
                    {'buildBuyBlockerCountRaw': 0, 'offset84Signed': 1}):
            state.update(raw['buildBuyBlockerCountRaw'], 3, save_counter=raw['offset84Signed'])
        result = state.snapshot()
        self.assertEqual(len(result['alarms']), 1)
        self.assertEqual(result['alarms'][0]['newReasons'], ['save_gate_restriction'])
        self.assertFalse(result['controlRestriction']['restrictionActive'])

    def test_independent_triggers_and_unknown_save(self):
        state = AlarmState()
        state.update(1, 3, save_counter=1)
        state.update(1, 3, save_counter=0)
        state.update(None, error='failed')
        self.assertIsNone(state.snapshot()['controlRestriction']['saveGateRestricted'])
        state.update(1, 3, save_counter=-1)
        self.assertEqual(len(state.snapshot()['alarms']), 2)
        state.update(1, 3, save_counter=1)
        state.update(1, 3, save_counter=0)
        self.assertEqual(len(state.snapshot()['alarms']), 3)

    def test_fire_phone_burglar_replay(self):
        state = AlarmState()
        for count in (0, 1, 1, 0, 0, 1, 0):
            state.update(count, 3)
        result = state.snapshot()
        self.assertEqual(len(result['alarms']), 2)
        self.assertFalse(result['controlRestriction']['restrictionActive'])
        self.assertTrue(all(a['bypassQuietHours'] for a in result['alarms']))
        self.assertEqual(state.snapshot(result['nextCursor'])['alarms'], [])

    def test_failed_read_cannot_clear_or_duplicate(self):
        state = AlarmState()
        state.update(1, 3)
        state.update(None, error='read failed')
        self.assertIsNone(state.snapshot()['controlRestriction']['restrictionActive'])
        state.update(1, 3)
        self.assertEqual(len(state.snapshot()['alarms']), 1)
        state.update(0, 3)
        state.update(1, 3)
        self.assertEqual(len(state.snapshot()['alarms']), 2)

    def test_initial_active_and_family_change(self):
        state = AlarmState()
        state.update(2, 3)
        self.assertTrue(state.snapshot()['latestAlarm']['detectedOnInitialSample'])
        state.update(3, 3)
        self.assertEqual(len(state.snapshot()['alarms']), 1)
        state.update(1, 4)
        self.assertEqual(len(state.snapshot()['alarms']), 2)

    def test_stale_sample_is_unknown(self):
        state = AlarmState()
        state.update(0, 3)
        state.last_tick -= 16
        self.assertIsNone(state.snapshot()['controlRestriction']['restrictionActive'])

    def test_cursor_reset_and_retention(self):
        state = AlarmState()
        state.update(1, 3)
        cursor = state.snapshot()['nextCursor']
        for _ in range(257):
            state.update(0, 3)
            state.update(1, 3)
        result = state.snapshot(cursor)
        self.assertTrue(result['reset'])
        self.assertEqual(len(result['alarms']), 256)
        self.assertTrue(state.snapshot('old:1')['reset'])
        with self.assertRaises(ValueError):
            state.snapshot('bad')


if __name__ == '__main__':
    unittest.main()
