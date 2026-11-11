import hashlib
import json
import time
import sys

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
        if case.get("type") == "SUBSTITUTION":
            try:
                from src.engine.solver import evaluate_route_substitution
            except ImportError as e:
                print(json.dumps({"status": "ERROR", "reason": f"Solver import failed: {str(e)}", "bench_hash": bench_hash}))
                sys.exit(1)

            t0 = time.perf_counter()
            result = evaluate_route_substitution(case["vehicle"], case["anomaly"], case["platform"], speed_mps)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

            exp = case["expected"]
            case_ok = True
            fail_reason = None

            if result.get("action") != exp["action"]:
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
        else:
            t0 = time.perf_counter()
            result = evaluate_request_insertion(case["vehicle"], case["request"], case["platform"], speed_mps)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies_ms.append(elapsed_ms)

            exp = case["expected"]
            case_ok = True
            fail_reason = None

            if result.get("feasible") != exp["feasible"]:
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
