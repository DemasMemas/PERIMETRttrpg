import { Server } from './api.js';
import { showNotification } from './utils.js';

function node(tag, text = '') {
    const el = document.createElement(tag);
    el.textContent = text;
    return el;
}

function choice(label, entries) {
    const select = node('select');
    select.className = 'form-control';
    select.setAttribute('aria-label', label);
    for (const [value, text] of entries) select.add(new Option(text, value));
    return select;
}

function action(label, run) {
    const button = node('button', label);
    button.type = 'button';
    button.className = 'btn btn-secondary';
    button.onclick = run;
    return button;
}

function stashEntries(items, path = ['contents']) {
    return items.flatMap((item, index) => {
        const itemPath = [...path, index];
        const nested = Array.isArray(item.contents) ? stashEntries(item.contents, [...itemPath,'contents']) : [];
        return [{item,path:itemPath}, ...nested];
    });
}

export function showWorldStashes(lobbyId, group) {
    document.getElementById('world-stash-dialog')?.remove();
    const modal = node('div');
    modal.id = 'world-stash-dialog';
    modal.className = 'modal';
    modal.style.display = 'flex';
    const panel = node('div');
    panel.className = 'modal-content';
    const body = node('div');
    const character = choice('Персонаж', group.members.map(member => [member.id,member.name]));
    panel.append(action('Закрыть', () => modal.remove()),
        node('h3',`Тайники на клетке (${group.tile_x}, ${group.tile_y})`), character, body);
    modal.append(panel);
    document.body.append(modal);

    let busy = false;
    let selected = null;
    const operate = async (operation, payload = {}) => {
        if (busy) return;
        busy = true;
        const characterId = Number(character.value);
        modal.querySelectorAll('button,select,input').forEach(control => { control.disabled = true; });
        try {
            await Server.waitForCharacterSave(characterId);
            const result = await Server.operateWorldStash(lobbyId, group.id, {operation, character_id:characterId,
                tile_x:group.tile_x, tile_y:group.tile_y, ...payload});
            const snapshot = await Server.getCrafting(characterId);
            if (modal.isConnected) render(result, snapshot);
        } catch (error) {
            showNotification(error.message, 'system');
            if (modal.isConnected) body.replaceChildren(action('Обновить', () => operate('list')));
        } finally {
            character.disabled = false;
            panel.firstElementChild.disabled = false;
            busy = false;
        }
    };

    const render = (result, snapshot) => {
        body.replaceChildren();
        const shovels = snapshot.inventory.filter(({item}) =>
            ['tool','misc','weapon','melee_weapon'].includes(item.category) && /лопат/i.test(item.name));
        const shovel = choice('Лопата', shovels.map(({item},index) => [index,item.name]));
        const bury = action('Прикопать тайник', () => operate('bury',
            {tool_path:shovels[Number(shovel.value)].path,operation_id:crypto.randomUUID()}));
        bury.disabled = !shovels.length;
        body.append(action('Осмотреть клетку', () => operate('search')));
        if (shovels.length) body.append(shovel,bury);
        else body.append(node('p','Для нового тайника нужна лопата.'));
        if (result.found != null) body.append(node('p',result.found ? `Обнаружено тайников: ${result.found}` : 'Новых тайников не обнаружено.'));
        if (!result.stashes.length) return;

        const list = choice('Тайник', result.stashes.map(stash => [stash.id,`Тайник №${stash.id}`]));
        if (result.stashes.some(stash => stash.id === selected)) list.value = selected;
        const exchange = node('div');
        const drawExchange = () => {
            selected = Number(list.value);
            const stash = result.stashes.find(entry => entry.id === selected);
            exchange.replaceChildren();
            for (const [direction, entries, label] of [
                ['to_container', snapshot.inventory, 'Положить в тайник'],
                ['to_character', stashEntries(stash.contents), 'Забрать из тайника'],
            ]) {
                if (!entries.length) continue;
                const item = choice(label, entries.map((entry,index) => [index,`${entry.item.name} × ${entry.item.quantity ?? 1}`]));
                const amount = node('input');
                Object.assign(amount,{type:'number',min:1,value:1,step:1,className:'form-control'});
                amount.setAttribute('aria-label','Количество предметов');
                const update = () => { amount.max = entries[Number(item.value)].item.quantity ?? 1; amount.value = 1; };
                item.onchange = update;
                update();
                exchange.append(item,amount,action(label, () => {
                    if (!amount.reportValidity()) return;
                    const entry = entries[Number(item.value)];
                    operate('transfer', {stash_id:stash.id, stash_revision:stash.revision, character_revision:snapshot.revision,
                        direction, item_path:entry.path, expected_item:entry.item, amount:Number(amount.value)});
                }));
            }
        };
        list.onchange = drawExchange;
        body.append(list,exchange);
        drawExchange();
    };

    character.onchange = () => operate('list');
    operate('list');
}
