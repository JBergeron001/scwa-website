#!/usr/bin/env python3
"""Rebuild watershed-maps/manifest.json and the Midwest county locator after adding counties.
Usage: python3 build_manifest.py <site root>"""
import sys, os, json, glob
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_county import read_shp_zip, fetch, RAW, TIGER
root = sys.argv[1]; wm = os.path.join(root, 'watershed-maps')
EXCLUDE = set()
# Maps made elsewhere and copied into the library (own page, own data)
EXTRA_MAPS = [{'geoid': '29183', 'name': 'St. Charles County', 'state': 'Missouri', 'url': 'watershed-maps/st-charles/',
               'watersheds': 7, 'subwatersheds': 26, 'areakm': 1525.9, 'thumb': 'watershed-maps/thumbs/29183.jpg', 'includes': []}]
MIDWEST = {'29': 'Missouri', '17': 'Illinois', '19': 'Iowa', '20': 'Kansas', '31': 'Nebraska', '27': 'Minnesota', '55': 'Wisconsin',
           '18': 'Indiana', '39': 'Ohio', '26': 'Michigan', '38': 'North Dakota', '46': 'South Dakota',
           # Mississippi River states to the south, plus Texas
           '21': 'Kentucky', '47': 'Tennessee', '05': 'Arkansas', '28': 'Mississippi', '22': 'Louisiana', '48': 'Texas', '40': 'Oklahoma', '01': 'Alabama'}
# Metro area each county belongs to, shown on the gallery cards and used by the search box. Add one line per new county.
METRO = {'29189': 'St. Louis', '29095': 'Kansas City', '31055': 'Omaha', '19153': 'Des Moines', '29019': 'Columbia',
         '29077': 'Springfield', '17167': 'Springfield', '17031': 'Chicago', '27053': 'Minneapolis-St. Paul',
         '18097': 'Indianapolis', '39049': 'Columbus', '55079': 'Milwaukee', '26163': 'Detroit', '20173': 'Wichita',
         '31109': 'Lincoln', '39061': 'Cincinnati', '39035': 'Cleveland', '55025': 'Madison', '26081': 'Grand Rapids',
         '19163': 'Quad Cities', '46099': 'Sioux Falls', '29031': 'Cape Girardeau', '29183': 'St. Charles and West Alton',
         '01073': 'Birmingham', '01097': 'Mobile', '40109': 'Oklahoma City', '40143': 'Tulsa', '48201': 'Houston',
         '48113': 'Dallas', '48439': 'Fort Worth', '48029': 'San Antonio', '48453': 'Austin', '21111': 'Louisville',
         '05119': 'Little Rock'}
# Mississippi River Cities and Towns Initiative member cities, by county
_mr = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mrcti.json')
if os.path.exists(_mr): METRO = {**json.load(open(_mr)), **METRO}
maps = []
for f in sorted(glob.glob(os.path.join(wm, 'data', '*', 'info.json'))):
    i = json.load(open(f))
    if i['geoid'] in EXCLUDE: continue
    i.pop('outline', None)
    i['metro'] = METRO.get(i['geoid'], '')
    i['thumb'] = f"watershed-maps/thumbs/{i['geoid']}.jpg" if os.path.exists(os.path.join(wm, 'thumbs', i['geoid'] + '.jpg')) else ''
    maps.append(i)
for x in EXTRA_MAPS:
    x = dict(x); x['metro'] = METRO.get(x['geoid'], ''); maps.append(x)
maps.sort(key=lambda r: (r['state'], r['name'].replace('City of ', '')))
json.dump({'updated': __import__('datetime').date.today().isoformat(), 'maps': maps}, open(os.path.join(wm, 'manifest.json'), 'w'), indent=1)
# Midwest county locator: simplified SVG paths in an equal-ish projection
import math
counties = read_shp_zip(fetch(TIGER + 'GENZ2023/shp/cb_2023_us_county_500k.zip', os.path.join(RAW, 'cb_2023_us_county_500k.zip')))
lat0 = 38.0; kx = math.cos(math.radians(lat0))
feats = [(p, g) for p, g in counties if p['STATEFP'] in MIDWEST]
allb = [g.bounds for p, g in feats]
W, S, E, N = min(b[0] for b in allb), min(b[1] for b in allb), max(b[2] for b in allb), max(b[3] for b in allb)
sc = 1000 / ((E - W) * kx)
def pt(x, y): return f"{(x - W) * kx * sc:.1f} {(N - y) * sc:.1f}"
out = []
for p, g in feats:
    g = g.simplify(0.016, preserve_topology=True)
    polys = [g] if g.geom_type == 'Polygon' else list(getattr(g, 'geoms', []))
    d = ''
    for pg in polys:
        if pg.geom_type != 'Polygon' or pg.area < 1e-5: continue
        cs = list(pg.exterior.coords)
        d += 'M' + 'L'.join(pt(x, y) for x, y in cs[:-1]) + 'Z'
    if d:
        nm = p['NAMELSAD']; nm = 'City of ' + nm[:-5] if nm.endswith(' city') else nm
        out.append(['', '', '', d, 1] if p['GEOID'] in EXCLUDE else [p['GEOID'], nm, MIDWEST[p['STATEFP']], d])
states = []
for p, g in read_shp_zip(fetch(TIGER + 'GENZ2023/shp/cb_2023_us_state_500k.zip', os.path.join(RAW, 'cb_2023_us_state_500k.zip'))):
    if p['STATEFP'] not in MIDWEST: continue
    g = g.simplify(0.01, preserve_topology=True); d = ''
    for pg in ([g] if g.geom_type == 'Polygon' else list(g.geoms)):
        if pg.area < 1e-3: continue
        d += 'M' + 'L'.join(pt(x, y) for x, y in list(pg.exterior.coords)[:-1]) + 'Z'
    core = g if g.geom_type == 'Polygon' else max(g.geoms, key=lambda q: q.area)
    c = core.buffer(-0.6).centroid if not core.buffer(-0.6).is_empty else core.representative_point()
    states.append([p['STUSPS'], p['NAME'], d, [float(f) for f in pt(c.x, c.y).split()]])
json.dump({'w': 1000, 'h': round((N - S) * sc), 'counties': out, 'states': states}, open(os.path.join(wm, 'midwest-counties.json'), 'w'), separators=(',', ':'))
print(len(maps), 'maps;', len(out), 'Midwest counties;', os.path.getsize(os.path.join(wm, 'midwest-counties.json')) // 1024, 'KB')
