import os
import ee
import tempfile
import folium
from folium.plugins import Fullscreen
from flask import Flask, render_template, request
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from datetime import datetime

# -----------------------------
# Earth Engine Initialization (env variable key)
# -----------------------------
SERVICE_ACCOUNT = "earth-engine-user@bloomwatch-474023.iam.gserviceaccount.com"
KEY_JSON = os.environ.get("EE_KEY_JSON")
if not KEY_JSON:
    raise ValueError("EE_KEY_JSON environment variable is not set.")

with tempfile.NamedTemporaryFile(mode="w+", delete=False) as f:
    f.write(KEY_JSON)
    KEY_FILE = f.name

credentials = ee.ServiceAccountCredentials(SERVICE_ACCOUNT, KEY_FILE)
ee.Initialize(credentials)

# -----------------------------
# Countries Setup
# -----------------------------
countries_fc = ee.FeatureCollection('USDOS/LSIB_SIMPLE/2017')
all_countries = ["World"] + sorted(countries_fc.aggregate_array('country_na').getInfo())

# -----------------------------
# NDVI + Bloom Map Function
# -----------------------------
def get_ndvi_and_bloom_map(
    country_name,
    selected_years,
    show_ndvi=True,
    show_bloom=True,
    show_sentinel=True,
    proj_scale=500,
    zoom_start=3,
    center=[20, 0],
    use_reduce_resolution=False
):
    try:
        selected_years = [int(y) for y in selected_years if str(y).isdigit()]
        if not selected_years:
            selected_years = [2023]
        last_year = int(selected_years[-1])

        country_geom = None if country_name == "World" else countries_fc.filter(ee.Filter.eq('country_na', country_name)).geometry()
        country_fc = countries_fc.filter(ee.Filter.eq('country_na', country_name)) if country_name != "World" else None

        # --- MODIS NDVI (existing) ---
        ndvi_collection = ee.ImageCollection('MODIS/006/MOD13Q1') \
            .filter(ee.Filter.calendarRange(last_year, last_year, 'year')) \
            .select('NDVI')
        ndvi_prev_collection = ee.ImageCollection('MODIS/006/MOD13Q1') \
            .filter(ee.Filter.calendarRange(last_year-1, last_year-1, 'year')) \
            .select('NDVI')

        ndvi_current = ndvi_collection.mean()
        ndvi_prev = ndvi_prev_collection.mean()
        if country_geom:
            ndvi_current = ndvi_current.clip(country_geom)
            ndvi_prev = ndvi_prev.clip(country_geom)

        modis_bloom_diff = ndvi_current.subtract(ndvi_prev).setDefaultProjection(crs='EPSG:4326', scale=proj_scale)
        if country_geom:
            modis_bloom_diff = modis_bloom_diff.clip(country_geom)
        if use_reduce_resolution:
            modis_bloom_mask = modis_bloom_diff.updateMask(modis_bloom_diff.gt(50)) \
                .reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024) \
                .reproject(crs='EPSG:4326', scale=proj_scale)
        else:
            modis_bloom_mask = modis_bloom_diff.updateMask(modis_bloom_diff.gt(50))

        # --- Sentinel-2 NDVI ---
        sentinel_col = ee.ImageCollection('COPERNICUS/S2_SR') \
            .filter(ee.Filter.calendarRange(last_year, last_year, 'year')) \
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)) \
            .select(['B4', 'B8'])  # Red and NIR
        sentinel_prev_col = ee.ImageCollection('COPERNICUS/S2_SR') \
            .filter(ee.Filter.calendarRange(last_year-1, last_year-1, 'year')) \
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)) \
            .select(['B4', 'B8'])

        def compute_ndvi(img):
            ndvi = img.normalizedDifference(['B8', 'B4']).rename('NDVI')
            return ndvi

        sentinel_ndvi = sentinel_col.map(compute_ndvi).mean()
        sentinel_prev_ndvi = sentinel_prev_col.map(compute_ndvi).mean()

        if country_geom:
            sentinel_ndvi = sentinel_ndvi.clip(country_geom)
            sentinel_prev_ndvi = sentinel_prev_ndvi.clip(country_geom)

        sentinel_bloom_diff = sentinel_ndvi.subtract(sentinel_prev_ndvi)
        if use_reduce_resolution:
            sentinel_bloom_mask = sentinel_bloom_diff.updateMask(sentinel_bloom_diff.gt(0.1)) \
                .reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024) \
                .reproject(crs='EPSG:4326', scale=proj_scale)
        else:
            sentinel_bloom_mask = sentinel_bloom_diff.updateMask(sentinel_bloom_diff.gt(0.1))

        # --- Optionally, overlap MODIS bloom and Sentinel bloom ---
        overlap_bloom = modis_bloom_mask.updateMask(sentinel_bloom_mask)

        # --- Visualization parameters ---
        ndvi_vis = {'min': 0, 'max': 9000, 'palette': ['#ff0000', '#ff7f00', '#ffff00', '#00ff00', '#006600']}
        modis_bloom_vis = {'min': 50, 'max': 1000, 'palette': ['#ffb6c1', '#ff69b4', '#ff00ff', '#800080']}
        sentinel_bloom_vis = {'min': 0.1, 'max': 1, 'palette': ['#800080', '#da70d6', '#ee82ee']}

        # --- Folium Map ---
        m = folium.Map(location=center, zoom_start=zoom_start, tiles=None, control_scale=True)
        folium.TileLayer(
            tiles="https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png",
            attr="&copy; OpenStreetMap contributors &copy; CARTO",
            name="CartoDB Positron No Labels",
            overlay=False,
            control=True,
        ).add_to(m)

        if show_ndvi:
            ndvi_mapid = ndvi_current.getMapId(ndvi_vis)
            folium.TileLayer(
                tiles=ndvi_mapid['tile_fetcher'].url_format,
                attr='MODIS NDVI',
                name='NDVI',
                overlay=True,
                control=True
            ).add_to(m)

        if show_bloom:
            modis_mapid = modis_bloom_mask.getMapId(modis_bloom_vis)
            folium.TileLayer(
                tiles=modis_mapid['tile_fetcher'].url_format,
                attr='MODIS Bloom',
                name='MODIS Bloom',
                overlay=True,
                control=True
            ).add_to(m)

        if show_sentinel:
            sentinel_mapid = overlap_bloom.getMapId(sentinel_bloom_vis)
            folium.TileLayer(
                tiles=sentinel_mapid['tile_fetcher'].url_format,
                attr='Sentinel Bloom',
                name='Sentinel Bloom',
                overlay=True,
                control=True
            ).add_to(m)

        # --- Country border and zoom ---
        if country_name != "World" and country_geom:
            styled_country = country_fc.style(
                color='red', width=3, fillColor='00000000'
            )
            country_mapid = styled_country.getMapId({})
            folium.TileLayer(
                tiles=country_mapid['tile_fetcher'].url_format,
                attr=f'{country_name} Borders',
                name=f'{country_name} Border',
                overlay=True,
                control=True
            ).add_to(m)

            bounds = country_geom.bounds().getInfo()['coordinates'][0]
            m.fit_bounds([[b[1], b[0]] for b in bounds])

        Fullscreen().add_to(m)
        folium.LayerControl(collapsed=False).add_to(m)
        return m.get_root().render()

    except Exception as e:
        return f"<h3>Error generating map for {country_name}: {str(e)}</h3>"

