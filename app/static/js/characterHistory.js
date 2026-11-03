import {Server} from './api.js';
import {traitChoiceFields} from './characterCreation.js';
import {ruleText} from './characterTraits.js';

const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export async function completeCharacterHistory(lobbyId,characterId,initialData,onUpdated=()=>{}){
    if(document.getElementById('character-history-modal'))return initialData;
    let data=initialData;
    if(!data.creation?.zoneHistory||data.creation.zoneHistory.settled){await onUpdated(data);return data;}
    const rules=await Server.characterCreationRules(lobbyId);
    rules.traits=rules.traits.filter(trait=>trait.name!=='Специалист');
    const modal=document.createElement('div');modal.id='character-history-modal';modal.className='modal';
    modal.style.cssText='display:flex;z-index:11000';modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');
    modal.setAttribute('aria-labelledby','history-title');
    modal.innerHTML='<div class="modal-content" style="max-width:760px;max-height:90vh;overflow:auto"><h3 id="history-title">Ваш путь в Зоне</h3><div data-history-progress style="display:flex;gap:5px;margin:16px 0"></div><form><section data-history-month aria-live="polite" style="min-height:220px"></section><p data-error role="alert"></p><div style="display:flex;gap:12px;justify-content:space-between"><button type="button" class="btn btn-secondary history-later">Продолжить позже</button><button type="submit" class="btn btn-primary">Подтвердить месяц</button></div></form></div>';
    const form=modal.querySelector('form'),content=modal.querySelector('[data-history-month]'),button=form.querySelector('[type=submit]');
    let busy=false;
    const render=()=>{
        const history=data.creation.zoneHistory,index=Number(history.confirmedThrough||0),event=history.events[index];
        modal.querySelector('[data-history-progress]').innerHTML=history.events.map((item,i)=>'<span aria-label="Месяц '+item.month+(i<index?' подтверждён':i===index?' текущий':'')+'" style="flex:1;height:5px;border-radius:3px;background:'+(i<index?'#758c6c':i===index?'#bcb587':'rgba(255,255,255,.14)')+'"></span>').join('');
        content.dataset.month=event.month;
        content.innerHTML='<p class="creation-hint">Месяц '+event.month+' из '+history.events.length+'</p><h4>'+escape(ruleText(event.title||'Жизнь в Зоне'))+'</h4>'+(event.details||[]).map(text=>'<p>'+escape(ruleText(text))+'</p>').join('')+'<p>Доход за месяц: '+Number((event.incomeOptions||[0])[0]).toLocaleString('ru-RU')+' ₽</p>';
        if(event.teacher){
            content.insertAdjacentHTML('beforeend','<label>Учитель · '+event.teacher.xp+' опыта без бонусов обучения<select data-teacher class="form-control" required><option value="">Выберите навык</option>'+rules.skillOrder.filter(path=>!event.teacher.group||path.startsWith(event.teacher.group+'.')).map(path=>'<option value="'+escape(path)+'">'+escape(rules.skills[path.split('.')[0]][path.split('.')[1]])+'</option>').join('')+'</select></label>');
        }else if(event.hardening){
            const count=trait=>(data.features?.specialTraits||[]).filter(item=>item.ruleId===trait.id||item.name===trait.name).length;
            content.insertAdjacentHTML('beforeend','<label>Закалка · черта до '+event.hardening.limit+' очков<select data-hardening class="form-control" required><option value="">Выберите решение</option><option value="points">Отказаться от черты: +'+Math.floor(event.hardening.limit/2)+' очк. навыков</option>'+rules.traits.filter(trait=>trait.cost<=event.hardening.limit).map(trait=>'<option value="'+escape(trait.id)+'" '+(trait.limitText&&count(trait)>=trait.maxCount?'disabled':'')+'>'+escape(ruleText(trait.name))+' · '+trait.cost+'</option>').join('')+'</select></label><p data-effect></p><div data-trait-choices></div>');
        }
        button.textContent=index===history.events.length-1?'Завершить историю':'Подтвердить месяц';
        modal.querySelector('[data-error]').textContent='';
        if(!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches)content.animate?.([{opacity:0,transform:'translateY(8px)'},{opacity:1,transform:'translateY(0)'}],{duration:280,easing:'ease-out'});
    };
    document.body.appendChild(modal);render();
    return await new Promise(resolve=>{
        const lifetime=new AbortController();
        const close=async()=>{if(busy)return;lifetime.abort();modal.remove();await onUpdated(data);resolve(data);};
        modal.querySelector('.history-later').onclick=close;
        document.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();void close();}},{capture:true,signal:lifetime.signal});
        form.onchange=event=>{
            if(!event.target.hasAttribute('data-hardening'))return;
            const trait=rules.traits.find(item=>item.id===event.target.value);
            content.querySelector('[data-effect]').textContent=ruleText(trait?.effect);
            const specialized=rules.skillOrder.filter(path=>{const [group,key]=path.split('.');return data.skills?.[group]?.[key]?.specialized;});
            content.querySelector('[data-trait-choices]').innerHTML=traitChoiceFields(rules,trait,{specialized});
        };
        form.onsubmit=async event=>{
            event.preventDefault();if(busy)return;
            const teacher=content.querySelector('[data-teacher]'),hardening=content.querySelector('[data-hardening]');let choice={};
            if(teacher)choice={skill:teacher.value};
            else if(hardening){
                if(hardening.value==='points')choice={decline:true};
                else{const trait={id:hardening.value};content.querySelectorAll('[data-choice]').forEach(input=>trait[input.dataset.choice]=input.value.trim());choice={trait};}
            }
            busy=true;button.disabled=true;
            try{
                const result=await Server.confirmCharacterHistoryMonth(lobbyId,characterId,Number(content.dataset.month),choice);data=result.data;
                busy=false;button.disabled=false;
                if(data.creation.zoneHistory.settled){await close();return;}
                render();
            }catch(error){busy=false;button.disabled=false;modal.querySelector('[data-error]').textContent=error.message;}
        };
    });
}
