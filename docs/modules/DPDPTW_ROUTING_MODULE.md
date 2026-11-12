# DPDPTW Routing & Substitution Module Reference

This document provides developer reference documentation for the Stateful Dynamic Pickup and Delivery Problem with Time Windows (DPDPTW) engine and FastAPI REST microservice implemented in `EAR-algorithm`.

---

## 1. Module Overview

* **Document ID:** `ARCH-SPEC-004`
* **Status:** `IMPLEMENTED & VERIFIED`
* **Benchmark Suite Hash:** `0fdda644535e` (10/10 passing)
* **Default Port:** `8090`
* **Service Entry Point:** `src.api.main:app`

---

## 2. Component Reference

### 2.1. Engine Layer (`src/engine/solver.py`)
- `evaluate_request_insertion(vehicle, request, platform, speed_mps, street_matrix=None) -> dict`:
  Computes the optimal insertion indices `(insert_pickup_index, insert_dropoff_index)` and marginal cost for a new request while checking capacity and deadline SLAs.
- `evaluate_route_substitution(vehicle, anomaly, platform, speed_mps) -> dict`:
  Evaluates route disruptions. Performs Tier 1 Disjoint Fallback Corridor swap or Tier 2 Destroy-and-Repair stop ejection.
- `dispatch_request_to_fleet(candidates, request, platform, speed_mps, street_matrices=None) -> dict`:
  Runs multi-vehicle candidate evaluation and ranks eligible vehicles by marginal cost.

### 2.2. Adapters Layer (`src/adapters/`)
- `SpatialCorridorIndex` (`h3_indexer.py`):
  In-memory spatial index that discretizes vehicle route corridors into discrete cells (~500m urban for Trippy, ~5km intercity for Entropic) and queries nearby vehicles in `< 2.0 ms`.
- `get_street_matrix` (`matrix_provider.py`):
  Constructs distance and duration matrices using a street winding factor (default $1.25\times$) or an external OSRM endpoint (`OSRM_URL`).

### 2.3. Pipeline & API Layer (`src/api/`)
- `process_live_dispatch` (`dispatch_pipeline.py`):
  Unified 5-stage pipeline connecting spatial filtering, matrix generation, and the DPDPTW solver.
- `app` (`main.py`):
  FastAPI application exposing `/health`, `/api/v1/dispatch/evaluate`, and `/api/v1/dispatch/substitute`.

---

## 3. Verified Benchmark Results (`python3 harness.py`)

```json
{
  "bench_hash": "0fdda644535e",
  "passed": 10,
  "failed": 0,
  "total_marginal_cost": 17.869,
  "max_latency_ms": 70.1589,
  "cases": [
    {"case_id": "CASE_01_CAPACITY_REJECT", "passed": true, "latency_ms": 0.0693},
    {"case_id": "CASE_02_SLACK_BREACH_REJECT", "passed": true, "latency_ms": 0.0399},
    {"case_id": "CASE_03_OPTIMAL_MID_ROUTE_SPLICE", "passed": true, "latency_ms": 0.0292},
    {"case_id": "CASE_04_SEQUENTIAL_APPEND_BACKHAUL", "passed": true, "latency_ms": 0.0255},
    {"case_id": "CASE_05_CORRIDOR_SWAP_SUBSTITUTION", "passed": true, "latency_ms": 0.0139},
    {"case_id": "CASE_06_DESTROY_AND_REPAIR_EJECTION", "passed": true, "latency_ms": 0.0159},
    {"case_id": "CASE_07_STREET_MATRIX_ASYMMETRY_TRAP", "passed": true, "latency_ms": 0.0349},
    {"case_id": "CASE_08_FLEET_DISPATCH_TOURNAMENT", "passed": true, "latency_ms": 0.0776},
    {"case_id": "CASE_09_END_TO_END_PIPELINE", "passed": true, "latency_ms": 0.4149},
    {"case_id": "CASE_10_FASTAPI_REST_CONTRACT", "passed": true, "latency_ms": 70.1589}
  ]
}
```

---

## 4. Quickstart & REST API Integration

### Starting the Server
```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8090
```

### 1. Health Probe
```bash
curl -s http://127.0.0.1:8090/health
```
**Response:**
```json
{"status":"ok","engine":"ARCH-SPEC-004","platforms":["ENTROPIC_FREIGHT","TRIPPY_POOL"]}
```

### 2. Live Dispatch Evaluation
```bash
curl -s -X POST http://127.0.0.1:8090/api/v1/dispatch/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "ENTROPIC_FREIGHT",
    "fleet": [
      {
        "id": "veh_01",
        "capacity_max": 10,
        "current_x": 0.0,
        "current_y": 3000.0,
        "stops": [
          {"name": "P1", "x": 1000.0, "y": 3000.0, "is_pickup": true, "load_change": 4, "latest_time_sec": 3600.0},
          {"name": "D1", "x": 8000.0, "y": 3000.0, "is_pickup": false, "load_change": -4, "latest_time_sec": 7200.0}
        ]
      }
    ],
    "request": {
      "id": "req_01",
      "load": 3,
      "pickup": {"name": "P_new", "x": 2500.0, "y": 3100.0, "latest_time_sec": 4000.0},
      "dropoff": {"name": "D_new", "x": 6000.0, "y": 3100.0, "latest_time_sec": 6500.0}
    },
    "speed_mps": 13.88
  }'
```
**Response:**
```json
{
  "spatial_filtered_count": 1,
  "winning_vehicle_id": "veh_01",
  "eligible_count": 1,
  "ranked_candidates": [
    {
      "vehicle_id": "veh_01",
      "marginal_cost": 0.137,
      "insert_pickup_index": 1,
      "insert_dropoff_index": 1
    }
  ],
  "compute_time_ms": 0.407
}
```

### 3. Route Substitution / Failover
```bash
curl -s -X POST http://127.0.0.1:8090/api/v1/dispatch/substitute \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "TRIPPY_POOL",
    "vehicle": {
      "id": "veh_02",
      "capacity_max": 4,
      "current_x": 0.0,
      "current_y": 0.0,
      "stops": [
        {"name": "P2", "x": 2000.0, "y": 500.0, "is_pickup": true, "load_change": 1, "latest_time_sec": 300.0, "request_id": "req_unvisited_2"},
        {"name": "D2", "x": 3500.0, "y": 500.0, "is_pickup": false, "load_change": -1, "latest_time_sec": 600.0, "request_id": "req_unvisited_2"},
        {"name": "D1", "x": 4000.0, "y": 0.0, "is_pickup": false, "load_change": -1, "latest_time_sec": 500.0, "request_id": "req_onboard_1"}
      ],
      "fallback_routes": []
    },
    "anomaly": {
      "affected_stop_index": 0,
      "primary_delay_sec": 1500.0
    },
    "speed_mps": 13.88
  }'
```
**Response:**
```json
{
  "action": "EJECTED_TO_REAUCTION",
  "selected_fallback_id": null,
  "ejected_request_ids": ["req_unvisited_2"],
  "compute_time_ms": 0.036
}
```
