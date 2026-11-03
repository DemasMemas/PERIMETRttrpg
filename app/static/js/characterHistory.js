import {Server} from './api.js';
import {traitChoiceFields} from './characterCreation.js';

const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export async function completeCharacterHistory(lobbyId,characterId,data,onUpdated){
    if(document.getElementById('character-history-modal'))return;
    const rules=await Server.characterCreationRules(lobbyId),history=data.creation.zoneHistory;
    const events=history.events.filter(e=>e.teacher||e.hardening),modal=document.createElement('div');
    rules.traits=rules.traits.filter(trait=>trait.name!=='Специалист');
    modal.id='character-history-modal';modal.className='modal';modal.style.display='flex';modal.style.zIndex='11000';
    modal.innerHTML=`<div class="modal-content" style="max-width:760px;max-height:90vh;overflow:auto"><button type="button" class="close" aria-label="Закрыть">×</button><h3>Жизнь до игры: решения событий</h3><form>${events.map(e=>`<section data-month="${e.month}" style="margin:18px 0"><h4>Месяц ${e.month}: ${escape(e.title)}</h4><p>${e.details.map(escape).join('<br>')}</p>${e.teacher?`<label>${e.teacher.xp} XP без бонусов обучения<select data-teacher class="form-control">${rules.skillOrder.filter(p=>!e.teacher.group||p.startsWith(e.teacher.group+'.')).map(p=>`<option value="${p}">${escape(rules.skills[p.split('.')[0]][p.split('.')[1]])}</option>`).join('')}</select></label>`:`<label>Закалка: до ${e.hardening.limit} очков<select data-hardening class="form-control"><option value="points">Отказаться: +${Math.floor(e.hardening.limit/2)} очк. навыков</option>${rules.traits.filter(t=>t.cost<=e.hardening.limit).map(t=>`<option value="${t.id}">${escape(t.name)} (${t.cost})</option>`).join('')}</select></label><p data-effect></p><div data-trait-choices></div>`}</section>`).join('')}<p>Черта Закалки не занимает начальные три места. Ограничения самой черты сохраняются. Доход считается по навыкам, с которыми персонаж начал эту историю.</p><p data-error role="alert"></p><button type="submit" class="btn">Сохранить решения</button></form></div>`;
    document.body.appendChild(modal);let busy=false;
    modal.querySelector('.close').onclick=()=>{if(!busy)modal.remove()};
    modal.onchange=event=>{
        if(!event.target.hasAttribute('data-hardening'))return;
        const section=event.target.closest('[data-month]'),trait=rules.traits.find(t=>t.id===event.target.value);
        section.querySelector('[data-effect]').textContent=trait?.effect||'';
        section.querySelector('[data-trait-choices]').innerHTML=traitChoiceFields(rules,trait);
    };
    modal.querySelector('form').onsubmit=async event=>{
        event.preventDefault();if(busy)return;busy=true;
        const button=event.target.querySelector('[type=submit]');button.disabled=true;const choices={};
        for(const section of modal.querySelectorAll('[data-month]')){
            const teacher=section.querySelector('[data-teacher]'),hardening=section.querySelector('[data-hardening]');
            if(teacher)choices[section.dataset.month]={skill:teacher.value};
            else if(hardening.value==='points')choices[section.dataset.month]={decline:true};
            else{
                const trait={id:hardening.value};section.querySelectorAll('[data-choice]').forEach(el=>trait[el.dataset.choice]=el.value.trim());
                choices[section.dataset.month]={trait};
            }
        }
        try{const result=await Server.settleCharacterHistory(lobbyId,characterId,choices);modal.remove();await onUpdated(result.data);}
        catch(error){busy=false;button.disabled=false;modal.querySelector('[data-error]').textContent=error.message;}
    };
}
