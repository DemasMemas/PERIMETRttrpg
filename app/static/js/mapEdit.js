import { waterProfileControls, normalizeWaterProfile } from './waterProfiles.js';
// static/js/mapEdit.js
import AppState, {
    expandMapToolsPanelOnToolChange,
    setMapEditorAutoExpandEnabled,
    syncMapEditorAutoExpandCheckbox,
} from './ui_interactions.js';
import {
    getHoveredTile,
    updateTileInChunk,
    chunksMap,
    getObjectHeightOffset,
    getObjectDimensions,
    showObjectHighlight,
    hideObjectHighlight,
    createPreviewObject,
    updatePreviewObject,
    removePreviewObject
} from './lobby3d.js';
import { updateWorldSculptHighlight } from './lobby3d.js';
import { showFenceLinePreview, removeFenceLinePreview, sampleWorldGroundHeight } from './lobby3d.js';
import { createFenceLineTool } from './worldFenceTool.js';
import { createWorldRoadTool } from './worldRoadTool.js';
import { initWorldSculpt } from './worldSculpt.js';
import { initWorldEditorHistory } from './worldEditorHistory.js';
import { initWorldClipboardEditor } from './worldClipboardEditor.js';
import { showWorldRoadPreview, removeWorldRoadPreview, renderer } from './lobby3d.js';
import { Server } from './api.js';
import { showNotification } from './utils.js';

import { initWorldNumericInputs, syncWorldNumber, getWorldNumericValue } from './worldNumericInputs.js';
import { initDirectWorldObjectEditor } from './worldObjectEditor.js';
import { WORLD_VEGETATION_TYPES, planWorldVegetationBrush, vegetationStrokeCenters } from './worldVegetationBrush.js';

const CHUNK_SIZE = 32;
let currentLobbyId;
let token;

let currentEditTile = null;
let pendingTileUpdates = [];
let batchUpdateTimeout = null;
let anomalyFieldCatalog = [];
let anomalyCatalog = [];
const WORLD_OBJECT_COLORS = {
    forest: '#315f3b', hamlet: '#aa8665', village: '#b39474',
    road: '#756f61', factory: '#66747b', base: '#6c765d', camp: '#9c8362',
    tree: '#2d5a27', house: '#8b4513', fence: '#ffffff',
};
const WORLD_OBJECT_TYPES = new Set(Object.keys(WORLD_OBJECT_COLORS));
const PROTECTED_BUILDING_TYPES = new Set(['hamlet', 'village', 'factory', 'base', 'camp', 'house', 'fence']);
const WORLD_OBJECT_LABELS = {
    tree: 'Дерево', forest: 'Лес', road: 'Дорога', hamlet: 'Хутор',
    village: 'Деревня', factory: 'Завод', base: 'База', camp: 'Лагерь',
    house: 'Дом', fence: 'Забор', anomaly: 'Аномалия',
};
const placingObjectTiles = new Set();
let selectedWorldObject = 'tree';
let selectedExistingObjectIndex = null;
let selectedExistingObjectSnapshot = null;
let savingExistingObject = false;
let choosingExistingObjectTarget = false;
let toolBeforeTargetChoice = 'select';
let lastPlacedObjectKey = null;
let lastPlacedObjectAt = 0;
let roadLinkActive = false;
let roadLinkFirst = null;
let roadLinkSaving = false;
let vegetationStroke = null;
let roadPointerOnCanvas=false;
const roadRouteTool=createWorldRoadTool({
    request:data=>Server.previewWorldRoad(currentLobbyId,data),
    async save(data){const result=await Server.buildWorldRoad(currentLobbyId,data);for(const u of result.updates)updateTileInChunk(u.chunk_x,u.chunk_y,u.tile_x,u.tile_y,u.updates);},
    preview:showWorldRoadPreview,clearPreview:removeWorldRoadPreview,settings:worldObjectSettings,
    bridges:()=>document.getElementById('world-road-bridges')?.checked===true,
    continueLine:()=>document.getElementById('world-road-continue')?.checked!==false,
    status:message=>{const el=document.getElementById('world-road-route-status');if(el)el.textContent=message;
        const hint=document.getElementById('world-editor-hint');if(hint&&window.isWorldRoadRouteTool?.()&&window.worldEditorTool==='objects')hint.textContent=message;},
    notify:message=>showNotification(message,'error'),
});
const roadPoint=tile=>tile?[((tile.chunkX??tile.chunk?.chunkX)*32+tile.tileX),((tile.chunkY??tile.chunk?.chunkY)*32+tile.tileY)]:null;
window.isWorldRoadRouteTool=()=>selectedWorldObject==='road'&&document.getElementById('world-road-route-enabled')?.checked===true;
window.cancelWorldRoadRoute=()=>roadRouteTool.cancel();
const fenceLineTool=createFenceLineTool({
    getTile:(x,z)=>chunksMap.get(`${Math.floor(x/32)},${Math.floor(z/32)}`)?.tilesData[z%32]?.[x%32],
    getBounds:()=>[window.MAP_CHUNKS_WIDTH*32,window.MAP_CHUNKS_HEIGHT*32],getSettings:worldObjectSettings,
    sampleHeight:sampleWorldGroundHeight,preview:showFenceLinePreview,clearPreview:removeFenceLinePreview,
    schedulePreview:callback=>requestAnimationFrame(callback),
    status:message=>{const element=document.getElementById('world-fence-line-status');if(element)element.textContent=message;},
    notify:message=>showNotification(message,'error'),continueLine:()=>document.getElementById('world-fence-continue')?.checked===true,
    async save(updates){await Server.batchUpdateTiles(currentLobbyId,updates);for(const u of updates)updateTileInChunk(u.chunk_x,u.chunk_y,u.tile_x,u.tile_y,u.updates);},
});
window.cancelWorldFenceLine=()=>fenceLineTool.cancel();
window.isWorldFenceLineTool=()=>selectedWorldObject==='fence' && document.getElementById('world-fence-line-enabled')?.checked===true;

const ANOMALY_TYPE_LABELS = {
    void: 'гравитационная',
    gravity: 'гравитационная',
    electric: 'электрическая',
    fire: 'термическая',
    thermal: 'термическая',
    radiation: 'радиационная',
    acid: 'химическая',
    chemical: 'химическая',
    psi: 'псионическая',
};

