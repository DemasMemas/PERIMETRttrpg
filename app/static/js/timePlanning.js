import { Server } from './api.js';
import { showNotification } from './utils.js';

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;',
})[character]);

export function clockLabel(absoluteMinute) {
    const minute = Math.max(0, Math.floor(Number(absoluteMinute) || 0));
    const day = Math.floor(minute / 1440);
    const time = minute % 1440;
    return `Сутки ${day}, ${String(Math.floor(time / 60)).padStart(2, '0')}:${String(time % 60).padStart(2, '0')}`;
}

export function timelineSegments(plan, activities) {
    let elapsed = 0;
    const segments = activities.map(activity => {
        const duration = Math.max(0, Number(activity.duration_minutes) || 0);
        const segment = { ...activity, start:elapsed, end:elapsed + duration,
            over:elapsed + duration > plan.window_minutes };
        elapsed += duration;
        return segment;
    });
    if (elapsed < plan.window_minutes) segments.push({id:'free',name:'Свободно',start:elapsed,
        end:plan.window_minutes,duration_minutes:plan.window_minutes-elapsed,free:true});
    return {segments,occupied:elapsed,free:Math.max(0,plan.window_minutes-elapsed),
        overflow:Math.max(0,elapsed-plan.window_minutes),
        total:Math.max(1,elapsed,plan.window_minutes)};
}

function timelineMarkup(plan, activities) {
    const data = timelineSegments(plan, activities);
    const pieces = data.segments.map(segment => {
        const start = segment.start / data.total * 100;
        const width = (segment.end - segment.start) / data.total * 100;
        const title = `${segment.name}: ${clockLabel(plan.start_minute + segment.start)} — ${clockLabel(plan.start_minute + segment.end)}`;
        return `<div class="time-plan-segment ${segment.free ? 'is-free' : segment.over ? 'is-over' : segment.approved ? 'is-approved' : ''}"
            style="left:${start}%;width:${width}%" title="${escapeHtml(title)}"><span>${escapeHtml(segment.name)}</span></div>`;
    }).join('');
    const limit = plan.window_minutes / data.total * 100;
    return `<div class="time-plan-track" role="img" aria-label="Занято ${data.occupied} минут, свободно ${data.free} минут, превышение ${data.overflow} минут">
        ${pieces}<i class="time-plan-limit" style="left:${limit}%" title="Граница выделенного времени"></i></div>
        <div class="time-plan-scale"><span>${clockLabel(plan.start_minute)}</span>
        <strong>Занято ${data.occupied} мин · Свободно ${data.free} мин${data.overflow ? ` · Превышение ${data.overflow} мин` : ''}</strong>
        <span>${clockLabel(plan.start_minute + Math.max(data.occupied,plan.window_minutes))}</span></div>`;
}

