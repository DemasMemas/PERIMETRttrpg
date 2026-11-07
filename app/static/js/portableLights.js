import { Server } from './api.js';
import { rememberEquipmentChoice } from './equipmentChoices.js';

export function usesNightVision(data) {
    const helmet=data?.equipment?.helmet;
    if(helmet?.protectionDisabled)return false;
    const modules=(helmet?.installedModules||[]).filter(module=>!module.protectionDisabled);
    const all=[...modules,...modules.filter(module=>module.slotType==='visor').flatMap(visor=>visor.installedModules||[])];
    return all.some(module=>!module.protectionDisabled&&(module.slotType==='nvg' || module.attributes?.night_vision)
        && module.active!==false && module.enabled!==false && !module.raised && (module.lightPower?.uses_left??10)>0);
}

export async function renderPortableLights(container,characterId,data,operate,isCurrent=()=>true,canEdit=true) {
    const result=await Server.getPortableLights(characterId);
    if (!isCurrent() || !result.lights.length) return;
    const root=document.createElement('section');root.className='loadout-group portable-lights';
    const heading=document.createElement('h4');heading.textContent='Приборы, свет и ПНВ';root.append(heading);
    const active=window.locationCombatState?.status==='active';
    const ownTurn=canEdit && (!active || window.locationCombatState?.current_character?.character_id===characterId);
    for (const light of result.lights) {
        const row=document.createElement('div');row.className='portable-light-row';
        row.dataset.lightId=light.id || JSON.stringify(light.path);
        const caption=document.createElement('strong');
        const mount={hand:'в руке',head:'на голове',taped:'на оружии',weapon:'модуль оружия'}[light.binding?.mount];
        caption.textContent=light.kind==='pda'
            ? `${light.name} · ${Number(light.pda_state?.charge_percent??100).toFixed(1)}% · ${light.pda_state?.on?'включён':'выключен'}`
            : light.kind==='glow'
            ? `${light.name} · ${light.activated?`${Math.ceil((light.remaining_seconds||0)/60)} мин осталось`:'не активирована'}${mount?` · ${mount}`:''}`
            : `${light.name} · ${light.uses_left}/10${mount?` · ${mount}`:''}${light.binding?` · ${light.binding.on?'включён':'выключен'}`:''}`;
        row.append(caption);
        const button=(text,operation,extra={},disabled=false)=>{
            const control=document.createElement('button');control.type='button';control.className='btn btn-sm btn-secondary';
            control.textContent=text;control.disabled=disabled || !ownTurn;
            control.dataset.lightOperation=operation;
            control.onclick=()=>operate({operation,path:light.path,source_id:light.id??null,...extra});row.append(control);
        };
        if(light.kind==='pda'){
            button(`${light.pda_state?.on?'Выключить':'Включить'}${active?' · 1 ОД':''}`,
                'pda_toggle',{},!light.pda_state?.on && (light.pda_state?.charge_percent??100)<=0);
            if(result.batteries.length){
                const choices=document.createElement('select');choices.className='form-control';
                choices.setAttribute('aria-label','Батарейки для КПК');
                result.batteries.filter(battery=>battery.name==='Батарейки').forEach(battery=>{
                    const option=document.createElement('option');option.value=JSON.stringify(battery.path);
                    option.textContent=battery.name;choices.append(option);
                });
                if(choices.options.length){
                    row.append(choices);
                    button(`Заменить батарейку${active?' · 3 ОД + достать':''}`,'pda_battery');
                    row.lastElementChild.onclick=()=>operate({operation:'pda_battery',path:light.path,
                        source_id:light.id??null,battery_path:JSON.parse(choices.value)});
                }
            }
            root.append(row);continue;
        }
        if (light.binding) {
            if(light.kind!=='glow')button(`${light.binding.on?'Выключить':'Включить'}${active?' · 1 ОД':''}`,'toggle',{},!light.binding.on && light.uses_left<=0);
            if (!['weapon','nvg'].includes(light.binding.mount)) button(`Убрать${active?' · 1 ОД':''}`,'stow');
        } else {
            if(light.kind==='glow'){
                if(!light.activated)button(`Активировать${active?' · 1 ОД + достать':''}`,'ignite');
                else if(light.remaining_seconds>0)button('Взять горящий источник','prepare',{mount:'hand'});
                root.append(row);continue;
            }
            const mount=light.kind==='head'||light.visor_mount?'head':light.kind==='module'?'weapon':'hand';
            button(mount==='head'?'Надеть':mount==='weapon'?'Подготовить модуль':'Взять в руку','prepare',
                {mount,...(mount==='weapon'?{weapon_index:light.path[1]}:{})});
            if (!light.visor_mount && !['head','module'].includes(light.kind) && (data.weapons || []).length) {
                const weapons=document.createElement('select');weapons.className='form-control';weapons.setAttribute('aria-label','Оружие для фонаря');
                (data.weapons || []).forEach((weapon,index)=>{
                    const option=document.createElement('option');option.value=index;option.textContent=weapon.name || weapon.model || `Оружие ${index+1}`;weapons.append(option);
                });
                rememberEquipmentChoice(weapons,`${characterId}:light-weapon:${row.dataset.lightId}`);
                row.append(weapons);
                const taped=document.createElement('button');taped.type='button';taped.className='btn btn-sm btn-secondary';
                taped.textContent=`Закрепить на оружии${active?' · 1 ОД + достать':''}`;taped.disabled=!ownTurn;taped.onclick=()=>operate({operation:'prepare',path:light.path,source_id:light.id??null,mount:'taped',weapon_index:Number(weapons.value)});row.append(taped);
            }
        }
        if (result.batteries.length && light.kind!=='glow') {
            const choices=document.createElement('select');choices.className='form-control';choices.setAttribute('aria-label','Батарейка для фонаря');
            result.batteries.forEach((battery,index)=>{const option=document.createElement('option');option.value=JSON.stringify(battery.path);option.textContent=battery.name;choices.append(option)});
            rememberEquipmentChoice(choices,`${characterId}:light-battery:${row.dataset.lightId}`);
            row.append(choices);
            const replace=document.createElement('button');replace.type='button';replace.className='btn btn-sm btn-secondary';replace.textContent=`Заменить батарейку${active?' · 1 ОД + достать':''}`;replace.disabled=!ownTurn;
            replace.onclick=()=>operate({operation:'battery',path:light.path,source_id:light.id??null,battery_path:JSON.parse(choices.value)});row.append(replace);
        }
        if (!canEdit) row.querySelectorAll('button,select').forEach(control=>{control.disabled=true});
        root.append(row);
    }
    container.append(root);
}

