from __future__ import annotations

import io
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROCESSED = ROOT / "data" / "processed"
DOCS_DATA = ROOT / "docs" / "data"
for p in [RAW, PROCESSED, DOCS_DATA]:
    p.mkdir(parents=True, exist_ok=True)


def read_config() -> dict[str, Any]:
    with open(ROOT / "scripts" / "config.yml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get(url: str, timeout: int = 120) -> requests.Response:
    headers = {"User-Agent": "dceh-dashboard/1.0 (public research dashboard)"}
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r


def normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [
        str(c).strip().lower().replace(" ", "_").replace("/", "_").replace("-", "_")
        for c in df.columns
    ]
    df.columns = [re.sub(r"_+", "_", c).strip("_") for c in df.columns]
    return df


def zfill_fips(x: Any, width: int = 5) -> str | None:
    if pd.isna(x):
        return None
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    s = re.sub(r"\D", "", s)
    if not s:
        return None
    return s.zfill(width)


def first_existing(df: pd.DataFrame, candidates: Iterable[str]) -> str | None:
    cols = set(df.columns)
    for c in candidates:
        if c in cols:
            return c
    return None


def percentile(s: pd.Series, higher_is_worse: bool = True) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    if x.notna().sum() < 3:
        return pd.Series(np.nan, index=s.index)
    p = x.rank(pct=True, method="average") * 100
    return p if higher_is_worse else 100 - p


def rowmean(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    valid = [c for c in cols if c in df.columns]
    if not valid:
        return pd.Series(np.nan, index=df.index)
    return df[valid].mean(axis=1, skipna=True)


def read_zip_csv(url: str, out_name: str) -> pd.DataFrame:
    print(f"Downloading {url}")
    content = get(url).content
    raw_path = RAW / out_name
    raw_path.write_bytes(content)
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        csv_names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise RuntimeError(f"No CSV found in {url}")
        with z.open(csv_names[0]) as f:
            return normalize_cols(pd.read_csv(f, low_memory=False))


def fetch_county_centroids(cfg: dict[str, Any]) -> pd.DataFrame:
    """Download Census county shapes and return county centroids for mapping."""
    try:
        import geopandas as gpd
    except Exception as e:  # pragma: no cover
        print(f"geopandas unavailable; county centroids will be missing: {e}")
        return pd.DataFrame(columns=["county_fips", "lat", "lon"])

    year = cfg["years"]["county_shapes"]
    url = cfg["sources"]["county_shapes_zip"].format(year=year)
    print(f"Downloading county shapes: {url}")
    zip_path = RAW / f"county_shapes_{year}.zip"
    zip_path.write_bytes(get(url).content)
    gdf = gpd.read_file(zip_path)
    gdf = gdf.to_crs("EPSG:4326")
    # For centroids, use representative points so the point lies inside the county polygon.
    reps = gdf.geometry.representative_point()
    out = pd.DataFrame(
        {
            "county_fips": gdf["GEOID"].astype(str).str.zfill(5),
            "county_name_shape": gdf["NAME"],
            "state_fips": gdf["STATEFP"].astype(str).str.zfill(2),
            "lon": reps.x,
            "lat": reps.y,
        }
    )
    return out



def discover_im3_data_urls(record_api: str) -> list[str]:
    """Find IM3 CSV/GPKG downloads from MSD-LIVE and GitHub.

    The IM3 record may expose files as CSV, GeoPackage, or API content links. This
    function is intentionally broad so the dashboard keeps working if the public
    data package changes format.
    """
    urls: list[str] = []

    print(f"Discovering IM3 data files from MSD-LIVE: {record_api}")
    try:
        rec = get(record_api).json()
        files = rec.get("files", {}).get("entries", {}) if isinstance(rec, dict) else {}
        for name, meta in files.items():
            lname = str(name).lower()
            if lname.endswith((".csv", ".gpkg", ".geojson", ".json")):
                link = meta.get("links", {}).get("content") or meta.get("links", {}).get("self")
                if link:
                    urls.append(link)

        def walk(obj: Any) -> None:
            if isinstance(obj, dict):
                for v in obj.values():
                    if isinstance(v, str):
                        low = v.lower()
                        if low.endswith((".csv", ".gpkg", ".geojson")) or ".csv?" in low or ".gpkg?" in low:
                            urls.append(v)
                    else:
                        walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)
        walk(rec)
    except Exception as e:
        print(f"WARNING: Could not discover IM3 files from MSD-LIVE API: {e}")

    # Fallback: inspect the public GitHub repository tree and pick data-center files.
    # This is important because the public atlas code/data may expose the geodatabase
    # through GitHub even when the MSD-LIVE API links are not CSV-readable.
    try:
        gh_api = "https://api.github.com/repos/IMMM-SFA/datacenter-atlas/git/trees/main?recursive=1"
        print(f"Discovering IM3 data files from GitHub tree: {gh_api}")
        tree = get(gh_api).json().get("tree", [])
        for item in tree:
            path = item.get("path", "")
            low = path.lower()
            if low.endswith((".csv", ".gpkg", ".geojson")) and (
                "data_center" in low or "datacenter" in low or "data-center" in low
            ):
                urls.append(f"https://raw.githubusercontent.com/IMMM-SFA/datacenter-atlas/main/{path}")
    except Exception as e:
        print(f"WARNING: Could not discover IM3 files from GitHub: {e}")

    cleaned: list[str] = []
    for u in urls:
        if u.startswith("/"):
            u = "https://data.msdlive.org" + u
        if u not in cleaned:
            cleaned.append(u)
    print(f"IM3 candidate data URLs discovered: {len(cleaned)}")
    for u in cleaned:
        print(f"  candidate: {u}")
    return cleaned



def _read_im3_gpkg(path: Path) -> pd.DataFrame:
    import geopandas as gpd

    frames: list[pd.DataFrame] = []
    layer_names = []
    try:
        import pyogrio
        layer_names = [x[0] for x in pyogrio.list_layers(path)]
    except Exception:
        layer_names = ["point", "building", "campus"]

    for layer in layer_names:
        try:
            gdf = gpd.read_file(path, layer=layer)
            if len(gdf) == 0:
                continue

            # Keep a usable coordinate before dropping geometry. Some IM3 layers carry
            # county_id as a 3-digit county code, not a full 5-digit state+county FIPS.
            # Coordinates allow us to recover the true county FIPS by spatial join.
            if gdf.geometry.name in gdf.columns and gdf.geometry.notna().any():
                try:
                    if gdf.crs is not None:
                        gdf = gdf.to_crs("EPSG:4326")
                    pts = gdf.geometry.representative_point()
                    if "lat" not in gdf.columns:
                        gdf["lat"] = pts.y
                    if "lon" not in gdf.columns:
                        gdf["lon"] = pts.x
                except Exception:
                    pass

            df = pd.DataFrame(gdf.drop(columns=["geometry"], errors="ignore"))
            df = normalize_cols(df)
            if "type" not in df.columns:
                df["type"] = layer
            frames.append(df)
            print(f"Read IM3 GPKG layer {layer}: {len(df):,} rows")
        except Exception as e:
            print(f"WARNING: Could not read GPKG layer {layer}: {e}")

    if not frames:
        raise RuntimeError("No readable layers found in IM3 GPKG.")
    return pd.concat(frames, ignore_index=True, sort=False)


STATE_ABBR_TO_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09",
    "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17",
    "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
    "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
    "WI": "55", "WY": "56", "PR": "72"
}


