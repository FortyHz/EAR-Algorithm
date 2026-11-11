import math
import os


def get_street_matrix(nodes: list, speed_mps: float = 13.88, winding_factor: float = 1.25) -> dict:
    """
    Takes a list of node dicts [{"x": float, "y": float}, ...], assigns each
    matrix_node_index 0..K-1, and returns {"durations_sec": [[...]], "distances_m": [[...]]}
    applying street winding factor or querying external OSRM if configured.
    """
    k = len(nodes)
    for idx, node in enumerate(nodes):
        node["matrix_node_index"] = idx

    distances_m = [[0.0] * k for _ in range(k)]
    durations_sec = [[0.0] * k for _ in range(k)]

    osrm_url = os.environ.get("OSRM_URL")
    if osrm_url:
        try:
            import urllib.request
            import json

            coords_str = ";".join(f"{n['x']},{n['y']}" for n in nodes)
            url = f"{osrm_url}/table/v1/driving/{coords_str}?annotations=duration,distance"
            with urllib.request.urlopen(url, timeout=3.0) as resp:
                data = json.loads(resp.read().decode())
                if data.get("code") == "Ok":
                    return {
                        "durations_sec": data["durations"],
                        "distances_m": data["distances"],
                    }
        except Exception:
            pass

    for i in range(k):
        for j in range(k):
            if i == j:
                distances_m[i][j] = 0.0
                durations_sec[i][j] = 0.0
            else:
                eucl = math.hypot(nodes[j]["x"] - nodes[i]["x"], nodes[j]["y"] - nodes[i]["y"])
                dist = eucl * winding_factor
                distances_m[i][j] = round(dist, 2)
                durations_sec[i][j] = round(dist / speed_mps, 2)

    return {
        "durations_sec": durations_sec,
        "distances_m": distances_m,
    }
