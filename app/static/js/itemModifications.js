export function modificationStat(item,key,base=0){
    let value=Number(base)||0;
    for(const record of item?.modifications||[]){
        if(!record?.ruleId)continue;
        const change=record.modifiers?.[key];if(change==null)continue;
        value=String(change).startsWith('=')?Number(String(change).slice(1)):value+Number(change);
    }
    return value;
}

export function wornModificationStat(data,key){
    return Object.values(data?.equipment||{}).reduce((sum,item)=>sum+(item&&!item.protectionDisabled?modificationStat(item,key):0),0);
}

export function modificationRule(item,key,fallback=0){
    const values=(item?.modifications||[]).filter(m=>m?.ruleId&&m.rules?.[key]!=null).map(m=>m.rules[key]);
    if(!values.length)return fallback;
    return ['ergonomics_tiers','burst_ap_delta','reload_ap_delta','aimed_accuracy','burst_accuracy_reduction'].includes(key)?values.reduce((a,b)=>a+b,0):Math.max(...values);
}

export function wornModificationRule(data,key){
    return Object.values(data?.equipment||{}).reduce((sum,item)=>sum+(item&&!item.protectionDisabled?modificationRule(item,key):0),0);
}

export function modifiedFireProfile(item,base,fireRate){
    const result={...base};
    if(modificationRule(item,'pistol_burst'))Object.assign(result,{supports_burst:true,supports_suppression:true,supports_area_fire:true,
        burst_size:Math.floor((Math.floor(fireRate/2)+1)*modificationRule(item,'pistol_burst_factor',1))});
    result.burst_size=Math.max(0,Math.floor(modificationStat(item,'burst_size',result.burst_size)));
    return result;
}
