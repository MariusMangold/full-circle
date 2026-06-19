"""Routing engine for the Every Single Street web app.

The module deliberately has no Streamlit dependency so the expensive routing
work can be tested and reused from another UI later.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import gpxpy.gpx
import networkx as nx
import osmnx as ox


CACHE_VERSION = 1
DEFAULT_CACHE_DIR = Path(os.environ.get("ROUTE_CACHE_DIR", "cache/web"))
MAX_AREA_KM2 = int(os.environ.get("MAX_AREA_KM2", "250"))
MAX_NODES = int(os.environ.get("MAX_NODES", "12000"))
MAX_EDGES = int(os.environ.get("MAX_EDGES", "30000"))
MAX_ODD_NODES = int(os.environ.get("MAX_ODD_NODES", "1200"))

ProgressCallback = Callable[[str], None]


class RouteError(RuntimeError):
    """A problem that can be shown directly to an app user."""


class RouteTooLargeError(RouteError):
    """The requested area is unsafe to calculate on a small hosted server."""


@dataclass
class RouteResult:
    place: str
    requested_start: str
    actual_start: str
    coordinates: list[tuple[float, float]]
    distance_km: float
    street_length_km: float
    nodes: int
    streets: int
    repeated_distance_km: float
    gpx_xml: str
    from_cache: bool = False

    @property
    def download_filename(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", _ascii(self.place).lower()).strip("-")
        return f"{slug or 'every-street'}-route.gpx"


def _notify(callback: ProgressCallback | None, message: str) -> None:
    if callback:
        callback(message)


def _ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def _normalise(value: object) -> str:
    text = _ascii(str(value)).casefold().replace("str.", "strasse")
    return re.sub(r"[^a-z0-9]", "", text)


def _cache_key(place: str, start_street: str) -> str:
    raw = json.dumps(
        {
            "version": CACHE_VERSION,
            "place": place.strip().casefold(),
            "start": start_street.strip().casefold(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _place_key(place: str) -> str:
    return hashlib.sha256(place.strip().casefold().encode()).hexdigest()


def _atomic_pickle(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as handle:
        pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
    temporary.replace(path)


def _load_result(path: Path) -> RouteResult | None:
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            value = pickle.load(handle)
        if isinstance(value, RouteResult):
            value.from_cache = True
            return value
    except (OSError, pickle.PickleError, EOFError, AttributeError):
        path.unlink(missing_ok=True)
    return None


def _area_km2(place: str) -> float:
    boundary = ox.geocode_to_gdf(place)
    if boundary.empty:
        raise RouteError("OpenStreetMap could not find that place. Add the country or region and try again.")
    try:
        projected = boundary.to_crs(boundary.estimate_utm_crs())
        return float(projected.geometry.area.sum() / 1_000_000)
    except Exception:
        # Area is only an early guard. Graph-size guards still protect the server.
        return 0.0


def _download_graph(place: str, cache_dir: Path, callback: ProgressCallback | None):
    graph_path = cache_dir / "graphs" / f"{_place_key(place)}.graphml"
    if graph_path.exists():
        _notify(callback, "Loading the cached street network…")
        try:
            return ox.load_graphml(graph_path)
        except Exception:
            graph_path.unlink(missing_ok=True)

    _notify(callback, "Checking the size of the requested area…")
    try:
        area_km2 = _area_km2(place)
    except RouteError:
        raise
    except Exception as exc:
        raise RouteError(f"OpenStreetMap could not resolve that place: {exc}") from exc
    if area_km2 > MAX_AREA_KM2:
        raise RouteTooLargeError(
            f"That boundary is about {area_km2:,.0f} km². The free server limit is "
            f"{MAX_AREA_KM2} km²; try a district or smaller municipality."
        )

    _notify(callback, "Downloading walkable streets from OpenStreetMap…")
    try:
        graph = ox.graph_from_place(place, network_type="walk", simplify=True)
    except Exception as exc:
        raise RouteError(f"The street network could not be downloaded: {exc}") from exc

    if graph.number_of_nodes() > MAX_NODES or graph.number_of_edges() > MAX_EDGES:
        raise RouteTooLargeError(
            f"This place contains {graph.number_of_nodes():,} junctions and "
            f"{graph.number_of_edges():,} street segments. Try a district or a smaller place."
        )

    graph_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ox.save_graphml(graph, graph_path)
    except OSError:
        pass  # A read-only/ephemeral host can still finish the current request.
    return graph


def _values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).casefold() for item in value]
    return [str(value).casefold()]


def _prepare_graph(graph, callback: ProgressCallback | None):
    _notify(callback, "Removing private roads, driveways, parking aisles, and mapped areas…")
    denied_access = {"private", "no", "customers", "delivery"}
    denied_service = {
        "parking_aisle",
        "driveway",
        "private",
        "alley",
        "industrial",
        "parking",
        "yard",
    }
    edges_to_remove = []
    for u, v, key, data in graph.edges(keys=True, data=True):
        if denied_access.intersection(_values(data.get("access"))):
            edges_to_remove.append((u, v, key))
        elif denied_service.intersection(_values(data.get("service"))):
            edges_to_remove.append((u, v, key))
        elif {"yes", "true", "1"}.intersection(_values(data.get("area"))):
            edges_to_remove.append((u, v, key))

    cleaned = graph.copy()
    cleaned.remove_edges_from(edges_to_remove)
    cleaned.remove_nodes_from(list(nx.isolates(cleaned)))
    undirected = ox.convert.to_undirected(cleaned)
    if undirected.number_of_nodes() == 0:
        raise RouteError("No connected public walking streets remained after filtering.")

    components = list(nx.connected_components(undirected))
    main_nodes = max(components, key=len)
    main = undirected.subgraph(main_nodes).copy()
    if main.number_of_edges() == 0:
        raise RouteError("No connected public walking streets were found for that place.")
    return main


def _edge_names(data: dict) -> Iterable[str]:
    name = data.get("name")
    if isinstance(name, list):
        yield from (str(value) for value in name)
    elif name:
        yield str(name)


def _start_node(graph, requested_street: str) -> tuple[int, str]:
    if not requested_street.strip():
        center_y = sum(float(data["y"]) for _, data in graph.nodes(data=True)) / graph.number_of_nodes()
        center_x = sum(float(data["x"]) for _, data in graph.nodes(data=True)) / graph.number_of_nodes()
        node = ox.distance.nearest_nodes(graph, X=center_x, Y=center_y)
        return node, "Near the centre"

    target = _normalise(requested_street)
    exact: list[tuple[int, str]] = []
    partial: list[tuple[int, str]] = []
    available: set[str] = set()
    for u, _v, _key, data in graph.edges(keys=True, data=True):
        for name in _edge_names(data):
            available.add(name)
            normalised = _normalise(name)
            if normalised == target:
                exact.append((u, name))
            elif target and (target in normalised or normalised in target):
                partial.append((u, name))
    candidates = exact or partial
    if candidates:
        return candidates[0]

    suggestions = sorted(available, key=lambda name: _edit_distance(target, _normalise(name)))[:5]
    hint = f" Similar mapped streets: {', '.join(suggestions)}." if suggestions else ""
    raise RouteError(
        f"The starting street ‘{requested_street}’ was not found inside this boundary."
        f" Leave it blank to start near the centre.{hint}"
    )


def _edit_distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left_char != right_char)))
        previous = current
    return previous[-1]


def _oriented_edge_coordinates(graph, u: int, v: int, key: int) -> list[tuple[float, float]]:
    data = graph.get_edge_data(u, v, key) or {}
    geometry = data.get("geometry")
    if geometry is None:
        return [(float(graph.nodes[u]["y"]), float(graph.nodes[u]["x"])),
                (float(graph.nodes[v]["y"]), float(graph.nodes[v]["x"]))]

    coords = [(float(y), float(x)) for x, y in geometry.coords]
    u_coord = (float(graph.nodes[u]["y"]), float(graph.nodes[u]["x"]))
    if coords and _distance_sq(coords[-1], u_coord) < _distance_sq(coords[0], u_coord):
        coords.reverse()
    return coords


def _distance_sq(a: tuple[float, float], b: tuple[float, float]) -> float:
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def _route_coordinates(graph, circuit: list[tuple[int, int, int]]) -> list[tuple[float, float]]:
    coordinates: list[tuple[float, float]] = []
    for u, v, key in circuit:
        segment = _oriented_edge_coordinates(graph, u, v, key)
        if coordinates and segment and coordinates[-1] == segment[0]:
            coordinates.extend(segment[1:])
        else:
            coordinates.extend(segment)
    return coordinates


def _gpx(place: str, start: str, coordinates: list[tuple[float, float]]) -> str:
    gpx = gpxpy.gpx.GPX()
    gpx.creator = "Every Single Street"
    track = gpxpy.gpx.GPXTrack(name=f"Every street in {place}")
    gpx.tracks.append(track)
    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)
    for latitude, longitude in coordinates:
        segment.points.append(gpxpy.gpx.GPXTrackPoint(latitude, longitude))
    gpx.description = f"Every Single Street route; start: {start}"
    return gpx.to_xml()


def _edge_length(data: dict, fallback: float = 0.0) -> float:
    value = data.get("length", fallback)
    if isinstance(value, list):
        value = min(value) if value else fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def generate_route(
    place: str,
    start_street: str = "",
    *,
    cache_dir: Path | str = DEFAULT_CACHE_DIR,
    progress: ProgressCallback | None = None,
) -> RouteResult:
    """Create a closed walk covering every edge in the main connected network."""
    place = " ".join(place.split())
    start_street = " ".join(start_street.split())
    if len(place) < 3 or len(place) > 160:
        raise RouteError("Enter a city, municipality, or district name between 3 and 160 characters.")
    if len(start_street) > 120:
        raise RouteError("The starting street name is too long.")

    cache_dir = Path(cache_dir)
    result_path = cache_dir / "routes" / f"{_cache_key(place, start_street)}.pkl"
    cached = _load_result(result_path)
    if cached:
        _notify(progress, "Loaded the finished route from cache.")
        return cached

    raw_graph = _download_graph(place, cache_dir, progress)
    graph = _prepare_graph(raw_graph, progress)
    if graph.number_of_nodes() > MAX_NODES or graph.number_of_edges() > MAX_EDGES:
        raise RouteTooLargeError("The connected street network is too large for the free server.")

    start_node, actual_start = _start_node(graph, start_street)
    odd_nodes = sum(1 for _, degree in graph.degree() if degree % 2)
    if odd_nodes > MAX_ODD_NODES:
        raise RouteTooLargeError(
            f"This network has {odd_nodes:,} odd junctions, making the exact route too expensive. "
            "Try a smaller district."
        )

    _notify(progress, "Connecting dead ends and odd junctions…")
    try:
        euler_graph = nx.eulerize(graph)
    except Exception as exc:
        raise RouteError(f"The street network could not be converted into one closed route: {exc}") from exc

    _notify(progress, "Calculating the complete turn-by-turn circuit…")
    circuit = list(nx.eulerian_circuit(euler_graph, source=start_node, keys=True))
    if not circuit:
        raise RouteError("The generated route was empty.")

    _notify(progress, "Building the interactive map and GPX file…")
    coordinates = _route_coordinates(euler_graph, circuit)
    route_metres = sum(_edge_length(euler_graph.get_edge_data(u, v, key) or {}) for u, v, key in circuit)
    street_metres = sum(_edge_length(data) for *_edge, data in graph.edges(keys=True, data=True))
    # An undirected OSMnx graph can retain paired directional edges. Report the
    # graph sum consistently; the route figure remains the useful comparison.
    result = RouteResult(
        place=place,
        requested_start=start_street,
        actual_start=actual_start,
        coordinates=coordinates,
        distance_km=route_metres / 1000,
        street_length_km=street_metres / 1000,
        nodes=graph.number_of_nodes(),
        streets=graph.number_of_edges(),
        repeated_distance_km=max(0.0, (route_metres - street_metres) / 1000),
        gpx_xml=_gpx(place, actual_start, coordinates),
    )
    try:
        _atomic_pickle(result_path, result)
    except OSError:
        pass
    return result
