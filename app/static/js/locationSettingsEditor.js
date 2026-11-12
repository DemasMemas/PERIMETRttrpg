import { bindNumericInput, readNumericInput } from './worldNumericInputs.js';

export function createLocationSettingsEditor(container, initial, tiles, onPreview) {
    container.innerHTML = `
        <label class="location-setting-row">
            <span>Дымка <small>Видимость дальнего плана</small></span>
            <input class="form-control" type="number" data-scene-setting="fog_density" min="0" max="0.1" step="0.001">
        </label>
        <label class="location-setting-row">
            <span>Отражения в воде</span>
            <input class="location-setting-switch" type="checkbox" data-scene-setting="water_reflections">
        </label>
        <label class="location-setting-row">
            <span>Природный декор <small>Трава, камыши, лужи и прочий мелкий декор</small></span>
            <input class="location-setting-switch" type="checkbox" data-scene-setting="natural_detail">
        </label>`;
    const fog = container.querySelector('[data-scene-setting="fog_density"]');
    const reflections = container.querySelector('[data-scene-setting="water_reflections"]');
    const natural = container.querySelector('[data-scene-setting="natural_detail"]');
    fog.value = Number(initial?.fog_density) || 0;
    reflections.checked = initial?.water_reflections === true;
    const savedNatural = initial?.natural_detail ?? null;
    const hasTileOverride = value => tiles.some(row => row.some(tile =>
        ['grass', 'sand', 'water', 'swamp', 'shallow_water'].includes(tile.terrain)
        && (tile.natural_detail !== false) === value));
    natural.checked = savedNatural ?? !hasTileOverride(false);
    natural.indeterminate = savedNatural === null && hasTileOverride(false) && hasTileOverride(true);
    let naturalChanged = false;
    natural.addEventListener('change', () => { naturalChanged = true; });
    bindNumericInput(fog, { precision: 3, onCommit: onPreview });
    container.oninput = onPreview;
    container.onchange = onPreview;

    return {
        get(report = true) {
            const density = readNumericInput(fog, { report, precision: 3 });
            if (!Number.isFinite(density)) return null;
            return { fog_density: density, water_reflections: reflections.checked,
                natural_detail: naturalChanged ? natural.checked : savedNatural };
        },
    };
}
