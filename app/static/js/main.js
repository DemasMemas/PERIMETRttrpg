import { waterProfileControls } from './waterProfiles.js';
import {initPdaProximity} from './pdaProximity.js';
// static/js/main.js
import { initWorldNumericInputs, getWorldNumericValue, bindNumericInput, readNumericInput, syncNumericInput } from './worldNumericInputs.js';

import { addTerrainMaterialOptions } from './terrainMaterialCatalog.js';
import { initSocket } from './socketHandlers.js';
import { initCharacters, initMutantForm, loadLobbyCharacters, showCreateCharacterForm, showCreateMutantForm, showNpcGeneratorSettings } from './characters.js';
import { initCommonMapTools } from './commonMapTools.js';
import { initLobbyData, loadLobbyInfo, loadAllChunks } from './lobbyData.js';
import { setCurrentLobbyId, toggleParticipants, toggleSettings, showSettingsTab, closeVisibilityModal,
saveVisibility, unbanUserHandler, closeSettings, openSettings, updateParticipantsList } from './ui.js';
import { initMapEdit, setEditMode, setBrushRadius, toggleEraserMode, applyBrush, openTileEditModal, closeTileEditModal,
 applyTerrainChange, applyHeightChange, addObjectToTile, clearObjectsFromTile, removeObjectFromTile, highlightObject,
 getEditMode, setBrushRadiusFromInput, setTileHeightFromInput, setEraserModeFromInput, updateTileEditHeight,
 selectWorldEditorTool,
 updateObjectOffsetX, updateObjectOffsetZ, updateObjectScale, updateObjectRotation,
 setWorldBrushRadiation, applyRadiationChange, updateTileEditRadiation, applyAnomalyFieldChange,
 updateAnomalyFieldRankOptions} from './mapEdit.js';
import { hideObjectHighlight, camera, getHoveredTile } from './lobby3d.js';
import { hideGlobalCanvas, showGlobalCanvas, controls as globalControls } from './lobby3d.js';
import { showNotification, getErrorMessage } from './utils.js';
import { Server } from './api.js';
import AppState, {
    collapseMapToolsPanel, expandMapToolsPanel, expandMapToolsPanelOnToolChange, initDraggablePanels, initHotkeys,
    syncMapEditorAutoExpandCheckbox,
} from './ui_interactions.js';
import { initMapEditorQuickMenu, closeMapEditorQuickMenu } from './mapEditorQuickMenu.js';
import { initMapRotationHold } from './rotationHold.js';
import { isTypingTarget as isRotationTypingTarget } from './cameraPan.js';
import { initWeather, applyWeather } from './weather.js';
import { createLocationLightingEditor } from './locationLightingEditor.js';
import { LOCATION_ASSETS } from './locationAssetCatalog.js';
import { assetDimensions, validAssetDimensions, assetDimensionLimits } from './locationAssetDimensions.js';
import { STOREY_HEIGHT } from './locationArchitecture.js';
import { initMarkers, setupMarkerInteraction, closeMarkerEditModal, saveMarkerEdit, submitCreateMarker,
openCreateMarkerModal, openCreateMarkerModalAtCenter, fillCenterCoordinates, deleteMarker,
fillEditCenterCoordinates, pickTileForMarker } from './markers.js';
import { openCharacterSheet, closeCharacterSheet, exportCharacter, importCharacter } from './characterSheet.js';
import { setCurrentLobbyId as setCharLobbyId } from './characterSheet.js';
import {
    initLocationScene, loadLocationInStages, startLocationLoadDiagnostics,
    updateCharacterPosition, setCurrentLocationId, getCurrentLocationId,
    loadCombatStartRequests, handleCombatStartRequest, handleCombatStartRequestResolved,
    addDeleteLocationButton, setDeleteButtonVisible, addEditLocationButton, setEditButtonVisible,
    addLocationGridButton, addLocationRoofButton, addLocationFloorMenu,
    getLocationViewLevel, setLocationViewLevel, destroyLocationScene,
    setLocationBrushRadius, setLocationBrushHeight, setLocationBrushTerrain, setLocationGrassSubtype,
    setLocationSculptSettings, getLocationSculptSettings,
    setLocationEraserMode, setLocationEditMode,
    getLocationEditMode, getHoveredTileCoords, updateHighlightByCoords, setLocationBrushRadiation, setLocationBrushObjectMode,
    setLocationBrushObjectType, setLocationBrushObjectColor, setLocationBrushObjectOffsetX, setLocationBrushObjectOffsetZ,
    setLocationBrushObjectScale, setLocationBrushObjectRotation, setLocationBuildMode, setLocationStructurePreset,
    setLocationHouseDrawMode, generateLocationBuildingAnomalies, refreshLocationFloorMenu,
    refreshLocationBuildPreview,
    setLocationStructureWidth, setLocationStructureDepth, setLocationStructureHeight, setLocationStructureColor,
    setLocationStructureRotation, rotateLocationPlacement, isLocationEditorTransformActive,
    applyLocationLighting, setLocationLightingPreset,
    getLocationEditorHistoryState, replayLocationObjectHistory,
    cancelLocationPaste,
    paintLocationDarknessAtTile, saveLocationEnvironment,
    getLocationLightingPreset, setLocationAssetPreset, clearLocationAssetPreset,
    setLocationAssetElevation,
    setLocationAssetDimensions, getSelectedLocationEditorAsset, getLocationBuildingIds, getLocationBuildingLevels,
    canRestoreGeneratedLocationFloor, restoreGeneratedLocationFloor, generateLocationFloorInterior, generateLocationFurniture,
    getSelectedLocationFloorFit, fitSelectedLocationFloorToBuilding,
    addFloorAboveSelectedLocationAsset, beginMoveSelectedLocationAsset,
    beginRotateSelectedLocationAsset, beginResizeSelectedLocationAsset, cancelLocationAssetResize,
    setLocationAssetResizeAxis, getLocationAssetResizeState,
    deleteSelectedLocationAsset, toggleSelectedLocationAssetsHidden, toggleSelectedLocationFire,
    updateSelectedLocationAssetDetails, previewSelectedLocationLamp, updateSelectedLocationRoof, generateSelectedLocationRoof,
    setSelectedLocationTraversalCategory, setSelectedLocationCover,
    setLocationDarknessOverlayVisible,
} from './locationScene.js';
import { getUserColorHex, updateMyColor } from './colors.js';
import { openLobbyRestModal, closeLobbyRestModal, saveLobbyTimeActivity, startLobbyRest } from './rest.js';
import { initTimePlanning } from './timePlanning.js';
import {
    beginWorldGroupCreation,
    beginWorldMapEventCreation,
    closeWorldTravelModal,
    initWorldTravel,
    refreshWorldTravel,
    openWorldTravelModal,
} from './worldTravel.js';

function buildLocationAnomalyOptions(anomalies) {
    const categoryMeta = {
        gravity: ['Гравитационные', '◌'],
        electric: ['Электрические', '⚡'],
        thermal: ['Термические', '🔥'],
        radiation: ['Радиационные', '☢'],
        chemical: ['Химические', '⌁'],
        psi: ['Псионические', '◉'],
    };
    const groups = new Map();
    (Array.isArray(anomalies) ? anomalies : []).forEach(anomaly => {
        const category = anomaly.category || 'other';
        if (!groups.has(category)) groups.set(category, []);
        groups.get(category).push(anomaly);
    });
    return [...groups.entries()].map(([category, entries]) => {
        const [label, icon] = categoryMeta[category] || ['Прочие', '◆'];
        return `<optgroup label="Аномалии: ${label}">${entries.map(anomaly =>
            `<option value="anomaly_${anomaly.key}" data-anomaly-name="${anomaly.name}" data-anomaly-visual="${anomaly.visual || 'void'}">${icon} ${anomaly.name} · ранг ${anomaly.rank}</option>`
        ).join('')}</optgroup>`;
    }).join('');
}
import * as THREE from 'three';

// A canvas click ends a sculpt-control draft before pointer handlers start a stroke.
window.addEventListener('pointerdown', event => {
    if (event.target.matches?.('#canvas-container canvas, #location-canvas canvas')
        && document.activeElement?.matches('[data-canvas-editor-control]')) document.activeElement.blur();
}, { capture: true });

initWeather();

const token = localStorage.getItem('access_token');
const pathParts = window.location.pathname.split('/').filter(p => p !== '');
let currentLobbyId = null;
const locationParam = Number(new URLSearchParams(window.location.search).get('location'));
const directLocationId = Number.isSafeInteger(locationParam) && locationParam > 0 ? locationParam : null;
window.locationDirectOpenPending = Boolean(directLocationId);

if (pathParts.length >= 2 && pathParts[0] === 'lobbies') {
    currentLobbyId = pathParts[1];
    window.currentLobbyId = currentLobbyId;
}

const savedCharacterId = parseInt(localStorage.getItem('currentCharacterId') || '0', 10);
if (savedCharacterId > 0) {
    window.currentCharacterId = savedCharacterId;
}
const savedLocationCharacterId = parseInt(localStorage.getItem('selectedLocationCharacterId') || '0', 10);
if (savedLocationCharacterId > 0) {
    window.currentLocationCharacterId = savedLocationCharacterId;
}

if (!token || window.authSession?.isExpired()) {
    showNotification('Вы не авторизованы');
    window.location.href = '/';
}
if (!currentLobbyId) {
    showNotification('Некорректный URL комнаты');
    window.location.href = '/';
}

// Инициализация модулей
setCurrentLobbyId(currentLobbyId);
setCharLobbyId(currentLobbyId);
initLobbyData(currentLobbyId);
initCharacters(currentLobbyId, token);
initMutantForm();
initMapEdit(currentLobbyId, token);
const socket = initSocket(currentLobbyId, token);
initTimePlanning(currentLobbyId, socket);

// Инициализация маркеров
initMarkers(currentLobbyId, token, socket);
setupMarkerInteraction();
initCommonMapTools(socket);

// ========== Глобальные флаги ==========
window.isLocationActive = false; // флаг активной локации
window.socket = socket;
window.currentLocationId = null;
initDraggablePanels();
window.currentLocationCharacterId = window.currentLocationCharacterId || null;
initPdaProximity(currentLobbyId,socket);

// ========== Перенаправление UI-вызовов в зависимости от активного режима ==========

// Сохраняем оригинальные функции из глобальной карты
const originalSetBrushRadiusFromInput = window.setBrushRadiusFromInput || setBrushRadiusFromInput;
const originalSetTileHeightFromInput = window.setTileHeightFromInput || setTileHeightFromInput;
const originalSetEraserModeFromInput = window.setEraserModeFromInput || setEraserModeFromInput;
const originalToggleEditMode = window.toggleEditMode || (() => setEditMode(!getEditMode()));

// Переопределяем глобальные функции для UI
window.setBrushRadiusFromInput = function(value) {
    if (window.isLocationActive) {
        setLocationBrushRadius(parseInt(value));
        const radiusSpan = document.getElementById('brush-radius-value');
        const radiusSlider = document.getElementById('brush-radius');
        if (radiusSpan) radiusSpan.textContent = value;
        if (radiusSlider) radiusSlider.value = value;
    } else {
        originalSetBrushRadiusFromInput(value);
    }
};

window.setTileHeightFromInput = function(value) {
    if (window.isLocationActive) {
        setLocationBrushHeight(parseFloat(value));
    } else {
        originalSetTileHeightFromInput(value);
    }
};

window.setEraserModeFromInput = function(checked) {
    if (window.isLocationActive) {
        setLocationEraserMode(checked);
    } else {
        originalSetEraserModeFromInput(checked);
    }
};

window.toggleEditMode = function() {
    if (window.isLocationActive) {
        const newMode = !getLocationEditMode();
        setLocationEditMode(newMode);
        const btn = document.getElementById('edit-toggle');
        if (btn) btn.style.background = newMode ? '#4a6fa5' : '';
        if (!newMode) collapseMapToolsPanel();
        else expandMapToolsPanel();
    } else {
        originalToggleEditMode();
        if (!getEditMode()) collapseMapToolsPanel();
    }
};
window.rotateLocationPlacement = rotateLocationPlacement;
window.isLocationEditorTransformActive = isLocationEditorTransformActive;

initMapRotationHold(isRotationTypingTarget);
initMapEditorQuickMenu({
    isEnabled: () => Boolean(window.isGM && (window.isLocationActive ? getLocationEditMode() : getEditMode())),
    isLocation: () => Boolean(window.isLocationActive),
    selectTool: tool => {
        if (window.isLocationActive) window.selectLocationEditorTool?.(tool);
        else selectWorldEditorTool(tool);
    },
    locationAction: action => {
        if (action === 'move') return beginMoveSelectedLocationAsset();
        if (action === 'rotate') return beginRotateSelectedLocationAsset();
        if (action === 'resize') {
            if (!getSelectedLocationEditorAsset()) {
                window.selectLocationEditorTool?.('erase');
                return true;
            }
            beginResizeSelectedLocationAsset();
            return true;
        }
        return false;
    },
});

// Также перенаправляем функции applyBrush, если они вызываются из UI (но для локации они не используются)
// Оставим как есть, так как applyBrush в локации не нужна.

// ========== Функции для работы с локацией ==========
let currentLocationData = null;

function resumeWorldMapLoad() {
    const loadingToken = showSceneLoading('world');
    const signal = sceneLoadingController.signal;
    const onProgress = (percent, detail) => setSceneLoadingProgress(loadingToken, 10 + percent * 0.9, detail);
    setSceneLoadingProgress(loadingToken, 10);
    (async () => {
        await loadAllChunks({ signal, onProgress });
        if (!isCurrentSceneLoading(loadingToken)) return;
        if (window.worldLoadMetrics?.pausedForLocation && !window.isLocationActive) {
            await loadAllChunks({ signal, onProgress });
        }
        if (!isCurrentSceneLoading(loadingToken) || window.isLocationActive) return;
        if (!window.worldLoadMetrics?.complete) throw new Error('World map load incomplete');
        finishSceneLoading(loadingToken);
        Promise.resolve().then(refreshWorldTravel).catch(error =>
            console.error('World travel refresh failed', error));
    })().catch(error => {
        console.error('World map load failed', error);
        failSceneLoading(loadingToken, 'Не удалось загрузить карту мира.');
    });
}

function setLocationViewActive(active) {
    closeSettings();
    const timePanel = document.getElementById('location-time-panel');
    if (timePanel) timePanel.hidden = true;
    document.body.classList.toggle('location-view-active', active);
    if (!active) {
        const menu = document.querySelector('.lobby-menu');
        const anchor = document.getElementById('edit-toggle');
        const clock = document.querySelector('.lobby-time');
        if (clock && menu) menu.querySelector('.lobby-name')?.after(clock);
        document.getElementById('location-time-btn')?.remove();
        document.getElementById('location-weather-btn')?.remove();
        for (const id of ['actions-toggle', 'chat-toggle', 'common-tools-toggle']) {
            const launcher = document.getElementById(id);
            if (menu && launcher) menu.insertBefore(launcher, anchor);
        }
    }
    document.getElementById('canvas-container').style.display = active ? 'none' : 'block';
    document.getElementById('location-container').style.display = active ? 'block' : 'none';
}

function addLocationRoomControls() {
    if (!window.isGM || window.locationScoutView) return;
    const group = document.getElementById('location-view-toggle-group');
    const clock = document.querySelector('.lobby-time');
    if (!group || !clock) return;
    let panel = document.getElementById('location-time-panel');
    if (!panel) {
        panel = document.createElement('div');
        panel.id = 'location-time-panel';
        panel.className = 'settings-panel';
        panel.hidden = true;
        panel.setAttribute('role', 'region');
        panel.setAttribute('aria-label', 'Глобальное время комнаты');
        panel.innerHTML = '<div class="settings-header"><span>Глобальное время</span><button type="button" class="location-room-close" aria-label="Закрыть">×</button></div>';
        panel.querySelector('button').onclick = () => { panel.hidden = true; document.getElementById('location-time-btn')?.focus(); };
        panel.addEventListener('keydown', event => {
            if (event.key === 'Escape') { event.stopPropagation(); panel.querySelector('button').click(); }
        });
        document.body.appendChild(panel);
    }
    // Переносим исходные поля вместе с обработчиками сохранения мирового времени.
    panel.appendChild(clock);
    const add = (id, label, callback) => {
        document.getElementById(id)?.remove();
        const button = document.createElement('button');
        button.id = id; button.type = 'button'; button.className = 'menu-item location-room-control';
        button.textContent = label; button.title = label === 'Время' ? 'Глобальное время комнаты' : 'Погода комнаты';
        button.onclick = callback; group.appendChild(button);
    };
    add('location-time-btn', 'Время', () => { closeSettings(); panel.hidden = !panel.hidden; });
    add('location-weather-btn', 'Погода', () => { panel.hidden = true; openSettings('weather'); });
}

