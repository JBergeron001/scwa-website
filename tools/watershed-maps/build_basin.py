#!/usr/bin/env python3
"""Build the Mississippi River Basin 3D map data for Mid-America Watershed.Org.

Usage: python3 build_basin.py <output dir>

Levels: the whole basin (6 great regions, 2-digit hydrologic units) and one view per region
(its subregions, 4-digit hydrologic units). Rivers, lakes, interstates and metros come from
Natural Earth (public domain); watersheds from the USGS Watershed Boundary Dataset; state and
county lines from the U.S. Census Bureau; elevation from AWS Terrain Tiles.
"""
import sys, os, json, math, re, base64
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_county as bc
from build_county import Grid, Elev, wbd_query, read_shp_zip, fetch, RAW, TIGER, km_scale, area_km2, b64, make_valid
from shapely.geometry import shape, box, Point, LineString
from shapely.ops import unary_union
from shapely.strtree import STRtree

NE = os.path.join(RAW, 'ne')
REGIONS = ['07', '10', '05', '06', '11', '08']
REGION_NAMES = {'05': 'Ohio', '06': 'Tennessee', '07': 'Upper Mississippi', '08': 'Lower Mississippi', '10': 'Missouri', '11': 'Arkansas-White-Red'}
# Where each region's water leaves it (lat, lon); the Lower Mississippi and the whole basin drain along the Gulf coast
OUTLETS = {'07': [(37.00, -89.18)], '05': [(37.00, -89.17)], '06': [(37.07, -88.58)], '10': [(38.81, -90.12)],
           '11': [(33.78, -91.07), (33.95, -91.08), (31.00, -91.67)]}
GULF_OUTLETS = [(29.15, -89.25), (29.42, -91.35)]  # Head of Passes (Mississippi mouth) and the Atchafalaya River mouth

PINS = [  # category, name, lat, lon, note
    ('landmark', 'Lake Itasca', 47.2395, -95.2075, 'Headwaters of the Mississippi River, where it starts as a small stream.'),
    ('landmark', 'Missouri Headwaters', 45.9275, -111.4955, 'Three Forks, Montana, where the Jefferson, Madison and Gallatin rivers form the Missouri River.'),
    ('landmark', 'The Point, Pittsburgh', 40.4417, -80.0125, 'Where the Allegheny and Monongahela rivers meet to form the Ohio River.'),
    ('landmark', 'Gateway Arch', 38.6247, -90.1848, 'St. Louis riverfront on the Mississippi, just below the Missouri River confluence.'),
    ('landmark', 'Missouri-Mississippi Confluence', 38.8156, -90.1200, 'The Missouri River joins the Mississippi just north of St. Louis.'),
    ('landmark', 'Cahokia Mounds', 38.6551, -90.0618, 'Largest prehistoric Native American city north of Mexico, in the Mississippi floodplain.'),
    ('landmark', 'Ohio-Mississippi Confluence', 36.9867, -89.1460, 'Cairo, Illinois, where the Ohio River joins the Mississippi.'),
    ('landmark', 'Old River Control Structure', 31.0800, -91.6000, 'Controls how much Mississippi water flows into the Atchafalaya River.'),
    ('landmark', 'Head of Passes', 29.1497, -89.2522, 'Where the Mississippi River splits into its passes to the Gulf of Mexico.'),
    ('landmark', 'Lake of the Ozarks', 38.20, -92.65, 'Reservoir on the Osage River, a Missouri River tributary.'),
    ('landmark', 'Lake Sakakawea', 47.50, -101.42, 'Reservoir behind Garrison Dam on the Missouri River, North Dakota.'),
    ('landmark', 'Fort Peck Lake', 47.80, -106.60, 'Reservoir behind Fort Peck Dam on the Missouri River, Montana.'),
    ('landmark', 'Lake Texoma', 33.85, -96.65, 'Reservoir on the Red River between Oklahoma and Texas.'),
    ('landmark', 'Mount Rushmore', 43.8791, -103.4591, 'National memorial in the Black Hills of South Dakota.'),
    ('park', 'Yellowstone National Park', 44.4280, -110.5885, 'The Yellowstone River here flows to the Missouri River.'),
    ('park', 'Rocky Mountain National Park', 40.3428, -105.6836, 'East-side streams feed the South Platte River and the Missouri River system.'),
    ('park', 'Theodore Roosevelt National Park', 46.9790, -103.5387, 'Little Missouri River badlands, North Dakota.'),
    ('park', 'Badlands National Park', 43.8554, -102.3397, 'Eroded buttes draining to the White River and the Missouri.'),
    ('park', 'Great Smoky Mountains National Park', 35.6118, -83.4895, 'Headwater streams of the Tennessee River system.'),
    ('park', 'Mammoth Cave National Park', 37.1862, -86.1000, 'World\'s longest known cave system, on the Green River in the Ohio basin.'),
    ('park', 'New River Gorge National Park', 38.0700, -81.0800, 'The New River, a headwater of the Kanawha and Ohio rivers.'),
    ('park', 'Land Between the Lakes', 36.80, -88.07, 'Peninsula between Kentucky Lake (Tennessee River) and Lake Barkley (Cumberland River).'),
    ('park', 'Hot Springs National Park', 34.5217, -93.0424, 'Ouachita Mountains of Arkansas, in the Ouachita River basin.'),
    ('park', 'Buffalo National River', 36.0400, -92.9100, 'America\'s first national river, a White River tributary in Arkansas.'),
    ('park', 'Ozark National Scenic Riverways', 37.1500, -91.3500, 'Current and Jacks Fork rivers in Missouri, draining to the Black and White rivers.'),
    ('park', 'Effigy Mounds National Monument', 43.0886, -91.1855, 'Ancient mounds on the bluffs above the Upper Mississippi in Iowa.'),
    ('park', 'Mississippi National River and Recreation Area', 44.9500, -93.2000, 'The Mississippi through the Minneapolis-Saint Paul metro area.'),
    ('park', 'Atchafalaya Basin', 30.3000, -91.7000, 'The largest river swamp in the United States, in Louisiana.'),
]
METRO_FIX = {'Ft.  Worth': 'Fort Worth', 'St.  Paul': 'Saint Paul', 'St.  Louis': 'St. Louis'}


