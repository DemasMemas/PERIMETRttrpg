import { Server } from './api.js';
import { showNotification } from './utils.js';
import { createPdaNetworkView } from './pdaNetworkView.js';
import { renderRemoteCharges } from './itemControllers.js';
import {createPdaAccessView} from './pdaAccessView.js';
import {bookTitle,createBookCard} from './pdaBookFile.js';
import {createPdaWipeControl} from './pdaWipeControl.js';

const sections = [
    ['chat', 'Общий чат', 'Сообщения участников Зоны'],
    ['contacts', 'Контакты', 'Люди и каналы связи'],
    ['instruments', 'Показания приборов', 'Мини-осциллограф'],
    ['charges', 'Заряды', 'Дистанционные взрыватели'],
    ['access', 'Подключения', 'Кабельное и дистанционное управление'],
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
    return { on: saved.on !== false&&!saved.broken, broken:Boolean(saved.broken),charge: Number.isFinite(charge) ? Math.max(0, Math.min(100, charge)) : 0 };
}

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
    let networkView=null;
    let accessView=null;
    let pdaQuote = null;
    let accountSnapshot = null;
    let accountLoading = true;
    let accountError = '';
    let accountMessage = '';
    let accountMode = 'login';
    const accountDraft = { login: '', password: '' };
    const selectedBookSkills = new Map();
    const accountSelection=()=>{
        const source=findItem(item.id);if(!source)throw Error('КПК перемещён. Откройте его заново.');
        return {item_path:source.path,item_id:source.item.id};
    };

    async function loadAccount() {
        try { accountSnapshot = await Server.getPdaAccount(characterId,accountSelection()); }
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
            await Server.waitForCharacterSave(characterId);
            if(operation==='switch_profile'){
                const source=findItem(item.id);
                await operate({operation:'pda_profile',path:source.path,source_id:source.item.id,profile:payload.profile});
                accountSnapshot=await Server.getPdaAccount(characterId,accountSelection());
            }else accountSnapshot = await Server.changePdaAccount(characterId, operation, {...accountSelection(),...payload});
            if(accountSnapshot.data)await onSaved?.(accountSnapshot.data);
            if(['login','register','logout','switch_profile'].includes(operation)){networkView?.close();networkView=null;accountDraft.password='';}
            if(operation==='switch_profile')accountDraft.login='';
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
        if(!item.id&&current.id)item.id=current.id;
        const state = powerState(current);
        networkView?.setActive(state.on&&['chat','contacts','instruments'].includes(selected),selected);
        accessView?.setActive(state.on&&selected==='access');
        const power = modal.querySelector('[data-pda-power]');
        const accountButton = modal.querySelector('[data-pda-account]');
        const guestName = getData()?.basic?.name || getData()?.name || 'Персонаж';
        accountButton.textContent = accountSnapshot?.account
            ? `АККАУНТ: ${accountSnapshot.account.login}`
            : `АККАУНТ: ${guestName} (гость)${canEdit ? ' · Войти' : ''}`;
        accountButton.disabled = !state.on;
        accountButton.onclick = () => { selected = 'home'; render(); };
        modal.querySelector('[data-pda-status]').textContent = state.broken?'НЕИСПРАВЕН':state.on ? 'ВКЛЮЧЁН' : 'ВЫКЛЮЧЕН';
        modal.querySelector('[data-pda-charge]').textContent = `ЗАРЯД ${state.charge.toFixed(1)}%`;
        power.textContent = state.on ? 'Выключить' : 'Включить';
        power.disabled = busy || !canEdit || state.broken || (!state.on && state.charge <= 0);
        nav.replaceChildren();
        for (const [id, label] of sections) {
            if(id==='instruments'&&!(current.modifications||[]).some(mod=>mod.ruleId==='pda-b6'))continue;
            const button = document.createElement('button');
            button.type = 'button';
            button.textContent = label;
            button.className = id === selected ? 'active' : '';
            button.disabled = !state.on;
            button.onclick = () => { selected = id; render(); if (id === 'files' || id === 'mods') void loadFiles(); };
            nav.append(button);
        }
        content.replaceChildren();
        if (state.on && accountSnapshot?.can_bind_profile_owner) {
            const bind = document.createElement('button');
            bind.type = 'button';
            bind.className = 'btn btn-secondary';
            bind.textContent = 'Закрепить второй профиль за персонажем';
            bind.disabled = busy || !canEdit;
            bind.onclick = () => accountAction('bind_profile_owner', {});
            content.append(bind);
        }
        if(state.on&&accountSnapshot?.profiles){
            const profiles=document.createElement('nav');profiles.className='pda-access-tabs';profiles.setAttribute('aria-label','Профили КПК');
            for(const profile of accountSnapshot.profiles){
                const button=document.createElement('button');button.type='button';button.className='btn btn-secondary';
                button.textContent=profile.label;button.dataset.pdaProfile=profile.id;
                button.setAttribute('aria-current',accountSnapshot.profile===profile.id?'true':'false');
                button.disabled=busy||!canEdit||accountSnapshot.profile===profile.id;
                button.onclick=()=>accountAction('switch_profile',{profile:profile.id});profiles.append(button);
            }
            content.append(profiles);
        }
        modal.querySelector('[data-pda-hold]')?.remove();
        const holding=getData()?.health?.combatMeta?.heldPda?.id===current.id&&Boolean(current.id);
        const hold=document.createElement('button');hold.type='button';hold.dataset.pdaHold='';hold.className='btn btn-secondary';
        hold.textContent=holding?'Убрать КПК':'Взять КПК в руку';hold.disabled=busy||!canEdit;
        hold.onclick=async()=>{
            const source=findItem(item.id);if(!source||busy)return;busy=true;render();
            try{await operate({operation:holding?'pda_stow':'pda_hold',path:source.path,source_id:source.item.id});}
            finally{busy=false;if(modal.isConnected)render();}
        };
        modal.querySelector('.pda-shell-footer').append(hold);
        modal.querySelector('[data-pda-dynamo]')?.remove();
        if((current.modifications||[]).some(mod=>mod.ruleId==='pda-b7')){
            const charging=getData()?.health?.combatMeta?.pdaDynamo?.id===current.id;
            const dynamo=document.createElement('button');dynamo.type='button';dynamo.dataset.pdaDynamo='';dynamo.className='btn btn-secondary';
            dynamo.textContent=charging?'Прекратить зарядку':'Крутить динамо-машину · 1%/мин';dynamo.disabled=busy||!canEdit||state.broken||!charging&&state.charge>=100;
            dynamo.onclick=async()=>{
                const source=findItem(item.id);if(!source||busy)return;
                busy=true;render();
                try{await operate({operation:charging?'pda_dynamo_stop':'pda_dynamo_start',path:source.path,source_id:source.item.id});}
                finally{busy=false;if(modal.isConnected)render();}
            };
            modal.querySelector('.pda-shell-footer').append(dynamo);
        }
        if (!state.on) {
            heading.textContent = 'Экран выключен';
            const message = document.createElement('p');
            message.textContent = state.broken?'КПК вышел из строя.':state.charge <= 0
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
            content.append(lead);
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
                name.textContent = `Аккаунт: ${accountSnapshot.account.login}`;
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
                const title = document.createElement('h3');
                title.className = 'pda-account-title';
                title.textContent = accountMode === 'register' ? 'Регистрация' : 'Вход в аккаунт';
                content.append(title);
                if (accountMode === 'register') {
                    const rules = document.createElement('p');
                    rules.className = 'pda-account-rules';
                    rules.textContent = 'Логин: 3–32 символа (буквы, цифры, точка, дефис или _). Пароль: 6–128 символов. После регистрации вход выполнится автоматически.';
                    content.append(rules);
                }
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
                const passwordField = document.createElement('label');
                passwordField.className = 'pda-account-field';
                passwordField.append('Пароль', password);
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
                const switcher = document.createElement('p');
                switcher.className = 'pda-account-switch';
                switcher.append(accountMode === 'register' ? 'Уже есть аккаунт? ' : 'Нет аккаунта? ');
                const switchButton = document.createElement('button');
                switchButton.type = 'button';
                switchButton.className = 'pda-account-mode-link';
                switchButton.textContent = accountMode === 'register' ? 'Перейти ко входу' : 'Перейти к регистрации';
                switchButton.disabled = busy;
                switchButton.onclick = () => { accountMode = accountMode === 'register' ? 'login' : 'register'; accountError = ''; render(); };
                switcher.append(switchButton);
                content.append(switcher);
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
        } else if (selected === 'access') {
            if(!accessView)accessView=createPdaAccessView({characterId,selection:accountSelection,onSaved,canEdit,getData});
            content.append(accessView.element);accessView.setActive(true);
        } else if (selected === 'files') {
            if (!filesSnapshot || filesLoading) {
                const message = document.createElement('p');
                message.textContent = 'Загрузка файлов…';
                content.append(message);
                return;
            }
            const pdaPath = findItem(item.id)?.path || [];
            const storedBooks = filesSnapshot.inventory.filter(entry => entry.item.attributes?.book_kind
                && entry.path.length > pdaPath.length
                && pdaPath.every((part, index) => entry.path[index] === part));
            const books=storedBooks.filter(entry=>!accountSnapshot?.profile
                || (accountSnapshot.file_profiles?.[entry.item.id]||'main')===accountSnapshot.profile);
            const job = filesSnapshot.state?.current;
            if (job?.kind === 'book'&&(!accountSnapshot?.profile||books.some(entry=>entry.item.id===job.book_id))) {
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
                if(pdaQuote?.station_name){
                    const station=document.createElement('p');
                    station.textContent=`${pdaQuote.station_name}: один заряд инструмента; риск порчи результата ${pdaQuote.spoil_chance}%.`;
                    content.append(station);
                }
            } else {
                const available = filesSnapshot.inventory.filter(entry => entry.item.attributes?.book_kind
                    && !storedBooks.some(stored => stored.item.id === entry.item.id));
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
                        const card=createBookCard(entry,{skills:filesSnapshot.skills,assignments:filesSnapshot.state.book_assignments,
                            selectedSkills:selectedBookSkills,learned:filesSnapshot.state.library,canEdit,busy,onSkillChange:render,
                            onRead:skillPath=>fileAction('read_book',{path:entry.path,skill_path:skillPath})});
                        if(accountSnapshot?.profiles&&canEdit){
                            const move=document.createElement('button');move.type='button';move.className='btn btn-secondary';
                            const profile=accountSnapshot.profile==='main'?'decoy':'main';
                            move.textContent=profile==='main'?'Перенести в основной профиль':'Перенести в ложный профиль';move.disabled=busy;
                            move.onclick=()=>accountAction('move_file',{file_id:entry.item.id,profile});card.append(move);
                        }
                        storedList.append(card);
                    }
                    content.append(storedList);
                }
            }
            const learned = (filesSnapshot.state.library || []).filter(entry=>!accountSnapshot?.profile
                || books.some(book=>book.item.name===entry.name));
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
        } else if(selected==='chat'||selected==='contacts'||selected==='instruments'){
            heading.textContent=selected==='chat'?'Радиосвязь':selected==='instruments'?'Показания приборов':'Контакты';
            networkView ||= createPdaNetworkView({characterId,findSource:()=>findItem(item.id),getData,operateHardware:operate,canEdit});
            content.append(networkView.element);networkView.setActive(true,selected);
        } else if(selected==='charges'){
            heading.textContent='Заряды';
            renderRemoteCharges(content,{characterId,selection:()=>{const source=findItem(item.id);return {
                item_path:source?.path,item_id:source?.item.id,...(window.isLocationActive?{location_id:window.currentLocationId}:{})};},onSaved});
        } else if (selected === 'mods') {
            const recorded = (current.modifications || []).filter(mod => mod?.ruleId);
            if(canEdit&&recorded.some(mod=>mod.ruleId==='pda-b14'))content.append(createPdaWipeControl({disabled:busy,hasProfiles:recorded.some(mod=>mod.ruleId==='pda-b15'),onWipe:async()=>{
                const source=findItem(item.id);if(busy||!source)return;busy=true;render();
                try{
                    await operate({operation:'pda_wipe',path:source.path,source_id:source.item.id});
                    networkView?.close();networkView=null;accessView?.close();accessView=null;
                    accountSnapshot=await Server.getPdaAccount(characterId,accountSelection());
                    filesSnapshot=await Server.getCrafting(characterId);accountMessage='Данные КПК удалены.';
                    showNotification('Данные КПК удалены','system');
                }catch(error){showNotification(error.message,'system');}
                finally{busy=false;if(modal.isConnected)render();}
            }}));
            const label = document.createElement('p');
            label.textContent = recorded.length ? 'Установленные модификации:' : 'Модификаций пока нет.';
            content.append(label);
            for (const mod of recorded) {
                const entry = document.createElement('div');
                entry.className = 'pda-shell-mod';
                const name = document.createElement('strong');
                name.textContent = mod.name;
                const effect = document.createElement('span');
                const revisedDescription = ['pda-b21', 'pda-b22'].includes(mod.ruleId)
                    ? filesSnapshot?.catalog?.modifications?.find(rule => rule.id === mod.ruleId)?.description
                    : null;
                effect.textContent = revisedDescription || mod.description || 'Описание не указано';
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
    const onTimeUpdated = async () => {
        if(!modal.isConnected||busy)return;
        try{
            const fresh=await Server.getCharacter(characterId);if(!modal.isConnected||busy)return;
            await onSaved?.(fresh.data);render();
            if(selected==='files')void loadFiles();
        }catch(error){if(modal.isConnected)showNotification(error.message,'system');}
    };
    window.addEventListener('pda-game-time-updated', onTimeUpdated);
    const onAccessUpdated=async()=>{
        if(busy||!modal.isConnected)return;
        await onTimeUpdated();
        if(!busy&&modal.isConnected)void loadAccount();
    };
    window.addEventListener('pda-access-updated',onAccessUpdated);
    const close = () => {
        networkView?.close();
        accessView?.close();
        window.removeEventListener('pda-game-time-updated', onTimeUpdated);
        window.removeEventListener('pda-access-updated',onAccessUpdated);
        modal.remove();
    };
    modal.pdaClose = close;
    modal.querySelector('[data-pda-close]').onclick = close;
    modal.onclick = event => { if (event.target === modal) close(); };
    render();
    void loadAccount();
    return modal;
}