function clearLocationEditorSubscriptions() {
    for (const [event, key] of [
        ['location-editor-selection', 'locationAssetSelectionHandler'],
        ['location-editor-buildings-changed', 'locationBuildingListHandler'],
        ['location-editor-active-building', 'locationActiveBuildingHandler'],
        ['location-editor-resize', 'locationAssetResizeHandler'],
    ]) {
        if (window[key]) window.removeEventListener(event, window[key]);
        delete window[key];
    }
}

window.enterLocation = async function(locationId, { scoutView = null } = {}) {
    window.locationScoutView=scoutView;
    clearLocationEditorSubscriptions();
    window.cancelCommonMapTool?.();
    window.clearDirectWorldObjectSelection?.();
    const loadingToken = showSceneLoading('location');
    const signal = sceneLoadingController.signal;
    closeMapEditorQuickMenu();
    if (window._locationEventCleanup) {
        window._locationEventCleanup();
        window._locationEventCleanup = null;
    }
    try {
        if (typeof window.resetHoveredMarker === 'function') {
            window.resetHoveredMarker();
        }
        // Устанавливаем флаг, что мы в локации
        window.isLocationActive = true;
        window.locationEditorTool = 'select';
        window.currentLocationId = locationId;

        // Скрываем глобальную информационную панель
        const globalTileInfo = document.getElementById('tile-info');
        if (globalTileInfo) globalTileInfo.style.display = 'none';

        const requestStartedAt = performance.now();
        const data = await Server.getLocationDetail(currentLobbyId, locationId, { signal });
        if (!isCurrentSceneLoading(loadingToken)) return;
        setSceneLoadingLocationName(loadingToken, data.name);
        setSceneLoadingProgress(loadingToken, 15, 'Создаём сцену подлокации.');
        const networkMs = performance.now() - requestStartedAt;
        currentLocationData = data;
        setCurrentLocationId(locationId);

        // Скрываем глобальную 3D-сцену
        setLocationViewActive(true);
        hideGlobalCanvas();

        // Инициализируем сцену локации
        const initStartedAt = performance.now();
        initLocationScene('location-canvas');
        startLocationLoadDiagnostics(networkMs, performance.now() - initStartedAt);
        const loaded = await loadLocationInStages(data, {
            signal,
            onProgress: ({ percent, detail }) => setSceneLoadingProgress(loadingToken, percent, detail),
        });
        if (!loaded || !isCurrentSceneLoading(loadingToken)) return;
        addLocationRoofButton();
        addLocationGridButton();
        addLocationFloorMenu();
        addLocationRoomControls();

        setLocationBrushRadius(window.brushRadius);
        setLocationBrushHeight(window.tileHeight);
        setLocationBrushTerrain(window.currentTileType);

        if (socket) {
            window.currentLocationCharacterId = null;
            socket.emit('join_location', {
                token,
                location_id: locationId,
                character_id: null,
            });
        }
        if (window.isGM&&!scoutView) loadCombatStartRequests();

        if (window.isGM&&!scoutView) {
            const locationWorldRules = await Server.getWorldRules(currentLobbyId, { signal }).catch(() => ({ anomalies: [] }));
            if (!isCurrentSceneLoading(loadingToken)) return;
            window.locationAnomalyCatalog = locationWorldRules.anomalies || [];
            // Кнопки удаления и редактирования параметров
            addDeleteLocationButton(async () => { await deleteCurrentLocation(locationId); });
            setDeleteButtonVisible(true);
            addEditLocationButton(() => openLocationEditModal(currentLocationData));
            setEditButtonVisible(true);

            const toolsPanel = document.getElementById('panel-tools');
            if (toolsPanel) {
                toolsPanel.style.display = 'flex';
                const editorBuildingId = getLocationBuildingIds()[0]
                    || `building-${globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)}`;
                toolsPanel.classList.add('is-location-editor');
                toolsPanel.classList.toggle('location-editor-idle', !window.locationEditMode);
                if (!window._originalToolsContent) {
                    window._originalToolsContent = toolsPanel.querySelector('.panel-content').innerHTML;
                }
                const panelContent = toolsPanel.querySelector('.panel-content');
                panelContent.innerHTML = `
                    <div style="display: flex; gap: 10px; flex-wrap: wrap; align-items: center;">
                        <label style="display: flex; align-items: center; gap: 5px;">
                            <input type="checkbox" id="loc-edit-toggle-checkbox"> Режим редактирования
                        </label>
                        <div style="display: flex; align-items: center; gap: 5px;">
                            <span>Инструмент:</span>
                            <select id="loc-build-mode" class="form-control" style="width: auto;">
                                <option value="terrain">Ландшафт и декор</option>
                                <option value="structure">Строительство</option>
                            </select>
                        </div>
                        <div style="display: flex; align-items: center; gap: 5px;">
                            <span>Тип:</span>
                            <select id="loc-edit-terrain" class="form-control" style="width: auto;">
                                <option value="grass">🌿 Трава</option>
                                <option value="sand">🏜️ Песок</option>
                                <option value="rock">⛰️ Камень</option>
                                <option value="swamp">💧 Болото</option>
                                <option value="water">🌊 Вода</option>
                            </select>
                        </div>
                        <label id="loc-grass-subtype-field" style="display: flex; align-items: center; gap: 5px;">
                            Подтип травы:
                            <select id="loc-grass-subtype" class="form-control" style="width: auto;">
                                <option value="plain">Обычная земля</option>
                                <option value="forest">Лесная подстилка</option>
                            </select>
                        </label>
                        <div style="display: flex; align-items: center; gap: 5px;">
                            <span>Радиус:</span>
                            <input type="range" id="loc-edit-radius" min="0" max="5" value="0">
                            <input class="form-control" type="number" id="loc-edit-radius-number" data-world-number="loc-edit-radius" min="0" max="5" step="1" aria-label="Радиус кисти" style="width:84px;">
                            <span id="loc-radius-value">0</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 5px;">
                            <span>Высота:</span>
                            <input type="range" id="loc-edit-height" min="-3.0" max="3.0" step="0.1" value="1.0">
                            <input class="form-control" type="number" id="loc-edit-height-number" data-world-number="loc-edit-height" min="-3" max="3" step="any" aria-label="Высота кисти" style="width:84px;">
                            <span id="loc-height-value">1.0</span>
                        </div>
                        <label style="display: flex; align-items: center; gap: 5px;">
                            <input type="checkbox" id="loc-eraser"> Ластик
                        </label>
                        <hr style="width:100%; margin:5px 0;">
                        <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center;">
                            <strong>Конструкция</strong>
                            <select id="loc-structure-preset" class="form-control" style="width: auto;">
                                <option value="wall">Стена</option>
                                <option value="floor">Пол</option>
                                <option value="door">Дверь</option>
                                <option value="table">Стол</option>
                                <option value="chair">Стул</option>
                                <option value="box">Ящик</option>
                                <option value="military_crate">Военный ящик</option>
                                <option value="barrel">Бочка</option>
                                <option value="shelf">Стеллаж</option>
                                <option value="fence">Забор</option>
                            </select>
                            <label id="loc-structure-width-field">Ширина <input id="loc-structure-width" type="number" min="0.2" max="30" step="0.1" value="3" style="width:58px;"></label>
                            <label id="loc-structure-depth-field">Глубина <input id="loc-structure-depth" type="number" min="0.1" max="30" step="0.1" value="0.2" style="width:58px;"></label>
                            <label id="loc-structure-height-field">Высота <input id="loc-structure-height" type="number" min="0.1" max="20" step="0.1" value="2.4" style="width:58px;"></label>
                            <label>Здание <select id="loc-structure-building" class="form-control" style="width:auto;"></select></label>
                            <button type="button" id="loc-restore-generated-floor" hidden>Восстановить пол дома</button>
                            <button type="button" id="loc-generate-interior">Изменить планировку этажа…</button>
                            <button type="button" id="loc-generate-furniture">Расставить мебель…</button>
                            <input id="loc-structure-building-id" type="hidden" value="${editorBuildingId}">
                            <label>Этаж для изменений <select id="loc-structure-level" class="form-control"></select></label>
                            <label id="loc-structure-roof-type-field" style="display:none;">Крыша <select id="loc-structure-roof-type" class="form-control" style="width:auto;"><option value="gable">Двускатная</option><option value="flat">Плоская</option><option value="none">Без автоматической крыши</option></select></label>
                            <label id="loc-structure-rotation-field">Поворот, ° <input id="loc-structure-rotation" class="form-control" type="number" min="0" max="359" step="15" value="0" style="width:72px;" title="Q / E: поворот на 15°"></label>
                            <label>Класс укрытия <select id="loc-structure-cover-class" class="form-control" style="width:auto;">
                                <option value="conditional">Условное</option>
                                <option value="flimsy">Хлипкое</option>
                                <option value="medium" selected>Средней прочности</option>
                                <option value="strong">Прочное</option>
                                <option value="very_strong">Очень прочное</option>
                                <option value="titanium">Титановое</option>
                                <option value="special">Особое</option>
                            </select></label>
                            <label>Цвет <input id="loc-structure-color" type="color" value="#8b6b4f"></label>
                            <span style="font-size:12px; opacity:.8;">Выберите структуру и кликните по тайлу</span>
                        </div>
                        <label style="display: flex; align-items: center; gap: 5px;">
                            <input type="checkbox" id="loc-obj-mode"> Режим объектов
                        </label>
                        <label style="display: flex; align-items: center; gap: 5px;">
                            <input type="checkbox" id="loc-rad-mode"> Режим радиации
                        </label>
                        <div style="display: flex; align-items: center; gap: 5px;">
                            <span>Радиация:</span>
                            <input type="range" id="loc-edit-radiation" min="0" max="10" step="0.5" value="0">
                            <input class="form-control" type="number" id="loc-edit-radiation-number" data-world-number="loc-edit-radiation" min="0" max="10" step="any" aria-label="Радиация" style="width:84px;">
                            <span id="loc-radiation-value">0.0</span>
                        </div>
                        <div style="display: flex; align-items: center; gap: 5px;">
                            <select id="loc-obj-type" class="form-control" style="width: auto;">
                                <option value="tree">🌲 Дерево</option>
                                <option value="rock">🪨 Камень</option>
                                ${buildLocationAnomalyOptions(locationWorldRules.anomalies)}
                            </select>
                            <input type="color" id="loc-obj-color" value="#2d5a27">
                        </div>
                        <div style="display: flex; align-items: center; gap: 5px;">
                            <span>X:</span>
                            <input type="range" id="loc-obj-offset-x" min="-0.5" max="0.5" step="0.01" value="0">
                            <span id="loc-obj-offset-x-value">0.00</span>
                            <span>Z:</span>
                            <input type="range" id="loc-obj-offset-z" min="-0.5" max="0.5" step="0.01" value="0">
                            <span id="loc-obj-offset-z-value">0.00</span>
                            <span>Масштаб:</span>
                            <input type="range" id="loc-obj-scale" min="0.2" max="2.0" step="0.05" value="1.0">
                            <span id="loc-obj-scale-value">1.00</span>
                            <span>Поворот:</span>
                            <input type="range" id="loc-obj-rotation" min="0" max="359" step="1" value="0">
                            <span id="loc-obj-rotation-value">0°</span>
                        </div>
                    </div>
                `;

                const toolsRoot = panelContent.firstElementChild;
                toolsRoot.className = 'map-editor map-editor-inspector-grid location-editor-root';
                toolsRoot.style.cssText = '';
                const createToolSection = (title, elements) => {
                    const section = document.createElement('section');
                    section.className = 'map-editor-section';
                    const heading = document.createElement('strong');
                    heading.textContent = title;
                    heading.className = 'map-editor-section-heading';
                    section.appendChild(heading);
                    elements.forEach(element => {
                        const node = document.getElementById(element);
                        if (!node) return;
                        const block = node.closest('label, div') || node;
                        block.style.display = 'flex';
                        block.style.flexWrap = 'wrap';
                        block.style.alignItems = 'center';
                        block.style.gap = '6px';
                        section.appendChild(block);
                    });
                    toolsRoot.appendChild(section);
                    return section;
                };
                const sections = {
                    select: createToolSection('Выбор', []),
                    brush: createToolSection('Кисть', ['loc-edit-radius']),
                    terrain: createToolSection('Ландшафт', ['loc-edit-terrain', 'loc-grass-subtype']),
                    height: createToolSection('Высота', ['loc-edit-height']),
                    radiation: createToolSection('Радиация', ['loc-edit-radiation']),
                };
                const autoExpandLabel = document.createElement('label');
                autoExpandLabel.innerHTML = '<input type="checkbox" data-map-editor-auto-expand> Раскрывать панель при смене инструмента';
                const editModeLabel = document.getElementById('loc-edit-toggle-checkbox').closest('label');
                editModeLabel.style.cssText = '';
                editModeLabel.className = 'location-edit-toggle';
                // Move the existing controls before collecting unused legacy controls.
                const common = document.createElement('div');
                common.className = 'location-editor-common';
                common.append(editModeLabel, autoExpandLabel);
                const historyControls = document.createElement('span');
                historyControls.className = 'location-history-controls';
                historyControls.innerHTML = `<button type="button" data-location-undo title="Отмена (Ctrl+Z)" aria-label="Отмена (Ctrl+Z)">↶</button><button type="button" data-location-redo title="Повтор (Ctrl+Y / Ctrl+Shift+Z)" aria-label="Повтор (Ctrl+Y / Ctrl+Shift+Z)">↷</button>`;
                const syncHistory = () => {
                    const state = getLocationEditorHistoryState();
                    historyControls.querySelector('[data-location-undo]').disabled = state.busy || !state.undo;
                    historyControls.querySelector('[data-location-redo]').disabled = state.busy || !state.redo;
                };
                historyControls.querySelector('[data-location-undo]').onclick = () => replayLocationObjectHistory(false);
                historyControls.querySelector('[data-location-redo]').onclick = () => replayLocationObjectHistory(true);
                if (window.locationHistoryUiListener) window.removeEventListener('location-editor-history', window.locationHistoryUiListener);
                window.locationHistoryUiListener = syncHistory;
                window.addEventListener('location-editor-history', syncHistory);
                syncHistory();
                const help = document.createElement('details');
                help.className = 'map-editor-help location-editor-help';
                help.innerHTML = `
                    <summary>Клавиши</summary>
                    <dl>
                        <div><dt>История редактора</dt><dd>Ctrl+Z — отмена · Ctrl+Y / Ctrl+Shift+Z — повтор. Один мазок — одно действие. В полях ввода действуют обычные сочетания текста. История хранится до выхода или перезагрузки сцены.</dd></div>
                        <div><dt>Копирование</dt><dd>Ctrl+C — выбранный объект или группа · Ctrl+V — предпросмотр копии · ЛКМ — вставить · Esc / ПКМ — отменить. Этаж выбирается обычными кнопками. Буфер действует в текущей подлокации.</dd></div>
                        <div><dt>Инструменты</dt><dd>1 / V — выбор · 2 — ландшафт · 3 — высота · 4 — радиация · 5 — ластик · 6 / B — ассеты · 7 — строительство · 8 — свет. Удерживайте Q для кругового меню.</dd></div>
                        <div><dt>Режим и размещение</dt><dd>E — редактирование. При размещении ассета Q / E поворачивают его на 15°. R без доступного изменения размера переключает ластик.</dd></div>
                        <div><dt>Выбор объектов</dt><dd>Shift + клик — несколько объектов · короткий ПКМ — снять выделение или отменить размещение · H — скрыть / показать · Delete — удалить.</dd></div>
                        <div><dt>Перемещение и поворот</dt><dd>G — переместить · T — повернуть мышью. Shift при повороте — шаг 15°, Ctrl — вокруг центра группы. ЛКМ — подтвердить · Esc — отменить.</dd></div>
                        <div><dt>Размер</dt><dd>R — размер одного объекта · X / Y / Z — ось. Для постройки тяните грань: Shift — симметрично, Ctrl — шаг 0,1 м (пол — 1 м), отпускание ЛКМ — сохранить. Для декора двигайте мышь и подтвердите ЛКМ. Esc — отменить / выйти. + / = / − — масштаб выделенных объектов по всем осям.</dd></div>
                        <div><dt>Кисть и точный ввод</dt><dd>Alt + = / − — радиус · [ / ] — тип покрытия · = / − без выделенного объекта — высота. Ползунки: высота — 0,1; радиация — 0,5. Числовые поля сохраняют точность; Enter или потеря фокуса подтверждают ввод.</dd></div>
                        <div><dt>Камера</dt><dd>W / A / S / D — движение · удержание и движение ПКМ — вращение · колесо — приближение.</dd></div>
                    </dl>`;
                help.querySelector('summary').addEventListener('keydown', event => {
                    if (event.key !== 'Enter' && event.key !== ' ') return;
                    event.preventDefault();
                    event.stopPropagation();
                    if (!event.repeat) help.open = !help.open;
                });
                common.appendChild(help);
                const legacyControls = document.createElement('div');
                legacyControls.hidden = true;
                Array.from(toolsRoot.children)
                    .filter(node => !Object.values(sections).includes(node))
                    .forEach(node => legacyControls.appendChild(node));
                toolsRoot.appendChild(legacyControls);

                const toolbar = document.createElement('div');
                toolbar.className = 'map-editor-toolbar';
                toolbar.setAttribute('role', 'toolbar');
                toolbar.setAttribute('aria-label', 'Инструмент подлокации');
                toolbar.innerHTML = `
                    <button type="button" data-location-tool="select">Выбор</button>
                    <button type="button" data-location-tool="terrain">Ландшафт</button>
                    <button type="button" data-location-tool="height">Высота</button>
                    <button type="button" data-location-tool="radiation">Радиация</button>
                    <button type="button" data-location-tool="erase">Ластик</button>
                    <button type="button" data-location-tool="assets">Ассеты</button>
                    <button type="button" data-location-tool="building">Строительство</button>
                    <button type="button" data-location-tool="light">Свет</button>
                `;
                const hint = document.createElement('p');
                toolbar.appendChild(historyControls);
                hint.className = 'map-editor-hint';
                toolsRoot.prepend(common, toolbar);
                syncMapEditorAutoExpandCheckbox();
                toolbar.after(hint);

                const toolSettings = document.createElement('div');
                toolSettings.className = 'location-tool-settings';
                hint.after(toolSettings);
                for (const section of [sections.brush, sections.terrain, sections.height, sections.radiation]) {
                    section.setAttribute('aria-label', section.querySelector('strong').textContent);
                    toolSettings.appendChild(section);
                }
                sections.select.classList.add('location-selection-section');
                for (const id of ['loc-edit-radius', 'loc-edit-height', 'loc-edit-radiation']) {
                    const row = document.getElementById(id).parentElement;
                    row.classList.add('location-brush-control');
                    row.style.cssText = '';
                }
                const exactHeightRow = document.getElementById('loc-edit-height').parentElement;
                const sculptModeRow = document.createElement('label');
                sculptModeRow.innerHTML = `Режим <select class="form-control" id="loc-height-mode">
                    <option value="exact">Точная высота</option>
                    <option value="raise">Скульптор: поднять</option>
                    <option value="lower">Скульптор: опустить</option>
                    <option value="smooth">Скульптор: сгладить</option>
                </select>`;
                exactHeightRow.before(sculptModeRow);
                const sculptControls = document.createElement('div');
                sculptControls.className = 'location-sculpt-controls';
                sculptControls.innerHTML = `<label><span data-sculpt-strength-label>Сила, м/с</span>
                    <input class="form-control" id="loc-sculpt-strength" type="number" min="0.05" max="3" step="any" aria-label="Сила скульптора"></label>
                    <label>Мягкость края (0,05–1)
                    <input class="form-control" id="loc-sculpt-softness" type="number" min="0.05" max="1" step="any" aria-label="Мягкость скульптора"></label>
                    <span>Удерживайте ЛКМ и ведите кисть. Esc — отмена мазка. Используется общий радиус кисти; вода сохраняет свой уровень.</span>`;
                sections.height.appendChild(sculptControls);
                const waterLevelControls = document.createElement('div');
                waterLevelControls.className = 'location-water-level-controls';
                waterLevelControls.innerHTML = `<label><input type="checkbox" id="loc-water-level-enabled"> Выровнять связанный водоём щелчком</label>
                    <label>Уровень воды <select class="form-control" id="loc-water-level-mode">
                        <option value="minimum">По текущему минимуму водоёма</option>
                        <option value="exact">По высоте, указанной выше</option>
                    </select></label>
                    <output id="loc-water-level-preview">Наведите на водоём, чтобы увидеть итоговую высоту.</output>`;
                sections.height.appendChild(waterLevelControls);
                for (const id of ['loc-water-level-enabled','loc-water-level-mode'])
                    document.getElementById(id).onchange = () => {
                        const tile=getHoveredTileCoords();
                        if(tile) updateHighlightByCoords(tile.x,tile.z);
                        if(id==='loc-water-level-mode') document.getElementById(id).blur();
                    };
                const heightMode = sculptModeRow.querySelector('select');
                const syncSculpt = () => {
                    const settings = getLocationSculptSettings();
                    heightMode.value = settings.mode;
                    exactHeightRow.hidden = settings.mode !== 'exact';
                    sculptControls.hidden = settings.mode === 'exact';
                    sculptControls.querySelector('[data-sculpt-strength-label]').textContent = settings.mode === 'smooth' ? 'Сила сглаживания, 1/с' : 'Сила, м/с';
                    syncNumericInput(document.getElementById('loc-sculpt-strength'), settings.strength);
                    syncNumericInput(document.getElementById('loc-sculpt-softness'), settings.softness);
                };
                heightMode.dataset.canvasEditorControl = '';
                heightMode.onchange = () => { setLocationSculptSettings({mode:heightMode.value}); syncSculpt(); heightMode.blur(); };
                for(const [id,key] of [['loc-sculpt-strength','strength'],['loc-sculpt-softness','softness']]) {
                    const input = document.getElementById(id);
                    input.dataset.canvasEditorControl = '';
                    bindNumericInput(input, { blurOnCommit:true, onCommit:value=>setLocationSculptSettings({[key]:value}) });
                }
                syncSculpt();

                const eraseSettings = document.createElement('section');
                eraseSettings.className = 'map-editor-section';
                eraseSettings.innerHTML = '<strong class="map-editor-section-heading">Ластик</strong><label><input type="checkbox" id="loc-erase-structures"> Также удалять постройки</label>';
                eraseSettings.hidden = true;
                toolSettings.appendChild(eraseSettings);

                const waterSettings=waterProfileControls('loc');waterSettings.hidden=true;sections.terrain.appendChild(waterSettings);
                let activeAsset = null;
                let assetSection = null;
                let environmentSection = null;
                const selectLocationTool = tool => {
                    window.cancelCommonMapTool?.();
                    if (!['select', 'terrain', 'height', 'radiation', 'erase', 'assets', 'building', 'light'].includes(tool)) return;
                    cancelLocationPaste();
                    cancelLocationAssetResize(false);
                    const previousTool = window.locationEditorTool;
                    if (['assets','building'].includes(previousTool) && previousTool !== tool) {
                        activeAsset = null;
                        clearLocationAssetPreset();
                        setLocationHouseDrawMode(false);
                        document.querySelector('[data-house-draw]')?.setAttribute('aria-pressed', 'false');
                        renderAssetCards();
                    }
                    window.locationEditorTool = tool;
                    expandMapToolsPanelOnToolChange(previousTool, tool);
                    toolbar.querySelectorAll('button').forEach(button => {
                        button.setAttribute('aria-pressed', String(button.dataset.locationTool === tool));
                    });
                    Object.entries(sections).forEach(([name, section]) => {
                        section.hidden = name !== tool
                            && !(name === 'brush' && ['terrain', 'height', 'radiation', 'erase'].includes(tool));
                    });
                    toolSettings.hidden = !['terrain', 'height', 'radiation', 'erase'].includes(tool);
                    waterSettings.hidden = document.getElementById('loc-edit-terrain')?.value !== 'water';
                    eraseSettings.hidden = tool !== 'erase';
                    if (assetSection) {
                        assetSection.hidden = !['assets','building'].includes(tool);
                        assetSection.querySelectorAll('.location-house-generator').forEach(node=>node.hidden=tool!=='building');
                        assetSection.querySelector('.map-editor-section-heading').textContent=tool==='building'?'Строительство':'Библиотека ассетов';
                        renderAssetCards();
                    }
                    if (environmentSection) environmentSection.hidden = tool !== 'light';
                    setLocationDarknessOverlayVisible(tool === 'light');
                    if (!['assets','building'].includes(tool)) setLocationBuildMode('terrain');
                    else if (activeAsset) setLocationAssetPreset(activeAsset);
                    setLocationEraserMode(tool === 'erase');
                    if (!['assets','building'].includes(tool)) setLocationBrushObjectMode(false);
                    const radCheck = document.getElementById('loc-rad-mode');
                    if (radCheck) radCheck.checked = tool === 'radiation';
                    hint.textContent = {
                        select: 'Выберите объект или персонажа без рисования.',
                        assets: 'Выберите ассет в библиотеке, наведите на карту и кликните для размещения. ПКМ отменяет выбор ассета.',
                        building: 'Выберите строительный элемент или нарисуйте контур здания. Здесь же доступны этажи, планировка, мебель и аномалии.',
                        terrain: 'Кликните по клетке или проведите кистью, чтобы сменить покрытие.',
                        height: 'Кликните по клетке или проведите кистью, чтобы изменить высоту.',
                        radiation: 'Кликните по клетке или проведите кистью, чтобы задать радиацию.',
                        light: 'Настройте освещение или закрасьте клетки без дневного света.',
                        erase: 'Удаляет декор. Поставьте галочку, чтобы удалять и постройки на выбранной клетке.',
                    }[tool];
                    renderPlacementSettings();
                    if (tool !== 'select' && !getLocationEditMode()) setLocationEditMode(true);
                    refreshLocationFloorMenu();
                };
                toolbar.addEventListener('click', event => {
                    const button = event.target.closest('[data-location-tool]');
                    if (button) selectLocationTool(button.dataset.locationTool);
                });
                window.selectLocationEditorTool = selectLocationTool;

                assetSection = document.createElement('section');
                assetSection.className = 'map-editor-section location-asset-library';
                assetSection.innerHTML = `
                    <div class="location-asset-library-heading">
                        <strong class="map-editor-section-heading">Библиотека ассетов</strong>
                        <span>Выберите карточку, затем место на карте</span>
                    </div>
                    <input class="form-control location-asset-search" type="search" placeholder="Найти ассет или аномалию" aria-label="Поиск по ассетам">
                    <div class="location-house-generator">
                        <button type="button" data-house-draw aria-pressed="false">Нарисовать здание</button>
                        <span>Потяните ЛКМ по свободному участку. В открывшемся окне выберите назначение здания, затем нажмите «Предпросмотр» и «Создать».</span>
                    </div>
                    <div class="location-asset-tabs"></div>
                    <div class="location-asset-grid"></div>
                    <p class="location-asset-empty" role="status" hidden>Ничего не найдено. Измените запрос или выберите категорию.</p>
                    <div class="location-asset-placement" hidden>
                        <span data-placement-name></span>
                        <label><span data-placement-height-label>Высота от земли, м</span>
                            <input class="form-control" type="number" data-placement-height min="0" max="10" step="any">
                        </label>
                        <div class="location-asset-dimensions" data-placement-size>
                            <label>Длина X, м <input class="form-control" type="number" data-placement-dimension="width" min="0.005" max="30" step="any"></label>
                            <label>Ширина Z, м <input class="form-control" type="number" data-placement-dimension="depth" min="0.005" max="30" step="any"></label>
                            <label>Высота модели, м <input class="form-control" type="number" data-placement-dimension="height" min="0.005" max="20" step="any"></label>
                        </div>
                        <label data-placement-building-field style="display:none;">Здание <select class="form-control" data-placement-building></select></label>
                        <button type="button" data-placement-new-building style="display:none;">Новое здание</button>
                        <input type="hidden" data-placement-building-id value="${editorBuildingId}">
                        <label data-placement-level-field style="display:none;">Этаж размещения <input class="form-control" type="number" step="1" value="0" data-placement-level></label>
                        <label data-placement-roof-type-field style="display:none;">Крыша <select class="form-control" data-placement-roof-type><option value="gable">Двускатная</option><option value="flat">Плоская</option><option value="none">Без автоматической крыши</option></select></label>
                        <span data-placement-angle style="display:none;">Поворот: 0° · Q/E: 15°</span>
                        <span class="location-asset-placement-hint">Наведите на карту и кликните для размещения · ПКМ: отмена</span>
                    </div>
                    <div class="location-asset-inspector"></div>
                `;
                toolsRoot.appendChild(assetSection);
                const buildingTools = document.createElement('div');
                buildingTools.className = 'location-house-generator location-building-management';
                buildingTools.innerHTML = '<strong>Изменить существующее здание</strong><p>Выберите здание на карте инструментом «Выбор» или в списке ниже. Затем выберите этаж и нужное действие. Для нового здания используйте кнопку «Нарисовать здание» выше.</p>';
                for (const id of ['loc-structure-building', 'loc-structure-level']) {
                    buildingTools.appendChild(document.getElementById(id).closest('label'));
                }
                for (const id of ['loc-generate-interior', 'loc-generate-furniture', 'loc-restore-generated-floor']) {
                    buildingTools.appendChild(document.getElementById(id));
                }
                assetSection.querySelector('.location-house-generator').after(buildingTools);
                const anomalyControls=document.createElement('details');
                anomalyControls.className='location-house-generator location-building-anomaly-controls';
                anomalyControls.innerHTML='<summary>Аномалии во всём доме</summary><label>Плотность <input class="form-control" data-building-anomaly-density type="number" min="0.25" max="2" step="0.25" value="1"></label><button type="button" id="loc-generate-anomalies">Добавить аномалии</button>';
                buildingTools.appendChild(anomalyControls);
                const floorControls=document.createElement('div');
                floorControls.id='loc-building-floor-controls';
                floorControls.style.flexBasis='100%';
                buildingTools.appendChild(floorControls);
                assetSection.hidden = !['assets','building'].includes(window.locationEditorTool);
                assetSection.querySelectorAll('.location-house-generator').forEach(node=>node.hidden=window.locationEditorTool!=='building');
                const assetTabs = assetSection.querySelector('.location-asset-tabs');
                const assetGrid = assetSection.querySelector('.location-asset-grid');
                const assetInspector = assetSection.querySelector('.location-asset-inspector');
                sections.select.appendChild(assetInspector);
                const assetPlacement = assetSection.querySelector('.location-asset-placement');
                const placementHeight = assetPlacement.querySelector('[data-placement-height]');
                const assetSearch = assetSection.querySelector('.location-asset-search');
                const houseButton = assetSection.querySelector('[data-house-draw]');
                houseButton.onclick = () => {
                    const enabled = houseButton.getAttribute('aria-pressed') !== 'true';
                    if (enabled) {
                        activeAsset = null;
                        clearLocationAssetPreset();
                        renderAssetCards();
                        renderPlacementSettings();
                    }
                    selectLocationTool('building');
                    setLocationHouseDrawMode(enabled);
                    houseButton.setAttribute('aria-pressed', String(enabled));
                    if (enabled) hint.textContent = 'Потяните ЛКМ от первого угла до противоположного; затем настройте здание и проверьте предпросмотр.';
                };
                const anomalyAssets = (locationWorldRules.anomalies || []).map(anomaly => ({
                    id: `anomaly_${anomaly.key}`, group: 'Аномалии', kind: 'decor',
                    label: anomaly.name, icon: '✧', color: '#79d7dd',
                }));
                const allLocationAssets = [...LOCATION_ASSETS, ...anomalyAssets].filter(asset => !asset.hidden);
                const groups = [...new Set(allLocationAssets.map(asset => asset.group))];
                let activeGroup = 'Свет';
                const renderPlacementSettings = () => {
                    assetPlacement.hidden = !activeAsset || window.locationEditorTool !== 'assets';
                    if (!activeAsset) return;
                    assetPlacement.querySelector('[data-placement-name]').textContent = activeAsset.label;
                    syncNumericInput(placementHeight, activeAsset.kind === 'lamp'
                        ? activeAsset.height : activeAsset.elevation ?? 0);
                    placementHeight.max = activeAsset.kind === 'lamp'
                        ? String(10) : '10';
                    assetPlacement.querySelector('[data-placement-height-label]').textContent = activeAsset.kind === 'lamp'
                        ? 'Высота над полом этажа, м' : 'Высота от уровня этажа, м';
                    assetPlacement.querySelector('[data-placement-size]').hidden = activeAsset.id?.startsWith('anomaly_');
                    assetPlacement.querySelector('[data-placement-building-field]').style.display = activeAsset.kind === 'structure' ? '' : 'none';
                    assetPlacement.querySelector('[data-placement-new-building]').style.display = activeAsset.kind === 'structure' ? '' : 'none';
                    assetPlacement.querySelector('[data-placement-level-field]').style.display =
                        ['structure', 'lamp'].includes(activeAsset.kind) ? '' : 'none';
                    assetPlacement.querySelector('[data-placement-roof-type-field]').style.display = activeAsset.id === 'floor' ? '' : 'none';
                    assetPlacement.querySelector('[data-placement-angle]').style.display = activeAsset.kind === 'structure'
                        || activeAsset.kind === 'decor' || activeAsset.lightKind === 'spot' ? '' : 'none';
                    const dimensions = assetDimensions(activeAsset, activeAsset.kind, activeAsset.lightKind || activeAsset.id);
                    assetPlacement.querySelectorAll('[data-placement-dimension]').forEach(input => {
                        const axis = input.dataset.placementDimension;
                        [input.min, input.max] = assetDimensionLimits(activeAsset.kind)[axis];
                        syncNumericInput(input, dimensions[axis]);
                    });
                };
                assetPlacement.querySelectorAll('[data-placement-dimension]').forEach(input => {
                    bindNumericInput(input, { onCommit: () => {
                        if (!activeAsset) return false;
                        const dimensions = Object.fromEntries(Array.from(
                            assetPlacement.querySelectorAll('[data-placement-dimension]'),
                            field => [field.dataset.placementDimension, readNumericInput(field, { report: true })],
                        ));
                        if (!validAssetDimensions(dimensions, activeAsset.kind)) return false;
                        activeAsset.dimensions = [dimensions.width, dimensions.depth, dimensions.height];
                        return setLocationAssetDimensions(dimensions);
                    } });
                });
                bindNumericInput(placementHeight, { onCommit: value => {
                    if (!activeAsset) return false;
                    if (activeAsset.kind === 'lamp') activeAsset.height = value;
                    else activeAsset.elevation = value;
                    setLocationAssetElevation(value);
                } });
                const renderAssetCards = () => {
                    assetTabs.replaceChildren();
                    assetGrid.replaceChildren();
                    const libraryVisible = window.locationEditorTool === 'assets';
                    for (const node of [assetTabs, assetGrid, assetSearch, assetSection.querySelector('.location-asset-empty'),
                        assetSection.querySelector('.location-asset-library-heading > span')]) node.hidden = !libraryVisible;
                    if (!libraryVisible) return;
                    groups.forEach(group => {
                        const tab = document.createElement('button');
                        tab.type = 'button';
                        tab.textContent = group;
                        tab.setAttribute('aria-pressed', String(group === activeGroup));
                        tab.onclick = () => {
                            activeGroup = group;
                            assetSearch.value = '';
                            renderAssetCards();
                            assetGrid.scrollTop = 0;
                        };
                        assetTabs.appendChild(tab);
                    });
                    const search = assetSearch.value.trim().toLocaleLowerCase('ru');
                    allLocationAssets.filter(asset => search
                        ? asset.label.toLocaleLowerCase('ru').includes(search)
                        : asset.group === activeGroup).forEach(asset => {
                        const card = document.createElement('button');
                        card.type = 'button';
                        card.className = 'location-asset-card';
                        card.setAttribute('aria-pressed', String(activeAsset?.id === asset.id));
                        const symbol = document.createElement('span');
                        symbol.className = 'location-asset-symbol';
                        symbol.style.setProperty('--asset-color', asset.color);
                        symbol.textContent = asset.icon;
                        const label = document.createElement('span');
                        label.textContent = asset.label;
                        card.append(symbol, label);
                        card.onclick = () => {
                            setLocationHouseDrawMode(false);
                            houseButton.setAttribute('aria-pressed', 'false');
                            if (activeAsset?.id === asset.id) {
                                activeAsset = null;
                                clearLocationAssetPreset();
                                renderPlacementSettings();
                            } else {
                                activeAsset = { ...asset };
                                selectLocationTool('assets');
                            }
                            renderAssetCards();
                        };
                        assetGrid.appendChild(card);
                    });
                    assetSection.querySelector('.location-asset-empty').hidden = assetGrid.childElementCount > 0;
                };
                window.cancelLocationAssetPlacement = () => {
                    activeAsset = null;
                    clearLocationAssetPreset();
                    renderAssetCards();
                    renderPlacementSettings();
                };
                let activeBuildingId = editorBuildingId;
                let activeLevel = getLocationViewLevel();
                const buildingSelectors = [document.getElementById('loc-structure-building'),
                    assetPlacement.querySelector('[data-placement-building]')];
                const buildingFields = [document.getElementById('loc-structure-building-id'),
                    assetPlacement.querySelector('[data-placement-building-id]')];
                const levelFields = [document.getElementById('loc-structure-level'),
                    assetPlacement.querySelector('[data-placement-level]')];
                const renderBuildingControls = () => {
                    const ids = getLocationBuildingIds();
                    const levels=getLocationBuildingLevels(activeBuildingId);
                    const floorSelect=document.getElementById('loc-structure-level');
                    floorSelect.replaceChildren(...levels.map(level=>new Option(level<0?`Подвал ${-level}`:`${level+1}-й этаж`,String(level))));
                    floorSelect.disabled=!levels.length;
                    if(levels.length&&!levels.includes(activeLevel))activeLevel=levels.includes(0)?0:levels[0];
                    for (const select of buildingSelectors) {
                        select.replaceChildren(...ids.map((id, index) => new Option(`Здание ${index + 1}`, id)));
                        if (!activeBuildingId) select.add(new Option('Старая постройка (без ID)', ''));
                        else if (!ids.includes(activeBuildingId)) select.add(new Option('Выберите построенный дом', activeBuildingId));
                        select.value = activeBuildingId;
                    }
                    for (const input of buildingFields) input.value = activeBuildingId;
                    for (const input of levelFields) {
                        if (input.type === 'number') syncNumericInput(input, activeLevel);
                        else input.value = String(activeLevel);
                    }
                    document.getElementById('loc-restore-generated-floor').hidden =
                        !canRestoreGeneratedLocationFloor(activeBuildingId, activeLevel);
                    document.getElementById('loc-generate-interior').disabled = !ids.includes(activeBuildingId);
                    document.getElementById('loc-generate-furniture').disabled = !ids.includes(activeBuildingId);
                    document.getElementById('loc-generate-anomalies').disabled = !ids.includes(activeBuildingId);
                };
                const setActiveBuilding = (id, level = activeLevel) => {
                    activeBuildingId = id || '';
                    activeLevel = Number.isInteger(Number(level)) ? Number(level) : 0;
                    renderBuildingControls();
                    if (activeAsset?.kind === 'lamp') {
                        const maxHeight = 10;
                        if (activeAsset.height > maxHeight) {
                            activeAsset.height = maxHeight;
                            setLocationAssetElevation(maxHeight);
                        }
                    }
                    renderPlacementSettings();
                    refreshLocationBuildPreview();
                };
                for (const select of buildingSelectors) select.onchange = () => setActiveBuilding(select.value);
                for (const button of [assetPlacement.querySelector('[data-placement-new-building]')]) {
                    button.onclick = () => setActiveBuilding(
                        `building-${globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2)}`, 0);
                }
                for (const input of levelFields) {
                    const commit = value => {
                        setActiveBuilding(activeBuildingId, value);
                        setLocationViewLevel(value);
                    };
                    if (input.type === 'number') bindNumericInput(input, { onCommit: commit });
                    else input.onchange = () => commit(Number(input.value));
                }
                document.getElementById('loc-restore-generated-floor').onclick = async event => {
                    const button = event.currentTarget;
                    button.disabled = true;
                    try { await restoreGeneratedLocationFloor(activeBuildingId, activeLevel); }
                    finally { button.disabled = false; renderBuildingControls(); }
                };
                document.getElementById('loc-generate-interior').onclick = async event => {
                    const button=event.currentTarget;
                    button.disabled=true;
                    try { await generateLocationFloorInterior(activeBuildingId,activeLevel); }
                    finally {button.disabled=false;renderBuildingControls();}
                };
                document.getElementById('loc-generate-furniture').onclick = async event => {
                    const button=event.currentTarget;button.disabled=true;
                    try {await generateLocationFurniture(activeBuildingId,activeLevel);}
                    finally {button.disabled=false;renderBuildingControls();}
                };
                document.getElementById('loc-generate-anomalies').onclick=async event=>{
                    const density = readNumericInput(document.querySelector('[data-building-anomaly-density]'), { report: true });
                    if (!Number.isFinite(density)) return;
                    const button=event.currentTarget;button.disabled=true;
                    try{await generateLocationBuildingAnomalies(activeBuildingId,{seed:globalThis.crypto?.randomUUID?.()||String(Math.random()),density});}
                    finally{button.disabled=false;renderBuildingControls();}
                };
                renderBuildingControls();
                let lampInspectorSave = Promise.resolve();
                const pendingInspectorSaves = new Map();
                const renderAssetSelection = () => {
                    const selection = getSelectedLocationEditorAsset();
                    if (selection && pendingInspectorSaves.has(selection.selectionKey)) return;
                    // A socket echo must not discard a number still being typed.
                    const focused = document.activeElement;
                    if (assetInspector.contains(focused) && focused?.dataset.numericDraft === 'true') return;
                    if (selection?.kind === 'structure' && selection.selectedCount === 1) {
                        setActiveBuilding(selection.buildingId, selection.level);
                    }
                    const keepPreciseOpen = assetInspector.querySelector('.location-asset-precise')?.open || false;
                    const keepRoofOpen=assetInspector.querySelector('[data-roof-section-fields]')?.open||false;
                    const keepCoverOpen=assetInspector.querySelector('[data-selected-cover-fields]')?.open||false;
                    assetInspector.replaceChildren();
                    if (!selection) {
                        const hint = document.createElement('p');
                        hint.textContent = 'Для настройки уже размещённого объекта выберите инструмент «Выбор» и кликните по нему.';
                        assetInspector.appendChild(hint);
                        return;
                    }
                    assetInspector.innerHTML = `
                        <div class="location-asset-selection-heading"><strong data-asset-name></strong><span data-asset-position></span></div>
                        <div class="location-asset-height-control">
                            <label>Высота от земли, м
                                <input class="form-control" type="number" data-asset-elevation min="0" max="10" step="any">
                            </label>
                            <button type="button" data-height-step="-0.25" aria-label="Опустить на 25 см">−</button>
                            <button type="button" data-height-step="0.25" aria-label="Поднять на 25 см">+</button>
                        </div>
                        <div class="location-asset-actions">
                            <button type="button" data-asset-action="move" title="G">Переместить</button>
                            <button type="button" data-asset-action="rotate" title="T">Повернуть на карте</button>
                            <button type="button" data-asset-action="resize" title="R">Изменить размер</button>
                            <button type="button" data-asset-action="hide" title="H">Скрыть</button>
                            <button type="button" data-asset-action="fire" hidden>Зажечь</button>
                            <button type="button" data-asset-action="delete">Удалить</button>
                        </div>
                        <div class="location-asset-dimensions" data-selected-size>
                            <label>Длина X, м <input class="form-control" type="number" data-selected-dimension="width" min="0.005" max="30" step="any"></label>
                            <label>Ширина Z, м <input class="form-control" type="number" data-selected-dimension="depth" min="0.005" max="30" step="any"></label>
                            <label>Высота модели, м <input class="form-control" type="number" data-selected-dimension="height" min="0.005" max="20" step="any"></label>
                        </div>
                        <div data-selected-building-fields style="display:none;">
                            <span data-selected-building-label></span>
                            <button type="button" data-add-floor hidden>Добавить этаж</button>
                            <button type="button" data-fit-floor hidden>Растянуть по форме здания</button>
                            <button type="button" data-generate-roof hidden>Создать / заменить крышу здания</button>
                            <span data-fit-floor-reason hidden></span>
                            <details><summary>Принадлежность вручную</summary>
                                <label>ID здания <input class="form-control" type="text" maxlength="64" data-selected-building-id></label>
                                <label>Этаж <input class="form-control" type="number" step="1" data-selected-level></label>
                            </details>
                        </div>
                        <label data-selected-roof-type-field style="display:none;">Крыша <select class="form-control" data-selected-roof-type><option value="gable">Двускатная</option><option value="flat">Плоская</option><option value="none">Без автоматической крыши</option></select></label>
                        <details data-roof-section-fields hidden><summary>Параметры секции крыши</summary>
                            <label>Область<select class="form-control" data-roof-scope><option value="body">Корпус и его сопряжения</option><option value="section">Только эта секция</option></select></label>
                            <label>Тип<select class="form-control" data-roof-setting="type"><option value="flat">Плоская</option><option value="shed">Односкатная</option><option value="gable">Двускатная</option></select></label>
                            <label>Направление<select class="form-control" data-roof-setting="direction"><option value="north">Север / С–Ю</option><option value="south">Юг / С–Ю</option><option value="east">Восток / В–З</option><option value="west">Запад / В–З</option></select></label>
                            <label>Уклон, °<input class="form-control" data-roof-setting="pitch" type="number" min="0" max="60" step="1"></label>
                            <label>Высота стен корпуса, м<input class="form-control" data-roof-setting="eave" type="number" min="2.4" max="8" step="0.1"></label>
                            <small>Удалённые секции не восстанавливаются. Размеры и перемещение отдельных секций меняются обычными инструментами.</small>
                            <button type="button" data-roof-apply>Применить к крыше</button>
                        </details>
                        <div data-roof-wall-fields hidden><small>Высота этой стороны изменяет кровлю корпуса. Для независимой ручной правки можно отвязать стену.</small><button type="button" data-roof-wall-detach>Отвязать стену от крыши</button></div>
                        <details class="location-cover-settings" data-selected-cover-fields hidden>
                            <summary>Настройка укрытия</summary>
                            <div class="location-cover-fields">
                                <label>Класс укрытия <select class="form-control" data-cover-field="coverClass">
                                    <option value="conditional">Условное</option><option value="flimsy">Хлипкое</option>
                                    <option value="medium">Средней прочности</option><option value="strong">Прочное</option>
                                    <option value="very_strong">Очень прочное</option><option value="titanium">Титановое</option>
                                    <option value="special">Особое</option>
                                </select></label>
                                <label>Текущие ОЗ <input class="form-control" type="number" min="0" max="1000000" step="1" data-cover-field="hp"></label>
                                <label>Максимальные ОЗ <input class="form-control" type="number" min="1" max="1000000" step="1" data-cover-field="maxHp"></label>
                                <label>Защита, % <input class="form-control" type="number" min="0" max="100" step="1" data-cover-field="protection"></label>
                            </div>
                            <button type="button" class="btn btn-secondary" data-cover-save>Сохранить укрытие</button>
                        </details>
                        <div class="location-asset-resize-tools" data-resize-tools hidden>
                            <strong data-resize-status></strong>
                            <span>Ось размера:</span>
                            <button type="button" data-resize-axis="all" title="Все оси">Все</button>
                            <button type="button" data-resize-axis="width" title="X">X</button>
                            <button type="button" data-resize-axis="depth" title="Z">Z</button>
                            <button type="button" data-resize-axis="height" title="Y">Y</button>
                            <span data-resize-hint>Volume Scale: тяните маркер грани. Shift: симметрично; Ctrl: шаг 0,1 м (пол: 1 м); Esc: отменить перетаскивание или выйти; отпустите ЛКМ: сохранить и выбрать следующую грань.</span>
                        </div>
                        <details class="location-asset-precise">
                            <summary>Точная настройка</summary>
                            <div class="location-asset-fields">
                                <label>X, м <input class="form-control" type="number" data-asset-field="x" step="any"></label>
                                <label>Z, м <input class="form-control" type="number" data-asset-field="z" step="any"></label>
                                <label data-angle-label>Угол, ° <input class="form-control" type="number" data-asset-field="rotation" step="any"></label>
                                <label data-lamp-field>Яркость <input class="form-control" type="number" data-asset-field="intensity" min="0" max="10" step="any"></label>
                                <label data-lamp-field>Радиус, м <input class="form-control" type="number" data-asset-field="distance" min="1" max="30" step="any"></label>
                                <label>Цвет <input class="form-control" type="color" data-asset-field="color"></label>
                            </div>
                            <button type="button" class="btn btn-secondary" data-asset-save>Сохранить параметры</button>
                        </details>
                    `;
                    assetInspector.querySelector('[data-asset-name]').textContent = selection.label;
                    if (selection.traversalCategory!==null&&selection.selectedCount===1) {
                        const label=document.createElement('label');label.textContent='Проходимость ';
                        const select=document.createElement('select');select.className='form-control';select.dataset.traversalCategory='';
                        for(const [value,text] of [['auto','По высоте'],['passable','Свободный проход'],['low','Низкое препятствие'],['high','Высокое препятствие'],['blocked','Непроходимо']]) {
                            const option=document.createElement('option');option.value=value;option.textContent=text;select.appendChild(option);
                        }
                        select.value=selection.traversalCategory||'auto';
                        select.onchange=async()=>{
                            select.disabled=true;
                            try {await setSelectedLocationTraversalCategory(select.value);}
                            finally {select.disabled=false;renderAssetSelection();}
                        };
                        label.appendChild(select);assetInspector.appendChild(label);
                    }
                    if (selection.selectedCount > 1) {
                        assetInspector.querySelector('[data-asset-name]').textContent = `Выбрано объектов: ${selection.selectedCount}`;
                        assetInspector.querySelector('.location-asset-height-control').hidden = true;
                        assetInspector.querySelector('[data-asset-action="resize"]').hidden = true;
                        assetInspector.querySelector('[data-selected-size]').hidden = true;
                        assetInspector.querySelector('.location-asset-precise').hidden = true;
                    }
                    assetInspector.querySelector('[data-asset-action="hide"]').textContent = selection.hidden && selection.selectedCount === 1 ? 'Показать' : 'Скрыть / показать';
                    const fireButton = assetInspector.querySelector('[data-asset-action="fire"]');
                    fireButton.hidden = !selection.fire || selection.selectedCount !== 1;
                    fireButton.textContent = selection.electric
                        ? (selection.isLit ? 'Выключить' : 'Включить')
                        : (selection.isLit ? 'Потушить' : 'Зажечь');
                    if (keepPreciseOpen) assetInspector.querySelector('.location-asset-precise').open = true;
                    const coverFields = assetInspector.querySelector('[data-selected-cover-fields]');
                    coverFields.hidden = !selection.cover || selection.selectedCount !== 1;
                    if (selection.cover && selection.selectedCount === 1) {
                        coverFields.open = keepCoverOpen;
                        for (const [key,value] of Object.entries(selection.cover)) {
                            const input = coverFields.querySelector(`[data-cover-field="${key}"]`);
                            if (input) input.value = value;
                        }
                        coverFields.querySelector('[data-cover-save]').onclick = async event => {
                            const read = key => {
                                const value = coverFields.querySelector(`[data-cover-field="${key}"]`).value;
                                return value === '' ? NaN : Number(value);
                            };
                            const button = event.currentTarget;
                            button.disabled = true;
                            try {
                                if (await setSelectedLocationCover({
                                    coverClass: coverFields.querySelector('[data-cover-field="coverClass"]').value,
                                    hp: read('hp'), maxHp: read('maxHp'), protection: read('protection'),
                                })) showNotification('Укрытие обновлено', 'success');
                            } finally { if (button.isConnected) button.disabled = false; }
                        };
                    }
                    assetInspector.querySelector('[data-asset-position]').textContent = `Клетка ${Math.floor(selection.x)}, ${Math.floor(selection.z)}`;
                    const heightInput = assetInspector.querySelector('[data-asset-elevation]');
                    heightInput.min = selection.kind === 'lamp' ? String((selection.level || 0)*STOREY_HEIGHT) : '0';
                    heightInput.max = selection.kind === 'lamp' ? String((selection.level || 0)*STOREY_HEIGHT+10) : '10';
                    if(selection.roofSection)heightInput.max='40';
                    heightInput.value = selection.elevation ?? 0;
                    assetInspector.querySelectorAll('[data-lamp-field]').forEach(field => {
                        field.hidden = selection.kind !== 'lamp';
                    });
                    assetInspector.querySelector('[data-angle-label]').hidden = selection.kind === 'lamp' && selection.lightKind !== 'spot';
                    assetInspector.querySelector('[data-asset-action="resize"]').hidden = selection.selectedCount > 1 || !selection.resizable;
                    assetInspector.querySelector('[data-selected-size]').hidden = selection.selectedCount > 1 || !selection.resizable;
                    if (selection.resizable) assetInspector.querySelectorAll('[data-selected-dimension]').forEach(input => {
                        const axis = input.dataset.selectedDimension;
                        [input.min, input.max] = assetDimensionLimits(selection.kind)[axis];
                        input.value = selection.dimensions[axis];
                    });
                    if(selection.roofWall)assetInspector.querySelector('[data-selected-dimension="height"]').parentElement.firstChild.textContent='Основная высота стены, м ';
                    const buildingIdInput = assetInspector.querySelector('[data-selected-building-id]');
                    const levelInput = assetInspector.querySelector('[data-selected-level]');
                    assetInspector.querySelector('[data-selected-building-fields]').style.display = selection.kind === 'structure' && selection.selectedCount === 1 ? '' : 'none';
                    if (selection.kind === 'structure') {
                        buildingIdInput.value = selection.buildingId || '';
                        levelInput.value = String(selection.level);
                        const buildingNumber = getLocationBuildingIds().indexOf(selection.buildingId) + 1;
                        assetInspector.querySelector('[data-selected-building-label]').textContent = buildingNumber
                            ? `Здание ${buildingNumber} · этаж ${selection.level}` : 'Старая постройка · без ID';
                    }
                    const addFloorButton = assetInspector.querySelector('[data-add-floor]');
                    const roofGenerateButton=assetInspector.querySelector('[data-generate-roof]');
                    roofGenerateButton.hidden=selection.selectedCount!==1||!selection.buildingId;
                    roofGenerateButton.onclick=()=>generateSelectedLocationRoof();
                    const roofFields=assetInspector.querySelector('[data-roof-section-fields]');
                    roofFields.open=keepRoofOpen;
                    const roofWallFields=assetInspector.querySelector('[data-roof-wall-fields]');
                    roofWallFields.hidden=!selection.roofWall||selection.selectedCount!==1;
                    roofWallFields.querySelector('[data-roof-wall-detach]').onclick=()=>updateSelectedLocationAssetDetails({...selection,detachRoof:true});
                    roofFields.hidden=!(selection.roofSection||selection.structureType==='roof')||selection.selectedCount!==1;
                    if(selection.roofSection||selection.structureType==='roof') {
                        roofFields.querySelector('[data-roof-apply]').disabled=selection.roofEditing;
                        const roofSettings=selection.roofSection||{type:'flat',direction:'south',pitch:25,eave:2.4};
                        for(const input of roofFields.querySelectorAll('[data-roof-setting]'))input.value=roofSettings[input.dataset.roofSetting];
                        roofFields.querySelector('[data-roof-apply]').onclick=async()=>{
                            const settings={scope:roofFields.querySelector('[data-roof-scope]').value};
                            for (const input of roofFields.querySelectorAll('[data-roof-setting]')) {
                                const value = input.type === 'number' ? readNumericInput(input, { report: true }) : input.value;
                                if (input.type === 'number' && !Number.isFinite(value)) return;
                                settings[input.dataset.roofSetting] = value;
                            }
                            await updateSelectedLocationRoof(settings);
                        };
                    }
                    addFloorButton.hidden = selection.selectedCount !== 1 || !selection.buildingId;
                    addFloorButton.onclick = () => addFloorAboveSelectedLocationAsset();
                    const fitFloorButton = assetInspector.querySelector('[data-fit-floor]');
                    const fitFloorReason = assetInspector.querySelector('[data-fit-floor-reason]');
                    fitFloorButton.hidden = selection.selectedCount !== 1 || selection.structureType !== 'floor';
                    if (!fitFloorButton.hidden) {
                        const fit = getSelectedLocationFloorFit();
                        fitFloorButton.disabled = Boolean(fit.error);
                        fitFloorButton.title = fit.error || 'Используется замкнутый контур стен этого этажа';
                        fitFloorReason.hidden = !fit.error;
                        fitFloorReason.textContent = fit.error || '';
                        fitFloorButton.onclick = async () => {
                            fitFloorButton.disabled = true;
                            await fitSelectedLocationFloorToBuilding();
                            if (fitFloorButton.isConnected) renderAssetSelection();
                        };
                    }
                    const roofTypeInput = assetInspector.querySelector('[data-selected-roof-type]');
                    roofTypeInput.parentElement.style.display = selection.selectedCount === 1 && selection.roofType !== undefined ? '' : 'none';
                    if (selection.roofType !== undefined) roofTypeInput.value = selection.roofType;
                    for (const input of assetInspector.querySelectorAll('[data-asset-field]')) {
                        input.value = selection[input.dataset.assetField] ?? '';
                        if (['x', 'z'].includes(input.dataset.assetField)) {
                            input.min = '0';
                            input.max = input.dataset.assetField === 'x'
                                ? currentLocationData.grid_width : currentLocationData.grid_height;
                            input.dataset.exclusiveMax = String(selection.kind !== 'lamp');
                        }
                    }
                    const readFields = (report = true) => {
                        const fields = { rotation: selection.rotation };
                        for (const input of assetInspector.querySelectorAll('[data-asset-field]')) {
                            if (input.type === 'number' && input.closest('[hidden]')) continue;
                            fields[input.dataset.assetField] = input.type === 'color' ? input.value
                                : readNumericInput(input, { report, precision: selection.kind === 'lamp' ? 3 : undefined });
                            if (input.type === 'number' && !Number.isFinite(fields[input.dataset.assetField])) return null;
                        }
                        const elevation = readNumericInput(heightInput, { report, precision: selection.kind === 'lamp' ? 3 : undefined });
                        if (!Number.isFinite(elevation)) return null;
                        fields[selection.kind === 'lamp' ? 'height' : 'elevation'] = elevation;
                        if (selection.kind === 'structure') {
                            if (buildingIdInput.value.trim() !== (selection.buildingId || '')) fields.buildingId = buildingIdInput.value.trim();
                            const level = readNumericInput(levelInput, { report });
                            if (!Number.isFinite(level)) return null;
                            if (level !== selection.level) fields.level = level;
                        }
                        if (selection.roofType !== undefined) fields.roofType = roofTypeInput.value;
                        if (selection.resizable && selection.selectedCount === 1) {
                            fields.dimensions = Object.fromEntries(Array.from(
                                assetInspector.querySelectorAll('[data-selected-dimension]'),
                                input => [input.dataset.selectedDimension, readNumericInput(input,
                                    { report, precision: selection.kind === 'lamp' ? 3 : undefined })],
                            ));
                            if (!validAssetDimensions(fields.dimensions, selection.kind)) return null;
                        }
                        return fields;
                    };
                    buildingIdInput.onchange = async () => {
                        const fields = readFields();
                        if (fields) await updateSelectedLocationAssetDetails(fields);
                    };
                    roofTypeInput.onchange = async () => {
                        const fields = readFields();
                        if (fields) await updateSelectedLocationAssetDetails(fields);
                    };
                    if (selection.kind === 'lamp') {
                        assetInspector.querySelector('.location-asset-fields').addEventListener('input', () => {
                            const fields = readFields(false);
                            if (fields) previewSelectedLocationLamp(fields);
                        });
                        heightInput.addEventListener('input', () => {
                            const fields = readFields(false);
                            if (fields) previewSelectedLocationLamp(fields);
                        });
                        assetInspector.querySelector('.location-asset-precise').addEventListener('toggle', event => {
                            if (!event.target.open) applyLocationLighting();
                        });
                    }
                    assetInspector.querySelector('[data-asset-action="move"]').onclick = () => beginMoveSelectedLocationAsset();
                    assetInspector.querySelector('[data-asset-action="rotate"]').onclick = () => beginRotateSelectedLocationAsset();
                    assetInspector.querySelector('[data-asset-action="resize"]').onclick = () => beginResizeSelectedLocationAsset();
                    assetInspector.querySelector('[data-asset-action="hide"]').onclick = () => toggleSelectedLocationAssetsHidden();
                    fireButton.onclick = () => toggleSelectedLocationFire();
                    assetInspector.querySelector('[data-asset-action="delete"]').onclick = () => deleteSelectedLocationAsset();
                    assetInspector.querySelectorAll('[data-resize-axis]').forEach(button => {
                        button.onclick = () => setLocationAssetResizeAxis(button.dataset.resizeAxis);
                    });
                    const saveDetails = fields => {
                        const run = async () => {
                            const key = selection.selectionKey;
                            if (getSelectedLocationEditorAsset()?.selectionKey !== key) return false;
                            pendingInspectorSaves.set(key, (pendingInspectorSaves.get(key) || 0) + 1);
                            try { return await updateSelectedLocationAssetDetails(fields); }
                            finally {
                                const pending = pendingInspectorSaves.get(key) - 1;
                                if (pending) pendingInspectorSaves.set(key, pending);
                                else pendingInspectorSaves.delete(key);
                                renderAssetSelection();
                            }
                        };
                        if (selection.kind !== 'lamp') return run();
                        // Lamp saves must also wait for the preceding REST/socket echo to settle.
                        lampInspectorSave = lampInspectorSave.then(run, run);
                        return lampInspectorSave;
                    };
                    const saveFields = async () => {
                        const fields = readFields();
                        return fields ? saveDetails(fields) : false;
                    };
                    bindNumericInput(levelInput, { onCommit: saveFields });
                    assetInspector.querySelectorAll('[data-asset-field][type="number"]').forEach(input => {
                        bindNumericInput(input, { precision: selection.kind === 'lamp' ? 3 : undefined,
                            onCommit: saveFields });
                    });
                    bindNumericInput(heightInput, { precision: selection.kind === 'lamp' ? 3 : undefined,
                        onCommit: saveFields });
                    assetInspector.querySelectorAll('[data-selected-dimension]').forEach(input => {
                        bindNumericInput(input, { precision: selection.kind === 'lamp' ? 3 : undefined,
                            onCommit: async () => {
                                const fields = readFields();
                                if (!fields) return false;
                                cancelLocationAssetResize();
                                return saveDetails(fields);
                            } });
                    });
                    assetInspector.querySelector('[data-asset-action="rotate"]').disabled = selection.selectedCount === 1
                        && selection.kind === 'lamp' && selection.lightKind !== 'spot';
                    assetInspector.querySelectorAll('[data-height-step]').forEach(button => {
                        button.onclick = async () => {
                            const value = readNumericInput(heightInput, { report: true });
                            if (!Number.isFinite(value)) return;
                            heightInput.value = Math.max(Number(heightInput.min), Math.min(Number(heightInput.max),
                                value + Number(button.dataset.heightStep)));
                            await saveFields();
                        };
                    });
                    assetInspector.querySelector('[data-asset-save]').onclick = async () => {
                        if (await saveFields()) showNotification('Параметры обновлены', 'success');
                    };
                    const resizeState = getLocationAssetResizeState();
                    const resizeTools = assetInspector.querySelector('[data-resize-tools]');
                    resizeTools.hidden = !resizeState.active;
                    resizeTools.querySelector('[data-resize-status]').textContent = resizeState.active
                        ? `X: ${resizeState.dimensions.width.toFixed(2)} м  Y: ${resizeState.dimensions.height.toFixed(2)} м  Z: ${resizeState.dimensions.depth.toFixed(2)} м${resizeState.snapped ? '  ·  ПРИВЯЗКА Ctrl' : ''}`
                        : '';
                    if (selection.kind !== 'structure') {
                        resizeTools.querySelector('[data-resize-hint]').textContent =
                            'Двигайте курсор для изменения размера, ЛКМ — сохранить, Esc — отменить.';
                    }
                    resizeTools.querySelectorAll('[data-resize-axis]').forEach(button => {
                        button.setAttribute('aria-pressed', String(button.dataset.resizeAxis === resizeState.axis));
                    });
                    if (resizeState.active && resizeState.dimensions) {
                        assetInspector.querySelectorAll('[data-selected-dimension]').forEach(input => {
                            input.value = resizeState.dimensions[input.dataset.selectedDimension];
                        });
                    }
                };
                if (window.locationAssetSelectionHandler) {
                    window.removeEventListener('location-editor-selection', window.locationAssetSelectionHandler);
                }
                window.locationAssetSelectionHandler = renderAssetSelection;
                window.addEventListener('location-editor-selection', renderAssetSelection);
                if (window.locationBuildingListHandler) {
                    window.removeEventListener('location-editor-buildings-changed', window.locationBuildingListHandler);
                    window.removeEventListener('location-editor-active-building', window.locationActiveBuildingHandler);
                }
                window.locationBuildingListHandler = renderBuildingControls;
                window.locationActiveBuildingHandler = event => setActiveBuilding(event.detail.buildingId, event.detail.level);
                window.addEventListener('location-editor-buildings-changed', window.locationBuildingListHandler);
                window.addEventListener('location-editor-active-building', window.locationActiveBuildingHandler);
                if (window.locationAssetResizeHandler) {
                    window.removeEventListener('location-editor-resize', window.locationAssetResizeHandler);
                }
                window.locationAssetResizeHandler = event => {
                    const tools = assetInspector.querySelector('[data-resize-tools]');
                    if (!tools) return;
                    tools.hidden = !event.detail.active;
                    tools.querySelector('[data-resize-status]').textContent = event.detail.active
                        ? `X: ${event.detail.dimensions.width.toFixed(2)} м  Y: ${event.detail.dimensions.height.toFixed(2)} м  Z: ${event.detail.dimensions.depth.toFixed(2)} м${event.detail.snapped ? '  ·  ПРИВЯЗКА Ctrl' : ''}`
                        : '';
                    tools.querySelectorAll('[data-resize-axis]').forEach(button => {
                        button.setAttribute('aria-pressed', String(button.dataset.resizeAxis === event.detail.axis));
                    });
                    if (event.detail.active && event.detail.dimensions) {
                        assetInspector.querySelectorAll('[data-selected-dimension]').forEach(input => {
                            if (document.activeElement !== input) input.value = event.detail.dimensions[input.dataset.selectedDimension];
                        });
                    } else if (getSelectedLocationEditorAsset()) {
                        const dimensions = getSelectedLocationEditorAsset().dimensions;
                        assetInspector.querySelectorAll('[data-selected-dimension]').forEach(input => {
                            if (document.activeElement !== input) input.value = dimensions[input.dataset.selectedDimension];
                        });
                    }
                };
                window.addEventListener('location-editor-resize', window.locationAssetResizeHandler);
                assetSearch.addEventListener('input', renderAssetCards);
                renderAssetCards();
                renderAssetSelection();

                let darkTilesDraft = (currentLocationData.lighting?.dark_tiles || []).map(tile => ({ ...tile }));
                let darknessBrushMode = null;
                environmentSection = document.createElement('section');
                environmentSection.className = 'map-editor-section location-environment-section';
                environmentSection.innerHTML = `
                    <strong class="map-editor-section-heading">Свет и атмосфера</strong>
                    <div class="location-environment-body">
                        <div class="location-environment-editor"></div>
                        <div class="location-darkness-controls">
                            <strong>Тёмные области</strong>
                            <button type="button" data-darkness-brush="paint" aria-pressed="false">Затемнять клетки</button>
                            <button type="button" data-darkness-brush="erase" aria-pressed="false">Стереть затемнение</button>
                            <label class="location-darkness-radius"><span>Размер кисти</span>
                                <input type="range" id="loc-darkness-radius" data-darkness-radius min="0" max="5" value="0">
                                <input class="form-control" type="number" id="loc-darkness-radius-number" data-world-number="loc-darkness-radius" min="0" max="5" step="1" aria-label="Радиус кисти затемнения" style="width:84px;">
                                <output data-darkness-radius-value>1 × 1</output>
                            </label>
                            <span>Выберите кисть. Мазок сохраняется при отпускании ЛКМ.</span>
                            <span data-darkness-count></span>
                        </div>
                        <div class="location-environment-actions">
                            <button type="button" class="btn btn-primary" data-save-environment>Сохранить свет</button>
                            <button type="button" class="btn btn-secondary" data-revert-environment>Отменить предпросмотр</button>
                            <span data-environment-status>Изменения видны на карте до сохранения</span>
                        </div>
                    </div>
                `;
                toolsRoot.appendChild(environmentSection);
                environmentSection.hidden = window.locationEditorTool !== 'light';
                const environmentStatus = environmentSection.querySelector('[data-environment-status]');
                let environmentEditor;
                let environmentBase = structuredClone(currentLocationData.lighting);
                const mountEnvironmentEditor = () => {
                    environmentBase = structuredClone(currentLocationData.lighting);
                    environmentEditor = createLocationLightingEditor(
                        environmentSection.querySelector('.location-environment-editor'),
                        currentLocationData.lighting,
                        settings => {
                            applyLocationLighting(settings, false);
                            environmentStatus.textContent = 'Предпросмотр: изменения ещё не сохранены';
                        },
                        () => currentLocationData.lighting?.local_lights || [],
                        () => darkTilesDraft,
                        getLocationLightingPreset,
                        setLocationLightingPreset,
                    );
                };
                mountEnvironmentEditor();
                const updateDarknessCount = () => {
                    environmentSection.querySelector('[data-darkness-count]').textContent = `Клеток: ${darkTilesDraft.length}`;
                };
                updateDarknessCount();
                const darknessRadiusInput = environmentSection.querySelector('[data-darkness-radius]');
                darknessRadiusInput.value = document.getElementById('loc-edit-radius')?.value || '0';
                environmentSection.querySelector('[data-darkness-radius-value]').textContent =
                    `${Number(darknessRadiusInput.value) * 2 + 1} × ${Number(darknessRadiusInput.value) * 2 + 1}`;
                darknessRadiusInput.oninput = event => {
                    setLocationBrushRadius(getWorldNumericValue('loc-darkness-radius'));
                };
                environmentSection.querySelectorAll('[data-darkness-brush]').forEach(button => {
                    button.onclick = () => {
                        darknessBrushMode = button.dataset.darknessBrush;
                        environmentSection.querySelectorAll('[data-darkness-brush]').forEach(item => {
                            item.setAttribute('aria-pressed', String(item === button));
                        });
                        selectLocationTool('light');
                    };
                });
                window.locationDarknessPaintAtTile = (x, z) => {
                    if (!darknessBrushMode) return;
                    const radius = Number(environmentSection.querySelector('[data-darkness-radius]').value);
                    if (!paintLocationDarknessAtTile(x, z, radius, darknessBrushMode === 'erase')) return;
                    darkTilesDraft = structuredClone(currentLocationData.lighting.dark_tiles);
                    updateDarknessCount();
                    environmentStatus.textContent = 'Мазок сохраняется при отпускании ЛКМ';
                };
                if (window.locationLightingHistoryListener) window.removeEventListener('location-editor-lighting-applied', window.locationLightingHistoryListener);
                window.locationLightingHistoryListener = event => {
                    darkTilesDraft = structuredClone(currentLocationData.lighting.dark_tiles || []);
                    updateDarknessCount();
                    if (event.detail?.environment) mountEnvironmentEditor();
                    else {
                        const draft = environmentEditor.get(false);
                        if (draft) applyLocationLighting(draft, false);
                    }
                    environmentStatus.textContent = 'Изменения сцены сохранены';
                };
                window.addEventListener('location-editor-lighting-applied', window.locationLightingHistoryListener);
                environmentSection.querySelector('[data-revert-environment]').onclick = () => {
                    darkTilesDraft = (currentLocationData.lighting?.dark_tiles || []).map(tile => ({ ...tile }));
                    updateDarknessCount();
                    applyLocationLighting();
                    mountEnvironmentEditor();
                    environmentStatus.textContent = 'Изменения видны на карте до сохранения';
                };
                environmentSection.querySelector('[data-save-environment]').onclick = async () => {
                    const settings = environmentEditor.get();
                    if (!settings) return;
                    try {
                        if (!await saveLocationEnvironment(settings, environmentBase)) return;
                        const lighting = currentLocationData.lighting;
                        darkTilesDraft = lighting.dark_tiles.map(tile => ({ ...tile }));
                        updateDarknessCount();
                        environmentStatus.textContent = 'Свет сохранён';
                        showNotification('Освещение сохранено', 'success');
                    } catch (error) {
                        showNotification(error.message || 'Не удалось сохранить освещение');
                    }
                };

                // Получаем элементы
                const editCheckbox = document.getElementById('loc-edit-toggle-checkbox');
                const terrainSelect = document.getElementById('loc-edit-terrain');
                addTerrainMaterialOptions(terrainSelect);
                const grassSubtypeSelect = document.getElementById('loc-grass-subtype');
                const radiusSlider = document.getElementById('loc-edit-radius');
                const heightSlider = document.getElementById('loc-edit-height');
                const eraserCheck = document.getElementById('loc-eraser');
                const radiationSlider = document.getElementById('loc-edit-radiation');
                const radiationSpan = document.getElementById('loc-radiation-value');
                if (radiationSlider) {
                    radiationSlider.oninput = (e) => {
                        const val = getWorldNumericValue('loc-edit-radiation');
                        setLocationBrushRadiation(val);
                    };
                    setLocationBrushRadiation(0);
                }

                // Инициализация начальных значений из глобальных переменных
                radiusSlider.value = window.brushRadius;
                document.getElementById('loc-radius-value').textContent = window.brushRadius;
                setLocationBrushRadius(window.brushRadius);

                heightSlider.value = window.tileHeight;
                document.getElementById('loc-height-value').textContent = window.tileHeight.toFixed(1);
                setLocationBrushHeight(window.tileHeight);

                terrainSelect.value = window.currentTileType;
                setLocationBrushTerrain(window.currentTileType);
                waterSettings.hidden=terrainSelect.value!=='water';
                setLocationGrassSubtype(grassSubtypeSelect.value);

                const objModeCheck = document.getElementById('loc-obj-mode');
                const objTypeSelect = document.getElementById('loc-obj-type');
                const objColorInput = document.getElementById('loc-obj-color');
                const objOffsetX = document.getElementById('loc-obj-offset-x');
                const objOffsetZ = document.getElementById('loc-obj-offset-z');
                const objScale = document.getElementById('loc-obj-scale');
                const objRotation = document.getElementById('loc-obj-rotation');
                const buildModeSelect = document.getElementById('loc-build-mode');
                const structurePreset = document.getElementById('loc-structure-preset');
                const structureWidth = document.getElementById('loc-structure-width');
                const structureDepth = document.getElementById('loc-structure-depth');
                const structureHeight = document.getElementById('loc-structure-height');
                const structureRotation = document.getElementById('loc-structure-rotation');
                const structureColor = document.getElementById('loc-structure-color');
                const structureWidthField = document.getElementById('loc-structure-width-field');
                const structureDepthField = document.getElementById('loc-structure-depth-field');
                const structureHeightField = document.getElementById('loc-structure-height-field');
                const structureRoofTypeField = document.getElementById('loc-structure-roof-type-field');
                const structureRotationField = document.getElementById('loc-structure-rotation-field');
                const structureDefaults = {
                    wall: { width: 3, depth: 0.2, height: 2.4 },
                    floor: { width: 1, depth: 1, height: 0.12 },
                    door: { width: 0.9, depth: 0.18, height: 2 },
                    table: { width: 1.4, depth: 0.8, height: 1 },
                    chair: { width: 0.55, depth: 0.55, height: 1 },
                    box: { width: 0.8, depth: 0.8, height: 0.7 },
                    military_crate: { width: 1.1, depth: 0.8, height: 0.65 },
                    barrel: { width: 0.65, depth: 0.65, height: 1 },
                    shelf: { width: 1.2, depth: 0.4, height: 2 },
                    fence: { width: 2, depth: 0.15, height: 1.2 }
                };
                const applyStructurePreset = (preset) => {
                    const values = structureDefaults[preset];
                    if (!values) return;
                    structureWidth.value = values.width;
                    structureDepth.value = values.depth;
                    structureHeight.value = values.height;
                    setLocationStructurePreset(preset);
                    setLocationStructureWidth(values.width);
                    setLocationStructureDepth(values.depth);
                    setLocationStructureHeight(values.height);
                    const isFloor = preset === 'floor';
                    structureWidthField.firstChild.textContent = isFloor ? 'Размер ' : 'Ширина ';
                    structureWidth.type = isFloor ? 'range' : 'number';
                    structureWidth.min = isFloor ? '1' : '0.2';
                    structureWidth.max = isFloor ? '10' : '30';
                    structureWidth.step = isFloor ? '1' : '0.1';
                    structureDepthField.style.display = isFloor ? 'none' : '';
                    structureHeightField.style.display = isFloor ? 'none' : '';
                    structureRoofTypeField.style.display = isFloor ? '' : 'none';
                    structureRotationField.style.display = '';
                };

                if (objModeCheck) objModeCheck.onchange = (e) => setLocationBrushObjectMode(e.target.checked);
                if (objTypeSelect) objTypeSelect.onchange = (e) => setLocationBrushObjectType(e.target.value);
                if (objColorInput) objColorInput.oninput = (e) => setLocationBrushObjectColor(e.target.value);
                if (objOffsetX) objOffsetX.oninput = (e) => setLocationBrushObjectOffsetX(e.target.value);
                if (objOffsetZ) objOffsetZ.oninput = (e) => setLocationBrushObjectOffsetZ(e.target.value);
                if (objScale) objScale.oninput = (e) => setLocationBrushObjectScale(e.target.value);
                if (objRotation) objRotation.oninput = (e) => setLocationBrushObjectRotation(e.target.value);
                if (buildModeSelect) buildModeSelect.onchange = (e) => setLocationBuildMode(e.target.value);
                if (structurePreset) structurePreset.onchange = (e) => applyStructurePreset(e.target.value);
                if (structureWidth) structureWidth.oninput = (e) => setLocationStructureWidth(e.target.value);
                if (structureDepth) structureDepth.oninput = (e) => setLocationStructureDepth(e.target.value);
                if (structureHeight) structureHeight.oninput = (e) => setLocationStructureHeight(e.target.value);
                if (structureRotation) structureRotation.onchange = (e) =>
                    setLocationStructureRotation(Number(e.target.value) * Math.PI / 180);
                if (structureColor) structureColor.oninput = (e) => setLocationStructureColor(e.target.value);
                applyStructurePreset(structurePreset.value);


                editCheckbox.checked = getLocationEditMode();

                // Обработчики
                editCheckbox.onchange = (e) => setLocationEditMode(e.target.checked);

                terrainSelect.onchange = (e) => {setLocationBrushTerrain(e.target.value);waterSettings.hidden=e.target.value!=='water';};
                grassSubtypeSelect.onchange = (e) => setLocationGrassSubtype(e.target.value);

                radiusSlider.oninput = (e) => {
                    const val = getWorldNumericValue('loc-edit-radius');
                    setLocationBrushRadius(val);
                    document.getElementById('loc-radius-value').textContent = val;
                };

                heightSlider.oninput = (e) => {
                    const val = getWorldNumericValue('loc-edit-height');
                    setLocationBrushHeight(val);
                    document.getElementById('loc-height-value').textContent = String(val);
                };

                eraserCheck.onchange = (e) => setLocationEraserMode(e.target.checked);
                initWorldNumericInputs();
                selectLocationTool('select');
            }
        } else {
            // Обычные игроки: скрываем панель инструментов
            const toolsPanel = document.getElementById('panel-tools');
            if (toolsPanel) toolsPanel.style.display = 'none';
        }
        finishSceneLoading(loadingToken);
    } catch (err) {
        if (!isCurrentSceneLoading(loadingToken)) return;
        failSceneLoading(loadingToken, 'Не удалось загрузить подлокацию.');
        showNotification(err.message);
    }
};

