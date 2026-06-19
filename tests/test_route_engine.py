from pathlib import Path

import networkx as nx
from shapely.geometry import LineString

import route_engine


def sample_graph():
    graph = nx.MultiGraph()
    nodes = {
        1: {"x": 8.0, "y": 49.0},
        2: {"x": 8.01, "y": 49.0},
        3: {"x": 8.01, "y": 49.01},
        4: {"x": 8.0, "y": 49.01},
        5: {"x": 8.02, "y": 49.0},
    }
    graph.add_nodes_from(nodes.items())
    edges = [
        (1, 2, "Main Street"),
        (2, 3, "North Street"),
        (3, 4, "Back Street"),
        (4, 1, "West Street"),
        (2, 5, "Dead End"),
    ]
    for index, (u, v, name) in enumerate(edges):
        a, b = nodes[u], nodes[v]
        graph.add_edge(
            u,
            v,
            osmid=index + 100,
            name=name,
            length=100.0,
            geometry=LineString([(a["x"], a["y"]), (b["x"], b["y"])]),
        )
    return graph


def test_start_node_matches_normalised_street_name():
    node, name = route_engine._start_node(sample_graph(), "Main StReEt")
    assert node in {1, 2}
    assert name == "Main Street"


def test_euler_route_coordinates_include_edge_geometry():
    graph = sample_graph()
    euler = nx.eulerize(graph)
    circuit = list(nx.eulerian_circuit(euler, source=1, keys=True))
    coordinates = route_engine._route_coordinates(euler, circuit)
    assert coordinates[0] == coordinates[-1]
    assert len(coordinates) >= len(circuit)


def test_result_cache_round_trip(tmp_path: Path):
    result = route_engine.RouteResult(
        place="Test, Germany",
        requested_start="",
        actual_start="Near the centre",
        coordinates=[(49.0, 8.0), (49.1, 8.1)],
        distance_km=1.0,
        street_length_km=0.8,
        nodes=2,
        streets=1,
        repeated_distance_km=0.2,
        gpx_xml="<gpx />",
    )
    path = tmp_path / "route.pkl"
    route_engine._atomic_pickle(path, result)
    loaded = route_engine._load_result(path)
    assert loaded is not None
    assert loaded.place == result.place
    assert loaded.from_cache is True
