import itertools
import math


def _get_leg_metric(
    from_node: dict,
    to_node: dict,
    speed_mps: float,
    street_matrix: dict = None,
) -> tuple[float, float]:
    if street_matrix and "matrix_node_index" in from_node and "matrix_node_index" in to_node:
        u = from_node["matrix_node_index"]
        v = to_node["matrix_node_index"]
        return float(street_matrix["distances_m"][u][v]), float(street_matrix["durations_sec"][u][v])
    dist_m = math.hypot(to_node["x"] - from_node["x"], to_node["y"] - from_node["y"])
    return dist_m, dist_m / speed_mps


def evaluate_request_insertion(
    vehicle: dict,
    request: dict,
    platform: str,
    speed_mps: float,
    street_matrix: dict = None,
) -> dict:
    stops = vehicle["stops"]
    n_stops = len(stops)

    origin_node = {
        "x": vehicle["current_x"],
        "y": vehicle["current_y"],
    }
    if "matrix_node_index" in vehicle:
        origin_node["matrix_node_index"] = vehicle["matrix_node_index"]

    # Calculate baseline route distance and duration starting from origin
    prev_node = origin_node
    baseline_dist_m = 0.0
    baseline_duration_sec = 0.0
    for stop in stops:
        d_m, t_sec = _get_leg_metric(prev_node, stop, speed_mps, street_matrix)
        baseline_dist_m += d_m
        baseline_duration_sec += t_sec
        prev_node = stop

    # Active trajectory vector for directional corridor preservation in Euclidean flatland
    if n_stops > 0:
        traj_x = stops[-1]["x"] - vehicle["current_x"]
        traj_y = stops[-1]["y"] - vehicle["current_y"]
    elif "manifest_stops" in request and request["manifest_stops"]:
        first_s = request["manifest_stops"][0]
        last_s = request["manifest_stops"][-1]
        traj_x = float(last_s.get("lat") if "lat" in last_s else last_s.get("x", 0.0)) - float(first_s.get("lat") if "lat" in first_s else first_s.get("x", 0.0))
        traj_y = float(last_s.get("lng") if "lng" in last_s else last_s.get("y", 0.0)) - float(first_s.get("lng") if "lng" in first_s else first_s.get("y", 0.0))
    elif "pickup" in request and "dropoff" in request:
        traj_x = request["dropoff"]["x"] - request["pickup"]["x"]
        traj_y = request["dropoff"]["y"] - request["pickup"]["y"]
    else:
        traj_x = 0.0
        traj_y = 0.0

    if "manifest_stops" in request:
        manifest = request["manifest_stops"]
        new_stops = []
        for s in manifest:
            is_p = (s.get("type", "").upper() == "PICKUP") if "type" in s else s.get("is_pickup", True)
            w = s.get("weight_delta_tons")
            if w is not None:
                l_change = abs(float(w)) if is_p else -abs(float(w))
            else:
                l_change = float(s.get("load_change", 0.0))

            stop_obj = {
                "name": s.get("name", "stop"),
                "x": float(s.get("lat") if "lat" in s else s.get("x", 0.0)),
                "y": float(s.get("lng") if "lng" in s else s.get("y", 0.0)),
                "is_pickup": is_p,
                "load_change": l_change,
                "latest_time_sec": float(s.get("latest_time_sec", 86400.0)),
                "request_id": request.get("id"),
            }
            if "matrix_node_index" in s:
                stop_obj["matrix_node_index"] = s["matrix_node_index"]
            new_stops.append(stop_obj)
    else:
        p_stop = {
            "name": request["pickup"]["name"],
            "x": request["pickup"]["x"],
            "y": request["pickup"]["y"],
            "is_pickup": True,
            "load_change": request["load"],
            "latest_time_sec": request["pickup"]["latest_time_sec"],
        }
        if "matrix_node_index" in request["pickup"]:
            p_stop["matrix_node_index"] = request["pickup"]["matrix_node_index"]
        if "request_id" in request["pickup"]:
            p_stop["request_id"] = request["pickup"]["request_id"]
        elif "id" in request:
            p_stop["request_id"] = request["id"]

        d_stop = {
            "name": request["dropoff"]["name"],
            "x": request["dropoff"]["x"],
            "y": request["dropoff"]["y"],
            "is_pickup": False,
            "load_change": -request["load"],
            "latest_time_sec": request["dropoff"]["latest_time_sec"],
        }
        if "matrix_node_index" in request["dropoff"]:
            d_stop["matrix_node_index"] = request["dropoff"]["matrix_node_index"]
        if "request_id" in request["dropoff"]:
            d_stop["request_id"] = request["dropoff"]["request_id"]
        elif "id" in request:
            d_stop["request_id"] = request["id"]

        new_stops = [p_stop, d_stop]

    m_count = len(new_stops)
    best_indices = None
    best_cost = float("inf")
    best_candidate = None

    initial_load = float(vehicle.get("current_load", 0.0))
    if initial_load > vehicle["capacity_max"]:
        return {"feasible": False}

    for indices in itertools.combinations_with_replacement(range(n_stops + 1), m_count):
        candidate = []
        prev_k = 0
        for m, curr_k in enumerate(indices):
            candidate.extend(stops[prev_k:curr_k])
            candidate.append(new_stops[m])
            prev_k = curr_k
        candidate.extend(stops[prev_k:])

        prev_node = origin_node
        running_load = initial_load
        candidate_dist_m = 0.0
        candidate_duration_sec = 0.0
        feasible = True

        for stop in candidate:
            dx = stop["x"] - prev_node["x"]
            dy = stop["y"] - prev_node["y"]

            # Enforce directional corridor alignment when street_matrix is not supplied
            if not street_matrix and (dx * traj_x + dy * traj_y) < 0:
                feasible = False
                break

            running_load += stop["load_change"]
            if running_load > vehicle["capacity_max"]:
                feasible = False
                break

            leg_d, leg_t = _get_leg_metric(prev_node, stop, speed_mps, street_matrix)
            candidate_dist_m += leg_d
            candidate_duration_sec += leg_t

            if candidate_duration_sec > stop["latest_time_sec"]:
                feasible = False
                break

            prev_node = stop

        if feasible:
            delta_time = candidate_duration_sec - baseline_duration_sec
            delta_dist_km = (candidate_dist_m - baseline_dist_m) / 1000.0

            if platform == "TRIPPY_POOL":
                marginal_cost = 0.15 * delta_dist_km + 0.85 * delta_time
            elif platform == "ENTROPIC_FREIGHT":
                marginal_cost = 0.65 * delta_dist_km + 0.25 * delta_time
            else:
                marginal_cost = delta_time

            if marginal_cost < best_cost:
                best_cost = marginal_cost
                best_indices = indices
                best_candidate = candidate

    if best_indices is None:
        return {"feasible": False}

    updated_stops = []
    prev_node = origin_node
    cum_time = 0.0
    curr_load = initial_load
    for stop in best_candidate:
        d_m, t_sec = _get_leg_metric(prev_node, stop, speed_mps, street_matrix)
        cum_time += t_sec
        curr_load += stop["load_change"]
        new_stop = dict(stop)
        new_stop["eta_sec"] = round(cum_time, 2)
        new_stop["projected_load_after"] = round(curr_load, 2)
        new_stop["projected_onboard_tons"] = round(curr_load, 2)
        updated_stops.append(new_stop)
        prev_node = stop

    # Peak onboard load across updated_stops sequence
    running_loads = [s["projected_load_after"] for s in updated_stops]
    peak_onboard_load = max(running_loads) if running_loads else initial_load
    assert peak_onboard_load <= vehicle["capacity_max"] + 1e-6, (
        f"peak_onboard_load {peak_onboard_load} exceeds capacity {vehicle['capacity_max']}"
    )

    first_pickup_k = best_indices[0]
    for m, s in enumerate(new_stops):
        if s.get("is_pickup"):
            first_pickup_k = best_indices[m]
            break

    has_downstream_dropoff = any(
        (s.get("is_pickup") is False or s.get("load_change", 0) < 0)
        for s in stops[first_pickup_k:]
    )
    insertion_mode = "MID_ROUTE_POOL" if has_downstream_dropoff else "NEXT_LEG_BACKHAUL"

    return {
        "feasible": True,
        "insert_pickup_index": best_indices[0],
        "insert_dropoff_index": best_indices[-1],
        "insert_indices": list(best_indices),
        "marginal_cost": best_cost,
        "max_slack_violation_sec": 0.0,
        "updated_stops": updated_stops,
        "insertion_mode": insertion_mode,
        "peak_onboard_load": round(peak_onboard_load, 2),
    }