async function deleteCurrentLocation(locationId) {
    try {
        await Server.deleteLocation(currentLobbyId, locationId);
        showNotification('Локация удалена', 'success');
        if (Number(getCurrentLocationId()) === Number(locationId)) exitLocation();
        if (socket) socket.emit('get_markers', { token, lobby_id: currentLobbyId });
    } catch (err) {
        showNotification(err.message);
    }
}

window.exitLocation = function() {
    invalidateSceneLoading();
    clearLocationEditorSubscriptions();
    window.cancelCommonMapTool?.();
    closeMapEditorQuickMenu();
    // Очищаем события локации, если они были установлены
    if (window._locationEventCleanup) {
        window._locationEventCleanup();
        window._locationEventCleanup = null;
    }
    const locationId = getCurrentLocationId();
    const characterId = window.currentLocationCharacterId;
    if (socket && locationId) {
        socket.emit('leave_location', {
            token: localStorage.getItem('access_token'),
            location_id: locationId,
            character_id: characterId
        });
    }
    // Сбрасываем флаг локации
    window.isLocationActive = false;
    window.locationEditorTool = 'select';
    delete window.selectLocationEditorTool;
    delete window.cancelLocationAssetPlacement;
    delete window.locationDarknessPaintAtTile;
    if (window.locationLightingHistoryListener) window.removeEventListener('location-editor-lighting-applied', window.locationLightingHistoryListener);
    delete window.locationLightingHistoryListener;
    window.currentLocationId = null;
    window.locationScoutView=null;
    window.currentLocationCharacterId = null;

    setLocationViewActive(false);
    showGlobalCanvas();

    setCurrentLocationId(null);
    currentLocationData = null;
    destroyLocationScene();

    // Удаляем кнопки удаления/редактирования
    const delBtn = document.getElementById('delete-location-btn');
    if (delBtn) delBtn.remove();
    const editBtn = document.getElementById('edit-location-btn');
    if (editBtn) editBtn.remove();
    document.getElementById('location-grid-btn')?.remove();
    document.getElementById('location-roof-btn')?.remove();
    document.getElementById('location-floor-menu')?.remove();
    document.getElementById('location-view-toggle-group')?.remove();

    // Восстанавливаем оригинальную панель инструментов (для глобальной карты)
    if (window.isGM && window._originalToolsContent) {
        const toolsPanel = document.getElementById('panel-tools');
        if (toolsPanel) {
            toolsPanel.classList.remove('is-location-editor', 'location-editor-idle');
            toolsPanel.querySelector('.panel-content').innerHTML = window._originalToolsContent;
            initWorldNumericInputs();
            window.refreshDirectWorldObjectEditor?.();
            syncMapEditorAutoExpandCheckbox();
            selectWorldEditorTool('select', { autoExpand: false });
            originalSetBrushRadiusFromInput(window.brushRadius);
            originalSetTileHeightFromInput(window.tileHeight);
            setWorldBrushRadiation(window.worldBrushRadiation ?? 0);
            document.getElementById('tile-type-select').value = window.currentTileType;
            setEditMode(getEditMode());
        }
    } else {
        const toolsPanel = document.getElementById('panel-tools');
        if (toolsPanel) toolsPanel.style.display = 'none';
    }

    // Принудительно обновляем глобальную карту
    resumeWorldMapLoad();
};

