import importlib.util
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
import urllib.request
import urllib.error
from preferences import decode


class PreferenceTests(unittest.TestCase):
    def test_observed_panels(self):
        for words, ons, offs in (
            ([768, 0, 2, 0, 0, 0], ['Facial Hair', 'Glasses'], ['Stink']),
            ([256, 4, 4, 0, 0, 0], ['Facial Hair', 'Brown Hair'], ['Fatness']),
            ([64, 16, 0, 8, 0, 0], ['Underwear', 'Custom Hair'], ['Black Hair']),
            ([64, 64, 0, 0, 0, 1], ['Underwear', 'Hard Worker'], ['Robots']),
        ):
            result = decode(words)
            self.assertEqual([e['label'] for e in result['turnOns']], ons)
            self.assertEqual([e['label'] for e in result['turnOffs']], offs)
            self.assertTrue(result['fullyLiveChecked'])

    def test_unknown_zero_and_role_specific_evidence(self):
        result = decode([0x8000, 0, 0, 0, 1, 1])
        self.assertEqual(result['unknownBits'], {'0xb6': 0x8000})
        self.assertEqual(result['turnOns'][0]['validation'], 'static_candidate')
        self.assertEqual(result['turnOffs'][0]['validation'], 'live_checked')
        self.assertFalse(result['fullyLiveChecked'])
        self.assertEqual(len(decode([0]*6)['warnings']), 2)
        for words in ([0]*4, [-1]*6, [65536]*6, [True]*6):
            with self.assertRaises(ValueError):
                decode(words)

    def test_endpoint_validation_and_read_failure(self):
        spec = importlib.util.spec_from_file_location('preference_test_api', Path(__file__).with_name('TS2Bridge-api.py'))
        api = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(api)
        context = patch.object(api, 'lot_snapshot', return_value={
            'fresh': True, 'gamePid': 123, 'sims': [{'nid': 255}], 'lotSessionId': 'test'})
        context.start()
        self.addCleanup(context.stop)
        handler = type('Handler', (api.ApiHandler,), {'preference_pid': 123})
        server = api.ExclusiveHTTPServer(('127.0.0.1', 0), handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def get(query):
            return urllib.request.urlopen('http://127.0.0.1:%d/v1/preferences%s' % (server.server_port, query))
        try:
            with patch.object(api, 'preference_snapshot', return_value={'status': 'ok', 'nid': 255}) as probe:
                for query in ('', '?nid=0', '?nid=-1', '?nid=65536', '?nid=1&nid=2', '?nid=1&extra=2'):
                    with self.assertRaises(urllib.error.HTTPError) as cm:
                        get(query)
                    self.assertEqual(cm.exception.code, 400)
                probe.assert_not_called()
                with get('?nid=255') as response:
                    self.assertEqual(json.load(response)['nid'], 255)
            with patch.object(api, 'preference_snapshot', side_effect=ValueError('Target missing')):
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    get('?nid=255')
                self.assertEqual(cm.exception.code, 503)
                self.assertEqual(json.load(cm.exception)['status'], 'unavailable')
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
