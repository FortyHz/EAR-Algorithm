import hashlib
import json
import time
import sys
import warnings

def get_benchmark_hash(filepath: str) -> str:
    with open(filepath, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]

def run_harness():
    bench_path = "benchmarks/ground_truth_cases.json"
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