// Вспомогательная функция – генерация локации
window.generateLocationTiles = function(terrainType, width, height, generateObjects = true) {
    const terrainMap = {
        'grass': 'grass',
        'forest': 'grass',
        'rock': 'rock',
        'swamp': 'swamp',
        'water': 'water',
        'desert': 'sand',
        'urban': 'grass',
        'camp': 'grass'
    };
    const baseTerrain = terrainMap[terrainType] || 'grass';
    const tiles = [];
    for (let y = 0; y < height; y++) {
        const row = [];
        for (let x = 0; x < width; x++) {
            let heightVal = 1.0 + (Math.random() - 0.5) * 0.2;
            heightVal = Math.round(heightVal * 10) / 10;
            const tile = {
                terrain: baseTerrain,
                height: heightVal,
                objects: []
            };
            if (generateObjects) {
                if (terrainType === 'forest' && Math.random() < 0.3) {
                    tile.objects.push({ type: 'tree' });
                } else if (terrainType === 'rock' && Math.random() < 0.2) {
                    tile.objects.push({ type: 'rock' });
                } else if (terrainType === 'urban' && Math.random() < 0.15) {
                    tile.objects.push({ type: 'house' });
                } else if (terrainType === 'camp') {
                    if (Math.random() < 0.2) tile.objects.push({ type: 'tent' });
                }
            }
            row.push(tile);
        }
        tiles.push(row);
    }
    return tiles;
};

