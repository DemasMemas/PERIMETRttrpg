import { Server } from './api.js';
import { ruleText } from './characterTraits.js';
import { characterPlanFile, readCharacterPlanFile } from './creationTemplates.js';

const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money = value => `${Number(value || 0).toLocaleString('ru-RU')} ₽`;
const compareChoice = (left, right) => ruleText(left.name).localeCompare(ruleText(right.name), 'ru', {sensitivity: 'base'});
export function traitDescription(trait) {
    if (!trait) return '';
    const effect = ruleText(trait.effect).trim(), limit = ruleText(trait.limitText).trim();
    return [effect && limit && !/[.!?…]$/.test(effect) ? `${effect}.` : effect, limit].filter(Boolean).join(' ');
}
const GROUPS = { physical: 'Физические', social: 'Социальные', other: 'Прочие' };
const STEPS = [
    ['Предыстория', 'Имя персонажа, предыстория и её положительные бонусы.'],
    ['Специализации', 'Выберите навыки со специализацией. Каждый получит +3.'],
    ['Повышение навыков', 'Повышайте нужные навыки за очки. До 11 включительно уровень стоит 1 очко, далее цена растёт на 1 за уровень.'],
    ['Понижение навыков', 'Каждое понижение на 3 возвращает 1 очко. Можно вернуть до 5 очков; навык не должен стать ниже 1.'],
    ['Минусы предыстории', 'Здесь применяются понижения навыков выбранной предыстории.'],
    ['Особые черты', 'Выберите до трёх черт. Цена отрицательной черты возвращает очки.'],
    ['Завершение навыков', 'После черт можно ещё повысить навыки. Цена продолжает ступень, достигнутую до понижений и минусов. Неиспользованные очки сохранятся на будущее.'],
    ['Стартовое снаряжение', ''],
    ['Жизнь в Зоне', ''],
];
const STAGES = ['background_positive', 'specializations', 'raises', 'lowers', 'background_negative', 'traits', 'final_raises', 'final_raises', 'final_raises'];

export function traitChoiceFields(rules, trait, { specialized = [] } = {}) {
    const paths = rules.skillOrder.filter(path => trait?.name !== 'Специалист' || !specialized.includes(path))
        .map(path => [path, rules.skills[path.split('.')[0]][path.split('.')[1]]]);
    if (trait && ['Специалист', 'Заинтересованный'].includes(trait.name)) {
        return `<label>Навык<select data-choice="skill" class="form-control">${paths.length ? paths.map(([path, name]) => `<option value="${path}">${escape(name)}</option>`).join('') : '<option value="">Все навыки уже специализированы</option>'}</select></label>`;
    }
    if (trait && ['Курс знакомства с оружием', 'Слияние с оружием'].includes(trait.name)) {
        return `<label>Вид оружия<select data-choice="weaponType" class="form-control">${Object.entries(rules.weaponTypes).map(([key, name]) => `<option value="${key}">${escape(name)}</option>`).join('')}</select></label>`;
    }
    if (trait?.name === 'Патронщик') return '<label>Калибр<input data-choice="caliber" class="form-control" maxlength="60"></label>';
    if (trait?.name === 'Шапочка из фольги') return '<label>Идея персонажа<input data-choice="idea" class="form-control" maxlength="300"></label>';
    return '';
}

