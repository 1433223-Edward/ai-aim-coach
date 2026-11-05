"""Deterministic, versioned descriptive scores. Client reports are not anti-cheat evidence."""
import math
import statistics
from analysis.scenarios import target_at, SCORING_VERSION, SCORING_CONFIG

def clamp(value):return max(0.0,min(1.0,value))
def number(value,low,high):
    if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:raise ValueError("Invalid finite numeric field.")
    return value

def score(session,config):
    if config.get("scoring_version")!=SCORING_VERSION or config.get("scoring_config")!=SCORING_CONFIG:raise ValueError("Unsupported scoring configuration.")
    width=number(session.get("trainerWidth"),100,10000);height=number(session.get("trainerHeight"),100,10000)
    elapsed=number(session.get("elapsed_ms"),0,config["duration_seconds"]*1000+1000)
    status=session.get("status")
    if status not in ("complete","incomplete"):raise ValueError("Invalid completion status.")
    number(session.get("startTime"),0,0);number(session.get("endTime"),0,elapsed)
    if session.get("schemaVersion")!="scenario-1" or session.get("startTime")!=0 or session.get("endTime")!=elapsed:raise ValueError("Invalid scenario clock/schema.")
    # Flatten only validated static clicks; tracking keeps one bounded stream inside its Attempt.
    attempts=session.get("attempts",[]);samples=[]
    if not isinstance(attempts,list):raise ValueError("Expected Attempts.")
    identities=[a.get("attemptId") if isinstance(a,dict) else None for a in attempts]
    if any(not isinstance(identity,str) or not identity for identity in identities) or len(set(identities))!=len(identities):raise ValueError("Attempt identities must be unique.")
    for attempt in attempts:
        if attempt.get("movements")!=[]:raise ValueError("Scenario mouse movement is sampled, not stored unbounded.")
        target=attempt.get("target")
        if not isinstance(target,dict):raise ValueError("Missing Attempt target.")
        appear=number(target.get("appearTime"),0,elapsed);disappear=number(target.get("disappearTime"),appear,elapsed)
        number(target.get("cursorStartX"),0,width);number(target.get("cursorStartY"),0,height)
    shots=[]
    if config["category"]=="tracking":
        if len(attempts)!=1:raise ValueError("Tracking requires one continuous Attempt.")
        target=attempts[0]["target"]
        tx,ty=target_at(config,width,height,0)
        # Tracking has one continuous target; positions during the run are reconstructed from its seed.
        if target.get("x")!=tx or target.get("y")!=ty or target.get("radius")!=config["radius"] or target["appearTime"]!=0 or target["disappearTime"]!=elapsed:
            raise ValueError("Tracking target snapshot mismatch.")
        samples=attempts[0].get("tracking_samples",[])
        if attempts[0].get("clicks")!=[]:raise ValueError("Tracking shots are not scored.")
    else:
        if len(attempts)>config["target_count"]:raise ValueError("Too many Attempts.")
        appeared=0
        for index,attempt in enumerate(attempts):
            target=attempt.get("target",{});events=attempt.get("clicks")
            if not isinstance(events,list) or attempt.get("tracking_samples",[])!=[]:raise ValueError("Invalid static Attempt evidence.")
            tx,ty=target_at(config,width,height,appeared,index)
            if target.get("x")!=tx or target.get("y")!=ty or target.get("radius")!=config["radius"] or target.get("appearTime")!=appeared:raise ValueError("Attempt target disagrees with seeded scenario.")
            if not events:
                if status=="incomplete" and index==len(attempts)-1:continue
                raise ValueError("Complete Attempts require shot evidence.")
            for event in events:
                if not isinstance(event,dict):raise ValueError("Invalid click object.")
                number(event.get("time"),target["appearTime"],target["disappearTime"])
            if any(event.get("hit") is True for event in events[:-1]):raise ValueError("HIT must end an Attempt.")
            if index<len(attempts)-1 and events[-1].get("hit") is not True:raise ValueError("An unfinished Attempt cannot advance the target.")
            shots.extend(events)
            if len(shots)>2000:raise ValueError("Shot budget exceeded.")
            if events[-1].get("hit") is True:appeared=events[-1]["time"]

    if not isinstance(attempts,list) or not isinstance(samples,list):raise ValueError("Expected event arrays.")
    metrics={"accuracy":None,"average_hit_time_ms":None,"targets_per_second":None,"tracking_coverage":None,"tracking_error_px":None,"stability":None,"recovery_time_ms":None,"direction_agreement":None,"first_hit_rate":None,"valid_hits":None}
    components={};eligible=False
    if config["category"]=="static":
        if len(shots)>2000 or samples:raise ValueError("Invalid static event budget.")
        hits=0;times=[];previous=0;shots_for_target=0;first_hits=0
        for event in shots:
            if not isinstance(event,dict):raise ValueError("Invalid shot.")
            time=number(event.get("time"),previous,elapsed)
            x=number(event.get("x"),0,width);y=number(event.get("y"),0,height)
            tx,ty=target_at(config,width,height,time,hits)
            hit=math.hypot(x-tx,y-ty)<=config["radius"]
            if type(event.get("hit")) is not bool or event["hit"]!=hit:raise ValueError("Shot geometry disagrees with hit flag.")
            if hits>=config["target_count"]:raise ValueError("Shots after completion.")
            shots_for_target+=1
            if hit:
                times.append(time-previous);hits+=1;previous=time
                first_hits+=shots_for_target==1;shots_for_target=0
            # Shot ordering is independent of target appearance time after a MISS.
        if any(shots[i]["time"]>shots[i+1]["time"] for i in range(len(shots)-1)):raise ValueError("Shots are not ordered.")
        # Hit intervals begin at the previous successful target, so intervening misses remain in latency.
        accuracy=hits/len(shots) if shots else 0
        mean=statistics.mean(times) if times else None
        tps=hits/(elapsed/1000) if elapsed else 0
        stability=1/(1+statistics.pstdev(times)/max(mean,1)) if times else 0
        metrics.update(accuracy=accuracy,average_hit_time_ms=mean,targets_per_second=tps,stability=stability,first_hit_rate=first_hits/hits if hits else 0,valid_hits=hits)
        # Speed combines completion latency and throughput; fast isolated clicks cannot hide long idle time.
        speed=clamp(min(SCORING_CONFIG["reference_hit_ms"]/max(mean or 1e9,1),tps/SCORING_CONFIG["reference_tps"]))
        components={"accuracy":accuracy,"speed":speed,"stability":stability}
        progress=hits/config["target_count"]
        eligible=status=="complete" and hits==config["target_count"] and hits>=5 and elapsed>0 and all(value>0 for value in times)
        total=sum(SCORING_CONFIG["static_weights"][key]*value for key,value in components.items())*progress*clamp(speed/SCORING_CONFIG["slow_speed_floor"])
    else:
        if len(samples)>config["duration_seconds"]*20+1:raise ValueError("Invalid tracking event budget.")
        errors=[];covered=0;previous_slot=-1
        for sample in samples:
            if not isinstance(sample,dict):raise ValueError("Invalid sample.")
            time=number(sample.get("time"),0,elapsed)
            slot=int(time//50)
            if slot<=previous_slot:raise ValueError("Only one sample per 50ms slot is permitted.")
            previous_slot=slot
            x=number(sample.get("x"),0,width);y=number(sample.get("y"),0,height)
            tx,ty=target_at(config,width,height,time)
            error=math.hypot(x-tx,y-ty);errors.append(error);covered+=error<=config["radius"]
        # Missing slots count against full-task coverage rather than disappearing from the denominator.
        expected=config["duration_seconds"]*20
        coverage=covered/expected
        error=statistics.mean(errors) if errors else None
        stability=1/(1+statistics.pstdev(errors)/config["radius"]) if errors else 0
        normalized_error=clamp(1-(error or 0)/(SCORING_CONFIG["error_radius_multiple"]*config["radius"])) if errors else 0
        metrics.update(tracking_coverage=clamp(coverage),tracking_error_px=error,stability=stability)
        if config["scenario_id"]=="reactive_tracking":
            recoveries=[]
            for change in range(2000,int(elapsed),2000):
                recovered=next((s["time"]-change for s,e in zip(samples,errors) if change<=s["time"]<change+2000 and e<=config["radius"]),2000)
                recoveries.append(recovered)
            metrics["recovery_time_ms"]=statistics.mean(recoveries) if recoveries else None
        axis="y" if config["scenario_id"]=="vertical_tracking" else "x"
        directions=[]
        for left,right in zip(samples,samples[1:]):
            lt=target_at(config,width,height,left["time"])[1 if axis=="y" else 0]
            rt=target_at(config,width,height,right["time"])[1 if axis=="y" else 0]
            if abs(rt-lt)>1e-9:directions.append((right[axis]-left[axis])*(rt-lt)>0)
        metrics["direction_agreement"]=sum(directions)/len(directions) if directions else None
        # Distance is normalized by target radius before unlike units enter the weighted score.
        components={"coverage":clamp(coverage),"error":normalized_error,"stability":stability}
        eligible=status=="complete" and elapsed>=config["duration_seconds"]*1000 and len(samples)>=expected*.9
        progress=min(1,len(samples)/expected)
        total=SCORING_CONFIG["tracking_weights"]["coverage"]*clamp(coverage)+(SCORING_CONFIG["tracking_weights"]["error"]*normalized_error+SCORING_CONFIG["tracking_weights"]["stability"]*stability)*progress
    # An interrupted or undersampled run never receives a high completed-task score.
    overall=round(100*clamp(total),2) if eligible else 0.0
    ordered=sorted(components,key=components.get)
    return {"scoring_version":SCORING_VERSION,"overall_score":overall,"eligible":eligible,
            "reason":None if eligible else "incomplete_or_insufficient_samples","metrics":metrics,"normalized_components":components,
            "strength":ordered[-1] if ordered and eligible else None,"weakness":ordered[0] if ordered and eligible else None,
            "limitation":"Descriptive client-reported evidence; not anti-cheat or a calibrated skill rating."}