export function initMapEdit(lobbyId, authToken) {
    if(!document.getElementById('world-water-profile')){
        const controls=waterProfileControls('world');controls.hidden=window.currentTileType!=='water';
        document.querySelector('[data-world-settings="terrain"]')?.appendChild(controls);
    }
    currentLobbyId = lobbyId;
    token = authToken;
    renderer.domElement.addEventListener('pointerenter',()=>{roadPointerOnCanvas=true;});
    renderer.domElement.addEventListener('pointerleave',()=>{roadPointerOnCanvas=false;if(window.isWorldRoadRouteTool())roadRouteTool.hover(null);});
    // Capture the real target before lobby3d's window pointer handler runs. Do not
    // depend on a pointerenter that may have occurred before editor initialization.
    window.addEventListener('pointermove',event=>{
        roadPointerOnCanvas=event.target===renderer.domElement;
        if(!roadPointerOnCanvas&&window.isWorldRoadRouteTool?.())roadRouteTool.hover(null);
    },{capture:true});
    loadAnomalyFieldCatalog();
    initWorldNumericInputs();
    initWorldEditorHistory(lobbyId, AppState);
    initWorldSculpt(lobbyId, AppState, selectWorldEditorTool);
    initDirectWorldObjectEditor({
        lobbyId, state: AppState, protectedTypes: PROTECTED_BUILDING_TYPES, labels: WORLD_OBJECT_LABELS,
        onDetails(selection) {
            openTileEditModal({ ...selection, tileData: chunksMap.get(`${selection.chunkX},${selection.chunkY}`)
                .tilesData[selection.tileY][selection.tileX] });
            selectExistingWorldObject(selection.index);
        },
    });
    initWorldClipboardEditor(AppState,selectWorldEditorTool);

    document.getElementById('panel-tools')?.addEventListener('click', event => {
        const button = event.target.closest('[data-world-tool]');
        if (button) selectWorldEditorTool(button.dataset.worldTool);
        const objectButton = event.target.closest('[data-world-object]');
        if (objectButton) selectWorldObject(objectButton.dataset.worldObject);
        if (event.target.closest('#world-road-link-button')) {
            if (!roadLinkActive) selectWorldObject('road');
            toggleWorldRoadLink();
        }
    });
    document.addEventListener('keydown', event => {
        if(window.isWorldRoadRouteTool?.()&&!window.isLocationActive&&AppState.editMode&&window.worldEditorTool==='objects'
            &&!event.target.closest('input,textarea,select,[contenteditable="true"]')){
            if(event.key==='Escape'&&roadRouteTool.active){event.preventDefault();event.stopImmediatePropagation();roadRouteTool.cancel();return;}
        }
        if(event.key==='Escape' && fenceLineTool.active){event.preventDefault();event.stopImmediatePropagation();fenceLineTool.cancel();return;}
        if (event.key === 'Escape' && roadLinkActive) toggleWorldRoadLink(false);
        if (event.key === 'Escape' && choosingExistingObjectTarget) {
            event.preventDefault();
            event.stopImmediatePropagation();
            finishExistingObjectTargetChoice();
        }
    });
    document.addEventListener('secondaryclick', event => {
        if (!choosingExistingObjectTarget) return;
        event.preventDefault();
        event.stopImmediatePropagation();
        finishExistingObjectTargetChoice();
    }, { capture: true });
    document.getElementById('panel-tools')?.addEventListener('change', event => {
        if(event.target.id==='world-road-route-enabled'){roadRouteTool.cancel();window.syncWorldVegetationControls?.();updateWorldObjectPreview(getHoveredTile());}
        if(event.target.id==='world-road-bridges')roadRouteTool.refresh();
        if(event.target.id==='world-fence-line-enabled'){fenceLineTool.cancel();window.syncWorldVegetationControls?.();updateWorldObjectPreview(getHoveredTile());}
        if (event.target.id === 'tile-type-select') {
            AppState.setCurrentTileType(event.target.value);
            document.getElementById('world-water-profile').closest('section').hidden=event.target.value!=='water';
        }
        if (event.target.id === 'world-brush-radiation') setWorldBrushRadiation(event.target.value);
        if (event.target.matches('[data-map-editor-auto-expand]')) {
            setMapEditorAutoExpandEnabled(event.target.checked);
        }
    });
    syncMapEditorAutoExpandCheckbox();
    selectWorldEditorTool('select');
    document.getElementById('panel-tools')?.addEventListener('input', event => {
        if (['world-object-color', 'world-object-scale', 'world-object-rotation'].includes(event.target.id)) {
            if(window.isWorldRoadRouteTool?.())roadRouteTool.refresh();
            updateWorldObjectPreview(getHoveredTile());
        }
    });
    document.getElementById('object-type-select')?.addEventListener('change', event => {
        const color = document.getElementById('object-color');
        if (color && WORLD_OBJECT_COLORS[event.target.value]) {
            color.value = WORLD_OBJECT_COLORS[event.target.value];
            updatePreviewFromModal();
        }
    });
    document.getElementById('tile-objects-list')?.addEventListener('click', event => {
        const button = event.target.closest('[data-existing-world-object]');
        if (button) selectExistingWorldObject(Number(button.dataset.existingWorldObject));
    });
    document.getElementById('world-existing-type')?.addEventListener('change', updateExistingBaseOptions);
    document.getElementById('world-existing-save')?.addEventListener('click', saveExistingWorldObject);
    document.getElementById('world-existing-pick-tile')?.addEventListener('click', beginExistingObjectTargetChoice);

    const previewFields = [
        'object-type-select',
        'object-color',
        'object-offset-x',
        'object-offset-z',
        'object-scale',
        'object-rotation'
    ];
    previewFields.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.addEventListener('input', updatePreviewFromModal);
            if (el.tagName === 'SELECT') {
                el.addEventListener('change', updatePreviewFromModal);
            }
        }
    });
}

async function loadAnomalyFieldCatalog() {
    const select = document.getElementById('tile-edit-anomaly-field');
    if (!select) return;
    try {
        const rules = await Server.getWorldRules(currentLobbyId);
        anomalyFieldCatalog = rules.anomaly_fields || [];
        anomalyCatalog = rules.anomalies || [];
        select.innerHTML = '<option value="">Нет аномального поля</option>'
            + anomalyFieldCatalog.map(field => (
                `<option value="${field.source_order}">${field.name} (${field.rank_min}-${field.rank_max})</option>`
            )).join('');
        updateAnomalyFieldRankOptions();
    } catch (error) {
        showNotification(`Не удалось загрузить аномальные поля: ${error.message}`, 'error');
    }
}

export function updateAnomalyFieldRankOptions() {
    const fieldSelect = document.getElementById('tile-edit-anomaly-field');
    const rankSelect = document.getElementById('tile-edit-anomaly-rank');
    if (!fieldSelect || !rankSelect) return;
    const field = anomalyFieldCatalog.find(item => String(item.source_order) === fieldSelect.value);
    rankSelect.disabled = !field;
    rankSelect.innerHTML = field
        ? Array.from(
            { length: field.rank_max - field.rank_min + 1 },
            (_, index) => field.rank_min + index,
        ).map(rank => `<option value="${rank}">${rank}</option>`).join('')
        : '<option value="">-</option>';
}

export function setEditMode(enabled) {
    window.cancelCommonMapTool?.();
    if (!enabled) {
        window.cancelWorldRoadRoute?.();
        window.cancelWorldFenceLine?.();
        toggleWorldRoadLink(false);
        cancelWorldObjectPlacement();
    }
    AppState.setEditMode(enabled);
    if (!enabled) {
        window.clearDirectWorldObjectSelection?.();
        if (choosingExistingObjectTarget) closeTileEditModal();
        toggleWorldRoadLink(false);
        removePreviewObject();
    }
    import('./lobby3d.js').then(module => {
        module.setEditMode(enabled);
    });
    updateGMControlsVisibility();
    const btn = document.getElementById('edit-toggle');
    if (btn) {
        btn.style.background = AppState.editMode ? '#4a6fa5' : '';
    }
}

export function selectWorldEditorTool(tool, { autoExpand = true } = {}) {
    const root = document.getElementById('gm-only-controls');
    if (!root || !['select', 'terrain', 'height', 'sculpt', 'radiation', 'erase', 'objects'].includes(tool)) return;
    const heightMode = document.getElementById('world-sculpt-mode');
    if (tool === 'sculpt' && heightMode.value === 'exact') heightMode.value = 'raise';
    if (tool === 'height' && heightMode.value !== 'exact') tool = 'sculpt';
    const panelTool = tool === 'sculpt' ? 'height' : tool;
    if (window.getWorldSculptState?.().active) window.cancelWorldBrushStroke?.();
    if (window.getWorldEditorHistoryState?.().kind==='brush') window.cancelWorldBrushStroke?.();
    const previousTool = window.worldEditorTool;
    if(tool!=='select')window.cancelWorldClipboard?.();
    if(previousTool==='sculpt'&&tool!=='sculpt')updateWorldSculptHighlight(null);
    if(tool!=='objects')window.cancelWorldRoadRoute?.();
    if(tool!=='objects')window.cancelWorldFenceLine?.();
    window.cancelCommonMapTool?.();
    if (tool !== 'objects') window.endWorldVegetationStroke?.();
    if (tool !== 'select') window.clearDirectWorldObjectSelection?.();
    if (tool !== 'objects' && roadLinkActive) toggleWorldRoadLink(false);
    if (previousTool === 'objects' && tool !== 'objects') cancelWorldObjectPlacement();
    root.dataset.tool = panelTool;
    window.worldEditorTool = tool;
    if (autoExpand) expandMapToolsPanelOnToolChange(previousTool, tool);
    root.querySelectorAll('[data-world-tool]').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.worldTool === panelTool));
    });
    document.getElementById('world-exact-height-controls').hidden = heightMode.value !== 'exact';
    document.getElementById('world-sculpt-controls').hidden = heightMode.value === 'exact';
    document.getElementById('world-sculpt-strength-label').textContent = heightMode.value === 'smooth'
        ? 'Сила сглаживания, 1/с' : 'Сила, уровня/с';
    if (tool === 'objects') updateWorldObjectPreview(getHoveredTile());
    else if (previousTool === 'objects') removePreviewObject();
    const eraser = document.getElementById('eraser-checkbox');
    if (eraser) eraser.checked = tool === 'erase';
    AppState.setEraserMode(tool === 'erase');
    const hint = document.getElementById('world-editor-hint');
    if (hint) hint.textContent = {
        select: 'Выбор без рисования. Двойной клик открывает настройки тайла.',
        terrain: 'Проведите кистью по карте, чтобы изменить покрытие.',
        height: 'Проведите кистью по карте, чтобы изменить высоту.',
        sculpt: 'Удерживайте ЛКМ и ведите кисть. Esc — отмена мазка; Ctrl+Z / Ctrl+Y — история рельефа.',
        radiation: 'Проведите кистью по карте, чтобы изменить радиацию.',
        erase: 'Проведите по карте, чтобы удалить объекты с тайлов.',
        objects: 'Выберите объект и поставьте его кликом по карте. Двойной клик открывает точные настройки тайла.',
    }[tool];
    window.syncWorldVegetationControls?.();
}

