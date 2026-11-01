// Parameters and targets stay on the server; the client only asks to resume an ID.
export function createDeferredActionResumer({ send, resumeConsumable, cancel, applyState, canResume, notify }) {
    const attempted = new Set();
    const running = new Set();
    return async function resume(state) {
        const actor = state?.current_character;
        const action = actor?.deferred_action;
        const medical = action?.client_executable && action.action_key === 'consumable_use' && resumeConsumable;
        if (state?.status !== 'active' || !action || action.status !== 'ready'
            || !canResume(actor) || running.has(action.id)) return;
        const discard = async () => {
            const fresh = await cancel({location_character_id: actor.location_character_id, action_id: action.id});
            if (fresh) applyState?.(fresh);
        };
        const stale = attempted.has(action.id);
        attempted.add(action.id);
        if (attempted.size > 200) attempted.delete(attempted.values().next().value);
        running.add(action.id);
        try {
            // Устаревший оплаченный снимок и действие без сохранённых параметров
            // не должны оставлять строку, требующую ручной уборки.
            if (stale || (!action.server_executable && !medical)) {
                await discard();
                return;
            }
            if (medical) {
                if (await resumeConsumable(action.id) === false) {
                    // The initiating request may still hold the inventory lock.
                    attempted.delete(action.id);
                    return;
                }
            } else {
                const result = await send({
                    location_character_id: actor.location_character_id,
                    action_key: action.action_key,
                    resume_pending_action_id: action.id,
                });
                if (result?.state) applyState?.(result.state);
            }
            notify('Длительное действие завершено', 'success');
        } catch (error) {
            try {
                await discard();
                if (error.status !== 409 || medical) notify('Длительное действие отменено: ' + (error.message || 'не удалось завершить'), 'system');
            } catch (cancelError) {
                attempted.delete(action.id);
                notify(cancelError.message || 'Не удалось обновить состояние действия', 'system');
            }
        } finally {
            running.delete(action.id);
        }
    };
}
