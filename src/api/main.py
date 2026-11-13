import time
from typing import Any, Optional
from fastapi import FastAPI
from pydantic import BaseModel, Field

from src.api.dispatch_pipeline import process_live_dispatch
from src.api.freight_lifecycle import router as freight_router
from src.engine.solver import evaluate_route_substitution

app = FastAPI(
    title="EAR Dynamic Routing & Route Substitution Engine",
    version="1.0.0",
    description="Stateful Dynamic Multi-Point Routing & Sub-Second Substitution Engine (DPDPTW - ARCH-SPEC-004)",
)
app.include_router(freight_router)


class EvaluateDispatchPayload(BaseModel):
    platform: str
    fleet: list[dict[str, Any]]
    request: dict[str, Any]
    speed_mps: float = Field(default=13.88)


class RouteSubstitutionPayload(BaseModel):
    platform: str
    vehicle: dict[str, Any]
    anomaly: dict[str, Any]
    speed_mps: float = Field(default=13.88)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "engine": "ARCH-SPEC-004",
        "platforms": ["ENTROPIC_FREIGHT", "TRIPPY_POOL"],
    }


@app.post("/api/v1/dispatch/evaluate")
def dispatch_evaluate(payload: EvaluateDispatchPayload) -> dict[str, Any]:
    t0 = time.perf_counter()
    res = process_live_dispatch(
        fleet=payload.fleet,
        request=payload.request,
        platform=payload.platform,
        speed_mps=payload.speed_mps,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    res["compute_time_ms"] = elapsed_ms
    return res


@app.post("/api/v1/dispatch/substitute")
def dispatch_substitute(payload: RouteSubstitutionPayload) -> dict[str, Any]:
    t0 = time.perf_counter()
    res = evaluate_route_substitution(
        vehicle=payload.vehicle,
        anomaly=payload.anomaly,
        platform=payload.platform,
        speed_mps=payload.speed_mps,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    res["compute_time_ms"] = elapsed_ms
    return res