export function selectWorldObject(type) {
    if (!WORLD_OBJECT_TYPES.has(type)) return;
    window.cancelWorldFenceLine?.();
    window.cancelWorldRoadRoute?.();
    if (type !== 'road' && roadLinkActive) toggleWorldRoadLink(false);
    selectedWorldObject = type;
    syncWorldNumber('world-object-rotation', 0);
    window.endWorldVegetationStroke?.();
    document.querySelectorAll('[data-world-object]').forEach(button => {
        button.setAttribute('aria-pressed', String(button.dataset.worldObject === type));
    });
    const color = document.getElementById('world-object-color');
    if (color) color.value = WORLD_OBJECT_COLORS[type];
    const activeBaseOption = document.getElementById('world-object-active-base-option');
    if (activeBaseOption) activeBaseOption.hidden = !PROTECTED_BUILDING_TYPES.has(type);
    window.syncWorldVegetationControls?.();
    updateWorldObjectPreview(getHoveredTile());
}

export function cancelWorldObjectPlacement() {
    window.stopMapRotationHold?.();
    if(window.isWorldRoadRouteTool?.()&&roadRouteTool.active)return roadRouteTool.cancel();
    if(window.isWorldFenceLineTool?.() && fenceLineTool.active)return fenceLineTool.cancel();
    if (window.isLocationActive || !AppState.isGM || !AppState.editMode || window.worldEditorTool !== 'objects'
        || !selectedWorldObject || roadLinkActive || choosingExistingObjectTarget) return false;
    selectedWorldObject = null;
    window.endWorldVegetationStroke?.();
    document.querySelectorAll('[data-world-object]').forEach(button => button.setAttribute('aria-pressed', 'false'));
    removePreviewObject();
    window.syncWorldVegetationControls?.();
    return true;
}

export function getWorldObjectRotationContext() {
    if (window.isLocationActive || !AppState.isGM || !AppState.editMode || window.worldEditorTool !== 'objects'
        || !selectedWorldObject || roadLinkActive || choosingExistingObjectTarget
        || window.isWorldRoadRouteTool?.() || window.isWorldFenceLineTool?.()
        || document.getElementById('tile-edit-modal')?.style.display === 'flex') return null;
    return selectedWorldObject;
}
window.getWorldObjectRotationContext = getWorldObjectRotationContext;

export function rotateWorldObjectPlacement(direction, fraction = 1) {
    if(window.isWorldRoadRouteTool?.())return false;
    if(window.isWorldFenceLineTool?.())return false;
    if (window.isLocationActive || !AppState.isGM || !AppState.editMode || window.worldEditorTool !== 'objects'
        || !selectedWorldObject || roadLinkActive || choosingExistingObjectTarget
        || document.getElementById('tile-edit-modal')?.style.display === 'flex') return false;
    if (![-1, 1].includes(direction) || !Number.isFinite(fraction) || fraction <= 0) return false;
    const rotation = ((Number(worldObjectSettings().rotation || 0) - direction * fraction * 15) % 360 + 360) % 360;
    syncWorldNumber('world-object-rotation', rotation);
    updateWorldObjectPreview(getHoveredTile());
    return true;
}
window.cancelWorldObjectPlacement = cancelWorldObjectPlacement;
window.rotateWorldObjectPlacement = rotateWorldObjectPlacement;

function updateWorldRoadLinkStatus(message) {
    const button = document.getElementById('world-road-link-button');
    const status = document.getElementById('world-road-link-status');
    if (button) button.setAttribute('aria-pressed', String(roadLinkActive));
    if (status) status.textContent = message || 'Выберите две соседние клетки с дорогами.';
}

export function toggleWorldRoadLink(enabled = !roadLinkActive) {
    roadLinkActive = Boolean(enabled);
    roadLinkFirst = null;
    updateWorldRoadLinkStatus(roadLinkActive
        ? 'Кликните по первой дороге, затем по соседней дороге. Esc отменяет.'
        : null);
    updateWorldObjectPreview(getHoveredTile());
}

async function connectRoadAtTile(tile) {
    if (roadLinkSaving) return false;
    if (!tile.tileData.objects?.some(object => object.type === 'road')) {
        showNotification('На выбранной клетке нет дороги.');
        return false;
    }
    const point = [tile.chunkX * CHUNK_SIZE + tile.tileX, tile.chunkY * CHUNK_SIZE + tile.tileY];
    if (!roadLinkFirst) {
        roadLinkFirst = point;
        updateWorldRoadLinkStatus(`Первая дорога: (${point[0]}, ${point[1]}). Выберите соседнюю.`);
        return true;
    }
    if (point[0] === roadLinkFirst[0] && point[1] === roadLinkFirst[1]) return false;
    if (Math.max(Math.abs(point[0] - roadLinkFirst[0]), Math.abs(point[1] - roadLinkFirst[1])) !== 1) {
        showNotification('Для соединения выберите соседнюю клетку с дорогой.');
        return false;
    }
    roadLinkSaving = true;
    try {
        const result = await Server.connectWorldRoads(currentLobbyId, roadLinkFirst, point);
        for (const update of result.updates) {
            updateTileInChunk(update.chunk_x, update.chunk_y, update.tile_x, update.tile_y, update.updates);
        }
        roadLinkFirst = null;
        updateWorldRoadLinkStatus('Дороги соединены. Выберите следующую пару или нажмите Esc.');
        showNotification('Дороги соединены.', 'success');
        return true;
    } catch (error) {
        showNotification(error.message, 'error');
        return false;
    } finally {
        roadLinkSaving = false;
    }
}

function worldObjectSettings() {
    const scale = Math.max(0.2, Math.min(2, Number(document.getElementById('world-object-scale')?.value) || 1));
    const rotation = ((getWorldNumericValue('world-object-rotation') || 0) % 360 + 360) % 360;
    const scaleOutput = document.getElementById('world-object-scale-value');
    const rotationOutput = document.getElementById('world-object-rotation-value');
    if (scaleOutput) scaleOutput.textContent = String(scale);
    if (rotationOutput) rotationOutput.textContent = `${rotation}°`;
    const activeBase = PROTECTED_BUILDING_TYPES.has(selectedWorldObject)
        && document.getElementById('world-object-active-base')?.checked;
    return { type: selectedWorldObject, color: document.getElementById('world-object-color')?.value
        || WORLD_OBJECT_COLORS[selectedWorldObject], scale, rotation,
    ...(activeBase ? { is_active: true } : {}) };
}

