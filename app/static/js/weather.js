import * as THREE from 'three';
import { scene, directionalLight, ambientLight, fillLight, waterMat, setSkyConditions, setCelestialTime, WORLD_HORIZON_HAZE } from './lobby3d.js';

let rainParticles = null;
let rainSound = null;
let emissionSound = null;
let rainSpeeds = [];
let weatherAudioUnlocked = false;
let desiredRainAudio = { enabled: false, volume: 0 };
let desiredEmissionAudio = { enabled: false, volume: 0 };

const RAIN_VOLUME_FACTOR = 0.5;
const EMISSION_VOLUME_FACTOR = 0.05; // ещё тише

const DEFAULT_FOG_INTENSITY = 0.5;
const DEFAULT_RAIN_INTENSITY = 0.5;
const DEFAULT_CLOUD_CLARITY = 0.5;
const DEFAULT_EMISSION_INTENSITY = 0.5;
let worldTimeMinutes = 480;
let currentClouds = { enabled: false, intensity: DEFAULT_CLOUD_CLARITY };
let currentEmission = { enabled: false, intensity: DEFAULT_EMISSION_INTENSITY };

export function daylightAtMinutes(minutes) {
    const normalized = ((Number(minutes) % 1440) + 1440) % 1440;
    const smoothstep = (start, end) => {
        const t = Math.max(0, Math.min(1, (normalized - start) / (end - start)));
        return t * t * (3 - 2 * t);
    };
    return smoothstep(5 * 60, 8 * 60) * (1 - smoothstep(18 * 60, 21 * 60));
}

export function starlightAtMinutes(minutes) {
    const normalized = ((Number(minutes) % 1440) + 1440) % 1440;
    const smoothstep = (start, end) => {
        const t = Math.max(0, Math.min(1, (normalized - start) / (end - start)));
        return t * t * (3 - 2 * t);
    };
    if (normalized >= 21 * 60) return smoothstep(21 * 60, 22.5 * 60);
    if (normalized < 6 * 60) return 1 - smoothstep(4.5 * 60, 6 * 60);
    return 0;
}

function applyWorldLighting() {
    const daylight = daylightAtMinutes(worldTimeMinutes);
    const starlight = starlightAtMinutes(worldTimeMinutes);
    const clarity = currentClouds.enabled
        ? Math.max(0, Math.min(1, Number(currentClouds.intensity) || 0)) : 1;
    const directNight = 0.07 + 0.06 * clarity;
    const ambientNight = 0.08 + 0.035 * clarity;
    const fillNight = 0.035 + 0.035 * clarity;
    directionalLight.intensity = directNight + (0.43 + 0.92 * clarity - directNight) * daylight;
    ambientLight.intensity = ambientNight + (0.36 + 0.08 * clarity - ambientNight) * daylight;
    fillLight.intensity = fillNight + (0.17 + 0.15 * clarity - fillNight) * daylight;
    directionalLight.color.lerpColors(
        new THREE.Color(0xaec6e4),
        new THREE.Color(0xffe8c2),
        daylight,
    );
    ambientLight.color.setHex(0xffffff);
    if (currentEmission.enabled) {
        directionalLight.color.lerp(new THREE.Color(0xff6666), currentEmission.intensity);
        ambientLight.color.lerp(new THREE.Color(0x882222), currentEmission.intensity);
    }
    setSkyConditions(daylight, clarity, starlight, currentEmission.enabled ? currentEmission.intensity : 0);
    setCelestialTime(worldTimeMinutes);
}

export function applyWorldTime(minutes) {
    const numericMinutes = Number(minutes);
    worldTimeMinutes = Number.isFinite(numericMinutes) ? numericMinutes : 480;
    applyWorldLighting();
}

function setLoopingSound(sound, state) {
    if (!sound) return;
    sound.volume = state.volume;
    if (!state.enabled) {
        sound.pause();
        return;
    }
    if (weatherAudioUnlocked && sound.paused) {
        const request = sound.play();
        request?.catch?.((error) => {
            weatherAudioUnlocked = false;
            console.warn('Weather audio could not be played.', error);
        });
    }
}

function syncWeatherAudio() {
    setLoopingSound(rainSound, desiredRainAudio);
    setLoopingSound(emissionSound, desiredEmissionAudio);
}

function unlockWeatherAudio() {
    if (weatherAudioUnlocked) return;
    weatherAudioUnlocked = true;
    syncWeatherAudio();
}

