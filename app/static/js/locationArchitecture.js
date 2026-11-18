const DEFAULT_DIMENSIONS = {
    wall: { width: 3, depth: 0.2, height: 2.4 },
    door: { width: 0.9, depth: 0.18, height: 2 },
    window: { width: 1.2, depth: 0.14, height: 1 },
    floor: { width: 2, depth: 2, height: 0.12 },
    ceiling: { width: 2, depth: 2, height: 0.12 },
    roof: { width: 2, depth: 2, height: 0.13 },
    stairs: { width: 1.2, depth: 3.6, height: 2.4 },
    ladder: { width: 0.9, depth: 0.9, height: 2.4 },
    hatch: { width: 1, depth: 1, height: 0.08 },
    floor_opening: { width: 1, depth: 1, height: 0.05 },
};

// Bottom-centred geometry, shared by placement previews and saved objects.
export function stairStepPlan({ width, depth, height }) {
    const count = Math.max(1, Math.ceil(height / 0.2));
    return Array.from({ length: count }, (_, index) => ({
        width, depth: depth / count, height: height * (index + 1) / count,
        z: -depth / 2 + depth * (index + 0.5) / count,
    }));
}

export const STOREY_HEIGHT = 2.4;

export function structureLevel(object) {
    const level = Number(object?.properties?.level ?? object?.level);
    return Number.isInteger(level) ? level : 0;
}

export function storeyPlan({ buildingId, sourceLevel, targetLevel, mode = 'copy', seed = 0 }, objects) {
    if (!buildingId || !Number.isInteger(sourceLevel) || !Number.isInteger(targetLevel)) return { error: 'Укажите здание и целочисленные уровни' };
    const peers = (objects || []).filter(o => structureBuildingId(o) === buildingId);
    if (peers.some(o => structureLevel(o) === targetLevel)) return { error: 'Этот этаж уже существует' };
    const source = peers.filter(o => structureLevel(o) === sourceLevel);
    const floors = source.filter(o => o.type === 'floor');
    if (!floors.length) return { error: 'На исходном этаже нет пола' };
    if (!['copy', 'empty', 'layout'].includes(mode)) return { error: 'Неизвестный вариант этажа' };
    const inside = (x, z) => floors.some(f => {
        const p = structurePose(f), dx = x - p.x, dz = z - p.z;
        return Math.abs(dx * p.axis.x + dz * p.axis.z) < p.width / 2 - .001
            && Math.abs(dx * p.normal.x + dz * p.normal.z) < p.depth / 2 - .001;
    });
    const exterior = source.filter(o => o.type === 'wall' && (() => {
        const p = structurePose(o), distance = p.depth / 2 + .05;
        // Sample the whole segment: a wall can border several floor sections.
        return [-.4, 0, .4].some(t => {
            const x = p.x + p.axis.x * p.width * t, z = p.z + p.axis.z * p.width * t;
            return inside(x + p.normal.x * distance, z + p.normal.z * distance)
                !== inside(x - p.normal.x * distance, z - p.normal.z * distance);
        });
    })());
    const copy = o => {
        const properties = JSON.parse(JSON.stringify(o.properties || {}));
        delete properties.generated_floor_spec;
        if (Array.isArray(properties.contents)) properties.contents = [];
        properties.level = targetLevel;
        properties.building_id = buildingId;
        if(properties.roof_group)properties.roof_group=`${properties.roof_group}-level-${targetLevel}`;
        for(const profile of [properties.roof_section,properties.roof_wall]) {
            if(Array.isArray(profile?.bodies))for(const body of profile.bodies)body.level+=targetLevel-sourceLevel;
        }
        if(properties.roof_section)properties.roof_section.level=(properties.roof_section.level??sourceLevel)+targetLevel-sourceLevel;
        if(properties.roof_wall?.frame)properties.roof_wall.frame.level+=targetLevel-sourceLevel;
        return { name: o.name || o.type, type: o.type, tile_x: o.tile_x, tile_y: o.tile_y, properties };
    };
    const selected = mode === 'copy' ? source : source.filter(o => ['floor','roof','ceiling'].includes(o.type) || exterior.includes(o)
        || ['door', 'window'].includes(o.type) && exterior.some(w => doorOnWall(o, w)));
    const result = selected.map(copy);
    if (mode === 'layout') floors.forEach((floor, index) => {
        const p = structurePose(floor);
        if (p.width < 3 || p.depth < 3) return;
        const shift = ((Number(seed) + index) % 2 ? -.15 : .15) * p.depth;
        const x = p.x + p.normal.x * shift, z = p.z + p.normal.z * shift;
        const tile_x = Math.floor(x), tile_y = Math.floor(z);
        const common = { building_id: buildingId, level: targetLevel, rotation: p.angle,
            offset_x: x - tile_x - .5, offset_z: z - tile_y - .5,
            elevation: floor.properties?.elevation || 0 };
        result.push({ type: 'wall', name: 'Перегородка нового этажа', tile_x, tile_y,
            properties: { ...common, dimensions: {width:p.width, depth:.12, height:STOREY_HEIGHT}, visual_asset:'inner_wall' } },
        { type: 'door', name: 'Проём перегородки', tile_x, tile_y,
            properties: { ...common, dimensions:{width:.9,depth:.18,height:2}, visual_asset:'opening', is_open:true, interactions:[] } });
    });
    return { objects: result };
}