def _state_to_fips(value: Any) -> str | None:
    if pd.isna(value):
        return None
    s = str(value).strip().upper()
    if s.endswith(".0"):
        s = s[:-2]
    if s in STATE_ABBR_TO_FIPS:
        return STATE_ABBR_TO_FIPS[s]
    digits = re.sub(r"\D", "", s)
    if len(digits) == 1:
        return digits.zfill(2)
    if len(digits) == 2 and digits != "00":
        return digits
    return None


def _make_full_fips_from_state_county(dc: pd.DataFrame, county_col: str) -> pd.Series:
    state_col = first_existing(dc, ["state_fips", "state_code", "statefp", "state", "state_abbr", "stateabbr"])
    if state_col is None:
        return pd.Series([None] * len(dc), index=dc.index)

    state_part = dc[state_col].apply(_state_to_fips)
    county_part = dc[county_col].apply(lambda x: zfill_fips(x, 3))
    return state_part.fillna("") + county_part.fillna("")


def _county_fips_from_spatial_join(dc: pd.DataFrame, lat_col: str, lon_col: str, cfg: dict[str, Any]) -> pd.Series:
    import geopandas as gpd

    year = cfg["years"].get("county_shapes", 2024)
    url = cfg["sources"]["county_shapes_zip"].format(year=year)
    zip_path = RAW / f"county_shapes_{year}.zip"
    if not zip_path.exists():
        print(f"Downloading county shapes for data-center spatial join: {url}")
        zip_path.write_bytes(get(url).content)

    counties = gpd.read_file(zip_path)[["GEOID", "geometry"]].to_crs("EPSG:4326")
    temp = dc.copy()
    temp[lat_col] = pd.to_numeric(temp[lat_col], errors="coerce")
    temp[lon_col] = pd.to_numeric(temp[lon_col], errors="coerce")
    valid = temp[lat_col].notna() & temp[lon_col].notna()
    result = pd.Series([None] * len(temp), index=temp.index)
    if valid.sum() == 0:
        return result

    pts = gpd.GeoDataFrame(
        temp.loc[valid].copy(),
        geometry=gpd.points_from_xy(temp.loc[valid, lon_col], temp.loc[valid, lat_col]),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(pts, counties, how="left", predicate="within")
    result.loc[valid] = joined["GEOID"].astype(str).str.zfill(5).values
    return result


def fetch_data_centers(cfg: dict[str, Any]) -> pd.DataFrame:
    urls = discover_im3_data_urls(cfg["sources"]["im3_record_api"])
    if not urls:
        raise RuntimeError("No IM3 CSV/GPKG candidate URLs were discovered.")

    dfs: list[pd.DataFrame] = []
    for i, url in enumerate(urls, start=1):
        try:
            print(f"Downloading IM3 candidate {i}/{len(urls)}: {url}")
            content = get(url).content
            lower = url.lower()

            if ".gpkg" in lower or content[:16].lower().startswith(b"sqlite format"):
                gpkg_path = RAW / f"im3_data_center_{i}.gpkg"
                gpkg_path.write_bytes(content)
                df = _read_im3_gpkg(gpkg_path)
            elif lower.endswith(".geojson") or lower.endswith(".json"):
                import geopandas as gpd
                json_path = RAW / f"im3_data_center_{i}.geojson"
                json_path.write_bytes(content)
                gdf = gpd.read_file(json_path)
                if gdf.geometry.notna().any():
                    if gdf.crs is not None:
                        gdf = gdf.to_crs("EPSG:4326")
                    pts = gdf.geometry.representative_point()
                    if "lat" not in gdf.columns:
                        gdf["lat"] = pts.y
                    if "lon" not in gdf.columns:
                        gdf["lon"] = pts.x
                df = pd.DataFrame(gdf.drop(columns=["geometry"], errors="ignore"))
                df = normalize_cols(df)
            else:
                df = normalize_cols(pd.read_csv(io.BytesIO(content), low_memory=False))

            if len(df) > 0:
                dfs.append(df)
                print(f"Accepted IM3 candidate {i}: {len(df):,} rows; columns include {list(df.columns)[:12]}")
        except Exception as e:
            print(f"Skipping IM3 candidate because it could not be parsed: {url} ({e})")

    if not dfs:
        raise RuntimeError("No IM3 CSV/GPKG files could be parsed.")

    dc = pd.concat(dfs, ignore_index=True, sort=False)

    # Identify useful columns before constructing county_fips.
    county_col = first_existing(dc, ["county_id", "county_code", "countyfp", "county_fips", "fips", "geoid", "county_geoid"])
    lat_col = first_existing(dc, ["lat", "latitude", "y"])
    lon_col = first_existing(dc, ["lon", "lng", "longitude", "x"])

    # IMPORTANT FIX: IM3 county_id may be only the 3-digit county code. If we simply
    # zfill it to 5 digits, we get invalid values like 00001, which cannot merge with
    # real county FIPS such as 48113 for Dallas County, TX.
    dc["county_fips"] = None

    if county_col is not None:
        candidate = dc[county_col].apply(zfill_fips)
        full_fips_ok = candidate.notna() & candidate.str.match(r"^(?!00)\d{5}$", na=False)
        if full_fips_ok.mean() > 0.50:
            dc.loc[full_fips_ok, "county_fips"] = candidate.loc[full_fips_ok]
            print("Using full county FIPS already present in IM3 data.")
        else:
            constructed = _make_full_fips_from_state_county(dc, county_col)
            constructed_ok = constructed.notna() & constructed.str.match(r"^(?!00)\d{5}$", na=False)
            if constructed_ok.mean() > 0.50:
                dc.loc[constructed_ok, "county_fips"] = constructed.loc[constructed_ok]
                print("Constructed county FIPS from state + county_id fields.")

    if dc["county_fips"].notna().mean() < 0.50 and lat_col and lon_col:
        try:
            spatial = _county_fips_from_spatial_join(dc, lat_col, lon_col, cfg)
            spatial_ok = spatial.notna() & spatial.str.match(r"^(?!00)\d{5}$", na=False)
            dc.loc[spatial_ok, "county_fips"] = spatial.loc[spatial_ok]
            print(f"Recovered county FIPS by spatial join for {spatial_ok.sum():,} data center records.")
        except Exception as e:
            print(f"WARNING: spatial join for data-center county FIPS failed: {e}")

    dc = dc.dropna(subset=["county_fips"])
    dc = dc[dc["county_fips"].astype(str).str.match(r"^(?!00)\d{5}$", na=False)].copy()

    if len(dc) == 0:
        raise RuntimeError("IM3 data loaded, but no valid 5-digit county FIPS codes could be constructed.")

    id_col = first_existing(dc, ["id", "osm_id", "facility_id", "name", "ref"])
    sqft_col = first_existing(dc, ["sqft", "square_feet", "area_sqft", "area_ft2"])
    lat_col = first_existing(dc, ["lat", "latitude", "y"])
    lon_col = first_existing(dc, ["lon", "lng", "longitude", "x"])
    type_col = first_existing(dc, ["type", "geometry_type"])

    if sqft_col:
        dc[sqft_col] = pd.to_numeric(dc[sqft_col], errors="coerce")
    if lat_col:
        dc[lat_col] = pd.to_numeric(dc[lat_col], errors="coerce")
    if lon_col:
        dc[lon_col] = pd.to_numeric(dc[lon_col], errors="coerce")

    agg_spec = {"dc_records": ("county_fips", "size")}
    if id_col:
        agg_spec["dc_count"] = (id_col, pd.Series.nunique)
    else:
        agg_spec["dc_count"] = ("county_fips", "size")
    if sqft_col:
        agg_spec["dc_sqft"] = (sqft_col, "sum")
    if lat_col:
        agg_spec["dc_mean_lat"] = (lat_col, "mean")
    if lon_col:
        agg_spec["dc_mean_lon"] = (lon_col, "mean")

    out = dc.groupby("county_fips").agg(**agg_spec).reset_index()

    if type_col:
        types = dc.groupby("county_fips")[type_col].apply(
            lambda s: ", ".join(sorted(set(s.dropna().astype(str))))
        ).reset_index(name="dc_types")
        out = out.merge(types, on="county_fips", how="left")
    else:
        out["dc_types"] = ""

    print(f"Loaded data_centers: {len(out):,} counties; total dc_count = {out['dc_count'].sum():,.0f}")
    print("Texas data center counties found:", out[out["county_fips"].astype(str).str.startswith("48")].head(10).to_dict("records"))
    return out


def fetch_airdata(cfg: dict[str, Any]) -> pd.DataFrame:
    year = cfg["years"]["airdata"]
    aqi_url = cfg["sources"]["airdata_annual_aqi_by_county"].format(year=year)
    conc_url = cfg["sources"]["airdata_annual_conc_by_monitor"].format(year=year)
    aqi = read_zip_csv(aqi_url, f"annual_aqi_by_county_{year}.zip")
    conc = read_zip_csv(conc_url, f"annual_conc_by_monitor_{year}.zip")

    def county_fips_from_air(df: pd.DataFrame) -> pd.Series:
        st = first_existing(df, ["state_code", "state_code", "state_fips"])
        co = first_existing(df, ["county_code", "county_fips"])
        if st and co:
            return df[st].apply(lambda x: zfill_fips(x, 2)).fillna("") + df[co].apply(lambda x: zfill_fips(x, 3)).fillna("")
        f = first_existing(df, ["county_fips", "fips"])
        if f:
            return df[f].apply(zfill_fips)
        return pd.Series([None] * len(df), index=df.index)

    aqi["county_fips"] = county_fips_from_air(aqi)
    aqi_cols = {}
    for src, dest in [
        ("max_aqi", "max_aqi"),
        ("90th_percentile_aqi", "p90_aqi"),
        ("median_aqi", "median_aqi"),
        ("days_with_aqi", "days_with_aqi"),
        ("unhealthy_days", "unhealthy_days"),
        ("unhealthy_for_sensitive_groups_days", "usg_days"),
    ]:
        if src in aqi.columns:
            aqi_cols[src] = dest
    aqi = aqi[["county_fips"] + list(aqi_cols.keys())].rename(columns=aqi_cols)
    for c in aqi.columns:
        if c != "county_fips":
            aqi[c] = pd.to_numeric(aqi[c], errors="coerce")
    aqi = aqi.groupby("county_fips", as_index=False).mean(numeric_only=True)

    conc["county_fips"] = county_fips_from_air(conc)
    pname = first_existing(conc, ["parameter_name", "parameter", "pollutant_standard"])
    mean_col = first_existing(conc, ["arithmetic_mean", "observation_percent", "mean"])
    if pname and mean_col:
        conc[mean_col] = pd.to_numeric(conc[mean_col], errors="coerce")
        pm25 = conc[conc[pname].astype(str).str.contains("PM2.5", case=False, na=False)].groupby("county_fips")[mean_col].mean().reset_index(name="pm25_mean")
        ozone = conc[conc[pname].astype(str).str.contains("Ozone", case=False, na=False)].groupby("county_fips")[mean_col].mean().reset_index(name="ozone_mean")
        out = aqi.merge(pm25, on="county_fips", how="outer").merge(ozone, on="county_fips", how="outer")
    else:
        out = aqi
    return out


def fetch_places(cfg: dict[str, Any]) -> pd.DataFrame:
    url = cfg["sources"]["places_county_csv"]
    print(f"Downloading CDC PLACES: {url}")
    df = normalize_cols(pd.read_csv(io.BytesIO(get(url).content), low_memory=False))
    fips_col = first_existing(df, ["countyfips", "locationid", "county_fips", "geolocation_id", "location_id"])
    if fips_col is None:
        raise RuntimeError("Could not identify county FIPS column in CDC PLACES.")
    df["county_fips"] = df[fips_col].apply(zfill_fips)
    name_col = first_existing(df, ["countyname", "locationname", "name"])
    state_col = first_existing(df, ["stateabbr", "state_abbr", "state"])
    keep = ["county_fips"]
    rename = {}
    if name_col:
        keep.append(name_col)
        rename[name_col] = "county_name"
    if state_col:
        keep.append(state_col)
        rename[state_col] = "state"

    measures = {
        "asthma_prev": ["casthma_crudeprev", "casthema_crudeprev", "asthma_crudeprev"],
        "copd_prev": ["copd_crudeprev"],
        "chd_prev": ["chd_crudeprev"],
        "poor_physical_health_prev": ["phlth_crudeprev"],
        "poor_mental_health_prev": ["mhlth_crudeprev"],
        "depression_prev": ["depression_crudeprev"],
        "uninsured_places_prev": ["access2_crudeprev"],
    }
    for out_name, candidates in measures.items():
        col = first_existing(df, candidates)
        if col:
            keep.append(col)
            rename[col] = out_name
    out = df[keep].rename(columns=rename).drop_duplicates(subset=["county_fips"])
    for c in out.columns:
        if c not in ["county_fips", "county_name", "state"]:
            out[c] = pd.to_numeric(out[c], errors="coerce")
    return out



def fetch_acs(cfg: dict[str, Any]) -> pd.DataFrame:
    """Fetch ACS county population and optional socioeconomic controls.

    The dashboard only needs population for data-center intensity. The profile API
    sometimes rejects individual DP variables, so this function first uses the
    stable detailed ACS table B01003 for population, then tries profile controls
    separately. If controls fail, the dashboard still builds correctly.
    """
    year = cfg["years"]["acs"]
    out = pd.DataFrame(columns=["county_fips"])

    # Stable population request from detailed ACS 5-year API.
    try:
        base_detail = f"https://api.census.gov/data/{year}/acs/acs5"
        params = {"get": "NAME,B01003_001E", "for": "county:*"}
        print(f"Downloading ACS detailed population data: {base_detail}")
        resp = requests.get(base_detail, params=params, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        df = normalize_cols(pd.DataFrame(data[1:], columns=data[0]))
        df["county_fips"] = df["state"].astype(str).str.zfill(2) + df["county"].astype(str).str.zfill(3)
        out = df[["county_fips", "name", "b01003_001e"]].rename(columns={"name": "acs_name", "b01003_001e": "population"})
        out["population"] = pd.to_numeric(out["population"], errors="coerce")
    except Exception as e:
        print(f"WARNING ACS population request failed: {e}")
        out = pd.DataFrame(columns=["county_fips", "acs_name", "population"])

    # Optional profile controls. This may fail without breaking the dashboard.
    try:
        base_profile = cfg["sources"]["acs_profile_api"].format(year=year)
        vars_ = ["NAME", "DP03_0128PE", "DP03_0099PE", "DP04_0046PE"]
        params = {"get": ",".join(vars_), "for": "county:*"}
        print(f"Downloading ACS optional profile controls: {base_profile}")
        resp = requests.get(base_profile, params=params, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        prof = normalize_cols(pd.DataFrame(data[1:], columns=data[0]))
        prof["county_fips"] = prof["state"].astype(str).str.zfill(2) + prof["county"].astype(str).str.zfill(3)
        rename = {"dp03_0128pe": "poverty_rate", "dp03_0099pe": "uninsured_rate", "dp04_0046pe": "renter_share"}
        keep = ["county_fips"] + [c for c in rename if c in prof.columns]
        prof = prof[keep].rename(columns=rename)
        for c in prof.columns:
            if c != "county_fips":
                prof[c] = pd.to_numeric(prof[c], errors="coerce")
        out = out.merge(prof, on="county_fips", how="outer")
    except Exception as e:
        print(f"WARNING ACS optional profile controls skipped: {e}")

    return out


def fetch_eji_optional(cfg: dict[str, Any]) -> pd.DataFrame:
    """Try to obtain tract-level EJI from CDC ArcGIS and aggregate to county.

    The ArcGIS service schema can change. If this step fails, the index is built without EJI and weights are renormalized.
    """
    try:
        item = get(cfg["sources"]["eji_portal_item_api"]).json()
        service_url = item.get("url")
        if not service_url:
            print("EJI service URL not found in portal item; skipping EJI.")
            return pd.DataFrame(columns=["county_fips"])
        # Query layer 0 by default. If the service exposes a different layer structure, update here.
        layer_url = service_url.rstrip("/") + "/0/query"
        params = {
            "where": "1=1",
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
            "resultRecordCount": 2000,
        }
        print(f"Downloading EJI attributes from {layer_url}")
        rows = []
        offset = 0
        while True:
            params["resultOffset"] = offset
            js = requests.get(layer_url, params=params, timeout=120).json()
            feats = js.get("features", [])
            if not feats:
                break
            rows.extend([f.get("attributes", {}) for f in feats])
            if len(feats) < params["resultRecordCount"]:
                break
            offset += params["resultRecordCount"]
        if not rows:
            return pd.DataFrame(columns=["county_fips"])
        df = normalize_cols(pd.DataFrame(rows))
        fips_col = first_existing(df, ["fips", "geoid", "census_tract", "tractfips", "locationid"])
        if not fips_col:
            print("No tract/geoid field found in EJI; skipping EJI.")
            return pd.DataFrame(columns=["county_fips"])
        df["county_fips"] = df[fips_col].apply(lambda x: zfill_fips(x, 11)).str[:5]
        eji_col = None
        for c in df.columns:
            low = c.lower()
            if ("eji" in low and ("rank" in low or "rpl" in low or "score" in low)) or low in ["rpl_themes", "rpl_eji"]:
                eji_col = c
                break
        if not eji_col:
            print("No EJI rank/score field found; skipping EJI.")
            return pd.DataFrame(columns=["county_fips"])
        df[eji_col] = pd.to_numeric(df[eji_col], errors="coerce")
        out = df.groupby("county_fips", as_index=False)[eji_col].mean().rename(columns={eji_col: "eji_score"})
        return out
    except Exception as e:
        print(f"EJI optional step failed; continuing without EJI. Error: {e}")
        return pd.DataFrame(columns=["county_fips"])


def build_index(df: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    out = df.copy()

    # Derived metrics. Use a Series default so the script still runs when an optional
    # source fails or a column is missing.
    def numeric_column(name: str, default: float = np.nan) -> pd.Series:
        if name in out.columns:
            return pd.to_numeric(out[name], errors="coerce")
        return pd.Series(default, index=out.index, dtype="float64")

    out["dc_sqft"] = numeric_column("dc_sqft", 0).fillna(0)
    out["dc_count"] = numeric_column("dc_count", 0).fillna(0)
    out["population"] = numeric_column("population", np.nan)
    out["dc_sqft_per_100k"] = np.where(out["population"] > 0, out["dc_sqft"] / out["population"] * 100000, np.nan)
    out["dc_count_per_100k"] = np.where(out["population"] > 0, out["dc_count"] / out["population"] * 100000, np.nan)

    # Percentile variables
    raw_vars = [
        "dc_count", "dc_sqft_per_100k", "dc_count_per_100k",
        "max_aqi", "p90_aqi", "pm25_mean", "ozone_mean", "unhealthy_days", "usg_days",
        "asthma_prev", "copd_prev", "chd_prev", "poor_physical_health_prev", "poor_mental_health_prev", "depression_prev",
        "poverty_rate", "uninsured_rate", "uninsured_places_prev", "renter_share", "eji_score",
    ]
    for c in raw_vars:
        if c in out.columns:
            out[c + "_pct"] = percentile(out[c])

    out["data_center_pressure"] = rowmean(out, ["dc_count_pct", "dc_sqft_per_100k_pct", "dc_count_per_100k_pct"])
    out["pollution_exposure"] = rowmean(out, ["max_aqi_pct", "p90_aqi_pct", "pm25_mean_pct", "ozone_mean_pct", "unhealthy_days_pct", "usg_days_pct"])
    out["health_vulnerability"] = rowmean(out, ["asthma_prev_pct", "copd_prev_pct", "chd_prev_pct", "poor_physical_health_prev_pct", "poor_mental_health_prev_pct", "depression_prev_pct"])
    out["social_vulnerability"] = rowmean(out, ["poverty_rate_pct", "uninsured_rate_pct", "uninsured_places_prev_pct", "renter_share_pct"])
    out["environmental_justice"] = rowmean(out, ["eji_score_pct"])

    domain_cols = ["data_center_pressure", "pollution_exposure", "health_vulnerability", "social_vulnerability", "environmental_justice"]
    # Renormalize weights if optional domains are missing.
    score = pd.Series(0.0, index=out.index)
    weight_sum = pd.Series(0.0, index=out.index)
    for d in domain_cols:
        if d in out.columns:
            w = float(weights.get(d, 0))
            vals = out[d]
            mask = vals.notna()
            score.loc[mask] += vals.loc[mask] * w
            weight_sum.loc[mask] += w
    out["dcehpi"] = np.where(weight_sum > 0, score / weight_sum, np.nan)
    out["dcehpi_rank"] = out["dcehpi"].rank(ascending=False, method="min")

    def classify(r: pd.Series) -> str:
        dc = r.get("data_center_pressure", np.nan)
        pol = r.get("pollution_exposure", np.nan)
        hlth = r.get("health_vulnerability", np.nan)
        if pd.notna(dc) and pd.notna(pol) and pd.notna(hlth) and dc >= 75 and pol >= 75 and hlth >= 75:
            return "Highest priority: data center + pollution + health overlap"
        if pd.notna(dc) and pd.notna(pol) and dc >= 75 and pol >= 75:
            return "Energy-environment priority"
        if pd.notna(dc) and pd.notna(hlth) and dc >= 75 and hlth >= 75:
            return "Infrastructure-health monitoring priority"
        if pd.notna(hlth) and hlth >= 75:
            return "High health vulnerability"
        if pd.notna(dc) and dc >= 75:
            return "High data center pressure"
        return "Lower combined pressure"

    out["priority_group"] = out.apply(classify, axis=1)
    return out


def write_dictionary(path: Path) -> None:
    rows = [
        ("county_fips", "5-digit county FIPS code"),
        ("county_name", "County name from CDC PLACES, where available"),
        ("state", "State abbreviation, where available"),
        ("population", "ACS county population estimate"),
        ("dc_count", "Number of unique data center records/facilities in the county from IM3"),
        ("dc_sqft", "Total data center square footage in the county where IM3 reports area"),
        ("dc_sqft_per_100k", "Total data center square footage per 100,000 residents"),
        ("max_aqi", "Maximum annual AQI from EPA AirData annual AQI by county"),
        ("p90_aqi", "90th percentile AQI from EPA AirData annual AQI by county"),
        ("pm25_mean", "Mean annual PM2.5 concentration across county monitors"),
        ("ozone_mean", "Mean annual ozone concentration across county monitors"),
        ("asthma_prev", "CDC PLACES crude prevalence of current asthma"),
        ("copd_prev", "CDC PLACES crude prevalence of COPD"),
        ("chd_prev", "CDC PLACES crude prevalence of coronary heart disease"),
        ("poor_physical_health_prev", "CDC PLACES crude prevalence of poor physical health"),
        ("poor_mental_health_prev", "CDC PLACES crude prevalence of poor mental health"),
        ("data_center_pressure", "Percentile-based domain score summarizing data center count/intensity"),
        ("pollution_exposure", "Percentile-based domain score summarizing AQI, PM2.5, ozone, and unhealthy days"),
        ("health_vulnerability", "Percentile-based domain score summarizing respiratory, cardiovascular, and health-status measures"),
        ("social_vulnerability", "Percentile-based domain score summarizing ACS/PLACES vulnerability measures"),
        ("environmental_justice", "Optional EJI/climate burden score if retrieved"),
        ("dcehpi", "Data Center Environmental Health Pressure Index, 0-100 percentile-like weighted score"),
        ("priority_group", "Dashboard classification based on high-overlap thresholds"),
    ]
    pd.DataFrame(rows, columns=["variable", "description"]).to_csv(path, index=False)


def main() -> None:
    cfg = read_config()
    print("Building dashboard data...")
    pieces = []
    errors = []

    # Each block is separate so the dashboard can still build with available public sources if one source changes.
    for name, func in [
        ("data_centers", fetch_data_centers),
        ("airdata", fetch_airdata),
        ("places", fetch_places),
        ("acs", fetch_acs),
        ("eji_optional", fetch_eji_optional),
        ("county_centroids", fetch_county_centroids),
    ]:
        try:
            df = func(cfg)
            if "county_fips" in df.columns:
                pieces.append((name, df))
                df.to_csv(PROCESSED / f"{name}.csv", index=False)
                print(f"Loaded {name}: {len(df):,} rows")
            else:
                print(f"Skipping {name}: no county_fips column")
        except Exception as e:
            msg = f"{name}: {type(e).__name__}: {e}"
            print("WARNING", msg)
            errors.append(msg)

    if not pieces:
        raise RuntimeError("No data sources could be loaded. Check source URLs and internet access.")

    base = None
    for name, df in pieces:
        df = df.drop_duplicates(subset=["county_fips"])
        if base is None:
            base = df.copy()
        else:
            base = base.merge(df, on="county_fips", how="outer", suffixes=("", f"_{name}"))
    assert base is not None

    # Prefer official names from PLACES, otherwise ACS NAME.
    if "county_name" not in base.columns and "name" in base.columns:
        base["county_name"] = base["name"]
    if "county_name" in base.columns and "name" in base.columns:
        base["county_name"] = base["county_name"].fillna(base["name"])
    if "state" not in base.columns:
        base["state"] = base["county_fips"].astype(str).str[:2]

    out = build_index(base, cfg["index_weights"])

    # Stable output order: highest pressure first.
    out = out.sort_values(["dcehpi", "dc_count"], ascending=[False, False])
    numeric_cols = out.select_dtypes(include=[np.number]).columns
    out[numeric_cols] = out[numeric_cols].round(4)

    county_csv = ROOT / cfg["output"]["county_csv"]
    meta_json = ROOT / cfg["output"]["metadata_json"]
    dict_csv = ROOT / cfg["output"]["dictionary_csv"]
    out.to_csv(county_csv, index=False)
    write_dictionary(dict_csv)
    metadata = {
        "built_at_utc": datetime.now(timezone.utc).isoformat(),
        "airdata_year": cfg["years"]["airdata"],
        "acs_year": cfg["years"]["acs"],
        "places_release": cfg["years"]["places_release"],
        "county_shapes_year": cfg["years"]["county_shapes"],
        "rows": int(len(out)),
        "sources_loaded": [name for name, _ in pieces],
        "warnings": errors,
        "interpretation": "DCEHPI is a screening index, not a causal health-impact estimate.",
    }
    meta_json.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Wrote {county_csv}")
    print(f"Wrote {meta_json}")
    print(f"Wrote {dict_csv}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