export function initWeather() {
    if (typeof window.Audio !== 'function') {
        console.warn('Browser audio is unavailable; weather audio is disabled.');
        return;
    }
    rainSound = new window.Audio('/static/audio/rain.mp3');
    emissionSound = new window.Audio('/static/audio/emission.mp3');
    rainSound.loop = true;
    emissionSound.loop = true;
    rainSound.volume = 0;
    emissionSound.volume = 0;
    ['pointerdown', 'keydown', 'touchstart'].forEach((eventName) => {
        window.addEventListener(eventName, unlockWeatherAudio, { capture: true, passive: true });
    });
    // Пытаемся начать автоматически; если браузер запрещает autoplay,
    // следующий пользовательский ввод повторит попытку без отдельной кнопки.
    unlockWeatherAudio();
}

export function applyWeather(settings) {
    settings = settings || {};
    const fog = {
        enabled: settings.fog?.enabled || false,
        intensity: settings.fog?.intensity !== undefined ? settings.fog.intensity : DEFAULT_FOG_INTENSITY
    };
    const rain = {
        enabled: settings.rain?.enabled || false,
        intensity: settings.rain?.intensity !== undefined ? settings.rain.intensity : DEFAULT_RAIN_INTENSITY
    };
    // The stored `sun` field is retained for existing lobbies; it now controls cloud clarity.
    const clouds = {
        enabled: settings.sun?.enabled || false,
        intensity: settings.sun?.intensity !== undefined ? settings.sun.intensity : DEFAULT_CLOUD_CLARITY
    };
    const emission = {
        enabled: settings.emission?.enabled || false,
        intensity: settings.emission?.intensity !== undefined ? settings.emission.intensity : DEFAULT_EMISSION_INTENSITY
    };
    currentClouds = clouds;
    currentEmission = emission;

    // Туман
    scene.fog.density = Math.max(WORLD_HORIZON_HAZE, fog.enabled ? 0.02 * fog.intensity : 0);

    // Вода всегда непрозрачная
    if (waterMat) {
        waterMat.transparent = false;
        waterMat.opacity = 1.0;
    }

    // Дождь
    if (rain.enabled) {
        if (!rainParticles) {
            createRainParticles(rain.intensity);
        } else {
            updateRainIntensity(rain.intensity);
        }
        rainParticles.visible = true;
        desiredRainAudio = { enabled: true, volume: rain.intensity * RAIN_VOLUME_FACTOR };
    } else {
        if (rainParticles) rainParticles.visible = false;
        desiredRainAudio = { enabled: false, volume: 0 };
    }

    applyWorldLighting();

    // Выброс
    if (emission.enabled) {
        const intensity = emission.intensity;
        desiredEmissionAudio = { enabled: true, volume: intensity * EMISSION_VOLUME_FACTOR };
    } else {
        desiredEmissionAudio = { enabled: false, volume: 0 };
    }
    syncWeatherAudio();
}

function createRainParticles(intensity = 0.5) {
    const count = 75000;
    const geometry = new THREE.BufferGeometry();
    const positions = new Float32Array(count * 3);
    rainSpeeds = new Float32Array(count);

    for (let i = 0; i < count; i++) {
        positions[i*3] = (Math.random() - 0.5) * 2000;
        positions[i*3+1] = Math.random() * 500;
        positions[i*3+2] = (Math.random() - 0.5) * 2000;
        rainSpeeds[i] = 50 + Math.random() * 100;
    }
    geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3));

    // Маленькая текстура капли
    const canvas = document.createElement('canvas');
    canvas.width = 4;
    canvas.height = 8;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = 'white';
    ctx.beginPath();
    ctx.ellipse(2, 4, 1, 3, 0, 0, Math.PI*2);
    ctx.fill();
    const texture = new THREE.CanvasTexture(canvas);

    const material = new THREE.PointsMaterial({
        color: 0xaaaaaa,
        size: 0.3 + intensity,
        map: texture,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false
    });

    rainParticles = new THREE.Points(geometry, material);
    scene.add(rainParticles);
}

function updateRainIntensity(intensity) {
    if (!rainParticles) return;
    rainParticles.material.size = 0.3 + intensity;
}

export function updateRain(deltaTime) {
    if (!rainParticles || !rainParticles.visible) return;

    const positions = rainParticles.geometry.attributes.position.array;
    const count = positions.length / 3;

    for (let i = 0; i < count; i++) {
        positions[i*3+1] -= rainSpeeds[i] * deltaTime;
        if (positions[i*3+1] < -50) {
            positions[i*3+1] = 500 + Math.random() * 100;
            positions[i*3] = (Math.random() - 0.5) * 2000;
            positions[i*3+2] = (Math.random() - 0.5) * 2000;
        }
    }
    rainParticles.geometry.attributes.position.needsUpdate = true;
}
