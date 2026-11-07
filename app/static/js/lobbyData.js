// static/js/lobbyData.js
import { Server } from './api.js';
import { addChunk, chunksMap, setMapDimensions, setTileClickCallback,
    setWorldGrassGroundPixelsEnabled, waitForWorldTerrainRefresh, beginWorldTerrainLoad,
    prepareInitialWorldTerrainPixels } from './lobby3d.js';
import { loadGrassGroundPixels } from './locationTerrainTexture.js';
import { setLobbyData, updateParticipantsList } from './ui.js';
import AppState from './ui_interactions.js';
import { applyWeather, applyWorldTime } from './weather.js';
import { updateMapTileSize } from './markers.js';

let currentLobbyId;

function applyLobbyTime(data) {
    const dayInput = document.getElementById('lobby-game-day');
    const timeInput = document.getElementById('lobby-game-time');
    const minutes = Math.max(0, Math.min(1439, Number(data?.game_time_minutes ?? 480)));
    window.lobbyGameDay = Math.max(1, Number(data?.game_day || 1));
    window.lobbyGameTimeMinutes = minutes;
    applyWorldTime(minutes);
    if (dayInput) dayInput.value = Math.max(1, Number(data?.game_day || 1));
    if (timeInput) {
        const hours = Math.floor(minutes / 60);
        const mins = minutes % 60;
        timeInput.value = `${String(hours).padStart(2, '0')}:${String(mins).padStart(2, '0')}`;
    }
}

export function updateDisplayedLobbyTime(data) {
    applyLobbyTime(data);
}

export function initLobbyData(lobbyId) {
    currentLobbyId = lobbyId;
}

export async function loadLobbyInfo() {
    try {
        const lobby = await Server.getLobbyInfo(currentLobbyId);
        document.getElementById('lobby-name').textContent = lobby.name;
        setLobbyData(lobby.participants, lobby.gm_id);
        updateParticipantsList();

        applyWeather(lobby.weather_settings || {});
        window.weatherSettings = lobby.weather_settings || {};

        window.isGM = (lobby.gm_id == localStorage.getItem('user_id'));
        AppState.setIsGM(window.isGM);
        applyLobbyTime(lobby);
        const dayInput = document.getElementById('lobby-game-day');
        const timeInput = document.getElementById('lobby-game-time');
        [dayInput, timeInput].forEach(input => { if (input) input.disabled = !window.isGM; });
        const saveTime = async () => {
            if (!window.isGM || !dayInput || !timeInput || !timeInput.value) return;
            const [hours, minutes] = timeInput.value.split(':').map(Number);
            try {
                await Server.updateLobbyTime(
                    currentLobbyId,
                    Number(dayInput.value),
                    hours * 60 + minutes,
                );
            } catch (error) {
                window.showNotification?.(error.message);
                applyLobbyTime(lobby);
            }
        };
        if (dayInput && !dayInput._lobbyTimeBound) {
            dayInput.addEventListener('change', saveTime);
            dayInput._lobbyTimeBound = true;
        }
        if (timeInput && !timeInput._lobbyTimeBound) {
            timeInput.addEventListener('change', saveTime);
            timeInput._lobbyTimeBound = true;
        }

        window.MAP_CHUNKS_WIDTH = lobby.chunks_width;
        window.MAP_CHUNKS_HEIGHT = lobby.chunks_height;
        setMapDimensions(lobby.chunks_width, lobby.chunks_height);
        updateMapTileSize(lobby.chunks_width, lobby.chunks_height);

        const weatherTab = document.getElementById('weather-tab-btn');
        if (weatherTab) {
            weatherTab.style.display = window.isGM ? 'inline-block' : 'none';
        }

        const mapSizeSpan = document.getElementById('map-size-info');
        if (mapSizeSpan) {
            mapSizeSpan.textContent = `${lobby.chunks_width} x ${lobby.chunks_height}`;
        }

        if (window.isGM) {
            document.getElementById('settings-btn').style.display = 'inline-block';
            document.getElementById('lobby-rest-btn').style.display = 'inline-block';
            document.getElementById('edit-toggle').style.display = 'inline-block';
            document.getElementById('templates-manager-btn').style.display = 'inline-block';
            const mutantButton = document.getElementById('create-mutant-btn');
            if (mutantButton) mutantButton.style.display = 'inline-block';
            const codeElement = document.getElementById('gm-invite-code');
            const codeSpan = document.getElementById('invite-code-value');
            if (codeElement && codeSpan) {
                codeSpan.textContent = lobby.invite_code;
                codeElement.style.display = 'inline-block';
            }

            setTileClickCallback((options) => {
                const { tile, isDoubleClick } = options;
                if (!window.isGM) {
                    window.showNotification('Только ГМ может редактировать тайлы');
                    return;
                }
                if (window.isWorldRoadLinkActive?.()) {
                    window.placeWorldObjectAtTile?.(tile);
                } else if (isDoubleClick) {
                    window.openTileEditModal(tile);
                } else if (window.eraserMode) {
                    window.applyBrush(tile, { objects: [] }, window.brushRadius);
                } else if (window.worldEditorTool === 'terrain') {
                    window.applyBrush(tile, { terrain: window.currentTileType }, window.brushRadius);

                } else if (window.worldEditorTool === 'height') {
                    if (document.getElementById('world-water-level-enabled')?.checked)
                        window.levelWorldWaterBody?.(tile);
                    else window.applyBrush(tile, { height: window.tileHeight }, window.brushRadius);
                } else if (window.worldEditorTool === 'radiation') {
                    window.applyBrush(tile, { radiation: window.worldBrushRadiation ?? 0 }, window.brushRadius);
                } else if (window.worldEditorTool === 'objects') {
                    window.placeWorldObjectAtTile?.(tile);
                }
            });
        }
    } catch (error) {
        console.error('loadLobbyInfo error:', error);
    }
}

