import { MathUtils, Quaternion, Vector3 } from 'three';

const forward = new Vector3(0, 0, 1);
const up = new Vector3(0, 1, 0), right = new Vector3(1, 0, 0);
const facing = new Vector3(), lateral = new Vector3(), supportNormal = new Vector3();
const target = new Vector3(), axis = new Vector3(), bend = new Vector3(), knee = new Vector3();
const from = new Vector3(), to = new Vector3(), point = new Vector3();
const deltaRotation = new Quaternion(), parentRotation = new Quaternion();
const groundRotation = new Quaternion(), inverseBind = new Quaternion();

export function createCharacterFootIK(model) {
    const skin = model.getObjectByName('base');
    if (!skin?.isSkinnedMesh) return null;
    const { bones, boneInverses } = skin.skeleton;
    const findBone = name => bones.find(bone => bone.name.replace('.', '') === name);
    const body = findBone('Body');
    const { position, skinIndex } = skin.geometry.attributes;
    const legs = ['L', 'R'].map(side => {
        const thigh = findBone('UpperLeg' + side), shin = findBone('LowerLeg' + side), boot = findBone('Foot' + side);
        if (!thigh || !shin || !boot) return null;
        const bootIndex = bones.indexOf(boot);
        const sole = [];
        for (let i = 0; i < position.count; i++) {
            if (skinIndex.getX(i) !== bootIndex || position.getY(i) > .002) continue;
            point.fromBufferAttribute(position, i);
            if (!sole.some(vertex => vertex.distanceToSquared(point) < .000001)) sole.push(point.clone());
        }
        // Носок, пятка и боковые кромки достаточно точно описывают опору подошвы.
        const edge = (x, z) => sole.reduce((a, b) => a.x * x + a.z * z > b.x * x + b.z * z ? a : b);
        const contact = sole.length ? [...new Set([edge(0, 1), edge(0, -1), edge(1, 0), edge(-1, 0),
            edge(1, 1), edge(-1, 1)])] : [];
        for (const vertex of contact) vertex.applyMatrix4(skin.bindMatrix).applyMatrix4(boneInverses[bootIndex]);
        if (!contact.length) return null;
        // Берец не дочерний к голени. Положение лодыжки берём из исходного положения костей.
        const tip = new Vector3().setFromMatrixPosition(boneInverses[bootIndex].clone().invert())
            .applyMatrix4(boneInverses[bones.indexOf(shin)]);
        if (tip.y < .001) return null;
        const soleCenter = contact.reduce((sum, vertex) => sum.add(vertex), new Vector3()).divideScalar(contact.length);
        contact.push(soleCenter);
        return { thigh, shin, boot, contact, tip, side: side === 'L' ? 1 : -1,
            hip: new Vector3(), joint: new Vector3(), ankle: new Vector3(), desired: new Vector3(), bootPoint: new Vector3(),
            plant: new Vector3(), planted: false, soleWorld: contact.map(() => new Vector3()),
            thighRotation: new Quaternion(), shinRotation: new Quaternion(), bootPosition: new Vector3(),
            bootRotation: new Quaternion(), soleCenter };
    });
    return body && legs.every(Boolean) ? { body, legs, bodyPosition: new Vector3(), applied: false } : null;
}

export function restoreCharacterFeet(ik) {
    if (!ik?.applied) return;
    ik.body.position.copy(ik.bodyPosition);
    for (const leg of ik.legs) {
        leg.thigh.quaternion.copy(leg.thighRotation);
        leg.shin.quaternion.copy(leg.shinRotation);
        leg.boot.position.copy(leg.bootPosition);
        leg.boot.quaternion.copy(leg.bootRotation);
    }
    ik.applied = false;
}

export function rotateJoint(bone, current, desired) {
    bone.parent.getWorldQuaternion(parentRotation).invert();
    from.copy(current).normalize().applyQuaternion(parentRotation);
    to.copy(desired).normalize().applyQuaternion(parentRotation);
    bone.quaternion.premultiply(deltaRotation.setFromUnitVectors(from, to));
}