export function floorSlabParts(floor, objects, p = floorVisualPose(floor, objects)) {
    let parts = [{x:0,z:0,width:p.width,depth:p.depth}];
    for (const stair of objects || []) {
        if (!(['stairs','ladder'].includes(stair.type) && structureLevel(stair) + 1 === structureLevel(floor)
            || ['hatch','floor_opening'].includes(stair.type) && structureLevel(stair) === structureLevel(floor))
            || structureBuildingId(stair) !== structureBuildingId(floor)) continue;
        const s = structurePose(stair), corners = [];
        for (const a of [-1,1]) for (const b of [-1,1]) {
            const dx = s.x + a*s.width/2*s.axis.x + b*s.depth/2*s.normal.x - p.x;
            const dz = s.z + a*s.width/2*s.axis.z + b*s.depth/2*s.normal.z - p.z;
            corners.push([dx*p.axis.x+dz*p.axis.z, dx*p.normal.x+dz*p.normal.z]);
        }
        const minX=Math.min(...corners.map(c=>c[0])),maxX=Math.max(...corners.map(c=>c[0]));
        const minZ=Math.min(...corners.map(c=>c[1])),maxZ=Math.max(...corners.map(c=>c[1]));
        parts = parts.flatMap(r => {
            const x0=r.x-r.width/2,x1=r.x+r.width/2,z0=r.z-r.depth/2,z1=r.z+r.depth/2;
            const a=Math.max(x0,minX),b=Math.min(x1,maxX),c=Math.max(z0,minZ),d=Math.min(z1,maxZ);
            if(a>=b||c>=d)return [r];
            return [[x0,a,z0,z1],[b,x1,z0,z1],[a,b,z0,c],[a,b,d,z1]]
                .filter(([l,h,t,u])=>h-l>.001&&u-t>.001)
                .map(([l,h,t,u])=>({x:(l+h)/2,z:(t+u)/2,width:h-l,depth:u-t}));
        });
    }
    return parts;
}

export function structureLevelOffset(object) {
    return structureLevel(object) * STOREY_HEIGHT;
}

export function structureBuildingId(object) {
    const id = object?.properties?.building_id;
    return id == null ? null : String(id).trim() || null;
}

export function sameBuildingLevel(a, b) {
    return structureLevel(a) === structureLevel(b)
        && structureBuildingId(a) === structureBuildingId(b);
}