def ne(name):
    return json.load(open(os.path.join(NE, name + '.geojson')))['features']


def edges(mask):
    e = np.zeros_like(mask)
    e[1:-1, 1:-1] = mask[1:-1, 1:-1] & ~(mask[:-2, 1:-1] & mask[2:, 1:-1] & mask[1:-1, :-2] & mask[1:-1, 2:])
    return e


def build_view(G, region, el, sheds, L, detail, outlets):
    LON, LAT = G.centers()
    h = el.sample(LON, LAT)
    inside = G.fill([(region, 1)]) > 0
    rb = box(*region.bounds)
    # water: lakes as areas, rivers as lines (major = class 2, others class 3)
    lakes = [g for g, nm in L['lakes'] if g.intersects(rb)]
    aw = G.fill([(g, 1) for g in lakes]) > 0
    major = [(g, 1, 2 if detail else 1) for g, nm, rk in L['rivers'] if rk <= 6 and g.intersects(rb)]
    minor = [(g, 1, 1) for g, nm, rk in L['rivers'] if rk > 6 and (detail or rk <= 8) and g.intersects(rb)]
    mj = G.stroke(major) > 0; mn = G.stroke(minor) > 0
    cls = np.zeros((G.ny, G.nx), np.uint8)
    cls[mn] = 3; cls[mj] = 2; cls[aw] = 1
    cls |= inside.astype(np.uint8) << 3
    # outlet cells (bit 5): edge cells where the water leaves this view
    edge = edges(inside)
    out = np.zeros_like(inside)
    ii, jj = np.nonzero(edge)
    for i, j in zip(ii, jj):
        lon, lat = LON[i, j], LAT[i, j]
        pts = GULF_OUTLETS if outlets == 'gulf' else outlets
        rad = 45 if outlets == 'gulf' else 30
        for (la, lo) in pts:
            if math.hypot((lon - lo) * math.cos(math.radians(la)), lat - la) * 111 < rad: out[i, j] = True
    if not out.any():  # fall back to the lowest edge cell
        k = np.argmin(np.where(edge, h, 1e9)); out.flat[k] = True
    cls |= out.astype(np.uint8) << 5
    cls |= inside.astype(np.uint8) << 6
    # creek / river names for hover and the rain story
    names = []
    cn = np.zeros((G.ny, G.nx), np.uint8)
    for g, nm, rk in sorted([r for r in L['rivers'] if r[1] and r[0].intersects(rb)], key=lambda r: r[2], reverse=True):
        if (rk > 6 and not detail and rk > 8): continue
        if nm not in names and len(names) < 254: names.append(nm)
        if nm in names:
            m = G.stroke([(g, 1, 2)]) > 0
            cn[m] = names.index(nm) + 1
    for g, nm in L['lakes']:
        if nm and g.intersects(rb):
            if nm not in names and len(names) < 254: names.append(nm)
            if nm in names: cn[G.fill([(g, 1)]) > 0] = names.index(nm) + 1
    # subregions
    sidx = G.fill([(g, i + 1) for i, (code, nm, g, a, gi) in sorted(enumerate(sheds), key=lambda t: -t[1][3])])
    # lines: roads (interstates), state lines (bit 3), county lines (bit 4)
    rr = np.zeros((G.ny, G.nx), np.uint8)
    rr |= G.stroke([(g, 3, 1) for g, nm in L['roads'] if g.intersects(rb)])
    st = G.fill([(g, (i % 250) + 1) for i, g in enumerate(L['states']) if g.intersects(rb)])
    se = np.zeros_like(inside); se[1:-1, 1:-1] = (st[1:-1, 1:-1] != st[:-2, 1:-1]) | (st[1:-1, 1:-1] != st[1:-1, :-2])
    rr |= (se & inside).astype(np.uint8) << 3
    ct = G.fill([(g, (i % 250) + 1) for i, g in enumerate(L['counties']) if g.intersects(rb)])
    ce = np.zeros_like(inside); ce[1:-1, 1:-1] = (ct[1:-1, 1:-1] != ct[:-2, 1:-1]) | (ct[1:-1, 1:-1] != ct[1:-1, :-2])
    rr |= (ce & inside & ~se).astype(np.uint8) << 4
    hin = h[inside]
    lo, hi = float(np.percentile(hin, 0.5)), float(np.percentile(hin, 99.8))
    h = np.clip(h, lo - 2, hi + (hi - lo) * 0.25)
    uv = lambda x, y: [round((x - G.w) / (G.dlon * G.nx), 4), round((G.n - y) / (G.dlat * G.ny), 4)]
    def inside_pt(x, y):
        j = int((x - G.w) / G.dlon); i = int((G.n - y) / G.dlat)
        return 0 <= i < G.ny and 0 <= j < G.nx and inside[i, j]
    cities = [[nm] + uv(x, y) for nm, x, y, pop in L['metros'] if inside_pt(x, y) and (detail or pop >= 900000)][:(34 if detail else 24)]
    best = {}
    for g, nm in L['roads']:
        if not nm or not g.intersects(region): continue
        for ln in G.lines(g.intersection(region)):
            if ln.length > best.get(nm, (0,))[0]: best[nm] = (ln.length, ln)
    roads = []
    for nm, (ln_len, ln) in sorted(best.items(), key=lambda t: -t[1][0])[:(16 if detail else 12)]:
        p = ln.interpolate(0.5, normalized=True); roads.append([nm] + uv(p.x, p.y) + [3])
    rb_best = {}
    for g, nm, rk in L['rivers']:
        if not nm or rk > (7 if detail else 5) or not g.intersects(region): continue
        for ln in G.lines(g.intersection(region)):
            if ln.length > rb_best.get(nm, (0,))[0]: rb_best[nm] = (ln.length, ln)
    rivers = []
    for nm, (ln_len, ln) in sorted(rb_best.items(), key=lambda t: -t[1][0])[:(14 if detail else 10)]:
        p = ln.interpolate(0.45, normalized=True); rivers.append([nm] + uv(p.x, p.y))
    pins = [[cat, nm] + uv(lo_, la) + [note] for cat, nm, la, lo_, note in PINS if inside_pt(lo_, la)]
    return {
        'bb': [round(v, 5) for v in G.bb], 'nx': G.nx, 'ny': G.ny, 'cellkm': round(G.cellkm, 4),
        'h': b64(np.clip(np.round(h * 2), -32000, 32000).astype('<i2')), 'c': b64(cls), 's': b64(sidx.astype(np.uint8)),
        'cn': b64(cn), 'creeks': names, 'r': b64(rr), 'cities': cities, 'roads': roads, 'rivers': rivers, 'places': pins,
        'hlo': round(lo, 1), 'hhi': round(hi, 1), 'elo': int(round(lo * 3.281)), 'ehi': int(round(float(np.percentile(hin, 99.97)) * 3.281)),
    }


