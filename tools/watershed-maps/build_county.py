#!/usr/bin/env python3
"""Build Mid-America Watershed.Org 3D watershed map data for one county.

Usage: python3 build_county.py <5-digit county GEOID> <output dir> [--with=<GEOID>,...]
  --with adds areas such as an independent city into the map with a highlighted boundary.

Sources (all public domain / open data):
  County and place boundaries, area and linear water: U.S. Census Bureau (cartographic boundary + TIGER/Line 2023)
  Watersheds (HUC10) and subwatersheds (HUC12): USGS Watershed Boundary Dataset
  Elevation: Terrain Tiles on AWS Open Data (Terrarium encoding; USGS 3DEP/NED in the U.S.)
"""
import sys, os, io, json, math, base64, zipfile, re, urllib.request, urllib.parse
import numpy as np, shapefile
from PIL import Image, ImageDraw
from shapely.geometry import shape, mapping, box, Point
from shapely.ops import unary_union
from shapely import make_valid

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'raw')
os.makedirs(os.path.join(RAW, 'tiles'), exist_ok=True)
TIGER = 'https://www2.census.gov/geo/tiger/'
WBD = 'https://hydro.nationalmap.gov/arcgis/rest/services/wbd/MapServer/'
ZOOM = 12


def fetch(url, path, timeout=180):
    import time
    for attempt in range(7):
        if os.path.exists(path) and os.path.getsize(path) > 0:
            if not path.endswith('.zip'): return path
            try:
                zipfile.ZipFile(path).close(); return path
            except Exception:
                os.remove(path)
        if attempt == 6: break
        try:
            time.sleep(1)
            req = urllib.request.Request(url, headers={'User-Agent': 'MAWA-map-builder'})
            with urllib.request.urlopen(req, timeout=timeout) as r: body = r.read()
            open(path, 'wb').write(body)
        except Exception as ex:
            print('download retry', attempt + 1, url, ex); time.sleep(10 + 10 * attempt)
    return path


def read_shp_zip(path):
    z = zipfile.ZipFile(path)
    base = [n[:-4] for n in z.namelist() if n.endswith('.shp')][0]
    r = shapefile.Reader(shp=io.BytesIO(z.read(base + '.shp')), shx=io.BytesIO(z.read(base + '.shx')),
                         dbf=io.BytesIO(z.read(base + '.dbf')), encoding='latin-1')
    names = [f[0] for f in r.fields[1:]]
    out = []
    for sr in r.iterShapeRecords():
        try:
            g = shape(sr.shape.__geo_interface__)
        except Exception:
            continue
        out.append((dict(zip(names, sr.record)), g))
    return out


def km_scale(lat):
    return 111.32 * math.cos(math.radians(lat)), 110.57


def area_km2(g, lat0):
    kx, ky = km_scale(lat0)
    from shapely.affinity import scale
    return scale(g, kx, ky, origin=(0, 0)).area


def wbd_query(layer, bb, fields, where='1=1'):
    w, s, e, n = bb
    q = urllib.parse.urlencode({'where': where, 'geometry': f'{w},{s},{e},{n}', 'geometryType': 'esriGeometryEnvelope', 'inSR': 4326,
                                'spatialRel': 'esriSpatialRelIntersects', 'outFields': fields, 'returnGeometry': 'true',
                                'outSR': 4326, 'f': 'geojson', 'geometryPrecision': 6, 'maxAllowableOffset': 0.00005})
    import time
    for attempt in range(6):
        try:
            req = urllib.request.Request(WBD + f'{layer}/query?' + q, headers={'User-Agent': 'MAWA-map-builder'})
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.load(r)['features']
        except Exception as ex:
            print('watershed service retry', attempt + 1, ex); time.sleep(10 + 10 * attempt)
    raise SystemExit('watershed service unavailable')


# ---------- elevation ----------
def tile_xy(lon, lat, z):
    n = 2 ** z
    x = (lon + 180) / 360 * n
    y = (1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi) / 2 * n
    return x, y


