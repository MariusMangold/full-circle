"""Every Single Street app with data-driven exclusions and route statistics."""

from __future__ import annotations

import json
import time

import folium
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from route_engine_old import RouteError, RouteTooLargeError
from route_engine import (
    ROAD_CATEGORIES,
    AdvancedRouteResult,
    FilterPreview,
    build_filter_preview,
    generate_route,
    inspect_place_roads,
    load_place_graph,
)


st.set_page_config(page_title="Every Single Street · Advanced", page_icon="🗺️", layout="wide")
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
TRAVERSAL_COLORS = {1: "#22c55e", 2: "#f59e0b"}


def traversal_color(count: int) -> str:
    return TRAVERSAL_COLORS.get(count, "#ef4444")


@st.cache_resource(show_spinner=False)
def cached_place_graph(place: str):
    return load_place_graph(place)


def exclusion_preview_map(preview: FilterPreview, geofence_data: bytes | None) -> folium.Map:
    all_coordinates = [point for segment in preview.segments for point in segment.coordinates]
    centre = all_coordinates[len(all_coordinates) // 2]
    map_view = folium.Map(location=centre, zoom_start=13, control_scale=True, tiles=None)
    folium.TileLayer(
        "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        attr="© OpenStreetMap contributors",
        name="OpenStreetMap",
        max_zoom=19,
    ).add_to(map_view)

    layer_settings = {
        "included": ("Included roads", "#0891b2", 2, 0.65),
        "road_type": ("Excluded road types", "#ef4444", 4, 0.95),
        "geofence": ("Excluded by GeoJSON", "#a855f7", 4, 0.95),
        "inaccessible": ("Private / inaccessible", "#64748b", 2, 0.45),
    }
    for status, (label, color, weight, opacity) in layer_settings.items():
        features = []
        for segment in preview.segments:
            if segment.status != status:
                continue
            features.append({
                "type": "Feature",
                "properties": {
                    "name": segment.name,
                    "highway": segment.highway,
                    "status": label,
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[longitude, latitude] for latitude, longitude in segment.coordinates],
                },
            })
        if features:
            folium.GeoJson(
                {"type": "FeatureCollection", "features": features},
                name=f"{label} ({len(features):,})",
                style_function=lambda _feature, color=color, weight=weight, opacity=opacity: {
                    "color": color, "weight": weight, "opacity": opacity,
                },
                tooltip=folium.GeoJsonTooltip(
                    fields=["name", "highway", "status"],
                    aliases=["Street", "OSM type", "Status"],
                    sticky=False,
                ),
            ).add_to(map_view)

    if geofence_data:
        geofence_payload = json.loads(geofence_data.decode("utf-8-sig"))
        folium.GeoJson(
            geofence_payload,
            name="Uploaded exclusion polygon",
            style_function=lambda _feature: {
                "color": "#7e22ce", "weight": 2, "fillColor": "#c084fc", "fillOpacity": 0.18,
            },
        ).add_to(map_view)

    bounds = [
        [min(point[0] for point in all_coordinates), min(point[1] for point in all_coordinates)],
        [max(point[0] for point in all_coordinates), max(point[1] for point in all_coordinates)],
    ]
    map_view.fit_bounds(bounds, padding=(24, 24))
    folium.LayerControl(collapsed=False).add_to(map_view)
    legend = """
    <div style="position:fixed;bottom:28px;left:28px;z-index:9999;background:white;
      padding:10px 14px;border-radius:8px;box-shadow:0 2px 10px #0003;font:14px sans-serif">
      <b>Current exclusions</b><br>
      <span style="color:#0891b2">━━</span> Included<br>
      <span style="color:#ef4444">━━</span> Road type excluded<br>
      <span style="color:#a855f7">━━</span> GeoJSON excluded<br>
      <span style="color:#64748b">━━</span> Private / inaccessible
    </div>
    """
    map_view.get_root().html.add_child(folium.Element(legend))
    return map_view