function solveLeg(leg) {
    const { thigh, shin, boot, hip, joint, ankle, bootPoint, tip } = leg;
    // Сгиб исходного клипа измеряется до переноса таза и лодыжки.
    from.subVectors(ankle, hip).normalize();
    to.subVectors(joint, hip).addScaledVector(from, -to.dot(from));
    const authoredBend = to.length();
    leg.thigh.getWorldPosition(leg.hip);
    leg.shin.getWorldPosition(leg.joint);
    leg.shin.localToWorld(target.copy(leg.tip));
    const a = leg.hip.distanceTo(leg.joint), b = leg.joint.distanceTo(target);
    axis.subVectors(leg.desired, leg.hip);
    const reach = axis.length();
    if (reach < .001) return;
    axis.divideScalar(reach);
    const length = MathUtils.clamp(reach, Math.abs(a - b) + .002, a + b - .002);
    bend.copy(facing).addScaledVector(lateral, leg.side * .16);
    bend.addScaledVector(axis, -bend.dot(axis)).normalize();
    to.addScaledVector(axis, -to.dot(axis)).normalize();
    // У выпрямленной ноги плоскость сгиба почти не определена. Колено опирается
    // на направление корпуса, а выраженный сгиб клипа уточняет его в той же полуплоскости.
    const agreement = MathUtils.smoothstep(bend.dot(to), .1, .85);
    bend.lerp(to, .86 * agreement * MathUtils.smoothstep(authoredBend, .018, .10));
    bend.normalize();
    const along = (a * a - b * b + length * length) / (2 * length);
    knee.copy(leg.hip).addScaledVector(axis, along).addScaledVector(bend, Math.sqrt(Math.max(0, a * a - along * along)));
    rotateJoint(thigh, target.subVectors(joint, hip), point.subVectors(knee, hip));
    shin.getWorldPosition(joint);
    shin.localToWorld(target.copy(tip)).sub(joint);
    point.copy(hip).addScaledVector(axis, length).sub(joint);
    rotateJoint(shin, target, point);

    // Кость берца независима от голени. Сохраняем её ориентацию и смещение относительно лодыжки.
    shin.localToWorld(target.copy(tip)).sub(ankle).add(bootPoint);
    boot.position.copy(boot.parent.worldToLocal(target));
}

function groundSlope(before, middle, after, radius) {
    if (!Number.isFinite(before) || !Number.isFinite(middle) || !Number.isFinite(after)) return 0;
    const left = middle - before, right = after - middle;
    // Ступень и край уступа задают высоту опоры. Наклон берётся только с участка склона.
    if (Math.abs(left - right) > .03) return 0;
    return (left + right) / (2 * radius);
}

function alignBoot(leg, surface, dx, dz, height, lift) {
    const radius = .09;
    point.copy(leg.soleCenter).applyMatrix4(leg.boot.matrixWorld);
    const x = point.x + dx, z = point.z + dz, middle = surface(x, z, height);
    const slopeX = groundSlope(surface(x - radius, z, height), middle, surface(x + radius, z, height), radius);
    const slopeZ = groundSlope(surface(x, z - radius, height), middle, surface(x, z + radius, height), radius);
    const steepness = Math.hypot(slopeX, slopeZ);
    const weight = 1 - MathUtils.smoothstep(lift, .026, .13);
    if (steepness < .04 || weight === 0) return false;
    const scale = Math.min(1, .90 / steepness) * weight;
    supportNormal.set(-slopeX * scale, 1, -slopeZ * scale).normalize();
    leg.boot.parent.getWorldQuaternion(parentRotation);
    inverseBind.copy(parentRotation).invert();
    groundRotation.setFromUnitVectors(up, supportNormal).premultiply(inverseBind).multiply(parentRotation);
    leg.boot.quaternion.premultiply(groundRotation);
    leg.boot.updateWorldMatrix(false, false);
    return true;
}