class Elev:
    def __init__(self, bb):
        w, s, e, n = bb
        x0, y0 = tile_xy(w, n, ZOOM); x1, y1 = tile_xy(e, s, ZOOM)
        self.tx0, self.ty0 = int(x0), int(y0)
        tx1, ty1 = int(x1), int(y1)
        W, H = (tx1 - self.tx0 + 1) * 256, (ty1 - self.ty0 + 1) * 256
        self.a = np.zeros((H, W), np.float32)
        for ty in range(self.ty0, ty1 + 1):
            for tx in range(self.tx0, tx1 + 1):
                p = os.path.join(RAW, 'tiles', f'{ZOOM}_{tx}_{ty}.png')
                fetch(f'https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{ZOOM}/{tx}/{ty}.png', p)
                im = np.asarray(Image.open(p).convert('RGB')).astype(np.float32)
                el = im[..., 0] * 256 + im[..., 1] + im[..., 2] / 256 - 32768
                oy, ox = (ty - self.ty0) * 256, (tx - self.tx0) * 256
                self.a[oy:oy + 256, ox:ox + 256] = el

    def sample(self, lons, lats):
        x, y = tile_xy(lons, lats, ZOOM) if np.isscalar(lons) else self._xy(lons, lats)
        px = (x - self.tx0) * 256 - .5; py = (y - self.ty0) * 256 - .5
        i0 = np.clip(np.floor(py).astype(int), 0, self.a.shape[0] - 2); j0 = np.clip(np.floor(px).astype(int), 0, self.a.shape[1] - 2)
        fy = np.clip(py - i0, 0, 1); fx = np.clip(px - j0, 0, 1)
        a = self.a
        return (a[i0, j0] * (1 - fx) * (1 - fy) + a[i0, j0 + 1] * fx * (1 - fy) + a[i0 + 1, j0] * (1 - fx) * fy + a[i0 + 1, j0 + 1] * fx * fy)

    def _xy(self, lons, lats):
        n = 2 ** ZOOM
        x = (lons + 180) / 360 * n
        lr = np.radians(lats)
        y = (1 - np.log(np.tan(lr) + 1 / np.cos(lr)) / math.pi) / 2 * n
        return x, y


# ---------- rasterizing ----------
class Grid:
    def __init__(self, bb, maxdim, mincell=0.03):
        w, s, e, n = bb
        lat0 = (s + n) / 2
        kx, ky = km_scale(lat0)
        wkm, hkm = (e - w) * kx, (n - s) * ky
        self.cellkm = max(max(wkm, hkm) / maxdim, mincell)
        self.nx = max(2, int(round(wkm / self.cellkm)))
        self.ny = max(2, int(round(hkm / self.cellkm)))
        self.dlon = (e - w) / self.nx; self.dlat = (n - s) / self.ny
        self.bb = [w, e, s, n]; self.w, self.n = w, n; self.lat0 = lat0

    def px(self, lon, lat):
        return ((lon - self.w) / self.dlon - .5, (self.n - lat) / self.dlat - .5)

    def centers(self):
        j = np.arange(self.nx); i = np.arange(self.ny)
        lon = self.w + (j + .5) * self.dlon; lat = self.n - (i + .5) * self.dlat
        return np.meshgrid(lon, lat)

    def polys(self, g):
        if g.is_empty: return []
        if g.geom_type == 'Polygon': return [g]
        if hasattr(g, 'geoms'):
            out = []
            for p in g.geoms: out += self.polys(p)
            return out
        return []

    def lines(self, g):
        if g.is_empty: return []
        if g.geom_type == 'LineString': return [g]
        if hasattr(g, 'geoms'):
            out = []
            for p in g.geoms: out += self.lines(p)
            return out
        return []

    def fill(self, items, mode='L'):
        """items: list of (geom, value). Later items paint over earlier ones."""
        im = Image.new(mode, (self.nx, self.ny), 0)
        d = ImageDraw.Draw(im)
        for g, v in items:
            for p in self.polys(g):
                d.polygon([self.px(x, y) for x, y in p.exterior.coords], fill=v)
                for h in p.interiors:
                    d.polygon([self.px(x, y) for x, y in h.coords], fill=0)
        return np.asarray(im)

    def stroke(self, items, mode='L'):
        im = Image.new(mode, (self.nx, self.ny), 0)
        d = ImageDraw.Draw(im)
        for g, v, wdt in items:
            for l in self.lines(g):
                d.line([self.px(x, y) for x, y in l.coords], fill=v, width=wdt)
        return np.asarray(im)


