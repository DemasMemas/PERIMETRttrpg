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
