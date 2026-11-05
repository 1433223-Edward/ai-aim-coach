// Mirror the server trajectory contract. Tests compare both implementations numerically.
export function randomValue(seed,index){
    let value=(seed+Math.imul(index,0x9e3779b9))>>>0;
    value=Math.imul(value^(value>>>16),0x85ebca6b)>>>0;
    value=Math.imul(value^(value>>>13),0xc2b2ae35)>>>0;
    return ((value^(value>>>16))>>>0)/4294967296;
}
export function targetAt(config,width,height,time,index=0){
    const radius=config.radius,key=config.scenario_id;
    if(key==="four_target_switching"||key==="six_target_precision"){
        const points=key==="four_target_switching"?[[.25,.25],[.75,.25],[.75,.75],[.25,.75]]:[[.2,.3],[.5,.3],[.8,.3],[.2,.7],[.5,.7],[.8,.7]];
        const [x,y]=points[index%points.length];return [x*width,y*height];
    }
    if(key==="flick_target")return [radius+randomValue(config.seed,index*2)*(width-2*radius),radius+randomValue(config.seed,index*2+1)*(height-2*radius)];
    const span=(key==="vertical_tracking"?height:width)-2*radius,seconds=time/1000;
    let distance=config.speed*seconds;
    if(key==="reactive_tracking"){
        const segment=Math.floor(seconds/2);distance=0;
        const velocity=i=>config.speed*(.6+randomValue(config.seed,i)*.8)*(i===0||randomValue(config.seed,i+100)>.5?1:-1);
        for(let i=0;i<segment;i++)distance+=2*velocity(i);
        distance+=(seconds-segment*2)*velocity(segment);
    }
    const phase=((span/2+(config.direction==="positive"?1:-1)*distance)%(2*span)+2*span)%(2*span);
    const position=radius+(phase<=span?phase:2*span-phase);
    return key==="vertical_tracking"?[width/2,position]:[position,height/2];
}