def b64(a):
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


ABBR = {'Crk': 'Creek', 'Cr': 'Creek', 'Ck': 'Creek', 'Riv': 'River', 'Br': 'Branch', 'Brk': 'Brook', 'Trib': 'Tributary',
        'Lk': 'Lake', 'Res': 'Reservoir', 'Spg': 'Spring', 'Spgs': 'Springs', 'Fk': 'Fork', 'Pd': 'Pond',
        'N': 'North', 'S': 'South', 'E': 'East', 'W': 'West', 'Mid': 'Middle', 'Ft': 'Fort', 'Mt': 'Mount',
        'Hbr': 'Harbor', 'Chnl': 'Channel', 'Chnnl': 'Channel', 'Dch': 'Ditch', 'Slo': 'Slough', 'Drn': 'Drain', 'Co': 'County', 'Cnl': 'Canal', 'Lks': 'Lakes', 'Pnd': 'Pond', 'Rsvr': 'Reservoir'}


def clean_name(n):
    n = re.sub(r' (city )?\(balance\)$', '', n)
    n = re.sub(r'/.*metro(politan)? government.*$', '', n)
    n = ' '.join(ABBR.get(w, w) for w in n.split())
    return n.replace("'", "’")


def road_name(n):
    n = re.sub(r'^I- ?(\d+)$', r'I-\1', n.strip())
    n = re.sub(r'^I- ?(\d+) .*', r'I-\1', n)
    n = re.sub(r'^US Hwy (\d+\w?)$', r'US \1', n)
    n = re.sub(r'^State Hwy (\w+)$', r'Hwy \1', n)
    n = re.sub(r'\b(Hwy|Rte|Route) ([A-Za-z]{1,2})$', lambda m: m.group(1) + ' ' + m.group(2).upper(), n)
    return clean_name(n) if len(n) < 26 else ''