export function updateWorldObjectPreview(tile) {
    if(AppState.isGM&&AppState.editMode&&!window.isLocationActive&&window.worldEditorTool==='objects'&&window.isWorldRoadRouteTool?.()){
        roadRouteTool.hover(roadPointerOnCanvas&&tile?.tileData?.terrain!=='water'?roadPoint(tile):null);removePreviewObject();return;
    }
    if (document.getElementById('tile-edit-modal')?.style.display === 'flex') return;
    if (window.worldEditorTool === 'select' && window.isDirectWorldObjectTransformActive?.()) return;
    if(AppState.isGM && AppState.editMode && window.worldEditorTool==='objects' && window.isWorldFenceLineTool?.()){
        fenceLineTool.hover(tile);removePreviewObject();return;
    }
    if (choosingExistingObjectTarget) {
        removePreviewObject();
        return;
    }
    if (!AppState.isGM || !AppState.editMode || window.worldEditorTool !== 'objects' || !selectedWorldObject || !tile || roadLinkActive) {
        removePreviewObject();
        return;
    }
    if (WORLD_VEGETATION_TYPES.has(selectedWorldObject)
        && (!(getWorldNumericValue('world-vegetation-density') > 0)
            || (!document.getElementById('world-vegetation-ignore-ground')?.checked
                && ['water', 'rock'].includes(tile.tileData.terrain)))) {
        removePreviewObject();
        return;
    }
    const globalX = tile.chunk.chunkX * CHUNK_SIZE + tile.tileX + 0.5;
    const globalZ = tile.chunk.chunkY * CHUNK_SIZE + tile.tileY + 0.5;
    createPreviewObject({
        chunkX: tile.chunk.chunkX, chunkY: tile.chunk.chunkY,
        tileX: tile.tileX, tileY: tile.tileY, tileData: tile.tileData,
    }, {
        ...worldObjectSettings(),
        offsetX: Math.max(-0.49, Math.min(0.49, (tile.pointX ?? globalX) - globalX)),
        offsetZ: Math.max(-0.49, Math.min(0.49, (tile.pointZ ?? globalZ) - globalZ)),
    });
}

export async function placeWorldObjectAtTile(tile) {
    if(window.isWorldRoadRouteTool?.()&&AppState.isGM&&AppState.editMode&&window.worldEditorTool==='objects'&&!window.isLocationActive&&!choosingExistingObjectTarget){
        removePreviewObject();return roadRouteTool.click(roadPoint(tile),tile?.tileData?.terrain==='water');
    }
    if(window.isWorldFenceLineTool?.() && AppState.isGM && AppState.editMode && window.worldEditorTool==='objects' && !window.isLocationActive && !choosingExistingObjectTarget){
        removePreviewObject();return fenceLineTool.click(tile);
    }
    if (!selectedWorldObject && !choosingExistingObjectTarget && !roadLinkActive) return false;
    if (choosingExistingObjectTarget) {
        document.getElementById('world-existing-tile-x').value = tile.chunkX * CHUNK_SIZE + tile.tileX;
        document.getElementById('world-existing-tile-y').value = tile.chunkY * CHUNK_SIZE + tile.tileY;
        finishExistingObjectTargetChoice();
        await saveExistingWorldObject();
        return true;
    }
    if (roadLinkActive) {
        return AppState.isGM && AppState.editMode && window.worldEditorTool === 'objects' && tile?.tileData
            ? connectRoadAtTile(tile) : false;
    }
    if (window.isWorldVegetationBrush?.()) return placeWorldVegetationBrush(tile);
    if (!AppState.isGM || !AppState.editMode || window.worldEditorTool !== 'objects'
        || !tile?.tileData || tile.tileData.terrain === 'water') {
        if (tile?.tileData?.terrain === 'water') showNotification('На воде этот объект разместить нельзя');
        return false;
    }
    const { chunkX, chunkY, tileX, tileY } = tile;
    const key = `${chunkX},${chunkY},${tileX},${tileY}`;
    if (placingObjectTiles.has(key) || (lastPlacedObjectKey === key && Date.now() - lastPlacedObjectAt < 400)) {
        return false;
    }
    placingObjectTiles.add(key);
    try {
        const globalX = chunkX * CHUNK_SIZE + tileX + 0.5;
        const globalZ = chunkY * CHUNK_SIZE + tileY + 0.5;
        const object = {
            ...worldObjectSettings(),
            x: Math.max(-0.49, Math.min(0.49, (tile.pointX ?? globalX) - globalX)),
            z: Math.max(-0.49, Math.min(0.49, (tile.pointZ ?? globalZ) - globalZ)),
        };
        const objects = [...(tile.tileData.objects || []), object];
        const saved = await handleTileUpdate(chunkX, chunkY, tileX, tileY, { objects });
        if (saved) {
            lastPlacedObjectKey = key;
            lastPlacedObjectAt = Date.now();
        }
        return saved;
    } finally {
        placingObjectTiles.delete(key);
    }
}

window.updateWorldObjectPreview = updateWorldObjectPreview;
window.placeWorldObjectAtTile = placeWorldObjectAtTile;
window.isWorldRoadLinkActive = () => roadLinkActive;

function placeWorldVegetationBrush(tile) {
    if (!AppState.isGM || !AppState.editMode || window.worldEditorTool !== 'objects'
        || window.isLocationActive || !tile?.tileData) return false;
    const center = { x: tile.chunkX * CHUNK_SIZE + tile.tileX, y: tile.chunkY * CHUNK_SIZE + tile.tileY };
    const centers = vegetationStrokeCenters(vegetationStroke?.previous, center);
    const radius = AppState.brushRadius;
    const updates = planWorldVegetationBrush({
        centers, radius, settings: worldObjectSettings(), seed: Number(currentLobbyId),
        density: getWorldNumericValue('world-vegetation-density'),
        ignoreGround: document.getElementById('world-vegetation-ignore-ground')?.checked === true,
        width: window.MAP_CHUNKS_WIDTH * CHUNK_SIZE, height: window.MAP_CHUNKS_HEIGHT * CHUNK_SIZE,
        visited: vegetationStroke?.visited,
        anchor: radius === 0 && centers.length === 1 && Number.isFinite(tile.pointX) && Number.isFinite(tile.pointZ)
            ? { x: tile.pointX, z: tile.pointZ } : null,
        getTile(x, y) {
            return chunksMap.get(`${Math.floor(x / CHUNK_SIZE)},${Math.floor(y / CHUNK_SIZE)}`)
                ?.tilesData[y % CHUNK_SIZE]?.[x % CHUNK_SIZE];
        },
    });
    if (vegetationStroke) vegetationStroke.previous = center;
    for (const item of updates) {
        pendingTileUpdates.push(item);
        updateTileInChunk(item.chunk_x, item.chunk_y, item.tile_x, item.tile_y, item.updates);
    }
    if (updates.length) scheduleBatchUpdate();
    return updates.length > 0;
}

window.isWorldVegetationBrush = () => WORLD_VEGETATION_TYPES.has(selectedWorldObject)
    && !roadLinkActive && !choosingExistingObjectTarget;
window.beginWorldVegetationStroke = () => {
    vegetationStroke = window.isWorldVegetationBrush() ? { previous: null, visited: new Set() } : null;
};
window.endWorldVegetationStroke = () => { vegetationStroke = null; };
window.syncWorldVegetationControls = () => {
    const vegetation = WORLD_VEGETATION_TYPES.has(selectedWorldObject);
    const root = document.getElementById('gm-only-controls');
    if (root) {
        root.dataset.vegetationBrush = String(vegetation);
        root.dataset.objectType = selectedWorldObject;
    }
    const options = document.getElementById('world-vegetation-options');
    const fenceOptions=document.getElementById('world-fence-options');
    if(fenceOptions)fenceOptions.hidden=selectedWorldObject!=='fence';
    const rotationOption=document.getElementById('world-object-rotation-option');
    if(rotationOption)rotationOption.hidden=Boolean(window.isWorldFenceLineTool?.()||window.isWorldRoadRouteTool?.());
    if (options) options.hidden = !vegetation;
    if(window.worldEditorTool==='objects'&&!vegetation){
        const hint=document.getElementById('world-editor-hint');
        if(hint)hint.textContent=window.isWorldFenceLineTool?.()
            ? 'Забор: начало → конец. Продолжайте кликами; Esc / ПКМ — завершить. Инструмент «Выбор» позволяет изменить или удалить связанный участок.'
            : 'Выберите объект и поставьте его кликом по карте. Двойной клик открывает точные настройки тайла.';
    }
    if(window.worldEditorTool==='objects'&&window.isWorldRoadRouteTool?.()){
        const hint=document.getElementById('world-editor-hint');
        if(hint)hint.textContent=document.getElementById('world-road-route-status')?.textContent||'';
    }
    if (window.worldEditorTool === 'objects' && vegetation) {
        const hint = document.getElementById('world-editor-hint');
        if (hint) hint.textContent = '';
    }
};

export function getEditMode() {
    return AppState.editMode;
}

export function setBrushRadius(radius) {
    AppState.setBrushRadius(radius);
    import('./lobby3d.js').then(module => module.setBrushRadius(radius));
}

export function toggleEraserMode(enabled) {
    AppState.setEraserMode(enabled);
}