export function nextStoreyFloors(source, objects) {
    if (source?.type !== 'floor') return { error: 'Выберите пол первого этажа' };
    const buildingId = structureBuildingId(source);
    if (!buildingId) return { error: 'Сначала назначьте ID старому зданию и его элементам' };
    if (structureLevel(source) !== 0) return { error: 'Пока поддерживаются только два этажа' };
    const floors = (objects || []).filter(object => object.type === 'floor'
        && structureBuildingId(object) === buildingId);
    const upper = floors.filter(object => structureLevel(object) === 1);
    const sameFootprint = (a, b) => {
        const p = structurePose(a);
        const q = structurePose(b);
        return Math.abs(p.x - q.x) < 0.001 && Math.abs(p.z - q.z) < 0.001
            && Math.abs(p.width - q.width) < 0.001 && Math.abs(p.depth - q.depth) < 0.001
            && Math.abs(p.angle - q.angle) < 0.001;
    };
    const lower = floors.filter(object => structureLevel(object) === 0);
    const missing = lower.filter((object, index) => !upper.some(other => sameFootprint(object, other))
        && !lower.slice(0, index).some(other => sameFootprint(object, other)));
    if (!missing.length) return { error: 'Перекрытие второго этажа уже существует' };
    // Compatibility entry point for old integrations that fill missing upper slabs.
    return storeyPlan({buildingId,sourceLevel:0,targetLevel:1,mode:'copy'}, missing);
}

export function structurePose(object) {
    const properties = object?.properties || {};
    const dimensions = { ...(DEFAULT_DIMENSIONS[object?.type] || DEFAULT_DIMENSIONS.wall), ...(properties.dimensions || {}) };
    const angle = Number(properties.rotation || 0);
    return {
        x: Number(object?.tile_x || 0) + 0.5 + Number(properties.offset_x || 0),
        z: Number(object?.tile_y || 0) + 0.5 + Number(properties.offset_z || 0),
        angle,
        axis: { x: Math.cos(angle), z: -Math.sin(angle) },
        normal: { x: Math.sin(angle), z: Math.cos(angle) },
        width: Number(dimensions.width),
        depth: Number(dimensions.depth),
        height: Number(dimensions.height),
    };
}

export function floorVisualPose(object, objects) {
    const pose = structurePose(object);
    const wallGap = 0.01;
    let minAlong = -pose.width / 2;
    let maxAlong = pose.width / 2;
    let minAcross = -pose.depth / 2;
    let maxAcross = pose.depth / 2;
    for (const wall of objects || []) {
        if (wall.type !== 'wall' || !sameBuildingLevel(object, wall)) continue;
        const edge = structurePose(wall);
        const dx = edge.x - pose.x;
        const dz = edge.z - pose.z;
        const along = dx * pose.axis.x + dz * pose.axis.z;
        const across = dx * pose.normal.x + dz * pose.normal.z;
        const parallel = Math.abs(edge.axis.x * pose.axis.x + edge.axis.z * pose.axis.z);
        // Trimming a whole slab edge is safe only when the wall covers it
        // completely. A partial wall can border a courtyard while the rest of
        // that edge joins another wing; shortening it all opens a floor gap.
        if (parallel >= 0.98 && Math.abs(along) + pose.width / 2 <= edge.width / 2 + wallGap
            && Math.abs(Math.abs(across) - pose.depth / 2) <= 0.2) {
            if (across < 0) minAcross = Math.max(minAcross, across + edge.depth / 2 + wallGap);
            else maxAcross = Math.min(maxAcross, across - edge.depth / 2 - wallGap);
        } else if (parallel <= 0.02 && Math.abs(across) + pose.depth / 2 <= edge.width / 2 + wallGap
            && Math.abs(Math.abs(along) - pose.width / 2) <= 0.2) {
            if (along < 0) minAlong = Math.max(minAlong, along + edge.depth / 2 + wallGap);
            else maxAlong = Math.min(maxAlong, along - edge.depth / 2 - wallGap);
        }
    }
    const shiftAlong = (minAlong + maxAlong) / 2;
    const shiftAcross = (minAcross + maxAcross) / 2;
    return {
        ...pose,
        x: pose.x + shiftAlong * pose.axis.x + shiftAcross * pose.normal.x,
        z: pose.z + shiftAlong * pose.axis.z + shiftAcross * pose.normal.z,
        width: maxAlong - minAlong,
        depth: maxAcross - minAcross,
    };
}