export async function openCharacterCreation(lobbyId, onCreated) {
    if (document.getElementById('character-creation-modal')) return;
    const rules = await Server.characterCreationRules(lobbyId);
    rules.backgrounds.sort(compareChoice);
    rules.traits.sort(compareChoice);
    // Используем тот же каталог карточек, что и обычное добавление в инвентарь.
    const { renderInventoryTemplatePicker, getCategoryDisplay } = await import('./characterSheet.js');
    if (document.getElementById('character-creation-modal')) return;
    const skills = rules.skillOrder.map(path => [path, rules.skills[path.split('.')[0]][path.split('.')[1]]]);
    const shop = rules.startingPurchaseTemplates || [], purchases = new Map();
    const purchaseName = template => template.attributes?.book_kind
        ? `Книга · ${template.name}` : template.name;
    const modal = document.createElement('div');
    modal.id = 'character-creation-modal'; modal.className = 'modal creation-modal';
    const skillCards = (stage, editable = true) => Object.entries(GROUPS).map(([group, title]) => `<section class="creation-skill-group"><h4>${title}</h4><div class="creation-skill-grid">${skills.filter(([path]) => path.startsWith(group + '.')).map(([path, label]) => `<div class="creation-skill-card">
        <div><strong>${escape(label)}</strong><span class="creation-skill-meta" data-skill-meta="${stage}:${path}"></span></div>
        <output data-result="${path}" data-result-stage="${stage}">5</output>
        ${stage === 'specializations' ? `<label class="creation-spec"><input type="checkbox" data-specialization="${path}" aria-label="Специализация: ${escape(label)}"> +3</label>` : editable ? `<div class="creation-stepper"><button type="button" class="btn btn-sm" data-adjust="-1" data-target="${stage}:${path}" aria-label="Уменьшить: ${escape(label)}">−</button><input type="number" min="0" max="${stage === 'lowers' ? 5 : 19}" value="0" data-stage="${stage}" data-skill="${path}" aria-label="${stage === 'lowers' ? 'Понижений на 3' : 'Добавить уровней'}: ${escape(label)}" class="form-control"><button type="button" class="btn btn-sm" data-adjust="1" data-target="${stage}:${path}" aria-label="Увеличить: ${escape(label)}">+</button></div>` : ''}
    </div>`).join('')}</div></section>`).join('');
    const purchaseCategory = template => template.attributes?.book_kind ? 'books'
        : ['belt', 'armor_plate'].includes(template.category) ? 'vest' : template.category;
    const purchaseCategoryLabel = category => category === 'vest' ? 'Разгрузки, пояса и бронеплиты' : getCategoryDisplay(category);
    const categories = [...new Set(shop.map(purchaseCategory))].sort((a, b) => purchaseCategoryLabel(a).localeCompare(purchaseCategoryLabel(b), 'ru'));
    const contents = [
        `<div class="creation-intro-fields"><label>Имя персонажа<input name="characterAlias" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="100" class="form-control" placeholder="Имя или прозвище в игре"></label>
        <label>Предыстория<select name="backgroundId" class="form-control"><option value="">Выберите предысторию</option>${rules.backgrounds.filter(background => background.available).map(background => `<option value="${background.id}">${escape(ruleText(background.name))}</option>`).join('')}</select></label></div>
        <div data-background-description></div>
        <label data-background-weapon-field hidden><span data-background-weapon-label>Владение оружием от предыстории</span><small id="creation-background-weapon-hint" data-background-weapon-hint class="creation-hint"></small><select name="backgroundWeapon" aria-describedby="creation-background-weapon-hint" class="form-control"><option value="">Выберите вид оружия</option>${Object.entries(rules.weaponTypes).map(([key, label]) => `<option value="${key}">${escape(label)}</option>`).join('')}</select></label>
        <label data-favored-mutant-field hidden>Избранный мутант<select name="favoredMutant" class="form-control"><option value="">Выберите мутанта</option>${(rules.mutants || []).map(mutant => `<option value="${escape(mutant.name)}">${escape(mutant.name)}</option>`).join('')}</select></label>
        <div data-background-positive></div>`,
        skillCards('specializations'),
        skillCards('raises'),
        skillCards('lowers'),
        `<div data-background-negative></div>${skillCards('background_negative', false)}`,
        [0, 1, 2].map(index => `<section class="creation-trait-card" data-trait-row="${index}"><label>Черта ${index + 1}<select data-trait="${index}" class="form-control"><option value="">Без черты</option>${rules.traits.map(trait => `<option value="${trait.id}">${escape(ruleText(trait.name))} · ${trait.cost} очк.</option>`).join('')}</select></label><p data-trait-description="${index}"></p><div data-trait-choice="${index}"></div></section>`).join(''),
        skillCards('finalRaises'),
        `<div class="creation-shop-budget"><div><span>Доступно</span><strong data-purchase-balance>${money(10000)}</strong></div><div><span>Стоимость закупки</span><strong data-purchase-total-cost>${money(0)}</strong></div><label class="creation-exchange">Обмен очков<div class="creation-stepper"><button type="button" class="btn btn-sm" data-cash-adjust="-1" aria-label="Обменять меньше очков">−</button><input name="cashPoints" type="number" min="0" value="0" class="form-control" aria-label="Очки в деньги"><button type="button" class="btn btn-sm" data-cash-adjust="1" aria-label="Обменять больше очков">+</button></div><small>1 очко = 200 ₽</small></label></div>
        <div class="creation-shop-filters"><label>Категория<select data-purchase-category class="form-control">${categories.map(category => `<option value="${category}">${escape(purchaseCategoryLabel(category))}</option>`).join('')}</select></label><label>Поиск<input type="search" data-purchase-search class="form-control" placeholder="Название, калибр..." autocomplete="off"></label></div>
        <div class="creation-shop-columns"><div data-purchase-catalog></div><aside class="creation-cart"><h4>Выбранное снаряжение</h4><div data-purchase-list></div><p class="creation-hint">Неизрасходованные деньги сохранятся у персонажа.</p></aside></div>
        <div data-purchase-selection hidden class="creation-purchase-selection"><strong data-purchase-title></strong><label>Количество<input data-purchase-quantity type="number" min="1" max="10000" value="1" class="form-control"></label><strong data-purchase-selection-total></strong><button type="button" data-purchase-add class="btn btn-primary">Добавить</button><button type="button" data-purchase-dismiss class="btn btn-secondary">Отмена</button></div>`,
        `<fieldset class="creation-history-choice"><legend>Персонаж уже жил в Зоне?</legend><label><input type="radio" name="zoneHistory" value="no" checked> Нет, только прибывает</label><label><input type="radio" name="zoneHistory" value="yes"> Да, жил в Зоне</label></fieldset><p data-history-description hidden>Будут определены срок жизни в Зоне и события, которые произошли с персонажем.</p><div data-creation-summary></div>`,
    ];
    modal.innerHTML = `<div class="modal-content creation-dialog" role="dialog" aria-modal="true" aria-labelledby="creation-title"><header class="creation-header"><div><p class="creation-eyebrow">Новый персонаж</p><h2 id="creation-title">Создание персонажа</h2></div><button type="button" data-creation-close class="btn btn-secondary creation-close" aria-label="Закрыть создание персонажа"><svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></button></header>
        <nav class="creation-nav" aria-label="Шаги создания">${STEPS.map(([title], index) => `<button type="button" data-go-step="${index}"><span>${index + 1}</span>${title}</button>`).join('')}</nav>
        <div class="creation-counters" aria-live="polite"><span>Очки навыков <strong data-points-remaining>30</strong></span><span>Специализации <strong data-specs-remaining>10</strong></span><span class="creation-hint" data-calculation-state></span></div>
        <form autocomplete="off" novalidate><main class="creation-main"><details class="creation-template-tools"><summary>Шаблон персонажа</summary><p class="creation-hint">Сохраните распределение, черты и закуп в файл для нового персонажа. Имя и события жизни в Зоне в шаблон не входят.</p><div><button type="button" data-template-save class="btn btn-sm btn-secondary">Сохранить шаблон</button><button type="button" data-template-load class="btn btn-sm btn-secondary">Загрузить шаблон</button><input type="file" data-template-file accept=".json,application/json" hidden></div><p data-template-status class="creation-hint" role="status"></p></details>${contents.map((content, index) => `<section class="creation-step" data-creation-step="${index}" ${index ? 'hidden' : ''}><div class="creation-step-heading"><div><h3>${STEPS[index][0]}</h3>${STEPS[index][1] ? `<p class="creation-hint">${STEPS[index][1]}</p>` : ''}</div><button type="button" data-reset-step="${index}" class="btn btn-sm btn-secondary">Сбросить шаг</button></div>${content}</section>`).join('')}</main>
        <footer class="creation-footer"><p data-creation-error role="alert"></p><div><button type="button" data-back class="btn btn-secondary">Назад</button><span data-step-position></span><button type="button" data-next class="btn btn-primary">Далее</button><button type="submit" class="btn btn-primary" hidden>Создать персонажа</button></div></footer></form>
        <div class="creation-confirm" data-reset-confirm hidden><div role="alertdialog" aria-modal="true" aria-labelledby="creation-reset-title"><h3 id="creation-reset-title">Сбросить этот шаг?</h3><p data-reset-description></p><div><button type="button" data-reset-cancel class="btn btn-secondary">Отмена</button><button type="button" data-reset-confirm-button class="btn btn-primary">Сбросить шаг</button></div></div></div>
    </div>`;
    document.body.appendChild(modal);
    const form = modal.querySelector('form'), lifetime = new AbortController();
    let step = 0, busy = false, timer, generation = 0, navigation = 0, request, latestData, latestError = '', latestKey = '';
    let selectedPurchase = null, resetIndex = 0;
    const find = selector => modal.querySelector(selector);
    const selectedBackground = () => rules.backgrounds.find(background => background.id === form.elements.backgroundId.value);
    const requiresWeapon = background => background?.entries.some(entry => /выбранным видом/i.test(entry.text || ''));
    const error = message => { find('[data-creation-error]').textContent = message || ''; };
    function readPlan() {
        const plan = { backgroundId: form.elements.backgroundId.value, specializations: [], raises: {}, lowers: {}, finalRaises: {}, traits: [],
            cashPoints: Number(form.elements.cashPoints.value), zoneHistory: form.elements.zoneHistory.value === 'yes', purchases: [...purchases].map(([templateId, quantity]) => ({ templateId, quantity })) };
        const background = selectedBackground();
        if (requiresWeapon(background) && form.elements.backgroundWeapon.value) plan.backgroundWeapon = form.elements.backgroundWeapon.value;
        if (background?.name === 'Охотник' && form.elements.favoredMutant.value) plan.favoredMutant = form.elements.favoredMutant.value;
        modal.querySelectorAll('[data-specialization]:checked').forEach(input => plan.specializations.push(input.dataset.specialization));
        modal.querySelectorAll('[data-stage]').forEach(input => { if (Number(input.value)) plan[input.dataset.stage][input.dataset.skill] = Number(input.value); });
        modal.querySelectorAll('[data-trait]').forEach(input => {
            if (!input.value) return;
            const choice = { id: input.value };
            find(`[data-trait-choice="${input.dataset.trait}"]`).querySelectorAll('[data-choice]').forEach(field => { choice[field.dataset.choice] = field.value.trim(); });
            plan.traits.push(choice);
        });
        return plan;
    }
    function applyTemplate(plan) {
        form.elements.backgroundId.value = plan.backgroundId;
        form.elements.backgroundWeapon.value = plan.backgroundWeapon || '';
        form.elements.favoredMutant.value = plan.favoredMutant || '';
        form.elements.cashPoints.value = plan.cashPoints;
        form.elements.zoneHistory.value = plan.zoneHistory ? 'yes' : 'no';
        modal.querySelectorAll('[data-specialization]').forEach(input => { input.checked = plan.specializations.includes(input.dataset.specialization); });
        modal.querySelectorAll('[data-stage]').forEach(input => { input.value = plan[input.dataset.stage][input.dataset.skill] || 0; });
        modal.querySelectorAll('[data-trait]').forEach(input => { input.value = ''; find(`[data-trait-choice="${input.dataset.trait}"]`).replaceChildren(); });
        for (const [index, choice] of plan.traits.entries()) {
            find(`[data-trait="${index}"]`).value = choice.id;
            traitDetails(index);
            find(`[data-trait-choice="${index}"]`).querySelectorAll('[data-choice]').forEach(input => { input.value = choice[input.dataset.choice] || ''; });
        }
        for (const index of [0, 1, 2]) if (!plan.traits[index]) traitDetails(index);
        purchases.clear();
        for (const item of plan.purchases) purchases.set(item.templateId, item.quantity);
        selectedPurchase = null; find('[data-purchase-selection]').hidden = true;
        backgroundDetails(); renderCart(); renderShop(); queuePreview();
    }
    async function loadTemplate(file) {
        if (!file || busy) return;
        if (file.size > 256 * 1024) { error('Файл шаблона слишком большой.'); return; }
        busy = true;
        const controller = new AbortController(), deadline = setTimeout(() => controller.abort(), 10000);
        try {
            const plan = readCharacterPlanFile(await file.text(), rules);
            // Сервер проверяет действующие цены, лимиты и правила. Готовый лист
            // из файла не принимается и не может повторно начислить эффекты.
            await Server.previewCharacterCreation(lobbyId, form.elements.characterAlias.value.trim(), plan, { signal: controller.signal });
            if (!modal.isConnected) return;
            applyTemplate(plan);
            find('[data-template-status]').textContent = 'Шаблон загружен. Проверьте шаги и укажите имя нового персонажа.';
        } catch (failure) { error(failure.name === 'AbortError' ? 'Проверка шаблона не завершилась. Попробуйте ещё раз.' : failure.message); }
        finally { clearTimeout(deadline); busy = false; find('[data-template-file]').value = ''; }
    }
    function backgroundDetails() {
        const background = selectedBackground();
        const weaponRule = background?.entries.find(entry => /выбранным видом/i.test(entry.text || ''));
        const professional = /^Профессионал/i.test(weaponRule?.text || '');
        find('[data-background-weapon-field]').hidden = !weaponRule;
        find('[data-background-weapon-label]').textContent = professional ? 'Профессиональное владение оружием' : 'Знакомство с оружием';
        find('[data-background-weapon-hint]').textContent = weaponRule
            ? `Предыстория «${ruleText(background.name)}» даёт ${professional ? 'профессиональное владение одним видом оружия' : 'знакомство с одним видом оружия'} на ваш выбор.`
            : '';
        find('[data-favored-mutant-field]').hidden = background?.name !== 'Охотник';
        find('[data-background-description]').innerHTML = background ? `<article class="creation-background-card"><h4>${escape(ruleText(background.name))}</h4><p>${escape(ruleText(background.pluses))}</p><p class="creation-hint">${escape(ruleText(background.minuses))}</p></article>` : '';
        const describe = positive => background?.entries.filter(entry => ['skill', 'group', 'all_skills', 'points', 'specializations'].includes(entry.type) && (entry.amount > 0) === positive).map(entry => {
            const label = entry.type === 'skill' ? rules.skills[entry.skill.split('.')[0]][entry.skill.split('.')[1]] : entry.type === 'group' ? `${GROUPS[entry.group]} навыки` : { all_skills: 'Все навыки', points: 'Очки навыков', specializations: 'Специализации' }[entry.type];
            return `<span class="creation-bonus">${escape(label)} ${entry.amount > 0 ? '+' : ''}${entry.amount}</span>`;
        }).join('') || '';
        find('[data-background-positive]').innerHTML = background ? `<p>Положительные бонусы</p><div class="creation-bonuses">${describe(true) || '<span class="creation-hint">Дополнительных бонусов к навыкам нет.</span>'}</div><p>Штрафы предыстории</p><div class="creation-bonuses">${describe(false) || '<span class="creation-hint">Штрафов к навыкам и очкам нет.</span>'}</div>` : '';
        find('[data-background-negative]').innerHTML = `<div class="creation-bonuses">${describe(false) || '<span class="creation-hint">Понижений навыков нет.</span>'}</div>`;
    }
    function renderCart() {
        find('[data-purchase-list]').innerHTML = purchases.size ? [...purchases].map(([id, quantity]) => {
            const template = shop.find(item => item.id === id);
            return `<div class="creation-cart-row"><strong>${escape(purchaseName(template))}</strong><span>${money(template.price * quantity)}</span><label>Количество<div class="creation-stepper"><button type="button" class="btn btn-sm" data-cart-adjust="${id}" data-delta="-1" aria-label="Уменьшить количество: ${escape(template.name)}">−</button><input type="number" min="0" max="10000" value="${quantity}" data-cart-quantity="${id}" class="form-control"><button type="button" class="btn btn-sm" data-cart-adjust="${id}" data-delta="1" aria-label="Увеличить количество: ${escape(template.name)}">+</button></div></label><button type="button" data-purchase-remove="${id}" class="btn btn-sm btn-secondary">Убрать</button></div>`;
        }).join('') : '<p class="creation-hint">Выберите предмет в каталоге.</p>';
    }
    function renderShop() {
        if (step !== 7) return;
        const templates = shop.filter(template => purchaseCategory(template) === find('[data-purchase-category]').value);
        find('[data-purchase-catalog]').innerHTML = renderInventoryTemplatePicker(templates, find('[data-purchase-search]').value, { purchase: true, selectedId: selectedPurchase?.id, open: true });
    }
    function refreshView() {
        const data = latestData, plan = readPlan(), background = selectedBackground();
        const available = 10 + (background?.entries.filter(entry => entry.type === 'specializations').reduce((sum, entry) => sum + entry.amount, 0) || 0);
        const remaining = available - plan.specializations.length;
        const specialistSkills = plan.traits.filter(choice => rules.traits.find(trait => trait.id === choice.id)?.name === 'Специалист').map(choice => choice.skill);
        const allSpecialized = new Set([...plan.specializations, ...specialistSkills]).size === skills.length;
        find('[data-specs-remaining]').textContent = `${remaining} осталось${allSpecialized ? ' · все навыки выбраны' : ''}`;
        const ledger = data?.creation.ledger || [];
        const current = ledger.find(entry => entry.stage === STAGES[step]);
        const points = step >= 6 ? data?.skills.skillPoints : current?.points;
        find('[data-points-remaining]').textContent = Number.isFinite(points) ? points : (30 + (background?.entries.filter(entry => entry.type === 'points').reduce((sum, entry) => sum + entry.amount, 0) || 0));
        find('[data-points-remaining]').classList.toggle('creation-negative', Number(points) < 0);
        modal.querySelectorAll('[data-result-stage]').forEach(output => {
            const stage = output.dataset.resultStage === 'finalRaises' ? 'final_raises' : output.dataset.resultStage;
            const snapshot = ledger.find(entry => entry.stage === stage), path = output.dataset.result;
            const value = snapshot?.levels[path] ?? 5;
            output.textContent = value;
            const hint = find(`[data-skill-meta="${output.dataset.resultStage}:${path}"]`);
            const pricing = data?.creation.finalRaisePricing;
            const priceLevel = stage === 'final_raises' ? (pricing?.levels[path] ?? value) : value;
            const cost = stage === 'final_raises' ? (pricing?.nextCosts[path] ?? Math.max(1,priceLevel+1-10)) : Math.max(1,value+1-10);
            if (hint) hint.textContent = stage === 'specializations' ? (specialistSkills.includes(path) ? 'Специализация от черты' : '') : ['raises', 'final_raises'].includes(stage) ? `Следующее повышение: ${cost} очк.${priceLevel>value ? ` (как до ${priceLevel+1})` : ''}` : '';
        });
        modal.querySelectorAll('[data-specialization]').forEach(input => { input.disabled = !input.checked && (remaining <= 0 || specialistSkills.includes(input.dataset.specialization)); });
        const budget = (background?.name === 'Бедный' ? 5000 : 10000) + plan.cashPoints * 200;
        const spent = [...purchases].reduce((sum, [id, quantity]) => sum + shop.find(template => template.id === id).price * quantity, 0);
        find('[data-purchase-total-cost]').textContent = money(spent);
        find('[data-purchase-balance]').textContent = money(budget - spent);
        find('[data-purchase-balance]').classList.toggle('creation-negative', spent > budget);
        find('[data-creation-summary]').innerHTML = `<div class="creation-review"><strong>${escape(form.elements.characterAlias.value.trim() || 'Без имени')}</strong><span>${escape(background?.name || 'Предыстория не выбрана')}</span><span>Свободных очков: ${data?.skills.skillPoints ?? '—'}</span><span>Деньги после закупа: ${money(budget - spent)}</span><span>Предметов: ${[...purchases.values()].reduce((sum, value) => sum + value, 0)}</span></div>`;
        find('[data-history-description]').hidden = form.elements.zoneHistory.value !== 'yes';
        if (step === 7 && spent > budget) error('Не хватает денег. Уберите снаряжение или обменяйте очки.');
    }
    async function preview() {
        clearTimeout(timer);
        const plan = readPlan(), key = JSON.stringify(plan);
        if (latestKey === key && latestData) { latestError = ''; error(''); find('[data-calculation-state]').textContent = ''; refreshView(); return true; }
        if (!plan.backgroundId) { latestData = null; latestError = ''; latestKey = ''; find('[data-calculation-state]').textContent = ''; refreshView(); return false; }
        const run = ++generation;
        request?.abort(); request = new AbortController();
        const controller = request;
        find('[data-calculation-state]').textContent = 'Считаем…';
        const deadline = setTimeout(() => controller.abort(), 10000);
        const task = (async () => {
            try {
                const aborted = new Promise((resolve, reject) => controller.signal.addEventListener('abort', () => reject(new DOMException('Расчёт прерван', 'AbortError')), { once: true }));
                const { data } = await Promise.race([Server.previewCharacterCreation(lobbyId, form.elements.characterAlias.value.trim(), plan, { signal: controller.signal }), aborted]);
                if (run !== generation || !modal.isConnected) return false;
                latestData = data; latestKey = key; latestError = ''; error(''); refreshView(); return true;
            } catch (failure) {
                if (run !== generation || !modal.isConnected) return false;
                latestError = failure.name === 'AbortError' ? 'Расчёт не завершился. Попробуйте ещё раз.' : failure.message;
                error(latestError); refreshView(); return false;
            } finally {
                clearTimeout(deadline);
                if (run === generation && modal.isConnected) find('[data-calculation-state]').textContent = '';
            }
        })();
        return task;
    }
    function queuePreview() {
        error(''); refreshView(); clearTimeout(timer); request?.abort(); ++generation;
        timer = setTimeout(() => { void preview(); }, 160);
    }
    function currentError() {
        if (step === 0) {
            if (!form.elements.characterAlias.value.trim()) return 'Укажите имя персонажа.';
            if (!selectedBackground()) return 'Выберите предысторию.';
            if (requiresWeapon(selectedBackground()) && !form.elements.backgroundWeapon.value) return 'Выберите вид оружия.';
            if (selectedBackground()?.name === 'Охотник' && !form.elements.favoredMutant.value) return 'Выберите избранного мутанта.';
        }
        if (latestError) return latestError;
        if (!latestData) return 'Сначала выберите предысторию.';
        const ledger = latestData.creation.ledger;
        const selected = ledger.find(entry => entry.stage === STAGES[step]);
        if (step === 1 && latestData.skills.specializations > 0) {
            const specialized = skills.filter(([path]) => { const [group, key] = path.split('.'); return latestData.skills[group][key].specialized; }).length;
            if (specialized < skills.length) return `Распределите оставшиеся специализации: ${latestData.skills.specializations}.`;
        }
        if (step >= 2 && ledger.find(entry => entry.stage === 'raises')?.points < 0) return 'Не хватает очков на шаге «Повышение навыков». Уменьшите количество повышений.';
        if (step >= 2 && selected?.points < 0 || step >= 6 && latestData.skills.skillPoints < 0) return 'Не хватает очков навыков. Уменьшите расходы на этом или предыдущих шагах.';
        if (step >= 7 && latestData.inventory.money < 0) return 'Баланс снаряжения не должен быть отрицательным.';
        return '';
    }
    async function goTo(target) {
        if (busy || target < 0 || target >= STEPS.length || target === step) return;
        const transition = ++navigation;
        if (target > step) {
            const calculated = await preview();
            if (!modal.isConnected || transition !== navigation) return;
            const problem = currentError();
            if (!calculated || problem) { error(problem || 'Дождитесь пересчёта после изменения данных.'); return; }
        }
        step = target;
        modal.querySelectorAll('[data-creation-step]').forEach(panel => { panel.hidden = Number(panel.dataset.creationStep) !== step; });
        modal.querySelectorAll('[data-go-step]').forEach(button => { button.setAttribute('aria-current', Number(button.dataset.goStep) === step ? 'step' : 'false'); });
        find('[data-back]').disabled = step === 0;
        find('[data-next]').hidden = step === STEPS.length - 1;
        find('[type=submit]').hidden = step !== STEPS.length - 1;
        find('[data-step-position]').textContent = `${step + 1} / ${STEPS.length}`;
        find('.creation-main').scrollTop = 0;
        error(''); renderShop(); refreshView();
        find(`[data-go-step="${step}"]`).scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'instant' });
    }
    function traitDetails(index) {
        const trait = rules.traits.find(entry => entry.id === find(`[data-trait="${index}"]`).value);
        find(`[data-trait-description="${index}"]`).textContent = traitDescription(trait);
        const plan = readPlan();
        const specialized = [...plan.specializations, ...plan.traits.filter(choice => rules.traits.find(entry => entry.id === choice.id)?.name === 'Специалист').map(choice => choice.skill).filter(Boolean)];
        find(`[data-trait-choice="${index}"]`).innerHTML = traitChoiceFields(rules, trait, { specialized });
    }
    function resetStep(index) {
        const panel = find(`[data-creation-step="${index}"]`);
        panel.querySelectorAll('input, select').forEach(input => {
            if (input.type === 'checkbox') input.checked = false;
            else if (input.type === 'radio') input.checked = input.value === 'no';
            else if (input.matches('[data-stage], [name=cashPoints]')) input.value = '0';
            else if (input.matches('[data-purchase-quantity]')) input.value = '1';
            else if (input.matches('[data-purchase-category]')) input.selectedIndex = 0;
            else input.value = '';
        });
        if (index === 0) backgroundDetails();
        if (index === 5) [0, 1, 2].forEach(traitDetails);
        if (index === 7) { purchases.clear(); selectedPurchase = null; find('[data-purchase-selection]').hidden = true; renderCart(); renderShop(); }
        queuePreview();
    }
    const close = () => {
        if (busy) return;
        ++generation; clearTimeout(timer); request?.abort(); lifetime.abort(); modal.remove();
    };
    modal.addEventListener('input', event => {
        const input = event.target;
        if (input.matches('[data-purchase-search]')) { renderShop(); return; }
        if (input.matches('[data-purchase-quantity]')) {
            if (selectedPurchase) find('[data-purchase-selection-total]').textContent = money(selectedPurchase.price * Math.max(0, Number(input.value) || 0));
            return;
        }
        if (input.matches('[name=characterAlias]')) { refreshView(); return; }
        if (input.matches('[data-stage], [name=cashPoints]')) queuePreview();
    });
    modal.addEventListener('change', event => {
        const input = event.target;
        if (input.matches('[data-purchase-quantity]')) {
            if (selectedPurchase) find('[data-purchase-selection-total]').textContent = money(selectedPurchase.price * Math.max(0, Number(input.value) || 0));
            return;
        }
        if (input.matches('[data-template-file]')) { void loadTemplate(input.files[0]); return; }
        if (input.matches('[name=backgroundId]')) { latestData = null; latestKey = ''; backgroundDetails(); }
        if (input.hasAttribute('data-trait')) traitDetails(input.dataset.trait);
        if (input.matches('[data-purchase-category]')) { selectedPurchase = null; find('[data-purchase-selection]').hidden = true; renderShop(); return; }
        if (input.matches('[data-cart-quantity]')) {
            const quantity = Number(input.value);
            if (!Number.isInteger(quantity) || quantity < 0 || quantity > 10000) { renderCart(); return; }
            if (quantity) purchases.set(Number(input.dataset.cartQuantity), quantity);
            else purchases.delete(Number(input.dataset.cartQuantity));
            renderCart();
        }
        if (!input.matches('[data-purchase-search], [data-purchase-quantity]')) queuePreview();
    });
    modal.addEventListener('click', event => {
        const button = event.target.closest('button'); if (!button || busy) return;
        if (button.hasAttribute('data-creation-close')) close();
        else if (button.hasAttribute('data-template-save')) {
            if (!selectedBackground()) { error('Сначала выберите предысторию.'); return; }
            const link = document.createElement('a'), url = URL.createObjectURL(new Blob([characterPlanFile(readPlan())], { type: 'application/json' }));
            link.href = url; link.download = 'Шаблон персонажа.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
            find('[data-template-status]').textContent = 'Шаблон сохранён в файл.';
        }
        else if (button.hasAttribute('data-template-load')) find('[data-template-file]').click();
        else if (button.hasAttribute('data-go-step')) void goTo(Number(button.dataset.goStep));
        else if (button.hasAttribute('data-next')) void goTo(step + 1);
        else if (button.hasAttribute('data-back')) void goTo(step - 1);
        else if (button.hasAttribute('data-adjust')) {
            const [stage, path] = button.dataset.target.split(':');
            const input = find(`[data-stage="${stage}"][data-skill="${path}"]`);
            input.value = Math.max(0, Math.min(Number(input.max), (Number(input.value) || 0) + Number(button.dataset.adjust)));
            queuePreview();
        } else if (button.hasAttribute('data-cash-adjust')) {
            form.elements.cashPoints.value = Math.max(0, (Number(form.elements.cashPoints.value) || 0) + Number(button.dataset.cashAdjust)); queuePreview();
        } else if (button.hasAttribute('data-purchase-select')) {
            selectedPurchase = shop.find(template => template.id === Number(button.dataset.purchaseSelect));
            find('[data-purchase-title]').textContent = `${purchaseName(selectedPurchase)} · ${money(selectedPurchase.price)}`;
            find('[data-purchase-selection]').hidden = false; find('[data-purchase-quantity]').value = '1';
            find('[data-purchase-selection-total]').textContent = money(selectedPurchase.price);
        } else if (button.hasAttribute('data-purchase-quick-add')) {
            const id = Number(button.dataset.purchaseQuickAdd), quantity = purchases.get(id) || 0;
            if (quantity >= 10000) { error('Слишком большое количество одного предмета.'); return; }
            purchases.set(id, quantity + 1); renderCart(); queuePreview();
        } else if (button.hasAttribute('data-cart-adjust')) {
            const id = Number(button.dataset.cartAdjust), quantity = (purchases.get(id) || 0) + Number(button.dataset.delta);
            if (quantity > 10000) { error('Слишком большое количество одного предмета.'); return; }
            if (quantity > 0) purchases.set(id, quantity); else purchases.delete(id);
            renderCart(); queuePreview();
        } else if (button.hasAttribute('data-purchase-dismiss')) { find('[data-purchase-selection]').hidden = true; selectedPurchase = null; }
        else if (button.hasAttribute('data-purchase-add') && selectedPurchase) {
            const quantity = Number(find('[data-purchase-quantity]').value);
            if (!Number.isInteger(quantity) || quantity < 1 || quantity > 10000) { error('Количество должно быть целым от 1 до 10 000.'); return; }
            const total = (purchases.get(selectedPurchase.id) || 0) + quantity;
            if (total > 10000) { error('Слишком большое количество одного предмета.'); return; }
            purchases.set(selectedPurchase.id, total); find('[data-purchase-selection]').hidden = true; selectedPurchase = null; renderCart(); queuePreview();
        } else if (button.hasAttribute('data-purchase-remove')) { purchases.delete(Number(button.dataset.purchaseRemove)); renderCart(); queuePreview(); }
        else if (button.hasAttribute('data-reset-step')) {
            resetIndex = Number(button.dataset.resetStep);
            find('[data-reset-description]').textContent = `Будут очищены данные шага «${STEPS[resetIndex][0]}». Остальные шаги сохранятся.`;
            find('[data-reset-confirm]').hidden = false; find('[data-reset-cancel]').focus();
        } else if (button.hasAttribute('data-reset-cancel')) find('[data-reset-confirm]').hidden = true;
        else if (button.hasAttribute('data-reset-confirm-button')) { find('[data-reset-confirm]').hidden = true; resetStep(resetIndex); }
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Tab' && !find('[data-reset-confirm]').hidden) {
            const controls = [find('[data-reset-cancel]'), find('[data-reset-confirm-button]')];
            const index = controls.indexOf(document.activeElement);
            event.preventDefault(); controls[(index + 1) % 2].focus();
            return;
        }
        if (event.key === 'Escape') {
            event.stopImmediatePropagation(); event.preventDefault();
            if (!find('[data-reset-confirm]').hidden) find('[data-reset-confirm]').hidden = true;
            else if (!find('[data-purchase-selection]').hidden) find('[data-purchase-selection]').hidden = true;
            else close();
        }
    }, { capture: true, signal: lifetime.signal });
    form.onsubmit = async event => {
        event.preventDefault(); if (busy) return;
        if (step < STEPS.length - 1) { await goTo(step + 1); return; }
        if (!form.elements.characterAlias.value.trim()) { error('Укажите имя персонажа.'); return; }
        if (!await preview()) return;
        const problem = currentError(); if (problem) { error(problem); return; }
        busy = true; clearTimeout(timer); ++generation;
        const button = find('[type=submit]'); button.disabled = true; button.textContent = 'Создаём…';
        try {
            const character = await Server.createLobbyCharacter(lobbyId, form.elements.characterAlias.value.trim(), { creationRequest: readPlan() });
            busy = false; close();
            if(character.data?.creation?.zoneHistory&&!character.data.creation.zoneHistory.settled){
                const {completeCharacterHistory}=await import('./characterHistory.js');
                character.data=await completeCharacterHistory(lobbyId,character.id,character.data);
            }
            await onCreated(character);
        } catch (failure) { busy = false; button.disabled = false; button.textContent = 'Создать персонажа'; error(failure.message); }
    };
    backgroundDetails(); renderCart(); refreshView();
    find('[data-back]').disabled = true;
    find('[data-go-step="0"]').setAttribute('aria-current', 'step');
    find('[data-step-position]').textContent = `1 / ${STEPS.length}`;
    form.elements.characterAlias.focus();
}