def route_map(result: AdvancedRouteResult) -> folium.Map:
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
                f"{segment.name} · {category_label} · {segment.highway} · "
                f"{segment.traversals} traversal{'s' if segment.traversals != 1 else ''}"
            ),
        ).add_to(map_view)

    folium.CircleMarker(
        result.coordinates[0],
        radius=7,
        color="#fff",
        weight=2,
        fill=True,
        fill_color="#0ea5e9",
        fill_opacity=1,
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
st.markdown(
    "First inspect the road data, then exclude only the road types actually present before generating the route."
)
st.caption("The previous stable version is preserved in appold.py and route_engine_old.py.")

st.subheader("1. Load the city data")
with st.form("load-road-data", border=True):
    left, right = st.columns(2)
    with left:
        preset = st.selectbox("Choose a place", PRESETS, index=0)
        custom_place = st.text_input(
            "Or enter a custom city, municipality, or district",
            placeholder="e.g. Kirchheim, Heidelberg, Germany",
            help="Typed text takes priority over the preset.",
        )
    with right:
        st.info(
            "This downloads the walking network and discovers its exact OpenStreetMap "
            "road types. It does not calculate a route yet."
        )
    load_submitted = st.form_submit_button(
        "Load and inspect road data", type="primary", use_container_width=True
    )

requested_place = custom_place.strip() or PRESETS[preset]
if load_submitted:
    st.session_state.pop("road_inventory", None)
    st.session_state.pop("route_result", None)
    if not requested_place:
        st.warning("Enter a city, municipality, or district.")
    else:
        started_at = time.perf_counter()
        messages: list[str] = []
        with st.status("Loading road data…", expanded=True) as status:
            def inventory_progress(message: str) -> None:
                if not messages or messages[-1] != message:
                    messages.append(message)
                    st.write(f"{message} · {time.perf_counter() - started_at:,.0f}s")

            try:
                inventory = inspect_place_roads(requested_place, progress=inventory_progress)
                st.session_state["road_inventory"] = inventory
                status.update(
                    label=f"Found {len(inventory.highway_counts)} road types in {inventory.place}",
                    state="complete",
                    expanded=False,
                )
            except RouteTooLargeError as exc:
                status.update(label="That place is too large", state="error", expanded=True)
                st.error(str(exc))
            except RouteError as exc:
                status.update(label="Could not load the road data", state="error", expanded=True)
                st.error(str(exc))
            except Exception as exc:
                status.update(label="Unexpected error", state="error", expanded=True)
                st.exception(exc)

inventory = st.session_state.get("road_inventory")
if inventory:
    st.subheader("2. Choose exclusions and generate the route")
    st.success(
        f"Loaded {inventory.public_segments:,} public walking segments in {inventory.place}. "
        f"Found {len(inventory.highway_counts)} distinct OSM highway types."
    )

    sorted_highways = sorted(inventory.highway_counts.items(), key=lambda item: (-item[1], item[0]))
    highway_label_to_value = {
        f"{highway} · {count:,} segments": highway for highway, count in sorted_highways
    }

    route_tab, advanced_tab = st.tabs(["Route", "Advanced exclusions"])
    with route_tab:
        start_street = st.text_input(
            "Starting street (optional)",
            placeholder="Leave blank to start near the centre",
        )
        optimization_label = st.radio(
            "Route optimization",
            options=["Fast", "Exact shortest"],
            horizontal=True,
            help=(
                "Fast uses a greedy pairing and usually finishes in seconds. Exact shortest solves the "
                "weighted Chinese Postman problem and minimizes repeated kilometres, but can take several minutes."
            ),
        )
        if optimization_label == "Exact shortest":
            st.warning(
                "Exact mode minimizes the final route length mathematically. Keep this page open; "
                "the matching step can take several minutes on a free server."
            )
        st.caption(f"The route will use the currently loaded network for **{inventory.place}**.")

    with advanced_tab:
        excluded_labels = st.multiselect(
            "Road types to exclude",
            options=list(highway_label_to_value),
            default=[],
            help=(
                "These are the exact highway tags detected in the loaded OpenStreetMap data. "
                "Selected types are removed before routing. The preview updates immediately."
            ),
        )
        st.dataframe(
            pd.DataFrame([
                {"OSM highway type": highway, "Segments found": count}
                for highway, count in sorted_highways
            ]),
            hide_index=True,
            use_container_width=True,
        )
        link_column, upload_column = st.columns([1, 2])
        with link_column:
            st.link_button(
                "Create exclusion areas at geojson.io ↗",
                "https://geojson.io/",
                use_container_width=True,
            )
        with upload_column:
            geofence_file = st.file_uploader(
                "Upload GeoJSON exclusion areas (optional)",
                type=["geojson", "json"],
                help=(
                    "At geojson.io, draw Polygon or MultiPolygon areas, save as GeoJSON, "
                    "then upload the exported file here. Roads intersecting them are removed."
                ),
            )
        st.caption("GeoJSON must use longitude/latitude (WGS84), as exported by geojson.io.")

    excluded_highways = tuple(highway_label_to_value[label] for label in excluded_labels)
    optimization_mode = "exact" if optimization_label == "Exact shortest" else "fast"
    geofence_data = geofence_file.getvalue() if geofence_file else None
    geofence_name = geofence_file.name if geofence_file else ""

    st.subheader("Current exclusion preview")
    preview_valid = True
    try:
        raw_preview_graph = cached_place_graph(inventory.place)
        preview = build_filter_preview(
            raw_preview_graph,
            inventory.place,
            excluded_highways,
            geofence_data,
        )
        preview_metrics = st.columns(4)
        preview_metrics[0].metric("Included", f"{preview.status_counts.get('included', 0):,}")
        preview_metrics[1].metric("Road type excluded", f"{preview.status_counts.get('road_type', 0):,}")
        preview_metrics[2].metric("GeoJSON excluded", f"{preview.status_counts.get('geofence', 0):,}")
        preview_metrics[3].metric("Private / inaccessible", f"{preview.status_counts.get('inaccessible', 0):,}")
        components.html(
            exclusion_preview_map(preview, geofence_data).get_root().render(),
            height=620,
            scrolling=False,
        )
        st.caption(
            "Preview counts are unique displayed road segments. The final route uses the largest connected "
            "component left after all exclusions. Use the layer control to hide or show each group."
        )
    except RouteError as exc:
        preview_valid = False
        st.error(f"The exclusion preview could not be built: {exc}")
    except Exception as exc:
        preview_valid = False
        st.exception(exc)

    submitted = st.button(
        "Generate route",
        type="primary",
        use_container_width=True,
        disabled=not preview_valid,
    )

    if submitted:
        messages: list[str] = []
        started_at = time.perf_counter()
        with st.status("Starting route generation…", expanded=True) as status:
            def progress(message: str) -> None:
                if not messages or messages[-1] != message:
                    messages.append(message)
                    st.write(f"{message} · {time.perf_counter() - started_at:,.0f}s")

            try:
                result = generate_route(
                    inventory.place,
                    start_street,
                    excluded_highways,
                    geofence_data,
                    geofence_name,
                    optimization_mode=optimization_mode,
                    progress=progress,
                )
                st.session_state["route_result"] = result
                label = "Route loaded from cache" if result.from_cache else (
                    f"Route generated in {time.perf_counter() - started_at:,.0f}s"
                )
                status.update(label=label, state="complete", expanded=False)
            except RouteTooLargeError as exc:
                status.update(label="That filtered network is too large", state="error", expanded=True)
                st.error(str(exc))
            except RouteError as exc:
                status.update(label="Could not generate the route", state="error", expanded=True)
                st.error(str(exc))
            except Exception as exc:
                status.update(label="Unexpected error", state="error", expanded=True)
                st.exception(exc)

result = st.session_state.get("route_result")
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
            "Download GPX",
            result.gpx_xml.encode("utf-8"),
            result.download_filename,
            "application/gpx+xml",
            type="primary",
            use_container_width=True,
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
        components.html(route_map(result).get_root().render(), height=680, scrolling=False)

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
        st.write(
            "Optimization: "
            + (
                "Exact shortest (minimum repeated distance)"
                if getattr(result, "optimization_mode", "fast") == "exact"
                else "Fast"
            )
        )
        filter_metrics = st.columns(3)
        filter_metrics[0].metric("Removed by road-type exclusions", f"{result.removed_by_road_filter:,}")
        filter_metrics[1].metric("Removed by GeoJSON", f"{result.removed_by_geofence:,}")
        filter_metrics[2].metric("Private/inaccessible removed", f"{result.removed_private:,}")
        if result.excluded_highways:
            st.write("Excluded road types: " + ", ".join(result.excluded_highways))
        else:
            st.write("Excluded road types: none")
        st.download_button(
            "Download segment statistics (CSV)",
            result.statistics_csv().encode("utf-8-sig"),
            result.download_filename.replace(".gpx", "-statistics.csv"),
            "text/csv",
        )
        st.caption(
            "Green segments are used once, amber twice, and red three or more times. "
            "Exclusions can split a city into several components; routing uses the largest remaining component."
        )