export function getCurrentTileType() { return AppState.currentTileType; }
export function getTileHeight() { return AppState.tileHeight; }
export function getEraserMode() { return AppState.eraserMode; }

function updateGMControlsVisibility() {
    const toolsPanel = document.getElementById('panel-tools');
    if (toolsPanel && !window.isLocationActive) toolsPanel.style.display = AppState.isGM && AppState.editMode ? 'flex' : 'none';
    const gmControls = document.getElementById('gm-only-controls');
    const createLocationBtn = document.getElementById('create-location-btn');
    if (gmControls) {
        gmControls.style.display = (AppState.isGM && AppState.editMode) ? 'flex' : 'none';
    }
    if (createLocationBtn) {
        createLocationBtn.style.display = (AppState.isGM && AppState.editMode) ? 'inline-flex' : 'none';
    }
}

function scheduleBatchUpdate() {
    if(window.getWorldEditorHistoryState?.().kind==='brush')return;
    if (batchUpdateTimeout) clearTimeout(batchUpdateTimeout);
    batchUpdateTimeout = setTimeout(() => {
        if (pendingTileUpdates.length > 0) {
            const merged = new Map();
            for (const item of pendingTileUpdates) {
                const key = `${item.chunk_x},${item.chunk_y},${item.tile_x},${item.tile_y}`;
                const previous = merged.get(key);
                merged.set(key, previous
                    ? { ...item, updates: { ...previous.updates, ...item.updates } }
                    : item);
            }
            pendingTileUpdates = [];
            Server.batchUpdateTiles(currentLobbyId, [...merged.values()]).catch(err => {
                showNotification(err.message);
            });
        }
    }, 500);
}
window.clearPendingWorldBrushUpdates=()=>{clearTimeout(batchUpdateTimeout);batchUpdateTimeout=null;pendingTileUpdates=[];};

let waterBasinPending=false;
let waterLevelPending=false;
export async function levelWorldWaterBody(centerTile){
    if(!AppState.isGM||waterLevelPending)return;
    if(centerTile?.tileData?.terrain!=='water'){
        showNotification('Выберите клетку водоёма');return;
    }
    const x=(centerTile.chunk?.chunkX??centerTile.chunkX)*32+centerTile.tileX;
    const z=(centerTile.chunk?.chunkY??centerTile.chunkY)*32+centerTile.tileY;
    waterLevelPending=true;
    try{
        const result=await Server.setWorldWaterLevel(currentLobbyId,{
            x,z,mode:document.getElementById('world-water-level-mode')?.value||'minimum',
            height:window.tileHeight,
        });
        for(const u of result.updates)updateTileInChunk(u.chunk_x,u.chunk_y,u.tile_x,u.tile_y,u.updates);
    }catch(error){showNotification(error.message);}finally{waterLevelPending=false;}
}
window.levelWorldWaterBody=levelWorldWaterBody;
export async function applyWorldWaterProfile(centerTile,profile,radius=0){
    if(!AppState.isGM)return;
    const cx=centerTile.chunk?.chunkX??centerTile.chunkX,cz=centerTile.chunk?.chunkY??centerTile.chunkY,
        x=cx*32+centerTile.tileX,z=cz*32+centerTile.tileY;
    profile=normalizeWaterProfile(profile);
    if(document.getElementById('world-water-basin')?.checked){
        if(waterBasinPending)return;waterBasinPending=true;
        try{
            const result=await Server.setWorldWaterProfile(currentLobbyId,{x,z,profile});
            for(const u of result.updates)updateTileInChunk(u.chunk_x,u.chunk_y,u.tile_x,u.tile_y,u.updates);
        }catch(error){showNotification(error.message);}finally{waterBasinPending=false;}
        return;
    }
    for(let dz=-radius;dz<=radius;dz++)for(let dx=-radius;dx<=radius;dx++){
        const wx=x+dx,wz=z+dz,chunkX=Math.floor(wx/32),chunkY=Math.floor(wz/32),tx=wx-chunkX*32,ty=wz-chunkY*32;
        const tile=chunksMap.get(`${chunkX},${chunkY}`)?.tilesData[ty]?.[tx];
        if(tile?.terrain!=='water'||normalizeWaterProfile(tile.water_profile)===profile)continue;
        const updates={water_profile:profile};pendingTileUpdates.push({chunk_x:chunkX,chunk_y:chunkY,tile_x:tx,tile_y:ty,updates});
        updateTileInChunk(chunkX,chunkY,tx,ty,updates);
    }
    scheduleBatchUpdate();
}

export function applyBrush(centerTile, updates, radius) {
    if (document.getElementById('location-container')?.style.display === 'block') return;
    if (!AppState.isGM) {
        showNotification('Только ГМ может редактировать тайлы');
        return;
    }
    if(window.getWorldEditorHistoryState&&!window.getWorldEditorHistoryState().kind){
        if(!window.beginWorldEditorStroke('brush'))return;
        queueMicrotask(()=>window.endWorldEditorStroke(false));
    }
    if(updates.terrain==='water')updates={...updates,water_profile:updates.water_profile??document.getElementById('world-water-profile')?.value??'auto'};
    if(updates.water_profile!==undefined&&(updates.terrain!=='water'||document.getElementById('world-water-basin')?.checked))
        return applyWorldWaterProfile(centerTile,updates.water_profile,radius);
    const allowedFields = ['terrain', 'height', 'radiation', 'objects', 'water_profile'];
    const filteredUpdates = {};
    for (const key of allowedFields) {
        if (updates[key] !== undefined) {
            filteredUpdates[key] = updates[key];
        }
    }
    if (Object.keys(filteredUpdates).length === 0) return;

    let chunkX, chunkY, tileX, tileY;
    if (centerTile.chunk) {
        chunkX = centerTile.chunk.chunkX;
        chunkY = centerTile.chunk.chunkY;
        tileX = centerTile.tileX;
        tileY = centerTile.tileY;
    } else {
        chunkX = centerTile.chunkX;
        chunkY = centerTile.chunkY;
        tileX = centerTile.tileX;
        tileY = centerTile.tileY;
    }

    const centerGlobalX = chunkX * CHUNK_SIZE + tileX;
    const centerGlobalY = chunkY * CHUNK_SIZE + tileY;
    const maxChunkX = window.MAP_CHUNKS_WIDTH - 1;
    const maxChunkY = window.MAP_CHUNKS_HEIGHT - 1;
    const maxGlobalX = (maxChunkX + 1) * CHUNK_SIZE;
    const maxGlobalY = (maxChunkY + 1) * CHUNK_SIZE;

    for (let dx = -radius; dx <= radius; dx++) {
        for (let dy = -radius; dy <= radius; dy++) {
            const targetGlobalX = centerGlobalX + dx;
            const targetGlobalY = centerGlobalY + dy;
            if (targetGlobalX < 0 || targetGlobalX >= maxGlobalX ||
                targetGlobalY < 0 || targetGlobalY >= maxGlobalY) {
                continue;
            }
            const targetChunkX = Math.floor(targetGlobalX / CHUNK_SIZE);
            const targetChunkY = Math.floor(targetGlobalY / CHUNK_SIZE);
            const targetTileX = targetGlobalX % CHUNK_SIZE;
            const targetTileY = targetGlobalY % CHUNK_SIZE;

            pendingTileUpdates.push({
                chunk_x: targetChunkX,
                chunk_y: targetChunkY,
                tile_x: targetTileX,
                tile_y: targetTileY,
                updates: filteredUpdates
            });
            updateTileInChunk(targetChunkX, targetChunkY, targetTileX, targetTileY, filteredUpdates);
        }
    }
    scheduleBatchUpdate();
}

export async function handleTileUpdate(chunkX, chunkY, tileX, tileY, updates) {
    if (!AppState.isGM) {
        showNotification('Только ГМ может редактировать тайлы');
        return;
    }
    const allowedFields = ['terrain', 'height', 'objects', 'name', 'radiation', 'anomaly_field', 'water_profile'];
    const filteredUpdates = {};
    for (const key of allowedFields) {
        if (updates[key] !== undefined) {
            filteredUpdates[key] = updates[key];
        }
    }
    if (Object.keys(filteredUpdates).length === 0) return;

    try {
        await Server.updateTile(currentLobbyId, chunkX, chunkY, tileX, tileY, filteredUpdates);
        updateTileInChunk(chunkX, chunkY, tileX, tileY, filteredUpdates);
        return true;
    } catch (error) {
        showNotification(error.message);
        return false;
    }
}

