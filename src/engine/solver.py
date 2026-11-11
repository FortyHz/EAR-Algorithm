import math


def evaluate_request_insertion(
    vehicle: dict,
    request: dict,
    platform: str,
    speed_mps: float,
) -> dict:
    stops = vehicle["stops"]
    n_stops = len(stops)

    # Calculate baseline route distance and duration starting from (current_x, current_y)
    cur_x, cur_y = vehicle["current_x"], vehicle["current_y"]
    baseline_dist_m = 0.0
    for stop in stops:
        baseline_dist_m += math.hypot(stop["x"] - cur_x, stop["y"] - cur_y)
        cur_x, cur_y = stop["x"], stop["y"]
    baseline_duration_sec = baseline_dist_m / speed_mps

    # Active trajectory vector for directional corridor preservation
    if n_stops > 0:
        traj_x = stops[-1]["x"] - vehicle["current_x"]
        traj_y = stops[-1]["y"] - vehicle["current_y"]
    else:
        traj_x = request["dropoff"]["x"] - request["pickup"]["x"]
        traj_y = request["dropoff"]["y"] - request["pickup"]["y"]

    p_stop = {
        "name": request["pickup"]["name"],
        "x": request["pickup"]["x"],
        "y": request["pickup"]["y"],
        "is_pickup": True,
        "load_change": request["load"],
        "latest_time_sec": request["pickup"]["latest_time_sec"],
    }
    d_stop = {
        "name": request["dropoff"]["name"],
        "x": request["dropoff"]["x"],
        "y": request["dropoff"]["y"],
        "is_pickup": False,
        "load_change": -request["load"],
        "latest_time_sec": request["dropoff"]["latest_time_sec"],
    }

    best_i = None
    best_j = None
    best_cost = float("inf")

    # Iterate over all valid insertion index pairs (i, j) where 0 <= i <= N and i <= j <= N
    for i in range(n_stops + 1):
        for j in range(i, n_stops + 1):
            if i == j:
                candidate = stops[:i] + [p_stop, d_stop] + stops[i:]
            else:
                candidate = stops[:i] + [p_stop] + stops[i:j] + [d_stop] + stops[j:]

            # Forward Pass on candidate sequence (length N + 2)
            cur_x, cur_y = vehicle["current_x"], vehicle["current_y"]
            running_load = 0
            candidate_dist_m = 0.0
            feasible = True

            for stop in candidate:
                dx = stop["x"] - cur_x
                dy = stop["y"] - cur_y

                # Enforce directional corridor alignment (disallow anti-directional backtracking)
                if (dx * traj_x + dy * traj_y) < 0:
                    feasible = False
                    break

                running_load += stop["load_change"]
                if running_load > vehicle["capacity_max"]:
                    feasible = False
                    break

                dist = math.hypot(dx, dy)
                candidate_dist_m += dist
                eta_sec = candidate_dist_m / speed_mps

                if eta_sec > stop["latest_time_sec"]:
                    feasible = False
                    break

                cur_x, cur_y = stop["x"], stop["y"]

            if feasible:
                candidate_duration_sec = candidate_dist_m / speed_mps
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
                    best_i = i
                    best_j = j

    if best_i is None:
        return {"feasible": False}

    return {
        "feasible": True,
        "insert_pickup_index": best_i,
        "insert_dropoff_index": best_j,
        "marginal_cost": best_cost,
        "max_slack_violation_sec": 0.0,
    }
