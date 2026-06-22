# Every Single Street · Advanced

This is the active advanced Streamlit app in `app.py` and `route_engine.py`.
The previous stable version is preserved in `appold.py` and
`route_engine_old.py`.

## Features

- Load a city first and inspect the exact OpenStreetMap `highway=*` road types
  present in its walking network.
- Select detected road types to exclude from the route.
- Upload Polygon or MultiPolygon GeoJSON exclusion areas, including exports from
  [geojson.io](https://geojson.io/).
- View an interactive traversal-frequency map:
  - green: segment used once;
  - amber: segment used twice;
  - red: segment used three or more times.
- Inspect traversal histograms and road-category statistics.
- Download the route as GPX and per-segment statistics as CSV.

Filtering can break a street network into disconnected pieces. The route uses
the largest connected component remaining after all filters and exclusion areas
are applied.

## Run locally

Install the same dependencies as the stable app, then run:

```powershell
streamlit run app.py
```

## Deploy

In Streamlit Community Cloud, set the main file path to:

```text
app.py
```

To deploy the preserved old version separately, use `appold.py` as the main file.
