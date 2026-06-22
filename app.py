"""Advanced, isolated version of the Every Single Street Streamlit app."""

from __future__ import annotations

import time

import folium
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from route_engine_old import RouteError, RouteTooLargeError
from route_engine import (
    ALL_CATEGORY_IDS,
    ROAD_CATEGORIES,
    AdvancedRouteResult,
    generate_advanced_route,
)


st.set_page_config(page_title="Every Single Street · Advanced", page_icon="🧭", layout="wide")
st.markdown(
    """
    <style>
      .block-container { max-width: 1240px; padding-top: 2rem; }
      [data-testid="stMetricValue"] { font-size: 1.65rem; }
      .subtle { color: #64748b; margin-top: -.55rem; }
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
CATEGORY_LABEL_TO_ID = {
    str(settings["label"]): category_id for category_id, settings in ROAD_CATEGORIES.items()
}
TRAVERSAL_COLORS = {1: "#22c55e", 2: "#f59e0b"}


def traversal_color(count: int) -> str:
    return TRAVERSAL_COLORS.get(count, "#ef4444")


def advanced_map(result: AdvancedRouteResult) -> folium.Map:
    centre = result.coordinates[len(result.coordinates) // 2]
    map_view = folium.Map(location=centre, zoom_start=13, control_scale=True, tiles=None)
    folium.TileLayer(
        "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="© OpenStreetMap contributors",
        name="OpenStreetMap",
        max_zoom=19,
    ).add_to(map_view)

    for segment in result.segments:
        category_label = ROAD_CATEGORIES[segment.category]["label"]
        folium.PolyLine(
            segment.coordinates,
            color=traversal_color(segment.traversals),
            weight=3 if segment.traversals == 1 else 4,
            opacity=0.9,
            tooltip=(
                f"{segment.name} · {category_label} · "
                f"{segment.traversals} traversal{'s' if segment.traversals != 1 else ''}"
            ),
        ).add_to(map_view)

    folium.CircleMarker(
        result.coordinates[0], radius=7, color="#fff", weight=2,
        fill=True, fill_color="#0ea5e9", fill_opacity=1,
        tooltip=f"Start: {result.actual_start}",
    ).add_to(map_view)
    bounds = [
        [min(point[0] for point in result.coordinates), min(point[1] for point in result.coordinates)],
        [max(point[0] for point in result.coordinates), max(point[1] for point in result.coordinates)],
    ]
    map_view.fit_bounds(bounds, padding=(24, 24))
    legend = """
    <div style="position:fixed;bottom:28px;left:28px;z-index:9999;background:white;
      padding:10px 14px;border-radius:8px;box-shadow:0 2px 10px #0003;font:14px sans-serif">
      <b>Segment traversals</b><br>
      <span style="color:#22c55e">━━</span> Once<br>
      <span style="color:#f59e0b">━━</span> Twice<br>
      <span style="color:#ef4444">━━</span> Three or more
    </div>
    """
    map_view.get_root().html.add_child(folium.Element(legend))
    return map_view


st.title("Every Single Street · Advanced")
st.markdown("Choose which kinds of roads count, cut out polygon areas, and inspect every repeated segment.")
st.caption("This is a separate experimental version. The stable app and routing engine are not modified.")

with st.form("advanced-route-request", border=True):
    basic_tab, advanced_tab = st.tabs(["Route", "Advanced filters"])
    with basic_tab:
        left, right = st.columns(2)
        with left:
            preset = st.selectbox("Choose a place", PRESETS, index=0)
            custom_place = st.text_input(
                "Or enter a custom city, municipality, or district",
                placeholder="e.g. Kirchheim, Heidelberg, Germany",
                help="Typed text takes priority over the preset.",
            )
        with right:
            start_street = st.text_input(
                "Starting street (optional)",
                placeholder="Leave blank to start near the centre",
            )
            st.info("Small municipalities and districts work best on the free server.")

    with advanced_tab:
        category_labels = st.multiselect(
            "Road categories to include",
            options=list(CATEGORY_LABEL_TO_ID),
            default=list(CATEGORY_LABEL_TO_ID),
            help="Only these OpenStreetMap highway categories become part of the route.",
        )
        with st.expander("What is included in each category?"):
            for settings in ROAD_CATEGORIES.values():
                st.markdown(f"**{settings['label']}** — {settings['description']}")
        geofence_file = st.file_uploader(
            "GeoJSON exclusion areas (optional)",
            type=["geojson", "json"],
            help="Draw Polygon or MultiPolygon areas at geojson.io and upload the exported file. Roads intersecting them are removed.",
        )
        st.caption("GeoJSON coordinates must use normal longitude/latitude (WGS84), as exported by geojson.io.")

    submitted = st.form_submit_button("Generate advanced route", type="primary", use_container_width=True)

place = custom_place.strip() or PRESETS[preset]
selected_categories = tuple(CATEGORY_LABEL_TO_ID[label] for label in category_labels)
geofence_data = geofence_file.getvalue() if geofence_file else None
geofence_name = geofence_file.name if geofence_file else ""

if submitted:
    if not place:
        st.warning("Enter a city, municipality, or district.")
    elif not selected_categories:
        st.warning("Select at least one road category in the Advanced filters tab.")
    else:
        messages = []
        started_at = time.perf_counter()
        with st.status("Starting advanced route generation…", expanded=True) as status:
            def progress(message: str) -> None:
                if not messages or messages[-1] != message:
                    messages.append(message)
                    st.write(f"{message} · {time.perf_counter() - started_at:,.0f}s")

            try:
                result = generate_advanced_route(
                    place,
                    start_street,
                    selected_categories,
                    geofence_data,
                    geofence_name,
                    progress=progress,
                )
                st.session_state["advanced_route_result"] = result
                label = "Advanced route loaded from cache" if result.from_cache else (
                    f"Advanced route generated in {time.perf_counter() - started_at:,.0f}s"
                )
                status.update(label=label, state="complete", expanded=False)
            except RouteTooLargeError as exc:
                status.update(label="That filtered network is too large", state="error", expanded=True)
                st.error(str(exc))
            except RouteError as exc:
                status.update(label="Could not generate the advanced route", state="error", expanded=True)
                st.error(str(exc))
            except Exception as exc:
                status.update(label="Unexpected error", state="error", expanded=True)
                st.exception(exc)

result = st.session_state.get("advanced_route_result")
if result:
    st.divider()
    heading, download = st.columns([3, 1])
    with heading:
        st.subheader(result.place)
        fence_note = f" · Excluding {result.geofence_name}" if result.geofence_name else ""
        st.markdown(
            f'<p class="subtle">Starts at {result.actual_start}{fence_note}</p>',
            unsafe_allow_html=True,
        )
    with download:
        st.download_button(
            "Download GPX", result.gpx_xml.encode("utf-8"), result.download_filename,
            "application/gpx+xml", type="primary", use_container_width=True,
        )

    metrics = st.columns(5)
    metrics[0].metric("Route", f"{result.distance_km:,.1f} km")
    metrics[1].metric("Unique network", f"{result.street_length_km:,.1f} km")
    metrics[2].metric("Repeated", f"{result.repeated_distance_km:,.1f} km")
    metrics[3].metric("Segments", f"{result.streets:,}")
    once = result.traversal_histogram.get(1, 0)
    metrics[4].metric("Covered once", f"{once / result.streets:.0%}" if result.streets else "—")

    map_tab, stats_tab, details_tab = st.tabs(["Traversal map", "Statistics", "Details & downloads"])
    with map_tab:
        components.html(advanced_map(result).get_root().render(), height=680, scrolling=False)

    with stats_tab:
        chart_data = pd.DataFrame([
            {"Traversals": f"{count}×", "Segments": amount}
            for count, amount in result.traversal_histogram.items()
        ])
        st.subheader("How often each segment is used")
        st.bar_chart(chart_data, x="Traversals", y="Segments", color="#0891b2")

        category_rows = []
        for category_id, stats in result.category_statistics.items():
            category_rows.append({
                "Road category": ROAD_CATEGORIES[category_id]["label"],
                "Segments": int(stats["segments"]),
                "Unique length (km)": round(stats["street_km"], 2),
                "Route distance (km)": round(stats["route_km"], 2),
            })
        st.subheader("Road-category breakdown")
        st.dataframe(pd.DataFrame(category_rows), hide_index=True, use_container_width=True)

    with details_tab:
        filter_metrics = st.columns(3)
        filter_metrics[0].metric("Removed by road filter", f"{result.removed_by_road_filter:,}")
        filter_metrics[1].metric("Removed by GeoJSON", f"{result.removed_by_geofence:,}")
        filter_metrics[2].metric("Private/inaccessible removed", f"{result.removed_private:,}")
        selected_labels = [ROAD_CATEGORIES[item]["label"] for item in result.selected_categories]
        st.write("Included road categories: " + ", ".join(selected_labels))
        st.download_button(
            "Download segment statistics (CSV)",
            result.statistics_csv().encode("utf-8-sig"),
            result.download_filename.replace(".gpx", "-statistics.csv"),
            "text/csv",
        )
        st.caption(
            "Green segments are used once, amber twice, and red three or more times. "
            "Filtering can split a city into several components; routing uses the largest remaining component."
        )
