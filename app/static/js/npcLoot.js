import { Server } from './api.js';
import { categoryNames } from './characterTrade.js';

const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const forbidden = new Set(['background', 'special_trait', 'modification']);
const typeNames = {scope:'Прицел',barrel:'Ствол',handguard:'Цевьё',filter:'Фильтр',nvg:'ПНВ',battery:'Батарея',
    ring:'Кольцо',bracelet:'Браслет',earrings:'Серьги',necklace:'Ожерелье'};

export function lootCandidates(templates, pool) {
    return templates.filter(template => template.category === pool.category && !template.attributes?.not_for_sale
        && (!pool.subcategory || template.subcategory === pool.subcategory)
        && (!pool.item_class || template.item_class === pool.item_class)
        && Number.isSafeInteger(template.price) && template.price >= pool.min_price && template.price <= pool.max_price);
}

export async function openNpcLoot(lobbyId, characterId, characters = [], onChanged = null) {
    const [character, catalog] = await Promise.all([Server.getCharacter(characterId), Server.getLobbyTemplates(lobbyId, '')]);
    const templates = (catalog.global || []).filter(template => template.id > 0 && !forbidden.has(template.category) && !template.attributes?.not_for_sale);
    const priceLimit = Math.max(0, ...templates.map(template => Number(template.price) || 0));
    const blankPool = () => ({category:'consumable', subcategory:'', item_class:'', min_price:0, max_price:priceLimit, draws:1, quantity:1, chance:100});
    let profile = structuredClone(character.data?.npcLoot?.profile || {name:'Добыча', budget:0, money_min:0, money_max:0, pools:[blankPool()]});
    let revision = character.data?._revision ?? character.revision;
    let dirty = !character.data?.npcLoot?.profile, busy = false, operation = crypto.randomUUID();
    const modal = document.createElement('div');
    modal.className = 'modal npc-loot-modal';modal.style.zIndex='1330';
    const profiles = characters.filter(entry => entry.id !== characterId && entry.data?.npcLoot?.profile);
    modal.innerHTML = `<div class="modal-content npc-loot-editor">
        <div class="character-trade-heading"><h3>Добыча: ${escape(character.name)}</h3><button type="button" class="btn btn-secondary trade-close" aria-label="Закрыть">×</button></div>
        <p>Каждый выбор — независимый случайный предмет из подходящих, с равными шансами. Бюджет ограничивает общую цену новых вещей. Деньги НПС задаются отдельно.</p>
        <p>Наполнение добавляет вещи к имеющимся. Оружие появится в разделе «Оружие», остальные вещи — в рюкзаке или неразмещённом снаряжении. Заряжать, надевать и раскладывать их нужно отдельно.</p>
        <div class="loot-profile-fields">
            <label>Название профиля<input class="form-control" data-profile="name" maxlength="100"></label>
            <label>Бюджет вещей, ₽<input class="form-control" data-profile="budget" type="number" min="0" step="1"></label>
            <label>Добавить денег от, ₽<input class="form-control" data-profile="money_min" type="number" min="0" step="1"></label>
            <label>До, ₽<input class="form-control" data-profile="money_max" type="number" min="0" step="1"></label>
        </div>
        ${profiles.length ? `<label>Взять настройки другого НПС<select class="form-control loot-profile-source"><option value="">Выберите профиль</option>${profiles.map(entry=>`<option value="${entry.id}">${escape(entry.name)} · ${escape(entry.data.npcLoot.profile.name)}</option>`).join('')}</select></label>` : ''}
        <div class="loot-pools"></div><button type="button" class="btn btn-secondary loot-add">Добавить пул</button>
        <div class="trade-error loot-error" role="status"></div><div class="loot-receipt" role="status"></div>
        <div class="form-actions"><button type="button" class="btn btn-secondary loot-save">Сохранить профиль</button><button type="button" class="btn btn-primary loot-generate">Добавить добычу</button></div>
    </div>`;
    document.body.appendChild(modal);
    const update = () => {
        modal.querySelector('.loot-generate').disabled = busy || dirty;
        modal.querySelector('.loot-save').disabled = busy;
        modal.querySelector('.loot-add').disabled = busy || profile.pools.length>=30;
        modal.querySelector('.loot-error').textContent = dirty ? 'Сначала сохраните изменения профиля.' : '';
    };
    const changed = () => {dirty=true;update();};
    const options = (values, selected, allLabel) => `${allLabel?`<option value="">${allLabel}</option>`:''}${[...new Set(values)].filter(Boolean).sort((a,b)=>String(a).localeCompare(String(b),'ru')).map(value=>`<option value="${escape(value)}" ${value===selected?'selected':''}>${escape(typeNames[value]||value)}</option>`).join('')}`;
    const render = () => {
        for(const input of modal.querySelectorAll('[data-profile]')) {
            const key=input.dataset.profile;input.value=profile[key];
            input.oninput=()=>{profile[key]=key==='name'?input.value:Number(input.value);changed();};
        }
        const list = modal.querySelector('.loot-pools');list.replaceChildren();
        profile.pools.forEach((pool,index)=>{
            const row=document.createElement('section');row.className='loot-pool';
            const sameCategory=templates.filter(template=>template.category===pool.category);
            row.innerHTML=`<div class="loot-pool-heading"><strong>Пул ${index+1}</strong><button type="button" class="btn btn-sm btn-secondary loot-remove" aria-label="Удалить пул ${index+1}">Убрать</button></div>
                <div class="loot-pool-filters"><label>Категория<select class="form-control" data-pool="category">${[...new Set(templates.map(t=>t.category))].sort((a,b)=>(categoryNames[a]||a).localeCompare(categoryNames[b]||b,'ru')).map(key=>`<option value="${escape(key)}" ${key===pool.category?'selected':''}>${escape(categoryNames[key]||'Прочее')}</option>`).join('')}</select></label>
                <label>Тип<select class="form-control" data-pool="subcategory">${options(sameCategory.map(t=>t.subcategory),pool.subcategory,'Все типы')}</select></label>
                <label>Класс<select class="form-control" data-pool="item_class">${options(sameCategory.map(t=>t.item_class),pool.item_class,'Все классы')}</select></label></div>
                <div class="loot-pool-numbers">${[['min_price','Цена от, ₽',0,null],['max_price','До, ₽',0,null],['draws','Выборов',1,50],['quantity','В стопке',1,1000],['chance','Шанс каждого, %',0,100]].map(([key,label,min,max])=>`<label>${label}<input class="form-control" data-pool="${key}" type="number" min="${min}" ${max===null?'':`max="${max}"`} step="1" value="${pool[key]}"></label>`).join('')}</div>
                <small class="loot-match-count"></small>`;
            const count=()=>{row.querySelector('.loot-match-count').textContent=`Подходящих предметов: ${lootCandidates(templates,pool).length}`;};count();
            row.querySelectorAll('[data-pool]').forEach(input=>{input[input.tagName==='SELECT'?'onchange':'oninput']=()=>{
                const key=input.dataset.pool;pool[key]=input.tagName==='SELECT'?input.value:Number(input.value);
                if(key==='category'){pool.subcategory='';pool.item_class='';render();}else count();changed();
            };});
            row.querySelector('.loot-remove').onclick=()=>{profile.pools.splice(index,1);render();changed();};
            list.appendChild(row);
        });
        update();
    };
    modal.querySelector('.trade-close').onclick=()=>{if(!busy)modal.remove();};
    modal.querySelector('.loot-profile-source')?.addEventListener('change',event=>{
        const source=profiles.find(entry=>String(entry.id)===event.target.value);
        if(source){profile=structuredClone(source.data.npcLoot.profile);render();changed();}
    });
    modal.querySelector('.loot-add').onclick=()=>{profile.pools.push(blankPool());render();changed();};
    const run=async generate=>{
        if(busy||generate&&dirty)return;
        busy=true;modal.querySelectorAll('button,input,select').forEach(element=>element.disabled=true);
        try{
            const result=await Server.npcLootPool(lobbyId,characterId,{character_revision:revision,...(generate?{operation_id:operation}:{profile})},generate);
            revision=result.data._revision;
            if(!generate){profile=structuredClone(result.data.npcLoot.profile);dirty=false;}
            else{
                operation=crypto.randomUUID();
                const receipt=result.receipt;
                modal.querySelector('.loot-receipt').textContent=`Добавлено: ${receipt.items.map(item=>`${item.name} ×${item.quantity}`).join(', ')||'без вещей'}. Цена вещей: ${receipt.spent.toLocaleString('ru-RU')} / ${receipt.budget.toLocaleString('ru-RU')} ₽. Деньги: +${receipt.money.toLocaleString('ru-RU')} ₽.${receipt.skipped_pools.length?` Не хватило бюджета для пулов: ${receipt.skipped_pools.join(', ')}.`:''}`;
            }
            busy=false;modal.querySelectorAll('button,input,select').forEach(element=>element.disabled=false);render();
            if(onChanged) await onChanged();
        }catch(error){
            busy=false;modal.querySelectorAll('button,input,select').forEach(element=>element.disabled=false);update();
            modal.querySelector('.loot-error').textContent=error.message||'Не удалось обновить добычу';
        }
    };
    modal.querySelector('.loot-save').onclick=()=>run(false);
    modal.querySelector('.loot-generate').onclick=()=>run(true);
    render();
}
