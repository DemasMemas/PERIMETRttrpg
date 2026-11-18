export function willFailureMessage(result) {
    const action = result?.narrative_action;
    if (!action?.blocked_by_will) return '';
    const checks = (action.checks || [action.check]).filter(check => check && !check.success);
    const rolls = checks.map(check => `${check.total} против СЛ ${check.difficulty}`).join(', ');
    const payment = Number.isFinite(action.action_points) ? ` Потрачено ${action.action_points} ОД.` : '';
    return `Проверка Воли провалена${rolls ? ` (${rolls})` : ''}. Атака сорвалась.${payment}`;
}

export function notifyCombatAttack(attack, notify) {
    if (!attack?.results?.length) return false;
    const rolls = attack.results.map((item, index) => {
        if (item.automatic_hit) return `${index + 1}: автоматическое попадание`;
        const dice = Array.isArray(item.rolls) && item.rolls.length > 1
            ? `[${item.rolls.join(', ')}] → ${item.roll}`
            : String(item.roll);
        const outcome = item.hit ? 'попадание' : 'промах';
        return `${index + 1}: d20 ${dice}, СЛ ${item.difficulty} — ${outcome}`;
    }).join(' · ');
    const strength = attack.results[0]?.strength_requirement;
    const strengthText = strength?.accuracy_penalty
        ? ` Требование Силы ${strength.effective_required}, Сила ${strength.strength}: точность −${strength.accuracy_penalty}.`
        : '';
    const bleedingCount = attack.results.flatMap(item => item.bleedings || []).length;
    const traumaCount = attack.results.flatMap(item => item.additional_traumas || []).length;
    const bleedingText = bleedingCount ? ` Кровотечения: ${bleedingCount}.` : '';
    const traumaText = traumaCount ? ` Доп. травмы: ${traumaCount}.` : '';
    notify(`${rolls}. Итоговый урон: ${attack.damage_total || 0}.${bleedingText}${traumaText}${strengthText}`,
        attack.results.some(item => item.hit) ? 'success' : 'system');
    return true;
}

// Parameters and targets stay on the server; the client only asks to resume an ID.
export function createDeferredActionResumer({ send, resumeConsumable, cancel, applyState, canResume, notify }) {
    const attempted = new Set();
    const running = new Map();
    return async function resume(state) {
        const actor = state?.current_character;
        const action = actor?.deferred_action;
        const consumable = action?.client_executable && action.action_key === 'consumable_use' && resumeConsumable;
        if (state?.status !== 'active' || !action || action.status !== 'ready'
            || !canResume(actor)) return;
        if (running.has(action.id)) {
            if (consumable) running.set(action.id, state);
            return;
        }
        const discard = async () => {
            const fresh = await cancel({location_character_id: actor.location_character_id, action_id: action.id}, state.location_id);
            if (fresh) applyState?.(fresh);
        };
        const stale = attempted.has(action.id);
        if (consumable && stale) return;
        attempted.add(action.id);
        if (attempted.size > 200) attempted.delete(attempted.values().next().value);
        running.set(action.id, null);
        try {
            // Устаревший оплаченный снимок и действие без сохранённых параметров
            // не должны оставлять строку, требующую ручной уборки.
            if (stale || (!action.server_executable && !consumable)) {
                await discard();
                return;
            }
            if (consumable) {
                const result = await resumeConsumable(action.id, state.location_id);
                if (result === false) attempted.delete(action.id);
                if (result !== true) return;
            } else {
                const result = await send({
                    location_character_id: actor.location_character_id,
                    action_key: action.action_key,
                    resume_pending_action_id: action.id,
                });
                if (result?.state) applyState?.(result.state);
                const failure = willFailureMessage(result);
                if (failure) {
                    notify(failure, 'error');
                    return;
                }
                if (notifyCombatAttack(result?.attack, notify)) return;
            }
            notify('Длительное действие завершено', 'success');
        } catch (error) {
            if (consumable && (error instanceof TypeError || error.status >= 500)) {
                attempted.delete(action.id);
                notify('Не удалось завершить применение расходника. Оплата сохранена.', 'system');
                return;
            }
            try {
                await discard();
                if (error.status !== 409 || consumable) notify('Длительное действие отменено: ' + (error.message || 'не удалось завершить'), 'system');
            } catch (cancelError) {
                attempted.delete(action.id);
                notify(cancelError.message || 'Не удалось обновить состояние действия', 'system');
            }
        } finally {
            const nextState = running.get(action.id);
            running.delete(action.id);
            if (nextState && !attempted.has(action.id)) await resume(nextState);
        }
    };
}
