"""
fix_fr_no.py
Targeted fix for countries Natural Earth assigns ISO_A2 = -1.
Uses NAME-based matching instead of ISO2 for these specific cases.

Run from backend/:
    python.exe scripts/visual/fix_fr_no.py
"""
import json
import os
import urllib.request
from pathlib import Path
from urllib.parse import urlparse
import psycopg2
from dotenv import load_dotenv

from geo_shape import ensure_shape_columns

BACKEND_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_ROOT / "generated" / "visual_shapes"
GEOJSON_50M = DATA_DIR / "ne_50m_countries.geojson"
GEOJSON_110M = DATA_DIR / "ne_110m_countries.geojson"

SVG_WIDTH  = 200
SVG_HEIGHT = 150
PADDING    = 8
FILL_COLOR = "#4A90D9"

GEOJSON_50M_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector"
    "/master/geojson/ne_50m_admin_0_countries.geojson"
)

GEOJSON_110M_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector"
    "/master/geojson/ne_110m_admin_0_countries.geojson"
)

# iso2 we want to fix -> list of NAME values to search in GeoJSON
NAME_FALLBACKS = {
    "FR": ["France", "Metropolitan France"],
    "NO": ["Norway"],
    "TW": ["Taiwan"],
    "XK": ["Kosovo"],
    "GF": ["French Guiana"],
    "GP": ["Guadeloupe"],
    "MQ": ["Martinique"],
    "RE": ["Reunion"],
}


def load_by_name(path) -> dict:
    """Returns normalized-name -> rings using several Natural Earth name fields."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    lookup = {}
    for feature in data["features"]:
        props = feature.get("properties", {})
        geom = feature.get("geometry", {})
        if not geom:
            continue
        rings = []
        if geom["type"] == "Polygon":
            rings.append(geom["coordinates"][0])
        elif geom["type"] == "MultiPolygon":
            for poly in geom["coordinates"]:
                rings.append(poly[0])
        if not rings:
            continue

        candidate_names = {
            props.get("NAME"),
            props.get("NAME_LONG"),
            props.get("ADMIN"),
            props.get("SOVEREIGNT"),
            props.get("FORMAL_EN"),
            props.get("NAME_EN"),
            props.get("name"),
        }
        for name in candidate_names:
            normalized = (name or "").strip().lower()
            if normalized:
                lookup[normalized] = rings
    return lookup


def download_geojson(url: str, local_path: str) -> bool:
    if os.path.exists(local_path) and os.path.getsize(local_path) > 50_000:
        return True
    print(f"  Downloading {local_path}...")
    req = urllib.request.Request(url, headers={"User-Agent": "AdaptIQ-PFE/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
        with open(local_path, "wb") as f:
            f.write(data)
        print(f"  Downloaded {len(data):,} bytes")
        return True
    except Exception as exc:
        print(f"  Download failed: {exc}")
        return False


def rings_to_svg(rings):
    all_lons, all_lats = [], []
    for ring in rings:
        for lon, lat in ring:
            all_lons.append(lon)
            all_lats.append(lat)
    if not all_lons:
        return ""
    min_lon, max_lon = min(all_lons), max(all_lons)
    min_lat, max_lat = min(all_lats), max(all_lats)
    lon_span = max_lon - min_lon or 1
    lat_span = max_lat - min_lat or 1
    draw_w = SVG_WIDTH  - 2 * PADDING
    draw_h = SVG_HEIGHT - 2 * PADDING
    scale = min(draw_w / lon_span, draw_h / lat_span)
    scaled_w = lon_span * scale
    scaled_h = lat_span * scale
    ox = PADDING + (draw_w - scaled_w) / 2
    oy = PADDING + (draw_h - scaled_h) / 2

    def proj(lon, lat):
        return round((lon - min_lon)*scale + ox, 2), round((max_lat - lat)*scale + oy, 2)

    parts = []
    for ring in rings:
        if len(ring) < 3:
            continue
        pts = " ".join(f"{proj(lo,la)[0]},{proj(lo,la)[1]}" for lo,la in ring)
        parts.append(f"M {pts} Z")
    path = " ".join(parts)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SVG_WIDTH} {SVG_HEIGHT}">'
        f'<path d="{path}" fill="{FILL_COLOR}" stroke="white" stroke-width="0.5"/>'
        f'</svg>'
    )


def main():
    backend_env = BACKEND_ROOT / ".env"
    if backend_env.exists():
        load_dotenv(backend_env)
    else:
        load_dotenv()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ensure_shape_columns()

    if not os.path.exists(GEOJSON_50M):
        download_geojson(GEOJSON_50M_URL, GEOJSON_50M)
    if not os.path.exists(GEOJSON_110M):
        download_geojson(GEOJSON_110M_URL, GEOJSON_110M)

    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://adaptiq:adaptiq@localhost:5433/adaptiq_db",
    )
    parsed = urlparse(database_url.replace("postgresql+asyncpg://", "postgresql://", 1))
    db = dict(
        host=parsed.hostname or "localhost",
        port=parsed.port or 5433,
        dbname=(parsed.path or "/adaptiq_db").lstrip("/"),
        user=parsed.username or "adaptiq",
        password=parsed.password or "adaptiq",
    )

    # Try 50m first (better coverage), fall back to 110m
    geojson_file = None
    for fname in [GEOJSON_50M, GEOJSON_110M]:
        if os.path.exists(fname) and os.path.getsize(fname) > 50_000:
            geojson_file = fname
            break

    if not geojson_file:
        print("No GeoJSON file found. Run ingest_shapes_v2.py first.")
        return

    print(f"Loading {geojson_file} by name...")
    by_name = load_by_name(geojson_file)
    print(f"  {len(by_name)} entries indexed by name")

    conn = psycopg2.connect(**db)
    cur  = conn.cursor()

    fixed = 0
    still_missing = []

    for iso2, name_candidates in NAME_FALLBACKS.items():
        # Check if this iso2 already has a shape
        cur.execute(
            "SELECT id FROM visual_questions WHERE topic='geography' AND iso2=%s AND shape_svg IS NULL",
            (iso2,)
        )
        rows = cur.fetchall()
        if not rows:
            print(f"  {iso2}: already has shape or not in DB - skip")
            continue

        # Try each name candidate
        rings = None
        matched_name = None
        for name in name_candidates:
            rings = by_name.get(name.lower())
            if rings:
                matched_name = name
                break

        if not rings:
            print(f"  {iso2}: not found by name ({name_candidates}) - skip")
            still_missing.append(iso2)
            continue

        svg = rings_to_svg(rings)
        if not svg:
            still_missing.append(iso2)
            continue

        for (row_id,) in rows:
            cur.execute(
                "UPDATE visual_questions SET shape_svg = %s WHERE id = %s",
                (svg, row_id)
            )
        print(f"  {iso2}: fixed via name '{matched_name}' ({len(rows)} row(s))")
        fixed += 1

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM visual_questions WHERE topic='geography' AND shape_svg IS NOT NULL;")
    total = cur.fetchone()[0]
    conn.close()

    print(f"\nDone. Fixed {fixed} countries.")
    print(f"Still missing: {still_missing}")
    print(f"Total shapes in DB: {total}/250")


if __name__ == "__main__":
    main()


