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



# -----------------------------
# NDVI Time-Series Function
# -----------------------------
def generate_ndvi_timeseries(country_name, year):
    try:
        if country_name == "World":
            geometry = ee.Geometry.Polygon([
                [-180, -90], [-180, 90], [180, 90], [180, -90], [-180, -90]
            ])
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
            geometry = ee.Geometry.Polygon([
                [-180, -90], [-180, 90], [180, 90], [180, -90], [-180, -90]
            ])
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
                .filterDate(date.advance(-16, 'day').format('YYYY-MM-dd'),
                            date.advance(-1, 'day').format('YYYY-MM-dd')) \
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
        bloom_values = [
            f['properties']['Bloom'] / 10000.0 if f['properties']['Bloom'] is not None else None
            for f in features
        ]

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
