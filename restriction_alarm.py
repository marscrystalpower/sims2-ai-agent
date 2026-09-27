"""Read-only restriction watcher. No screenshots, game input, or model calls."""
import copy
import json
import time
import uuid
from datetime import datetime, timezone
from threading import Event, Lock, Thread
from collections import deque

from control_probe import sample


def utc():
    return datetime.now(timezone.utc).isoformat()


class AlarmState:
    def __init__(self):
        self.lock = Lock()
        self.session = uuid.uuid4().hex
        self.sequence = 0
        self.events = deque(maxlen=256)
        self.context = None
        self.active = None
        self.reasons = set()
        self.latest = None
        self.current = {'status': 'unknown', 'blockerCount': None,
                        'restrictionActive': None, 'error': 'No sample yet'}
        self.last_tick = None

    def leave_lot(self):
        """Confirmed lot exit retires the prior visit's alarm history."""
        with self.lock:
            if self.context is not None:
                self.session, self.sequence = uuid.uuid4().hex, 0
                self.events.clear()
                self.latest = None
                self.context = self.active = None
                self.reasons = set()
            self.current = {'status': 'unknown', 'blockerCount': None,
                            'restrictionActive': None, 'saveEnableCounterRaw': None,
                            'saveGateRestricted': None, 'reasons': None,
                            'error': 'No active household'}

    def update(self, count, context=None, error=None, *, save_counter=1):
        with self.lock:
            now = utc()
            self.last_tick = time.monotonic()
            if error is not None:
                # Preserve the episode across failures; never clear or rearm it.
                self.current = {'status': 'unknown', 'sampledUtc': now,
                                'blockerCount': None, 'restrictionActive': None,
                                'saveEnableCounterRaw': None, 'saveGateRestricted': None,
                                'reasons': None,
                                'error': error}
                return
            if type(count) is not int or not 0 <= count <= 8192:
                raise ValueError('Invalid blocker count')
            if type(save_counter) is not int or not -(2**31) <= save_counter < 2**31:
                raise ValueError('Invalid save counter')
            if context != self.context:
                self.session, self.sequence = uuid.uuid4().hex, 0
                self.events.clear()
                self.latest = None
                self.context, self.active = context, None
                self.reasons = set()
            reasons = set()
            if count > 0:
                reasons.add('build_buy_restriction')
            if save_counter <= 0:
                reasons.add('save_gate_restriction')
            active = bool(reasons)
            added = reasons - self.reasons
            if added:
                self.sequence += 1
                self.latest = {'id': '%s:%d' % (self.session, self.sequence),
                               'sequence': self.sequence, 'sampledUtc': now,
                               'kind': ('build_buy_restriction_detected' if added == {'build_buy_restriction'}
                                        else 'save_gate_restriction_detected' if added == {'save_gate_restriction'}
                                        else 'control_restriction_detected'),
                               'reasons': sorted(reasons), 'newReasons': sorted(added),
                               'saveEnableCounterRaw': save_counter,
                               'priority': 'high', 'bypassQuietHours': True,
                               'action': 'inspect_scene', 'cause': 'unknown',
                               'currentFamily': context, 'blockerCount': count,
                               'detectedOnInitialSample': self.active is None}
                self.events.append(dict(self.latest))
            self.active = active
            self.reasons = reasons
            self.current = {'status': 'ok', 'sampledUtc': now,
                            'blockerCount': count, 'restrictionActive': active,
                            'saveEnableCounterRaw': save_counter,
                            'saveGateRestricted': save_counter <= 0,
                            'reasons': sorted(reasons),
                            'currentFamily': context, 'error': None}

    def snapshot(self, after=None):
        with self.lock:
            current = dict(self.current)
            if self.last_tick is None or time.monotonic() - self.last_tick > 15:
                current.update(status='unknown', blockerCount=None,
                               restrictionActive=None, saveEnableCounterRaw=None,
                               saveGateRestricted=None, reasons=None, error='Watcher sample stale')
            reset = False
            sequence = 0
            if after is not None:
                try:
                    session, number = after.split(':')
                    sequence = int(number)
                    if sequence < 0:
                        raise ValueError()
                except (ValueError, AttributeError):
                    raise ValueError('Invalid alarm cursor') from None
                reset = (session != self.session or sequence > self.sequence or
                         bool(self.events and sequence < self.events[0]['sequence'] - 1))
                if reset:
                    sequence = 0
            return copy.deepcopy({
                'api': 1, 'alarmVersion': 2, 'experimental': True, 'controlRestriction': current,
                'alarms': [e for e in self.events if e['sequence'] > sequence],
                'latestAlarm': self.latest,
                'nextCursor': '%s:%d' % (self.session, self.sequence),
                'reset': reset, 'retention': 256,
                'consumerAction': 'Inspect once per unseen alarm ID; bypass quiet hours. '
                                  'Historical alarms may already be resolved; check current state.'})


class RestrictionWatcher:
    def __init__(self, mods, pid, exe, reader=sample, context_observer=None):
        self.mods, self.pid, self.exe, self.reader = mods, pid, exe, reader
        self.context_observer = context_observer
        self.state = AlarmState()
        self.stop_event = Event()
        self.thread = Thread(target=self.run, name='restriction-alarm', daemon=True)

    def context(self):
        state = json.loads((self.mods / 'TS2Bridge-state.json').read_text(encoding='utf-8'))
        if state.get('status') != 'lot':
            if self.context_observer:
                self.context_observer(state, False)
            self.state.leave_lot()
            raise ValueError('No active household')
        when = datetime.fromisoformat(state['sampledUtc'].replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - when).total_seconds()
        if state.get('status') != 'lot' or not -5 <= age <= 15:
            raise ValueError('Bridge lot state unavailable or stale')
        if state.get('pid') != self.pid:
            self.state.leave_lot()
            raise ValueError('Game process changed; restart the API')
        if self.context_observer:
            self.context_observer(state, True)
        family = state.get('currentFamily')
        if type(family) is not int:
            raise ValueError('Unknown family')
        return family

    def tick(self):
        try:
            family = self.context()
            raw = self.reader(self.pid, self.exe)
            if self.context() != family:
                raise ValueError('Household changed during memory read')
            self.state.update(raw['buildBuyBlockerCountRaw'], family,
                              save_counter=raw['offset84Signed'])
        except Exception as error:
            self.state.update(None, error='%s: %s' % (type(error).__name__, error))

    def run(self):
        while not self.stop_event.is_set():
            self.tick()
            self.stop_event.wait(2)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=5)
