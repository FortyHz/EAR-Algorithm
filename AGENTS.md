# Project Agents & Architecture Governance

## Core Role Definition
- **Role**: **EAR Core Routing & Dispatch Architect**
- **Ownership Scope**:
  - `src/engine/`: DPDPTW combinatorial solver, route insertion, route substitution, and multi-stop manifest interleaving algorithms.
  - `src/adapters/`: Spatial trajectory corridors (Uber H3), GIS matrix routing (Valhalla / OSRM / Haversine topological fallback), and road shape polylines.
  - `src/api/`: FastAPI contracts, stateful freight lifecycle, telematics state, and dispatch pipelines.
  - `harness.py` & `benchmarks/`: End-to-end benchmark regression testing, SLA enforcement, and ground-truth verification suites.

## Architectural Contracts & Integration Rules
1. **Fusion Integration Hooks**:
   - Every new or updated FastAPI route in `src/api/freight_lifecycle.py` and `src/api/main.py` MUST include a machine-parseable comment header directly above the route definition:
     ```python
     # [FUSION-HOOK]: <METHOD> <endpoint_path> -> <description>
     ```
   - This enables the downstream Fusion integration agent to automatically discover and map contract fields, request payloads, and response models.

2. **Deterministic Offline Execution**:
   - All routing and dispatch algorithms must operate deterministically without hard dependencies on external network services or platform-specific GIS data directories.
   - GIS matrix and polyline providers must transparently fall back to high-throughput in-memory topological models when compiled local graph tiles are unavailable.

3. **Multi-Stop Manifest & Forward Compatibility**:
   - Standard 1-pickup/1-dropoff requests and N-stop shipper manifests must share a unified scheduling and verification engine.
   - Cumulative vehicle onboard loads and deadlines must be verified across every intermediate stop in the interleaved schedule.
