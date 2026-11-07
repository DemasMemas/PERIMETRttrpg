import { Server } from './api.js';
import { showNotification } from './utils.js';

const sections = [
    ['chat', 'Общий чат', 'Сообщения участников Зоны'],
    ['contacts', 'Контакты', 'Люди и каналы связи'],
    ['gallery', 'Галерея', 'Фото, видео и аудио'],
    ['files', 'Файлы', 'Электронные книги'],
    ['notes', 'Заметки', 'Личные записи'],
    ['map', 'Метки на карте', 'Сохранённые координаты'],
    ['sos', 'SOS', 'Экстренный сигнал'],
    ['alarm', 'Будильник', 'Напоминания по времени'],
    ['clock', 'Часы', 'Игровое время'],
    ['quests', 'Активные задания', 'Текущие поручения'],
    ['guide', 'Справочник', 'Сведения о Зоне'],
    ['mods', 'Модификации', 'Установленные возможности'],
];

function powerState(item) {
    const saved = item?.pdaState || {};
    const charge = Number(saved.charge_percent ?? 100);
    return { on: saved.on !== false, charge: Number.isFinite(charge) ? Math.max(0, Math.min(100, charge)) : 0 };
}

const bookTitle = name => String(name || '').replace('?. Раздел:', '? Раздел:');

