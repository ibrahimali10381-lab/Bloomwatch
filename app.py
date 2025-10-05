import os
import json
import ee
import tempfile
import folium
from folium.plugins import Fullscreen
from flask import Flask, render_template, request, redirect, url_for, session

# Get JSON string from environment
KEY_JSON = os.environ.get("EE_KEY_JSON")
if not KEY_JSON:
    raise ValueError("EE_KEY_JSON environment variable is not set.")

# Write JSON to a temporary file
with tempfile.NamedTemporaryFile(mode="w+", delete=False) as f:
    f.write(KEY_JSON)
    KEY_FILE = f.name

# Initialize Earth Engine
SERVICE_ACCOUNT = "earth-engine-user@bloomwatch-474023.iam.gserviceaccount.com"
credentials = ee.ServiceAccountCredentials(SERVICE_ACCOUNT, KEY_FILE)
ee.Initialize(credentials)

countries_fc = ee.FeatureCollection('USDOS/LSIB_SIMPLE/2017')
all_countries = ["World"] + sorted(countries_fc.aggregate_array('country_na').getInfo())

def get_ndvi_and_bloom_map(country_name, selected_years, show_ndvi=True, show_bloom=True,
                           proj_scale=500, zoom_start=3, center=[20,0], use_reduce_resolution=False):

    try:
        # Ensure selected_years is a list of valid ints, fallback to [2023]
        selected_years = [y for y in selected_years if str(y).isdigit()]
        if not selected_years:
            selected_years = [2023]
        else:
            selected_years = [int(y) for y in selected_years]
        last_year = int(selected_years[-1])

        # Debug: print types
        # print("selected_years (final):", selected_years, [type(y) for y in selected_years])
        # print("last_year:", last_year, type(last_year))

        ndvi_current = ee.ImageCollection('MODIS/006/MOD13Q1') \
            .filter(ee.Filter.calendarRange(last_year, last_year, 'year')) \
            .select('NDVI').mean()

        ndvi_prev = ee.ImageCollection('MODIS/006/MOD13Q1') \
            .filter(ee.Filter.calendarRange(last_year-1, last_year-1, 'year')) \
            .select('NDVI').mean()

        # Bloom difference
        bloom_diff = ndvi_current.subtract(ndvi_prev).setDefaultProjection(crs='EPSG:4326', scale=proj_scale)

        if use_reduce_resolution:
            bloom_mask = bloom_diff.updateMask(bloom_diff.gt(50)) \
                .reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024) \
                .reproject(crs='EPSG:4326', scale=proj_scale)
        else:
            bloom_mask = bloom_diff.updateMask(bloom_diff.gt(50))

        ndvi_vis = {
            'min': 0,
            'max': 9000,
            'palette': ['#ff0000', '#ff7f00', '#ffff00', '#00ff00', '#006600']
        }
        bloom_vis = {
            'min': 50,
            'max': 1000,
            'palette': ['#ffb6c1', '#ff69b4', '#ff00ff', '#800080']
        }

        m = folium.Map(location=center, zoom_start=zoom_start, tiles='OpenStreetMap', control_scale=True)

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
            bloom_mapid = bloom_mask.getMapId(bloom_vis)
            folium.TileLayer(
                tiles=bloom_mapid['tile_fetcher'].url_format,
                attr='MODIS Bloom',
                name='Bloom',
                overlay=True,
                control=True
            ).add_to(m)

        if country_name == "World":
            world_mapid = countries_fc.getMapId({'color': 'red'})
            folium.TileLayer(
                tiles=world_mapid['tile_fetcher'].url_format,
                attr='Earth Engine / Country Borders',
                name='Countries',
                overlay=True,
                control=True
            ).add_to(m)
        else:
            # Overlay just the selected country
            country_fc = countries_fc.filter(ee.Filter.eq('country_na', country_name))
            country_mapid = country_fc.getMapId({'color': 'red'})
            folium.TileLayer(
                tiles=country_mapid['tile_fetcher'].url_format,
                attr=f'{country_name} Borders',
                name=f'{country_name}',
                overlay=True,
                control=True
            ).add_to(m)

    geometry = country_fc.geometry()
    bounds = geometry.bounds().getInfo()['coordinates'][0]
    m.fit_bounds([[b[1], b[0]] for b in bounds])

            geometry = country_fc.geometry()
            bounds = geometry.bounds().getInfo()['coordinates'][0]
            m.fit_bounds([[b[1], b[0]] for b in bounds])

        Fullscreen().add_to(m)
        folium.LayerControl(collapsed=False).add_to(m)

        return m.get_root().render()

    except Exception as e:
        return f"<h3>Error generating map for {country_name}: {str(e)}</h3>"



app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    selected_country = request.form.get('country', 'World')
    selected_years = request.form.getlist('year')
    show_ndvi = 'show_ndvi' in request.form
    show_bloom = 'show_bloom' in request.form

    # Remove empty or non-numeric years, fallback to 2023
    selected_years = [y for y in selected_years if str(y).isdigit()]
    if not selected_years:
        selected_years = [2023]

    years = list(range(2005, 2024))

    try:
        ndvi_map = get_ndvi_and_bloom_map(selected_country, selected_years, show_ndvi, show_bloom)
    except Exception as e:
        ndvi_map = f"<h3>Error generating map: {str(e)}</h3>"

    return render_template(
        'index.html',
        map=ndvi_map,
        countries=all_countries,
        selected_country=selected_country,
        selected_years=[str(y) for y in selected_years],
        years=years,
        show_ndvi=show_ndvi,
        show_bloom=show_bloom
    )


if __name__ == "__main__":
    app.run(debug=True)
