// One browser preference schema. The server independently validates every frozen experiment snapshot.
export const DEFAULT_SETTINGS = Object.freeze({sessions:9,attempts:3,sensitivity:1,time_limit_seconds:0,countdown_seconds:0,
    crosshair_type:"Circle",crosshair_color:"#ffffff",crosshair_size:16,crosshair_thickness:2,
    target_color:"#e74c3c",background:"dark",hit_sound:false,hit_feedback:false,miss_feedback:false});
const ranges = {sessions:[1,30],attempts:[1,100],sensitivity:[.1,5],time_limit_seconds:[0,3600],countdown_seconds:[0,10],crosshair_size:[4,40],crosshair_thickness:[1,8]};
export function validateSettings(value) {
    if (!value || typeof value !== "object" || Array.isArray(value) || Object.keys(value).some(key => !Object.hasOwn(DEFAULT_SETTINGS,key))) throw new Error("未知设置字段。");
    const settings = {...DEFAULT_SETTINGS,...value};
    for (const [key,[min,max]] of Object.entries(ranges)) {
        if (!Number.isFinite(settings[key]) || settings[key]<min || settings[key]>max || (key!=="sensitivity" && !Number.isInteger(settings[key]))) throw new Error(`无效设置：${key}`);
    }
    for (const key of ["crosshair_color","target_color"]) if (!/^#[0-9a-fA-F]{6}$/.test(settings[key])) throw new Error("颜色需要六位 HEX。");
    if (!["Cross","Dot","Circle"].includes(settings.crosshair_type) || !["dark","slate","midnight"].includes(settings.background)) throw new Error("无效视觉预设。");
    for (const key of ["hit_sound","hit_feedback","miss_feedback"]) if (typeof settings[key]!=="boolean") throw new Error("反馈开关必须是布尔值。");
    return settings;
}
export function readSettings() {
    try { const raw=localStorage.getItem("aimcoachSettings"); return raw ? validateSettings(JSON.parse(raw)) : null; }
    catch { return null; }
}
export function bindSettings(canOpen) {
    const dialog=document.getElementById("settingsDialog"),form=document.getElementById("settingsForm");
    function fill(settings){
        for(const [key,value] of Object.entries(settings)) {
            const input=form.elements.namedItem(key);
            if(input.type==="checkbox")input.checked=value;else input.value=value;
        }
    }
    // Reset edits the preference form only; an active Experiment retains its frozen settings.
    document.getElementById("resetSettings")?.addEventListener("click",()=>{
        fill(DEFAULT_SETTINGS);document.getElementById("settingsStatus").textContent="默认值已填入；点击保存偏好后生效，当前实验不变。";
    });
    for(const [key,[min,max]] of Object.entries(ranges)) {
        const input=form.elements.namedItem(key),label=input.closest?.("label");
        if(label){const help=document.createElement("small");help.textContent=`合法范围 ${min}–${max}`;label.append(help);}
    }
    document.getElementById("openSettings").addEventListener("click",()=>{
        if (!canOpen()) return;
        const settings=readSettings()||DEFAULT_SETTINGS;
        fill(settings);
        document.getElementById("settingsStatus").textContent="设置仅在新实验创建时冻结；当前实验不变。";
        dialog.showModal();
    });
    document.getElementById("closeSettings").addEventListener("click",()=>dialog.close());
    form.addEventListener("submit",event=>{
        event.preventDefault();
        try {
            const value=Object.fromEntries(Object.entries(DEFAULT_SETTINGS).map(([key,defaultValue])=>{
                const input=form.elements.namedItem(key);
                return [key,typeof defaultValue==="boolean"?input.checked:typeof defaultValue==="number"?Number(input.value):input.value];
            }));
            localStorage.setItem("aimcoachSettings",JSON.stringify(validateSettings(value)));
            document.getElementById("settingsStatus").textContent="偏好已保存；当前实验不会改变。";
        } catch(error) {document.getElementById("settingsStatus").textContent=`设置未保存：${error.message}`;}
    });
}
export function applyVisualSettings(settings, crosshair, target, area) {
    crosshair.dataset.shape=settings.crosshair_type;
    crosshair.style.width=`${settings.crosshair_size}px`;crosshair.style.height=`${settings.crosshair_size}px`;
    crosshair.style.color=settings.crosshair_color;crosshair.style.borderColor=settings.crosshair_color;
    crosshair.style.borderWidth=`${settings.crosshair_thickness}px`;
    crosshair.style.setProperty?.("--crosshair-thickness",`${settings.crosshair_thickness}px`);
    target.style.background=settings.target_color;
    area.style.background={dark:"#111923",slate:"#202a34",midnight:"#0a1025"}[settings.background];
}
export function shotFeedback(hit,settings,area) {
    if((hit?settings.hit_feedback:settings.miss_feedback) && !window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches) {
        area.animate?.([{boxShadow:`inset 0 0 0 3px ${hit?"#8adab4":"#f49a9a"}`},{boxShadow:"inset 0 0 0 1px #25313d"}],{duration:120});
    }
    if(hit && settings.hit_sound) {
        // Audio is created only from a real shot gesture; unsupported browsers remain silent.
        try {const Audio=window.AudioContext||window.webkitAudioContext;if(!Audio)return;
            const context=new Audio(),osc=context.createOscillator(),gain=context.createGain();
            osc.frequency.value=660;gain.gain.value=.035;osc.connect(gain);gain.connect(context.destination);
            osc.start();osc.stop(context.currentTime+.05);osc.onended=()=>{void context.close();};
        } catch { /* Audio failure never changes hit detection or raw data. */ }
    }
}
