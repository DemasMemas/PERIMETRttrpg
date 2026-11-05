import { categoryNames } from './characterTrade.js';
import { Server } from './api.js';
import { showNotification } from './utils.js';
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

export async function openNpcMerchant(lobbyId, characterId, onChanged = null) {
    const [character, catalog] = await Promise.all([Server.getCharacter(characterId), Server.getLobbyTemplates(lobbyId, '')]);
    const modal = document.createElement('div');
    modal.className = 'modal';modal.style.zIndex = '1330';
    renderMerchantEditor(modal, {character, templates:catalog.global || [], onClose:()=>modal.remove(),
        onSave:async payload=>{
            await Server.configureNpcMerchant(lobbyId, characterId, payload);
            modal.remove();
            if(onChanged)await onChanged();
            showNotification('Прайс торговца сохранён','success');
        }});
    document.body.appendChild(modal);
}

export function merchantPriceInput(value) {
    if (value == null || String(value).trim() === '') return null;
    const number = Number(value);
    if (!Number.isSafeInteger(number) || number < 0) throw Error('Цена должна быть целым неотрицательным числом рублей. Пустое поле отключает товар.');
    return number;
}

export function renderMerchantEditor(modal, {character, templates, onSave, onClose}) {
    const eligible = templates.filter(template => !['background','special_trait','modification'].includes(template.category)
        && !template.attributes?.not_for_sale && (template.id>0||template.category==='weapon_module'||[-32001,-32002,-32003].includes(template.id)));
    const prices = new Map(Object.entries(character.data?.merchant?.prices || {}).map(([key,value])=>[key,{...value}]));
    modal.innerHTML = `<div class="modal-content character-trade merchant-editor">
        <div class="character-trade-heading"><h3>Торговля и ремонт: ${escape(character.name)}</h3><button type="button" class="btn btn-secondary trade-close" aria-label="Закрыть">×</button></div>
        <label class="merchant-enable"><input type="checkbox" class="merchant-enabled" ${character.data?.merchant?.enabled ? 'checked' : ''}> НПС торгует по прайсу без отдельного согласия ГМа</label>
        <label class="merchant-enable"><input type="checkbox" class="merchant-repair-enabled" ${character.data?.merchant?.repair_enabled ? 'checked' : ''}> НПС оказывает платный ремонт</label>
        <label class="trade-money-label">Наценка механика <input type="number" min="1" max="10" step="0.01" class="form-control merchant-repair-markup" value="${Number(character.data?.merchant?.repair_markup||1)}"></label>
        <label class="trade-money-label">Деньги НПС <input type="number" min="0" step="1" class="form-control merchant-money" value="${Number(character.data?.inventory?.money||0)}"></label>
        <p>Цены за один экземпляр. Пустое поле — не продаёт или не выкупает. Износ, содержимое и установленные модули автоматически цену не меняют.</p>
        <div class="trade-filters"><input type="search" class="form-control merchant-search" placeholder="Поиск товара" aria-label="Поиск товара">
            <select class="form-control merchant-category" aria-label="Категория"><option value="">Все категории</option>${[...new Set(eligible.map(template=>template.category))]
                .sort((a,b)=>(categoryNames[a]||'Прочее').localeCompare(categoryNames[b]||'Прочее','ru'))
                .map(key=>`<option value="${escape(key)}">${escape(categoryNames[key]||'Прочее')}</option>`).join('')}</select></div>
        <div class="merchant-columns"><span>Товар</span><span>Продаёт за, ₽</span><span>Выкупает за, ₽</span></div>
        <div class="merchant-price-list"></div><div class="merchant-page-info"><small class="merchant-row-count"></small><button type="button" class="btn btn-sm merchant-more">Показать ещё</button></div><div class="trade-error" role="status"></div>
        <div class="form-actions"><button type="button" class="btn btn-secondary merchant-reference">Заполнить ценами каталога</button>
            <button type="button" class="btn btn-secondary merchant-clear">Очистить прайс</button>
            <button type="button" class="btn btn-primary merchant-save">Сохранить</button></div></div>`;
    let saving = false, visibleLimit = 100;
    const render = (reset=true) => {
        if(reset) visibleLimit=100;
        const query = modal.querySelector('.merchant-search').value.trim().toLocaleLowerCase('ru');
        const category = modal.querySelector('.merchant-category').value;
        const list = modal.querySelector('.merchant-price-list');
        const oldScroll=reset?0:list.scrollTop;
        const matching=eligible.filter(template=>!(query && !template.name.toLocaleLowerCase('ru').includes(query) || category && template.category!==category));
        list.replaceChildren();
        for (const template of matching.slice(0,visibleLimit)) {
            const key = String(template.id), price = prices.get(key) || {buy:null,sell:null};
            const row = document.createElement('div');row.className='merchant-price-row';row.dataset.templateId=key;
            row.innerHTML = `<span>${escape(template.name)}<small>${escape(categoryNames[template.category]||'Прочее')}</small></span>
                ${['sell','buy'].map(side=>`<input type="number" min="0" step="1" class="form-control merchant-price" data-side="${side}" value="${price[side]??''}" aria-label="${side==='sell'?'Цена продажи':'Цена выкупа'}: ${escape(template.name)}">`).join('')}`;
            row.querySelectorAll('input').forEach(input=>{input.oninput=()=>{
                const current=prices.get(key)||{buy:null,sell:null};
                current[input.dataset.side]=input.value;
                prices.set(key,current);
            };});
            list.appendChild(row);
        }
        if (!list.childElementCount) list.textContent='Ничего не найдено';
        list.scrollTop=oldScroll;
        modal.querySelector('.merchant-row-count').textContent=`Показано ${Math.min(visibleLimit,matching.length)} из ${matching.length}`;
        modal.querySelector('.merchant-more').hidden=visibleLimit>=matching.length;
    };
    modal.querySelector('.merchant-search').oninput=render;
    modal.querySelector('.merchant-category').onchange=render;
    modal.querySelector('.merchant-more').onclick=()=>{visibleLimit+=100;render(false);};
    modal.querySelector('.trade-close').onclick=()=>{if(!saving)onClose();};
    modal.querySelector('.merchant-reference').onclick=()=>{
        if(saving)return;
        for(const template of eligible) if(Number.isSafeInteger(template.price)&&template.price>0) prices.set(String(template.id),{buy:template.price,sell:template.price});
        render();
    };
    modal.querySelector('.merchant-clear').onclick=()=>{if(!saving){prices.clear();render();}};
    modal.querySelector('.merchant-save').onclick=async()=>{
        if(saving)return;
        try{
            const payload={};
            for(const template of eligible){
                const key=String(template.id), price=prices.get(key);
                if(!price)continue;
                const buy=merchantPriceInput(price.buy??''),sell=merchantPriceInput(price.sell??'');
                if(buy!==null||sell!==null)payload[key]={buy,sell};
            }
            const funds=merchantPriceInput(modal.querySelector('.merchant-money').value);
            if(funds===null)throw Error('Укажите запас денег НПС.');
            const repairMarkup=Number(modal.querySelector('.merchant-repair-markup').value);
            if(!Number.isFinite(repairMarkup)||repairMarkup<1||repairMarkup>10)throw Error('Наценка механика должна быть от 1 до 10.');
            saving=true;modal.querySelectorAll('button,input,select').forEach(element=>element.disabled=true);
            await onSave({enabled:modal.querySelector('.merchant-enabled').checked,prices:payload,money:funds,
                repair_enabled:modal.querySelector('.merchant-repair-enabled').checked,repair_markup:repairMarkup,
                character_revision:character.data?._revision??character.revision});
        }catch(error){
            saving=false;modal.querySelectorAll('button,input,select').forEach(element=>element.disabled=false);
            modal.querySelector('.trade-error').textContent=error.message||'Прайс не сохранён';
        }
    };
    render();
}