def evaluate_route_substitution(
    vehicle: dict,
    anomaly: dict,
    platform: str,
    speed_mps: float,
) -> dict:
    stops = vehicle["stops"]
    cur_x, cur_y = vehicle["current_x"], vehicle["current_y"]

    # 1. Baseline ETAs for all stops in vehicle["stops"] starting from current location at t = 0.0
    baseline_etas = []
    cumulative_dist_m = 0.0
    cx, cy = cur_x, cur_y
    for stop in stops:
        cumulative_dist_m += math.hypot(stop["x"] - cx, stop["y"] - cy)
        baseline_etas.append(cumulative_dist_m / speed_mps)
        cx, cy = stop["x"], stop["y"]

    affected_idx = anomaly["affected_stop_index"]
    primary_delay = anomaly["primary_delay_sec"]

    # 3. Check if primary route with primary_delay added violates any deadline
    primary_violates = False
    for k in range(len(stops)):
        eta = baseline_etas[k]
        if k >= affected_idx:
            eta += primary_delay
        if eta > stops[k]["latest_time_sec"]:
            primary_violates = True
            break

    if not primary_violates:
        return {
            "action": "NO_ACTION_REQUIRED",
            "selected_fallback_id": None,
            "ejected_request_ids": [],
        }

    # 4. TIER 1 — Disjoint Fallback Corridor Swap
    valid_fallbacks = []
    for fb in vehicle.get("fallback_routes", []):
        extra_t = fb["extra_time_sec"]
        fb_violates = False
        for k in range(len(stops)):
            eta = baseline_etas[k]
            if k >= affected_idx:
                eta += extra_t
            if eta > stops[k]["latest_time_sec"]:
                fb_violates = True
                break
        if not fb_violates:
            valid_fallbacks.append(fb)

    if valid_fallbacks:
        best_fb = min(valid_fallbacks, key=lambda x: x["extra_time_sec"])
        return {
            "action": "SWAPPED_DISJOINT_CORRIDOR",
            "selected_fallback_id": best_fb["route_id"],
            "ejected_request_ids": [],
        }

    # 5. TIER 2 — Destroy-and-Repair Stop Ejection (Re-Auction)
    ejected_request_ids = []
    seen = set()
    for k in range(affected_idx, len(stops)):
        stop = stops[k]
        if stop.get("is_pickup") and stop.get("request_id"):
            rid = stop["request_id"]
            if rid not in seen:
                seen.add(rid)
                ejected_request_ids.append(rid)

    return {
        "action": "EJECTED_TO_REAUCTION",
        "selected_fallback_id": None,
        "ejected_request_ids": ejected_request_ids,
    }


def dispatch_request_to_fleet(
    candidates: list,
    request: dict,
    platform: str,
    speed_mps: float,
    street_matrices: dict = None,
) -> dict:
    eligible = []
    for vehicle in candidates:
        veh_matrix = street_matrices.get(vehicle["id"]) if street_matrices else None
        res = evaluate_request_insertion(vehicle, request, platform, speed_mps, street_matrix=veh_matrix)
        if res.get("feasible"):
            eligible.append({
                "vehicle_id": vehicle["id"],
                "marginal_cost": res["marginal_cost"],
                "insert_pickup_index": res["insert_pickup_index"],
                "insert_dropoff_index": res["insert_dropoff_index"],
                "updated_stops": res.get("updated_stops", []),
                "insertion_mode": res.get("insertion_mode"),
                "peak_onboard_load": res.get("peak_onboard_load"),
            })

    eligible.sort(key=lambda x: x["marginal_cost"])

    return {
        "winning_vehicle_id": eligible[0]["vehicle_id"] if eligible else None,
        "eligible_count": len(eligible),
        "ranked_candidates": eligible,
    }