let selectedTileForLocation = null;
function openLocationCreateModal(tile) {
    selectedTileForLocation = tile;
    const modal = document.getElementById('location-create-modal');
    if (!modal) {
        showNotification('Ошибка: окно создания локации не найдено');
        return;
    }
    const globalTerrain = tile.tileData.terrain || 'grass';
    let suggestedTerrainOption = 'grass';
    switch(globalTerrain) {
        case 'grass': suggestedTerrainOption = 'grass'; break;
        case 'sand': suggestedTerrainOption = 'desert'; break;
        case 'rock': suggestedTerrainOption = 'rock'; break;
        case 'swamp': suggestedTerrainOption = 'swamp'; break;
        case 'water': suggestedTerrainOption = 'water'; break;
        default: suggestedTerrainOption = 'grass';
    }
    document.getElementById('new-loc-name').value = '';
    document.getElementById('new-loc-terrain').value = suggestedTerrainOption;
    document.getElementById('new-loc-width').value = 30;
    document.getElementById('new-loc-height').value = 30;
    document.getElementById('new-loc-gen-objects').checked = true;
    modal.style.display = 'flex';
}

async function createLocationFromModal() {
    const name = document.getElementById('new-loc-name').value.trim();
    if (!name) {
        showNotification('Введите название локации');
        return;
    }
    const terrainType = document.getElementById('new-loc-terrain').value;
    let width = parseInt(document.getElementById('new-loc-width').value);
    let height = parseInt(document.getElementById('new-loc-height').value);
    const genObjects = document.getElementById('new-loc-gen-objects').checked;
    if (isNaN(width) || width < 5) width = 30;
    if (isNaN(height) || height < 5) height = 30;
    width = Math.min(100, Math.max(10, width));
    height = Math.min(100, Math.max(10, height));

    if (!selectedTileForLocation) {
        showNotification('Не выбран тайл для локации');
        return;
    }
    const worldX = selectedTileForLocation.chunk.chunkX * 32 + selectedTileForLocation.tileX;
    const worldZ = selectedTileForLocation.chunk.chunkY * 32 + selectedTileForLocation.tileY;
    const tilesData = generateLocationTiles(terrainType, width, height, genObjects);

    try {
        await Server.createLocation(currentLobbyId, {
            name: name,
            type: 'exploration',
            world_tile_x: worldX,
            world_tile_z: worldZ,
            grid_width: width,
            grid_height: height,
            tiles_data: tilesData
        });
        showNotification('Локация создана', 'success');
        document.getElementById('location-create-modal').style.display = 'none';
        if (socket) socket.emit('get_markers', { token, lobby_id: currentLobbyId });
    } catch (err) {
        showNotification(err.message);
    }
}

