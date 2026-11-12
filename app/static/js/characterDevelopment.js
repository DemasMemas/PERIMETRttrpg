import { Server } from './api.js';
import { backgroundIs } from './characterTraits.js';

const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const reasons = [
    ['roleplay', 'Отыгрыш'],
    ['heroism', 'Героизм'],
    ['contribution', 'Решение сложной ситуации'],
    ['gmBonus', 'Поощрение от ведущего'],
];

export async function showSessionReward(lobbyId, characterId, data, onUpdated) {
    if(document.getElementById('session-reward-modal'))return;
    const rules = await Server.characterCreationRules(lobbyId);
    if (document.getElementById('session-reward-modal')) return;
    const background = data?.basic?.background;
    const securityBackground = background?.ruleId === 'background-af'
        || backgroundIs(data, 'СБшник') || backgroundIs(data, 'СБУшник');
    const weapon = rules.weaponTypes[background?.choices?.backgroundWeapon];
    const options = Object.entries(rules.skills).flatMap(([group, skills]) =>
        Object.entries(skills).map(([key, name]) => `<option value="${group}.${key}">${escape(name)}</option>`));
    const modal = document.createElement('div');
    modal.id = 'session-reward-modal';
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content session-reward-dialog" role="dialog" aria-modal="true" aria-labelledby="session-reward-title">
            <header class="session-reward-header">
                <h3 id="session-reward-title">Награждение опытом</h3>
                <button type="button" class="btn btn-secondary" data-dismiss aria-label="Закрыть">×</button>
            </header>
            <form class="session-reward-form">
                <div class="session-reward-base"><strong>+1</strong> свободное очко за сессию или цель</div>
                <fieldset>
                    <legend>Дополнительные очки</legend>
                    <div class="session-reward-reasons">
                        ${reasons.map(([key, name]) => `
                            <label class="session-reward-option">
                                <input type="checkbox" name="${key}">
                                <span>${name}</span><strong>+1</strong>
                            </label>`).join('')}
                    </div>
                </fieldset>
                <fieldset>
                    <legend>Обучение</legend>
                    <label for="session-reward-skill">Навык</label>
                    <select id="session-reward-skill" name="trainingSkill" class="form-control">
                        <option value="">Без обучения</option>${options.join('')}
                    </select>
                    ${securityBackground ? `
                        <label class="session-reward-option session-reward-weapon" data-weapon hidden>
                            <input type="checkbox" name="usedBackgroundWeapon" ${weapon ? '' : 'disabled'}>
                            <span><strong>СБшник</strong><br>${weapon
                                ? `Использован выбранный вид оружия · ${escape(weapon)}`
                                : 'В листе не указан выбранный вид оружия'
                            }</span>
                        </label>` : ''}
                    <small>Бонусы и риски обучения учитываются автоматически.</small>
                </fieldset>
                <p data-error role="alert" hidden></p>
                <footer><button type="submit" class="btn">Начислить награду</button></footer>
            </form>
        </div>`;
    document.body.appendChild(modal);
    let busy = false;
    const operationId = crypto.randomUUID?.() || `reward-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    const form = modal.querySelector('form');
    const training = form.elements.trainingSkill;
    const weaponRow = form.querySelector('[data-weapon]');
    const submit = form.querySelector('[type=submit]');
    const errorMessage = form.querySelector('[data-error]');
    if (weaponRow) training.onchange = () => { weaponRow.hidden = training.value !== 'physical.shooting'; };
    modal.querySelector('[data-dismiss]').onclick = () => { if (!busy) modal.remove(); };
    modal.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !busy) {
            event.stopPropagation();
            modal.remove();
        }
    });
    training.focus();
    form.onsubmit = async event => {
        event.preventDefault();
        if (busy) return;
        busy = submit.disabled = true;
        errorMessage.hidden = true;
        const payload = { operationId };
        for (const [key] of reasons) payload[key] = form.elements[key].checked;
        if (training.value) payload.trainingSkill = training.value;
        if (weaponRow && !weaponRow.hidden) payload.usedBackgroundWeapon = form.elements.usedBackgroundWeapon.checked;
        try {
            const result = await Server.awardCharacterSession(lobbyId, characterId, payload);
            modal.remove();
            onUpdated(result);
        } catch (error) {
            busy = submit.disabled = false;
            errorMessage.textContent = error.message;
            errorMessage.hidden = false;
        }
    };
}
