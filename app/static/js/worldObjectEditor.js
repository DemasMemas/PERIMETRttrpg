import {
    renderer, controls, chunksMap, pickWorldObject, worldPointOnObjectPlane, worldPointOnTerrain,
    createPreviewObject, removePreviewObject, showObjectHighlights, hideObjectHighlight,
    getObjectDimensions, getObjectHeightOffset, updateTileInChunk, worldObjectDataEqual,
} from './lobby3d.js';
import { Server } from './api.js';
import { roadSurfaceHeight } from './worldRoadTopology.js';
import { connectedFenceReferences, MAX_FENCE_SECTIONS } from './worldFenceLine.js';
import { showNotification } from './utils.js';
import { isTypingTarget } from './cameraPan.js';
import { syncWorldNumber, getWorldNumericValue } from './worldNumericInputs.js';
import { createWorldObjectSelection, worldObjectDragTarget, worldObjectMouseTransform } from './worldObjectSelection.js';

export function initDirectWorldObjectEditor({ lobbyId, state, protectedTypes, labels, onDetails }) {
    const field = name => document.getElementById(`world-selection-${name}`);
    const getTile = ref => chunksMap.get(`${ref.chunkX},${ref.chunkY}`)?.tilesData[ref.tileY]?.[ref.tileX];
    const available = () => state.isGM && state.editMode && !window.isLocationActive
        && window.worldEditorTool === 'select' && !window.isWorldTravelSelectionActive?.() && !window.isWorldClipboardActive?.();
    let gesture = null;
    let previewChanges = {};
    let pointer = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
    let quickTarget = null;
    let quickDirty = false;
    let quickSaving = false;
    let quickGeneration = 0;
    const highlight = (selection, changes = {}, target = null, preview = false) => {
        if (!selection) { removePreviewObject(); hideObjectHighlight(); return; }
        const plans = selectionController.plan(changes, target);
        if (!plans) { removePreviewObject(); hideObjectHighlight(); return; }
        const highlights = plans.flatMap(plan => {
            const object = { ...plan.reference.object, ...plan.changes }, destination = plan.target;
            const tile = getTile({ chunkX: Math.floor(destination.tile_x / 32), chunkY: Math.floor(destination.tile_y / 32), tileX: destination.tile_x % 32, tileY: destination.tile_y % 32 });
            if (!tile) return [];
            const scale = object.scale ?? 1;
            return [{ x: destination.tile_x + 0.5 + (object.x || 0), z: destination.tile_y + 0.5 + (object.z || 0),
                y: object.type==='road'&&object.bridge ? roadSurfaceHeight(tile,object) : (tile.height || 1) + getObjectHeightOffset(object.type, object.anomalyType) * scale,
                dimensions: getObjectDimensions(object.type, object.anomalyType, scale), rotation: object.rotation || 0 }];
        });
        showObjectHighlights(highlights);
        changes = plans[0].changes; target = plans[0].target;
        const object = { ...selection.object, ...changes };
        const tileX = target?.tile_x ?? selection.chunkX * 32 + selection.tileX;
        const tileY = target?.tile_y ?? selection.chunkY * 32 + selection.tileY;
        const tile = { chunkX: Math.floor(tileX / 32), chunkY: Math.floor(tileY / 32),
            tileX: tileX % 32, tileY: tileY % 32 };
        tile.tileData = getTile(tile);
        if (!tile.tileData) return;
        const scale = object.scale ?? 1;
        if (preview) createPreviewObject(tile, {
            ...object, color: object.color || '#808080', scale, rotation: object.rotation || 0,
            offsetX: object.x || 0, offsetZ: object.z || 0,
        });
    };
    const render = (selection, busy) => {
        const panel = field('editor');
        if (!panel) return;
        panel.hidden = !selection;
        panel.disabled = busy;
        if (!selection) { highlight(null); return; }
        const tileInfo = document.getElementById('tile-info');
        if (tileInfo) tileInfo.style.display = 'none';
        const object = selection.object;
        const multiple = selectionController.members.length > 1;
        const fenceButton=document.getElementById('world-selection-fence-run');
        if(fenceButton)fenceButton.hidden=object.type!=='fence';
        field('title').textContent = multiple ? `Выбрано объектов: ${selectionController.members.length}` : object.name || labels[object.type] || object.type;
        for (const name of ['name', 'color', 'active', 'details']) field(name).disabled = multiple;
        field('name').value = object.name || '';
        field('color').value = /^#[0-9a-f]{6}$/i.test(object.color || '') ? object.color : '#808080';
        syncWorldNumber('world-selection-scale', object.scale ?? 1);
        syncWorldNumber('world-selection-rotation', object.rotation ?? 0);
        field('active-option').hidden = !protectedTypes.has(object.type);
        field('active').checked = object.is_active === true;
        // Connected roads derive direction from their links, not this angle.
        field('rotation-option').hidden = selectionController.members.some(member => member.object.type === 'road'
            && Boolean(member.object.manual_connections?.length || member.object.connections?.length));
        field('position').textContent = `Клетка ${selection.chunkX * 32 + selection.tileX}, ${selection.chunkY * 32 + selection.tileY}`;
        if (!busy) { previewChanges = {}; removePreviewObject(); highlight(selection); }
    };
    const selectionController = createWorldObjectSelection({
        equal: worldObjectDataEqual,
        getTile,
        getBounds: () => [window.MAP_CHUNKS_WIDTH * 32, window.MAP_CHUNKS_HEIGHT * 32],
        editObject: payload => Server.editWorldObject(lobbyId, payload),
        applyUpdate: item => updateTileInChunk(item.chunk_x, item.chunk_y, item.tile_x, item.tile_y, item.updates),
        onChange: render,
        onError: message => showNotification(message, 'error'),
    });
    const selection = selectionController;
    const cancelGesture = () => {
        if (!gesture) return;
        controls.enabled = gesture.controlsEnabled;
        gesture = null;
        render(selection.selected, selection.busy);
    };
    window.clearDirectWorldObjectSelection = () => {
        window.stopMapRotationHold?.();
        quickGeneration++;
        quickTarget = null;
        quickDirty = false;
        cancelGesture(); selection.clear();
    };
    window.hasDirectWorldObjectSelection = () => Boolean(selection.selected);
    window.isDirectWorldObjectTransformActive = () => Boolean(gesture);
    window.getDirectWorldObjectSelection = () => selection.members.map(member => ({ ...member, object: { ...member.object } }));
    window.selectDirectWorldObjects = references => {
        window.stopMapRotationHold?.();
        return selection.selectMany(references);
    };
    window.refreshDirectWorldObjectEditor = () => render(selection.selected, selection.busy);
    window.syncDirectWorldObjectSelection = (chunkX, chunkY, tileX, tileY) => {
        const selected = selection.selected;
        if (!selected || selection.busy || !selection.members.some(member => member.chunkX === chunkX && member.chunkY === chunkY
            && member.tileX === tileX && member.tileY === tileY)) return;
        if (!selection.validate()) cancelGesture();
        else highlight(selected);
    };
    window.beginDirectWorldObjectEdit = event => {
        if (!available() || selection.busy || document.getElementById('tile-edit-modal')?.style.display === 'flex') return false;
        const reference = pickWorldObject(event.clientX, event.clientY);
        if (event.shiftKey) {
            selection.select(reference, { toggle: true });
            return true;
        }
        selection.select(reference, { preserve: true });
        if (!reference) return false;
        pointer = { x: event.clientX, y: event.clientY };
        const selected = selection.selected;
        const height = getTile(selected)?.height || 1;
        const point = worldPointOnObjectPlane(event.clientX, event.clientY, height);
        if (!point) return true;
        gesture = { kind: 'drag', reference, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY,
            controlsEnabled: controls.enabled, height, moved: false, target: null,
            grabOffset: { x: selected.chunkX * 32 + selected.tileX + 0.5 + (selected.object.x || 0) - point.x,
                z: selected.chunkY * 32 + selected.tileY + 0.5 + (selected.object.z || 0) - point.z } };
        controls.enabled = false;
        renderer.domElement.setPointerCapture?.(event.pointerId);
        return true;
    };
    const updateGesture = event => {
        if (!gesture) return;
        if (!available() || !selection.validate()) { cancelGesture(); return; }
        if (gesture.kind === 'scale' || gesture.kind === 'rotate') {
            gesture.changes = worldObjectMouseTransform(gesture.kind, gesture.baseObject, event.clientX - gesture.startX);
            for (const [key, value] of Object.entries(gesture.changes)) syncWorldNumber(`world-selection-${key}`, value);
            highlight(selection.selected, gesture.changes, null, true);
            return;
        }
        if (!gesture.moved && gesture.kind === 'drag'
            && Math.hypot(event.clientX - gesture.startX, event.clientY - gesture.startY) < 4) return;
        gesture.moved = true;
        const point = gesture.kind === 'move' ? worldPointOnTerrain(event.clientX, event.clientY)
            : worldPointOnObjectPlane(event.clientX, event.clientY, gesture.height);
        if (!point) { gesture.target = null; removePreviewObject(); highlight(selection.selected); return; }
        const target = worldObjectDragTarget(point, gesture.grabOffset,
            window.MAP_CHUNKS_WIDTH * 32, window.MAP_CHUNKS_HEIGHT * 32);
        gesture.target = target;
        if (!target) { removePreviewObject(); highlight(selection.selected); return; }
        const tile = getTile({ chunkX: Math.floor(target.tile_x / 32), chunkY: Math.floor(target.tile_y / 32),
            tileX: target.tile_x % 32, tileY: target.tile_y % 32 });
        if (!tile) { gesture.target = null; return; }
        highlight(selection.selected, { x: target.x, z: target.z }, target, true);
    };
    window.addEventListener('pointermove', event => {
        pointer = { x: event.clientX, y: event.clientY };
        if(event.buttons&2)return;
        if (!gesture || (gesture.kind === 'drag' && event.pointerId !== gesture.pointerId)) return;
        updateGesture(event);
        event.preventDefault();
    }, { capture: true });
    window.addEventListener('pointerup', event => {
        if (!gesture || gesture.kind !== 'drag' || event.pointerId !== gesture.pointerId || event.button !== 0) return;
        const { moved, target, reference } = gesture;
        cancelGesture();
        if (moved && target && available()) selection.commit({ x: target.x, z: target.z }, target);
        else if (!moved && available()) selection.select(reference);
    }, { capture: true });
    window.addEventListener('pointercancel', cancelGesture, { capture: true });
    window.addEventListener('pointerdown', event => {
        if(event.button===2){if(gesture)controls.enabled=true;return;}
        if (!gesture) {
            return;
        }
        if (gesture.kind !== 'drag' && (event.button === 0 || event.button === 2)) {
            event.preventDefault();
            event.stopImmediatePropagation();
            if (event.button === 0) {
                updateGesture(event);
                if (!gesture) return;
                const { kind, changes, target } = gesture;
                cancelGesture();
                if ((kind === 'move' && target) || kind === 'scale' || kind === 'rotate') {
                    selection.commit(kind === 'move' ? { x: target.x, z: target.z } : changes, target);
                }
            } else cancelGesture();
        } else if (event.button === 2) cancelGesture();
    }, { capture: true });
    window.addEventListener('blur', cancelGesture);
    window.addEventListener('secondaryclick',event=>{
        if(event.target!==renderer.domElement||!available())return;
        if(gesture){cancelGesture();event.stopImmediatePropagation();}
        else if(selection.selected){window.clearDirectWorldObjectSelection();event.stopImmediatePropagation();}
    },{capture:true});
    const startTransform = kind => {
        if (selection.busy || !selection.validate()) return;
        cancelGesture();
        const selected = selection.selected;
        const height = getTile(selected)?.height || 1;
        const point = worldPointOnObjectPlane(pointer.x, pointer.y, height);
        if (!point && kind !== 'move') return;
        gesture = { kind, startX: pointer.x, startY: pointer.y, height,
            controlsEnabled: controls.enabled, baseObject: { ...selected.object }, changes: {}, target: null,
            grabOffset: kind === 'move' ? { x: 0, z: 0 } : { x: selected.chunkX * 32 + selected.tileX + 0.5 + (selected.object.x || 0) - point.x,
                z: selected.chunkY * 32 + selected.tileY + 0.5 + (selected.object.z || 0) - point.z } };
        controls.enabled = false;
        field('editor').disabled = true;
        updateGesture({ clientX: pointer.x, clientY: pointer.y });
    };
    const nudge = changes => {
        if (selection.busy && !quickSaving) return;
        quickTarget = { ...(quickTarget || selection.selected.object), ...changes };
        quickDirty = true;
        if (quickSaving) return;
        const generation = quickGeneration;
        quickSaving = true;
        (async () => {
            try {
                while (quickDirty && available() && selection.selected && generation === quickGeneration) {
                    quickDirty = false;
                    if (!await selection.commit({ scale: quickTarget.scale ?? 1, rotation: quickTarget.rotation ?? 0 })) break;
                }
            } finally {
                quickSaving = false;
                quickTarget = null;
                quickDirty = false;
            }
        })();
    };
    const rotationContext = () => available() && selection.selected && !field('rotation-option').hidden
        ? `${quickGeneration}:${selection.members.length}` : null;
    const rotateHeld = (direction, fraction) => {
        if (rotationContext() === null) return false;
        const object = quickTarget || selection.selected.object;
        nudge({ rotation: ((Number(object.rotation || 0) - direction * fraction * 15) % 360 + 360) % 360 });
        return true;
    };
    // Window capture gives selected-object commands priority over the global
    // document handlers for Q (menu), E (editor), R (eraser) and +/- (height).
    window.addEventListener('keydown', event => {
        if (!available() || !selection.selected || document.getElementById('tile-edit-modal')?.style.display === 'flex'
            || event.ctrlKey || event.metaKey || event.altKey
            || isTypingTarget(event.target)) return;
        const command = { KeyR: 'scale', KeyG: 'move', KeyT: 'rotate' }[event.code];
        const scaleDirection = ['Equal', 'NumpadAdd'].includes(event.code) || event.key === '+' ? 1
            : ['Minus', 'NumpadSubtract'].includes(event.code) || event.key === '-' ? -1 : 0;
        const rotateDirection = event.code === 'KeyQ' ? -1 : event.code === 'KeyE' ? 1 : 0;
        if (!command && !scaleDirection && !rotateDirection && !['Escape', 'Delete', 'Backspace'].includes(event.key)) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        if (rotateDirection && event.repeat) return;
        if (event.key === 'Escape') {
            if (gesture) cancelGesture();
            else selection.clear();
        } else if (event.key === 'Delete' || event.key === 'Backspace') {
            cancelGesture(); selection.commit({}, null, true);
        } else if (command) {
            if (!event.repeat) startTransform(command);
        } else if (!selection.busy || quickSaving) {
            if (gesture) cancelGesture();
            const object = quickTarget || selection.selected.object;
            if (scaleDirection) nudge(worldObjectMouseTransform('scale', object, scaleDirection * 10));
            else if (!field('rotation-option').hidden) {
                rotateHeld(rotateDirection, 1);
                window.startMapRotationHold?.(event, rotateHeld, rotationContext);
            }
        }
    }, { capture: true });
    document.addEventListener('input', event => {
        if (!available() || selection.busy || !selection.selected || !event.target.closest('#world-selection-editor')) return;
        const input = event.target;
        if (input.id.includes('scale') || input.id.includes('rotation')) {
            if (input.type === 'number' && !input.value.trim()) return;
            const key = input.id.includes('scale') ? 'scale' : 'rotation';
            previewChanges[key] = getWorldNumericValue(`world-selection-${key}`);
        } else if (input.id === 'world-selection-color') previewChanges.color = input.value;
        else return;
        highlight(selection.selected, previewChanges, null, true);
    });
    document.addEventListener('change', event => {
        if (!available() || selection.busy || !selection.selected || !event.target.closest('#world-selection-editor')) return;
        const input = event.target;
        if (input.id === 'world-selection-name') selection.commit({ name: input.value.trim() });
        else if (input.id === 'world-selection-active') selection.commit({ is_active: input.checked });
        else if (Object.keys(previewChanges).length) selection.commit(previewChanges);
    });
    document.addEventListener('click', event => {
        if (!available() || selection.busy) return;
        if(event.target.closest('#world-selection-fence-run') && selection.selected?.object.type==='fence'){
            const references=[];
            for(const entry of chunksMap.values())entry.tilesData.forEach((row,tileY)=>row.forEach((tile,tileX)=>(tile.objects||[]).forEach((object,index)=>{
                if(object.type==='fence')references.push({chunkX:entry.chunkX,chunkY:entry.chunkY,tileX,tileY,index,object});
            })));
            const connected=connectedFenceReferences(references,selection.selected);
            if(connected.length>MAX_FENCE_SECTIONS){showNotification('Участок содержит более 1000 секций. Выберите меньшую группу с Shift.');return;}
            selection.selectMany(connected);
        }
        if (event.target.closest('#world-selection-delete')) { cancelGesture(); selection.commit({}, null, true); }
        if (event.target.closest('#world-selection-details') && selection.selected) onDetails(selection.selected);
    });
}
