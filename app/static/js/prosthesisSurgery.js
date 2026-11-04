import { rememberEquipmentChoice } from './equipmentChoices.js';

const labels={leftArm:'Левая рука',rightArm:'Правая рука',leftLeg:'Левая нога',rightLeg:'Правая нога'};

export async function renderProsthesisSurgery(container,data,doctorId,forcedPatientId,api){
    const available=[];
    const visit=(value,path)=>{
        if(Array.isArray(value))value.forEach((item,index)=>visit(item,[...path,index]));
        else if(value&&typeof value==='object'){
            if(['clamp','integrated'].includes(value.attributes?.prosthesis?.mount))available.push({item:value,path});
            for(const key of ['contents','pouches'])if(value[key])visit(value[key],[...path,key]);
        }
    };
    for(const [key,value] of Object.entries(data.inventory||{}))visit(value,['inventory',key]);
    for(const [key,value] of Object.entries(data.equipment||{}))visit(value,['equipment',key]);
    const form=document.createElement('form');form.className='prosthesis-surgery';
    form.style.cssText='display:grid;gap:8px;padding:12px 0;border-top:1px solid rgba(255,255,255,.15)';
    form.innerHTML=`<strong>Операция с протезом</strong>
        <label style="display:grid;gap:4px;">Пациент <select class="combat-dialog-select" name="patient"></select></label>
        <label style="display:grid;gap:4px;">Конечность и крепление <select class="combat-dialog-select" name="operation"></select></label>
        <small class="surgery-requirements"></small><button type="submit" class="btn btn-secondary">Начать операцию</button><small class="surgery-status"></small>`;
    const patients=form.elements.patient,operations=form.elements.operation,submit=form.querySelector('[type="submit"]');
    const status=form.querySelector('.surgery-status'),requirements=form.querySelector('.surgery-requirements');
    const candidates=api.candidates.filter(candidate=>!forcedPatientId||Number(candidate.character_id)===Number(forcedPatientId));
    if(!forcedPatientId&&!candidates.some(candidate=>Number(candidate.character_id)===Number(doctorId)))candidates.unshift({character_id:doctorId,name:'Себе'});
    for(const candidate of candidates)patients.add(new Option(Number(candidate.character_id)===Number(doctorId)?'Себе':candidate.name||'Персонаж',candidate.character_id));
    rememberEquipmentChoice(patients,`${doctorId}:surgery-patient`);
    let choices=[],patientData,version=0;
    const describe=()=>{
        const selected=choices[Number(operations.value)],rule=selected?.item.attributes?.prosthesis,installation=rule?.installation;
        requirements.textContent=installation?`${installation.duration_seconds/3600} ч · Медицина и Инженерия от ${installation.skill_minimum} · ${installation.surgical_uses} ${installation.surgical_uses===3?'заряда':'зарядов'} хирургического набора · Морфий: ${installation.morphine_ampoules} амп.${installation.emotion_suppressor_ampoules?` · Подавитель эмоций: ${installation.emotion_suppressor_ampoules} амп.`:''} При провале время и расходники теряются. Ампулы используются только для операции.`:'';
    };
    const load=async()=>{
        const current=++version;submit.disabled=true;operations.replaceChildren();choices=[];status.textContent='';
        try{
            patientData=await api.inspect(Number(patients.value));if(current!==version)return;
            const meta=patientData?.health?.combatMeta||{};
            if(data.health?.combatMeta?.prosthesisSurgeryId||meta.prosthesisSurgeryId||meta.prosthesisSurgeryPatientId){status.textContent='Врач или пациент уже участвует в операции.';return;}
            for(const [zone,label] of Object.entries(labels)){
                const parts=patientData?.health?.prostheses?.[zone]||{};
                if(parts.base?.attributes?.prosthesis?.mount==='clamp'&&!parts.hand)
                    choices.push({item:parts.base,zone,operation:'surgery_remove',label:`${label}: снять ${parts.base.name}`});
                const injury=(patientData?.health?.effects||[]).find(effect=>effect.type==='amputation'&&effect.area===zone&&effect.active!==false);
                for(const source of available){
                    const rule=source.item.attributes.prosthesis;
                    if(!parts.base&&injury?.loss_extent===rule.extent&&rule.kind===(zone.endsWith('Arm')?'arm':'leg')&&!source.item.broken)
                        choices.push({...source,zone,operation:'surgery_install',label:`${label}: установить ${source.item.name}`});
                }
            }
            choices.forEach((choice,index)=>operations.add(new Option(choice.label,index)));
            submit.disabled=!choices.length;describe();if(!choices.length)status.textContent='Нет подходящих протезов для установки или снятия. Расходники должны находиться у врача; протез для установки — тоже.';
        }catch(error){if(current===version)status.textContent=error.message||'Не удалось осмотреть пациента';}
    };
    patients.onchange=load;operations.onchange=describe;
    form.onsubmit=async event=>{
        event.preventDefault();const selected=choices[Number(operations.value)];if(!selected)return;
        submit.disabled=true;status.textContent='';
        try{
            const patientId=Number(patients.value),consent=await api.consent(patientId);
            if(consent&&!consent.allowed)return;
            await api.operate({operation:selected.operation,zone:selected.zone,path:selected.path,target_character_id:patientId,
                target_revision:patientData._revision,interaction_request_id:consent?.requestId,
                operation_id:`prosthesis-surgery-${doctorId}-${crypto.randomUUID()}`});
        }catch(error){status.textContent=error.message||'Не удалось начать операцию';}
        finally{submit.disabled=false;}
    };
    container.append(form);await load();
}
