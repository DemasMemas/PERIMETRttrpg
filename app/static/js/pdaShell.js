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

export function openPdaShell({ item, findItem, getData, operate, canEdit, characterId, onSaved }) {
    document.getElementById('pda-shell-modal')?.remove();
    const modal = document.createElement('div');
    modal.id = 'pda-shell-modal';
    modal.className = 'modal pda-shell-modal';
    modal.innerHTML = `
        <div class="modal-content pda-shell" role="dialog" aria-modal="true" aria-label="КПК">
            <div class="pda-shell-bezel">
                <div class="pda-shell-brand"><span class="pda-shell-light"></span> PERIMETR / КПК <span class="pda-shell-model">FIELD TERMINAL · 01</span></div>
                <div class="pda-shell-screen">
                    <header class="pda-shell-header"><div><small>ПЕРСОНАЛЬНЫЙ ТЕРМИНАЛ</small><strong data-pda-heading>Главная</strong></div><div class="pda-shell-power"><span data-pda-status></span><button type="button" data-pda-power aria-label="Переключить питание"></button></div></header>
                    <div class="pda-shell-content"><nav class="pda-shell-nav" aria-label="Разделы КПК"></nav><main class="pda-shell-main" data-pda-content></main></div>
                    <footer class="pda-shell-footer"><span>КПК / ПЕРВЫЙ ЭТАП</span><span data-pda-charge></span></footer>
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
            button.onclick = () => { selected = id; render(); if (id === 'files') void loadFiles(); };
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
            lead.textContent = 'Личный терминал сталкера';
            const hint = document.createElement('p');
            hint.textContent = 'Выберите раздел слева. Функции разделов появятся в следующих этапах.';
            content.append(lead, hint);
            return;
        }
        const section = sections.find(([id]) => id === selected);
        heading.textContent = section[1];
        const lead = document.createElement('p');
        lead.className = 'pda-shell-lead';
        lead.textContent = section[2];
        content.append(lead);
        if (selected === 'files') {
            if (!filesSnapshot || filesLoading) {
                const message = document.createElement('p');
                message.textContent = 'Загрузка файлов…';
                content.append(message);
                return;
            }
            const job = filesSnapshot.state?.current;
            if (job?.kind === 'book') {
                const title = document.createElement('strong');
                title.textContent = job.name;
                const status = document.createElement('p');
                status.textContent = job.remaining_seconds > 0
                    ? `Чтение идёт. Осталось ${Math.ceil(job.remaining_seconds / 60)} мин игрового времени.`
                    : 'Чтение можно завершить.';
                const finish = document.createElement('button');
                finish.type = 'button';
                finish.className = 'btn btn-secondary';
                finish.textContent = 'Завершить чтение';
                finish.disabled = busy || !canEdit || job.remaining_seconds > 0;
                finish.onclick = () => fileAction('finish', { job_id: job.id });
                const cancel = document.createElement('button');
                cancel.type = 'button';
                cancel.className = 'btn btn-secondary';
                cancel.textContent = 'Прервать чтение';
                cancel.disabled = busy || !canEdit;
                cancel.onclick = () => fileAction('cancel', { job_id: job.id });
                content.append(title, status, finish, cancel);
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
                if (available.length && canEdit) {
                    const upload = document.createElement('select');
                    upload.className = 'form-control';
                    upload.setAttribute('aria-label', 'Книга для записи в КПК');
                    available.forEach((entry, index) => {
                        const option = document.createElement('option');
                        option.value = String(index);
                        option.textContent = entry.item.name;
                        upload.append(option);
                    });
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
                                source_id: current.item.id, book_path: available[Number(upload.value)].path });
                            filesSnapshot = await Server.getCrafting(characterId);
                        } finally { busy = false; if (modal.isConnected) render(); }
                    };
                    content.append(upload, importButton);
                }
                if (!books.length) {
                    const empty = document.createElement('p');
                    empty.textContent = 'В КПК пока нет книг.';
                    content.append(empty);
                } else {
                    const book = document.createElement('select');
                    book.className = 'form-control';
                    book.setAttribute('aria-label', 'Электронная книга');
                    books.forEach((entry, index) => {
                        const option = document.createElement('option');
                        option.value = String(index);
                        option.textContent = entry.item.name;
                        book.append(option);
                    });
                    const skill = document.createElement('select');
                    skill.className = 'form-control';
                    skill.setAttribute('aria-label', 'Навык книги');
                    const updateSkill = () => {
                        const entry = books[Number(book.value)];
                        const attrs = entry.item.attributes;
                        skill.replaceChildren();
                        skill.hidden = attrs.book_kind !== 'skill';
                        if (skill.hidden) return;
                        const subject = String(attrs.book_subject || '').toLowerCase();
                        const group = subject.includes('физическ') ? 'physical'
                            : subject.includes('социальн') ? 'social' : 'other';
                        for (const row of filesSnapshot.skills.filter(row => row.path.startsWith(group + '.'))) {
                            const option = document.createElement('option');
                            option.value = row.path;
                            option.textContent = row.label;
                            skill.append(option);
                        }
                        const assigned = filesSnapshot.state.book_assignments?.[entry.item.id];
                        if (assigned) skill.value = assigned;
                        skill.disabled = busy || !canEdit || Boolean(assigned);
                    };
                    book.onchange = updateSkill;
                    updateSkill();
                    const read = document.createElement('button');
                    read.type = 'button';
                    read.className = 'btn btn-secondary';
                    read.textContent = 'Читать · 4 часа';
                    read.disabled = busy || !canEdit;
                    read.onclick = () => fileAction('read_book', {
                        path: books[Number(book.value)].path,
                        skill_path: skill.hidden ? null : skill.value,
                    });
                    content.append(book, skill, read);
                }
            }
            const learned = filesSnapshot.state.library || [];
            if (learned.length) {
                const label = document.createElement('h4');
                label.textContent = 'Прочитано';
                content.append(label);
                for (const entry of learned) {
                    const row = document.createElement('p');
                    row.textContent = entry.name + (entry.skill_path
                        ? ' · ' + (filesSnapshot.skills.find(skill => skill.path === entry.skill_path)?.label || '') : '');
                    content.append(row);
                }
            }
        } else if (selected === 'mods') {
            const mods = getData()?.modifications?.pda?.items || [];
            const recorded = mods.filter(mod => String(mod?.name || '').trim());
            const label = document.createElement('p');
            label.textContent = recorded.length ? 'Записанные модификации:' : 'Модификаций пока нет.';
            content.append(label);
            for (const mod of recorded) {
                const entry = document.createElement('div');
                entry.className = 'pda-shell-mod';
                const name = document.createElement('strong');
                name.textContent = mod.name;
                const effect = document.createElement('span');
                effect.textContent = mod.effect || 'Описание не указано';
                entry.append(name, effect);
                content.append(entry);
            }
            const note = document.createElement('p');
            note.className = 'pda-shell-note';
            note.textContent = 'Установка и действия модификаций появятся в следующем этапе.';
            content.append(note);
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
        finally { busy = false; if (modal.isConnected) render(); }
    };
    const close = () => modal.remove();
    modal.querySelector('[data-pda-close]').onclick = close;
    modal.onclick = event => { if (event.target === modal) close(); };
    render();
    return modal;
}
