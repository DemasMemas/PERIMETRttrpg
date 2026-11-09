const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');
const start = source.indexOf('function equipmentFigureAppearance(');
const end = source.indexOf('\nfunction renderEquipmentLoadout(', start);
assert.ok(start >= 0 && end > start);
const context = vm.createContext({});
vm.runInContext(source.slice(start, end), context);
const appearance = (equipment, armorTemplates = [], helmetTemplates = []) =>
    context.equipmentFigureAppearance(equipment, armorTemplates, helmetTemplates);
const figure = (equipment, armorTemplates = [], helmetTemplates = []) =>
    context.renderEquipmentFigure(equipment, armorTemplates, helmetTemplates);

test('armor figure follows the template class and exoskeleton flag', () => {
    const armor = { templateId: 1, name: 'Пользовательская броня' };
    for (const [itemClass, expected] of [
        ['Легкая', 'light'], ['Средняя', 'medium'], ['Тяжелая', 'heavy'], ['Аномальная', 'anomalous'],
    ]) {
        assert.equal(appearance({ armor }, [{ id: 1, item_class: itemClass }]).armorKind, expected);
    }
    assert.equal(appearance({ armor: { ...armor, isExoskeleton: true } }).armorKind, 'exo');
    assert.equal(appearance({ armor: { templateId: 2, name: 'Кожаная куртка' } }).armorKind, 'light');
    assert.equal(appearance({ armor: { templateId: 4, name: 'Броня путника' } }).armorKind, 'medium');
    assert.equal(appearance({ armor: { templateId: 4, name: 'Броня путника' } }, [{ id: 4, item_class: 'Средняя' }]).armorKind, 'medium');
    assert.equal(appearance({ armor: { templateId: 4, name: 'Броня путника' } }, [{ id: 4, item_class: 'Легкая' }]).armorKind, 'medium');
    assert.equal(appearance({ armor: { templateId: 3, name: 'Комбинезон Купол' } }).armorKind, 'anomalous');
});

test('helmet figure distinguishes open, visor and gas-mask helmets', () => {
    const helmet = { templateId: 1, name: 'Шлем' };
    assert.equal(appearance({ helmet }, [], [{ id: 1, attributes: { protection_zones: ['crown', 'back'] } }]).helmetKind, 'open');
    assert.equal(appearance({ helmet }, [], [{ id: 1, attributes: { protection_zones: ['crown', 'face'] } }]).helmetKind, 'visor');
    assert.equal(appearance({ helmet }, [], [{ id: 1, attributes: { requires_filter: true } }]).helmetKind, 'sealed');
    assert.equal(appearance({ helmet: { ...helmet, installedModules: [{ slotType: 'visor' }] } }).helmetKind, 'visor');
    assert.equal(appearance({ armor: { templateId: 2, name: 'Комбинезон Купол' }, helmet: { ...helmet, integratedWithArmor: true } }).helmetKind, 'anomalous');
});

test('figure draws generic slot layers but never weapons', () => {
    const empty = figure({});
    assert.match(empty, /data-armor="none"/);
    const equipped = figure({
        gasMask: { templateId: 1, name: 'Любой противогаз' },
        backpack: { templateId: 2 }, detector: { templateId: 3 },
        gloves: { templateId: 4 }, ring: { templateId: 5 },
    });
    for (const flag of ['mask', 'backpack', 'detector', 'gloves', 'ring']) {
        assert.match(equipped, new RegExp(`data-${flag}="true"`));
    }
    for (const flag of ['necklace', 'earrings', 'bracelet1', 'bracelet2']) {
        assert.match(equipped, new RegExp(`data-${flag}="false"`));
    }
    assert.match(equipped, /loadout-art-hands/);
    assert.match(equipped, /loadout-art-gloves/);
    assert.doesNotMatch(equipped, /data-ranged|data-melee|loadout-art-ranged|loadout-art-melee/);
    assert.doesNotMatch(equipped, /Любой противогаз/);
    assert.match(equipped, /loadout-art-mask[^>]*>.*loadout-art-filter/s);
    assert.match(equipped, /loadout-art-helmet-anomalous[^>]*>.*loadout-art-visor-frame/s);
});

test('balaclavas have a fabric opening and the half-mask is in front of the headwear', () => {
    const markup = figure({
        helmet: {templateId: 1, name: 'Балаклава Призрак', attributes: {protection_zones: ['face']}},
        gasMask: {templateId: 2, name: 'Противогаз Респиратор'},
    });
    assert.match(markup, /data-helmet="balaclava"[^>]*data-mask-kind="respirator"/);
    assert.match(markup, /loadout-art-helmet-balaclava"><path fill-rule="evenodd"/);
    const respirator = markup.match(/<g class="loadout-art-mask loadout-art-respirator">([\s\S]*?)<\/g>/)?.[1];
    assert.ok(respirator);
    assert.doesNotMatch(respirator, /loadout-art-lens/);
    assert.ok(markup.indexOf('loadout-art-mask loadout-art-respirator') > markup.indexOf('loadout-art-helmet-anomalous'));
});

test('anomalous suit connects its glass helmet to a sealed collar', () => {
    const markup = figure({
        armor: { templateId: 2, name: 'Комбинезон Купол' },
        helmet: { templateId: 'integrated:2', integratedWithArmor: true },
    });
    const styles = fs.readFileSync(path.join(__dirname, '../app/static/style.css'), 'utf8');
    assert.match(markup, /data-armor="anomalous"/);
    assert.match(markup, /data-helmet="anomalous"/);
    assert.match(markup, /loadout-art-seal/);
    assert.match(markup, /loadout-art-face/);
    assert.match(markup, /loadout-art-glass-highlight/);
    const helmetMarkup = markup.match(/<g class="loadout-art-helmet loadout-art-helmet-anomalous">([\s\S]*?)<\/g>/)?.[1];
    assert.ok(helmetMarkup);
    assert.doesNotMatch(helmetMarkup, /<circle\b/);
    assert.match(helmetMarkup, /<ellipse[^>]*rx="24"[^>]*ry="28"[^>]*class="loadout-art-visor-frame"/);
    assert.match(helmetMarkup, /<ellipse[^>]*rx="21"[^>]*ry="25"[^>]*class="loadout-art-visor-glass"/);
    assert.match(styles, /\.loadout-art-helmet-anomalous\s*\{[^}]*fill:\s*rgba\([^)]*,\s*\.10\)/);
    assert.match(styles, /\.loadout-art-visor-frame\s*\{[^}]*stroke:\s*var\(--anomalous-suit\)/);
    assert.match(styles, /\.loadout-art-visor-glass\s*\{[^}]*fill:\s*rgba\([^)]*,\s*\.05\)/);
});
