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

        modis_bloom_diff = ndvi_current.subtract(ndvi_prev).clip(country_geometry)
        if country_geom:
            modis_bloom_diff = modis_bloom_diff.clip(country_geom)
        if use_reduce_resolution:
            modis_bloom_mask = modis_bloom_diff.updateMask(modis_bloom_diff.gt(50)) \
                .reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024) \
                #.reproject(crs='EPSG:4326', scale=proj_scale)
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
                #.reproject(crs='EPSG:4326', scale=proj_scale)
        else:
            sentinel_bloom_mask = sentinel_bloom_diff.updateMask(sentinel_bloom_diff.gt(0.1))

        # --- Optionally, overlap MODIS bloom and Sentinel bloom ---
        overlap_bloom = modis_bloom_mask.updateMask(sentinel_bloom_mask)

        # --- Visualization parameters ---
        ndvi_vis = {'min': 0, 'max': 9000, 'palette': ['#ff0000', '#ff7f00', '#ffff00', '#00ff00', '#006600']}
        modis_bloom_vis = {'min': 50, 'max': 1000, 'palette': ['#ffb6c1', '#ff69b4', '#ff00ff', '#800080']}
        sentinel_bloom_vis = {'min': 0.1, 'max': 1, 'palette': ['#800080', '#da70d6', '#ee82ee']}

        # --- Folium Map ---
        m = folium.Map(location=center, zoom_start=zoom_start, control_scale=True, prefer_canvas=True, crs='EPSG3857')
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
# NDVI Time-Series Function
# -----------------------------
def generate_ndvi_timeseries(country_name, year):
    try:
        if country_name == "World":
            geometry = ee.Geometry.Polygon([[-180, -90], [-180, 90], [180, 90], [180, -90], [-180, -90]])
        else:
            country_fc = countries_fc.filter(ee.Filter.eq('country_na', country_name))
            geometry = country_fc.geometry()

        col = ee.ImageCollection("MODIS/061/MOD13Q1") \
            .filterBounds(geometry) \
            .filterDate(f'{year}-01-01', f'{year}-12-31') \
            .select('NDVI') \
            .sort('system:time_start')

        def compute_mean(img):
            mean_dict = img.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=geometry,
                scale=500,
                maxPixels=1e13
            )
            return ee.Feature(None, {
                'date': img.date().format('YYYY-MM-dd'),
                'NDVI': mean_dict.get('NDVI')
            })

        features = col.map(compute_mean).filter(ee.Filter.notNull(['NDVI'])).getInfo()['features']
        dates = [datetime.strptime(f['properties']['date'], '%Y-%m-%d') for f in features]
        ndvi_values = [f['properties']['NDVI'] / 10000.0 for f in features]

        plt.figure(figsize=(12, 5))
        plt.plot(dates, ndvi_values, marker='o', linestyle='-', color='green')
        plt.title(f"NDVI Time Series for {country_name} ({year})")
        plt.xlabel("Date")
        plt.ylabel("NDVI")
        plt.grid(True)
        plt.ylim(0, 1)
        plt.tight_layout()

        output_dir = os.path.join('static', 'charts')
        os.makedirs(output_dir, exist_ok=True)
        filename = f"ndvi_timeseries_{country_name}_{year}.png".replace(" ", "_")
        filepath = os.path.join(output_dir, filename)
        plt.savefig(filepath)
        plt.close()
        return f"/static/charts/{filename}"

    except Exception as e:
        print(f"Error generating time-series: {e}")
        return None

# -----------------------------
# Bloom Time-Series Function
# -----------------------------
def generate_bloom_timeseries(country_name, year):
    try:
        if country_name == "World":
            geometry = ee.Geometry.Polygon([[-180, -90], [-180, 90], [180, 90], [180, -90], [-180, -90]])
        else:
            country_fc = countries_fc.filter(ee.Filter.eq('country_na', country_name))
            geometry = country_fc.geometry()

        col = ee.ImageCollection("MODIS/061/MOD13Q1") \
            .filterBounds(geometry) \
            .filterDate(f'{year}-01-01', f'{year}-12-31') \
            .select('NDVI') \
            .sort('system:time_start')

        def compute_bloom(img):
            date = img.date()
            prev = ee.ImageCollection("MODIS/061/MOD13Q1") \
                .filterDate(date.advance(-16, 'day').format('YYYY-MM-dd'), date.advance(-1, 'day').format('YYYY-MM-dd')) \
                .select('NDVI') \
                .mean()
            bloom = img.subtract(prev)
            mean_dict = bloom.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=geometry,
                scale=500,
                maxPixels=1e13
            )
            return ee.Feature(None, {
                'date': date.format('YYYY-MM-dd'),
                'Bloom': mean_dict.get('NDVI')
            })

        features = col.map(compute_bloom).filter(ee.Filter.notNull(['Bloom'])).getInfo()['features']
        dates = [datetime.strptime(f['properties']['date'], '%Y-%m-%d') for f in features]
        bloom_values = [f['properties']['Bloom'] / 10000.0 if f['properties']['Bloom'] is not None else None for f in features]

        plt.figure(figsize=(12, 5))
        plt.plot(dates, bloom_values, marker='o', linestyle='-', color='purple')
        plt.title(f"Bloom Time Series for {country_name} ({year})")
        plt.xlabel("Date")
        plt.ylabel("Bloom (NDVI difference)")
        plt.grid(True)
        plt.tight_layout()

        output_dir = os.path.join('static', 'charts')
        os.makedirs(output_dir, exist_ok=True)
        filename = f"bloom_timeseries_{country_name}_{year}.png".replace(" ", "_")
        filepath = os.path.join(output_dir, filename)
        plt.savefig(filepath)
        plt.close()
        return f"/static/charts/{filename}"

    except Exception as e:
        print(f"Error generating bloom time-series: {e}")
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

    timeseries_url = generate_ndvi_timeseries(selected_country, selected_years[0]) if selected_years else None
    bloom_timeseries_url = generate_bloom_timeseries(selected_country, selected_years[0]) if (show_bloom_graph and selected_years) else None

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
