import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import vm from 'node:vm';

const context = vm.createContext({});
const files = ['locationImportedAssets.js', 'locationAssetCatalog.js', 'locationAssetVariants.js'];
const modules = new Map();
for (const file of files) {
    const source = await readFile(new URL(`../app/static/js/${file}`, import.meta.url), 'utf8');
    modules.set(file, new vm.SourceTextModule(source, { context }));
}
const variants = modules.get('locationAssetVariants.js');
await variants.link(specifier => modules.get(specifier.replace('./', '')));
await variants.evaluate();
const { placementForLocationAsset, visualAssetForLocationObject, locationObjectLabel, vegetationColorValues } = variants.namespace;
const { findLocationAsset, locationAssetCategory, LOCATION_ASSETS } = modules.get('locationAssetCatalog.js').namespace;

test('весь каталог показывает игровые названия, включая сохранённые технические имена', () => {
    const assets = modules.get('locationAssetCatalog.js').namespace.LOCATION_ASSETS;
    for (const asset of assets) {
        assert.doesNotMatch(asset.label, /[a-z]|тест|финальный/i, asset.id);
        assert.doesNotMatch(asset.group, /[a-z]|ассет/i, asset.id);
        const placement = placementForLocationAsset(asset);
        assert.equal(locationObjectLabel({type:placement.type,name:asset.id,
            properties:{visual_asset:placement.visualAsset}}), asset.label);
    }
    for (const [type,visual,name,expected] of [
        ['barrel','art_rusty_barrel','Ржавая бочка PBR','Ржавая бочка'],
        ['box','art_wooden_crate','Складской ящик 12','Старый деревянный ящик'],
        ['fence','art_chainlink_fence','Секция ограды 0','Сетчатый забор'],
        ['floor','art_dirty_earth','Грунтовая площадка 3','Грязная земля'],
        ['wall','art_worn_plaster','Северная стена дома','Северная стена дома'],
    ]) assert.equal(locationObjectLabel({type,name,properties:{visual_asset:visual}}),expected);
});

test('replacement furniture keeps both generations of saved IDs and original dimensions', () => {
    for (const [canonical, alias, model, oldSize, newSize] of [
        ['table', 'household_dining_table', 'dining_table', [1.4,.8,1], [1.25,.75,.76]],
        ['chair', 'household_wood_chair', 'wood_chair', [.55,.55,1], [.45,.48,.88]],
    ]) {
        const original = findLocationAsset(canonical), replacement = findLocationAsset(alias);
        assert.equal(original.visual.family, 'household');
        assert.equal(original.visual.modelId, model);
        assert.deepEqual(Array.from(original.dimensions), oldSize);
        assert.deepEqual(Array.from(replacement.dimensions), newSize);
        assert.equal(replacement.hidden, true);
        for (const id of [canonical, alias]) {
            assert.equal(visualAssetForLocationObject(id, {}).id, id);
            assert.equal(placementForLocationAsset(findLocationAsset(id)).type, id);
        }
    }
});

test('plumbing is a separate category and leaves the kitchen sink available', () => {
    const assets = modules.get('locationAssetCatalog.js').namespace.LOCATION_ASSETS;
    assert.ok(assets.filter(a => a.group === 'Сантехника' && !a.hidden).length >= 3);
    for (const id of ['household_washbasin', 'household_toilet', 'household_bathtub']) {
        assert.equal(findLocationAsset(id).group, 'Сантехника');
        assert.equal(findLocationAsset(id).visual.pivot, 'bottom-center');
    }
    assert.equal(findLocationAsset('household_sink').group, 'Кухня и быт');
});

test('artistic variants keep the existing gameplay type and stable visual id', () => {
    for (const [id, type] of [
        ['art_rusty_barrel', 'barrel'],
        ['art_wooden_crate', 'box'],
        ['art_chainlink_fence', 'fence'],
        ['art_worn_plaster', 'wall'],
        ['art_dirty_earth', 'floor'],
    ]) {
        const asset = findLocationAsset(id);
        assert.ok(asset, id);
        const placement = placementForLocationAsset(asset);
        assert.equal(placement.type, type);
        assert.equal(placement.visualAsset, id);
        assert.equal(visualAssetForLocationObject(type, { visual_asset: id }).id, id);
    }
});

test('unknown or mismatched variant falls back to the original asset', () => {
    assert.equal(visualAssetForLocationObject('box', { visual_asset: 'art_rusty_barrel' }).id, 'box');
    assert.equal(visualAssetForLocationObject('box', { visual_asset: 'missing' }).id, 'box');
    assert.equal(visualAssetForLocationObject('box', {}).id, 'box');
    assert.equal(placementForLocationAsset(findLocationAsset('box')).visualAsset, null);
});

