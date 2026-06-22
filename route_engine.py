"""Advanced routing engine with road filters, geofences, and traversal stats.

This module intentionally lives beside, rather than inside, ``route_engine`` so
the stable application remains unchanged.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import pickle
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import networkx as nx
import osmnx as ox
from shapely.geometry import LineString, shape
from shapely.ops import unary_union
from shapely.prepared import prep

import route_engine_old as base


ADVANCED_CACHE_VERSION = 1
DEFAULT_CACHE_DIR = Path("cache/advanced")

ROAD_CATEGORIES: dict[str, dict[str, object]] = {
    "main_roads": {
        "label": "Main roads",
        "description": "Primary, secondary and tertiary roads",
        "highways": {
            "primary", "primary_link", "secondary", "secondary_link",
            "tertiary", "tertiary_link",
        },
    },
    "residential": {
        "label": "Residential streets",
        "description": "Residential and living streets",
        "highways": {"residential", "living_street"},
    },
    "local_roads": {
        "label": "Local / unclassified roads",
        "description": "Unclassified roads and roads without a clearer class",
        "highways": {"unclassified", "road"},
    },
    "footways": {
        "label": "Footways and pedestrian areas",
        "description": "Footways, pedestrian ways, paths and corridors",
        "highways": {"footway", "pedestrian", "path", "corridor"},
    },
    "cycleways": {
        "label": "Cycleways",
        "description": "Mapped cycleways that are available in the walking network",
        "highways": {"cycleway"},
    },
    "tracks": {
        "label": "Tracks",
        "description": "Agricultural, forest and other mapped tracks",
        "highways": {"track"},
    },
    "steps": {
        "label": "Steps",
        "description": "Stairways and stepped paths",
        "highways": {"steps"},
    },
    "service_roads": {
        "label": "Public service roads",
        "description": "Public service roads; driveways and parking aisles remain excluded",
        "highways": {"service"},
    },
    "other": {
        "label": "Other walkable ways",
        "description": "Walkable highway tags not covered by the groups above",
        "highways": set(),
    },
}

ALL_CATEGORY_IDS = tuple(ROAD_CATEGORIES)
_KNOWN_HIGHWAYS = set().union(
    *(category["highways"] for key, category in ROAD_CATEGORIES.items() if key != "other")
)

ProgressCallback = Callable[[str], None]


@dataclass
class SegmentStat:
    edge_id: str
    name: str
    category: str
    highway: str
    length_m: float
    traversals: int
    coordinates: list[tuple[float, float]]


@dataclass
class AdvancedRouteResult:
    place: str
    requested_start: str
    actual_start: str
    selected_categories: tuple[str, ...]
    geofence_name: str
    coordinates: list[tuple[float, float]]
    segments: list[SegmentStat]
    distance_km: float
    street_length_km: float
    repeated_distance_km: float
    nodes: int
    streets: int
    removed_by_road_filter: int
    removed_by_geofence: int
    removed_private: int
    traversal_histogram: dict[int, int]
    category_statistics: dict[str, dict[str, float]]
    gpx_xml: str
    from_cache: bool = False

    @property
    def download_filename(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", base._ascii(self.place).lower()).strip("-")
        return f"{slug or 'every-street'}-advanced-route.gpx"

    def statistics_csv(self) -> str:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["segment_id", "street_name", "category", "highway", "length_m", "traversals"])
        for segment in self.segments:
            writer.writerow([
                segment.edge_id,
                segment.name,
                ROAD_CATEGORIES[segment.category]["label"],
                segment.highway,
                f"{segment.length_m:.1f}",
                segment.traversals,
            ])
        return output.getvalue()


def _notify(callback: ProgressCallback | None, message: str) -> None:
    if callback:
        callback(message)


def _geojson_geometries(payload: object) -> Iterable[dict]:
    if not isinstance(payload, dict):
        raise base.RouteError("The uploaded GeoJSON must contain a JSON object.")
    object_type = payload.get("type")
    if object_type == "FeatureCollection":
        for feature in payload.get("features", []):
            if isinstance(feature, dict) and feature.get("geometry"):
                yield feature["geometry"]
    elif object_type == "Feature":
        if payload.get("geometry"):
            yield payload["geometry"]
    elif object_type in {"Polygon", "MultiPolygon"}:
        yield payload
    else:
        raise base.RouteError("Upload polygon or multipolygon GeoJSON, such as an export from geojson.io.")


def parse_exclusion_geojson(data: bytes | None):
    if not data:
        return None
    if len(data) > 5 * 1024 * 1024:
        raise base.RouteError("The GeoJSON file is larger than the 5 MB limit.")
    try:
        payload = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise base.RouteError(f"The uploaded file is not valid GeoJSON: {exc}") from exc

    polygons = []
    for geometry_data in _geojson_geometries(payload):
        try:
            geometry = shape(geometry_data)
        except Exception as exc:
            raise base.RouteError(f"A GeoJSON geometry could not be read: {exc}") from exc
        if geometry.geom_type not in {"Polygon", "MultiPolygon"}:
            raise base.RouteError("Every exclusion geometry must be a polygon or multipolygon.")
        if not geometry.is_valid:
            geometry = geometry.buffer(0)
        if not geometry.is_empty:
            polygons.append(geometry)
    if not polygons:
        raise base.RouteError("The GeoJSON contains no usable polygon areas.")
    return unary_union(polygons)


def _highway_values(data: dict) -> set[str]:
    value = data.get("highway")
    if isinstance(value, (list, tuple, set)):
        return {str(item).casefold() for item in value}
    return {str(value).casefold()} if value else set()


def edge_categories(data: dict) -> set[str]:
    highways = _highway_values(data)
    matched = {
        category_id
        for category_id, category in ROAD_CATEGORIES.items()
        if category_id != "other" and highways.intersection(category["highways"])
    }
    if not matched and (not highways or highways - _KNOWN_HIGHWAYS):
        matched.add("other")
    return matched


def _edge_geometry(graph, u, v, data: dict):
    geometry = data.get("geometry")
    if geometry is not None:
        return geometry
    return LineString([
        (float(graph.nodes[u]["x"]), float(graph.nodes[u]["y"])),
        (float(graph.nodes[v]["x"]), float(graph.nodes[v]["y"])),
    ])


def _prepare_advanced_graph(raw_graph, selected_categories: set[str], exclusion, callback):
    _notify(callback, "Applying road categories, access rules, and exclusion areas…")
    graph = raw_graph.copy()
    denied_access = {"private", "no", "customers", "delivery"}
    denied_service = {"parking_aisle", "driveway", "private", "alley", "parking", "yard"}
    prepared_exclusion = prep(exclusion) if exclusion is not None else None
    removals = []
    counts = Counter()

    for u, v, key, data in graph.edges(keys=True, data=True):
        reason = None
        if denied_access.intersection(base._values(data.get("access"))):
            reason = "private"
        elif denied_service.intersection(base._values(data.get("service"))):
            reason = "private"
        elif {"yes", "true", "1"}.intersection(base._values(data.get("area"))):
            reason = "private"
        elif not edge_categories(data).intersection(selected_categories):
            reason = "road_filter"
        elif prepared_exclusion and prepared_exclusion.intersects(_edge_geometry(graph, u, v, data)):
            reason = "geofence"
        if reason:
            removals.append((u, v, key))
            counts[reason] += 1

    graph.remove_edges_from(removals)
    graph.remove_nodes_from(list(nx.isolates(graph)))
    if graph.number_of_nodes() == 0:
        raise base.RouteError("No streets remain after applying the advanced filters.")

    undirected = ox.convert.to_undirected(graph)
    components = list(nx.connected_components(undirected))
    if not components:
        raise base.RouteError("The selected streets do not form a connected network.")
    main = undirected.subgraph(max(components, key=len)).copy()
    if main.number_of_edges() == 0:
        raise base.RouteError("The largest remaining connected network contains no streets.")

    for index, (u, v, key, data) in enumerate(main.edges(keys=True, data=True)):
        data["_advanced_edge_id"] = f"edge-{index}"
        categories = edge_categories(data).intersection(selected_categories)
        data["_advanced_category"] = sorted(categories)[0] if categories else "other"
    return main, counts


def _cache_key(place: str, start: str, categories: tuple[str, ...], geofence_data: bytes | None) -> str:
    payload = {
        "version": ADVANCED_CACHE_VERSION,
        "place": place.strip().casefold(),
        "start": start.strip().casefold(),
        "categories": categories,
        "geofence": hashlib.sha256(geofence_data or b"").hexdigest(),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _load_result(path: Path) -> AdvancedRouteResult | None:
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            result = pickle.load(handle)
        if isinstance(result, AdvancedRouteResult):
            result.from_cache = True
            return result
    except (OSError, pickle.PickleError, EOFError, AttributeError):
        path.unlink(missing_ok=True)
    return None


def _segment_name(data: dict) -> str:
    name = data.get("name")
    if isinstance(name, list):
        return " / ".join(str(value) for value in name)
    return str(name) if name else "Unnamed way"


def _build_result(place, start_street, actual_start, categories, geofence_name, graph, circuit, euler_graph, counts):
    traversal_counts = Counter()
    for u, v, key in circuit:
        data = euler_graph.get_edge_data(u, v, key) or {}
        edge_id = data.get("_advanced_edge_id")
        if edge_id:
            traversal_counts[edge_id] += 1

    segments = []
    category_statistics = defaultdict(lambda: {"segments": 0, "street_km": 0.0, "route_km": 0.0})
    for u, v, key, data in graph.edges(keys=True, data=True):
        edge_id = data["_advanced_edge_id"]
        category = data["_advanced_category"]
        length_m = base._edge_length(data)
        traversals = traversal_counts.get(edge_id, 1)
        highway = ", ".join(sorted(_highway_values(data))) or "unknown"
        coordinates = base._oriented_edge_coordinates(graph, u, v, key)
        segments.append(SegmentStat(
            edge_id=edge_id,
            name=_segment_name(data),
            category=category,
            highway=highway,
            length_m=length_m,
            traversals=traversals,
            coordinates=coordinates,
        ))
        stats = category_statistics[category]
        stats["segments"] += 1
        stats["street_km"] += length_m / 1000
        stats["route_km"] += length_m * traversals / 1000

    coordinates = base._route_coordinates(euler_graph, circuit)
    route_metres = sum(base._edge_length(euler_graph.get_edge_data(u, v, key) or {}) for u, v, key in circuit)
    street_metres = sum(segment.length_m for segment in segments)
    histogram = dict(sorted(Counter(segment.traversals for segment in segments).items()))
    return AdvancedRouteResult(
        place=place,
        requested_start=start_street,
        actual_start=actual_start,
        selected_categories=categories,
        geofence_name=geofence_name,
        coordinates=coordinates,
        segments=segments,
        distance_km=route_metres / 1000,
        street_length_km=street_metres / 1000,
        repeated_distance_km=max(0.0, (route_metres - street_metres) / 1000),
        nodes=graph.number_of_nodes(),
        streets=graph.number_of_edges(),
        removed_by_road_filter=counts.get("road_filter", 0),
        removed_by_geofence=counts.get("geofence", 0),
        removed_private=counts.get("private", 0),
        traversal_histogram=histogram,
        category_statistics=dict(category_statistics),
        gpx_xml=base._gpx(place, actual_start, coordinates),
    )


def generate_advanced_route(
    place: str,
    start_street: str = "",
    selected_categories: Iterable[str] = ALL_CATEGORY_IDS,
    geofence_data: bytes | None = None,
    geofence_name: str = "",
    *,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    progress: ProgressCallback | None = None,
) -> AdvancedRouteResult:
    place = " ".join(place.split())
    start_street = " ".join(start_street.split())
    categories = tuple(sorted(set(selected_categories)))
    invalid = set(categories) - set(ROAD_CATEGORIES)
    if len(place) < 3 or len(place) > 160:
        raise base.RouteError("Enter a city, municipality, or district name between 3 and 160 characters.")
    if not categories:
        raise base.RouteError("Select at least one road category.")
    if invalid:
        raise base.RouteError(f"Unknown road categories: {', '.join(sorted(invalid))}")

    cache_dir = Path(cache_dir)
    key = _cache_key(place, start_street, categories, geofence_data)
    result_path = cache_dir / "routes" / f"{key}.pkl"
    cached = _load_result(result_path)
    if cached:
        _notify(progress, "Loaded the advanced route from cache.")
        return cached

    lock = base._request_lock(f"advanced-{key}")
    if lock.locked():
        _notify(progress, "Another visitor is calculating these same options; waiting…")
    with lock:
        cached = _load_result(result_path)
        if cached:
            _notify(progress, "Loaded the advanced route from cache.")
            return cached

        exclusion = parse_exclusion_geojson(geofence_data)
        raw_graph = base._download_graph(place, cache_dir, progress)
        graph, counts = _prepare_advanced_graph(raw_graph, set(categories), exclusion, progress)
        if graph.number_of_nodes() > base.MAX_NODES or graph.number_of_edges() > base.MAX_EDGES:
            raise base.RouteTooLargeError("The filtered network is too large for the free server.")
        odd_nodes = sum(1 for _, degree in graph.degree() if degree % 2)
        if odd_nodes > base.MAX_ODD_NODES:
            raise base.RouteTooLargeError(
                f"The filtered network has {odd_nodes:,} odd junctions. Select a smaller area or fewer road types."
            )

        start_node, actual_start = base._start_node(graph, start_street)
        _notify(progress, f"Connecting {odd_nodes:,} odd junctions into a closed route…")
        euler_graph = base._fast_eulerize(graph, progress)
        _notify(progress, "Calculating traversals and route statistics…")
        circuit = list(nx.eulerian_circuit(euler_graph, source=start_node, keys=True))
        if not circuit:
            raise base.RouteError("The generated advanced route was empty.")
        result = _build_result(
            place, start_street, actual_start, categories, geofence_name,
            graph, circuit, euler_graph, counts,
        )
        try:
            base._atomic_pickle(result_path, result)
        except OSError:
            pass
        return result
