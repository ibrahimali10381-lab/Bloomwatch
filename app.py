import os
import json
import ee

SERVICE_ACCOUNT = 'earth-engine-user@bloomwatch-474023.iam.gserviceaccount.com'
KEY_JSON = os.environ.get('EE_KEY_JSON')  # <-- from Render env variable

if KEY_JSON is None:
    raise ValueError("EE_KEY_JSON environment variable is not set.")

# Write JSON to a temporary file
import tempfile
with tempfile.NamedTemporaryFile(mode='w+', delete=False) as key_file:
    key_file.write(KEY_JSON)
    key_file_path = key_file.name

credentials = ee.ServiceAccountCredentials(SERVICE_ACCOUNT, key_file_path)
ee.Initialize(credentials)

countries_fc = ee.FeatureCollection('USDOS/LSIB_SIMPLE/2017')
all_countries = sorted(countries_fc.aggregate_array('country_na').getInfo())


def get_ndvi_and_bloom_map(country_name, years, show_ndvi=True, show_bloom=True):
    try:
        latest_year = int(years[-1])
        prev_year = latest_year - 1

        if country_name == "World":
            geometry = ee.Geometry.Rectangle([-180, -90, 180, 90])
            center = [20, 0]
            zoom_start = 2
            proj_scale = 10000
            country_fc = None
            use_reduce_resolution = False
        else:
            country_fc = countries_fc.filter(ee.Filter.Or(
                ee.Filter.eq('country_na', country_name),
                ee.Filter.eq('ADMIN', country_name)
            ))
            country = country_fc.first()
            if not country:
                raise Exception(f"Country '{country_name}' not found in FeatureCollection.")
            geometry = country.geometry()
            centroid = geometry.centroid().getInfo()['coordinates']
            center = [centroid[1], centroid[0]]
            zoom_start = 5
            proj_scale = 500
            use_reduce_resolution = True


        ndvi_collection = ee.ImageCollection("MODIS/061/MOD13Q1") \
            .filterBounds(geometry) \
            .filterDate(f'{latest_year}-01-01', f'{latest_year}-12-31') \
            .select('NDVI')
        ndvi_image = ndvi_collection.mean()

        if country_name != "World":
            ndvi_image = ndvi_image.clip(geometry)

        ndvi_current = ee.ImageCollection("MODIS/061/MOD13Q1") \
            .filterBounds(geometry) \
            .filterDate(f'{latest_year}-01-01', f'{latest_year}-12-31') \
            .select('NDVI').mean()

        ndvi_prev = ee.ImageCollection("MODIS/061/MOD13Q1") \
            .filterBounds(geometry) \
            .filterDate(f'{prev_year}-01-01', f'{prev_year}-12-31') \
            .select('NDVI').mean()

        bloom_diff = ndvi_current.subtract(ndvi_prev).setDefaultProjection(crs='EPSG:4326', scale=proj_scale)

        if use_reduce_resolution:
            bloom_mask = bloom_diff.updateMask(bloom_diff.gt(50)) \
                .reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024) \
                .reproject(crs='EPSG:4326', scale=proj_scale) \
                .clip(geometry)
        else:
            bloom_mask = bloom_diff.updateMask(bloom_diff.gt(50))

        if country_name != "World":
            bloom_mask = bloom_mask.clip(geometry)

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
            ndvi_mapid = ndvi_image.getMapId(ndvi_vis)
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
            countries = countries_fc.getInfo()['features']
            for country in countries:
                if 'geometry' in country and country['geometry'] is not None:
                    folium.GeoJson(
                        data=country,
                        name=country['properties']['country_na'],
                        style_function=lambda f: {'fill': False, 'color': 'red', 'weight': 1}
                    ).add_to(m)
        else:
            folium.GeoJson(
                data=country_fc.getInfo(),
                name='Country Borders',
                style_function=lambda f: {'fill': False, 'color': 'red', 'weight': 2}
            ).add_to(m)
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

    if not selected_years:
        selected_years = ['2023']

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
        selected_years=selected_years,
        years=years,
        show_ndvi=show_ndvi,
        show_bloom=show_bloom
    )


if __name__ == "__main__":
    app.run(debug=True)
