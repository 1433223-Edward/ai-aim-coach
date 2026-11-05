"""Server-owned 2D task registry and reproducible trajectories; no executable client dispatch."""
from copy import deepcopy
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
import math

SCORING_VERSION = "aim-score-1"
SCORING_CONFIG = {"version":SCORING_VERSION,"static_weights":{"accuracy":.45,"speed":.35,"stability":.20},
                  "tracking_weights":{"coverage":.60,"error":.25,"stability":.15},
                  "reference_hit_ms":800,"reference_tps":1.25,"slow_speed_floor":.25,"error_radius_multiple":4}
DEFINITIONS = (
    ("four_target_switching", "Four Target Switching", "static", "Cycle clockwise through four fixed targets."),
    ("six_target_precision", "Six Target Precision", "static", "Cycle through six smaller fixed targets."),
    ("horizontal_tracking", "Horizontal Tracking", "tracking", "Follow a horizontally reflecting target."),
    ("vertical_tracking", "Vertical Tracking", "tracking", "Follow a vertically reflecting target."),
    ("flick_target", "Flick Target", "static", "Acquire a seeded random target after each hit."),
    ("reactive_tracking", "Reactive Tracking", "tracking", "Follow seeded speed changes every two seconds."),
)

class ScenarioConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    scenario_id: Literal["four_target_switching","six_target_precision","horizontal_tracking","vertical_tracking","flick_target","reactive_tracking"]
    difficulty: Literal["easy","normal","hard"] = "normal"
    seed: int = Field(default=1,ge=0,le=2147483647)
    duration_seconds: int = Field(default=20,ge=10,le=120)
    target_count: int = Field(default=20,ge=5,le=100)
    speed: int = Field(default=120,ge=40,le=400)
    direction: Literal["positive","negative"] = "positive"

def registry():
    return [{"scenario_id":key,"name":name,"category":category,"description":description,
             "difficulty":["easy","normal","hard"],"allowed_parameters":list(ScenarioConfig.model_fields),
             "scoring_version":SCORING_VERSION,"supported_metrics":
             ["accuracy","average_hit_time_ms","targets_per_second","first_hit_rate","valid_hits","stability","overall_score"] if category=="static"
             else ["tracking_coverage","tracking_error_px","stability","direction_agreement","recovery_time_ms","overall_score"]}
            for key,name,category,description in DEFINITIONS]

def snapshot(value):
    # Copy versioned scoring constants into the frozen protocol, never accept client-selected weights.
    config=ScenarioConfig(**value).model_dump()
    definition=next(item for item in registry() if item["scenario_id"]==config["scenario_id"])
    return {**config,"category":definition["category"],"scoring_version":SCORING_VERSION,"scoring_config":deepcopy(SCORING_CONFIG),"sampling_hz":20,
            "radius":({"easy":32,"normal":24,"hard":16}[config["difficulty"]] * (.65 if config["scenario_id"]=="six_target_precision" else 1))}

def random_value(seed,index):
    # The same unsigned 32-bit hash is implemented in the browser.
    value=(seed+index*0x9e3779b9)&0xffffffff
    value=((value^(value>>16))*0x85ebca6b)&0xffffffff
    value=((value^(value>>13))*0xc2b2ae35)&0xffffffff
    return ((value^(value>>16))&0xffffffff)/4294967296

def target_at(config,width,height,time_ms,index=0):
    radius=config["radius"]
    if min(width,height)<radius*6:raise ValueError("Viewport is too small for this difficulty.")
    key=config["scenario_id"]
    if key in ("four_target_switching","six_target_precision"):
        points=[(.25,.25),(.75,.25),(.75,.75),(.25,.75)] if key=="four_target_switching" else [(x,y) for y in (.3,.7) for x in (.2,.5,.8)]
        x,y=points[index%len(points)];return x*width,y*height
    if key=="flick_target":
        return radius+random_value(config["seed"],index*2)*(width-2*radius),radius+random_value(config["seed"],index*2+1)*(height-2*radius)
    # Triangle-wave reflection preserves speed while keeping the complete target inside the viewport.
    span=(height if key=="vertical_tracking" else width)-2*radius
    seconds=time_ms/1000
    sign=1 if config["direction"]=="positive" else -1
    if key=="reactive_tracking":
        # Integrate piecewise velocities so direction changes never teleport the target.
        segments=int(seconds//2)
        distance=sum(2*config["speed"]*(.6+random_value(config["seed"],i)*.8)*(1 if i==0 or random_value(config["seed"],i+100)>.5 else -1) for i in range(segments))
        distance+=(seconds-segments*2)*config["speed"]*(.6+random_value(config["seed"],segments)*.8)*(1 if segments==0 or random_value(config["seed"],segments+100)>.5 else -1)
    else:distance=config["speed"]*seconds
    phase=(span/2+sign*distance)%(2*span)
    position=radius+(phase if phase<=span else 2*span-phase)
    return (width/2,position) if key=="vertical_tracking" else (position,height/2)
