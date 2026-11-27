# 3D Watershed Map Library: adding counties

These scripts build the data behind `watershed-maps.html` (the gallery) and `watershed-map.html` (the 3D viewer).

## Add a county

```
pip install shapely pyshp numpy pillow playwright
python3 tools/watershed-maps/build_county.py <county GEOID> watershed-maps/data/<county GEOID> [--with=<GEOID>,...]
python3 -m http.server 8765            # in another terminal, from the site folder
python3 tools/watershed-maps/thumbs.py http://localhost:8765 . <county GEOID>
python3 tools/watershed-maps/build_manifest.py .
```

- The county GEOID is the 5-digit state + county FIPS code (St. Louis County, Missouri is 29189).
- `--with` adds another area inside the same map with a pink outline. St. Louis County uses `--with=29510` for the City of St. Louis.
- `build_manifest.py` refreshes the gallery list and the Midwest locator map. Counties in its `EXCLUDE` list
  (St. Charles County, 29183) never appear in the library.
- Downloads are cached in `tools/watershed-maps/raw/`, which is not committed.

## Data sources (public domain or open data)

- Elevation: Terrain Tiles on AWS Open Data (U.S. Geological Survey 3D Elevation Program in the U.S.)
- Watersheds (10-digit) and subwatersheds (12-digit): U.S. Geological Survey Watershed Boundary Dataset
- County, place, area water and linear water: U.S. Census Bureau cartographic boundary files and TIGER/Line 2023
