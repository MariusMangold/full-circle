import json

import networkx as nx
from shapely.geometry import LineString, Point

import route_engine_old as base
import route_engine as advanced


def test_geojson_feature_collection_becomes_exclusion_polygon():
    payload = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[8.0, 49.0], [8.1, 49.0], [8.1, 49.1], [8.0, 49.1], [8.0, 49.0]]],
            },
        }],
    }
    exclusion = advanced.parse_exclusion_geojson(json.dumps(payload).encode())
    assert exclusion.contains(Point(8.05, 49.05))


def test_road_categories_follow_osm_highway_tags():
    assert advanced.edge_categories({"highway": "residential"}) == {"residential"}
    assert advanced.edge_categories({"highway": ["footway", "cycleway"]}) == {"footways", "cycleways"}
    assert advanced.edge_categories({"highway": "bridleway"}) == {"other"}


def _statistics_graph():
    graph = nx.MultiGraph()
    graph.add_node(1, x=8.0, y=49.0)
    graph.add_node(2, x=8.01, y=49.0)
    graph.add_node(3, x=8.01, y=49.01)
    graph.add_edge(
        1, 2, length=100.0, name="First Street", highway="residential",
        geometry=LineString([(8.0, 49.0), (8.01, 49.0)]),
        _advanced_edge_id="edge-1", _advanced_category="residential",
    )
    graph.add_edge(
        2, 3, length=120.0, name="Foot Path", highway="footway",
        geometry=LineString([(8.01, 49.0), (8.01, 49.01)]),
        _advanced_edge_id="edge-2", _advanced_category="footways",
    )
    return graph


def test_statistics_count_repeated_segments_and_export_csv():
    graph = _statistics_graph()
    euler = base._fast_eulerize(graph)
    circuit = list(nx.eulerian_circuit(euler, source=1, keys=True))
    result = advanced._build_result(
        "Test place", "", "Near the centre", ("footways", "residential"), "",
        graph, circuit, euler, {},
    )
    assert result.traversal_histogram[2] == 2
    assert result.distance_km == 0.44
    assert "First Street" in result.statistics_csv()