export function fitCharacterFeet(model, ik, surface, moving) {
    if (!ik) return false;
    if (!surface) {
        for (const leg of ik.legs) leg.planted = false;
        return false;
    }
    model.updateWorldMatrix(true, true);
    const height = model.matrixWorld.elements[13];
    let bodyOffset = 0, maxBodyOffset = Infinity, changed = false;
    for (const leg of ik.legs) {
        leg.bootRotation.copy(leg.boot.quaternion);
        leg.hip.setFromMatrixPosition(leg.thigh.matrixWorld);
        leg.joint.setFromMatrixPosition(leg.shin.matrixWorld);
        leg.ankle.copy(leg.tip).applyMatrix4(leg.shin.matrixWorld);
        leg.bootPoint.setFromMatrixPosition(leg.boot.matrixWorld);
        let lowest = Infinity;
        for (let i = 0; i < leg.contact.length; i++) {
            leg.soleWorld[i].copy(leg.contact[i]).applyMatrix4(leg.boot.matrixWorld);
            lowest = Math.min(lowest, leg.soleWorld[i].y);
        }
        const lift = moving ? Math.max(0, lowest - height) : 0;
        if (!moving || lift > .058) leg.planted = false;
        if (moving && lift <= .025 && !leg.planted) {
            leg.planted = true;
            leg.plant.copy(leg.ankle);
        }
        let dx = 0, dz = 0;
        if (leg.planted) {
            dx = leg.plant.x - leg.ankle.x;
            dz = leg.plant.z - leg.ankle.z;
            const drift = Math.hypot(dx, dz);
            const weight = (1 - MathUtils.smoothstep(lift, .012, .058)) * (1 - MathUtils.smoothstep(drift, .14, .30));
            dx *= weight;
            dz *= weight;
        }
        const tilted = alignBoot(leg, surface, dx, dz, height, lift);
        if (tilted) {
            for (let i = 0; i < leg.contact.length; i++) {
                leg.soleWorld[i].copy(leg.contact[i]).applyMatrix4(leg.boot.matrixWorld);
            }
        }
        let floorOffset = -Infinity;
        for (const vertex of leg.soleWorld) {
            const y = surface(vertex.x + dx, vertex.z + dz, height);
            if (Number.isFinite(y)) floorOffset = Math.max(floorOffset, y - vertex.y + lift);
        }
        const dy = Number.isFinite(floorOffset) ? floorOffset + .002 : 0;
        leg.desired.copy(leg.ankle);
        leg.desired.x += dx;
        leg.desired.y += dy;
        leg.desired.z += dz;
        if (tilted || Math.abs(dx) + Math.abs(dy) + Math.abs(dz) > .003) changed = true;
        bodyOffset += dy / ik.legs.length;
        const reach = leg.hip.distanceTo(leg.joint) + leg.joint.distanceTo(leg.ankle) - .015;
        const across = (leg.desired.x - leg.hip.x) ** 2 + (leg.desired.z - leg.hip.z) ** 2;
        maxBodyOffset = Math.min(maxBodyOffset, leg.desired.y - leg.hip.y + Math.sqrt(Math.max(.01, reach * reach - across)));
    }
    if (!changed) return false;
    ik.bodyPosition.copy(ik.body.position);
    ik.body.getWorldPosition(point);
    point.y += Math.min(bodyOffset, maxBodyOffset);
    ik.body.position.copy(ik.body.parent.worldToLocal(point));
    facing.copy(forward).transformDirection(model.matrixWorld);
    lateral.copy(right).transformDirection(model.matrixWorld);
    for (const leg of ik.legs) {
        leg.thighRotation.copy(leg.thigh.quaternion);
        leg.shinRotation.copy(leg.shin.quaternion);
        leg.bootPosition.copy(leg.boot.position);
        solveLeg(leg);
    }
    ik.applied = true;
    return true;
}
