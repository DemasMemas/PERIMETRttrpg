import { findLocationAsset } from './locationAssetCatalog.js';

export function placementForLocationAsset(asset, seed = asset.id) {
    const properties=asset.properties?JSON.parse(JSON.stringify(asset.properties)):{};
    for(const key of ['structural_damage','structural_debris']){
        if(properties[key]?.version===1)properties[key].seed=String(seed);
    }
    return {
        type: asset.baseType || asset.id,
        visualAsset: asset.baseType ? asset.id : null,
        properties,
    };
}

export function visualAssetForLocationObject(type, properties) {
    const variant = properties?.visual_asset ? findLocationAsset(properties.visual_asset) : null;
    return variant?.baseType === type ? variant : findLocationAsset(type);
}

export function locationObjectLabel(object) {
    const asset = visualAssetForLocationObject(object?.type, object?.properties);
    const raw = String(object?.name || object?.properties?.anomalyName || '').trim();
    const technical = /\b(?:PBR|GLB|GLTF|Poly\s*Haven)\b|тест[,\s]|не финальный/i.test(raw);
    const generated = /^(?:Секция (?:ограды|забора)|Грунтовая площадка|Складской ящик|Бочка полигона)\s+\d+$/i.test(raw);
    if (asset && (technical || generated || !/[А-Яа-яЁё]/.test(raw))) return asset.label;
    const clean = raw.replace(/\s*[([](?:PBR|GLB|GLTF|Poly\s*Haven|тест,\s*не финальный)[)\]]/gi, '')
        .replace(/\s+\b(?:PBR|GLB|GLTF)\b/gi, '').trim();
    if (/[А-Яа-яЁё]/.test(clean)) return clean;
    return asset?.label || ({chest:'Контейнер',anomaly:'Аномалия',workbench:'Станок'}[object?.type]) || 'Объект';
}
