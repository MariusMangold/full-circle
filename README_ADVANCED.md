# Every Single Street · Advanced

This is a second, isolated Streamlit app. The stable `app.py` and
`route_engine.py` remain the deployment entry point for the original version.

## Features

- Select OpenStreetMap road categories to include in the route.
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
streamlit run app_advanced.py
```

## Deploy beside the stable app

In Streamlit Community Cloud, create a **second app** from the same GitHub
repository and branch. Set its main file path to:

```text
app_advanced.py
```

This produces a second public URL and does not change the stable deployment.
