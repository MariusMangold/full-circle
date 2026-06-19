# Every Single Street

A Streamlit web app that downloads a walkable OpenStreetMap network, creates a
closed route covering every street in its main connected component, displays it
on an interactive map, and exports it as GPX.

## Run locally

Python 3.12 is recommended.

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

The original desktop scripts and generated route files are retained as reference.
The web app uses `app.py` and `route_engine.py`.

## Deploy free on Streamlit Community Cloud

1. Create a GitHub repository and commit this folder. Large local `.graphml`,
   `.pkl`, and previously generated files do not need to be committed for the app.
2. Sign in at <https://share.streamlit.io> using GitHub.
3. Choose **Create app**, select the repository and branch, and set the main file
   path to `app.py`.
4. Deploy. No secrets or environment variables are required.

The free host may sleep when unused. The first request after waking—and the first
request for a new place—will therefore take longer. Cached graph and route files
live under `cache/web` and may disappear when a cloud instance is restarted.

## Safety limits and configuration

Exact Eulerisation can consume a lot of memory for a large city. The app rejects
large requests and asks the user for a district instead. Defaults can be changed
with environment variables:

| Variable | Default | Meaning |
|---|---:|---|
| `MAX_AREA_KM2` | `250` | Largest boundary downloaded |
| `MAX_NODES` | `12000` | Largest connected network |
| `MAX_EDGES` | `30000` | Largest connected network |
| `MAX_ODD_NODES` | `1200` | Eulerisation complexity guard |
| `ROUTE_CACHE_DIR` | `cache/web` | On-disk cache directory |

The app removes roads explicitly tagged as private/no-access, common driveway and
parking service roads, and mapped area edges. It does not apply the local
`sperrzonen.geojson`, because that file is specific to one area. OpenStreetMap data
can be incomplete; users must verify access and safety themselves.

## Other hosts

The app starts with `streamlit run app.py`. On a host that expects an explicit
port, use:

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port $PORT
```
