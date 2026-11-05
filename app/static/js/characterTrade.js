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
        + (selected[side].get(tradeEntryKey(entry)) || 0) * Number(prices[String(entry.item?.templateId)]?.[field] || 0), 0);
    const sale = total('actor', 'buy'), purchase = total('target', 'sell');
    return {sale, purchase, actorMoney:Math.max(0, purchase-sale), targetMoney:Math.max(0, sale-purchase)};
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
                                        merchant=null, onConfigure=null}) {
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
        <div class="form-actions">${onConfigure ? '<button type="button" class="btn btn-secondary trade-configure">Настроить торговца</button>' : ''}
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
                <small>${escape(entry.rootLabel || '')} · доступно ${available}${shop ? ` · ${rubles(prices[String(entry.item?.templateId)][side==='actor'?'buy':'sell'])}/шт.` : ''}</small></span></label>
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
    const mode = modal.querySelector('.trade-mode');
    if (mode) mode.onclick = () => { if (!submitting) renderTradeEditor(modal, {
        actor,target,actorEntries,targetEntries,quantity,onSubmit,onClose,onConfigure,merchant:shop ? null : target.data.merchant,
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
