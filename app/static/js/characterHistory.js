import {Server} from './api.js';
import {traitChoiceFields} from './characterCreation.js';
import {ruleText} from './characterTraits.js';

const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const historyText=value=>ruleText(value).replace(/^\d+\.\s*/u,'');
const rubles=value=>Number(value).toLocaleString('ru-RU');

export function historyMonthPresentation(event,income){
    const delta=Number(event.cashDelta||0);
    const options=event.incomeOptions?.length ? event.incomeOptions : [0];
    const total=Number(options[0]);
    const original=Number((income?.options||[])[0]);
    const base=Number.isFinite(original)?original*Number(event.incomeMultiplier??1):total-delta;
    const details=(event.details||[]).map(historyText).filter(text=>{
        if(!text || /^(?:болезнь|травма|зависимость)\.\s*бросьте\s+1\s*[кkdд]\s*4\s+для определения вида\.?$/iu.test(text))return false;
        const amount=text.replace(/\s/g,'');
        return !(delta&&/^\+?\d+$/u.test(amount)&&Number(amount)===delta);
    });
    const incomeText=delta && options.length===1
        ? `${rubles(base)} ₽ + ${rubles(delta)} ₽ = ${rubles(total)} ₽`
        : options.map(amount=>`${rubles(amount)} ₽`).join(' или ');
    return {title:historyText(event.title||'Жизнь в Зоне'),details,incomeText};
}

export function renderHistoryMonth(event,income,monthCount){
    const view=historyMonthPresentation(event,income);
    const debt=event.debt;
    const debtRows=debt ? [
        ['Группировка-кредитор',debt.creditor||'Выберите нейтральную группировку'],
        ['Сумма долга',`${rubles(debt.amount)} ₽`],
        ['Списано при создании',`${rubles(debt.paidAtCreation??0)} ₽`],
        ['Остаток долга',`${rubles(debt.remaining??debt.amount)} ₽`],
    ] : [];
    return `<div class="zone-history-month">
        <header class="zone-history-heading">
            <p>Месяц ${Number(event.month)}${monthCount ? ` из ${Number(monthCount)}` : ''}</p>
            <h4>${escape(view.title)}</h4>
        </header>
        ${view.details.length ? `<div class="zone-history-details">${view.details.map(text=>`<p>${escape(text)}</p>`).join('')}</div>` : ''}
        ${debt ? `<dl class="zone-history-debt" data-history-debt>${debtRows.map(([label,value])=>
            `<div><dt>${label}</dt><dd>${escape(value)}</dd></div>`).join('')}</dl>` : ''}
        <p class="zone-history-income"><span>Доход за месяц</span><strong>${escape(view.incomeText)}</strong></p>
    </div>`;
}

export async function completeCharacterHistory(lobbyId,characterId,initialData,onUpdated=()=>{}){
    if(document.getElementById('character-history-modal'))return initialData;
    let data=initialData;
    if(!data.creation?.zoneHistory||data.creation.zoneHistory.settled){await onUpdated(data);return data;}
    const rules=await Server.characterCreationRules(lobbyId);
    rules.traits=rules.traits.filter(trait=>trait.name!=='Специалист');
    const modal=document.createElement('div');modal.id='character-history-modal';modal.className='modal';
    modal.style.cssText='display:flex;z-index:11000';modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');
    modal.setAttribute('aria-labelledby','history-title');
    modal.innerHTML='<div class="modal-content character-history-dialog"><h3 id="history-title">Ваш путь в Зоне</h3><div data-history-progress style="display:flex;gap:5px;margin:16px 0"></div><form><section data-history-month aria-live="polite"></section><p data-error role="alert"></p><div class="zone-history-actions"><button type="button" class="btn btn-secondary history-later">Продолжить позже</button><button type="submit" class="btn btn-primary">Подтвердить месяц</button></div></form></div>';
    const form=modal.querySelector('form'),content=modal.querySelector('[data-history-month]'),button=form.querySelector('[type=submit]');
    let busy=false;
    const render=()=>{
        const history=data.creation.zoneHistory,index=Number(history.confirmedThrough||0),event=history.events[index];
        modal.querySelector('[data-history-progress]').innerHTML=history.events.map((item,i)=>'<span aria-label="Месяц '+item.month+(i<index?' подтверждён':i===index?' текущий':'')+'" style="flex:1;height:5px;border-radius:3px;background:'+(i<index?'#758c6c':i===index?'#bcb587':'rgba(255,255,255,.14)')+'"></span>').join('');
        content.dataset.month=event.month;
        content.innerHTML=renderHistoryMonth(event,history.income,history.events.length);
        if(event.debt?.chooseCreditor){
            content.insertAdjacentHTML('beforeend','<label>Нейтральная группировка<select data-debt-creditor class="form-control" required><option value="">Выберите группировку</option>'
                +(rules.debtCreditors||[]).map(group=>'<option value="'+escape(group.id)+'">'+escape(group.name)+'</option>').join('')+'</select></label>');
        }else if(event.teacher){
            content.insertAdjacentHTML('beforeend','<label>Учитель · '+event.teacher.xp+' опыта без бонусов обучения<select data-teacher class="form-control" required><option value="">Выберите навык</option>'+rules.skillOrder.filter(path=>!event.teacher.group||path.startsWith(event.teacher.group+'.')).map(path=>'<option value="'+escape(path)+'">'+escape(rules.skills[path.split('.')[0]][path.split('.')[1]])+'</option>').join('')+'</select></label>');
        }else if(event.hardening){
            const count=trait=>(data.features?.specialTraits||[]).filter(item=>item.ruleId===trait.id||item.name===trait.name).length;
            content.insertAdjacentHTML('beforeend','<label>Закалка · черта до '+event.hardening.limit+' очков<select data-hardening class="form-control" required><option value="">Выберите решение</option><option value="points">Отказаться от черты: +'+Math.floor(event.hardening.limit/2)+' очк. навыков</option>'+rules.traits.filter(trait=>trait.cost>0&&trait.cost<=event.hardening.limit).map(trait=>'<option value="'+escape(trait.id)+'" '+(trait.limitText&&count(trait)>=trait.maxCount?'disabled':'')+'>'+escape(ruleText(trait.name))+' · '+trait.cost+'</option>').join('')+'</select></label><p data-effect></p><div data-trait-choices></div>');
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
            const creditor=content.querySelector('[data-debt-creditor]');
            if(creditor)choice={creditor:creditor.value};
            else if(teacher)choice={skill:teacher.value};
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