export function initTimePlanning(lobbyId, socket) {
    const modal = document.getElementById('time-plans-modal');
    const button = document.getElementById('time-plans-btn');
    const list = document.getElementById('time-plans-list');
    const createRoot = document.getElementById('time-plans-create');
    if (!modal || !button || !list || !createRoot) return;
    let state = {plans:[],owners:[],skills:{},is_gm:false};
    let busy = false;
    let initialShown = false;
    let requestId = null;
    const drafts = new Map();
    const createDraft = {owners:new Set(),minutes:120};
    const planById = id => state.plans.find(plan => plan.id === Number(id));

    function renderCreate() {
        createRoot.hidden = !state.is_gm;
        if (!state.is_gm) return;
        createRoot.innerHTML = `<div class="time-plan-request"><h3>Запросить распределение времени</h3>
            <div class="time-plan-owners">${state.owners.map(owner => `<label><input type="checkbox" data-owner-id="${owner.id}"
                ${createDraft.owners.has(owner.id) ? 'checked' : ''}> ${escapeHtml(owner.name)}</label>`).join('')}</div>
            <label>Окно, минут <input id="time-plan-window" type="number" min="1" max="10080" value="${createDraft.minutes}"></label>
            <button type="button" class="btn btn-primary" data-time-action="create">Отправить запрос</button></div>`;
    }

    function activityMarkup(plan, entry, index) {
        const checked = entry.approved ? '<span class="time-plan-approved">Утверждено</span>' : '';
        const result = entry.roll ? `<span class="time-plan-roll-result">${escapeHtml(entry.roll.skill_label)}: ${entry.roll.failure_reason
            ? escapeHtml(entry.roll.failure_reason) : `${entry.roll.total} / СЛ ${entry.roll.difficulty} — ${entry.roll.success ? 'успех' : 'провал'}`}</span>` : '';
        if (state.is_gm) {
            const beyondWindow = plan.activities.slice(0, index + 1)
                .reduce((minutes, activity) => minutes + activity.duration_minutes, 0) > plan.window_minutes;
            const waitForOverflow = beyondWindow && plan.overflow_state !== 'approved';
            const skillOptions = Object.entries(state.skills).map(([key,label]) =>
                `<option value="${escapeHtml(key)}">${escapeHtml(label)}</option>`).join('');
            return `<div class="time-plan-activity-row" data-activity-id="${escapeHtml(entry.id)}">
                <div><strong>${index + 1}. ${escapeHtml(entry.name)}</strong> · ${entry.duration_minutes} мин ${checked}
                    ${entry.note ? `<p>${escapeHtml(entry.note)}</p>` : ''}${result}</div>
                ${plan.confirmed ? `<div class="time-plan-row-actions">
                    <button type="button" class="btn btn-sm btn-secondary" data-time-action="${entry.approved ? 'deny_activity' : 'approve_activity'}" ${entry.roll || waitForOverflow ? 'disabled' : ''} ${waitForOverflow ? 'title="Сначала согласуйте превышение времени"' : ''}>${entry.approved ? 'Снять утверждение' : 'Утвердить'}</button>
                    ${entry.approved && !entry.roll ? `<select data-roll-skill aria-label="Навык">${skillOptions}</select>
                    <input data-roll-difficulty type="number" min="1" max="40" value="10" aria-label="Сложность">
                    <button type="button" class="btn btn-sm btn-primary" data-time-action="roll">Бросить</button>` : ''}
                </div>` : ''}</div>`;
        }
        return `<div class="time-plan-activity-row" data-activity-id="${escapeHtml(entry.id)}">
            <span class="time-plan-index">${index + 1}</span>
            <input data-time-field="name" maxlength="120" value="${escapeHtml(entry.name)}" aria-label="Активность">
            <input data-time-field="duration" type="number" min="1" max="10080" value="${entry.duration_minutes}" aria-label="Минуты">
            <input data-time-field="note" maxlength="1000" value="${escapeHtml(entry.note || '')}" placeholder="Пояснение" aria-label="Пояснение">
            ${checked}${result}<button type="button" class="btn btn-sm btn-secondary" data-time-action="remove" ${entry.approved ? 'disabled' : ''}>×</button>
        </div>`;
    }

    function renderPlan(plan) {
        const owner = state.owners.find(person => person.id === plan.owner_id);
        const draft = drafts.get(plan.id);
        const entries = draft ? draft.map(entry => {
            const stored = plan.activities.find(item => item.id === entry.id);
            return {...entry,approved:stored?.approved,roll:stored?.roll};
        }) : plan.activities;
        const status = !plan.confirmed ? 'Черновик' : plan.overflow_state === 'pending' ? 'Ожидает согласования превышения'
            : plan.overflow_state === 'denied' ? 'Превышение отклонено' : 'Подтверждён';
        let elapsed = 0;
        const approvedBeyondWindow = plan.activities.some(entry => {
            elapsed += entry.duration_minutes;
            return elapsed > plan.window_minutes && entry.approved;
        });
        return `<section class="time-plan-card" data-plan-id="${plan.id}">
            <header><div><h3>${escapeHtml(plan.character_name)}</h3>
                ${owner ? `<small>Владелец: ${escapeHtml(owner.name)}</small>` : ''}</div>
                <span class="time-plan-status">${status}</span></header>
            <p>Выделено ${plan.window_minutes} мин · ${clockLabel(plan.start_minute)} — ${clockLabel(plan.start_minute + plan.window_minutes)}</p>
            <div class="time-plan-timeline">${timelineMarkup(plan,entries)}</div>
            <div class="time-plan-activity-list">${entries.map((entry,index) => activityMarkup(plan,entry,index)).join('')}</div>
            ${state.is_gm ? `<div class="time-plan-actions">
                ${plan.confirmed && plan.overflow_minutes ? `<button type="button" class="btn btn-sm btn-primary" data-time-action="approve_overflow" ${plan.overflow_state === 'approved' ? 'disabled' : ''}>Согласовать превышение</button>
                <button type="button" class="btn btn-sm btn-secondary" data-time-action="deny_overflow" ${plan.overflow_state === 'denied' || approvedBeyondWindow ? 'disabled title="Сначала снимите утверждение активности сверх лимита"' : ''}>Отклонить превышение</button>` : ''}</div>`
                : `<div class="time-plan-actions"><button type="button" class="btn btn-sm btn-secondary" data-time-action="add">+ Активность</button>
                ${plan.activities.some(entry => entry.approved) ? '' : '<button type="button" class="btn btn-sm btn-secondary" data-time-action="save">Сохранить черновик</button>'}
                <button type="button" class="btn btn-sm btn-primary" data-time-action="confirm">Подтвердить план</button></div>`}
        </section>`;
    }

    function render() {
        renderCreate();
        list.innerHTML = state.plans.length ? state.plans.map(renderPlan).join('') : '<p>Запросов времени пока нет.</p>';
        const count = document.getElementById('time-plans-count');
        const pending = state.plans.filter(plan => state.is_gm
            ? plan.confirmed && (plan.overflow_state === 'pending' || plan.activities.some(entry => !entry.approved))
            : !plan.confirmed || plan.overflow_state === 'denied').length;
        count.hidden = pending === 0;
        count.textContent = String(pending);
    }

    async function refresh() {
        state = await Server.getTimePlans(lobbyId);
        render();
        if (!initialShown) {
            initialShown = true;
            if (!state.is_gm && state.plans.some(plan => !plan.confirmed)) modal.style.display = 'flex';
        }
    }

    function readRows(card) {
        return [...card.querySelectorAll('.time-plan-activity-row')].map(row => ({
            id:row.dataset.activityId,
            name:row.querySelector('[data-time-field="name"]').value,
            duration_minutes:Number(row.querySelector('[data-time-field="duration"]').value),
            note:row.querySelector('[data-time-field="note"]').value,
        }));
    }

    function updateDraft(card) {
        const plan = planById(card.dataset.planId);
        if (!plan || state.is_gm) return;
        const entries = readRows(card);
        drafts.set(plan.id, entries);
        card.querySelector('.time-plan-timeline').innerHTML = timelineMarkup(plan, entries);
    }

    async function runAction(target) {
        if (busy) return;
        const action = target.dataset.timeAction;
        const card = target.closest('[data-plan-id]');
        const plan = card && planById(card.dataset.planId);
        busy = true;
        try {
            if (action === 'create') {
                const ownerIds = [...createDraft.owners];
                if (!ownerIds.length) throw new Error('Выберите владельцев персонажей');
                if (!requestId) requestId = crypto.randomUUID();
                await Server.createTimePlanRequest(lobbyId,{request_id:requestId,
                    owner_ids:ownerIds,window_minutes:createDraft.minutes});
                requestId = null;createDraft.owners.clear();
                showNotification('Запрос отправлен', 'success');
            } else if (action === 'add' && plan) {
                const entries = drafts.get(plan.id) || plan.activities.map(item => ({...item}));
                entries.push({id:crypto.randomUUID(),name:'',duration_minutes:30,note:''});
                drafts.set(plan.id,entries);render();
                return;
            } else if (action === 'remove' && plan) {
                const entries = readRows(card).filter(item => item.id !== target.closest('[data-activity-id]').dataset.activityId);
                drafts.set(plan.id,entries);render();
                return;
            } else if ((action === 'save' || action === 'confirm') && plan) {
                const activities = readRows(card);
                await Server.saveTimePlan(lobbyId,plan.id,{revision:plan.revision,
                    activities,confirm:action === 'confirm'});
                drafts.delete(plan.id);
                showNotification(action === 'confirm' ? 'План подтверждён' : 'Черновик сохранён', 'success');
            } else if (plan && ['approve_overflow','deny_overflow','approve_activity','deny_activity'].includes(action)) {
                await Server.reviewTimePlan(lobbyId,plan.id,{revision:plan.revision,action,
                    ...(target.closest('[data-activity-id]') ? {activity_id:target.closest('[data-activity-id]').dataset.activityId} : {})});
            } else if (action === 'roll' && plan) {
                const row = target.closest('[data-activity-id]');
                await Server.rollTimePlan(lobbyId,plan.id,{activity_id:row.dataset.activityId,
                    skill_path:row.querySelector('[data-roll-skill]').value,
                    difficulty:Number(row.querySelector('[data-roll-difficulty]').value)});
            }
            await refresh();
        } catch (error) {
            showNotification(error.message || 'Не удалось сохранить план');
            if (error.status === 409) { drafts.clear(); await refresh().catch(() => {}); }
        } finally { busy = false; }
    }

    button.addEventListener('click', async () => {
        modal.style.display = 'flex';
        await refresh().catch(error => showNotification(error.message));
    });
    modal.querySelector('.time-plans-close').addEventListener('click', () => { modal.style.display = 'none'; });
    modal.addEventListener('click', event => { if (event.target === modal) modal.style.display = 'none'; });
    modal.addEventListener('input', event => {
        if (event.target.id === 'time-plan-window') {
            createDraft.minutes = Number(event.target.value);requestId = null;
        } else if (event.target.matches('[data-time-field]')) updateDraft(event.target.closest('[data-plan-id]'));
    });
    modal.addEventListener('change', event => {
        if (event.target.matches('[data-owner-id]')) {
            const id = Number(event.target.dataset.ownerId);
            if (event.target.checked) createDraft.owners.add(id); else createDraft.owners.delete(id);
            requestId = null;
        }
    });
    modal.addEventListener('click', event => {
        const target = event.target.closest('[data-time-action]');
        if (target) runAction(target);
    });
    socket?.on('time_plans_changed', event => {
        if (Number(event.lobby_id) !== Number(lobbyId)) return;
        refresh().then(() => {
            if (!state.is_gm && state.plans.some(plan => plan.request_id === event.request_id && !plan.confirmed))
                modal.style.display = 'flex';
        }).catch(() => {});
    });
    refresh().catch(error => console.error('Time planning load failed', error));
}