export function openPdaShell({ item, findItem, getData, operate, canEdit, characterId, onSaved }) {
    const previous = document.getElementById('pda-shell-modal');
    if (previous) (previous.pdaClose || (() => previous.remove()))();
    const modal = document.createElement('div');
    modal.id = 'pda-shell-modal';
    modal.className = 'modal pda-shell-modal';
    modal.innerHTML = `
        <div class="modal-content pda-shell" role="dialog" aria-modal="true" aria-label="КПК">
            <div class="pda-shell-bezel">
                <div class="pda-shell-brand"><span class="pda-shell-light"></span> PERIMETR / КПК <span class="pda-shell-model">ZOKIA HIKING PHONE</span></div>
                <div class="pda-shell-screen">
                    <header class="pda-shell-header"><div><strong data-pda-heading>Главная</strong></div><div class="pda-shell-power"><span data-pda-status></span><button type="button" data-pda-power aria-label="Переключить питание"></button></div></header>
                    <div class="pda-shell-content"><nav class="pda-shell-nav" aria-label="Разделы КПК"></nav><main class="pda-shell-main" data-pda-content></main></div>
                    <footer class="pda-shell-footer"><button type="button" class="pda-shell-account" data-pda-account></button><span data-pda-charge></span></footer>
                </div>
                <button type="button" class="btn btn-secondary pda-shell-close" data-pda-close>Закрыть КПК</button>
            </div>
        </div>`;
    document.body.append(modal);
    modal.style.display = 'flex';

    const nav = modal.querySelector('.pda-shell-nav');
    const content = modal.querySelector('[data-pda-content]');
    const heading = modal.querySelector('[data-pda-heading]');
    let selected = 'home';
    let busy = false;
    let filesSnapshot = null;
    let filesLoading = false;
    let selectedMod = '';
    let selectedTool = '';
    let pdaQuote = null;
    let accountSnapshot = null;
    let accountLoading = true;
    let accountError = '';
    let accountMessage = '';
    let accountMode = 'login';
    const accountDraft = { login: '', password: '' };
    const selectedBookSkills = new Map();

    async function loadAccount() {
        try { accountSnapshot = await Server.getPdaAccount(characterId); }
        catch (error) { showNotification(error.message, 'system'); }
        finally { accountLoading = false; if (modal.isConnected) render(); }
    }

    async function accountAction(operation, payload = {}) {
        if (busy || !canEdit) return;
        busy = true;
        accountError = '';
        accountMessage = '';
        render();
        try {
            accountSnapshot = await Server.changePdaAccount(characterId, operation, payload);
            if (operation === 'login' || operation === 'register') {
                accountDraft.password = '';
                accountMessage = operation === 'register'
                    ? `Аккаунт «${accountSnapshot.account.login}» создан. Вход выполнен.`
                    : `Вход выполнен: ${accountSnapshot.account.login}.`;
            }
            if (operation === 'save_notes') showNotification('Заметки сохранены в аккаунте КПК', 'system');
        }
        catch (error) {
            accountError = operation === 'register'
                ? `Аккаунт не создан: ${error.message}`
                : operation === 'login'
                    ? `Вход не выполнен: ${error.message}`
                    : error.message;
            if (operation !== 'login' && operation !== 'register') showNotification(error.message, 'system');
        }
        finally { busy = false; if (modal.isConnected) render(); }
    }

    async function loadFiles() {
        if (filesLoading) return;
        filesLoading = true;
        render();
        try { filesSnapshot = await Server.getCrafting(characterId); }
        catch (error) { showNotification(error.message, 'system'); }
        finally { filesLoading = false; if (modal.isConnected) render(); }
    }

    async function fileAction(operation, payload) {
        if (busy || !filesSnapshot) return;
        busy = true;
        render();
        try {
            const response = await Server.craft(characterId, {
                operation, operation_id: crypto.randomUUID(),
                character_revision: filesSnapshot.revision, ...payload,
            });
            await onSaved?.(response.data);
            filesSnapshot = await Server.getCrafting(characterId);
        } catch (error) { showNotification(error.message, 'system'); }
        finally { busy = false; if (modal.isConnected) render(); }
    }

    function render() {
        const current = findItem(item.id)?.item || item;
        const state = powerState(current);
        const power = modal.querySelector('[data-pda-power]');
        const accountButton = modal.querySelector('[data-pda-account]');
        const guestName = getData()?.basic?.name || getData()?.name || 'Персонаж';
        accountButton.textContent = accountSnapshot?.account
            ? `АККАУНТ: ${accountSnapshot.account.login}`
            : `АККАУНТ: ${guestName} (гость)${canEdit ? ' · Войти' : ''}`;
        accountButton.disabled = !state.on;
        accountButton.onclick = () => { selected = 'home'; render(); };
        modal.querySelector('[data-pda-status]').textContent = state.on ? 'ВКЛЮЧЁН' : 'ВЫКЛЮЧЕН';
        modal.querySelector('[data-pda-charge]').textContent = `ЗАРЯД ${state.charge.toFixed(1)}%`;
        power.textContent = state.on ? 'Выключить' : 'Включить';
        power.disabled = busy || !canEdit || (!state.on && state.charge <= 0);
        nav.replaceChildren();
        for (const [id, label] of sections) {
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = label;
            button.className = id === selected ? 'active' : '';
            button.disabled = !state.on;
            button.onclick = () => { selected = id; render(); if (id === 'files' || id === 'mods') void loadFiles(); };
            nav.append(button);
        }
        content.replaceChildren();
        if (!state.on) {
            heading.textContent = 'Экран выключен';
            const message = document.createElement('p');
            message.textContent = state.charge <= 0
                ? 'Батарейка разряжена. Заменить её можно в разделе оборудования.'
                : 'Включите КПК, чтобы открыть меню.';
            content.append(message);
            return;
        }
        if (selected === 'home') {
            heading.textContent = 'Главная';
            const lead = document.createElement('p');
            lead.className = 'pda-shell-lead';
            lead.textContent = 'Добро пожаловать!';
            const hint = document.createElement('p');
            hint.textContent = 'Выберите раздел слева.';
            content.append(lead, hint);
            if (accountLoading) {
                const loading = document.createElement('p');
                loading.textContent = 'Загрузка аккаунта…';
                content.append(loading);
            } else if (accountSnapshot?.account) {
                if (accountMessage) {
                    const message = document.createElement('p');
                    message.className = 'pda-account-success';
                    message.setAttribute('role', 'status');
                    message.textContent = accountMessage;
                    content.append(message);
                }
                const name = document.createElement('p');
                name.textContent = `Вы вошли как ${accountSnapshot.account.login}. Заметки аккаунта доступны с другого КПК в этом мире; книги остаются на устройстве.`;
                content.append(name);
                if (canEdit) {
                    const logout = document.createElement('button');
                    logout.type = 'button';
                    logout.className = 'btn btn-secondary';
                    logout.textContent = 'Выйти из аккаунта';
                    logout.disabled = busy;
                    logout.onclick = () => accountAction('logout');
                    content.append(logout);
                }
            } else if (canEdit) {
                const switcher = document.createElement('div');
                switcher.className = 'pda-account-switch';
                for (const [mode, label] of [['login', 'Вход'], ['register', 'Создать аккаунт']]) {
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = `btn btn-secondary${accountMode === mode ? ' active' : ''}`;
                    button.textContent = label;
                    button.setAttribute('aria-pressed', String(accountMode === mode));
                    button.disabled = busy;
                    button.onclick = () => { accountMode = mode; accountError = ''; render(); };
                    switcher.append(button);
                }
                const introduction = document.createElement('p');
                introduction.className = 'pda-account-introduction';
                introduction.textContent = accountMode === 'register'
                    ? 'Создайте аккаунт в этом мире. После создания вход выполнится автоматически.'
                    : 'Войдите в существующий аккаунт этого мира.';
                content.append(switcher, introduction);
                const form = document.createElement('form');
                form.className = 'pda-account-form';
                form.autocomplete = 'off';
                form.noValidate = true;
                const login = document.createElement('input');
                login.className = 'form-control';
                login.name = 'pda-account-alias';
                login.autocomplete = 'off';
                login.readOnly = true;
                login.onfocus = () => { login.readOnly = false; };
                login.maxLength = 32;
                login.placeholder = 'Логин';
                login.setAttribute('aria-label', 'Логин КПК');
                login.value = accountDraft.login;
                login.oninput = () => { accountDraft.login = login.value; accountError = ''; content.querySelector('[data-pda-account-error]')?.remove(); };
                const password = document.createElement('input');
                password.className = 'form-control';
                password.type = 'password';
                password.name = 'pda-account-secret';
                password.autocomplete = 'off';
                password.readOnly = true;
                password.onfocus = () => { password.readOnly = false; };
                password.maxLength = 128;
                password.placeholder = 'Пароль';
                password.setAttribute('aria-label', 'Пароль КПК');
                password.value = accountDraft.password;
                password.oninput = () => { accountDraft.password = password.value; accountError = ''; content.querySelector('[data-pda-account-error]')?.remove(); };
                const submit = () => {
                    accountDraft.login = login.value;
                    accountDraft.password = password.value;
                    accountError = '';
                    const normalizedLogin = login.value.normalize('NFKC').trim();
                    if (!/^[\p{L}\p{N}_.-]{3,32}$/u.test(normalizedLogin)) {
                        accountError = 'Логин должен содержать от 3 до 32 букв, цифр, точек, дефисов или подчёркиваний.';
                    } else if (password.value.length < 6 || password.value.length > 128) {
                        accountError = 'Пароль должен содержать от 6 до 128 символов.';
                    }
                    if (accountError) {
                        accountError = `${accountMode === 'register' ? 'Аккаунт не создан' : 'Вход не выполнен'}: ${accountError}`;
                        render();
                        return;
                    }
                    void accountAction(accountMode, { ...accountDraft });
                };
                const loginField = document.createElement('label');
                loginField.className = 'pda-account-field';
                loginField.append('Логин', login);
                const loginHelp = document.createElement('small');
                loginHelp.textContent = '3–32 символа: буквы, цифры, точка, дефис или _.';
                loginField.append(loginHelp);
                const passwordField = document.createElement('label');
                passwordField.className = 'pda-account-field';
                passwordField.append('Пароль', password);
                const passwordHelp = document.createElement('small');
                passwordHelp.textContent = 'От 6 до 128 символов.';
                passwordField.append(passwordHelp);
                form.append(loginField, passwordField);
                if (accountError) {
                    const error = document.createElement('p');
                    error.className = 'error-message pda-account-error';
                    error.dataset.pdaAccountError = '';
                    error.setAttribute('role', 'alert');
                    error.textContent = accountError;
                    form.append(error);
                }
                const button = document.createElement('button');
                button.type = 'submit';
                button.className = 'btn btn-secondary pda-account-submit';
                button.textContent = busy ? 'Подождите…' : accountMode === 'register' ? 'Создать аккаунт' : 'Войти';
                button.disabled = busy;
                form.append(button);
                form.onsubmit = event => { event.preventDefault(); submit(); };
                content.append(form);
            }
            return;
        }
        const section = sections.find(([id]) => id === selected);
        heading.textContent = section[1];
        const lead = document.createElement('p');
        lead.className = 'pda-shell-lead';
        lead.textContent = section[2];
        content.append(lead);
        if (selected === 'notes') {
            if (accountLoading) {
                content.append(document.createTextNode('Загрузка аккаунта…'));
            } else if (!accountSnapshot?.account) {
                const hint = document.createElement('p');
                hint.textContent = 'Войдите в аккаунт, чтобы сохранять заметки и читать их с другого КПК в этом мире.';
                const login = document.createElement('button');
                login.type = 'button';
                login.className = 'btn btn-secondary';
                login.textContent = 'Войти в аккаунт';
                login.onclick = () => { selected = 'home'; render(); };
                content.append(hint, login);
            } else {
                const account = document.createElement('p');
                account.textContent = `Аккаунт: ${accountSnapshot.account.login}`;
                const notes = document.createElement('textarea');
                notes.className = 'form-control pda-account-notes';
                notes.setAttribute('aria-label', 'Заметки аккаунта КПК');
                notes.maxLength = 10000;
                notes.value = accountSnapshot.account.notes || '';
                notes.readOnly = !canEdit;
                content.append(account, notes);
                if (canEdit) {
                    const save = document.createElement('button');
                    save.type = 'button';
                    save.className = 'btn btn-secondary';
                    save.textContent = 'Сохранить заметки';
                    save.disabled = busy;
                    save.onclick = () => accountAction('save_notes', { notes: notes.value });
                    content.append(save);
                }
            }
        } else if (selected === 'files') {
            if (!filesSnapshot || filesLoading) {
                const message = document.createElement('p');
                message.textContent = 'Загрузка файлов…';
                content.append(message);
                return;
            }
            const job = filesSnapshot.state?.current;
            if (job?.kind === 'book') {
                const title = document.createElement('strong');
                title.textContent = bookTitle(job.name);
                const status = document.createElement('p');
                status.textContent = job.remaining_seconds > 0
                    ? `Чтение идёт. Осталось ${Math.ceil(job.remaining_seconds / 60)} мин игрового времени.`
                    : 'Чтение завершится автоматически при обновлении игрового времени.';
                const cancel = document.createElement('button');
                cancel.type = 'button';
                cancel.className = 'btn btn-secondary';
                cancel.textContent = 'Прервать чтение';
                cancel.disabled = busy || !canEdit;
                cancel.onclick = () => fileAction('cancel', { job_id: job.id });
                content.append(title, status, cancel);
            } else if (job) {
                const status = document.createElement('p');
                status.textContent = 'Сначала завершите текущую работу.';
                content.append(status);
            } else {
                const pdaPath = findItem(item.id)?.path || [];
                const books = filesSnapshot.inventory.filter(entry => entry.item.attributes?.book_kind
                    && entry.path.length > pdaPath.length
                    && pdaPath.every((part, index) => entry.path[index] === part));
                const available = filesSnapshot.inventory.filter(entry => entry.item.attributes?.book_kind
                    && !books.some(stored => stored.item.id === entry.item.id));
                const cardList = document.createElement('div');
                cardList.className = 'pda-file-list';
                if (available.length && canEdit) {
                    const label = document.createElement('h4');
                    label.textContent = 'Книги в инвентаре';
                    content.append(label);
                    for (const entry of available) {
                        const card = document.createElement('article');
                        card.className = 'pda-file-card';
                        const name = document.createElement('strong');
                        name.textContent = bookTitle(entry.item.name);
                        const importButton = document.createElement('button');
                        importButton.type = 'button';
                        importButton.className = 'btn btn-secondary';
                        importButton.textContent = 'Записать в КПК';
                        importButton.disabled = busy;
                        importButton.onclick = async () => {
                            if (busy) return;
                            busy = true;
                            render();
                            try {
                                const current = findItem(item.id);
                                await operate({ operation: 'pda_import_book', path: current.path,
                                    source_id: current.item.id, book_path: entry.path });
                                filesSnapshot = await Server.getCrafting(characterId);
                            } catch (error) { showNotification(error.message, 'system'); }
                            finally { busy = false; if (modal.isConnected) render(); }
                        };
                        card.append(name, importButton);
                        cardList.append(card);
                    }
                    content.append(cardList);
                }
                if (!books.length) {
                    const empty = document.createElement('p');
                    empty.textContent = 'В КПК пока нет книг.';
                    content.append(empty);
                } else {
                    const label = document.createElement('h4');
                    label.textContent = 'Файлы КПК';
                    content.append(label);
                    const storedList = document.createElement('div');
                    storedList.className = 'pda-file-list';
                    for (const entry of books) {
                        const attrs = entry.item.attributes || {};
                        const card = document.createElement('article');
                        card.className = 'pda-file-card';
                        const name = document.createElement('strong');
                        name.textContent = bookTitle(entry.item.name);
                        card.append(name);
                        const assigned = filesSnapshot.state.book_assignments?.[entry.item.id];
                        let skillPath = attrs.book_skill_path || assigned || null;
                        if (attrs.book_kind === 'skill' && !skillPath) {
                            const subject = String(attrs.book_subject || '').toLowerCase();
                            const group = subject.includes('физическ') ? 'physical'
                                : subject.includes('социальн') ? 'social' : 'other';
                            const skills = filesSnapshot.skills.filter(row => row.path.startsWith(group + '.'));
                            const choices = document.createElement('div');
                            choices.className = 'pda-file-skills';
                            const selectedPath = selectedBookSkills.get(entry.item.id) || skills[0]?.path;
                            skillPath = selectedPath;
                            for (const row of skills) {
                                const choice = document.createElement('button');
                                choice.type = 'button';
                                choice.className = row.path === selectedPath ? 'active' : '';
                                choice.textContent = row.label;
                                choice.onclick = () => { selectedBookSkills.set(entry.item.id, row.path); render(); };
                                choices.append(choice);
                            }
                            card.append(choices);
                        }
                        const read = document.createElement('button');
                        read.type = 'button';
                        read.className = 'btn btn-secondary';
                        read.textContent = 'Читать · 4 часа';
                        read.disabled = busy || !canEdit || (attrs.book_kind === 'skill' && !skillPath);
                        read.onclick = () => fileAction('read_book', { path: entry.path, skill_path: skillPath });
                        card.append(read);
                        storedList.append(card);
                    }
                    content.append(storedList);
                }
            }
            const learned = filesSnapshot.state.library || [];
            if (learned.length) {
                const label = document.createElement('h4');
                label.textContent = 'Прочитано';
                content.append(label);
                for (const entry of learned) {
                    const row = document.createElement('p');
                    const title = bookTitle(entry.name);
                    row.textContent = title + (entry.skill_path && !title.includes('Раздел:')
                        ? ' · ' + (filesSnapshot.skills.find(skill => skill.path === entry.skill_path)?.label || '') : '');
                    content.append(row);
                }
            }
        } else if (selected === 'mods') {
            const recorded = (current.modifications || []).filter(mod => mod?.ruleId);
            const label = document.createElement('p');
            label.textContent = recorded.length ? 'Установленные модификации:' : 'Модификаций пока нет.';
            content.append(label);
            for (const mod of recorded) {
                const entry = document.createElement('div');
                entry.className = 'pda-shell-mod';
                const name = document.createElement('strong');
                name.textContent = mod.name;
                const effect = document.createElement('span');
                effect.textContent = mod.description || 'Описание не указано';
                entry.append(name, effect);
                content.append(entry);
            }
            if (!filesSnapshot || filesLoading) {
                const loading = document.createElement('p');
                loading.textContent = 'Загрузка каталога улучшений…';
                content.append(loading);
                return;
            }
            const job = filesSnapshot.state?.current;
            if (job?.kind === 'pda_modification') {
                const status = document.createElement('p');
                status.textContent = `${job.name}: ${job.remaining_seconds > 0
                    ? `осталось ${Math.ceil(job.remaining_seconds / 60)} мин игрового времени`
                    : 'установка готова к завершению'}`;
                content.append(status);
                const chosenCount = job.quote?.automatic_success || job.quote?.failure_loss?.all
                    ? 0 : Math.min(job.quote?.failure_loss?.chosen || 0,
                        (job.escrow || []).reduce((sum, entry) => sum + entry.amount, 0));
                const loss = document.createElement('select');
                if (chosenCount) {
                    loss.className = 'form-control';
                    loss.setAttribute('aria-label', 'Материал на случай неудачи');
                    for (const [index, entry] of (job.escrow || []).entries()) {
                        const option = document.createElement('option');
                        option.value = String(index);
                        option.textContent = `${entry.item.name} · ${entry.amount}`;
                        loss.append(option);
                    }
                    const hint = document.createElement('p');
                    hint.textContent = 'Выберите материал, который может быть потерян при неудаче.';
                    content.append(hint, loss);
                }
                for (const [operation, text] of [['finish', 'Завершить установку'], ['cancel', 'Отменить установку']]) {
                    const button = document.createElement('button');
                    button.type = 'button'; button.className = 'btn btn-secondary'; button.textContent = text;
                    button.disabled = busy || !canEdit || (operation === 'finish' && job.remaining_seconds > 0);
                    button.onclick = () => fileAction(operation, { job_id: job.id,
                        ...(operation === 'finish' ? {chosen_loss: chosenCount ? [Number(loss.value)] : []} : {}) });
                    content.append(button);
                }
                return;
            }
            if (job) {
                const status = document.createElement('p');
                status.textContent = 'Сначала завершите текущую работу.';
                content.append(status);
                return;
            }
            if (!canEdit) return;
            const available = (filesSnapshot.catalog?.modifications || []).filter(rule => rule.kind === 'pda'
                && !recorded.some(mod => mod.ruleId === rule.id));
            if (!available.length) return;
            if (!available.some(rule => rule.id === selectedMod)) selectedMod = available[0].id;
            const choice = document.createElement('select');
            choice.className = 'form-control'; choice.setAttribute('aria-label', 'Модификация КПК');
            for (const rule of available) {
                const option = document.createElement('option');
                option.value = rule.id; option.textContent = `${rule.name} · ${rule.difficulty_group}`;
                choice.append(option);
            }
            choice.value = selectedMod;
            choice.onchange = () => { selectedMod = choice.value; pdaQuote = null; render(); };
            const rule = available.find(entry => entry.id === selectedMod);
            const description = document.createElement('p');
            description.textContent = rule.description;
            const tool = document.createElement('select');
            tool.className = 'form-control'; tool.setAttribute('aria-label', 'Инструмент для КПК');
            const station = document.createElement('option');
            station.value = ''; station.textContent = 'Станок Электрика поблизости'; tool.append(station);
            const solder = filesSnapshot.inventory.filter(entry => /паяльные инструменты/i.test(entry.item.name)
                && entry.uses > 0);
            solder.forEach((entry, index) => {
                const option = document.createElement('option');
                option.value = String(index + 1); option.textContent = `${entry.item.name} · ${entry.uses} зарядов`;
                tool.append(option);
            });
            if (selectedTool && !solder[Number(selectedTool) - 1]) selectedTool = '';
            tool.value = selectedTool;
            tool.onchange = () => { selectedTool = tool.value; pdaQuote = null; render(); };
            const payload = () => ({ target_path: findItem(item.id)?.path, item_id: item.id,
                rule_id: selectedMod, tool_path: selectedTool ? solder[Number(selectedTool) - 1]?.path : null });
            const check = document.createElement('button');
            check.type = 'button'; check.className = 'btn btn-secondary'; check.textContent = 'Проверить установку';
            check.disabled = busy;
            check.onclick = async () => {
                busy = true; pdaQuote = null; render();
                try {
                    const response = await Server.craft(characterId, {operation: 'quote_pda_modification', ...payload()});
                    pdaQuote = response.result;
                } catch (error) { showNotification(error.message, 'system'); }
                finally { busy = false; if (modal.isConnected) render(); }
            };
            content.append(choice, description, tool, check);
            if (pdaQuote) {
                const status = document.createElement('p');
                status.textContent = pdaQuote.missing?.length ? pdaQuote.missing.join('; ')
                    : `СЛ ${pdaQuote.difficulty}; время 1 час. При неудаче материалы могут быть потеряны.`;
                content.append(status);
                if (!pdaQuote.missing?.length) {
                    const install = document.createElement('button');
                    install.type = 'button'; install.className = 'btn btn-primary'; install.textContent = 'Начать установку';
                    install.disabled = busy;
                    install.onclick = async () => {
                        const request = payload(); pdaQuote = null;
                        await fileAction('install_pda_modification', request);
                    };
                    content.append(install);
                }
            }
        } else {
            const note = document.createElement('p');
            note.className = 'pda-shell-note';
            note.textContent = 'Раздел готов к подключению. Его функции пока недоступны.';
            content.append(note);
        }
    }

    modal.querySelector('[data-pda-power]').onclick = async () => {
        const current = findItem(item.id);
        if (!current || busy) return;
        busy = true;
        render();
        try { await operate({ operation: 'pda_toggle', path: current.path, source_id: current.item.id ?? null }); }
        finally {
            busy = false;
            if (modal.isConnected) {
                render();
                if (selected === 'files') void loadFiles();
            }
        }
    };
    const onTimeUpdated = () => { if (selected === 'files' && modal.isConnected) void loadFiles(); };
    window.addEventListener('pda-game-time-updated', onTimeUpdated);
    const close = () => {
        window.removeEventListener('pda-game-time-updated', onTimeUpdated);
        modal.remove();
    };
    modal.pdaClose = close;
    modal.querySelector('[data-pda-close]').onclick = close;
    modal.onclick = event => { if (event.target === modal) close(); };
    render();
    void loadAccount();
    return modal;
}
