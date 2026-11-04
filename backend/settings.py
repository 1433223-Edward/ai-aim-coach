"""Bounds shared by API callers and storage; existing protocols stay untouched."""
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class ExperimentSettings(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    sessions: int = Field(default=9, ge=1, le=30)
    attempts: int = Field(default=3, ge=1, le=100)
    sensitivity: float = Field(default=1.0, ge=0.1, le=5.0)
    time_limit_seconds: int = Field(default=0, ge=0, le=3600)
    countdown_seconds: int = Field(default=0, ge=0, le=10)
    crosshair_type: Literal["Cross", "Dot", "Circle"] = "Circle"
    crosshair_color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    crosshair_size: int = Field(default=16, ge=4, le=40)
    crosshair_thickness: int = Field(default=2, ge=1, le=8)
    target_color: str = Field(default="#e74c3c", pattern=r"^#[0-9a-fA-F]{6}$")
    background: Literal["dark", "slate", "midnight"] = "dark"
    hit_sound: bool = False
    hit_feedback: bool = False
    miss_feedback: bool = False