export function openTileEditModal(tile) {
    if (!AppState.isGM) {
        showNotification('Только ГМ может редактировать тайлы');
        return;
    }
    window.clearDirectWorldObjectSelection?.();
    currentEditTile = tile;
    selectedExistingObjectIndex = null;
    selectedExistingObjectSnapshot = null;
    updateTileEditModal();
    document.getElementById('tile-edit-modal').style.display = 'flex';
    updatePreviewFromModal();
}

export function closeTileEditModal() {
    document.getElementById('tile-edit-modal').style.display = 'none';
    hideObjectHighlight();
    removePreviewObject();
    currentEditTile = null;
    selectedExistingObjectIndex = null;
    selectedExistingObjectSnapshot = null;
    choosingExistingObjectTarget = false;
}

function beginExistingObjectTargetChoice() {
    if (!currentEditTile || selectedExistingObjectIndex === null) return;
    if (window.isWorldTravelSelectionActive?.()) {
        showNotification('Сначала завершите выбор маршрута группы', 'error');
        return;
    }
    toolBeforeTargetChoice = window.worldEditorTool || 'select';
    choosingExistingObjectTarget = true;
    document.getElementById('tile-edit-modal').style.display = 'none';
    selectWorldEditorTool('objects', { autoExpand: false });
    removePreviewObject();
    showNotification('Выберите клетку для перемещения. Esc или ПКМ отменяет.', 'success');
}

function finishExistingObjectTargetChoice() {
    choosingExistingObjectTarget = false;
    selectWorldEditorTool(toolBeforeTargetChoice, { autoExpand: false });
    document.getElementById('tile-edit-modal').style.display = 'flex';
    removePreviewObject();
    highlightSelectedWorldObject();
}

function escapeWorldObjectText(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    })[character]);
}

function worldObjectDisplayName(object) {
    return object.name || object.anomalyName || WORLD_OBJECT_LABELS[object.type] || object.type || 'Объект';
}

function updateExistingBaseOptions() {
    const isBase = PROTECTED_BUILDING_TYPES.has(document.getElementById('world-existing-type')?.value);
    const options = document.getElementById('world-existing-base-options');
    if (options) options.hidden = !isBase;
    if (!isBase) {
        document.getElementById('world-existing-active').checked = false;
    }
}

function renderExistingWorldObject() {
    const editor = document.getElementById('world-existing-object-editor');
    if (!editor) return;
    const object = currentEditTile?.tileData.objects?.[selectedExistingObjectIndex];
    if (selectedExistingObjectIndex === null || !object) {
        editor.hidden = true;
        return;
    }
    editor.hidden = false;
    document.getElementById('world-existing-object-title').textContent = worldObjectDisplayName(object);
    document.getElementById('world-existing-name').value = object.name || '';
    const typeInput = document.getElementById('world-existing-type');
    typeInput.querySelector('[data-current-unlisted]')?.remove();
    if (!WORLD_OBJECT_TYPES.has(object.type)) {
        const option = document.createElement('option');
        option.value = object.type;
        option.textContent = WORLD_OBJECT_LABELS[object.type] || object.type;
        option.dataset.currentUnlisted = 'true';
        typeInput.appendChild(option);
    }
    typeInput.value = object.type;
    typeInput.disabled = !WORLD_OBJECT_TYPES.has(object.type);
    document.getElementById('world-existing-color').value = /^#[0-9a-f]{6}$/i.test(object.color || '')
        ? object.color : '#808080';
    document.getElementById('world-existing-scale').value = object.scale ?? 1;
    document.getElementById('world-existing-rotation').value = object.rotation ?? 0;
    document.getElementById('world-existing-offset-x').value = object.x ?? 0;
    document.getElementById('world-existing-offset-z').value = object.z ?? 0;
    document.getElementById('world-existing-tile-x').value = currentEditTile.chunkX * CHUNK_SIZE + currentEditTile.tileX;
    document.getElementById('world-existing-tile-y').value = currentEditTile.chunkY * CHUNK_SIZE + currentEditTile.tileY;
    document.getElementById('world-existing-active').checked = object.is_active === true;
    updateExistingBaseOptions();
}

export function selectExistingWorldObject(index) {
    const object = currentEditTile?.tileData.objects?.[index];
    if (!object) return;
    selectedExistingObjectIndex = index;
    selectedExistingObjectSnapshot = JSON.parse(JSON.stringify(object));
    updateTileEditModal();
    highlightObject(index);
}

export function highlightSelectedWorldObject() {
    if (selectedExistingObjectIndex === null) hideObjectHighlight();
    else highlightObject(selectedExistingObjectIndex);
}
window.highlightSelectedWorldObject = highlightSelectedWorldObject;

function updateTileEditModal() {
    if (!currentEditTile) return;
    hideObjectHighlight();
    const tileData = currentEditTile.tileData;
    if (selectedExistingObjectIndex !== null
        && JSON.stringify(tileData.objects?.[selectedExistingObjectIndex]) !== JSON.stringify(selectedExistingObjectSnapshot)) {
        selectedExistingObjectIndex = null;
        selectedExistingObjectSnapshot = null;
    }

    let infoHtml = `
        <p>Координаты: (${currentEditTile.chunkX * CHUNK_SIZE + currentEditTile.tileX}, ${currentEditTile.chunkY * CHUNK_SIZE + currentEditTile.tileY})</p>
        <p>Высота: ${tileData.height}</p>
        <p>Радиация: ${tileData.radiation ?? 0}</p>
    `;
    document.getElementById('tile-edit-info').innerHTML = infoHtml;

    const objectsListDiv = document.getElementById('tile-objects-list');
    if (!objectsListDiv) return;

    if (!tileData.objects || tileData.objects.length === 0) {
        objectsListDiv.innerHTML = '<p>Нет объектов</p>';
    } else {
        let listHtml = '<ul style="list-style: none; padding: 0; margin: 0;">';
        tileData.objects.forEach((obj, index) => {
            let typeDisplay = obj.type;
            if (obj.type === 'anomaly' && obj.anomalyType) {
                const anomalyName = obj.anomalyName || anomalyCatalog.find(
                    anomaly => anomaly.key === obj.anomalyKey
                )?.name;
                const anomalyType = ANOMALY_TYPE_LABELS[obj.anomalyCategory]
                    || ANOMALY_TYPE_LABELS[obj.anomalyType]
                    || 'неизвестного типа';
                typeDisplay = anomalyName
                    ? `аномалия «${anomalyName}» · ${anomalyType}`
                    : `аномалия · ${anomalyType}`;
            }
            listHtml += `
                <li style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; padding: 3px; background: rgba(255,255,255,0.1); border-radius: 4px;"
                    onmouseenter="window.highlightObject(${index})"
                    onmouseleave="window.highlightSelectedWorldObject()">
                    <button type="button" class="btn btn-sm btn-secondary world-existing-object-select"
                        data-existing-world-object="${index}" aria-pressed="${selectedExistingObjectIndex === index}">
                        ${escapeWorldObjectText(worldObjectDisplayName(obj))} · ${escapeWorldObjectText(typeDisplay)}
                    </button>
                    <button class="btn btn-sm btn-danger" onclick="window.removeObjectFromTile(${index})" style="padding: 2px 8px;">✕</button>
                </li>
            `;
        });
        listHtml += '</ul>';
        objectsListDiv.innerHTML = listHtml;
    }
    renderExistingWorldObject();

    document.getElementById('tile-edit-terrain').value = tileData.terrain;
    syncWorldNumber('tile-edit-height', tileData.height);
    document.getElementById('tile-edit-height-value').textContent = String(tileData.height);
    syncWorldNumber('tile-edit-radiation', tileData.radiation ?? 0);
    document.getElementById('tile-edit-radiation-value').textContent = String(tileData.radiation ?? 0);
    syncWorldNumber('tile-edit-height');
    syncWorldNumber('tile-edit-radiation');
    const fieldSelect = document.getElementById('tile-edit-anomaly-field');
    const selectedField = anomalyFieldCatalog.find(field => field.name === tileData.anomaly_field?.name);
    if (fieldSelect) fieldSelect.value = selectedField ? String(selectedField.source_order) : '';
    updateAnomalyFieldRankOptions();
    const rankSelect = document.getElementById('tile-edit-anomaly-rank');
    if (rankSelect && selectedField) rankSelect.value = String(tileData.anomaly_field?.rank || selectedField.rank_min);
    if (selectedExistingObjectIndex !== null) highlightObject(selectedExistingObjectIndex);
}

