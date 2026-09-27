"""Conservative mutual chemistry estimates from paired attraction caches."""


def rating(first, second):
    # Installed global BHAV114/BCON115: integer average, then conditional penalty.
    score = int((first + second) / 2)
    if min(first, second) < -25 and score > 0:
        score -= 50
    return -1 if score <= -25 else 0 if score < 1 else 1 if score < 35 else 2 if score < 90 else 3


def attach(pairs, sims, supported=True):
    indexed = {}
    for pair in pairs:
        indexed.setdefault((pair['viewerNid'], pair['otherNid']), []).append(pair)
    ages = {sim['nid']: sim.get('ageStage') for sim in sims}

    def unknown(reason):
        return {'status': 'unknown', 'category': None, 'bolts': None, 'reason': reason}

    def attraction(pair):
        slots = pair.get('rawSlots')
        if (not isinstance(slots, list) or len(slots) not in (9, 10) or
                (len(slots) == 10 and (type(slots[9]) is not int or slots[9] not in (0, 1))) or
                type(slots[8]) is not int or not -32768 <= slots[8] <= 32767):
            return None
        return slots[8]

    for pair in pairs:
        a, b = pair['viewerNid'], pair['otherNid']
        forward, reverse = indexed[(a, b)], indexed.get((b, a), [])
        if not supported:
            value = unknown('unsupported_bridge_version')
        elif len(forward) != 1 or len(reverse) != 1:
            value = unknown('missing_or_ambiguous_direction')
        else:
            other = reverse[0]
            age_a, age_b = ages.get(a), ages.get(b)
            age_ok = ((age_a in ('adult', 'elder') and age_b in ('adult', 'elder')) or
                      age_a == age_b == 'teen')
            scores = attraction(pair), attraction(other)
            if not age_ok or a == b:
                value = unknown('age_eligibility_unverified')
            elif pair.get('hasFamilyTie') is not False or other.get('hasFamilyTie') is not False:
                value = unknown('family_eligibility_unverified')
            elif pair.get('known') is not True or other.get('known') is not True:
                value = unknown('acquaintance_unverified')
            elif None in scores:
                value = unknown('invalid_attraction_layout')
            else:
                bolts = rating(*scores)
                value = {'status': 'estimated', 'category': {-1: 'poor', 0: 'none',
                         1: 'mild', 2: 'medium', 3: 'strong'}[bolts], 'bolts': bolts,
                         'validation': 'static_formula_with_live_examples',
                         'source': 'paired_attraction_cache'}
            # No hidden average is exposed as a partner-ranking signal.
        pair['mutualChemistry'] = value
