const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
})[char]);
const rubles = value => `${Number(value).toLocaleString('ru-RU')} ₽`;
const tradeNotices = new Set();

export function tradeResultNotice(result) {
    const key = `${result.id}:${result.status}`;
    const messages = {completed:result.payload?._merchant ? 'Сделка завершена' : 'Обмен завершён',
        pending:'Предложение обмена отправлено',rejected:'Предложение обмена отклонено',cancelled:'Предложение обмена отменено'};
    if (!messages[result.status] || tradeNotices.has(key)) return null;
    if (result.status === 'pending' && ['completed','rejected','cancelled'].some(status=>tradeNotices.has(`${result.id}:${status}`))) return null;
    tradeNotices.add(key);
    if (tradeNotices.size>500) tradeNotices.delete(tradeNotices.values().next().value);
    return {message:messages[result.status],type:result.status==='completed'?'success':'system'};
}
export const categoryNames = {weapon:'Оружие',melee_weapon:'Ближний бой',armor:'Броня',helmet:'Шлемы',
    gas_mask:'Противогазы',ammo:'Патроны',magazine:'Магазины',grenade:'Гранаты',
    consumable:'Расходники',tool:'Инструменты',crafting_material:'Материалы',
    container:'Контейнеры',backpack:'Рюкзаки',vest:'Разгрузки',pouch:'Подсумки',
    weapon_module:'Оружейные модули',headphones:'Наушники',glasses:'Очки',gloves:'Перчатки',
    jewelry:'Бижутерия',device:'Приборы',detector:'Детекторы',artifact:'Артефакты',
    misc:'Прочее',belt:'Пояса',armor_plate:'Бронеплиты',exoskeleton_module:'Модули экзоскелета'};

export function tradeEntryKey(entry) {
    return entry.item?.id != null ? `id:${entry.item.id}` : `path:${JSON.stringify(entry.path)}`;
}

export function tradeOffer(entries, selected) {
    return entries.filter(entry => selected.has(tradeEntryKey(entry))).map(entry => ({
        item_id: entry.item?.id ?? null, path: [...entry.path], name: entry.item?.name || 'Предмет',
        amount: selected.get(tradeEntryKey(entry)),
    }));
}

export function merchantTotals(entries, selected, prices) {
    const total = (side, field) => entries[side].reduce((sum, entry) => sum
        + (selected[side].get(tradeEntryKey(entry)) || 0) * merchantUnitPrice(entry.item, prices, field), 0);
    const sale = total('actor', 'buy'), purchase = total('target', 'sell');
    return {sale, purchase, actorMoney:Math.max(0, purchase-sale), targetMoney:Math.max(0, sale-purchase)};
}

export function merchantUnitPrice(item, prices, side) {
    const price = Number(prices[String(item?.templateId)]?.[side] || 0);
    if (item?.category !== 'tool') return price;
    const maximum = Number(item.maxUses ?? item.attributes?.max_uses ?? item.attributes?.uses);
    if (!Number.isSafeInteger(maximum) || maximum <= 0) return price;
    const remaining = Number(item.uses ?? item.attributes?.uses_remaining ?? maximum);
    return Number.isSafeInteger(remaining) && remaining >= 0 && remaining <= maximum
        ? Math.floor(price * remaining / maximum) : price;
}

export function validateTrade({actorOffer, targetOffer, actorMoney, targetMoney, actorBalance, targetBalance}) {
    for (const [value, available] of [[actorMoney, actorBalance], [targetMoney, targetBalance]]) {
        if (!Number.isSafeInteger(value) || value < 0) return 'Укажите целое неотрицательное число рублей.';
        if (value > available) return 'В предложении указано больше денег, чем есть у персонажа.';
    }
    if (!Number.isSafeInteger(actorBalance - actorMoney + targetMoney)
        || !Number.isSafeInteger(targetBalance - targetMoney + actorMoney)) return 'Слишком большая сумма.';
    for (const offer of [actorOffer, targetOffer]) {
        for (let index = 0; index < offer.length; index++) {
            const entry = offer[index];
            if (!Number.isSafeInteger(entry.amount) || entry.amount < 1) return 'Количество должно быть положительным целым числом.';
            if (offer.some((other, otherIndex) => otherIndex !== index && other.path.length <= entry.path.length
                && other.path.every((part, partIndex) => part === entry.path[partIndex]))) {
                return 'Выберите контейнер целиком или вещи внутри него.';
            }
        }
    }
    if (!actorOffer.length && !targetOffer.length && !actorMoney && !targetMoney) return 'Выберите вещи или деньги для передачи.';
    return '';
}

