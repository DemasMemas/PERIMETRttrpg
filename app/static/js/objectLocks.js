import { Server } from './api.js';
import { showNotification } from './utils.js';

export async function showObjectLock({ lobbyId, characterId, object, inCombat, onObject }) {
    if (document.getElementById('object-lock-modal')) return;
    const character = await Server.getCharacter(characterId);
    if (document.getElementById('object-lock-modal')) return;

    const entries = [];
    const names = new Set(['Отмычка', 'Монтировка', 'Горелка', 'Термический Заряд']);
    const visit = (items, path) => {
        for (const [index, item] of (items || []).entries()) {
            const sourcePath = [...path, index];
            if (names.has(item?.name)) entries.push({ item, path: sourcePath });
            if (Array.isArray(item?.contents)) visit(item.contents, [...sourcePath, 'contents']);
        }
    };
    visit(character.data.inventory?.pockets, ['inventory', 'pockets']);
    visit(character.data.inventory?.backpack, ['inventory', 'backpack']);
    for (const slot of ['belt', 'vest']) {
        for (const [index, pouch] of (character.data.equipment?.[slot]?.pouches || []).entries()) {
            visit(pouch.contents, ['equipment', slot, 'pouches', index, 'contents']);
        }
    }

    const modal = document.createElement('dialog');
    modal.id = 'object-lock-modal';
    modal.className = 'object-lock-dialog';
    modal.setAttribute('aria-labelledby', 'object-lock-heading');
    modal.setAttribute('aria-describedby', 'object-lock-target');
    modal.innerHTML = `
        <div class="modal-content object-lock-content">
            <header class="object-lock-heading">
                <div>
                    <h2 id="object-lock-heading">Вскрытие замка</h2>
                    <p id="object-lock-target"></p>
                </div>
                <button type="button" class="btn btn-secondary modal-icon-close" aria-label="Закрыть">×</button>
            </header>
            <div class="object-lock-fields">
                <label>Способ
                    <select class="form-control" data-lock-mode>
                        <option value="tools">Инструментами</option>
                        <option value="force">Силой</option>
                    </select>
                </label>
                <label data-lock-tool-label>Инструмент
                    <select class="form-control" data-lock-tool>
                        <option value="">Без инструмента · штраф 10</option>
                    </select>
                </label>
            </div>
            <p class="object-lock-info" data-lock-info></p>
            <p class="object-lock-result" data-lock-result role="status"></p>
            <footer class="form-actions">
                <button type="button" class="btn btn-primary" data-lock-attempt></button>
                <button type="button" class="btn btn-secondary" data-lock-cancel>Отмена</button>
            </footer>
        </div>`;
    modal.querySelector('#object-lock-target').textContent =
        (object.name || 'Объект') + ' · СЛ ' + (object.properties?.lock_difficulty ?? 8);
    const mode = modal.querySelector('[data-lock-mode]');
    const tool = modal.querySelector('[data-lock-tool]');
    const toolLabel = modal.querySelector('[data-lock-tool-label]');
    const info = modal.querySelector('[data-lock-info]');
    const resultText = modal.querySelector('[data-lock-result]');
    const attempt = modal.querySelector('[data-lock-attempt]');
    const cancel = modal.querySelector('[data-lock-cancel]');
    const attemptLabel = inCombat ? 'Попытка · 3 ОД' : 'Попытка';
    for (const [index, entry] of entries.entries()) tool.add(new Option(entry.item.name, index));

    const close = () => modal.close();
    modal.querySelector('.modal-icon-close').onclick = cancel.onclick = close;
    modal.addEventListener('close', () => modal.remove(), { once: true });
    modal.addEventListener('keydown', event => {
        event.stopPropagation();
        if (event.key === 'Escape') {
            event.preventDefault();
            close();
        }
    });

    const updateMethod = () => {
        const force = mode.value === 'force';
        toolLabel.hidden = force;
        info.textContent = force
            ? 'СЛ = сложность замка × 1,5 − бонус Силы. При неудаче замок может заклинить.'
            : 'СЛ = сложность замка − бонусы Ловкости, Инженерии и инструмента. При неудаче отмычка может сломаться.';
    };
    mode.onchange = updateMethod;
    updateMethod();

    const showJammed = () => {
        attempt.textContent = 'Замок заклинило';
        attempt.classList.replace('btn-primary', 'btn-danger');
        attempt.disabled = mode.disabled = tool.disabled = true;
        cancel.textContent = 'Закрыть';
    };
    attempt.textContent = attemptLabel;
    if (object.properties?.lock_jammed) showJammed();

    const operationId = crypto.randomUUID();
    attempt.onclick = async () => {
        if (attempt.disabled) return;
        attempt.disabled = mode.disabled = tool.disabled = true;
        attempt.textContent = 'Попытка…';
        resultText.textContent = '';
        try {
            const entry = mode.value === 'tools' && tool.value !== '' ? entries[Number(tool.value)] : null;
            const response = await Server.attemptObjectLock(lobbyId, object.id, {
                character_id: Number(characterId),
                character_revision: character.data._revision,
                object_revision: object.revision,
                operation_id: operationId,
                mode: mode.value,
                tool: entry ? { path: entry.path, snapshot: entry.item } : null,
            });
            onObject?.(response.object);
            const result = response.operation;
            resultText.textContent = 'd20: ' + result.roll + ', СЛ ' + result.difficulty + '. '
                + (result.success ? 'Замок открыт.' : 'Попытка не удалась.')
                + (result.tool_broken ? ' Отмычка сломалась.' : '');
            if (result.jammed) {
                showJammed();
                if (modal.open) cancel.focus();
            } else {
                attempt.textContent = 'Готово';
                attempt.disabled = false;
                attempt.onclick = close;
                cancel.remove();
                if (modal.open) attempt.focus();
            }
            showNotification(result.success ? 'Замок открыт' : 'Попытка вскрытия не удалась',
                result.success ? 'success' : 'system');
        } catch (error) {
            resultText.textContent = error.message;
            attempt.textContent = attemptLabel;
            attempt.disabled = mode.disabled = tool.disabled = false;
        }
    };
    document.body.append(modal);
    modal.showModal();
}