export async function applyAnomalyFieldChange() {
    if (!currentEditTile) return;
    const sourceOrder = document.getElementById('tile-edit-anomaly-field')?.value;
    const profile = anomalyFieldCatalog.find(field => String(field.source_order) === sourceOrder);
    const rank = Number(document.getElementById('tile-edit-anomaly-rank')?.value || 0);
    const anomalyField = profile ? {
        name: profile.name,
        field_type: profile.field_type,
        hazard: profile.hazard,
        rank: Math.max(profile.rank_min, Math.min(profile.rank_max, rank)),
    } : null;
    await handleTileUpdate(
        currentEditTile.chunkX,
        currentEditTile.chunkY,
        currentEditTile.tileX,
        currentEditTile.tileY,
        { anomaly_field: anomalyField },
    );
    currentEditTile.tileData.anomaly_field = anomalyField;
    updateTileEditModal();
    showNotification(anomalyField ? 'Аномальное поле сохранено' : 'Аномальное поле удалено', 'success');
}

export async function applyTerrainChange() {
    if (!currentEditTile) return;
    const newTerrain = document.getElementById('tile-edit-terrain').value;
    // Legacy local-only floors have no selectable world option. Keep their saved type.
    if (!newTerrain) return;
    await handleTileUpdate(
        currentEditTile.chunkX,
        currentEditTile.chunkY,
        currentEditTile.tileX,
        currentEditTile.tileY,
        { terrain: newTerrain }
    );
    const chunkKey = `${currentEditTile.chunkX},${currentEditTile.chunkY}`;
    const chunkEntry = chunksMap.get(chunkKey);
    if (chunkEntry) {
        currentEditTile.tileData = chunkEntry.tilesData[currentEditTile.tileY][currentEditTile.tileX];
        updateTileEditModal();
    }
}

export async function applyHeightChange() {
    if (!currentEditTile) return;
    const newHeight = getWorldNumericValue('tile-edit-height');
    await handleTileUpdate(
        currentEditTile.chunkX,
        currentEditTile.chunkY,
        currentEditTile.tileX,
        currentEditTile.tileY,
        { height: newHeight }
    );
    const chunkKey = `${currentEditTile.chunkX},${currentEditTile.chunkY}`;
    const chunkEntry = chunksMap.get(chunkKey);
    if (chunkEntry) {
        currentEditTile.tileData = chunkEntry.tilesData[currentEditTile.tileY][currentEditTile.tileX];
        updateTileEditModal();
    }
}

export async function addObjectToTile() {
    if (!currentEditTile) return;
    const tile = currentEditTile.tileData;
    const selectValue = document.getElementById('object-type-select').value;
    const color = document.getElementById('object-color').value;
    const offsetX = parseFloat(document.getElementById('object-offset-x').value) || 0;
    const offsetZ = parseFloat(document.getElementById('object-offset-z').value) || 0;
    const scale = parseFloat(document.getElementById('object-scale').value) || 1.0;
    const rotation = parseFloat(document.getElementById('object-rotation').value) || 0;

    let newObject;
    const anomaly = getAnomalyTypeFromSelect(selectValue);
    if (anomaly) {
        newObject = {
            type: 'anomaly',
            anomalyType: anomaly.visual,
            anomalyKey: anomaly.key,
            anomalyName: anomaly.name || anomaly.key,
            anomalyRank: anomaly.rank || null,
            anomalyCategory: anomaly.category || null,
            x: offsetX,
            z: offsetZ,
            scale: scale,
            rotation: rotation,
            color: color
        };
    } else {
        newObject = {
            type: selectValue,
            x: offsetX,
            z: offsetZ,
            scale: scale,
            rotation: rotation,
            color: color
        };
    }

    const objects = tile.objects ? [...tile.objects, newObject] : [newObject];
    await handleTileUpdate(
        currentEditTile.chunkX,
        currentEditTile.chunkY,
        currentEditTile.tileX,
        currentEditTile.tileY,
        { objects: objects }
    );
    const chunkKey = `${currentEditTile.chunkX},${currentEditTile.chunkY}`;
    const chunkEntry = chunksMap.get(chunkKey);
    if (chunkEntry) {
        currentEditTile.tileData = chunkEntry.tilesData[currentEditTile.tileY][currentEditTile.tileX];
        updateTileEditModal();
    }
    removePreviewObject();
}

export async function saveExistingWorldObject() {
    if (!currentEditTile || selectedExistingObjectIndex === null || savingExistingObject) return;
    const original = selectedExistingObjectSnapshot;
    const field = id => document.getElementById(`world-existing-${id}`);
    const numericFields = {
        x: 'offset-x', z: 'offset-z', scale: 'scale', rotation: 'rotation',
    };
    const changes = {};
    const name = field('name').value.trim();
    if (name !== (original.name || '')) changes.name = name;
    const type = field('type').value;
    if (type !== original.type) changes.type = type;
    const originalColor = /^#[0-9a-f]{6}$/i.test(original.color || '') ? original.color : '#808080';
    if (field('color').value.toLowerCase() !== originalColor.toLowerCase()) {
        changes.color = field('color').value;
    }
    for (const [key, inputId] of Object.entries(numericFields)) {
        const input = field(inputId);
        if (input.value.trim() === '' || !input.checkValidity()) {
            showNotification(`Проверьте значение поля ${key}`, 'error');
            return;
        }
        const value = Number(input.value);
        if (!Number.isFinite(value)) return;
        const fallback = key === 'scale' ? 1 : 0;
        if (value !== Number(original[key] ?? fallback)) changes[key] = value;
    }
    if (PROTECTED_BUILDING_TYPES.has(type)) {
        const checked = field('active').checked;
        if (checked !== (original.is_active === true)) changes.is_active = checked;
    }
    const tileXInput = field('tile-x');
    const tileYInput = field('tile-y');
    const tileX = Number(tileXInput.value);
    const tileY = Number(tileYInput.value);
    if (!tileXInput.value.trim() || !tileYInput.value.trim()
        || !Number.isInteger(tileX) || !Number.isInteger(tileY)
        || !tileXInput.checkValidity() || !tileYInput.checkValidity()) {
        showNotification('Укажите целые координаты клетки назначения', 'error');
        return;
    }
    const sourceX = currentEditTile.chunkX * CHUNK_SIZE + currentEditTile.tileX;
    const sourceY = currentEditTile.chunkY * CHUNK_SIZE + currentEditTile.tileY;
    if (tileX === sourceX && tileY === sourceY && !Object.keys(changes).length) return;
    const button = document.getElementById('world-existing-save');
    savingExistingObject = true;
    button.disabled = true;
    try {
        const result = await Server.editWorldObject(currentLobbyId, {
            source: { tile_x: sourceX, tile_y: sourceY, index: selectedExistingObjectIndex },
            target: { tile_x: tileX, tile_y: tileY },
            expected: original,
            changes,
        });
        for (const item of result.updates) {
            updateTileInChunk(item.chunk_x, item.chunk_y, item.tile_x, item.tile_y, item.updates);
        }
        const targetChunkX = Math.floor(tileX / CHUNK_SIZE);
        const targetChunkY = Math.floor(tileY / CHUNK_SIZE);
        const entry = chunksMap.get(`${targetChunkX},${targetChunkY}`);
        if (entry) {
            currentEditTile = {
                chunkX: targetChunkX, chunkY: targetChunkY,
                tileX: tileX % CHUNK_SIZE, tileY: tileY % CHUNK_SIZE,
                tileData: entry.tilesData[tileY % CHUNK_SIZE][tileX % CHUNK_SIZE],
            };
            selectedExistingObjectIndex = result.index;
            selectedExistingObjectSnapshot = JSON.parse(JSON.stringify(
                currentEditTile.tileData.objects[result.index],
            ));
            updateTileEditModal();
            highlightObject(result.index);
        } else {
            closeTileEditModal();
        }
        showNotification('Объект сохранён', 'success');
    } catch (error) {
        showNotification(error.message, 'error');
    } finally {
        savingExistingObject = false;
        button.disabled = false;
    }
}

