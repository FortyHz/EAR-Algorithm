import os
import hashlib
import json
import time
import sys
import warnings

def get_benchmark_hash(filepath: str) -> str:
    with open(filepath, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]

def run_harness():
    _dir = os.path.dirname(os.path.abspath(__file__))
    if _dir not in sys.path:
        sys.path.insert(0, _dir)
    bench_path = os.path.join(_dir, "benchmarks", "ground_truth_cases.json") if not os.path.exists("benchmarks/ground_truth_cases.json") else "benchmarks/ground_truth_cases.json"
    bench_hash = get_benchmark_hash(bench_path)

    with open(bench_path, "r") as f:
        data = json.load(f)

    try:
        from src.engine.solver import evaluate_request_insertion
    except ImportError as e:
        print(json.dumps({"status": "ERROR", "reason": f"Solver import failed: {str(e)}", "bench_hash": bench_hash}))
        sys.exit(1)

    speed_mps = data["speed_mps"]
    passed = 0
    failed = 0
    total_cost = 0.0
    latencies_ms = []
    case_reports = []

    for case in data["cases"]:
        case_type = case.get("type")

        if case_type == "SUBSTITUTION":
            try:
                from src.engine.solver import evaluate_route_substitution
            except ImportError as e:
                print(json.dumps({"status": "ERROR", "reason": f"Solver import failed: {str(e)}", "bench_hash": bench_hash}))
                sys.exit(1)

            t0 = time.perf_counter()
            try:
                result = evaluate_route_substitution(case["vehicle"], case["anomaly"], case["platform"], speed_mps)
            except Exception as e:
                result = {"action": None, "selected_fallback_id": None, "ejected_request_ids": None, "_error": str(e)}
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

            exp = case["expected"]
            case_ok = True
            fail_reason = None

            if "_error" in result:
                case_ok = False
                fail_reason = f"Execution error: {result['_error']}"
            elif result.get("action") != exp["action"]:
                case_ok = False
                fail_reason = f"Expected action={exp['action']}, got {result.get('action')}"
            elif result.get("selected_fallback_id") != exp["selected_fallback_id"]:
                case_ok = False
                fail_reason = f"Expected fallback_id={exp['selected_fallback_id']}, got {result.get('selected_fallback_id')}"
            elif result.get("ejected_request_ids") != exp["ejected_request_ids"]:
                case_ok = False
                fail_reason = f"Expected ejected_request_ids={exp['ejected_request_ids']}, got {result.get('ejected_request_ids')}"

            if case_ok:
                passed += 1
            else:
                failed += 1

            case_reports.append({
                "case_id": case["case_id"],
                "passed": case_ok,
                "latency_ms": round(elapsed_ms, 4),
                "marginal_cost": None,
                "error": fail_reason
            })

        elif case_type == "INSERTION_MATRIX":
            t0 = time.perf_counter()
            try:
                result = evaluate_request_insertion(
                    case["vehicle"],
                    case["request"],
                    case["platform"],
                    speed_mps,
                    street_matrix=case["street_matrix"]
                )
            except Exception as e:
                result = {"feasible": None, "_error": str(e)}
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

            exp = case["expected"]
            case_ok = True
            fail_reason = None

            if "_error" in result:
                case_ok = False
                fail_reason = f"Execution error: {result['_error']}"
            elif result.get("feasible") != exp["feasible"]:
                case_ok = False
                fail_reason = f"Expected feasible={exp['feasible']}, got {result.get('feasible')}"
            elif exp["feasible"]:
                if result.get("insert_pickup_index") != exp["insert_pickup_index"] or result.get("insert_dropoff_index") != exp["insert_dropoff_index"]:
                    case_ok = False
                    fail_reason = f"Expected indices ({exp['insert_pickup_index']}, {exp['insert_dropoff_index']}), got ({result.get('insert_pickup_index')}, {result.get('insert_dropoff_index')})"
                elif result.get("max_slack_violation_sec", 0.0) > 1e-6:
                    case_ok = False
                    fail_reason = f"SLA slack violated by {result.get('max_slack_violation_sec')}s"
                else:
                    total_cost += float(result.get("marginal_cost", 0.0))

            if case_ok:
                passed += 1
            else:
                failed += 1

            case_reports.append({
                "case_id": case["case_id"],
                "passed": case_ok,
                "latency_ms": round(elapsed_ms, 4),
                "marginal_cost": round(float(result.get("marginal_cost", 0.0)), 3) if result.get("feasible") else None,
                "error": fail_reason
            })

        elif case_type == "FLEET_DISPATCH":
            try:
                from src.engine.solver import dispatch_request_to_fleet
            except ImportError as e:
                print(json.dumps({"status": "ERROR", "reason": f"Solver import failed: {str(e)}", "bench_hash": bench_hash}))
                sys.exit(1)

            t0 = time.perf_counter()
            try:
                result = dispatch_request_to_fleet(case["candidates"], case["request"], case["platform"], speed_mps)
            except Exception as e:
                result = {"winning_vehicle_id": None, "eligible_count": None, "_error": str(e)}
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

            exp = case["expected"]
            case_ok = True
            fail_reason = None

            if "_error" in result:
                case_ok = False
                fail_reason = f"Execution error: {result['_error']}"
            elif result.get("winning_vehicle_id") != exp["winning_vehicle_id"]:
                case_ok = False
                fail_reason = f"Expected winning_vehicle_id={exp['winning_vehicle_id']}, got {result.get('winning_vehicle_id')}"
            elif result.get("eligible_count") != exp["eligible_count"]:
                case_ok = False
                fail_reason = f"Expected eligible_count={exp['eligible_count']}, got {result.get('eligible_count')}"

            if case_ok:
                passed += 1
            else:
                failed += 1

            case_reports.append({
                "case_id": case["case_id"],
                "passed": case_ok,
                "latency_ms": round(elapsed_ms, 4),
                "marginal_cost": None,
                "error": fail_reason
            })

        elif case_type == "END_TO_END_PIPELINE":
            try:
                from src.api.dispatch_pipeline import process_live_dispatch
            except ImportError as e:
                print(json.dumps({"status": "ERROR", "reason": f"Dispatch pipeline import failed: {str(e)}", "bench_hash": bench_hash}))
                sys.exit(1)

            t0 = time.perf_counter()
            try:
                result = process_live_dispatch(case["fleet"], case["request"], case["platform"], speed_mps)
            except Exception as e:
                result = {"spatial_filtered_count": None, "winning_vehicle_id": None, "eligible_count": None, "_error": str(e)}
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

            exp = case["expected"]
            case_ok = True
            fail_reason = None

            if "_error" in result:
                case_ok = False
                fail_reason = f"Execution error: {result['_error']}"
            elif result.get("spatial_filtered_count") != exp["spatial_filtered_count"]:
                case_ok = False
                fail_reason = f"Expected spatial_filtered_count={exp['spatial_filtered_count']}, got {result.get('spatial_filtered_count')}"
            elif result.get("winning_vehicle_id") != exp["winning_vehicle_id"]:
                case_ok = False
                fail_reason = f"Expected winning_vehicle_id={exp['winning_vehicle_id']}, got {result.get('winning_vehicle_id')}"
            elif result.get("eligible_count") != exp["eligible_count"]:
                case_ok = False
                fail_reason = f"Expected eligible_count={exp['eligible_count']}, got {result.get('eligible_count')}"

            if case_ok:
                passed += 1
            else:
                failed += 1

            case_reports.append({
                "case_id": case["case_id"],
                "passed": case_ok,
                "latency_ms": round(elapsed_ms, 4),
                "marginal_cost": None,
                "error": fail_reason
            })

        elif case_type == "MID_ROUTE_LOAD_CHECK":
            t0 = time.perf_counter()
            case_ok = True
            fail_reason = None
            feasible_res = {}
            try:
                heavy_res = evaluate_request_insertion(case["vehicle"], case["heavy_request"], case["platform"], speed_mps)
                feasible_res = evaluate_request_insertion(case["vehicle"], case["feasible_request"], case["platform"], speed_mps)

                if heavy_res.get("feasible") is not False:
                    case_ok = False
                    fail_reason = f"Expected heavy_request feasible=False, got {heavy_res.get('feasible')}"
                elif feasible_res.get("feasible") is not True:
                    case_ok = False
                    fail_reason = f"Expected feasible_request feasible=True, got {feasible_res.get('feasible')}"
                else:
                    updated_stops = feasible_res.get("updated_stops", [])
                    stop_names = [s.get("name") for s in updated_stops]
                    expected_order = case["expected"].get("expected_stops_order", ["P_new", "D_new", "D1"])
                    if stop_names != expected_order:
                        case_ok = False
                        fail_reason = f"Expected updated_stops order {expected_order}, got {stop_names}"
                    else:
                        total_cost += float(feasible_res.get("marginal_cost", 0.0))
            except Exception as e:
                case_ok = False
                fail_reason = f"Execution error in MID_ROUTE_LOAD_CHECK: {str(e)}"

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

            if case_ok:
                passed += 1
            else:
                failed += 1

            case_reports.append({
                "case_id": case["case_id"],
                "passed": case_ok,
                "latency_ms": round(elapsed_ms, 4),
                "marginal_cost": round(float(feasible_res.get("marginal_cost", 0.0)), 3) if case_ok else None,
                "error": fail_reason
            })

        elif case_type == "REST_API_CONTRACT":
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    from fastapi.testclient import TestClient
                    from src.api.main import app
                    client = TestClient(app)
                except ImportError as e:
                    print(json.dumps({"status": "ERROR", "reason": f"FastAPI app import failed: {str(e)}", "bench_hash": bench_hash}))
                    sys.exit(1)

            t0 = time.perf_counter()
            case_ok = True
            fail_reason = None

            try:
                # 1. Health check
                r_health = client.get("/health")
                if r_health.status_code != 200:
                    case_ok = False
                    fail_reason = f"Expected health status 200, got {r_health.status_code}"

                # 2. Evaluate dispatch endpoint
                r_eval = client.post("/api/v1/dispatch/evaluate", json=case["evaluate_payload"])
                eval_data = r_eval.json()
                if r_eval.status_code != case["expected"]["evaluate_status"]:
                    case_ok = False
                    fail_reason = f"Expected evaluate status {case['expected']['evaluate_status']}, got {r_eval.status_code}"
                elif eval_data.get("winning_vehicle_id") != case["expected"]["winning_vehicle_id"]:
                    case_ok = False
                    fail_reason = f"Expected winning_vehicle_id={case['expected']['winning_vehicle_id']}, got {eval_data.get('winning_vehicle_id')}"

                # 3. Substitute endpoint
                r_sub = client.post("/api/v1/dispatch/substitute", json=case["substitute_payload"])
                sub_data = r_sub.json()
                if r_sub.status_code != case["expected"]["substitute_status"]:
                    case_ok = False
                    fail_reason = f"Expected substitute status {case['expected']['substitute_status']}, got {r_sub.status_code}"
                elif sub_data.get("action") != case["expected"]["action"]:
                    case_ok = False
                    fail_reason = f"Expected action={case['expected']['action']}, got {sub_data.get('action')}"

            except Exception as e:
                case_ok = False
                fail_reason = f"REST Client invocation error: {str(e)}"

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

            if case_ok:
                passed += 1
            else:
                failed += 1

            case_reports.append({
                "case_id": case["case_id"],
                "passed": case_ok,
                "latency_ms": round(elapsed_ms, 4),
                "marginal_cost": None,
                "error": fail_reason
            })

        elif case_type == "FREIGHT_LIFECYCLE_REST":
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    from fastapi.testclient import TestClient
                    from src.api.main import app
                    client = TestClient(app)
                except ImportError as e:
                    print(json.dumps({"status": "ERROR", "reason": f"FastAPI app import failed: {str(e)}", "bench_hash": bench_hash}))
                    sys.exit(1)

            t0 = time.perf_counter()
            case_ok = True
            fail_reason = None

            try:
                # 1. Register 10.0t truck
                carrier = case["carrier"]
                r_reg = client.post("/api/v1/freight/carriers/register", json=carrier)
                if r_reg.status_code != 200:
                    case_ok = False
                    fail_reason = f"Carrier register failed with status {r_reg.status_code}"

                # 2. Post 8.0t Load A
                load_a = case["load_A"]
                r_la = client.post("/api/v1/freight/loads", json=load_a)
                if r_la.status_code != 200:
                    case_ok = False
                    fail_reason = f"Load A creation failed with status {r_la.status_code}"

                # 3. Accept Load A
                r_acc_a = client.post(f"/api/v1/freight/loads/{load_a['load_id']}/accept", json={"vehicle_id": carrier["vehicle_id"]})
                if r_acc_a.status_code != 200:
                    case_ok = False
                    fail_reason = f"Load A accept failed with status {r_acc_a.status_code}"

                # 4. Confirm Stop 0 (Pickup) -> assert confirmed_onboard_load == 8.0
                r_conf_a = client.post(f"/api/v1/freight/carriers/{carrier['vehicle_id']}/stops/0/confirm")
                conf_a_data = r_conf_a.json()
                if r_conf_a.status_code != 200:
                    case_ok = False
                    fail_reason = f"Confirm Stop 0 failed with status {r_conf_a.status_code}"
                elif conf_a_data.get("confirmed_onboard_load") != case["expected"]["confirmed_onboard_after_pickup_A"]:
                    case_ok = False
                    fail_reason = f"Expected confirmed_onboard_load={case['expected']['confirmed_onboard_after_pickup_A']}, got {conf_a_data.get('confirmed_onboard_load')}"

                # 5. Post 6.0t Load B (tight deadline requiring mid-route pickup)
                load_b = case["load_B"]
                r_lb = client.post("/api/v1/freight/loads", json=load_b)
                if r_lb.status_code != 200:
                    case_ok = False
                    fail_reason = f"Load B creation failed with status {r_lb.status_code}"

                # 6. Assert Load B is REJECTED from mid_route_pool_offers
                r_off_b = client.get(f"/api/v1/freight/carriers/{carrier['vehicle_id']}/offers")
                off_b_data = r_off_b.json()
                mid_pool_b = off_b_data.get("mid_route_pool_offers", [])
                if any(o.get("load_id") == load_b["load_id"] for o in mid_pool_b):
                    case_ok = False
                    fail_reason = f"Load B {load_b['load_id']} was unexpectedly present in mid_route_pool_offers"

                # 7. Assert returns HTTP 409 if forced
                r_force_b = client.post(f"/api/v1/freight/loads/{load_b['load_id']}/accept", json={"vehicle_id": carrier["vehicle_id"]})
                if r_force_b.status_code != case["expected"]["load_B_force_status"]:
                    case_ok = False
                    fail_reason = f"Expected forced Load B accept to return {case['expected']['load_B_force_status']}, got {r_force_b.status_code}"

                # 8. Post 1.5t Load C along the route
                load_c = case["load_C"]
                r_lc = client.post("/api/v1/freight/loads", json=load_c)
                if r_lc.status_code != 200:
                    case_ok = False
                    fail_reason = f"Load C creation failed with status {r_lc.status_code}"

                # 9. Assert Load C appears in mid_route_pool_offers with "insertion_mode": "MID_ROUTE_POOL"
                r_off_c = client.get(f"/api/v1/freight/carriers/{carrier['vehicle_id']}/offers")
                off_c_data = r_off_c.json()
                mid_pool_c = [o for o in off_c_data.get("mid_route_pool_offers", []) if o.get("load_id") == load_c["load_id"]]
                if len(mid_pool_c) != 1:
                    case_ok = False
                    fail_reason = f"Expected Load C in mid_route_pool_offers, found {len(mid_pool_c)}"
                elif mid_pool_c[0].get("insertion_mode") != case["expected"]["load_C_insertion_mode"]:
                    case_ok = False
                    fail_reason = f"Expected Load C insertion_mode={case['expected']['load_C_insertion_mode']}, got {mid_pool_c[0].get('insertion_mode')}"
                elif mid_pool_c[0].get("peak_onboard_load") != case["expected"]["load_C_peak_onboard_load"]:
                    case_ok = False
                    fail_reason = f"Expected Load C peak_onboard_load={case['expected']['load_C_peak_onboard_load']}, got {mid_pool_c[0].get('peak_onboard_load')}"

                # 10. Accept Load C, assert active_stops updates cleanly
                r_acc_c = client.post(f"/api/v1/freight/loads/{load_c['load_id']}/accept", json={"vehicle_id": carrier["vehicle_id"]})
                acc_c_data = r_acc_c.json()
                if r_acc_c.status_code != 200:
                    case_ok = False
                    fail_reason = f"Load C accept failed with status {r_acc_c.status_code}"
                else:
                    active_stops = acc_c_data.get("active_stops", [])
                    stop_names = [s.get("name") for s in active_stops]
                    if stop_names != case["expected"]["final_active_stops_order"]:
                        case_ok = False
                        fail_reason = f"Expected final active_stops {case['expected']['final_active_stops_order']}, got {stop_names}"

            except Exception as e:
                case_ok = False
                fail_reason = f"Execution error in FREIGHT_LIFECYCLE_REST: {str(e)}"

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

            if case_ok:
                passed += 1
            else:
                failed += 1

            case_reports.append({
                "case_id": case["case_id"],
                "passed": case_ok,
                "latency_ms": round(elapsed_ms, 4),
                "marginal_cost": None,
                "error": fail_reason
            })

        elif case_type == "MULTI_STOP_MANIFEST_REST":
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    from fastapi.testclient import TestClient
                    from src.api.main import app
                    client = TestClient(app)
                except ImportError as e:
                    print(json.dumps({"status": "ERROR", "reason": f"FastAPI app import failed: {str(e)}", "bench_hash": bench_hash}))
                    sys.exit(1)

            t0 = time.perf_counter()
            case_ok = True
            fail_reason = None

            try:
                # 1. Register 10.0t truck
                carrier = case["carrier"]
                r_reg = client.post("/api/v1/freight/carriers/register", json=carrier)
                if r_reg.status_code != 200:
                    case_ok = False
                    fail_reason = f"Carrier registration failed with status {r_reg.status_code}"

                # 2. Shipper 1 posts 3-stop manifest
                s1_manifest = case["shipper_1_manifest"]
                r_s1 = client.post("/api/v1/freight/loads", json=s1_manifest)
                if r_s1.status_code != 200:
                    case_ok = False
                    fail_reason = f"Shipper 1 manifest creation failed with status {r_s1.status_code}"

                # 3. Accept Shipper 1 manifest
                r_acc1 = client.post(
                    f"/api/v1/freight/loads/{s1_manifest['load_id']}/accept",
                    json={"vehicle_id": carrier["vehicle_id"]},
                )
                if r_acc1.status_code != 200:
                    case_ok = False
                    fail_reason = f"Shipper 1 accept failed with status {r_acc1.status_code}"

                # 4. Confirm Stop 0 (Pickup A)
                r_conf1 = client.post(f"/api/v1/freight/carriers/{carrier['vehicle_id']}/stops/0/confirm")
                conf1_data = r_conf1.json()
                if r_conf1.status_code != 200:
                    case_ok = False
                    fail_reason = f"Pickup A confirm failed with status {r_conf1.status_code}"
                elif conf1_data.get("confirmed_onboard_load") != case["expected"]["confirmed_onboard_after_pickup_A"]:
                    case_ok = False
                    fail_reason = f"Expected confirmed_onboard_load={case['expected']['confirmed_onboard_after_pickup_A']}, got {conf1_data.get('confirmed_onboard_load')}"

                # Check carrier state
                r_state = client.get(f"/api/v1/freight/carriers/{carrier['vehicle_id']}/state")
                state_data = r_state.json()
                if r_state.status_code != 200:
                    case_ok = False
                    fail_reason = f"Carrier state check failed with status {r_state.status_code}"
                elif state_data.get("remaining_capacity_tons") != case["expected"]["remaining_capacity_after_pickup_A"]:
                    case_ok = False
                    fail_reason = f"Expected remaining_capacity_tons={case['expected']['remaining_capacity_after_pickup_A']}, got {state_data.get('remaining_capacity_tons')}"
                elif len(state_data.get("route_coordinates", [])) < case["expected"]["min_route_coordinates_points"]:
                    case_ok = False
                    fail_reason = f"State route_coordinates points {len(state_data.get('route_coordinates', []))} < {case['expected']['min_route_coordinates_points']}"

                # 5. Shipper 2 posts 2.5t load along corridor
                s2_load = case["shipper_2_load"]
                r_s2 = client.post("/api/v1/freight/loads", json=s2_load)
                if r_s2.status_code != 200:
                    case_ok = False
                    fail_reason = f"Shipper 2 load creation failed with status {r_s2.status_code}"

                # 6. Verify Shipper 2 in mid_route_pool_offers
                r_off = client.get(f"/api/v1/freight/carriers/{carrier['vehicle_id']}/offers")
                off_data = r_off.json()
                pool_offers = off_data.get("mid_route_pool_offers", [])
                matching_s2 = [o for o in pool_offers if o.get("load_id") == s2_load["load_id"]]
                if len(matching_s2) != 1:
                    case_ok = False
                    fail_reason = f"Expected 1 matching offer for Shipper 2 in mid_route_pool_offers, got {len(matching_s2)}"
                elif matching_s2[0].get("insertion_mode") != case["expected"]["shipper_2_insertion_mode"]:
                    case_ok = False
                    fail_reason = f"Expected insertion_mode={case['expected']['shipper_2_insertion_mode']}, got {matching_s2[0].get('insertion_mode')}"
                elif matching_s2[0].get("peak_onboard_load") != case["expected"]["shipper_2_peak_onboard_load"]:
                    case_ok = False
                    fail_reason = f"Expected peak_onboard_load={case['expected']['shipper_2_peak_onboard_load']}, got {matching_s2[0].get('peak_onboard_load')}"
                elif len(matching_s2[0].get("route_coordinates", [])) < case["expected"]["min_route_coordinates_points"]:
                    case_ok = False
                    fail_reason = f"Offer route_coordinates points {len(matching_s2[0].get('route_coordinates', []))} < {case['expected']['min_route_coordinates_points']}"

                # 7. Accept Shipper 2 load and verify spliced active_stops + projected onboard loads + route coordinates
                r_acc2 = client.post(
                    f"/api/v1/freight/loads/{s2_load['load_id']}/accept",
                    json={"vehicle_id": carrier["vehicle_id"]},
                )
                acc2_data = r_acc2.json()
                if r_acc2.status_code != 200:
                    case_ok = False
                    fail_reason = f"Shipper 2 accept failed with status {r_acc2.status_code}"
                else:
                    final_stops = acc2_data.get("active_stops", [])
                    stop_names = [s.get("name") for s in final_stops]
                    if stop_names != case["expected"]["final_active_stops_order"]:
                        case_ok = False
                        fail_reason = f"Expected final active_stops {case['expected']['final_active_stops_order']}, got {stop_names}"
                    projected_loads = [s.get("projected_onboard_tons") for s in final_stops]
                    if projected_loads != case["expected"]["final_projected_onboard_tons"]:
                        case_ok = False
                        fail_reason = f"Expected projected_onboard_tons {case['expected']['final_projected_onboard_tons']}, got {projected_loads}"
                    if len(acc2_data.get("route_coordinates", [])) < case["expected"]["min_route_coordinates_points"]:
                        case_ok = False
                        fail_reason = f"Accept route_coordinates points {len(acc2_data.get('route_coordinates', []))} < {case['expected']['min_route_coordinates_points']}"

            except Exception as e:
                case_ok = False
                fail_reason = f"Execution error in MULTI_STOP_MANIFEST_REST: {str(e)}"

            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

            if case_ok:
                passed += 1
            else:
                failed += 1

            case_reports.append({
                "case_id": case["case_id"],
                "passed": case_ok,
                "latency_ms": round(elapsed_ms, 4),
                "marginal_cost": None,
                "error": fail_reason
            })

        else:
            t0 = time.perf_counter()
            try:
                result = evaluate_request_insertion(case["vehicle"], case["request"], case["platform"], speed_mps)
            except Exception as e:
                result = {"feasible": None, "_error": str(e)}
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

            exp = case["expected"]
            case_ok = True
            fail_reason = None

            if "_error" in result:
                case_ok = False
                fail_reason = f"Execution error: {result['_error']}"
            elif result.get("feasible") != exp["feasible"]:
                case_ok = False
                fail_reason = f"Expected feasible={exp['feasible']}, got {result.get('feasible')}"
            elif exp["feasible"]:
                if result.get("insert_pickup_index") != exp["insert_pickup_index"] or result.get("insert_dropoff_index") != exp["insert_dropoff_index"]:
                    case_ok = False
                    fail_reason = f"Expected indices ({exp['insert_pickup_index']}, {exp['insert_dropoff_index']}), got ({result.get('insert_pickup_index')}, {result.get('insert_dropoff_index')})"
                elif result.get("max_slack_violation_sec", 0.0) > 1e-6:
                    case_ok = False
                    fail_reason = f"SLA slack violated by {result.get('max_slack_violation_sec')}s"
                else:
                    total_cost += float(result.get("marginal_cost", 0.0))

            if case_ok:
                passed += 1
            else:
                failed += 1

            case_reports.append({
                "case_id": case["case_id"],
                "passed": case_ok,
                "latency_ms": round(elapsed_ms, 4),
                "marginal_cost": round(float(result.get("marginal_cost", 0.0)), 3) if result.get("feasible") else None,
                "error": fail_reason
            })

    summary = {
        "bench_hash": bench_hash,
        "passed": passed,
        "failed": failed,
        "total_marginal_cost": round(total_cost, 3),
        "max_latency_ms": round(max(latencies_ms) if latencies_ms else 0.0, 4),
        "cases": case_reports
    }
    print(json.dumps(summary, indent=2))
    sys.exit(0 if failed == 0 else 1)

if __name__ == "__main__":
    run_harness()