function openLocationEditModal(locationData) {
    let dominantTerrain = 'grass';
    let terrainCount = {};
    let hasObjects = false;
    if (locationData.tiles_data && locationData.tiles_data.length) {
        for (let row of locationData.tiles_data) {
            for (let tile of row) {
                const t = tile.terrain || 'grass';
                terrainCount[t] = (terrainCount[t] || 0) + 1;
                if (tile.objects && tile.objects.length) hasObjects = true;
            }
        }
        let maxCount = 0;
        for (let [t, count] of Object.entries(terrainCount)) {
            if (count > maxCount) {
                maxCount = count;
                dominantTerrain = t;
            }
        }
    }

    const modal = document.createElement('div');
    modal.className = 'modal';
    modal.innerHTML = `
        <div class="modal-content" style="max-width: 620px;max-height:90vh;overflow:auto;">
            <span class="close" onclick="this.closest('.modal').remove()">&times;</span>
            <h3>Редактирование локации</h3>
            <div class="form-group">
                <label>Название</label>
                <input type="text" id="edit-loc-name" value="${escapeHtml(locationData.name)}" class="form-control">
            </div>
            <div class="form-group">
                <label>Ширина (тайлов)</label>
                <input type="number" id="edit-loc-width" value="${locationData.grid_width}" min="10" max="100" class="form-control">
            </div>
            <div class="form-group">
                <label>Высота (тайлов)</label>
                <input type="number" id="edit-loc-height" value="${locationData.grid_height}" min="10" max="100" class="form-control">
            </div>
            <div class="form-group">
                <label>Тип ландшафта</label>
                <select id="edit-loc-terrain" class="form-control">
                    <option value="grass" ${dominantTerrain === 'grass' ? 'selected' : ''}>🌿 Поле / Трава</option>
                    <option value="forest" ${dominantTerrain === 'forest' ? 'selected' : ''}>🌲 Лес</option>
                    <option value="rock" ${dominantTerrain === 'rock' ? 'selected' : ''}>⛰️ Горы / Камни</option>
                    <option value="swamp" ${dominantTerrain === 'swamp' ? 'selected' : ''}>💧 Болото</option>
                    <option value="water" ${dominantTerrain === 'water' ? 'selected' : ''}>🌊 Вода</option>
                    <option value="desert" ${dominantTerrain === 'sand' ? 'selected' : ''}>🏜️ Пустыня</option>
                    <option value="urban" ${dominantTerrain === 'urban' ? 'selected' : ''}>🏙️ Город / Руины</option>
                    <option value="camp" ${dominantTerrain === 'camp' ? 'selected' : ''}>🔥 Лагерь</option>
                </select>
            </div>
            <div class="form-group">
                <label><input type="checkbox" id="edit-loc-gen-objects" ${hasObjects ? 'checked' : ''}> Генерировать декоративные объекты</label>
            </div>
            <div class="form-group">
                <button id="regenerate-loc-btn" class="btn btn-secondary">🔄 Перегенерировать ландшафт</button>
            </div>
            <div class="form-group" style="padding:10px 12px;border:1px solid rgba(172,75,57,.45);background:rgba(91,31,23,.16);border-radius:7px;">
                <label style="display:flex;gap:8px;align-items:flex-start;cursor:pointer;">
                    <input type="checkbox" id="edit-loc-delete-structures" style="margin-top:3px;">
                    <span>
                        <strong>Удалить все созданные структуры</strong><br>
                        <small style="opacity:.75;">Будут удалены стены, полы, двери, мебель и контейнеры. Декор, аномалии и предметы на земле сохранятся.</small>
                    </span>
                </label>
            </div>
            <div class="form-actions">
                <button id="save-loc-changes" class="btn btn-primary">Сохранить</button>
                <button class="btn btn-secondary" onclick="this.closest('.modal').remove()">Отмена</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    modal.style.display = 'flex';
    let tempTiles = null;
    document.getElementById('regenerate-loc-btn').onclick = () => {
        const newWidth = parseInt(document.getElementById('edit-loc-width').value);
        const newHeight = parseInt(document.getElementById('edit-loc-height').value);
        const terrainType = document.getElementById('edit-loc-terrain').value;
        const genObjects = document.getElementById('edit-loc-gen-objects').checked;
        if (isNaN(newWidth) || isNaN(newHeight)) return;
        tempTiles = generateLocationTiles(terrainType, newWidth, newHeight, genObjects);
        showNotification('Ландшафт перегенерирован. Не забудьте сохранить.', 'success');
    };
    document.getElementById('save-loc-changes').onclick = async () => {
        const newName = document.getElementById('edit-loc-name').value;
        let newWidth = parseInt(document.getElementById('edit-loc-width').value);
        let newHeight = parseInt(document.getElementById('edit-loc-height').value);
        const deleteStructures = document.getElementById('edit-loc-delete-structures').checked;
        if (deleteStructures && !window.confirm('Удалить все созданные структуры этой локации? Отменить это действие после сохранения будет нельзя.')) {
            return;
        }
        let finalTiles = tempTiles;
        if (!finalTiles) finalTiles = locationData.tiles_data;
        if (newWidth !== locationData.grid_width || newHeight !== locationData.grid_height) {
            finalTiles = resizeTilesData(finalTiles, newWidth, newHeight);
        }
        try {
            const updates = {
                name: newName,
                grid_width: newWidth,
                grid_height: newHeight,
                delete_structures: deleteStructures,
            };
            if (tempTiles || newWidth !== locationData.grid_width || newHeight !== locationData.grid_height) {
                updates.tiles_data = finalTiles;
            }
            await Server.updateLocation(currentLobbyId, locationData.id, updates);
            showNotification('Локация обновлена', 'success');
            modal.remove();
            window.enterLocation(locationData.id);
        } catch (err) {
            showNotification(err.message);
        }
    };
}

function resizeTilesData(oldTiles, newWidth, newHeight) {
    const newTiles = [];
    for (let y = 0; y < newHeight; y++) {
        const row = [];
        for (let x = 0; x < newWidth; x++) {
            if (y < oldTiles.length && x < oldTiles[0].length) {
                row.push({ ...oldTiles[y][x] });
            } else {
                row.push({ terrain: 'grass', height: 1.0, objects: [] });
            }
        }
        newTiles.push(row);
    }
    return newTiles;
}

function startLocationPick() {
    window.awaitingLocationPick = true;
    showNotification('Кликните по тайлу на карте для создания локации', 'system');
    window.locationPickCallback = (tile) => {
        window.awaitingLocationPick = false;
        openLocationCreateModal(tile);
    };
}

document.getElementById('panel-tools')?.addEventListener('click', event => {
    if (event.target.closest('#create-location-btn')) startLocationPick();
});
document.getElementById('confirm-create-location')?.addEventListener('click', createLocationFromModal);
document.getElementById('exit-location-btn')?.addEventListener('click', exitLocation);

// ========== Обработчики сокетов для локации ==========
if (socket) {
    socket.on('location_deleted', data => {
        if (Number(getCurrentLocationId()) !== Number(data.location_id)) return;
        showNotification('Открытая подлокация была удалена', 'system');
        window.exitLocation();
    });
    socket.on('joined_location', (data) => {
        console.log('Joined location', data);
        if (data.character_id) {
            window.currentLocationCharacterId = data.character_id;
            localStorage.setItem('selectedLocationCharacterId', String(data.character_id));
        }
        updateCharacterPosition(data.character_id, data.x, data.y, data);
    });
    socket.on('location_state', (state) => {
        state.forEach(s => {
            import('./locationScene.js').then(module => {
                module.addCharacterToLocation(
                    s.character_id,
                    s.name,
                    Number(s.owner_id),
                    s.owner_username,
                    s.x,
                    s.y,
                    s.hp_zones,
                    s.effects,
                    Number(s.controlled_by),
                    s.team_name,
                    s.team_color,
                    s.facing_x,
                    s.facing_y,
                    s.is_mutant,
                    s.is_npc,
                    s.gm_hidden,
                    s.mutant_type,
                    s.posture,
                    s.prone_on_back,
                    s.floor_level,
                    s.height,
                );
            });
        });
    });
    socket.on('trap_alarm',data=>{
        if(data.location_id===window.currentLocationId)showNotification(data.message,'system');
    });
    socket.on('pda_network_updated',data=>{
        if(Number(data.lobby_id)===Number(window.currentLobbyId))window.dispatchEvent(new Event('pda-network-updated'));
    });
    socket.on('detonation_pending',data=>{
        if(Number(data.lobby_id)===Number(window.currentLobbyId)&&window.isGM){
            void import('./itemControllers.js').then(m=>m.refreshDetonations(window.currentLobbyId));
        }
    });
    socket.on('character_moved', (data) => {
        updateCharacterPosition(data.character_id, data.x, data.y, data);
        for (const check of data.slip_checks || []) {
            showNotification(`Лёд · Ловкость: ${check.total} против СЛ12 — ${check.success ? 'успех' : 'провал, переход на ходьбу'}`, 'system');
        }
        if (data.slip_stopped) showNotification('Оставшихся ОП хватило только на часть маршрута ходьбой.', 'system');
        if (data.fall) {
            const damage = (data.fall.legs || []).reduce((sum,leg)=>sum+Number(leg.damage || 0),0);
            showNotification(`Падение с ${Number(data.fall.height).toFixed(1)} м · урон ногам: ${damage}`, 'system');
        }
    });
    socket.on('movement_rejected', (data) => {
        if (Number.isFinite(Number(data.x)) && Number.isFinite(Number(data.y))) {
            updateCharacterPosition(data.character_id, Number(data.x), Number(data.y), data);
        }
        showNotification(`Ошибка: ${data.message || 'Перемещение недоступно'}`, 'error');
    });
    socket.on('location_tiles_updated', (data) => {
        if (data.location_id === getCurrentLocationId()) {
            import('./locationScene.js').then(module => {
                module.receiveLocationObjectEvent('applyLocationTilesUpdate', data.location_id, data.updates);
            });
        }
    });
    socket.on('location_editor_objects_changed', (data) => {
        if (Number(data.location_id) === Number(getCurrentLocationId())) {
            import('./locationScene.js').then(module => module.applyLocationEditorObjectsChange(data));
        }
    });
    socket.on('location_objects_changed', (data) => {
        if (Number(data.location_id) === Number(getCurrentLocationId())) {
            import('./locationScene.js').then(module => module.receiveLocationObjectEvent('applyLocationObjectsChange', data));
        }
    });
    socket.on('location_object_created', (data) => {
        if (Number(data.location_id) === Number(getCurrentLocationId())) {
            import('./locationScene.js').then(module => module.receiveLocationObjectEvent('addLocationObject', data.object));
        }
    });
    socket.on('location_object_deleted', (data) => {
        if (Number(data.location_id) === Number(getCurrentLocationId())) {
            import('./locationScene.js').then(module => module.receiveLocationObjectEvent('removeLocationObject', data.object_id));
        }
    });
    socket.on('location_object_updated', (data) => {
        if (Number(data.location_id) === Number(getCurrentLocationId())) {
            import('./locationScene.js').then(module => module.receiveLocationObjectEvent('updateLocationObject', data.object));
        }
    });
    socket.on('character_spawned', (data) => {
        const locId = getCurrentLocationId();
        if (!locId) return;
        import('./locationScene.js').then(module => {
            module.addCharacterToLocation(
                data.character.id,
                data.character.name,
                Number(data.character.owner_id),
                data.character.owner_username,
                data.character.pos_x,
                data.character.pos_y,
                data.character.hp_zones,
                data.character.effects,
                Number(data.character.controlled_by),
                data.character.team_name,
                data.character.team_color,
                data.character.facing_x,
                data.character.facing_y,
                data.character.is_mutant,
                data.character.is_npc,
                data.character.gm_hidden,
                data.character.mutant_type,
                data.character.posture,
                data.character.prone_on_back,
                data.character.floor_level,
                data.character.height,
            );
        });
    });
    socket.on('location_character_removed', (data) => {
        if (Number(data.location_id) !== Number(getCurrentLocationId())) return;
        import('./locationScene.js').then(module => {
            module.removeCharacterFromLocation(data.character_id);
        });
    });
    socket.on('location_character_visibility_updated', (data) => {
        if (Number(data.location_id) !== Number(getCurrentLocationId())) return;
        import('./locationScene.js').then(module => {
            module.applyCharacterVisibilityUpdate(data.character_id, data.gm_hidden);
        });
    });
    socket.on('location_character_posture_updated', (data) => {
        if (Number(data.location_id) !== Number(getCurrentLocationId())) return;
        import('./locationScene.js').then(module => {
            module.applyCharacterPostureVisual(data.character_id, data.posture);
        });
    });
    socket.on('location_character_visual_pose_updated', (data) => {
        if (Number(data.location_id) !== Number(getCurrentLocationId())) return;
        import('./locationScene.js').then(module => {
            module.applyCharacterVisualPose(data.character_id, data.prone_on_back);
        });
    });
    socket.on('location_character_facing_updated', (data) => {
        if (Number(data.location_id) !== Number(getCurrentLocationId())) return;
        import('./locationScene.js').then(module => {
            if (Number(data.location_id) !== Number(getCurrentLocationId())) return;
            module.applyCharacterFacingVisual(data.character_id, data.facing_x, data.facing_y);
        }).catch(error => {
            console.error('Не удалось обновить направление персонажа', error);
            showNotification('Не удалось обновить направление персонажа', 'system');
        });
    });
    socket.on('location_teams_updated', (data) => {
        if (Number(data.location_id) !== Number(getCurrentLocationId())) return;
        import('./locationScene.js').then(module => module.refreshLocationTeams());
    });

    const syncCombatState = (state) => {
        import('./locationScene.js').then(module => {
            module.setCombatState(state);
        });
    };

    socket.on('combat_state', syncCombatState);
    socket.on('combat_state_updated', syncCombatState);
    socket.on('combat_explosion', (data) => {
        if (Number(data?.location_id) !== Number(getCurrentLocationId())) return;
        import('./locationScene.js').then(module => module.showCombatExplosion(data));
    });
    socket.on('combat_start_requested', (data) => {
        showNotification(`${data.actor_name || 'Персонаж'} просит начать бой атакой`, 'system');
        handleCombatStartRequest(data);
    });
    socket.on('combat_start_request_resolved', handleCombatStartRequestResolved);
    socket.on('character_interaction_requested', (data) => {
        import('./locationScene.js').then(module => module.handleCharacterInteractionRequest(data));
    });
    socket.on('character_interaction_resolved', (data) => {
        import('./locationScene.js').then(module => module.handleCharacterInteractionResolved(data));
    });
}

// ========== Глобальные функции из других модулей ==========
window.sendMessage = () => {
    const input = document.getElementById('message-input');
    const message = input.value.trim();
    if (!message) return;
    socket.emit('send_message', { token, lobby_id: currentLobbyId, message });
    input.value = '';
};

window.exitLobbyToList = () => {
    if (!confirm('Выйти из комнаты в список комнат?')) return;
    socket.disconnect();
    window.location.href = '/';
};

window.toggleParticipants = toggleParticipants;
window.toggleSettings = toggleSettings;
window.showSettingsTab = showSettingsTab;
window.closeSettings = closeSettings;
window.openSettings = openSettings;
window.closeVisibilityModal = closeVisibilityModal;
window.saveVisibility = saveVisibility;
window.unbanUserHandler = unbanUserHandler;

// Функции для модального окна редактирования тайла
window.openTileEditModal = openTileEditModal;
window.closeTileEditModal = closeTileEditModal;
window.applyTerrainChange = applyTerrainChange;
window.applyHeightChange = applyHeightChange;
window.setWorldBrushRadiation = setWorldBrushRadiation;
window.applyRadiationChange = applyRadiationChange;
window.applyAnomalyFieldChange = applyAnomalyFieldChange;
window.updateAnomalyFieldRankOptions = updateAnomalyFieldRankOptions;
window.addObjectToTile = addObjectToTile;
window.clearObjectsFromTile = clearObjectsFromTile;
window.removeObjectFromTile = removeObjectFromTile;
window.updateTileEditHeight = updateTileEditHeight;
window.updateTileEditRadiation = updateTileEditRadiation;
window.updateObjectOffsetX = updateObjectOffsetX;
window.updateObjectOffsetZ = updateObjectOffsetZ;
window.updateObjectScale = updateObjectScale;
window.updateObjectRotation = updateObjectRotation;

window.openCharacterSheet = openCharacterSheet;
window.closeCharacterSheet = closeCharacterSheet;
window.exportCharacter = exportCharacter;
window.importCharacter = importCharacter;

window.showCreateCharacterForm = showCreateCharacterForm;
window.showNpcGeneratorSettings = showNpcGeneratorSettings;
window.showCreateMutantForm = showCreateMutantForm;
window.openLobbyRestModal = openLobbyRestModal;
window.closeLobbyRestModal = closeLobbyRestModal;
window.saveLobbyTimeActivity = saveLobbyTimeActivity;
window.startLobbyRest = startLobbyRest;
window.openWorldTravelModal = openWorldTravelModal;
window.closeWorldTravelModal = closeWorldTravelModal;
window.beginWorldGroupCreation = beginWorldGroupCreation;
window.beginWorldMapEventCreation = beginWorldMapEventCreation;

// Маркеры
window.closeMarkerEditModal = closeMarkerEditModal;
window.saveMarkerEdit = saveMarkerEdit;
window.submitCreateMarker = submitCreateMarker;
window.openCreateMarkerModal = openCreateMarkerModal;
window.openCreateMarkerModalAtCenter = openCreateMarkerModalAtCenter;
window.fillCenterCoordinates = fillCenterCoordinates;
window.deleteMarker = deleteMarker;
window.fillEditCenterCoordinates = fillEditCenterCoordinates;
window.pickTileForMarker = pickTileForMarker;

// Экспорт карты
window.exportMap = async () => {
    try {
        const blob = await Server.exportMap(currentLobbyId);
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
    } catch (error) {
        showNotification(error.message);
    }
};

window.toggleTheme = () => {
    document.body.classList.toggle('light-theme');
    const isLight = document.body.classList.contains('light-theme');
    localStorage.setItem('theme', isLight ? 'light' : 'dark');
    const btn = document.getElementById('theme-toggle');
    if (btn) btn.textContent = isLight ? '🌑' : '🌓';
};

window.applyWeatherSettings = () => {
    const settings = {
        fog: {
            enabled: document.getElementById('weather-fog').checked,
            intensity: parseFloat(document.getElementById('weather-fog-intensity').value)
        },
        rain: {
            enabled: document.getElementById('weather-rain').checked,
            intensity: parseFloat(document.getElementById('weather-rain-intensity').value)
        },
        sun: {
            enabled: document.getElementById('weather-sun').checked,
            intensity: parseFloat(document.getElementById('weather-sun-intensity').value)
        },
        emission: {
            enabled: document.getElementById('weather-emission').checked,
            intensity: parseFloat(document.getElementById('weather-emission-intensity').value)
        }
    };
    fetch(`/lobbies/${currentLobbyId}/weather`, {
        method: 'PATCH',
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
        },
        body: JSON.stringify(settings)
    }).then(res => {
        if (!res.ok) throw new Error('Failed to update weather');
        showNotification('Погода обновлена', 'success');
    }).catch(err => showNotification(err.message));
};

window.loadWeatherSettings = (settings) => {
    const updateControl = (id, enabled, intensity) => {
        const cb = document.getElementById(id);
        const range = document.getElementById(id + '-intensity');
        const valueSpan = document.getElementById(id + '-value');
        if (cb) cb.checked = enabled;
        if (range) {
            range.value = intensity;
            range.disabled = !enabled;
        }
        if (valueSpan) valueSpan.textContent = intensity.toFixed(1);
    };
    updateControl('weather-fog', settings.fog?.enabled || false, settings.fog?.intensity ?? 0.5);
    updateControl('weather-rain', settings.rain?.enabled || false, settings.rain?.intensity ?? 0.5);
    updateControl('weather-sun', settings.sun?.enabled || false, settings.sun?.intensity ?? 0.5);
    updateControl('weather-emission', settings.emission?.enabled || false, settings.emission?.intensity ?? 0.5);
};

window.updateWeatherValue = (id, value) => {
    const span = document.getElementById(id + '-value');
    if (span) span.textContent = parseFloat(value).toFixed(1);
};

function bindWeatherSliders() {
    const sliderIds = ['weather-fog-intensity', 'weather-rain-intensity', 'weather-sun-intensity', 'weather-emission-intensity'];
    sliderIds.forEach(id => {
        const slider = document.getElementById(id);
        const checkbox = document.getElementById(id.replace('-intensity', ''));
        if (slider) {
            slider.addEventListener('input', (e) => {
                window.updateWeatherValue(id.replace('-intensity', ''), e.target.value);
            });
            checkbox?.addEventListener('change', () => {
                slider.disabled = !checkbox.checked;
            });
        }
    });
}
bindWeatherSliders();

window.setCurrentTileTypeFromUI = function(value) {
    if (window.isLocationActive) {
        setLocationBrushTerrain(value);
    } else {
        const select = document.getElementById('tile-type-select');
        if (select) {
            select.value = value;
            AppState.setCurrentTileType(value);
            select.dispatchEvent(new Event('change', { bubbles: true }));
        }
    }
};

let sceneLoadingGeneration = 0;
let sceneLoadingController = null;
let sceneLoadingType = 'world';
let sceneLoadingProgress = 0;
let sceneLoadingActive = false;

function invalidateSceneLoading() {
    sceneLoadingController?.abort();
    sceneLoadingActive = false;
    sceneLoadingGeneration++;
}

window.cancelSceneLoading = function() {
    if (!sceneLoadingActive) return;
    const returnToMap = sceneLoadingType === 'location' && Number.isInteger(window.MAP_CHUNKS_WIDTH);
    invalidateSceneLoading();
    window.locationDirectOpenPending = false;
    if (returnToMap) window.exitLocation();
    else window.location.href = '/';
};

function isCurrentSceneLoading(token) {
    return token === sceneLoadingGeneration;
}

function showSceneLoading(sceneType) {
    sceneLoadingController?.abort();
    sceneLoadingController = new AbortController();
    sceneLoadingType = sceneType;
    sceneLoadingActive = true;
    sceneLoadingProgress = 0;
    const token = ++sceneLoadingGeneration;
    const screen = document.getElementById('scene-loading-screen');
    if (!screen) return token;
    screen.classList.remove('is-hidden', 'is-error');
    document.getElementById('scene-loading-title').textContent =
        sceneType === 'location' ? 'Загрузка…' : (document.getElementById('lobby-name')?.textContent || 'Карта мира');
    document.getElementById('scene-loading-detail').textContent = sceneType === 'location'
        ? 'Разворачиваем рельеф, объекты и освещение.'
        : 'Загружаем сектора и собираем поверхность.';
    document.getElementById('scene-loading-retry').hidden = true;
    const cancel = document.getElementById('scene-loading-cancel');
    if (cancel) {
        cancel.hidden = false;
        cancel.textContent = sceneType === 'location' && Number.isInteger(window.MAP_CHUNKS_WIDTH)
            ? 'Отменить и вернуться на карту' : 'Отменить и вернуться в меню';
    }
    setSceneLoadingProgress(token, 0);
    return token;
}

function setSceneLoadingProgress(token, percent, detail) {
    if (!isCurrentSceneLoading(token)) return;
    sceneLoadingProgress = Math.max(sceneLoadingProgress, Math.min(100, Math.max(0, Math.round(percent))));
    const label = document.getElementById('scene-loading-percent');
    const fill = document.getElementById('scene-loading-fill');
    const progress = document.getElementById('scene-loading-progress');
    if (label) label.textContent = `${sceneLoadingProgress}%`;
    if (fill) fill.style.width = `${sceneLoadingProgress}%`;
    progress?.setAttribute('aria-valuenow', String(sceneLoadingProgress));
    if (detail) document.getElementById('scene-loading-detail').textContent = detail;
}

function setSceneLoadingLocationName(token, name) {
    if (!isCurrentSceneLoading(token)) return;
    document.getElementById('scene-loading-title').textContent = String(name || '').trim() || 'Загрузка…';
}

function finishSceneLoading(token) {
    setSceneLoadingProgress(token, 100);
    requestAnimationFrame(() => requestAnimationFrame(() => {
        if (isCurrentSceneLoading(token)) {
            sceneLoadingActive = false;
            const cancel = document.getElementById('scene-loading-cancel');
            if (cancel) cancel.hidden = true;
            document.getElementById('scene-loading-screen')?.classList.add('is-hidden');
        }
    }));
}

function failSceneLoading(token, message) {
    if (!isCurrentSceneLoading(token)) return;
    const screen = document.getElementById('scene-loading-screen');
    if (!screen) return;
    screen.classList.remove('is-hidden');
    screen.classList.add('is-error');
    document.getElementById('scene-loading-title').textContent = 'Ошибка загрузки';
    document.getElementById('scene-loading-detail').textContent = message;
    document.getElementById('scene-loading-retry').hidden = false;
}

function escapeHtml(unsafe) {
    if (!unsafe) return '';
    return String(unsafe)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

// Загружаем данные при старте
(async function init() {
    const startupToken = showSceneLoading(directLocationId ? 'location' : 'world');
    try {
        await loadLobbyInfo();
        if (!isCurrentSceneLoading(startupToken)) return;
        if (!directLocationId) setSceneLoadingLocationName(startupToken, document.getElementById('lobby-name').textContent);
        else document.getElementById('scene-loading-cancel').textContent = 'Отменить и вернуться на карту';
        setSceneLoadingProgress(startupToken, 5, 'Загружаем персонажей комнаты.');
        await loadLobbyCharacters();
        initHotkeys();
    } catch (error) {
        console.error('Lobby initialization failed', error);
        failSceneLoading(startupToken, 'Не удалось подготовить комнату.');
        return;
    }
    if (!isCurrentSceneLoading(startupToken)) return;
    if (directLocationId) {
        await window.enterLocation(directLocationId);
        window.locationDirectOpenPending = false;
    } else {
        resumeWorldMapLoad();
    }
    initWorldTravel(currentLobbyId, socket).catch(error => console.error('World travel load failed', error));

    // ===== Настройка кнопки выбора цвета =====
    const colorPickerContainer = document.getElementById('color-picker-container');
    if (colorPickerContainer) {
        colorPickerContainer.style.display = window.isGM ? 'none' : 'inline-block';
    }

    const colorPickerBtn = document.getElementById('color-picker-btn');
    if (colorPickerBtn) {
        colorPickerBtn.addEventListener('click', () => {
            const currentColor = getUserColorHex(parseInt(localStorage.getItem('user_id')));
            const input = document.createElement('input');
            input.type = 'color';
            input.value = currentColor;
            input.addEventListener('input', async (e) => {
                try {
                    await updateMyColor(e.target.value);
                    showNotification('Цвет обновлён', 'success');
                    updateParticipantsList();
                    if (window.isLocationActive) {
                        const locId = getCurrentLocationId();
                        if (locId) {
                            window.enterLocation(locId);
                        }
                    }
                } catch (err) {
                    showNotification(err.message);
                }
            });
            input.click();
        });
    }
})();