export function renderTradeEditor(modal, {actor, target, actorEntries, targetEntries, quantity, onSubmit, onClose,
                                        merchant=null, onConfigure=null, onRepair=null, onModification=null}) {
    const shop = merchant?.enabled === true;
    const prices = merchant?.prices || {};
    const selected = {actor: new Map(), target: new Map()};
    const balances = {actor: Number(actor.data?.inventory?.money || 0), target: Number(target.data?.inventory?.money || 0)};
    const allowed = (entry, side) => !entry.item?.createdByPlayer && prices[String(entry.item?.templateId)]?.[side] != null;
    const entries = {actor: shop ? actorEntries.filter(entry=>allowed(entry,'buy')) : actorEntries,
        target: shop ? targetEntries.filter(entry=>entry.path[0]==='inventory'&&allowed(entry,'sell')) : targetEntries};
    modal.innerHTML = `<div class="modal-content character-trade">
        <div class="character-trade-heading"><div><h3>${shop ? 'Торговля' : 'Обмен'}</h3><small>${shop ? 'В бою — 2 ОД. Сделка выполняется сразу.' : 'В бою — 2 ОД после согласия'}</small></div>
        <button type="button" class="btn btn-secondary trade-close" aria-label="Закрыть">×</button></div>
        <div class="character-trade-sides">${[['actor', actor], ['target', target]].map(([side, character]) => `
            <section class="character-trade-side" data-trade-side="${side}">
                <h4>${escape(character.name)}</h4><div>Доступно: ${rubles(balances[side])}</div>
                <label class="trade-money-label">${shop ? (side === 'actor' ? 'Доплатить торговцу' : 'Получить от торговца') : (side === 'actor' ? 'Передать деньги' : 'Запросить деньги')}
                    <span><input type="number" min="0" max="${balances[side]}" step="1" value="0" class="form-control trade-money" ${shop ? 'readonly' : ''} aria-label="${side === 'actor' ? 'Передать рублей' : 'Запросить рублей'}"> ₽</span></label>
                <small class="trade-balance"></small>
                <div class="trade-filters"><input type="search" class="form-control trade-search" placeholder="Поиск по названию" aria-label="Найти предмет">
                    <select class="form-control trade-category" aria-label="Категория"><option value="">Все категории</option>${[...new Set(entries[side].map(entry => entry.item?.category || 'misc'))]
                        .sort((a,b) => (categoryNames[a] || 'Прочее').localeCompare(categoryNames[b] || 'Прочее','ru'))
                        .map(key => `<option value="${escape(key)}">${escape(categoryNames[key] || 'Прочее')}</option>`).join('')}</select></div>
                <div class="trade-item-list"></div>
            </section>`).join('')}</div>
        <div class="trade-summary" aria-live="polite"></div><div class="trade-error" role="status"></div>
        <div class="form-actions">${onConfigure ? '<button type="button" class="btn btn-secondary trade-configure">Настроить услуги НПС</button>' : ''}
            ${onRepair ? '<button type="button" class="btn btn-secondary trade-repair">Ремонт у механика</button>' : ''}
            ${onModification ? '<button type="button" class="btn btn-secondary trade-modification">Установить модификацию</button>' : ''}
            ${target.data?.merchant?.enabled ? `<button type="button" class="btn btn-secondary trade-mode">${shop ? 'Обычный обмен' : 'Торговля по прайсу'}</button>` : ''}
            <button type="button" class="btn btn-secondary trade-reset">Очистить выбор</button>
            <button type="button" class="btn btn-primary trade-confirm">${shop ? 'Заключить сделку' : 'Предложить обмен'}</button></div></div>`;
    const sides = Object.fromEntries(['actor','target'].map(side => [side, modal.querySelector(`[data-trade-side="${side}"]`)]));
    const money = side => Number(sides[side].querySelector('.trade-money').value);
    const offer = side => tradeOffer(entries[side], selected[side]);
    let submitting = false;
    const update = () => {
        if (shop) {
            const quote = merchantTotals(entries, selected, prices);
            sides.actor.querySelector('.trade-money').value = quote.actorMoney;
            sides.target.querySelector('.trade-money').value = quote.targetMoney;
        }
        const actorMoney = money('actor'), targetMoney = money('target');
        for (const side of ['actor','target']) {
            const delta = side === 'actor' ? targetMoney - actorMoney : actorMoney - targetMoney;
            sides[side].querySelector('.trade-balance').textContent = `После обмена: ${rubles(balances[side] + delta)}`;
        }
        const summary = side => {
            const count = offer(side).reduce((sum, entry) => sum + entry.amount, 0);
            return `${count} шт. · ${rubles(money(side))}`;
        };
        modal.querySelector('.trade-summary').textContent = shop
            ? `Продаёте: ${rubles(merchantTotals(entries,selected,prices).sale)}     Покупаете: ${rubles(merchantTotals(entries,selected,prices).purchase)}`
            : `Отдаёте: ${summary('actor')}     Получаете: ${summary('target')}`;
        const error = validateTrade({actorOffer:offer('actor'),targetOffer:offer('target'),actorMoney,targetMoney,
            actorBalance:balances.actor,targetBalance:balances.target});
        modal.querySelector('.trade-error').textContent = error;
        modal.querySelector('.trade-confirm').disabled = submitting || Boolean(error);
    };
    const renderList = side => {
        const panel = sides[side], container = panel.querySelector('.trade-item-list');
        const search = panel.querySelector('.trade-search').value.trim().toLocaleLowerCase('ru');
        const category = panel.querySelector('.trade-category').value;
        container.replaceChildren();
        for (const entry of entries[side]) {
            if (search && !String(entry.item?.name || '').toLocaleLowerCase('ru').includes(search)) continue;
            if (category && entry.item?.category !== category) continue;
            const key = tradeEntryKey(entry), available = quantity(entry.item);
            const row = document.createElement('div');
            row.className = 'trade-item-row';
            row.innerHTML = `<label><input type="checkbox" ${selected[side].has(key) ? 'checked' : ''}><span><strong>${escape(entry.item?.name || 'Предмет')}</strong>
                <small>${escape(entry.rootLabel || '')} · доступно ${available}${shop ? ` · ${rubles(merchantUnitPrice(entry.item, prices, side==='actor'?'buy':'sell'))}/шт.` : ''}</small></span></label>
                <input type="number" class="form-control trade-quantity" value="${selected[side].get(key) ?? 1}" min="1" max="${available}" step="1" aria-label="Количество: ${escape(entry.item?.name)}" ${selected[side].has(key) ? '' : 'disabled'}>`;
            const checkbox = row.querySelector('[type="checkbox"]'), input = row.querySelector('.trade-quantity');
            checkbox.onchange = () => {
                input.disabled = !checkbox.checked;
                if (checkbox.checked) selected[side].set(key, Number(input.value));
                else selected[side].delete(key);
                update();
            };
            input.oninput = () => {
                input.value = Math.min(available, Number(input.value));
                selected[side].set(key, Number(input.value));
                update();
            };
            container.appendChild(row);
        }
        if (!container.childElementCount) container.textContent = entries[side].length ? 'Ничего не найдено' : 'Нет предметов для обмена';
    };
    for (const side of ['actor','target']) {
        sides[side].querySelector('.trade-search').oninput = () => renderList(side);
        sides[side].querySelector('.trade-category').onchange = () => renderList(side);
        sides[side].querySelector('.trade-money').oninput = update;
        renderList(side);
    }
    modal.querySelector('.trade-close').onclick = () => { if (!submitting) onClose(); };
    const configure = modal.querySelector('.trade-configure');
    if (configure) configure.onclick = () => { if (!submitting) onConfigure(); };
    const repair = modal.querySelector('.trade-repair');
    if (repair) repair.onclick = () => { if (!submitting) onRepair(); };
    const modification=modal.querySelector('.trade-modification');
    if (modification) modification.onclick=()=>{if(!submitting)onModification();};
    const mode = modal.querySelector('.trade-mode');
    if (mode) mode.onclick = () => { if (!submitting) renderTradeEditor(modal, {
        actor,target,actorEntries,targetEntries,quantity,onSubmit,onClose,onConfigure,onRepair,onModification,
        merchant:shop ? null : target.data.merchant,
    }); };
    modal.querySelector('.trade-reset').onclick = () => {
        if (submitting) return;
        for (const side of ['actor','target']) {
            selected[side].clear();
            sides[side].querySelector('.trade-money').value = 0;
            renderList(side);
        }
        update();
    };
    modal.querySelector('.trade-confirm').onclick = async () => {
        if (submitting || modal.querySelector('.trade-confirm').disabled) return;
        submitting = true;
        modal.querySelectorAll('input,select,button').forEach(input => { input.disabled = true; });
        try {
            await onSubmit({actor_offer:offer('actor'),target_offer:offer('target'),actor_money:money('actor'),target_money:money('target'),
                merchant_mode:shop,
                actor_revision:actor.data?._revision ?? actor.revision,target_revision:target.data?._revision ?? target.revision});
        } catch (error) {
            submitting = false;
            modal.querySelectorAll('input,select,button').forEach(input => { input.disabled = false; });
            for (const side of ['actor','target']) renderList(side);
            update();
            modal.querySelector('.trade-error').textContent = error.message || 'Обмен не отправлен. Попробуйте ещё раз.';
        }
    };
    update();
}

