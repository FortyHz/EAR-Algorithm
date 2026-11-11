import copy
from src.adapters.h3_indexer import SpatialCorridorIndex
from src.adapters.matrix_provider import get_street_matrix
from src.engine.solver import dispatch_request_to_fleet


def process_live_dispatch(
    fleet: list,
    request: dict,
    platform: str,
    speed_mps: float = 13.88,
) -> dict:
    """
    Unified end-to-end dispatch pipeline:
    1. Spatial indexing of route corridors.
    2. Candidate pruning based on pickup proximity.
    3. Street matrix calculation with node index binding.
    4. Multi-vehicle tournament evaluation via DPDPTW solver.
    """
    # Step 1: Index all vehicles in fleet
    spatial_index = SpatialCorridorIndex()
    for veh in fleet:
        coords = [(veh["current_x"], veh["current_y"])]
        for s in veh.get("stops", []):
            coords.append((s["x"], s["y"]))
        spatial_index.index_vehicle(veh["id"], coords, platform)

    # Step 2: Filter candidate vehicle IDs near pickup
    px = request["pickup"]["x"]
    py = request["pickup"]["y"]
    filtered_ids = set(spatial_index.query_candidates((px, py), platform, radius_m=3500.0))

    candidate_vehicles = [copy.deepcopy(v) for v in fleet if v["id"] in filtered_ids]
    street_matrices = {}

    req_copy = copy.deepcopy(request)

    # Step 3: For each candidate, construct node list and generate street_matrix
    for veh in candidate_vehicles:
        origin_node = {
            "x": veh["current_x"],
            "y": veh["current_y"],
        }
        all_nodes = [origin_node]
        for s in veh["stops"]:
            all_nodes.append(s)

        p_node = req_copy["pickup"]
        d_node = req_copy["dropoff"]
        all_nodes.append(p_node)
        all_nodes.append(d_node)

        # Generate matrix and bind matrix_node_index to nodes
        matrix = get_street_matrix(all_nodes, speed_mps=speed_mps)
        veh["matrix_node_index"] = origin_node["matrix_node_index"]
        street_matrices[veh["id"]] = matrix

    # Step 4: Call dispatch_request_to_fleet from solver
    tournament_result = dispatch_request_to_fleet(
        candidate_vehicles,
        req_copy,
        platform,
        speed_mps,
        street_matrices=street_matrices,
    )

    # Step 5: Return summary
    return {
        "spatial_filtered_count": len(candidate_vehicles),
        "winning_vehicle_id": tournament_result.get("winning_vehicle_id"),
        "eligible_count": tournament_result.get("eligible_count", 0),
        "ranked_candidates": tournament_result.get("ranked_candidates", []),
    }
