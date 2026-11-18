import { findLocationAsset } from './locationAssetCatalog.js';

export function defaultAssetCoverClass(asset) {
    const id = asset?.id || '';
    if (/^crafted_cover_m1[89]$|^crafted_cover_m2[0-3]$/.test(id)) {
        return ['conditional', 'flimsy', 'strong', 'very_strong', 'strong', 'strong'][Number(id.slice(-2)) - 18];
    }
    if (/concrete|monument|masonry_debris|^ruin_wall|^rock$|swamp_rock|household_well/.test(id)) return 'very_strong';
    if (/bunker_hatch|sandbag|chainlink|safe_|corrugated_iron|rusty_metal|car_|electric_pole|power_tower|transformer|dumpster|generator|wood_stove|sauna_stove/.test(id)) return 'strong';
    if (/cardboard|bush|shrub|grass|fern|wheat|reeds|litter|rug|clothes_line/.test(id)
        || asset?.kind === 'lamp' || asset?.group === 'Посуда' || asset?.group === 'Огонь') return 'conditional';
    if (/tire|broken_boards|^fence$|^tent$|bucket_plastic|laundry_basket|glass|mirror|poster|lamp|bulb/.test(id)) return 'flimsy';
    return asset?.passable ? 'conditional' : 'medium';
}

export function placementForLocationAsset(asset, seed = asset.id) {
    const properties=asset.properties?JSON.parse(JSON.stringify(asset.properties)):{};
    for(const key of ['structural_damage','structural_debris']){
        if(properties[key]?.version===1)properties[key].seed=String(seed);
    }
    properties.cover_class ??= defaultAssetCoverClass(asset);
    if (asset.id === 'art_chainlink_fence') properties.mesh_cover = true;
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

export function vegetationColorValues(asset, properties) {
    const channels = asset?.colorChannels;
    if (!channels) return {};
    const color = typeof properties?.color === 'string' && /^#[0-9a-f]{6}$/i.test(properties.color)
        && properties.color.toLowerCase() !== asset.color.toLowerCase() ? properties.color : '#ffffff';
    return Object.fromEntries(Object.keys(channels).map(channel => {
        const saved = properties?.[channel];
        const legacy = asset.visual?.renderMode !== 'glb' || !channels.foliage_color || channel === 'foliage_color'
            ? color : '#ffffff';
        return [channel, typeof saved === 'string' && /^#[0-9a-f]{6}$/i.test(saved) ? saved.toLowerCase() : legacy.toLowerCase()];
    }));
}

export function locationObjectLabel(object) {
    const asset = visualAssetForLocationObject(object?.type, object?.properties);
    const raw = String(object?.name || object?.properties?.anomalyName || '').trim();
    if (object?.type === 'swamp_wheat_wild_1' && raw === 'Сухая трава') return asset.label;
    const technical = /\b(?:PBR|GLB|GLTF|Poly\s*Haven)\b|тест[,\s]|не финальный/i.test(raw);
    const generated = /^(?:Секция (?:ограды|забора)|Грунтовая площадка|Складской ящик|Бочка полигона)\s+\d+$/i.test(raw);
    if (asset && (technical || generated || !/[А-Яа-яЁё]/.test(raw))) return asset.label;
    const clean = raw.replace(/\s*[([](?:PBR|GLB|GLTF|Poly\s*Haven|тест,\s*не финальный)[)\]]/gi, '')
        .replace(/\s+\b(?:PBR|GLB|GLTF)\b/gi, '').trim();
    if (/[А-Яа-яЁё]/.test(clean)) return clean;
    return asset?.label || ({chest:'Контейнер',anomaly:'Аномалия',workbench:'Станок'}[object?.type]) || 'Объект';
}