export function renderProfessionalRepairEditor(modal,{entries,balance,onQuote,onSubmit,onClose}) {
    const options=entries.filter(entry=>entry.item&&(
        ['weapon','melee_weapon','armor','helmet','gas_mask'].includes(entry.item.category)||entry.path[0]==='weapons'||entry.item.model));
    modal.innerHTML=`<div class="modal-content character-trade merchant-editor">
        <div class="character-trade-heading"><h3>Ремонт у механика</h3><button type="button" class="btn btn-secondary repair-close" aria-label="Закрыть">×</button></div>
        <p>Доступно: ${rubles(balance)}. Цена рассчитывается по каталогу, износу и наценке механика.</p>
        <label>Предмет <select class="form-control repair-item">${options.map((entry,index)=>
            `<option value="${index}">${escape(entry.item.name||entry.item.model||'Предмет')}</option>`).join('')}</select></label>
        <label>Вид ремонта <select class="form-control repair-mode"></select></label>
        <div class="trade-summary repair-quote" aria-live="polite"></div><div class="trade-error" role="status"></div>
        <div class="form-actions"><button type="button" class="btn btn-secondary repair-check">Рассчитать</button>
            <button type="button" class="btn btn-primary repair-confirm" disabled>Оплатить ремонт</button></div></div>`;
    const itemSelect=modal.querySelector('.repair-item'),modeSelect=modal.querySelector('.repair-mode');
    const confirm=modal.querySelector('.repair-confirm'),message=modal.querySelector('.trade-error'),summary=modal.querySelector('.repair-quote');
    let quoted=null,operationId=null,busy=false;
    const refresh=()=>{
        const entry=options[Number(itemSelect.value)];
        modeSelect.innerHTML=entry?.path[0]==='weapons'||entry?.item?.model||['weapon','melee_weapon'].includes(entry?.item?.category)
            ? '<option value="weapon">Починить оружие</option>'
            : '<option value="armor_stage">Восстановить стадию брони</option><option value="armor_durability">Восстановить прочность текущей стадии</option>';
        quoted=null;operationId=null;confirm.disabled=true;summary.textContent='';message.textContent='';
    };
    itemSelect.onchange=refresh;modeSelect.onchange=()=>{quoted=null;operationId=null;confirm.disabled=true;summary.textContent='';};
    modal.querySelector('.repair-close').onclick=()=>{if(!busy)onClose();};
    modal.querySelector('.repair-check').onclick=async()=>{
        const entry=options[Number(itemSelect.value)];if(!entry)return;
        busy=true;message.textContent='';confirm.disabled=true;
        try{
            quoted={entry,mode:modeSelect.value,result:await onQuote(entry,modeSelect.value)};
            operationId=crypto.randomUUID();
            const outcome=quoted.result.result;
            const after=outcome.kind==='weapon'
                ? `Прочность после ремонта: ${outcome.after} из ${outcome.maximum_after}.`
                : `Стадия после ремонта: ${outcome.stage_after}. Прочность: ${outcome.durability_after}.`;
            summary.textContent=`${quoted.result.item_name}: ${rubles(quoted.result.price)}. ${after}`;
            confirm.disabled=quoted.result.price>balance;
            if(quoted.result.price>balance)message.textContent='Не хватает денег.';
        }catch(error){quoted=null;message.textContent=error.message||'Не удалось рассчитать ремонт';}
        finally{busy=false;}
    };
    confirm.onclick=async()=>{
        if(busy||!quoted)return;
        busy=true;confirm.disabled=true;message.textContent='';
        try{await onSubmit(quoted.entry,quoted.mode,operationId);}
        catch(error){message.textContent=error.message||'Не удалось оплатить ремонт';confirm.disabled=false;}
        finally{busy=false;}
    };
    if(options.length)refresh();
    else {itemSelect.innerHTML='<option>Нет подходящих предметов</option>';modal.querySelector('.repair-check').disabled=true;}
}

