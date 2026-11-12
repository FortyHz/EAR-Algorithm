---
file_path: specs/DPDPTW_ROUTING_ENGINE.md
document_id: ARCH-SPEC-004
title: Stateful Dynamic Multi-Point Routing & Sub-Second Substitution Engine (DPDPTW)
platforms:
  - ENTROPIC / Freight (Intercity & High-Capacity Freight Clearinghouse)
  - Trippy Platform (Shared Micro-Transit & Dynamic Pooling)
status: IMPLEMENTED & VERIFIED (10/10 BENCHMARKS PASSING)
version: 1.3.0
bench_hash: 0fdda644535e
last_updated: 2026-11-12
---

# ARCH-SPEC-004: Stateful Dynamic Multi-Point Routing & Substitution Engine

> **STATUS:** `IMPLEMENTED & VERIFIED` (10/10 ground-truth verification benchmarks passing, deterministic bench_hash: `0fdda644535e`).

---

## 1. Executive Summary & Verification Summary

The unified dual-profile algorithmic core solving the **Dynamic Pickup and Delivery Problem with Time Windows (DPDPTW)** is fully implemented, verified, and wrapped in a sub-second FastAPI REST microservice.

### Benchmark Verification Matrix (`bench_hash: 0fdda644535e`)

| Case ID | Type | Description | Result | Latency |
| :--- | :--- | :--- | :--- | :--- |
| `CASE_01_CAPACITY_REJECT` | Insertion | Instant rejection when vehicle capacity max is exceeded | **PASSED** | `< 0.10 ms` |
| `CASE_02_SLACK_BREACH_REJECT` | Insertion | Instant rejection when detour delay breaches downstream SLA slack | **PASSED** | `< 0.05 ms` |
| `CASE_03_OPTIMAL_MID_ROUTE_SPLICE` | Insertion | Optimal mid-route insertion between active stops along corridor | **PASSED** | `< 0.05 ms` |
| `CASE_04_SEQUENTIAL_APPEND_BACKHAUL`| Insertion | Sequential backhaul append after existing load unloads | **PASSED** | `< 0.05 ms` |
| `CASE_05_CORRIDOR_SWAP_SUBSTITUTION` | Substitution| Hot-swap to pre-cached disjoint fallback route on edge delay | **PASSED** | `< 0.02 ms` |
| `CASE_06_DESTROY_AND_REPAIR_EJECTION`| Substitution| Eject unvisited pickup stops to re-auction while preserving onboard | **PASSED** | `< 0.02 ms` |
| `CASE_07_STREET_MATRIX_ASYMMETRY_TRAP`| Matrix | Asymmetric street network matrix avoidance of one-way traps | **PASSED** | `< 0.05 ms` |
| `CASE_08_FLEET_DISPATCH_TOURNAMENT` | Tournament | Multi-vehicle candidate ranking and winning vehicle dispatch | **PASSED** | `< 0.15 ms` |
| `CASE_09_END_TO_END_PIPELINE` | Pipeline | End-to-end spatial pruning, matrix generation, and dispatch | **PASSED** | `< 0.50 ms` |
| `CASE_10_FASTAPI_REST_CONTRACT` | REST API | Complete HTTP REST contract (Health, Evaluate, Substitute) | **PASSED** | `< 80.0 ms` (cold) / `< 0.50 ms` (warm) |

---

## 2. Core Architecture & File Tree

```
/root/EAR-algorithm/
├── AGENT_INSTRUCTIONS.md           # Immutable agent constitution & guardrails
├── Dockerfile                      # Container specification (port 8090)
├── README.md                       # Executive overview & quickstart
├── benchmarks/
│   ├── baseline_scores.json        # Verified benchmark outputs
│   └── ground_truth_cases.json     # 10 deterministic ground-truth scenarios (v1.3.0)
├── harness.py                      # Deterministic algebraic evaluation referee
├── requirements.txt                # Microservice dependencies
├── specs/
│   └── DPDPTW_ROUTING_ENGINE.md    # As-built architecture specification
├── src/
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── h3_indexer.py           # Spatial corridor trajectory index
│   │   └── matrix_provider.py      # Street distance & duration matrix provider (OSRM/Valhalla)
│   ├── api/
│   │   ├── __init__.py
│   │   ├── dispatch_pipeline.py    # Unified 5-stage dispatch pipeline
│   │   └── main.py                 # FastAPI REST API microservice
│   └── engine/
│       ├── __init__.py
│       └── solver.py               # Kinetic DPDPTW solver, corridor swap, and ejection
└── tests/
```

---

## 3. Mathematical Formulation & Profiles

### Dual-Profile Marginal Cost Formula
$$\Delta C = \alpha \cdot (\Delta \text{Distance}_{\text{km}}) + \beta \cdot (\Delta \text{Duration}_{\text{sec}})$$

* **`TRIPPY_POOL` Profile:** $\alpha = 0.15$, $\beta = 0.85$ (optimized for minimal passenger detour delay and sub-second ETAs).
* **`ENTROPIC_FREIGHT` Profile:** $\alpha = 0.65$, $\beta = 0.25$ (optimized for ton-kilometer transport efficiency and backhaul utilization).

---

## 4. REST API Integration Contracts

### `POST /api/v1/dispatch/evaluate`
```bash
curl -X POST http://127.0.0.1:8090/api/v1/dispatch/evaluate \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "ENTROPIC_FREIGHT",
    "fleet": [...],
    "request": {...},
    "speed_mps": 13.88
  }'
```

### `POST /api/v1/dispatch/substitute`
```bash
curl -X POST http://127.0.0.1:8090/api/v1/dispatch/substitute \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "TRIPPY_POOL",
    "vehicle": {...},
    "anomaly": {"affected_stop_index": 0, "primary_delay_sec": 1500.0},
    "speed_mps": 13.88
  }'
```