def osm_layers(bb, path):
    """Parks, schools, colleges, hospitals, fire stations and city halls from OpenStreetMap (ODbL)."""
    w, s, e, n = bb
    q = f'[out:json][timeout:120];(way["leisure"="park"]({s},{w},{n},{e});relation["leisure"="park"]({s},{w},{n},{e}););out geom;' \
        f'(nwr["amenity"~"^(school|college|university|hospital|fire_station|townhall)$"]({s},{w},{n},{e}););out center tags;'
    import time
    for attempt in range(4):
      if os.path.exists(path): break
      if attempt: time.sleep(25)
      data = urllib.parse.urlencode({'data': q}).encode()
      for url in ('https://maps.mail.ru/osm/tools/overpass/api/interpreter', 'https://overpass-api.de/api/interpreter'):
        try:
            req = urllib.request.Request(url, data=data, headers={'User-Agent': 'MAWA-map-builder'})
            with urllib.request.urlopen(req, timeout=240) as r: body = r.read()
            json.loads(body)
            open(path, 'wb').write(body)
            break
        except Exception as ex:
            print('overpass failed', url, ex)
    if not os.path.exists(path): return [], []
    from shapely.geometry import Polygon, LineString
    from shapely.ops import polygonize
    parks, pois = [], []
    CAT = {'school': 'school', 'college': 'college', 'university': 'college', 'hospital': 'hospital', 'fire_station': 'fire', 'townhall': 'cityhall'}
    for el in json.load(open(path)).get('elements', []):
        t = el.get('tags', {}); nm = t.get('name', '')
        if t.get('leisure') == 'park' and ('geometry' in el or 'members' in el):
            try:
                if el['type'] == 'way':
                    cs = [(p['lon'], p['lat']) for p in el['geometry']]
                    if len(cs) < 4 or cs[0] != cs[-1]: continue
                    g = make_valid(Polygon(cs))
                else:
                    outer = [LineString([(p['lon'], p['lat']) for p in m['geometry']]) for m in el['members'] if m.get('geometry') and m.get('role') != 'inner']
                    inner = [LineString([(p['lon'], p['lat']) for p in m['geometry']]) for m in el['members'] if m.get('geometry') and m.get('role') == 'inner']
                    g = unary_union(list(polygonize(unary_union(outer))))
                    if inner: g = g.difference(unary_union(list(polygonize(unary_union(inner)))))
                    g = make_valid(g)
            except Exception:
                continue
            if g.is_empty: continue
            a = area_km2(g, (s + n) / 2)
            parks.append((nm, g, a))
            if nm: pois.append(('park', nm, g.representative_point(), a))
        elif t.get('amenity') in CAT and nm:
            c = el.get('center') or ({'lat': el['lat'], 'lon': el['lon']} if 'lat' in el else None)
            if c: pois.append((CAT[t['amenity']], nm, Point(c['lon'], c['lat']), 0))
    order = {'college': 0, 'hospital': 1, 'cityhall': 2, 'fire': 3, 'park': 4, 'school': 5}
    pois.sort(key=lambda r: (order[r[0]], -r[3]))
    seen, uniq = set(), []
    for r in pois:
        if (r[0], r[1]) in seen and r[0] != 'park': continue
        seen.add((r[0], r[1])); uniq.append(r)
    return parks, uniq


_TREES = {}


def near_items(items, geom, gi=1):
    """Items (tuples whose element gi is a geometry) whose bounds touch geom, via a cached STRtree."""
    from shapely.strtree import STRtree
    key = id(items)
    if key not in _TREES: _TREES[key] = (STRtree([t[gi] for t in items]), items)
    tree, its = _TREES[key]
    return [its[i] for i in tree.query(geom)]


