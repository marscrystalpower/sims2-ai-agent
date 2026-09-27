"""On-demand preference decoding. Static candidates are never silently verified."""
from datetime import datetime, timezone
from preference_probe import sample

BANKS = (
    ('Cologne', 'Stink', 'Fatness', 'Fitness', 'Formal Wear', 'Swim Wear',
     'Underwear', 'Vampirism', 'Facial Hair', 'Glasses', 'Makeup',
     'Full Face Makeup', 'Hats', 'Jewelry'),
    ('Blonde Hair', 'Red Hair', 'Brown Hair', 'Black Hair', 'Custom Hair',
     'Grey Hair', 'Hard Worker', 'Unemployed', 'Logical', 'Charismatic',
     'Great Cook', 'Mechanical', 'Creative', 'Athletic', 'Good at Cleaning', 'Zombiism'),
    ('Robots', 'Plantsimism', 'Lycanthropy', 'Witchiness'),
)
# Evidence is role-specific: a checked turn-off is not a checked turn-on.
CHECKED = {
    'turnOns': {'Facial Hair', 'Glasses', 'Brown Hair', 'Underwear', 'Custom Hair', 'Hard Worker'},
    'turnOffs': {'Stink', 'Fatness', 'Black Hair', 'Robots'},
}
INDICES = ('0xb6', '0xb7', '0xb8', '0xb9', '0xc9', '0xca')


def decode(words):
    if (len(words) != 6 or any(type(w) is not int or not 0 <= w <= 65535 for w in words)):
        raise ValueError('Expected six unsigned 16-bit preference words')
    result = {'rawWords': dict(zip(INDICES, words)), 'turnOns': [], 'turnOffs': [],
              'unknownBits': {}, 'warnings': []}
    for role, positions in (('turnOns', (0, 1, 4)), ('turnOffs', (2, 3, 5))):
        count = 0
        for bank, (labels, position) in enumerate(zip(BANKS, positions), 1):
            word = words[position]
            count += word.bit_count()
            for bit, label in enumerate(labels):
                if word & (1 << bit):
                    result[role].append({'label': label, 'bank': bank, 'mask': 1 << bit,
                        'validation': 'live_checked' if label in CHECKED[role] else 'static_candidate'})
            unknown = word & ~((1 << len(labels)) - 1)
            if unknown:
                result['unknownBits'][INDICES[position]] = unknown
        if count != (2 if role == 'turnOns' else 1):
            result['warnings'].append(role + ': unexpected number of set bits; do not assume complete preferences')
    result['fullyLiveChecked'] = (not result['unknownBits'] and not result['warnings'] and
        all(e['validation'] == 'live_checked' for role in CHECKED for e in result[role]))
    return result


def snapshot(pid, exe, nid):
    raw = sample(pid, exe, nid)
    return {'api': 1, 'status': 'ok', 'sampledUtc': datetime.now(timezone.utc).isoformat(),
            'nid': raw['nid'], 'family': raw['family'], 'ageRaw': raw['ageRaw'],
            'source': 'guarded_read_only_memory', 'decoderVersion': 1,
            'coverage': 'requested_loaded_sim_only', **decode(raw['candidateWords'])}
