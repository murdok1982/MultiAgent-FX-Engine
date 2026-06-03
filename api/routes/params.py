"""Dynamic parameters management endpoints."""
from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

router = APIRouter()

# ── Per-parameter safety bounds ───────────────────────────────────────────────
# Each entry: (min_inclusive, max_inclusive)
# Values outside these ranges are rejected regardless of caller identity.
_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "sl_multiplier":                (0.5,  3.0),
    "tp_multiplier":                (0.5,  5.0),
    "min_confidence_threshold":     (0.4,  0.95),
    "rsi_overbought":               (60.0, 90.0),
    "rsi_oversold":                 (10.0, 40.0),
    "technical_strength_threshold": (0.3,  0.95),
    "session_quality_threshold":    (0.2,  0.9),
}


def _validate_param_value(param_name: str, value: float) -> None:
    """Raise HTTPException 422 if value is outside the allowed safety bounds."""
    bounds = _PARAM_BOUNDS.get(param_name)
    if bounds is None:
        return  # No bounds defined for this param — allow any value
    lo, hi = bounds
    if not (lo <= value <= hi):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Value {value} for '{param_name}' is outside the allowed safety range "
                f"[{lo}, {hi}]. Change rejected."
            ),
        )


class ParamUpdate(BaseModel):
    value: float
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("reason must not be empty")
        return v[:300]  # Truncate to DB column size


@router.get("/")
def list_params():
    """List all dynamic parameters with metadata."""
    try:
        from database import get_session, DynamicParam
        with get_session() as session:
            params = session.query(DynamicParam).all()
            return [
                {
                    "param_name": p.param_name,
                    "param_value": p.param_value,
                    "description": p.description,
                    "updated_by": p.updated_by,
                    "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                    "change_reason": p.change_reason,
                    # Surface safety bounds so the UI can enforce them client-side too
                    "bounds": _PARAM_BOUNDS.get(p.param_name),
                }
                for p in params
            ]
    except Exception as e:
        return {"error": str(e), "params": []}


@router.put("/{param_name}")
def update_param(param_name: str, update: ParamUpdate):
    """Manually override a dynamic parameter (safety-bound validated)."""
    try:
        from database import set_dynamic_param, INITIAL_DYNAMIC_PARAMS
        if param_name not in INITIAL_DYNAMIC_PARAMS:
            raise HTTPException(status_code=404, detail=f"Unknown parameter: {param_name}")

        # Enforce safety bounds before any write
        _validate_param_value(param_name, update.value)

        set_dynamic_param(param_name, update.value, update.reason, updated_by="user")
        return {"param_name": param_name, "value": update.value, "reason": update.reason}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