// Динамический свет не пересчитывает статическое поле сцены при каждом шаге.
export function createPortableLightRenderer(THREE,scene) {
    const lights=new Map();
    function dispose(entry) {
        scene.remove(entry.light);
        if(entry.light.target)scene.remove(entry.light.target);
        entry.light.shadow.map?.dispose();entry.light.shadow.mapPass?.dispose();
    }
    return {
        update(characters,models,groundSources=[]) {
            const wanted=[];
            for (const character of characters || []) {
                const modelEntry=models.get(character.character_id),model=modelEntry?.model;
                if (!model?.visible) continue;
                for (const source of character.portable_lights || []) wanted.push({source,character,model,modelEntry});
            }
            for(const source of groundSources||[])wanted.push({source,character:null,model:null,modelEntry:null});
            const selected=new Set(wanted.slice(0,4).map(({source})=>source.id));
            for (const [key,entry] of lights) if (!selected.has(key)) {dispose(entry);lights.delete(key)}
            for (const {source,character,model,modelEntry} of wanted.slice(0,4)) {
                let entry=lights.get(source.id);
                if (!entry) {
                    const light=source.radial?new THREE.PointLight('#dcf0aa',12,source.range||5,1)
                        :new THREE.SpotLight('#fff4d8',14,35,Math.atan(2.5/35),.4,1);
                    light.castShadow=true;light.shadow.mapSize.set(512,512);light.shadow.camera.near=.08;
                    light.shadow.bias=-.0002;light.shadow.normalBias=.015;light.shadow.autoUpdate=false;
                    scene.add(light);if(light.target)scene.add(light.target);
                    entry={light,source};lights.set(source.id,entry);
                }
                if(source.radial){
                    const x=model?model.position.x:source.x,y=model?model.position.y+1.1:source.y,z=model?model.position.z:source.z;
                    const signature=[x,y,z].join(':');
                    if(entry.signature!==signature){entry.signature=signature;entry.light.position.set(x,y,z);entry.light.shadow.needsUpdate=true;}
                    continue;
                }
                const fx=Number(modelEntry.facingX??character.facing_x)||0,fz=Number(modelEntry.facingY??character.facing_y)||0,length=Math.hypot(fx,fz)||1;
                const y=model.position.y+({standing:1.4,sitting:.95,prone:.3}[character.posture]||1.4);
                const signature=[model.position.x,y,model.position.z,fx,fz].join(':');
                if (entry.signature===signature) continue;
                entry.signature=signature;
                entry.light.position.set(model.position.x+.35*fx/length,y,model.position.z+.35*fz/length);
                entry.light.target.position.set(model.position.x+35.35*fx/length,model.position.y,model.position.z+35.35*fz/length);
                entry.light.target.updateMatrixWorld();entry.light.shadow.needsUpdate=true;
            }
        },
        dirty() { for (const {light} of lights.values()) light.shadow.needsUpdate=true; },
        dispose() { for (const entry of lights.values()) dispose(entry);lights.clear(); },
        get count(){return lights.size},
    };
}
