import os
import pickle
import osmnx as ox
import networkx as nx
import gpxpy
import gpxpy.gpx
from tqdm import tqdm
import matplotlib.pyplot as plt
import geopandas as gpd
from shapely.geometry import Point
from collections import Counter

# --- Konfiguration ---
PLACE = "Rohrbach, Baden-Württemberg, Germany"
FILENAME_GRAPH = "rohrbach_strassennetz.graphml"
FILENAME_ROUTE = "rohrbach_route.pkl" 
FILENAME_GPX = "rohrbach_route.gpx"
FILENAME_GEOJSON = None

def main():
    # =========================================================================
    # 1. KARTENDATEN LADEN (mit lokalem Cache)
    # =========================================================================
    if os.path.exists(FILENAME_GRAPH):
        print(f"[*] Lade Straßennetz aus lokaler Datei: {FILENAME_GRAPH}...")
        G = ox.load_graphml(FILENAME_GRAPH)
    else:
        print(f"[*] Lade Straßennetz für '{PLACE}' frisch aus dem Internet herunter...")
        G = ox.graph_from_place(PLACE, network_type='walk')
        ox.save_graphml(G, filepath=FILENAME_GRAPH)
        print("[+] Straßennetz erfolgreich lokal gespeichert!")

    # =========================================================================
    # 2. MANUELLER RÄUMLICHER FILTER (Deine selbst gezeichneten Sperrzonen)
    # =========================================================================
    if FILENAME_GEOJSON is not None:
        print(f"[*] Suche nach deinen handgezeichneten Sperrzonen in '{FILENAME_GEOJSON}'...")
        if os.path.exists(FILENAME_GEOJSON):
            try:
                sperrzonen_gdf = gpd.read_file(FILENAME_GEOJSON)
                nodes_to_remove = []
                
                for node, data in G.nodes(data=True):
                    punkt = Point(data['x'], data['y'])
                    
                    if any(geom.contains(punkt) for geom in sperrzonen_gdf.geometry):
                        nodes_to_remove.append(node)
                        
                G.remove_nodes_from(nodes_to_remove)
                print(f"[+] {len(nodes_to_remove)} Wegpunkte innerhalb deiner eigenen Sperrzonen gelöscht!")
            except Exception as e:
                print(f"[-] Fehler beim Laden der GeoJSON-Datei: {e}")
        else:
            print(f"[-] Keine Datei '{FILENAME_GEOJSON}' gefunden. Überspringe den manuellen Filter.")

    # =========================================================================
    # 3. KATEGORIEN-FILTER (Werksgelände, Privatwege, Parkplätze ausschließen)
    # =========================================================================
    print("[*] Bereinige die Karte (Entferne Werksgelände, Parkplätze & Höfe)...")
    edges_to_remove = []
    
    for u, v, k, data in G.edges(keys=True, data=True):
        
        # 1. Zugangsrechte prüfen
        if 'access' in data:
            access_values = data['access'] if isinstance(data['access'], list) else [data['access']]
            if any(a in ['school','cemetery', 'private', 'no', 'customers', 'delivery'] for a in access_values):
                edges_to_remove.append((u, v, k))
                continue
                
        # 2. Service-Wege & Industrie aussortieren
        if 'service' in data:
            service_types = data['service'] if isinstance(data['service'], list) else [data['service']]
            if any(s in ['parking_aisle', 'driveway', 'private', 'alley', 'industrial', 'parking', 'yard'] for s in service_types):
                edges_to_remove.append((u, v, k))
                continue
                
        # 3. Reine Beton/Asphalt-Flächen aussortieren
        if 'area' in data:
            area_values = data['area'] if isinstance(data['area'], list) else [data['area']]
            if any(a in ['yes', 'true', '1'] for a in area_values):
                edges_to_remove.append((u, v, k))

    G.remove_edges_from(edges_to_remove)
    
    # Verwaiste Kreuzungen löschen
    G.remove_nodes_from(list(nx.isolates(G)))
    print(f"[+] {len(edges_to_remove)} unerwünschte Werks- und Privatwege gelöscht!")

    # =========================================================================
    # 4. GRAPH FÜR ROUTING VORBEREITEN
    # =========================================================================
    G_undirected = G.to_undirected()
    components = sorted(nx.connected_components(G_undirected), key=len, reverse=True)
    G_main = G_undirected.subgraph(components[0]).copy()

    # =========================================================================
    # 5. ROUTENBERECHNUNG (Startpunkt Lessingstraße & Pickle-Cache)
    # =========================================================================
    if os.path.exists(FILENAME_ROUTE):
        print(f"\n[*] Juhu! Lade fertig berechnete Route aus '{FILENAME_ROUTE}'...")
        with open(FILENAME_ROUTE, 'rb') as f:
            route = pickle.load(f)
        print(f"[+] Route mit {len(route)} Wegabschnitten erfolgreich geladen!")
        wunsch_strasse = "Lessingstraße" 
    else:
        print("\n[*] 'Eulerisiere' das Netzwerk (Sackgassen & T-Kreuzungen verdoppeln)...")
        print("[!] ACHTUNG: Das dauert jetzt einen Moment. Hol dir einen Kaffee!")
        G_euler = nx.eulerize(G_main)
        
        # --- Startpunkt Lessingstraße exakt suchen ---
        wunsch_strasse = "Lessingstraße" 
        start_node = None
        
        def normalize_name(name):
            return name.lower().replace("ß", "ss").replace("str.", "strasse").replace(" ", "")

        ziel = normalize_name(wunsch_strasse)
        
        print(f"[*] Suche Start-Kreuzung exakt in der '{wunsch_strasse}'...")
        for u, v, data in G_main.edges(data=True):
            if 'name' in data:
                namen = data['name'] if isinstance(data['name'], list) else [data['name']]
                for n in namen:
                    if normalize_name(n) == ziel:
                        start_node = u
                        break
            if start_node is not None:
                break
                
        if start_node:
            print(f"[+] Perfekt! Startpunkt in der {wunsch_strasse} gefunden.")
        else:
            print("[-] Wunschstraße nicht gefunden! Starte an einem zufälligen Punkt.")

        # --- Route berechnen ---
        print("[*] Berechne exakte Abbiegefolge...")
        anzahl_kanten = G_euler.number_of_edges()
        
        route = list(tqdm(nx.eulerian_circuit(G_euler, source=start_node), total=anzahl_kanten, desc="Berechne Route"))
        
        with open(FILENAME_ROUTE, 'wb') as f:
            pickle.dump(route, f)
        print(f"[+] Route erfolgreich für die Zukunft in '{FILENAME_ROUTE}' gespeichert!")

    # =========================================================================
    # 6. GPX-EXPORT
    # =========================================================================
    print("\n[*] Erstelle GPX-Datei für deine Laufuhr/Navi-App...")
    gpx = gpxpy.gpx.GPX()
    gpx_track = gpxpy.gpx.GPXTrack()
    gpx.tracks.append(gpx_track)
    gpx_segment = gpxpy.gpx.GPXTrackSegment()
    gpx_track.segments.append(gpx_segment)

    for u, v in tqdm(route, desc="Schreibe GPX-Punkte"):
        lat = G_main.nodes[u]['y']
        lon = G_main.nodes[u]['x']
        gpx_segment.points.append(gpxpy.gpx.GPXTrackPoint(lat, lon))

    letzter_knoten = route[-1][1]
    lat = G_main.nodes[letzter_knoten]['y']
    lon = G_main.nodes[letzter_knoten]['x']
    gpx_segment.points.append(gpxpy.gpx.GPXTrackPoint(lat, lon))

    with open(FILENAME_GPX, "w", encoding="utf-8") as f:
        f.write(gpx.to_xml())
    print(f"[+] Datei erfolgreich als '{FILENAME_GPX}' gespeichert.")

    # =========================================================================
    # 7. GRAFISCHE DARSTELLUNG (Matplotlib)
    # =========================================================================
    print("\n[*] Generiere grafische Darstellung...")
    node_route = [u for u, v in route]
    node_route.append(route[-1][1])

    fig, ax = ox.plot_graph_route(
        G_main, 
        node_route, 
        route_color="cyan",      
        route_linewidth=1.5,     
        node_size=0,             
        bgcolor="#111111",       
        show=False,
        close=False
    )

    ax.set_title(f"Every Single Street: Rohrbach (Start: {wunsch_strasse})", color="white", fontsize=14, pad=10)
    plt.show()

    # =========================================================================
    # 7. ANALYSE & GRAFISCHE DARSTELLUNG (Heatmap & Histogramm)
    # =========================================================================
    print("\n[*] Analysiere Straßen-Durchläufe für die Heatmap...")
    
    # Zähle, wie oft jede Straßenverbindung gelaufen wird
    # Wir sortieren die Knoten (u, v), da die Laufrichtung beim Zählen egal ist
    durchlaeufe = Counter()
    for step in route:
        u, v = step[0], step[1]
        durchlaeufe[tuple(sorted((u, v)))] += 1

    edge_colors = []
    edge_widths = []
    hist_data = []

    # Jede Kante im Original-Straßennetz prüfen und Farbe zuweisen
    for u, v, k in G_main.edges(keys=True):
        anzahl = durchlaeufe[tuple(sorted((u, v)))]
        hist_data.append(anzahl)
        
        # Farbschema festlegen
        if anzahl == 1:
            edge_colors.append("#2ecc71")  # Grün: Perfekt, nur 1x gelaufen
            edge_widths.append(1.5)
        elif anzahl == 2:
            edge_colors.append("#f1c40f")  # Gelb: 2x gelaufen (z. B. in Sackgassen)
            edge_widths.append(2.5)
        else:
            edge_colors.append("#e74c3c")  # Rot: 3x oder öfter (Knotenpunkte)
            edge_widths.append(3.5)

    # --- TEIL A: Histogramm zeichnen ---
    print("[*] Generiere Histogramm...")
    plt.figure(figsize=(10, 5))
    max_durchlaeufe = max(hist_data) if hist_data else 1
    
    plt.hist(hist_data, bins=range(1, max_durchlaeufe + 2), align='left', rwidth=0.8, color="#3498db")
    plt.title("Wie oft muss jede Straße in Rohrbach gelaufen werden?", fontsize=14)
    plt.xlabel("Anzahl der Durchläufe pro Straße", fontsize=12)
    plt.ylabel("Anzahl der Straßenabschnitte", fontsize=12)
    plt.xticks(range(1, max_durchlaeufe + 1))
    plt.grid(axis='y', linestyle='--', alpha=0.7)
    
    # Zeigt das Histogramm an (Skript pausiert hier, bis das Fenster geschlossen wird)
    print("[!] Schließe das Histogramm-Fenster, um die Heatmap-Karte zu sehen!")
    plt.show()

    # --- TEIL B: Heatmap Karte zeichnen ---
    print("[*] Generiere eingefärbte Heatmap-Karte...")
    fig, ax = ox.plot_graph(
        G_main, 
        edge_color=edge_colors,      
        edge_linewidth=edge_widths,     
        node_size=0,             
        bgcolor="#111111",       
        show=False,
        close=False
    )

    ax.set_title("Heatmap Rohrbach | Grün: 1x | Gelb: 2x | Rot: 3x+", color="white", fontsize=14, pad=10)
    plt.show()

if __name__ == "__main__":
    main()