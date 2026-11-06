const sections = [
    ['chat', 'Общий чат', 'Сообщения участников Зоны'],
    ['contacts', 'Контакты', 'Люди и каналы связи'],
    ['gallery', 'Галерея', 'Фото, видео и аудио'],
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

export function openPdaShell({ item, findItem, getData, operate, canEdit }) {
    document.getElementById('pda-shell-modal')?.remove();
    const modal = document.createElement('div');
    modal.id = 'pda-shell-modal';
    modal.className = 'modal pda-shell-modal';
    modal.innerHTML = `
        <div class="modal-content pda-shell" role="dialog" aria-modal="true" aria-label="КПК">
            <div class="pda-shell-bezel">
                <div class="pda-shell-brand"><span class="pda-shell-light"></span> ZONE / КПК <span class="pda-shell-model">FIELD TERMINAL · 01</span></div>
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
            button.onclick = () => { selected = id; render(); };
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
        if (selected === 'mods') {
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
