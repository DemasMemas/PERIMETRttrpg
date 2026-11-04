import { rememberEquipmentChoice } from './equipmentChoices.js';

export function prostheticLimb(data,zone) {
    if (!(data.health?.effects || []).some(e=>e.type==='amputation'&&e.area===zone&&e.active!==false)) return null;
    const parts=data.health?.prostheses?.[zone] || {},base=parts.base,hand=parts.hand;
    const usable=item=>item?.attributes?.prosthesis && !item.broken && Number(item.durability)>0
        && (!(item.attributes.prosthesis.batteries || item.attributes.prosthesis.artifact_class) || item.energy_seconds>0);
    if (!usable(base) || base.attributes.prosthesis.provides_base && !usable(hand)) return null;
    const items=base.attributes.prosthesis.provides_base?[base,hand]:[base];
    return {efficiency:items.reduce((sum,item)=>sum+item.attributes.prosthesis.efficiency-(item.missed_lubrications||0),0)};
}

export function renderProstheses(container,data,operate,canEdit,characterId) {
    const inventory=[];
    const visit=(value,path)=>{
        if (Array.isArray(value)) value.forEach((item,index)=>visit(item,[...path,index]));
        else if(value && typeof value==='object') {
            if(value.name)inventory.push({item:value,path});
            for(const key of ['contents','pouches'])if(value[key])visit(value[key],[...path,key]);
        }
    };
    for(const [key,items] of Object.entries(data.inventory || {}))visit(items,['inventory',key]);
    for(const [key,item] of Object.entries(data.equipment || {}))visit(item,['equipment',key]);
    const available=inventory.filter(v=>v.item.attributes?.prosthesis);
    const entries=Object.entries(data.health?.prostheses || {}).flatMap(([zone,parts])=>Object.entries(parts).map(([slot,item])=>({zone,slot,item})));
    const meta=data.health?.combatMeta || {};
    if(!available.length && !entries.length && !meta.prosthesisSurgeryId && !meta.prosthesisSurgeryPatientId && !meta.prosthesisSurgeryResult)return;
    const section=document.createElement('section');section.className='loadout-group prostheses';
    const heading=document.createElement('h4');heading.textContent='Протезы';section.append(heading);
    if(meta.prosthesisSurgeryId || meta.prosthesisSurgeryPatientId){
        const status=document.createElement('p');status.textContent='Идёт операция установки протеза. Окончание определяется игровым временем.';section.append(status);
        const cancel=document.createElement('button');cancel.type='button';cancel.className='btn btn-sm btn-secondary';cancel.textContent='Прервать операцию';cancel.disabled=!canEdit;
        cancel.onclick=()=>{if(window.confirm('Прервать операцию? Расходники уже потрачены.'))operate({operation:'cancel_surgery'})};section.append(cancel);
    }else if(meta.prosthesisSurgeryResult){
        const result=meta.prosthesisSurgeryResult,status=document.createElement('p');
        status.textContent=`Последняя операция: ${result.success?'успех':result.reason?'прервана':'провал'}${result.roll!=null?` · d20: ${result.roll}, СЛ ${result.difficulty}`:''}. Расходники потрачены.`;section.append(status);
    }
    const active=window.locationCombatState?.status==='active';
    const disabled=!canEdit || active && window.locationCombatState.current_character?.character_id!==characterId;
    const button=(row,text,payload,blocked=false)=>{
        const b=document.createElement('button');b.type='button';b.className='btn btn-sm btn-secondary';
        b.textContent=text;b.disabled=disabled || blocked;b.onclick=()=>operate(payload);row.append(b);
    };
    const labels={leftArm:'Левая рука',rightArm:'Правая рука',leftLeg:'Левая нога',rightLeg:'Правая нога'};
    for(const {zone,slot,item} of entries) {
        const row=document.createElement('div');row.className='prosthesis-row';
        const p=item.attributes?.prosthesis || {},text=document.createElement('p');
        const efficiency=(p.efficiency||0)-(item.missed_lubrications||0);
        text.textContent=`${labels[zone]} · ${item.name} · прочность ${item.durability}/${p.durability} · эффективность ${efficiency}${item.broken?' · сломан':p.batteries||p.artifact_class?` · питание ${Math.ceil((item.energy_seconds||0)/60)} мин`:''}${item.lubrication_remaining?` · смазка: ещё ${Math.ceil(item.lubrication_remaining/60)} мин`:''}`;
        row.append(text);
        if(['strap','base'].includes(p.mount) && (slot==='hand'||!data.health.prostheses[zone].hand))
            button(row,`Снять${active?` · ${slot==='hand'?5:3} ОД`:''}`,{operation:'remove',zone});
        const supplies=inventory.filter(({item:source})=>/батаре|набор смазочных приспособлений/i.test(source.name)||source.category==='artifact');
        if(supplies.length){
            const select=document.createElement('select');select.className='form-control';select.disabled=disabled||active;
            supplies.forEach(({item:source,path},index)=>{const o=document.createElement('option');o.value=source.id || JSON.stringify(path);o.textContent=source.name;select.append(o)});
            rememberEquipmentChoice(select,`${characterId}:prosthesis:${zone}:${slot}`);row.append(select);
            const b=document.createElement('button');b.type='button';b.className='btn btn-sm btn-secondary';b.textContent='Использовать для протеза';b.disabled=disabled||active||item.broken;
            b.onclick=()=>{const source=supplies.find(({item,path})=>(item.id||JSON.stringify(path))===select.value);if(source)operate({operation:/набор смазочных/i.test(source.item.name)?'lubricate':'power',zone,slot,path:source.path})};row.append(b);
        }
        section.append(row);
    }
    for(const {item,path} of available){
        const row=document.createElement('div'),caption=document.createElement('span');caption.textContent=item.name+' ';row.append(caption);
        const rule=item.attributes.prosthesis, surgical=['clamp','integrated'].includes(rule.mount);
        const select=document.createElement('select');select.className='form-control';select.disabled=disabled;
        for(const [zone,label] of Object.entries(labels)){const o=document.createElement('option');o.value=zone;o.textContent=label;select.append(o)}if(!surgical)row.append(select);
        rememberEquipmentChoice(select,`${characterId}:prosthesis-target:${item.id||JSON.stringify(path)}`);
        const b=document.createElement('button');b.type='button';b.className='btn btn-sm btn-secondary';b.disabled=disabled||surgical&&active||Boolean(meta.prosthesisSurgeryId||meta.prosthesisSurgeryPatientId);
        b.textContent=surgical?`Операция · ${rule.installation.duration_seconds/3600} ч`:`Установить${active?` · ${rule.mount==='base'?5:10} ОД`:''}`;
        b.onclick=()=>surgical?window.openProsthesisSurgery(characterId):
            operate({operation:'install',zone:select.value,path});row.append(b);section.append(row);
    }
    container.append(section);
}
