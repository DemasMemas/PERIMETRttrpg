export const DEFAULT_LOCATION_LIGHTING = Object.freeze({
    mode: 'world',
    ambient_color: '#b9c4d0',
    ambient_intensity: 0.55,
    directional_color: '#fff0d5',
    directional_intensity: 1,
    directional_azimuth: 45,
    directional_elevation: 50,
    background_color: '#283642',
    fog_density: 0,
    water_reflections: false,
    natural_detail: null,
    local_lights: [],
    dark_tiles: [],
});

// Render slots are independent of the number of editable/saved lamps.
export function sameLocationLightingSettings(left, right) {
    if (!left || !right) return false;
    const a = { ...DEFAULT_LOCATION_LIGHTING, ...left };
    const b = { ...DEFAULT_LOCATION_LIGHTING, ...right };
    const numberEqual = (first, second) => Number.isFinite(Number(first))
        && Number.isFinite(Number(second)) && Math.abs(Number(first) - Number(second)) < 0.00051;
    const colorEqual = (first, second) => String(first).toLowerCase() === String(second).toLowerCase();
    if (a.mode !== b.mode || a.water_reflections !== b.water_reflections
        || a.natural_detail !== b.natural_detail) return false;
    for (const key of ['ambient_color', 'directional_color', 'background_color']) {
        if (!colorEqual(a[key], b[key])) return false;
    }
    for (const key of ['ambient_intensity', 'directional_intensity',
        'directional_azimuth', 'directional_elevation', 'fog_density']) {
        if (!numberEqual(a[key], b[key])) return false;
    }
    if (a.dark_tiles.length !== b.dark_tiles.length || a.local_lights.length !== b.local_lights.length) return false;
    for (let index = 0; index < a.dark_tiles.length; index++) {
        if (a.dark_tiles[index].x !== b.dark_tiles[index].x
            || a.dark_tiles[index].z !== b.dark_tiles[index].z) return false;
    }
    for (let index = 0; index < a.local_lights.length; index++) {
        const source = a.local_lights[index];
        const saved = b.local_lights[index];
        if ((source.kind || 'point') !== (saved.kind || 'point')
            || Number(source.level || 0) !== Number(saved.level || 0)
            || Boolean(source.gm_hidden) !== Boolean(saved.gm_hidden)
            || !colorEqual(source.color, saved.color)) return false;
        for (const key of ['x', 'z', 'height', 'intensity', 'distance', 'azimuth']) {
            if (!numberEqual(source[key] ?? 0, saved[key] ?? 0)) return false;
        }
        if (Boolean(source.dimensions) !== Boolean(saved.dimensions)) return false;
        if (source.dimensions) {
            for (const key of ['width', 'depth', 'height']) {
                if (!numberEqual(source.dimensions[key], saved.dimensions[key])) return false;
            }
        }
    }
    return true;
}

export function createLocationLightingEchoTracker() {
    let entries = [];
    let generation = 0;
    let lastApplied = 0;
    return {
        begin(locationId, settings) {
            const entry = { locationId: String(locationId),
                settings: JSON.parse(JSON.stringify(settings)), generation: ++generation, applied: false };
            entries.push(entry);
            if (entries.length > 16) entries.shift();
            return entry;
        },
        socket(locationId, settings) {
            const entry = [...entries].reverse().find(item => item.locationId === String(locationId)
                && sameLocationLightingSettings(item.settings, settings));
            if (!entry) return true;
            if (entry.applied || entry.generation < lastApplied) return false;
            entry.applied = true;
            lastApplied = entry.generation;
            return true;
        },
        rest(entry) {
            if (!entries.includes(entry) || entry.applied || entry.generation < lastApplied) return false;
            entry.applied = true;
            lastApplied = entry.generation;
            return true;
        },
        fail(entry) { entries = entries.filter(item => item !== entry); },
        reset() { entries = []; generation = 0; lastApplied = 0; },
    };
}

const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
const smoothstep = (start, end, value) => {
    const t = clamp((value - start) / (end - start), 0, 1);
    return t * t * (3 - 2 * t);
};
const mixColor = (dark, light, amount) => {
    const from = parseInt(dark.slice(1), 16);
    const to = parseInt(light.slice(1), 16);
    const channels = [16, 8, 0].map(shift => Math.round(
        ((from >> shift) & 255) * (1 - amount) + ((to >> shift) & 255) * amount,
    ));
    return `#${channels.map(channel => channel.toString(16).padStart(2, '0')).join('')}`;
};

export function resolveLocationLighting(settings, minutes = 480, clarity = 1) {
    const config = { ...DEFAULT_LOCATION_LIGHTING, ...(settings || {}) };
    if (config.mode === 'manual') return config;
    const time = ((Number(minutes) || 0) % 1440 + 1440) % 1440;
    const day = smoothstep(300, 480, time) * (1 - smoothstep(1080, 1260, time));
    const clouds = clamp(Number(clarity) || 0, 0, 1);
    return {
        ...config,
        ambient_color: mixColor('#9eb5d3', '#e6e0d4', day),
        ambient_intensity: 0.08 + day * (0.4 + 0.15 * clouds),
        directional_color: mixColor('#a6b9d8', '#ffe8c2', day),
        directional_intensity: 0.04 + day * (0.65 + 0.35 * clouds),
        background_color: mixColor('#101b2b', '#8ba3ae', day),
    };
}

export function resolveLocationVisualPreset(profile, preset = 'current', minutes = 480) {
    if (preset !== 'natural_daylight' || profile.mode !== 'world') return profile;
    const time = ((Number(minutes) || 0) % 1440 + 1440) % 1440;
    const day = smoothstep(300, 480, time) * (1 - smoothstep(1080, 1260, time));
    if (!day) return profile;
    return {
        ...profile,
        ambient_color: mixColor(profile.ambient_color, '#cbd5d0', day * 0.55),
        ambient_intensity: Number(profile.ambient_intensity) + day * 0.15,
        directional_color: mixColor(profile.directional_color, '#f5e7d3', day * 0.35),
        directional_intensity: Math.max(0, Number(profile.directional_intensity) - day * 0.12),
    };
}
