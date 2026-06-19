"""Streamlit UI for the Every Single Street route generator."""

from __future__ import annotations

import time

import folium
import streamlit as st
import streamlit.components.v1 as components

from route_engine import RouteError, RouteResult, RouteTooLargeError, generate_route


st.set_page_config(
    page_title="Every Single Street",
    page_icon="🗺️",
    layout="wide",
)

st.markdown(
    """
    <style>
      .block-container { max-width: 1180px; padding-top: 2.2rem; }
      [data-testid="stMetricValue"] { font-size: 1.7rem; }
      .subtle { color: #64748b; margin-top: -.6rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


PRESETS = {
    "Eppelheim, Germany": "Eppelheim, Baden-Württemberg, Germany",
    "Wieblingen, Heidelberg": "Wieblingen, Heidelberg, Germany",
    "Rohrbach, Heidelberg": "Rohrbach, Heidelberg, Germany",
    "Custom place…": "",
}


def route_map(result: RouteResult) -> folium.Map:
    coordinates = result.coordinates
    centre = coordinates[len(coordinates) // 2]
    map_view = folium.Map(location=centre, zoom_start=13, control_scale=True, tiles=None)
    folium.TileLayer(
        tiles="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="© OpenStreetMap contributors",
        name="OpenStreetMap",
        max_zoom=19,
    ).add_to(map_view)
    folium.PolyLine(
        coordinates,
        color="#00bcd4",
        weight=4,
        opacity=0.9,
        tooltip=f"{result.distance_km:,.1f} km route",
    ).add_to(map_view)
    folium.CircleMarker(
        coordinates[0],
        radius=7,
        color="#ffffff",
        weight=2,
        fill=True,
        fill_color="#22c55e",
        fill_opacity=1,
        tooltip=f"Start: {result.actual_start}",
    ).add_to(map_view)
    bounds = [
        [min(point[0] for point in coordinates), min(point[1] for point in coordinates)],
        [max(point[0] for point in coordinates), max(point[1] for point in coordinates)],
    ]
    map_view.fit_bounds(bounds, padding=(24, 24))
    folium.LayerControl().add_to(map_view)
    return map_view


st.title("Every Single Street")
st.markdown(
    "Generate one closed walking route that covers the main connected public street network of a place."
)
st.caption("Map and street data © OpenStreetMap contributors. Routes are suggestions—always check local access and safety.")

with st.form("route-request", border=True):
    left, right = st.columns([1, 1])
    with left:
        preset = st.selectbox("Choose a place", PRESETS.keys(), index=0)
        custom_place = st.text_input(
            "Or enter a city, municipality, or district",
            placeholder="e.g. Kirchheim, Heidelberg, Germany",
            disabled=preset != "Custom place…",
        )
    with right:
        start_street = st.text_input(
            "Starting street (optional)",
            placeholder="Leave blank to start near the centre",
            help="Use a street inside the selected boundary. The route starts and finishes there.",
        )
        st.info("Small towns and districts work best. Large cities may exceed the free server’s limits.")
    submitted = st.form_submit_button("Generate route", type="primary", use_container_width=True)

place = custom_place.strip() if preset == "Custom place…" else PRESETS[preset]

if submitted:
    if not place:
        st.warning("Enter a city, municipality, or district.")
    else:
        messages: list[str] = []
        started_at = time.perf_counter()
        with st.status("Starting route generation…", expanded=True) as status:
            def progress(message: str) -> None:
                if not messages or messages[-1] != message:
                    messages.append(message)
                    elapsed = time.perf_counter() - started_at
                    st.write(f"{message}  ·  {elapsed:,.0f}s")

            try:
                result = generate_route(place, start_street, progress=progress)
                st.session_state["route_result"] = result
                status.update(
                    label=(
                        "Route loaded from cache"
                        if result.from_cache
                        else f"Route generated in {time.perf_counter() - started_at:,.0f}s"
                    ),
                    state="complete",
                    expanded=False,
                )
            except RouteTooLargeError as exc:
                status.update(label="That area is too large", state="error", expanded=True)
                st.error(str(exc))
            except RouteError as exc:
                status.update(label="Could not generate the route", state="error", expanded=True)
                st.error(str(exc))
            except Exception:
                status.update(label="Unexpected error", state="error", expanded=True)
                st.exception(RuntimeError("An unexpected error occurred. Please try a more specific place name."))

result = st.session_state.get("route_result")
if result:
    st.divider()
    heading, download = st.columns([3, 1])
    with heading:
        st.subheader(result.place)
        st.markdown(f'<p class="subtle">Starts and finishes at {result.actual_start}</p>', unsafe_allow_html=True)
    with download:
        st.download_button(
            "Download GPX",
            data=result.gpx_xml.encode("utf-8"),
            file_name=result.download_filename,
            mime="application/gpx+xml",
            type="primary",
            use_container_width=True,
        )

    metric_columns = st.columns(4)
    metric_columns[0].metric("Route length", f"{result.distance_km:,.1f} km")
    metric_columns[1].metric("Mapped segments", f"{result.streets:,}")
    metric_columns[2].metric("Junctions", f"{result.nodes:,}")
    metric_columns[3].metric("Repeated distance", f"{result.repeated_distance_km:,.1f} km")

    rendered_map = route_map(result).get_root().render()
    components.html(rendered_map, height=650, scrolling=False)
    st.caption(
        "The calculation uses the largest connected walking network and filters mapped private roads, "
        "driveways, parking aisles, and area features. OpenStreetMap can still be incomplete."
    )
