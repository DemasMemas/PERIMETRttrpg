import { DEFAULT_LOCATION_LIGHTING } from './locationLighting.js';
import { bindNumericInput, readNumericInput } from './worldNumericInputs.js';

const safeColor = (value, fallback) => /^#[0-9a-f]{6}$/i.test(value || '') ? value : fallback;

export function createLocationLightingEditor(container, initial, onPreview = () => {},
    getLights = () => initial?.local_lights || [], getDarkTiles = () => initial?.dark_tiles || [],
    getVisualPreset = () => 'current', onVisualPresetChange = () => {}) {
    const settings = { ...DEFAULT_LOCATION_LIGHTING, ...(initial || {}) };
    const input = (label, key, min, max) => `
        <label class="location-lighting-field">
            ${label}
            <input class="form-control" type="number" data-lighting="${key}" min="${min}" max="${max}" step="any" value="${Number(settings[key])}" title="Точность до 0,001">
        </label>`;
    container.innerHTML = `
        <label class="location-lighting-field location-lighting-mode">Режим
            <select class="form-control" data-lighting="mode">
                <option value="world">По времени мира</option>
                <option value="manual">Ручной: помещение, подземелье</option>
            </select>
        </label>
        <label class="location-lighting-field">Визуальный пресет
            <select class="form-control" data-lighting-preset>
                <option value="current">Текущий свет</option>
                <option value="natural_daylight">Естественный дневной</option>
            </select>
        </label>
        <div class="location-lighting-manual">
            <label class="location-lighting-field">Цвет общего света<input class="form-control" type="color" data-lighting="ambient_color" value="${safeColor(settings.ambient_color, DEFAULT_LOCATION_LIGHTING.ambient_color)}"></label>
            ${input('Яркость общего света', 'ambient_intensity', 0, 3)}
            <label class="location-lighting-field">Цвет направленного света<input class="form-control" type="color" data-lighting="directional_color" value="${safeColor(settings.directional_color, DEFAULT_LOCATION_LIGHTING.directional_color)}"></label>
            ${input('Яркость направленного света', 'directional_intensity', 0, 3)}
            ${input('Направление, °', 'directional_azimuth', -180, 180)}
            ${input('Высота света, °', 'directional_elevation', 0, 90)}
            <label class="location-lighting-field">Цвет фона<input class="form-control" type="color" data-lighting="background_color" value="${safeColor(settings.background_color, DEFAULT_LOCATION_LIGHTING.background_color)}"></label>
        </div>
        <small class="location-lighting-hint">Изменения видны сразу. Лампы размещаются на карте через Библиотеку ассетов.</small>
    `;
    const mode = container.querySelector('[data-lighting="mode"]');
    mode.value = settings.mode === 'manual' ? 'manual' : 'world';
    const visualPreset = container.querySelector('[data-lighting-preset]');
    visualPreset.value = getVisualPreset();
    visualPreset.addEventListener('change', () => onVisualPresetChange(visualPreset.value));
    const manual = container.querySelector('.location-lighting-manual');
    const syncMode = () => {
        manual.style.display = mode.value === 'manual' ? 'grid' : 'none';
        visualPreset.disabled = mode.value === 'manual';
    };
    mode.addEventListener('change', syncMode);
    syncMode();

    const editor = {
        get(report = true) {
            const value = { ...settings, mode: mode.value };
            for (const element of manual.querySelectorAll('[data-lighting]')) {
                const key = element.dataset.lighting;
                value[key] = element.type === 'number'
                    ? readNumericInput(element, { report: report && mode.value === 'manual', precision: 3 })
                    : element.value;
                if (element.type === 'number' && !Number.isFinite(value[key]) && mode.value === 'world') {
                    value[key] = settings[key];
                }
                if (element.type === 'number' && !Number.isFinite(value[key])) return null;
            }
            value.local_lights = getLights().map(light => ({ ...light }));
            value.dark_tiles = getDarkTiles().map(tile => ({ ...tile }));
            return value;
        },
    };
    const preview = event => {
        if (event.target === visualPreset) return;
        const value = editor.get(false);
        if (value) onPreview(value);
    };
    container.querySelectorAll('input[type="number"]').forEach(element => {
        bindNumericInput(element, { precision: 3, onCommit: () => preview({ target: element }) });
    });
    container.oninput = preview;
    container.onchange = preview;
    return editor;
}
