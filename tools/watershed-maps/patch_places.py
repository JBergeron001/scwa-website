#!/usr/bin/env python3
"""Rebuild town labels and city-limit lines in finished map data from the Census Bureau's newest place list.

Only ACTIVE incorporated places (cities, towns, villages) get city-limit lines. Labels show those plus large
census-designated communities (25+ sq km of land, e.g. Metairie). Inactive or dissolved places are never shown.

Usage: python3 patch_places.py <data dir> [GEOID ...]
"""
import sys, os, json, glob, re, base64
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county import read_shp_zip, fetch, RAW, TIGER, Grid, make_valid, clean_name, box

YEAR = '2025'
CDP_MIN_LAND = 25e6  # square meters


def label_name(n):
    n = re.sub(r'^(Village|Town|City) of ', '', n)
    return clean_name(n)


_cache = {}


def places_for_state(st):
    if st not in _cache:
        rows = read_shp_zip(fetch(TIGER + f'TIGER{YEAR}/PLACE/tl_{YEAR}_{st}_place.zip', os.path.join(RAW, f'tl_{YEAR}_{st}_place.zip')))
        out = []
        for p, g in rows:
            fs = p['FUNCSTAT']
            active = fs in ('A', 'B', 'C') or (fs == 'F' and re.search(r'balance|metro|government', p['NAMELSAD'], re.I))
            big_cdp = fs == 'S' and p['ALAND'] >= CDP_MIN_LAND
            if not (active or big_cdp): continue  # inactive (I), fictitious (F) and small CDPs are skipped
            out.append({'name': label_name(p['NAME']), 'inc': bool(active), 'land': p['ALAND'], 'g': g, 'bounds': g.bounds})
        _cache[st] = out
    return _cache[st]


STATES = ['01', '05', '17', '18', '19', '20', '21', '22', '26', '27', '28', '29', '31', '38', '39', '40', '46', '47', '48', '55']
_bboxes = None


def states_touching(bb):
    global _bboxes
    if _bboxes is None:
        rows = read_shp_zip(fetch(TIGER + 'GENZ2023/shp/cb_2023_us_state_500k.zip', os.path.join(RAW, 'cb_2023_us_state_500k.zip')))
        _bboxes = {p['STATEFP']: g.bounds for p, g in rows}
    w, e, s, n = bb
    return [st for st, b in _bboxes.items() if not (b[2] < w or b[0] > e or b[3] < s or b[1] > n)]


def grid_from(D):
    w, e, s, n = D['bb']
    G = Grid.__new__(Grid)
    G.nx, G.ny = D['nx'], D['ny']; G.dlon = (e - w) / G.nx; G.dlat = (n - s) / G.ny
    G.bb = D['bb']; G.w, G.n = w, n; G.cellkm = D['cellkm']; G.lat0 = (s + n) / 2
    return G


def patch_file(f, detail):
    D = json.load(open(f))
    G = grid_from(D)
    c = np.frombuffer(base64.b64decode(D['c']), np.uint8).reshape(G.ny, G.nx)
    inside = ((c >> 3) & 1) > 0
    w, e, s, n = D['bb']; gb = box(w, s, e, n)
    cand = []
    for st in states_touching(D['bb']):
        for pl in places_for_state(st):
            b = pl['bounds']
            if b[2] < w or b[0] > e or b[3] < s or b[1] > n: continue
            if pl['g'].intersects(gb): cand.append(pl)
    # city limits: active incorporated places only
    munis = [pl for pl in cand if pl['inc']]
    r = np.frombuffer(base64.b64decode(D['r']), np.uint8).reshape(G.ny, G.nx).copy() if D.get('r') else np.zeros((G.ny, G.nx), np.uint8)
    r &= ~np.uint8((1 << 3) | (1 << 6))
    muni = G.fill([(make_valid(pl['g']), 1) for pl in munis]) > 0
    me = np.zeros_like(muni); me[1:-1, 1:-1] = muni[1:-1, 1:-1] & ~(muni[:-2, 1:-1] & muni[2:, 1:-1] & muni[1:-1, :-2] & muni[1:-1, 2:])
    mid = G.fill([(make_valid(pl['g']), (i % 250) + 1) for i, pl in enumerate(munis)])
    me[1:-1, 1:-1] |= (mid[1:-1, 1:-1] > 0) & ((mid[1:-1, 1:-1] != mid[:-2, 1:-1]) | (mid[1:-1, 1:-1] != mid[1:-1, :-2])) & (mid[:-2, 1:-1] > 0) & (mid[1:-1, :-2] > 0)
    r |= (me & inside).astype(np.uint8) << 3
    r |= (muni & inside).astype(np.uint8) << 6
    D['r'] = base64.b64encode(np.ascontiguousarray(r).tobytes()).decode()
    # labels
    labs, seen = [], set()
    for pl in sorted(cand, key=lambda q: -q['land']):
        if pl['name'] in seen: continue
        g = make_valid(pl['g']).intersection(gb)
        if g.is_empty: continue
        pt = g.representative_point()
        j = int((pt.x - w) / G.dlon); i = int((n - pt.y) / G.dlat)
        if not (0 <= i < G.ny and 0 <= j < G.nx and inside[i, j]): continue
        seen.add(pl['name'])
        labs.append([pl['name'], round((pt.x - w) / (G.dlon * G.nx), 4), round((n - pt.y) / (G.dlat * G.ny), 4)])
    D['cities'] = labs[:(36 if detail else 18)]
    json.dump(D, open(f, 'w'), separators=(',', ':'))
    return len(munis), len(D['cities'])


if __name__ == '__main__':
    root = sys.argv[1]
    ids = sys.argv[2:] or sorted(os.path.basename(d) for d in glob.glob(os.path.join(root, '[0-9]*')))
    for gid in ids:
        d = os.path.join(root, gid)
        tot = 0
        for f in sorted(glob.glob(os.path.join(d, '*.json'))):
            if f.endswith(('notes.json', 'info.json')): continue
            m, k = patch_file(f, not f.endswith('county.json'))
            if f.endswith('county.json'): first = [x[0] for x in json.load(open(f))['cities'][:4]]
            tot += 1
        print(gid, tot, 'grids', first, flush=True)
