import {Server} from './api.js';
import {showNotification} from './utils.js';

const pending = new Map();

export async function rollQuickSkill({characterId, skillPath, skillLabel, locationId, onSaved}) {
    const key = `${characterId}:${skillPath}:${locationId || ''}`;
    const request = pending.get(key) || {id:crypto.randomUUID(), busy:false};
    if (request.busy) return null;
    request.busy = true;pending.set(key, request);
    try {
        const result = await Server.performSkillCheck(characterId, {
            operation_id:request.id, skill_path:skillPath, difficulty:null,
            ...(locationId ? {location_id:locationId} : {}),
        });
        pending.delete(key);
        await onSaved?.(result.data);
        const check = result.check;
        let text = check.automatic_failure ? check.failure_reason
            : `d20 ${(check.rolls || []).join('/')} → ${check.roll}, итог ${check.total}`;
        if (check.disadvantage) text += ', Помеха';
        if (check.advantage) text += ', Преимущество';
        if (check.equipment_modifier) text += `; экипировка ${check.equipment_modifier > 0 ? '+' : ''}${check.equipment_modifier}`;
        if (check.rules?.length) text += '; ' + check.rules.join('; ');
        showNotification(`🎲 ${skillLabel}: ${text}`, 'system');
        return result;
    } catch (error) {
        showNotification(error.message || 'Не удалось выполнить проверку', 'system');
        return null;
    } finally {
        request.busy = false;
    }
}