export function renderProfessionalModificationEditor(modal,{targets,rules,balance,onQuote,onSubmit,onUnlockQuote,onUnlockSubmit,onClose}) {
    const options=(targets||[]).filter(entry=>entry?.item && entry?.slots?.family);
    modal.innerHTML=`<div class="modal-content character-trade modification-service">
        <div class="character-trade-heading"><div><h3>Установка модификации</h3><small>Услуга специалиста · сразу после оплаты</small></div>
            <button type="button" class="btn btn-secondary modification-close" aria-label="Закрыть">×</button></div>
        <div class="modification-service-grid">
            <section class="modification-service-panel"><label>Ваш предмет<select class="form-control modification-item">${options.map((entry,index)=>
                `<option value="${index}">${escape(entry.item.name||entry.item.model||'Предмет')}</option>`).join('')}</select></label>
                <div class="modification-item-card"></div>
                ${onUnlockQuote?'<button type="button" class="btn btn-secondary modification-unlock">Открыть слот у механика · 2500 ₽</button>':''}</section>
            <section class="modification-service-panel"><label>Улучшение<select class="form-control modification-rule"></select></label>
                <div class="modification-rule-details"></div>
                <label>Заменить установленное улучшение<select class="form-control modification-replace"></select></label>
                <div class="modification-service-price"><small>На руках: ${rubles(balance)}</small><strong class="modification-price-value">Выберите улучшение</strong></div></section>
        </div><div class="trade-error modification-error" role="status"></div>
        <div class="form-actions"><button type="button" class="btn btn-secondary modification-check">Рассчитать стоимость</button>
            <button type="button" class="btn btn-primary modification-confirm" disabled>Оплатить и установить</button></div></div>`;
    const itemSelect=modal.querySelector('.modification-item'),ruleSelect=modal.querySelector('.modification-rule');
    const replaceSelect=modal.querySelector('.modification-replace'),card=modal.querySelector('.modification-item-card');
    const details=modal.querySelector('.modification-rule-details'),price=modal.querySelector('.modification-price-value');
    const error=modal.querySelector('.modification-error'),confirm=modal.querySelector('.modification-confirm');
    const unlock=modal.querySelector('.modification-unlock');
    let quoted=null,operationId=null,busy=false;
    const reset=()=>{quoted=null;operationId=null;confirm.disabled=true;price.textContent='Выберите улучшение';error.textContent='';};
    const current=()=>options[Number(itemSelect.value)];
    const rule=()=>((rules||[]).find(entry=>entry.id===ruleSelect.value));
    const renderRule=()=>{
        reset();const selected=rule(),entry=current();
        details.textContent=selected ? `${selected.description||selected.name} · Уровень ${selected.level}` : '';
        const installed=(entry?.item?.modifications||[]).filter(value=>value?.ruleId&&value.level===selected?.level);
        replaceSelect.innerHTML='<option value="">Без замены</option>'+installed.map(value=>
            `<option value="${escape(value.ruleId)}">${escape(value.name||value.ruleId)}</option>`).join('');
    };
    const renderItem=()=>{
        reset();const entry=current();if(!entry)return;
        const item=entry.item,slots=entry.slots,installed=(item.modifications||[]).filter(value=>value?.ruleId);
        if(unlock)unlock.disabled=slots.available>=slots.maximum;
        const glyph=entry.path?.[0]==='weapons'||item.category==='weapon'?'⌖':'◇';
        card.innerHTML=`<div class="modification-item-heading"><span class="modification-item-glyph" aria-hidden="true">${glyph}</span>
            <div><strong>${escape(item.name||item.model||'Предмет')}</strong><small>${escape(slots.family)} · Класс ${escape(slots.factor??'—')}</small></div></div>
            <div class="modification-slots" aria-label="Слоты модификаций">${Array.from({length:slots.maximum},(_,index)=>
                `<span class="${index<installed.length?'filled':index>=slots.available?'locked':''}" title="${index<installed.length?'Занят':index>=slots.available?'Закрыт':'Свободен'}"></span>`).join('')}</div>
            <small>${installed.length} занято · ${Math.max(0,slots.available-installed.length)} свободно · ${slots.maximum-slots.available} закрыто</small>
            <div class="modification-installed">${installed.length?installed.map(value=>`<span>${escape(value.name||value.ruleId)}</span>`).join(''):'Пока нет улучшений'}</div>`;
        const available=(rules||[]).filter(value=>value.automatic_installation && value.family===slots.family);
        ruleSelect.innerHTML=available.map(value=>`<option value="${escape(value.id)}">Ур. ${value.level} · ${escape(value.name||value.description)}</option>`).join('');
        if(!available.length)ruleSelect.innerHTML='<option value="">Нет доступных улучшений</option>';
        modal.querySelector('.modification-check').disabled=!available.length;
        renderRule();
    };
    itemSelect.onchange=renderItem;ruleSelect.onchange=renderRule;replaceSelect.onchange=reset;
    modal.querySelector('.modification-close').onclick=()=>{if(!busy)onClose();};
    if(unlock)unlock.onclick=async()=>{
        const entry=current();if(busy||!entry)return;
        busy=true;unlock.disabled=true;error.textContent='';
        try{
            const plan=await onUnlockQuote(entry);
            if(plan.price>balance)throw Error('Не хватает денег на открытие слота');
            if(window.confirm(`Открыть слот на «${entry.item.name||entry.item.model}» у механика за ${rubles(plan.price)}?`)){
                await onUnlockSubmit(entry,crypto.randomUUID());
            }
        }catch(reason){error.textContent=reason.message||'Не удалось открыть слот';}
        finally{busy=false;unlock.disabled=current()?.slots.available>=current()?.slots.maximum;}
    };
    modal.querySelector('.modification-check').onclick=async()=>{
        const entry=current(),selected=rule();if(!entry||!selected)return;
        busy=true;error.textContent='';confirm.disabled=true;
        try{
            const result=await onQuote(entry,selected,replaceSelect.value||null);
            quoted={entry,selected,replace:replaceSelect.value||null,result};operationId=crypto.randomUUID();
            price.textContent=`К оплате ${rubles(result.price)}`;
            confirm.disabled=result.price>balance;
            if(result.price>balance)error.textContent='Не хватает денег на установку.';
        }catch(reason){quoted=null;error.textContent=reason.message||'Не удалось рассчитать установку';}
        finally{busy=false;}
    };
    confirm.onclick=async()=>{
        if(busy||!quoted)return;
        busy=true;confirm.disabled=true;error.textContent='';
        try{await onSubmit(quoted.entry,quoted.selected,quoted.replace,operationId);}
        catch(reason){error.textContent=reason.message||'Не удалось установить улучшение';confirm.disabled=false;}
        finally{busy=false;}
    };
    if(options.length)renderItem();
    else {itemSelect.innerHTML='<option>Нет подходящих предметов</option>';modal.querySelector('.modification-check').disabled=true;}
}