export function floorFitPlan(floor, objects, blueprint = null) {
    if (floor?.type !== 'floor' || !structureBuildingId(floor)) {
        return { error: 'Выберите пол с назначенным зданием' };
    }
    const peers = (objects || []).filter(object => object.type === 'floor' && sameBuildingLevel(floor, object));
    if (peers.length !== 1) return { error: 'Этаж содержит несколько полов: выберите контур сегмента вручную' };
    const walls = (objects || []).filter(object => object.type === 'wall' && sameBuildingLevel(floor, object));
    if (walls.length < 4) return { error: 'Для точной подгонки нужны четыре стороны здания на этом этаже' };
    const base = structurePose(floor);
    const blueprintPose = blueprint?.type === 'floor' && sameBuildingLevel(floor, blueprint)
        ? structurePose(blueprint) : null;
    const angles = [...new Set([blueprintPose?.angle, base.angle, ...walls.map(wall => structurePose(wall).angle)]
        .filter(Number.isFinite))];
    const covers = (edges, coordinate, start, end) => {
        const intervals = edges.filter(edge => Math.abs(edge.coordinate - coordinate) < 0.12)
            .sort((a, b) => a.start - b.start);
        if (!intervals.some(interval => interval.end - interval.start >= (end - start) * 0.5)) return false;
        let cursor = start;
        for (const interval of intervals) {
            if (interval.start > cursor + 0.12) return false;
            cursor = Math.max(cursor, interval.end);
        }
        return cursor >= end - 0.12;
    };
    for (const angle of angles) {
        const axis = { x: Math.cos(angle), z: -Math.sin(angle) };
        const normal = { x: Math.sin(angle), z: Math.cos(angle) };
        const alongWalls = [];
        const acrossWalls = [];
        for (const wall of walls) {
            const edge = structurePose(wall);
            const along = edge.x * axis.x + edge.z * axis.z;
            const across = edge.x * normal.x + edge.z * normal.z;
            const parallel = Math.abs(edge.axis.x * axis.x + edge.axis.z * axis.z);
            if (parallel > 0.995) alongWalls.push({ coordinate: across,
                start: along - edge.width / 2, end: along + edge.width / 2 });
            else if (parallel < 0.005) acrossWalls.push({ coordinate: along,
                start: across - edge.width / 2, end: across + edge.width / 2 });
        }
        if (alongWalls.length < 2 || acrossWalls.length < 2) continue;
        const minAlong = Math.min(...acrossWalls.map(edge => edge.coordinate));
        const maxAlong = Math.max(...acrossWalls.map(edge => edge.coordinate));
        const minAcross = Math.min(...alongWalls.map(edge => edge.coordinate));
        const maxAcross = Math.max(...alongWalls.map(edge => edge.coordinate));
        const width = maxAlong - minAlong;
        const depth = maxAcross - minAcross;
        const floorAlong = base.x * axis.x + base.z * axis.z;
        const floorAcross = base.x * normal.x + base.z * normal.z;
        if (![width, depth, floorAlong, floorAcross].every(Number.isFinite)
            || width <= 0 || depth <= 0 || width > 30 || depth > 30
            || floorAlong < minAlong - 0.5 || floorAlong > maxAlong + 0.5
            || floorAcross < minAcross - 0.5 || floorAcross > maxAcross + 0.5
            || !covers(alongWalls, minAcross, minAlong, maxAlong)
            || !covers(alongWalls, maxAcross, minAlong, maxAlong)
            || !covers(acrossWalls, minAlong, minAcross, maxAcross)
            || !covers(acrossWalls, maxAlong, minAcross, maxAcross)) continue;
        const centerAlong = (minAlong + maxAlong) / 2;
        const centerAcross = (minAcross + maxAcross) / 2;
        const contour = { x: centerAlong * axis.x + centerAcross * normal.x,
            z: centerAlong * axis.z + centerAcross * normal.z, angle, width, depth };
        if (blueprintPose && Math.abs(blueprintPose.x - contour.x) < 0.12
            && Math.abs(blueprintPose.z - contour.z) < 0.12
            && Math.abs(blueprintPose.width - width) < 0.12
            && Math.abs(blueprintPose.depth - depth) < 0.12) return { pose: blueprintPose };
        return { pose: contour };
    }
    return { error: 'Замкнутый прямоугольный контур стен не найден; подгонка отменена' };
}