def main(outdir):
    os.makedirs(outdir, exist_ok=True)
    regs = {}
    for f in wbd_query(1, (-115, 28, -77, 51), 'huc2,name,areasqkm', where="huc2 IN ('05','06','07','08','10','11')", offset=0.01):
        regs[f['properties']['huc2']] = (make_valid(shape(f['geometry'])).simplify(0.01), f['properties']['areasqkm'])
    basin = make_valid(unary_union([regs[r][0] for r in REGIONS]).buffer(0.02).buffer(-0.02))
    lat0 = 39.0
    print('regions', {r: round(regs[r][1]) for r in REGIONS})
    subs = []
    for r in REGIONS:
        for f in wbd_query(2, regs[r][0].bounds, 'huc4,name,areasqkm', where=f"huc4 LIKE '{r}%'", offset=0.005):
            g = make_valid(shape(f['geometry'])).simplify(0.005)
            subs.append([f['properties']['huc4'], bc.clean_name(f['properties']['name']), g, f['properties']['areasqkm'], REGIONS.index(r)])
    subs.sort(key=lambda s: (s[4], -s[3]))
    print(len(subs), 'subregions')
    # reference layers
    L = {}
    L['rivers'] = []
    for fname in ('ne_10m_rivers_lake_centerlines', 'ne_10m_rivers_north_america'):
        for f in ne(fname):
            if not f['geometry']: continue
            g = shape(f['geometry'])
            if not g.intersects(box(*basin.bounds)): continue
            nm = (f['properties'].get('name_en') or f['properties'].get('name') or '').strip()
            if nm and not re.search(r'\b(River|Creek|Fork|Bayou)\b', nm): nm = nm + ' River' if fname.endswith('centerlines') else nm
            L['rivers'].append((g, nm, f['properties'].get('scalerank') or 12))
    L['lakes'] = [(make_valid(shape(f['geometry'])), (f['properties'].get('name') or '').strip()) for fname in ('ne_10m_lakes', 'ne_10m_lakes_north_america')
                  for f in ne(fname) if f['geometry'] and shape(f['geometry']).intersects(box(*basin.bounds))]
    L['roads'] = []
    for f in ne('ne_10m_roads'):
        p = f['properties']
        if p.get('sov_a3') != 'USA' or p.get('level') != 'Interstate' or not f['geometry']: continue
        g = shape(f['geometry'])
        if g.intersects(box(*basin.bounds)): L['roads'].append((g, 'I-' + str(p.get('name')).strip() if p.get('name') else ''))
    L['metros'] = []
    for f in ne('ne_10m_populated_places'):
        p = f['properties']
        if p.get('ADM0_A3') != 'USA' or (p.get('POP_MAX') or 0) < 250000: continue
        x, y = f['geometry']['coordinates']
        if basin.contains(Point(x, y)):
            L['metros'].append((METRO_FIX.get(p['NAME'], re.sub(r'\s+', ' ', p['NAME'])), x, y, p['POP_MAX']))
    L['metros'].sort(key=lambda m: -m[3])
    seen = set(); L['metros'] = [m for m in L['metros'] if not (m[0] in seen or seen.add(m[0]))]
    L['states'] = [make_valid(g) for p, g in read_shp_zip(fetch(TIGER + 'GENZ2023/shp/cb_2023_us_state_20m.zip', os.path.join(RAW, 'cb_2023_us_state_20m.zip'))) if g.intersects(box(*basin.bounds))]
    L['counties'] = [make_valid(g) for p, g in read_shp_zip(fetch(TIGER + 'GENZ2023/shp/cb_2023_us_county_20m.zip', os.path.join(RAW, 'cb_2023_us_county_20m.zip'))) if g.intersects(box(*basin.bounds))]
    print(len(L['rivers']), 'rivers', len(L['lakes']), 'lakes', len(L['roads']), 'interstate pieces', len(L['metros']), 'metros', len(L['counties']), 'counties')
    sheds_list = [(s[0], s[1], s[2].intersection(basin), s[3], s[4]) for s in subs]

    # whole basin
    bc.ZOOM = 7
    el7 = Elev(basin.bounds)
    b = basin.bounds
    G = Grid((b[0] - .05, b[1] - .05, b[2] + .05, b[3] + .05), 820)
    D = build_view(G, basin, el7, sheds_list, L, False, 'gulf')
    D.update({'kind': 'county', 'geoid': 'mississippi', 'name': 'Mississippi River Basin', 'state': '31 states and 2 Canadian provinces', 'includes': [],
              'areakm': round(sum(regs[r][1] for r in REGIONS), 1),
              'groups': [[r, REGION_NAMES[r], round(regs[r][1], 1), [i + 1 for i, s in enumerate(subs) if s[4] == gi], round(regs[r][1], 1)] for gi, r in enumerate(REGIONS)],
              'sheds': [[s[0], s[1], round(s[3], 1), 1.0, s[4] + 1, round(s[3], 1)] for s in subs]})
    json.dump(D, open(os.path.join(outdir, 'basin.json'), 'w'), separators=(',', ':'))
    print('basin grid', G.nx, G.ny, round(G.cellkm, 2), 'km')
    bc.ZOOM = 8
    for gi, r in enumerate(REGIONS):
        rg = regs[r][0]
        b = rg.bounds
        el8 = Elev(b)
        GG = Grid((b[0] - .05, b[1] - .05, b[2] + .05, b[3] + .05), 640)
        DD = build_view(GG, rg, el8, sheds_list, L, True, 'gulf' if r == '08' else OUTLETS[r])
        DD.update({'kind': 'watershed', 'huc10': r, 'name': REGION_NAMES[r], 'areakm': round(regs[r][1], 1), 'fullkm': round(regs[r][1], 1)})
        json.dump(DD, open(os.path.join(outdir, r + '.json'), 'w'), separators=(',', ':'))
        print(' ', r, REGION_NAMES[r], GG.nx, GG.ny, round(GG.cellkm, 2), 'km')


if __name__ == '__main__':
    main(sys.argv[1])