let loadingAllChunks = null;
let chunkLoadControl = null;
let worldGrassGroundReady = null;

function prepareWorldGrassGround() {
    if (!worldGrassGroundReady) {
        worldGrassGroundReady = loadGrassGroundPixels().then(() => true).catch(error => {
            console.warn('World grass ground unavailable; using procedural fallback', error);
            return false;
        });
    }
    return worldGrassGroundReady;
}

export function loadAllChunks({ signal, onProgress } = {}) {
    if (loadingAllChunks) {
        if (chunkLoadControl.signal?.aborted) {
            return loadingAllChunks.then(() => signal?.aborted ? undefined : loadAllChunks({ signal, onProgress }));
        }
        if (signal) chunkLoadControl.signal = signal;
        if (onProgress) {
            chunkLoadControl.listeners.add(onProgress);
            onProgress(chunkLoadControl.progress, chunkLoadControl.detail);
        }
        return loadingAllChunks;
    }
    if (window.locationDirectOpenPending || window.isLocationActive) return Promise.resolve();
    if (!Number.isInteger(window.MAP_CHUNKS_WIDTH) || !Number.isInteger(window.MAP_CHUNKS_HEIGHT)) {
        return Promise.resolve();
    }
    const startedAt = performance.now();
    const control = { signal, listeners: new Set(onProgress ? [onProgress] : []), progress: 0 };
    chunkLoadControl = control;
    const reportProgress = (value, detail) => {
        control.progress = Math.max(control.progress, value);
        if (detail) control.detail = detail;
        if (!control.signal?.aborted) control.listeners.forEach(listener => listener(control.progress, control.detail));
    };
    const metrics = {
        startedAt, firstFrameMs: null, pageToFirstFrameMs: null,
        fullLoadMs: null, pageToFullLoadMs: null,
        expectedChunks: window.MAP_CHUNKS_WIDTH * window.MAP_CHUNKS_HEIGHT,
        loadedChunks: 0, requests: 0, failedRequests: 0, chunks: 0,
        requestAndParseMs: 0, addChunkMs: 0,
        terrainRefreshMs: 0, terrainRefreshes: 0,
        terrainFullRefreshes: 0, terrainPartialRefreshes: 0,
        terrainStages: { geometryMs: 0, groundPixelsMs: 0, waterMs: 0, dependentMeshesMs: 0 },
        groundPixelDetails: {
            fullMs: 0, partialMs: 0, reusedMs: 0,
            fullPasses: 0, partialPasses: 0, reusedPasses: 0,
            fullPixels: 0, partialPixels: 0, terrainLookups: 0,
            photoColorCalls: 0, photoSampledCalls: 0, photoSampledMs: 0,
            photoSamplingEstimateMs: 0, otherProcessingEstimateMs: 0,
        },
        grassGroundSource: 'unchanged', grassGroundReadyMs: 0,
    };
    window.worldLoadMetrics = metrics;
    window.getWorldLoadDiagnostics = () => ({ ...metrics });
    performance.mark('world-map-load-start');
    loadingAllChunks = (async () => {
        const ranges = [];
        for (let cx = 0; cx < window.MAP_CHUNKS_WIDTH; cx += 2) {
            for (let cy = 0; cy < window.MAP_CHUNKS_HEIGHT; cy += 2) {
                const maxX = Math.min(cx + 1, window.MAP_CHUNKS_WIDTH - 1);
                const maxY = Math.min(cy + 1, window.MAP_CHUNKS_HEIGHT - 1);
                let complete = true;
                for (let x = cx; x <= maxX; x++) {
                    for (let y = cy; y <= maxY; y++) {
                        if (!chunksMap.has(`${x},${y}`)) complete = false;
                    }
                }
                if (!complete) ranges.push([cx, maxX, cy, maxY]);
            }
        }
        const centerX = window.MAP_CHUNKS_WIDTH / 2;
        const centerY = window.MAP_CHUNKS_HEIGHT / 2;
        ranges.sort((a, b) => {
            const distance = range => ((range[0] + range[1] + 1) / 2 - centerX) ** 2
                + ((range[2] + range[3] + 1) / 2 - centerY) ** 2;
            return distance(a) - distance(b);
        });
        const grassGroundStartedAt = performance.now();
        const grassGroundReady = ranges.length ? prepareWorldGrassGround().then(enabled => {
            if (control.signal?.aborted) return;
            setWorldGrassGroundPixelsEnabled(enabled);
            metrics.grassGroundSource = enabled ? 'photos' : 'procedural-fallback';
            metrics.grassGroundReadyMs = performance.now() - grassGroundStartedAt;
        }) : Promise.resolve();
        let nextIndex = 0;
        const worker = async () => {
            while (nextIndex < ranges.length) {
                if (window.isLocationActive || control.signal?.aborted) return;
                await fetchChunkBatch(ranges[nextIndex++], metrics, grassGroundReady, control);
                reportProgress(65 * chunksMap.size / metrics.expectedChunks, 'Получаем данные секторов.');
            }
        };
        reportProgress(65 * chunksMap.size / metrics.expectedChunks, 'Получаем данные секторов.');
        // Чанки доступны для живых правок сразу, но тяжёлая сборка рельефа
        // выполняется один раз, когда поступят данные всех соседей.
        const finishTerrainLoad = beginWorldTerrainLoad();
        try {
            await Promise.all(Array.from({ length: Math.min(4, ranges.length) }, worker));
            if (!control.signal?.aborted && !window.isLocationActive) {
                reportProgress(65, 'Подготавливаем текстуры поверхности.');
                await prepareInitialWorldTerrainPixels({ signal: control.signal,
                    onProgress: fraction => reportProgress(65 + 20 * fraction) });
                reportProgress(90, 'Собираем рельеф, берега и дороги.');
            }
        } finally {
            finishTerrainLoad({ defer: Boolean(control.signal?.aborted || window.isLocationActive) });
        }
        if (control.signal?.aborted) return;
        if (window.isLocationActive) {
            metrics.pausedForLocation = true;
            return;
        }
        await waitForWorldTerrainRefresh();
        if (control.signal?.aborted) return;
        metrics.loadedChunks = 0;
        for (let x = 0; x < window.MAP_CHUNKS_WIDTH; x++) {
            for (let y = 0; y < window.MAP_CHUNKS_HEIGHT; y++) {
                if (chunksMap.has(`${x},${y}`)) metrics.loadedChunks++;
            }
        }
        metrics.complete = metrics.loadedChunks === metrics.expectedChunks;
        if (!metrics.complete) {
            console.warn('[World load] incomplete', window.getWorldLoadDiagnostics());
            return;
        }
        metrics.fullLoadMs = performance.now() - startedAt;
        metrics.pageToFullLoadMs = performance.now();
        performance.mark('world-map-load-complete');
        performance.measure('world-map-load', 'world-map-load-start', 'world-map-load-complete');
        console.info('[World load]', window.getWorldLoadDiagnostics());
        reportProgress(100);
    })().finally(() => { loadingAllChunks = null; chunkLoadControl = null; });
    return loadingAllChunks;
}

async function fetchChunkBatch([minX, maxX, minY, maxY], metrics, grassGroundReady, control = {}) {
    try {
        const requestedAt = performance.now();
        const chunks = await Server.getChunks(currentLobbyId, minX, maxX, minY, maxY, { signal: control.signal });
        metrics.requests++;
        metrics.requestAndParseMs += performance.now() - requestedAt;
        if (window.isLocationActive || control.signal?.aborted) return;
        await grassGroundReady;
        if (window.isLocationActive || control.signal?.aborted) return;
        for (const chunk of chunks) {
            if (window.isLocationActive || control.signal?.aborted) return;
            if (chunksMap.has(`${chunk.chunk_x},${chunk.chunk_y}`)) continue;
            const buildStartedAt = performance.now();
            addChunk(chunk.chunk_x, chunk.chunk_y, chunk.data);
            metrics.addChunkMs += performance.now() - buildStartedAt;
            metrics.chunks++;
        }
    } catch (error) {
        if (control.signal?.aborted) return;
        metrics.failedRequests++;
        console.error('Error fetching chunks', minX, maxX, minY, maxY, error);
    }
}