export function floorEntrancePoint(pose, objects, floor = null) {
    const halfWidth = pose.width / 2;
    const halfDepth = pose.depth / 2;
    for (const object of objects || []) {
        if (object?.type !== 'door' || (floor && !sameBuildingLevel(floor, object))) continue;
        const door = structurePose(object);
        const dx = door.x - pose.x;
        const dz = door.z - pose.z;
        const along = dx * pose.axis.x + dz * pose.axis.z;
        const across = dx * pose.normal.x + dz * pose.normal.z;
        if (Math.abs(along) > halfWidth + 0.5 || Math.abs(across) > halfDepth + 0.5) continue;
        if (Math.min(Math.abs(Math.abs(along) - halfWidth),
            Math.abs(Math.abs(across) - halfDepth)) > 0.5) continue;
        return [Math.max(-halfWidth, Math.min(halfWidth, along)),
            Math.max(-halfDepth, Math.min(halfDepth, across))];
    }
    return null;
}

export function doorOnWall(door, wall) {
    if (!sameBuildingLevel(door, wall)) return null;
    const a = structurePose(wall);
    const b = structurePose(door);
    const parallel = Math.abs(a.axis.x * b.axis.x + a.axis.z * b.axis.z);
    const dx = b.x - a.x;
    const dz = b.z - a.z;
    const along = dx * a.axis.x + dz * a.axis.z;
    const away = Math.abs(dx * a.normal.x + dz * a.normal.z);
    const wallElevation = Number(wall.properties?.elevation || 0);
    const doorElevation = Number(door.properties?.elevation || 0);
    const bottom = doorElevation - wallElevation;
    if ((door.type === 'window' ? bottom < 0 || bottom + b.height > a.height + 0.15 : Math.abs(bottom) > 0.15)
        || parallel < 0.94 || away > (a.depth + b.depth) / 2 + 0.12
        || Math.abs(along) > (a.width + b.width) / 2) return null;
    return { along, width: b.width, height: b.height, bottom };
}

export function doorFrameStyle(door, objects) {
    const doorPose = structurePose(door);
    const wall = (objects || []).find(item => item?.type === 'wall' && doorOnWall(door, item));
    if (!wall) {
        return { inWall: false, height: doorPose.height + 0.12,
            depth: doorPose.depth + 0.08, bottom: 0, color: '#4e3829' };
    }
    const wallPose = structurePose(wall);
    return {
        inWall: true,
        height: wallPose.height,
        depth: wallPose.depth + 0.04,
        bottom: Number(wall.properties?.elevation || 0) - Number(door.properties?.elevation || 0),
        color: wall.properties?.color || '#aa8866',
    };
}

export function doorTrimLayout(width, height, frame, jamb = 0.09) {
    const openingHeight = frame.inWall ? Math.min(height, frame.bottom + frame.height) : height;
    const baseY = frame.inWall ? Math.max(0, frame.bottom) : 0;
    const inset = frame.inWall ? 0.006 : 0;
    return {
        baseY,
        postX: frame.inWall ? width / 2 - jamb / 2 - inset : width / 2 + jamb / 2,
        postHeight: frame.inWall ? openingHeight - baseY : frame.height,
        postY: frame.inWall ? (baseY + openingHeight) / 2 : frame.bottom + frame.height / 2,
        lintelWidth: frame.inWall ? width : width + 2 * jamb,
        lintelY: frame.inWall ? openingHeight - 0.06 - inset : frame.bottom + frame.height - 0.06,
        pivotX: frame.inWall ? -width / 2 + jamb + 0.01 : -width / 2,
        panelWidth: frame.inWall ? Math.max(0.05, width - 2 * jamb - 0.02) : width,
        panelHeight: frame.inWall ? Math.max(0.05, openingHeight - baseY - 0.13) : height,
    };
}