# -----------------------------
# Efficient Monthly NDVI/Bloom Time-Series Function
# -----------------------------
def generate_monthly_timeseries(country_name, year, kind="NDVI"):
    try:
        if country_name == "World":
            geometry = ee.Geometry.Polygon([[-180, -90], [-180, 90], [180, 90], [180, -90], [-180, -90]])
        else:
            country_fc = countries_fc.filter(ee.Filter.eq('country_na', country_name))
            geometry = country_fc.geometry()

        months = list(range(1, 13))
        def per_month(month):
            start = ee.Date.fromYMD(year, month, 1)
            end = start.advance(1, 'month')
            img = ee.ImageCollection("MODIS/061/MOD13Q1") \
                .filterDate(start, end) \
                .select('NDVI') \
                .mean()
            prev_img = ee.ImageCollection("MODIS/061/MOD13Q1") \
                .filterDate(start.advance(-1, 'month'), start) \
                .select('NDVI') \
                .mean()
            if kind == "NDVI":
                val = img.reduceRegion(ee.Reducer.mean(), geometry, 1000, maxPixels=1e13).get('NDVI')
            else:
                bloom = img.subtract(prev_img)
                val = bloom.reduceRegion(ee.Reducer.mean(), geometry, 1000, maxPixels=1e13).get('NDVI')
            return ee.Feature(None, {
                'date': start.format('YYYY-MM'),
                kind: val
            })
        features = ee.FeatureCollection(ee.List(months).map(lambda m: per_month(ee.Number(m))))
        features = features.filter(ee.Filter.notNull([kind])).getInfo()['features']
        dates = [datetime.strptime(f['properties']['date'], '%Y-%m') for f in features]
        values = [f['properties'][kind] / 10000.0 if f['properties'][kind] is not None else None for f in features]

        plt.figure(figsize=(12, 5))
        plt.plot(dates, values, marker='o', linestyle='-', color='green' if kind == "NDVI" else 'purple')
        plt.title(f"{kind} Monthly Time Series for {country_name} ({year})")
        plt.xlabel("Month")
        plt.ylabel(kind)
        plt.grid(True)
        plt.tight_layout()
        output_dir = os.path.join('static', 'charts')
        os.makedirs(output_dir, exist_ok=True)
        filename = f"{kind.lower()}_monthly_timeseries_{country_name}_{year}.png".replace(" ", "_")
        filepath = os.path.join(output_dir, filename)
        plt.savefig(filepath)
        plt.close()
        return f"/static/charts/{filename}"
    except Exception as e:
        print(f"Error generating {kind} monthly time-series: {e}")
        return None

# -----------------------------
# Flask App
# -----------------------------
app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    selected_country = request.form.get('country', 'World')
    selected_years = request.form.getlist('year')
    show_ndvi = 'show_ndvi' in request.form
    show_bloom = 'show_bloom' in request.form
    show_bloom_graph = 'show_bloom_graph' in request.form

    selected_years = [y for y in selected_years if str(y).isdigit()]
    if not selected_years:
        selected_years = [2023]

    years = list(range(2005, 2024))

    try:
        ndvi_map = get_ndvi_and_bloom_map(selected_country, selected_years, show_ndvi, show_bloom)
    except Exception as e:
        ndvi_map = f"<h3>Error generating map: {str(e)}</h3>"

    timeseries_url = generate_monthly_timeseries(selected_country, int(selected_years[0]), kind="NDVI") if selected_years else None
    bloom_timeseries_url = generate_monthly_timeseries(selected_country, int(selected_years[0]), kind="Bloom") if (show_bloom_graph and selected_years) else None

    return render_template(
        'index.html',
        map=ndvi_map,
        countries=all_countries,
        selected_country=selected_country,
        selected_years=[str(y) for y in selected_years],
        years=years,
        show_ndvi=show_ndvi,
        show_bloom=show_bloom,
        timeseries_url=timeseries_url,
        show_bloom_graph=show_bloom_graph,
        bloom_timeseries_url=bloom_timeseries_url
    )

if __name__ == "__main__":
    app.run(debug=True)
