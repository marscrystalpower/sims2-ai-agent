import importlib.util
import json
from pathlib import Path
import threading
import unittest
import urllib.request
from unittest.mock import patch
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from restriction_alarm import AlarmState

spec = importlib.util.spec_from_file_location('bridge_api', Path(__file__).with_name('TS2Bridge-api.py'))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class HttpTests(unittest.TestCase):
    def test_independent_alarm_reads_and_observe(self):
        state = AlarmState()
        state.update(1, 3)
        handler = type('TestHandler', (api.ApiHandler,), {})
        handler.alarm = SimpleNamespace(state=state)
        handler.mods = Path('.')
        fixtures = [
            patch.object(api, 'household_snapshot', return_value={
                'sampledUtc': datetime.now(timezone.utc).isoformat(), 'status': 'lot',
                'currentFamily': 3, 'gamePaused': True, 'sims': []}),
            patch.object(api, 'relationship_snapshot', return_value={}),
            patch.object(api, 'change_page', return_value={
                'changes': [], 'scannedEvents': 0, 'nextCursor': None,
                'reset': False, 'historySkipped': False}),
        ]
        for fixture in fixtures:
            fixture.start()
            self.addCleanup(fixture.stop)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def get(path):
            with urllib.request.urlopen('http://127.0.0.1:%d%s' % (server.server_port, path)) as response:
                return json.load(response)
        try:
            first = get('/v1/alarms')
            self.assertEqual(first['alarms'], get('/v1/alarms')['alarms'])
            self.assertEqual(get('/v1/alarms?after=' + first['nextCursor'])['alarms'], [])
            obs = get('/v1/observe')
            self.assertTrue(obs['alarmEnabled'])
            self.assertEqual(obs['screenCheckReason'], 'build_buy_restriction')
            self.assertEqual(obs['latestAlarm']['id'], first['alarms'][0]['id'])
            state.update(0, 3, save_counter=0)
            obs = get('/v1/observe')
            self.assertEqual(obs['screenCheckReason'], 'save_gate_restriction')
            self.assertEqual(obs['alarmVersion'], 2)
            self.assertTrue(obs['controlRestriction']['saveGateRestricted'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