export function houseWallWear(x, y, start, end, openings = []) {
    const fade = (distance, range) => Math.max(0, 1 - distance / range);
    const base = 0.055 * fade(y, 0.7);
    const corner = 0.025 * fade(Math.min(x - start, end - x), 0.7);
    let entrance = 0;
    for (const opening of openings) {
        if (opening.bottom > 0.15 || opening.top < 1.4) continue;
        const edge = Math.min(Math.abs(x - opening.start), Math.abs(x - opening.end));
        entrance = Math.max(entrance, fade(edge, 0.75) * fade(y, 1.8));
    }
    return Math.max(0.85, 1 - base - corner - 0.045 * entrance);
}

function intersection(a, b) {
    const determinant = a.axis.x * b.axis.z - a.axis.z * b.axis.x;
    if (Math.abs(determinant) < 0.4) return null;
    const dx = b.x - a.x;
    const dz = b.z - a.z;
    return {
        onA: (dx * b.axis.z - dz * b.axis.x) / determinant,
        onB: (dx * a.axis.z - dz * a.axis.x) / determinant,
    };
}

function wallTakesOuterCorner(wall, other, a, b) {
    const horizontalDifference = Math.abs(a.axis.x) - Math.abs(b.axis.x);
    if (Math.abs(horizontalDifference) > 0.1) return horizontalDifference > 0;
    return String(wall.id ?? '') < String(other.id ?? '');
}

export function wallVisualPlan(wall, objects) {
    const pose = structurePose(wall);
    let start = -pose.width / 2;
    let end = pose.width / 2;
    for (const other of objects || []) {
        if (other === wall || other?.type !== 'wall' || !sameBuildingLevel(wall, other)
            || (wall.id != null && other.id === wall.id)) continue;
        const otherPose = structurePose(other);
        const meeting = intersection(pose, otherPose);
        if (!meeting || Math.abs(Math.abs(meeting.onA) - pose.width / 2) > 0.7
            || Math.abs(Math.abs(meeting.onB) - otherPose.width / 2) > 0.7) continue;
        const sign = meeting.onA >= 0 ? 1 : -1;
        const outer = wallTakesOuterCorner(wall, other, pose, otherPose);
        const adjusted = meeting.onA + sign * (outer ? otherPose.depth / 2 : -otherPose.depth / 2);
        if (sign > 0) end = Math.min(end + 0.7, adjusted);
        else start = Math.max(start - 0.7, adjusted);
    }
    if (end - start < 0.1) { start = -pose.width / 2; end = pose.width / 2; }
    const openings = (objects || []).filter(item => ['door', 'window'].includes(item?.type))
        .map(door => doorOnWall(door, wall))
        .filter(Boolean)
        .map(opening => ({
            start: Math.max(start, opening.along - opening.width / 2),
            end: Math.min(end, opening.along + opening.width / 2),
            bottom: Math.max(0, opening.bottom),
            top: Math.min(pose.height, opening.bottom + opening.height),
        }))
        .filter(opening => opening.end - opening.start > 0.1)
        .sort((a, b) => a.start - b.start);
    const spans = [];
    let cursor = start;
    for (const opening of openings) {
        if (opening.start > cursor + 0.01) spans.push({ start: cursor, end: opening.start, bottom: 0, top: pose.height });
        if (opening.bottom > 0.01) spans.push({ start: opening.start, end: opening.end, bottom: 0, top: opening.bottom });
        if (opening.top < pose.height - 0.01) spans.push({ start: opening.start, end: opening.end, bottom: opening.top, top: pose.height });
        cursor = Math.max(cursor, opening.end);
    }
    if (cursor < end - 0.01) spans.push({ start: cursor, end, bottom: 0, top: pose.height });
    return { pose, spans, openings };
}
