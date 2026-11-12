# EAR-Algorithm (DPDPTW Core)

Stateful Dynamic Multi-Point Routing & Sub-Second Substitution Engine for:
- **ENTROPIC / Freight**: High-capacity intercity freight clearinghouse.
- **Trippy Platform**: Shared micro-transit & dynamic pooling bot.

Based on specification **ARCH-SPEC-004**.

---

## Performance & Benchmark Highlights

- **Deterministic Evaluation Hash:** `0fdda644535e`
- **Benchmark Coverage:** 10/10 verified test scenarios in `benchmarks/ground_truth_cases.json`.
- **Latency:**
  - $O(1)$ Incremental Insertion SLA check: `< 0.05 ms`
  - Multi-vehicle candidate ranking tournament: `< 0.15 ms`
  - 5-stage dispatch pipeline: `< 0.50 ms`
  - Route failover & substitution: `< 0.02 ms`

---

## Quickstart

### 1. Installation
```bash
pip install -r requirements.txt
```

### 2. Run Verification Harness
```bash
python3 harness.py
```

### 3. Launch the REST API
```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8090
```

### 4. Health Check
```bash
curl http://127.0.0.1:8090/health
```

---

## Documentation

- Architecture Specification: [specs/DPDPTW_ROUTING_ENGINE.md](specs/DPDPTW_ROUTING_ENGINE.md)
- Module Reference: [docs/modules/DPDPTW_ROUTING_MODULE.md](docs/modules/DPDPTW_ROUTING_MODULE.md)
- Benchmark Scores: [benchmarks/baseline_scores.json](benchmarks/baseline_scores.json)
