const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const read = file => fs.readFileSync(path.join(__dirname, '..', file), 'utf8');

function harness() {
    const elements = new Map();
    const numbers = [];
    const document = {
        activeElement: null,
        getElementById: id => elements.get(id),
        querySelectorAll: () => numbers,
    };
    class Input {
        constructor(attributes) {
            Object.assign(this, attributes);
            this.listeners = {};
            this.value = attributes.value || '';
        }
        get value() { return this.currentValue; }
        set value(value) {
            let parsed = Number(value);
            if (this.type === 'range' && this.step && this.step !== 'any' && Number.isFinite(parsed)) {
                const step = Number(this.step);
                parsed = Number((Number(this.min) + Math.round((parsed - Number(this.min)) / step) * step).toFixed(10));
                this.currentValue = String(Math.max(Number(this.min), Math.min(Number(this.max), parsed)));
            } else this.currentValue = String(value);
        }
        addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
        dispatchEvent(event) { this.listeners[event.type]?.forEach(callback => callback(event)); }
    }
    const html = read('app/templates/lobby.html');
    for (const tag of html.matchAll(/<input\b[^>]*>/g)) {
        const attrs = Object.fromEntries([...tag[0].matchAll(/([\w-]+)="([^"]*)"/g)].map(m => [m[1], m[2]]));
        if (!attrs.id) continue;
        const input = new Input({ ...attrs, dataset: {
            worldNumber: attrs['data-world-number'], offset: attrs['data-offset'],
        } });
        elements.set(input.id, input);
        if (attrs['data-world-number']) numbers.push(input);
    }
    const context = vm.createContext({ document, Event: class { constructor(type) { this.type = type; } } });
    vm.runInContext(read('app/static/js/worldNumericInputs.js').replaceAll('export function', 'function'), context);
    context.initWorldNumericInputs();
    const fire = (input, type = 'input') => input.dispatchEvent({ type });
    const type = (id, value) => {
        const input = elements.get(`${id}-number`);
        document.activeElement = input;
        input.value = value;
        fire(input);
        return input;
    };
    return { context, elements, numbers, document, type, fire };
}

test('every world slider has a bounded number input and retains exact fractions', () => {
    const { context, numbers, elements, type, fire, document } = harness();
    assert.equal(numbers.length, 14);
    for (const number of numbers) {
        const range = elements.get(number.dataset.worldNumber);
        assert.equal(range.type, 'range');
        const offset = Number(number.dataset.offset || 0);
        assert.equal(Number(range.min) + offset, Number(number.min));
        if (range.id.endsWith('rotation')) {
            assert.equal(Number(range.max), 359);
            assert.equal(Number(number.max), 359.99999999999994);
        } else assert.equal(Number(range.max) + offset, Number(number.max));
        if (offset || number.step === '1') continue;
        const value = String(Number(range.min) + 0.123456);
        type(range.id, value);
        assert.equal(context.getWorldNumericValue(range.id), Number(value));
        assert.equal(number.value, value);
        document.activeElement = range;
        range.value = String(Number(range.max) - 0.234567);
        fire(range);
        assert.equal(Number(number.value), Number(range.value));
    }
});

test('rotation number fields retain 359.5 through change and blur while sliders stay bounded', () => {
    const { context, elements, type, fire } = harness();
    for (const id of ['world-object-rotation', 'world-selection-rotation', 'object-rotation']) {
        const number = type(id, '359.5');
        fire(number, 'change');
        fire(number, 'blur');
        assert.equal(context.getWorldNumericValue(id), 359.5);
        assert.equal(Number(number.value), 359.5);
        assert.equal(Number(elements.get(id).max), 359);
    }
});

test('drafts stay editable; empty/nonfinite input restores and bounds clamp only on commit', () => {
    const { context, elements, type, fire } = harness();
    const range = elements.get('tile-height');
    for (const draft of ['', '-', '1e', 'Infinity', 'NaN']) {
        const number = type('tile-height', draft);
        assert.equal(number.value, draft);
        assert.equal(Number(range.value), 1);
        fire(number, 'blur');
        assert.equal(Number(number.value), 1);
    }
    const number = type('tile-height', '1.2300');
    assert.equal(number.value, '1.2300');
    assert.equal(context.getWorldNumericValue('tile-height'), 1.23);
    type('tile-height', '25');
    assert.equal(context.getWorldNumericValue('tile-height'), 1.23);
    fire(number, 'change');
    assert.equal(Number(range.value), 3);
    type('tile-height', '-2');
    fire(number, 'blur');
    assert.equal(Number(range.value), 0.5);
});

test('height sliders step by 0.1 and radiation by 0.5 while exact entry stays precise', () => {
    const { context, elements, document, type, fire } = harness();
    for (const [id, step] of [
        ['tile-height', 0.1], ['tile-edit-height', 0.1],
        ['world-brush-radiation', 0.5], ['tile-edit-radiation', 0.5],
    ]) {
        const range = elements.get(id);
        const number = elements.get(`${id}-number`);
        assert.equal(Number(number.step), step);
        fire(range, 'pointerdown');
        assert.equal(Number(range.step), step);
        document.activeElement = range;
        range.value = '5.55104166666667';
        if (step === 0.1) range.value = '1.55104166666667';
        fire(range);
        assert.equal(Number(range.value), step === 0.1 ? 1.6 : 5.5);
        assert.equal(number.value, range.value);
        fire(range, 'pointerup');
        type(id, '1.234567');
        assert.equal(context.getWorldNumericValue(id), 1.234567);
        assert.equal(Number(range.step), step);
    }
});

test('brush sizes 1 through 5 paint the agreed squares across chunk boundaries', () => {
    const { context, elements, type } = harness();
    const source = read('app/static/js/mapEdit.js');
    const start = source.indexOf('export function applyBrush(');
    const end = source.indexOf('\nexport async function handleTileUpdate(', start);
    const painted = [];
    Object.assign(context, {
        AppState: { isGM: true }, window: { MAP_CHUNKS_WIDTH: 2, MAP_CHUNKS_HEIGHT: 2 },
        updateTileInChunk(...args) { painted.push(args); }, scheduleBatchUpdate() {},
    });
    vm.runInContext('const CHUNK_SIZE = 32; let pendingTileUpdates = [];\n'
        + source.slice(start, end).replace('export function', 'function'), context);
    for (let size = 1; size <= 5; size++) {
        painted.length = 0;
        type('brush-radius', String(size));
        const radius = Number(elements.get('brush-radius').value);
        assert.equal(radius, size - 1);
        context.applyBrush({ chunkX: 0, chunkY: 0, tileX: 31, tileY: 31 }, { height: 1.234567 }, radius);
        assert.equal(painted.length, (2 * size - 1) ** 2);
        assert.ok(painted.every(call => call[2] >= 0 && call[2] < 32 && call[3] >= 0 && call[3] < 32));
        assert.ok(painted.every(call => call[4].height === 1.234567));
    }
});

test('fractional brush size waits for commit and repeated initialization does not duplicate events', () => {
    const { context, elements, type, fire } = harness();
    const range = elements.get('brush-radius');
    let changes = 0;
    range.addEventListener('input', () => changes++);
    context.initWorldNumericInputs();
    const number = type('brush-radius', '2.7');
    assert.equal(Number(range.value), 0);
    fire(number, 'blur');
    assert.equal(Number(number.value), 3);
    assert.equal(Number(range.value), 2);
    assert.equal(changes, 1);
    type('brush-radius', '99');
    fire(number, 'change');
    fire(number, 'blur');
    assert.equal(Number(number.value), 5);
    assert.equal(Number(range.value), 4);
    assert.equal(changes, 2);
});

test('modal object saving sends fractional rotation and offsets without truncation', async () => {
    const { context, elements, type } = harness();
    type('object-offset-x', '-0.123456');
    type('object-offset-z', '0.234567');
    type('object-scale', '1.234567');
    type('object-rotation', '123.456789');
    elements.set('object-type-select', { value: 'tree' });
    const sent = [];
    Object.assign(context, {
        currentEditTile: { chunkX: 0, chunkY: 0, tileX: 1, tileY: 1, tileData: { objects: [{ type: 'house' }] } },
        chunksMap: new Map(), getAnomalyTypeFromSelect() { return null; },
        handleTileUpdate(...args) { sent.push(args); }, removePreviewObject() {},
    });
    const source = read('app/static/js/mapEdit.js');
    const start = source.indexOf('export async function addObjectToTile(');
    const end = source.indexOf('\nexport async function saveExistingWorldObject(', start);
    vm.runInContext(source.slice(start, end).replace('export async function', 'async function'), context);
    await context.addObjectToTile();
    const objects = sent[0][4].objects;
    assert.equal(objects[0].type, 'house');
    assert.equal(objects[1].x, -0.123456);
    assert.equal(objects[1].z, 0.234567);
    assert.equal(objects[1].scale, 1.234567);
    assert.equal(objects[1].rotation, 123.456789);
});

test('restored panel fields bind again and Enter commits the value', () => {
    const { context, elements, numbers, type } = harness();
    const oldNumber = elements.get('tile-height-number');
    const oldRange = elements.get('tile-height');
    const range = new oldRange.constructor(oldRange);
    const number = new oldNumber.constructor(oldNumber);
    elements.set(range.id, range);
    elements.set(number.id, number);
    numbers[numbers.indexOf(oldNumber)] = number;
    context.initWorldNumericInputs();
    type('tile-height', '99');
    let prevented = false;
    number.dispatchEvent({ type: 'keydown', key: 'Enter', preventDefault() { prevented = true; } });
    assert.equal(prevented, true);
    assert.equal(range.value, '3');
    assert.equal(number.value, '3');
    assert.match(read('app/static/js/main.js'), /innerHTML = window\._originalToolsContent;\s*initWorldNumericInputs\(\)/);
});
