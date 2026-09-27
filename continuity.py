"""Local household memory. No game writes, model calls, or invented narratives."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from threading import Event, RLock, Thread

DEFAULT_DIRECTORY = Path(__file__).resolve().parent / 'continuity-data'
NOTE_LIMITS = {'significantEvents': 24, 'relationships': 12, 'intentions': 6, 'unresolved': 8}
SOURCES = {'user_report', 'visual_observation', 'telemetry', 'interpretation', 'intention'}
MEANINGFUL = {'household_joined', 'household_left', 'household_death_indicated',
              'new_household_baby_observed', 'age_raw_changed', 'body_flags_raw_changed',
              'relationship_flag_removed', 'aspiration_bit_added', 'job_level_raw_changed'}


def utc():
    return datetime.now(timezone.utc).isoformat()


def validate_key(neighborhood, family):
    if not isinstance(neighborhood, str) or not re.fullmatch(r'[A-Z][0-9]{3}', neighborhood):
        raise ValueError('Use an explicit neighborhood code such as G001 or N001')
    if type(family) is not int or not 0 < family < 32767:
        raise ValueError('Invalid household ID')


def validate_notes(notes):
    if not isinstance(notes, dict) or set(notes) != set(NOTE_LIMITS):
        raise ValueError('Notes must contain significantEvents, relationships, intentions and unresolved')
    for field, limit in NOTE_LIMITS.items():
        entries = notes[field]
        if not isinstance(entries, list) or len(entries) > limit:
            raise ValueError('Too many entries in ' + field)
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != {'text', 'source'}:
                raise ValueError('Each note requires text and source')
            if not isinstance(entry['text'], str) or not 1 <= len(entry['text'].strip()) <= 600:
                raise ValueError('Note text must contain 1 to 600 characters')
            if not isinstance(entry['source'], str) or entry['source'] not in SOURCES:
                raise ValueError('Invalid note source')
    return notes


class Store:
    def __init__(self, path):
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.db = sqlite3.connect(str(path), timeout=5, check_same_thread=False)
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS households (
                neighborhood TEXT NOT NULL, family INTEGER NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0, notes TEXT NOT NULL,
                PRIMARY KEY(neighborhood, family));
            CREATE TABLE IF NOT EXISTS visits (
                id TEXT PRIMARY KEY, neighborhood TEXT NOT NULL, family INTEGER NOT NULL,
                started TEXT NOT NULL, ended TEXT, reason TEXT,
                roster TEXT NOT NULL, events TEXT NOT NULL);
        ''')
        self.db.commit()

    def _ensure(self, neighborhood, family):
        validate_key(neighborhood, family)
        self.db.execute('INSERT OR IGNORE INTO households VALUES (?,?,0,?)',
                        (neighborhood, family, json.dumps({field: [] for field in NOTE_LIMITS})))

    def read(self, neighborhood, family):
        validate_key(neighborhood, family)
        with self.lock:
            row = self.db.execute('SELECT revision,notes FROM households WHERE neighborhood=? AND family=?',
                                  (neighborhood, family)).fetchone()
            visits = self.db.execute('SELECT id,started,ended,reason,roster,events FROM visits '
                                    'WHERE neighborhood=? AND family=? ORDER BY started DESC, rowid DESC LIMIT 8',
                                    (neighborhood, family)).fetchall()
            return {'neighborhood': neighborhood, 'family': family, 'testOnly': neighborhood == 'G001',
                    'identityScope': 'operator_asserted_neighborhood_and_save',
                    'revision': row[0] if row else 0,
                    'notes': json.loads(row[1]) if row else {field: [] for field in NOTE_LIMITS},
                    'visits': [{'id': v[0], 'startedUtc': v[1], 'endedUtc': v[2], 'endReason': v[3],
                                'saveConfirmed': None, 'lastObservedMembers': json.loads(v[4]),
                                'developments': json.loads(v[5]),
                                'recap': ('Visit still open. ' if v[2] is None else 'Visit ended. ') +
                                         ('; '.join(e.get('text', e['kind'].replace('_', ' '))
                                                    for e in json.loads(v[5])[-6:]) or
                                          'No noteworthy developments captured.') + ' Save status unknown.'}
                               for v in visits]}

    def write_notes(self, neighborhood, family, notes, expected_revision):
        validate_key(neighborhood, family)
        validate_notes(notes)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Expected revision must be a nonnegative integer')
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._ensure(neighborhood, family)
            cursor = self.db.execute('UPDATE households SET notes=?,revision=revision+1 '
                                    'WHERE neighborhood=? AND family=? AND revision=?',
                                    (json.dumps(notes), neighborhood, family, expected_revision))
            if cursor.rowcount != 1:
                raise ValueError('Notes changed; reread the household before updating')
        return self.read(neighborhood, family)

    def open_visit(self, neighborhood, family, session, started, roster):
        with self.lock, self.db:
            self._ensure(neighborhood, family)
            self.db.execute('INSERT OR IGNORE INTO visits VALUES (?,?,?,?,NULL,NULL,?,?)',
                            (session, neighborhood, family, started, json.dumps(roster), '[]'))
            # Bound active recall/storage per household; curated notes survive.
            self.db.execute('DELETE FROM visits WHERE neighborhood=? AND family=? AND id NOT IN '
                            '(SELECT id FROM visits WHERE neighborhood=? AND family=? ORDER BY started DESC, rowid DESC LIMIT 8)',
                            (neighborhood, family, neighborhood, family))

    def recover(self, neighborhood):
        with self.lock, self.db:
            self.db.execute('UPDATE visits SET ended=?,reason=? WHERE neighborhood=? AND ended IS NULL',
                            (utc(), 'observer_restarted_save_unknown', neighborhood))

    def close_visit(self, session, reason):
        with self.lock, self.db:
            self.db.execute('UPDATE visits SET ended=?,reason=? WHERE id=? AND ended IS NULL',
                            (utc(), reason, session))

    def update_visit(self, session, roster, events):
        with self.lock, self.db:
            row = self.db.execute('SELECT roster,events FROM visits WHERE id=?', (session,)).fetchone()
            if not row:
                raise ValueError('Visit missing')
            previous = json.loads(row[1])
            for event in events:
                if event not in previous:
                    previous.append(event)
            new_events, new_roster = json.dumps(previous[-24:]), json.dumps(roster)
            if (new_roster, new_events) != row:
                self.db.execute('UPDATE visits SET roster=?,events=? WHERE id=?', (new_roster, new_events, session))


def roster_of(observation):
    return [{'nid': sim['nid'], 'name': ' '.join(filter(None, [
        (sim.get('identity') or {}).get('firstName'), (sim.get('identity') or {}).get('lastName')])) or None,
        'ageStage': sim.get('ageStage')} for sim in observation['household']['sims']]


class Continuity:
    def __init__(self, store, neighborhood):
        validate_key(neighborhood, 1)
        self.store, self.neighborhood = store, neighborhood
        self.lock = RLock()
        self.session = None
        self.family = None
        self.recovered = False
        self.error = None

    def ingest(self, observation):
        with self.lock:
            if not self.recovered:
                self.store.recover(self.neighborhood)
                self.recovered = True
            household = observation['household']
            if not observation.get('fresh'):
                if household.get('sourceStatus') not in (None, 'lot') and self.session:
                    self.store.close_visit(self.session, 'left_lot_save_unknown')
                    self.session = self.family = None
                return
            session, family = household.get('lotSessionId'), household.get('currentFamily')
            if not session:
                raise ValueError('Missing lot session')
            roster = roster_of(observation)
            if session != self.session:
                if self.session:
                    self.store.close_visit(self.session, 'context_changed_save_unknown')
                self.store.open_visit(self.neighborhood, family, session,
                                      household['lotSessionStartedUtc'], roster)
                self.session, self.family = session, family
            events = []
            for change in observation.get('changes', []):
                if change.get('kind') not in MEANINGFUL:
                    continue
                event = change.get('observation') or {}
                events.append({'kind': change['kind'], 'nid': change.get('nid'),
                               'text': 'Sim ' + str(change.get('nid')) + ': ' + change['kind'].replace('_', ' '),
                               'otherNid': change.get('otherNid'), 'source': 'telemetry',
                               'sampledUtc': event.get('sampledUtc'),
                               'details': {k: event[k] for k in (
                                   'previousAgeRaw', 'ageRaw', 'previousFamilyNumber', 'familyNumber',
                                   'previousJobLevelRaw', 'jobLevelRaw', 'lostKnownFlagBits') if k in event},
                               'gameTime': [event.get('gameHour'), event.get('gameMinute')]})
            alarm = observation.get('latestAlarm')
            restriction = observation.get('controlRestriction') or {}
            if alarm and restriction.get('restrictionActive') is True:
                events.append({'kind': 'restriction_alarm_cause_unknown', 'alarmId': alarm['id'],
                               'source': 'telemetry', 'sampledUtc': alarm.get('sampledUtc')})
            self.store.update_visit(session, roster, events)
            self.error = None

    def view(self, observation):
        with self.lock:
            household = observation['household']
            if not observation.get('fresh'):
                return {'status': 'unavailable', 'record': None, 'reason': 'No fresh active household'}
            if self.error:
                return {'status': 'unavailable', 'record': None, 'reason': self.error}
            return {'status': 'ok', 'lotSessionId': household.get('lotSessionId'),
                    'record': self.store.read(self.neighborhood, household['currentFamily'])}


def decision_summary(observation, continuity):
    household = observation['household']
    fresh = observation.get('fresh') is True
    concerns = []
    restriction = observation.get('controlRestriction') or {}
    if not fresh:
        concerns.append({'kind': 'telemetry_unavailable', 'action': 'wait_for_fresh_household'})
    else:
        if restriction.get('status') != 'ok':
            concerns.append({'kind': 'restriction_visibility_unknown'})
        elif restriction.get('restrictionActive'):
            concerns.append({'kind': 'restriction_alarm', 'action': 'inspect_scene',
                             'alarmId': (observation.get('latestAlarm') or {}).get('id')})
        for sim in household['sims']:
            for need in ('hunger', 'bladder', 'energy'):
                if sim.get(need + 'Band') == 'urgent':
                    concerns.append({'kind': 'urgent_need', 'nid': sim['nid'], 'need': need})
        if household.get('gamePaused'):
            concerns.append({'kind': 'paused', 'action': 'determine_reason_respect_manual_pause'})
    record = continuity.get('record') or {}
    notes = record.get('notes') or {}
    current_visit = next((v for v in record.get('visits', []) if v['id'] == household.get('lotSessionId')), {})
    return {'api': 1, 'fresh': fresh, 'lotSessionId': household.get('lotSessionId'),
            'neighborhood': record.get('neighborhood'), 'family': household.get('currentFamily'),
            'testOnly': record.get('testOnly'), 'continuityStatus': continuity['status'],
            'concerns': concerns[:24], 'intentions': notes.get('intentions', []),
            'unresolved': notes.get('unresolved', []),
            'recentDevelopments': current_visit.get('developments', [])[-6:],
            'gameTime': [household.get('gameHour'), household.get('gameMinute')],
            'routineReview': 'consider_only_when_useful' if fresh else 'defer',
            'sleepState': 'unknown', 'autoPlay': False}


class Worker:
    def __init__(self, continuity, observer):
        self.continuity, self.observer = continuity, observer
        self.cursor = None
        self.stop_event = Event()
        self.thread = Thread(target=self.run, name='household-continuity', daemon=True)

    def tick(self):
        try:
            observation = self.observer(self.cursor)
            self.continuity.ingest(observation)
            self.cursor = observation.get('nextCursor')
        except Exception as error:
            self.continuity.error = str(error)

    def run(self):
        while not self.stop_event.is_set():
            self.tick()
            self.stop_event.wait(2)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument('--neighborhood', required=True)
    parser.add_argument('--family', type=int, required=True)
    parser.add_argument('--input', type=Path, help='Complete replacement notes JSON; omit to read')
    parser.add_argument('--expected-revision', type=int)
    args = parser.parse_args()
    store = Store(args.directory / 'households.sqlite3')
    if args.input:
        result = store.write_notes(args.neighborhood, args.family,
                                   json.loads(args.input.read_text(encoding='utf-8-sig')),
                                   args.expected_revision)
    else:
        result = store.read(args.neighborhood, args.family)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