def build_grid(G, county, region, el, sheds, area_water, lin_water, places, detail, extras=(), L=None):
    """Returns the encoded payload for a grid covering `region` (already inside the county)."""
    # only look at features near this area (spatial index), which keeps big counties fast
    rb = box(*region.bounds)
    area_water = near_items(area_water, rb); lin_water = near_items(lin_water, rb)
    if L: L = dict(L, roads=near_items(L['roads'], rb), munis=near_items(L['munis'], rb, 1), parks=near_items(L['parks'], rb, 1))
    LON, LAT = G.centers()
    h = el.sample(LON, LAT)
    inside = G.fill([(region, 1)])
    cty = G.fill([(county, 1)])
    # water
    aw = G.fill([(g, 1) for p, g in area_water if g.intersects(region)])
    major, minor, cn_items, creeks = [], [], [], []
    for p, g in lin_water:
        if not g.intersects(region): continue
        nm = clean_name((p.get('FULLNAME') or '').strip())
        if not nm and not detail: continue
        if re.search(r'\bRiver\b', nm): major.append((g, 1, 2 if detail else 1))
        else: minor.append((g, 1, 1))
        if nm:
            if nm not in creeks and len(creeks) < 254: creeks.append(nm)
            if nm in creeks: cn_items.append((g, creeks.index(nm) + 1, 2))
    for p, g in area_water:
        nm = clean_name((p.get('FULLNAME') or '').strip())
        if nm and g.intersects(region):
            if nm not in creeks and len(creeks) < 254: creeks.append(nm)
    mj = G.stroke(major); mn = G.stroke(minor)
    cn = G.stroke(cn_items)
    cna = G.fill([(g, creeks.index(clean_name((p.get('FULLNAME') or '').strip())) + 1) for p, g in area_water
                  if clean_name((p.get('FULLNAME') or '').strip()) in creeks and g.intersects(region)])
    cn = np.where(cna > 0, cna, cn).astype(np.uint8)
    # subwatersheds (global index), drawn biggest first
    sidx = G.fill([(g, i + 1) for i, (code, nm, g, a, fr, gi) in sorted(enumerate(sheds), key=lambda t: -t[1][3])])
    # elevation: fill outside-region cells with nearest-ish value so edges don't spike
    hv = h.copy()
    # cls bits: 0-2 class (0 land, 1 open water, 2 major river line, 3 minor stream), bit3 inside region, bit4 county line
    cls = np.zeros((G.ny, G.nx), np.uint8)
    cls[mn > 0] = 3; cls[mj > 0] = 2; cls[aw > 0] = 1
    cls |= (inside > 0).astype(np.uint8) << 3
    # county edge marker (cells inside county with an outside neighbour) - used when region == county
    edge = np.zeros_like(cty)
    c = cty > 0
    e = np.zeros_like(c); e[1:-1, 1:-1] = c[1:-1, 1:-1] & ~(c[:-2, 1:-1] & c[2:, 1:-1] & c[1:-1, :-2] & c[1:-1, 2:])
    cls |= e.astype(np.uint8) << 4
    cls |= ((cty > 0) & (inside > 0)).astype(np.uint8) << 6
    # bit5: boundary of an included area (e.g. an independent city inside the county map), highlighted in the viewer
    for xg in extras:
        xr = G.fill([(xg, 1)]) > 0
        xe = np.zeros_like(xr); xe[1:-1, 1:-1] = xr[1:-1, 1:-1] & ~(xr[:-2, 1:-1] & xr[2:, 1:-1] & xr[1:-1, :-2] & xr[1:-1, 2:])
        xe &= (inside > 0)
        cls |= xe.astype(np.uint8) << 5
    hin = hv[inside > 0]
    lo, hi = float(np.percentile(hin, 0.5)), float(np.percentile(hin, 99.8))
    # labels (u,v in 0..1 grid space)
    cities = []
    for nm, pt, a in places:
        if region.contains(pt):
            cities.append([clean_name(nm), round((pt.x - G.w) / (G.dlon * G.nx), 4), round((G.n - pt.y) / (G.dlat * G.ny), 4), round(a, 2)])
    hv = np.clip(hv, lo - 2.0, hi + (hi - lo) * 0.25)  # trims rare spikes and pits in the elevation tiles
    cities.sort(key=lambda r: -r[3])
    cities = [r[:3] for r in cities[:(36 if detail else 18)]]
    uv = lambda x, y: [round((x - G.w) / (G.dlon * G.nx), 4), round((G.n - y) / (G.dlat * G.ny), 4)]
    # extra layers: roads, parks, city limits, places (r byte: bits0-1 road class 1 local/2 highway/3 interstate,
    # bit3 city-limit edge, bit4 park, bit5 park edge, bit6 inside a city)
    rr = np.zeros((G.ny, G.nx), np.uint8); road_labels, pois = [], []
    if L:
        bbox = box(*region.bounds)
        rl = [(g, 1, 1) for p, g in L['roads'] if detail and p['MTFCC'] == 'S1400' and g.intersects(bbox)]
        rl += [(g, 2, 1) for p, g in L['roads'] if p['MTFCC'] == 'S1200' and g.intersects(bbox)]
        rl += [(g, 3, 2 if detail else 1) for p, g in L['roads'] if p['MTFCC'] == 'S1100' and g.intersects(bbox)]
        rr |= G.stroke(rl)
        muni = G.fill([(g, 1) for nm, g in L['munis'] if g.intersects(region)]) > 0
        me = np.zeros_like(muni); me[1:-1, 1:-1] = muni[1:-1, 1:-1] & ~(muni[:-2, 1:-1] & muni[2:, 1:-1] & muni[1:-1, :-2] & muni[1:-1, 2:])
        # also the lines between two neighbouring cities
        mid = G.fill([(g, (i % 250) + 1) for i, (nm, g) in enumerate(L['munis']) if g.intersects(region)])
        me[1:-1, 1:-1] |= (mid[1:-1, 1:-1] > 0) & ((mid[1:-1, 1:-1] != mid[:-2, 1:-1]) | (mid[1:-1, 1:-1] != mid[1:-1, :-2])) & (mid[:-2, 1:-1] > 0) & (mid[1:-1, :-2] > 0)
        rr |= me.astype(np.uint8) << 3; rr |= muni.astype(np.uint8) << 6
        pk = [(g, 1) for nm, g, a in L['parks'] if g.intersects(region) and (detail or a > 0.15)]
        pr = G.fill(pk) > 0
        pe = np.zeros_like(pr); pe[1:-1, 1:-1] = pr[1:-1, 1:-1] & ~(pr[:-2, 1:-1] & pr[2:, 1:-1] & pr[1:-1, :-2] & pr[1:-1, 2:])
        rr |= pr.astype(np.uint8) << 4; rr |= pe.astype(np.uint8) << 5
        rr[(inside == 0)] = 0
        # road labels: one per numbered route, on its longest piece inside the area
        best = {}
        for p, g in L['roads']:
            if p['MTFCC'] not in ('S1100', 'S1200') or p.get('RTTYP') not in ('I', 'U', 'S') and not re.search(r'\bHwy\b', p.get('FULLNAME') or ''): continue
            nm = road_name(p.get('FULLNAME') or '')
            if not nm or not g.intersects(region): continue
            gi = g.intersection(region)
            for ln in G.lines(gi):
                if ln.length > best.get(nm, (0,))[0]: best[nm] = (ln.length, ln, 3 if p['MTFCC'] == 'S1100' else 2)
        for nm, (ln_len, ln, c) in sorted(best.items(), key=lambda t: -t[1][0])[:(14 if detail else 12)]:
            pt = ln.interpolate(0.5, normalized=True); road_labels.append([nm] + uv(pt.x, pt.y) + [c])
        for cat, nm, pt, a in L['pois']:
            if not region.contains(pt): continue
            if not detail and not (cat in ('college', 'hospital') or (cat == 'park' and a > 0.6)): continue
            pois.append([cat, clean_name(nm)] + uv(pt.x, pt.y))
        pois = pois[:400]
    return {
        'r': b64(rr), 'roads': road_labels, 'places': pois,
        'bb': [round(v, 5) for v in G.bb], 'nx': G.nx, 'ny': G.ny, 'cellkm': round(G.cellkm, 5),
        'h': b64(np.clip(np.round(hv * 2), -32000, 32000).astype('<i2')), 'c': b64(cls), 's': b64(sidx.astype(np.uint8)),
        'cn': b64(cn), 'creeks': creeks, 'cities': cities,
        'hlo': round(lo, 1), 'hhi': round(hi, 1), 'elo': int(round(lo * 3.281)), 'ehi': int(round(min(float(np.percentile(hin, 99.97)), hi + (hi - lo) * 0.25) * 3.281)),
    }