test('Природа показывает три состояния одного куста и один обычный камень, сохраняя старые ID', () => {
    const visible = LOCATION_ASSETS.filter(asset => !asset.hidden);
    const shrubs = visible.filter(asset => asset.label.startsWith('Куст ·'));
    assert.equal(shrubs.length, 3);
    assert.equal(new Set(shrubs.map(asset => asset.visual.modelNode)).size, 3);
    for (const asset of shrubs) {
        assert.equal(locationAssetCategory(asset).subgroup, 'Мелкая растительность');
        assert.equal(asset.passable, true);
    }
    for (const id of ['swamp_shrub_02_1', 'swamp_shrub_02_2', 'swamp_shrub_02_3', 'swamp_shrub_02_4']) {
        assert.equal(visualAssetForLocationObject(id, {}).hidden, true);
    }
    assert.equal(visible.filter(asset => asset.label === 'Камень').length, 1);
    assert.equal(locationAssetCategory(findLocationAsset('rock')).subgroup, 'Камни');
    assert.equal(findLocationAsset('rock').visual.modelNode, findLocationAsset('swamp_rock_09_1').visual.modelNode);
    assert.equal(locationAssetCategory(findLocationAsset('nature_birch_green')).subgroup, 'Деревья');
    assert.equal(locationObjectLabel({ type: 'swamp_wheat_wild_1', name: 'Сухая трава' }), 'Дикий злак');
});

test('Оттенки листвы и веток сохраняются отдельно и читают старые сохранения', () => {
    const shrub = findLocationAsset('nature_bush_green');
    const defaults = { color: shrub.color };
    assert.deepEqual({ ...vegetationColorValues(shrub, defaults) },
        { wood_color: '#ffffff', foliage_color: '#ffffff' });
    assert.deepEqual({ ...vegetationColorValues(shrub, { color: '#112233', wood_color: '#665544' }) },
        { wood_color: '#665544', foliage_color: '#112233' });
    assert.deepEqual({ ...vegetationColorValues(shrub, { color: '#112233', foliage_color: '#ffffff' }) },
        { wood_color: '#ffffff', foliage_color: '#ffffff' });
    const leafless = findLocationAsset('nature_bush_dead');
    assert.deepEqual({ ...vegetationColorValues(leafless, { foliage_color: '#335522' }) }, { wood_color: '#ffffff' });
    const moss = findLocationAsset('swamp_rock_moss_set_02_1');
    assert.deepEqual({ ...vegetationColorValues(moss, { foliage_color: '#667744' }) }, { foliage_color: '#667744' });
});

test('Замшелые камни делят один комплект карт, а оголённые корни ставятся над уровнем грунта', async () => {
    const stones = LOCATION_ASSETS.filter(asset => asset.id.startsWith('swamp_rock_moss_set_02_'));
    assert.equal(stones.length, 7);
    assert.equal(new Set(stones.map(asset => asset.visual.modelPath)).size, 1);
    const model = await readFile(new URL(`../app${stones[0].visual.modelPath}`, import.meta.url));
    const data = JSON.parse(model.toString('utf8', 20, 20 + model.readUInt32LE(12)));
    for (const stone of stones) {
        assert.ok(data.nodes.some(node => node.name === stone.visual.modelNode));
        assert.equal(stone.colorChannels.foliage_color, 'Оттенок мха');
    }
    assert.ok(data.materials[0].normalTexture && data.materials[0].pbrMetallicRoughness.metallicRoughnessTexture);
    for (const id of ['swamp_pine_roots_1', 'swamp_pine_roots_2']) {
        assert.ok(findLocationAsset(id).visual.groundClearance > 0);
    }
});

test('Разрушения сохраняют обычные игровые типы, собственные размеры и независимые свойства',()=>{
    const definitions=[['ruin_wall_low','wall','structural_damage',false],['ruin_wall_high','wall','structural_damage',false],
        ['ruin_partition_low','wall','structural_damage',false],['ruin_partition_high','wall','structural_damage',false],
        ['ruin_masonry_debris','household_rubble','structural_debris',true],['ruin_wood_debris','household_rubble','structural_debris',true],
        ['ruin_wood_opening','floor_opening','structural_damage',true],['ruin_masonry_opening','floor_opening','structural_damage',true]];
    for(const [id,type,key,passable]of definitions){
        const asset=findLocationAsset(id),p=placementForLocationAsset(asset),other=placementForLocationAsset(asset);
        assert.equal(asset.group,'Разрушения');assert.equal(asset.passable,passable);assert.equal(p.type,type);assert.equal(p.visualAsset,id);
        assert.equal(p.properties[key].version,1);assert.equal(p.properties[key].seed,id);
        assert.equal(visualAssetForLocationObject(type,{visual_asset:id}).id,id);
        p.properties[key].material='changed';assert.notEqual(other.properties[key].material,'changed');
        assert.notEqual(asset.properties[key].material,'changed');
    }
    for(const size of ['low','high']){
        const inner=findLocationAsset('ruin_partition_'+size),outer=findLocationAsset('ruin_wall_'+size);
        assert.ok(inner.dimensions[1]<outer.dimensions[1]);assert.notEqual(inner.id,outer.id);
        assert.equal(placementForLocationAsset(inner).visualAsset,inner.id);
    }
});