export async function clearObjectsFromTile() {
    if (!currentEditTile) return;
    await handleTileUpdate(
        currentEditTile.chunkX,
        currentEditTile.chunkY,
        currentEditTile.tileX,
        currentEditTile.tileY,
        { objects: [] }
    );
    const chunkKey = `${currentEditTile.chunkX},${currentEditTile.chunkY}`;
    const chunkEntry = chunksMap.get(chunkKey);
    if (chunkEntry) {
        selectedExistingObjectIndex = null;
        selectedExistingObjectSnapshot = null;
        currentEditTile.tileData = chunkEntry.tilesData[currentEditTile.tileY][currentEditTile.tileX];
        updateTileEditModal();
    }
    removePreviewObject();
}

export async function removeObjectFromTile(index) {
    if (!currentEditTile) return;
    const objects = currentEditTile.tileData.objects ? [...currentEditTile.tileData.objects] : [];
    if (index < 0 || index >= objects.length) return;
    objects.splice(index, 1);
    await handleTileUpdate(
        currentEditTile.chunkX,
        currentEditTile.chunkY,
        currentEditTile.tileX,
        currentEditTile.tileY,
        { objects: objects }
    );
    const chunkKey = `${currentEditTile.chunkX},${currentEditTile.chunkY}`;
    const chunkEntry = chunksMap.get(chunkKey);
    if (chunkEntry) {
        selectedExistingObjectIndex = null;
        selectedExistingObjectSnapshot = null;
        currentEditTile.tileData = chunkEntry.tilesData[currentEditTile.tileY][currentEditTile.tileX];
        updateTileEditModal();
    }
    removePreviewObject();
}

export function highlightObject(index) {
    if (!currentEditTile) return;
    const tileData = currentEditTile.tileData;
    const obj = tileData.objects[index];
    if (!obj) return;

    const worldX = currentEditTile.chunkX * CHUNK_SIZE + currentEditTile.tileX + 0.5 + (obj.x || 0);
    const worldZ = currentEditTile.chunkY * CHUNK_SIZE + currentEditTile.tileY + 0.5 + (obj.z || 0);
    const height = tileData.height || 1.0;
    const yOffset = getObjectHeightOffset(obj.type, obj.anomalyType);
    const worldY = height + yOffset * (obj.scale || 1.0);
    const dimensions = getObjectDimensions(obj.type, obj.anomalyType, obj.scale || 1.0);
    showObjectHighlight(worldX, worldY, worldZ, dimensions);
}

function getAnomalyTypeFromSelect(value) {
    const map = {
        'anomaly_electric': 'electric',
        'anomaly_fire': 'fire',
        'anomaly_acid': 'acid',
        'anomaly_void': 'void'
    };
    if (map[value]) return { visual: map[value], key: null };
    if (!String(value).startsWith('anomaly_')) return null;
    const key = String(value).slice('anomaly_'.length);
    const electric = ['vspishka', 'pautina', 'tesla', 'katushka', 'kapkan', 'paralizator', 'akkumulyator', 'ionny_tuman'];
    const thermal = ['banya', 'parilka', 'mochalka', 'kipyatilnik', 'morozilnik', 'uley', 'metelitsa', 'gril'];
    const radiation = ['zarosli', 'svetlyachki', 'mor', 'zapovednik', 'mertvy_koster', 'solyariy', 'radioaktivny_roy', 'bagrovaya_luna'];
    const chemical = ['varevo', 'soda', 'ezhinoe_oblako', 'rzhavchina', 'mogilshchik', 'chuma', 'myshelovka', 'gniloy_razlom'];
    const psi = ['ekho', 'mirazh', 'starshina', 'tuman_poteryannykh', 'tma', 'piyavka', 'kolodets', 'mozgotrobilka'];
    const visual = electric.includes(key) ? 'electric'
        : thermal.includes(key) ? 'fire'
            : radiation.includes(key) ? 'radiation'
                : chemical.includes(key) ? 'acid'
                    : psi.includes(key) ? 'psi' : 'void';
    const profile = anomalyCatalog.find(anomaly => anomaly.key === key);
    return {
        visual: profile?.visual || visual,
        key,
        name: profile?.name || key,
        rank: profile?.rank || null,
        category: profile?.category || null,
    };
}

export function setBrushRadiusFromInput(value) {
    const radius = Number(value);
    if (String(value).trim() === '' || !Number.isFinite(radius)) return;
    AppState.setBrushRadius(Math.max(0, Math.min(4, Math.round(radius))));
    document.getElementById('brush-radius-value').textContent = `${2 * AppState.brushRadius + 1}×${2 * AppState.brushRadius + 1}`;
    document.getElementById('brush-radius').value = AppState.brushRadius;
    syncWorldNumber('brush-radius');
    setBrushRadius(AppState.brushRadius);
}

export function setTileHeightFromInput(value) {
    const height = Number(value);
    if (String(value).trim() === '' || !Number.isFinite(height)) return;
    AppState.setTileHeight(Math.max(0.5, Math.min(3, height)));
    document.getElementById('tile-height-value').textContent = String(AppState.tileHeight);
    syncWorldNumber('tile-height', AppState.tileHeight);
}

export function setEraserModeFromInput(checked) {
    selectWorldEditorTool(checked ? 'erase' : 'select');
}

export function setWorldBrushRadiation(value) {
    if (String(value).trim() === '' || !Number.isFinite(Number(value))) return;
    const radiation = Math.max(0, Math.min(10, Number(value)));
    window.worldBrushRadiation = radiation;
    const output = document.getElementById('world-brush-radiation-value');
    if (output) output.textContent = String(radiation);
    syncWorldNumber('world-brush-radiation', radiation);
}

export function updateTileEditHeight(value) {
    document.getElementById('tile-edit-height-value').textContent = String(Number(value));
}

export function updateObjectOffsetX(value) {
    document.getElementById('object-offset-x-value').textContent = String(Number(value));
    updatePreviewFromModal();
}

export function updateObjectOffsetZ(value) {
    document.getElementById('object-offset-z-value').textContent = String(Number(value));
    updatePreviewFromModal();
}

export function updateObjectScale(value) {
    document.getElementById('object-scale-value').textContent = String(Number(value));
    updatePreviewFromModal();
}

export function updateObjectRotation(value) {
    document.getElementById('object-rotation-value').textContent = value + '°';
    updatePreviewFromModal();
}

export function updateTileEditRadiation(value) {
    document.getElementById('tile-edit-radiation-value').textContent = String(Number(value));
}

export async function applyRadiationChange() {
    if (!currentEditTile) return;
    const newRadiation = getWorldNumericValue('tile-edit-radiation');
    await handleTileUpdate(
        currentEditTile.chunkX,
        currentEditTile.chunkY,
        currentEditTile.tileX,
        currentEditTile.tileY,
        { radiation: newRadiation }
    );
    const chunkKey = `${currentEditTile.chunkX},${currentEditTile.chunkY}`;
    const chunkEntry = chunksMap.get(chunkKey);
    if (chunkEntry) {
        currentEditTile.tileData = chunkEntry.tilesData[currentEditTile.tileY][currentEditTile.tileX];
        updateTileEditModal();
    }
}

function updatePreviewFromModal() {
    if (!currentEditTile) return;
    const selectValue = document.getElementById('object-type-select').value;
    const color = document.getElementById('object-color').value;
    const offsetX = parseFloat(document.getElementById('object-offset-x').value) || 0;
    const offsetZ = parseFloat(document.getElementById('object-offset-z').value) || 0;
    const scale = parseFloat(document.getElementById('object-scale').value) || 1.0;
    const rotation = parseFloat(document.getElementById('object-rotation').value) || 0;

    let type, anomalyType;
    const anomalyTypeFromSelect = getAnomalyTypeFromSelect(selectValue);
    if (anomalyTypeFromSelect) {
        type = 'anomaly';
        anomalyType = anomalyTypeFromSelect.visual;
    } else {
        type = selectValue;
        anomalyType = null;
    }

    const params = {
        type,
        anomalyType,
        color,
        offsetX,
        offsetZ,
        scale,
        rotation
    };
    createPreviewObject(currentEditTile, params);
}

window.applyBrush = applyBrush;