def main(geoid, outdir, extra=()):
    st = geoid[:2]
    counties = read_shp_zip(fetch(TIGER + 'GENZ2023/shp/cb_2023_us_county_500k.zip', os.path.join(RAW, 'cb_2023_us_county_500k.zip')))
    rec = [(p, g) for p, g in counties if p['GEOID'] == geoid]
    if not rec: sys.exit('county not found')
    cp, county = rec[0]
    county = make_valid(county)
    cname, sname = cp['NAMELSAD'], cp['STATE_NAME']
    includes, extra_geoms = [], []
    for xg in extra:  # areas shown inside this map with a highlighted boundary (e.g. City of St. Louis)
        xp, xgeom = [(p, g) for p, g in counties if p['GEOID'] == xg][0]
        xn = xp['NAMELSAD']; xn = 'City of ' + xn[:-5] if xn.endswith(' city') else xn
        includes.append([xg, xn]); extra_geoms.append(make_valid(xgeom))
    if extra_geoms:
        county = make_valid(unary_union([county] + extra_geoms).buffer(0.0004).buffer(-0.0004))
    if cname.endswith(' city'): cname = 'City of ' + cname[:-5]  # independent cities (St. Louis, Baltimore, Virginia cities)
    w, s, e, n = county.bounds
    lat0 = (s + n) / 2
    print('county', cname, sname, county.bounds)
    pad = 0.01
    bb = (w - pad, s - pad, e + pad, n + pad)

    # watersheds: the county view shows the part inside the county; each watershed view shows the WHOLE watershed,
    # so rain can be followed all the way to the watershed's outlet even where it crosses the county line
    h10 = wbd_query(5, bb, 'huc10,name,areasqkm')
    groups = []
    for f in h10:
        full = make_valid(shape(f['geometry']))
        g = full.intersection(county)
        a = area_km2(g, lat0)
        if a < 0.5: continue
        if f['properties']['areasqkm'] > 5000: continue  # open-water Great Lakes units (e.g. 'Lake Michigan'), not land watersheds
        bx = full.bounds; kx, ky = km_scale(lat0)
        if f['properties']['huc10'].startswith('04') and (bx[2] - bx[0]) * kx * (bx[3] - bx[1]) * ky > 20 * f['properties']['areasqkm']: continue  # Great Lakes open-water units such as Lake Erie's 'Pelee Island' (not river corridors)
        groups.append([f['properties']['huc10'], clean_name(f['properties']['name']), g, a, f['properties']['areasqkm'], full])
    groups.sort(key=lambda r: -r[3])
    gb = [g[5].bounds for g in groups]
    BB = (min([b[0] for b in gb] + [bb[0]]) - .01, min([b[1] for b in gb] + [bb[1]]) - .01, max([b[2] for b in gb] + [bb[2]]) + .01, max([b[3] for b in gb] + [bb[3]]) + .01)
    h12 = []
    for g in groups:  # one small request per watershed (a single big box can overload the service near the Great Lakes)
        h12 += wbd_query(6, g[5].bounds, 'huc12,name,areasqkm', where=f"huc12 LIKE '{g[0]}%'")
    sheds = []
    for f in h12:
        code = f['properties']['huc12']
        gi = next((i for i, gr in enumerate(groups) if code.startswith(gr[0])), None)
        if gi is None: continue
        full = make_valid(shape(f['geometry']))
        g = full.intersection(county)
        a = area_km2(g, lat0) if not g.is_empty else 0.0
        if a < 0.25: a, g = 0.0, g.__class__() if not g.is_empty else g
        fa = f['properties']['areasqkm']
        sheds.append([code, clean_name(f['properties']['name']), g, a, min(1, a / max(fa, .01)), gi, full, fa])
    sheds.sort(key=lambda r: (r[5], -r[3], -r[7]))
    if len(sheds) > 254: sys.exit('too many subwatersheds')
    print(len(groups), 'watersheds,', sum(1 for r in sheds if r[3] > 0), 'subwatersheds in the county,', len(sheds), 'in the full watersheds')
    sheds_cty = [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in sheds]
    sheds_full = [(r[0], r[1], r[6], r[7], r[4], r[5]) for r in sheds]

    # every county the full watersheds touch, for water, roads and places
    near = [(p, g) for p, g in counties if g.intersects(box(*BB))]
    states = sorted(set(p['STATEFP'] for p, g in near))
    print('data from', len(near), 'counties in states', states)
    aw, lw, roads = [], [], []
    for p, g in near:
        gg = p['GEOID']
        for kind in ('areawater', 'linearwater', 'roads'):
            try:
                rows = read_shp_zip(fetch(TIGER + f'TIGER2023/{kind.upper()}/tl_2023_{gg}_{kind}.zip', os.path.join(RAW, f'tl_2023_{gg}_{kind}.zip')))
            except Exception as ex:
                if gg == geoid or gg in extra: raise
                print('skipping neighbour file', gg, kind, ex); continue
            if kind == 'areawater': aw += rows
            elif kind == 'linearwater': lw += rows
            else: roads += [(q, r) for q, r in rows if q['MTFCC'] in ('S1100', 'S1200', 'S1400')]
    aw = [(p, make_valid(g)) for p, g in aw]
    pl = []
    for sf in states:
        pl += read_shp_zip(fetch(TIGER + f'GENZ2023/shp/cb_2023_{sf}_place_500k.zip', os.path.join(RAW, f'cb_2023_{sf}_place_500k.zip')))
    BBg = box(*BB)
    places, munis = [], []
    for p, g in pl:
        if not g.intersects(BBg): continue
        g = make_valid(g)
        munis.append((p['NAME'], g))
        a = area_km2(g, lat0)
        if a >= 0.3: places.append((p['NAME'], g.representative_point(), a))
    parks, pois = osm_layers(BB, os.path.join(RAW, f'osm_{geoid}_full.json'))
    print(len(roads), 'roads,', len(munis), 'cities,', len(parks), 'parks,', len(pois), 'places')
    L = {'roads': roads, 'munis': munis, 'parks': parks, 'pois': pois}
    el = Elev(BB)
    cslug = slug(cname)
    os.makedirs(outdir, exist_ok=True)

    G = Grid(bb, 560)
    D = build_grid(G, county, county, el, sheds_cty, aw, lw, places, detail=False, extras=extra_geoms, L=L)
    D.update({'geoid': geoid, 'name': cname, 'state': sname, 'slug': cslug, 'kind': 'county', 'includes': includes,
              'areakm': round(area_km2(county, lat0), 2),
              'groups': [[g[0], g[1], round(g[3], 2), [i + 1 for i, sh in enumerate(sheds) if sh[5] == gi], round(g[4], 1)] for gi, g in enumerate(groups)],
              'sheds': [[sh[0], sh[1], round(sh[3], 2), round(sh[4], 3), sh[5] + 1, round(sh[7], 2)] for sh in sheds]})
    json.dump(D, open(os.path.join(outdir, 'county.json'), 'w'), separators=(',', ':'))
    print('county grid', G.nx, G.ny, round(G.cellkm, 3), 'km')

    for gi, g in enumerate(groups):
        b = g[5].bounds
        GG = Grid((b[0] - .004, b[1] - .004, b[2] + .004, b[3] + .004), 460)
        DD = build_grid(GG, county, g[5], el, sheds_full, aw, lw, places, detail=True, extras=extra_geoms, L=L)
        DD.update({'kind': 'watershed', 'huc10': g[0], 'name': g[1], 'areakm': round(g[3], 2), 'fullkm': round(g[4], 1)})
        json.dump(DD, open(os.path.join(outdir, g[0] + '.json'), 'w'), separators=(',', ':'))
        print(' ', g[0], g[1], GG.nx, GG.ny, round(GG.cellkm, 3))

    # small outline for the gallery card (lon/lat simplified)
    simp = county.simplify(0.003)
    outline = []
    for p in G.polys(simp):
        outline.append([[round(x, 4), round(y, 4)] for x, y in p.exterior.coords])
    return {'geoid': geoid, 'includes': includes, 'name': cname, 'state': sname, 'slug': cslug, 'areakm': D['areakm'],
            'watersheds': len(groups), 'subwatersheds': sum(1 for r in sheds if r[3] > 0), 'elo': D['elo'], 'ehi': D['ehi'],
            'bb': D['bb'], 'outline': outline}


if __name__ == '__main__':
    ex = [a.split('=', 1)[1] for a in sys.argv[3:] if a.startswith('--with=')]
    ex = [x for a in ex for x in a.split(',')]
    info = main(sys.argv[1], sys.argv[2], ex)
    json.dump(info, open(os.path.join(sys.argv[2], 'info.json'), 'w'), separators=(',', ':'))
    print('done', info['name'], info['watersheds'], info['subwatersheds'])
