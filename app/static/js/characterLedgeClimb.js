import { MathUtils, Quaternion, Vector3 } from 'three';
import { rotateJoint } from './characterFootIK.js';

export const TERRAIN_CLIMB_MS = 1480;
const ease = (value, start, end) => MathUtils.smoothstep(value, start, end);
const lerp = MathUtils.lerp;
const up = new Vector3(0, 1, 0);
const point = new Vector3(), joint = new Vector3(), end = new Vector3(), axis = new Vector3();
const bend = new Vector3(), target = new Vector3(), current = new Vector3(), desired = new Vector3();
const ground = new Vector3(), pole = new Vector3(), offset = new Vector3();
const rotation = new Quaternion(), parent = new Quaternion();

export function createTerrainClimbMotion(first, last, surface) {
    const origin = new Vector3(first.x + .5, first.height, first.y + .5);
    const dx = last.x - first.x, dz = last.y - first.y;
    const distance = Math.hypot(dx, dz);
    if (distance < .001) return null;
    const enterTile = (start, delta, destination) => {
        if (!delta) return 0;
        const min = Math.floor(destination);
        return ((delta > 0 ? min : min + 1) - start) / delta;
    };
    const edge = MathUtils.clamp(Math.max(enterTile(origin.x, dx, last.x + .5),
        enterTile(origin.z, dz, last.y + .5)) * distance, .05, distance);
    return { origin, forward: new Vector3(dx / distance, 0, dz / distance),
        right: new Vector3(dz / distance, 0, -dx / distance),
        edge, distance, rise: last.height - first.height, surface, progress: 0 };
}

export function terrainClimbRoot(climb, progress, result) {
    const { origin, forward, edge, distance, rise } = climb;
    const approach = Math.max(0, edge - .25);
    const over = Math.min(distance, edge + .17);
    const travel = approach * ease(progress, 0, .23)
        + (over - approach) * ease(progress, .33, .75)
        + (distance - over) * ease(progress, .69, 1);
    result.copy(origin).addScaledVector(forward, travel);
    result.y += rise * ease(progress, .22, .81);
    result.y -= .075 * ease(progress, .02, .14) * (1 - ease(progress, .15, .32));
    result.y += .045 * ease(progress, .62, .79) * (1 - ease(progress, .80, 1));
    return result;
}

export function createCharacterLedgeClimb(model, feet) {
    if (!feet) return null;
    const { bones } = model.getObjectByName('base').skeleton;
    const find = name => bones.find(bone => bone.name.replace('.', '') === name);
    const torso = [find('Abdomen'), find('Torso')].filter(Boolean);
    const arms = ['L', 'R'].map(side => ({
        upper: find('UpperArm' + side), lower: find('LowerArm' + side),
        hand: find('Wrist' + side), middle: find('Middle1' + side),
    }));
    if (arms.some(arm => !arm.upper || !arm.lower || !arm.hand || !arm.middle)) return null;
    const touched = new Set([...torso, ...arms.flatMap(arm => [arm.upper, arm.lower, arm.hand]),
        ...feet.legs.flatMap(leg => [leg.thigh, leg.shin, leg.boot])]);
    return { arms, torso, feet, applied: false,
        saved: [...touched].map(bone => ({ bone, position: new Vector3(), rotation: new Quaternion() })) };
}

export function restoreCharacterLedgeClimb(rig) {
    if (!rig?.applied) return;
    for (const { bone, position, rotation } of rig.saved) {
        bone.position.copy(position);
        bone.quaternion.copy(rotation);
    }
    rig.applied = false;
}

function aimLimb(upper, lower, tip, goal, poleDirection) {
    upper.getWorldPosition(point);
    lower.getWorldPosition(joint);
    lower.localToWorld(end.copy(tip));
    const a = point.distanceTo(joint), b = joint.distanceTo(end);
    axis.subVectors(goal, point);
    const distance = axis.length();
    if (distance < .001) return end;
    axis.divideScalar(distance);
    const reach = MathUtils.clamp(distance, Math.abs(a - b) + .002, a + b - .002);
    const along = (a * a - b * b + reach * reach) / (2 * reach);
    bend.copy(poleDirection).addScaledVector(axis, -axis.dot(poleDirection)).normalize();
    target.copy(point).addScaledVector(axis, reach);
    desired.copy(axis).multiplyScalar(along).addScaledVector(bend, Math.sqrt(Math.max(0, a*a-along*along)));
    rotateJoint(upper, current.subVectors(joint, point), desired);
    lower.getWorldPosition(joint);
    lower.localToWorld(current.copy(tip)).sub(joint);
    rotateJoint(lower, current, desired.subVectors(target, joint));
    return lower.localToWorld(end.copy(tip));
}

export function applyCharacterLedgeClimb(model, rig, climb) {
    if (!rig) return;
    const { origin, forward, right, edge, distance, rise, surface, progress } = climb;
    const reach = ease(progress, .045, .30) * (1 - ease(progress, .67, .93));
    const effort = ease(progress, .06, .37) * (1 - ease(progress, .67, .94));
    for (const saved of rig.saved) {
        saved.position.copy(saved.bone.position);
        saved.rotation.copy(saved.bone.quaternion);
    }
    rig.applied = true;
    model.updateWorldMatrix(true, true);
    for (const bone of rig.torso) {
        bone.parent.getWorldQuaternion(parent).invert();
        current.copy(right).applyQuaternion(parent);
        bone.quaternion.premultiply(rotation.setFromAxisAngle(current, effort * .34));
    }
    model.getWorldPosition(ground);
    for (const [index, arm] of rig.arms.entries()) {
        const side = index === 0 ? 1 : -1;
        const handProgress = index === 0 ? reach : reach * ease(progress, .025, .18);
        if (handProgress <= .001) continue;
        const handDistance = Math.min(distance, edge + .22);
        const handGoal = offset.copy(origin).addScaledVector(forward, handDistance).addScaledVector(right, side * .19);
        const rim = surface?.(handGoal.x, handGoal.z, origin.y + rise) ?? origin.y + rise;
        handGoal.y = Math.max(origin.y + rise - .14, rim) + .075;
        arm.hand.getWorldPosition(current);
        handGoal.lerp(current, 1 - handProgress);
        pole.copy(right).multiplyScalar(side * .55).addScaledVector(up, -.52).addScaledVector(forward, -.22);
        aimLimb(arm.upper, arm.lower, arm.hand.position, handGoal, pole);
        arm.hand.getWorldPosition(point);
        arm.middle.getWorldPosition(current).sub(point);
        desired.copy(forward).addScaledVector(up, -.34).setLength(current.length());
        desired.lerp(current, 1 - handProgress);
        rotateJoint(arm.hand, current, desired);
    }
    for (const [index, leg] of rig.feet.legs.entries()) {
        leg.boot.localToWorld(current.copy(leg.soleCenter));
        const ankle = leg.shin.localToWorld(leg.ankle.copy(leg.tip));
        const soleOffset = leg.bootPoint.copy(leg.boot.getWorldPosition(desired)).sub(ankle);
        const lead = index === 1;
        const step = ease(progress, lead ? .29 : .62, lead ? .61 : .90);
        const approach = Math.max(0, edge - (lead ? .22 : .29));
        let startStep = approach * ease(progress, lead ? .015 : .09, lead ? .21 : .29);
        if (!lead) startStep += Math.max(0, edge + .06 - approach) * ease(progress, .36, .63);
        const settle = ease(progress, lead ? .67 : .86, 1);
        const landing = lead ? Math.min(distance + .05, edge + .43) : Math.min(distance, edge + .20);
        const climbDistance = lerp(lerp(startStep, landing, step), distance, settle);
        offset.copy(current).sub(ground);
        const footGoal = current.copy(origin).add(offset).addScaledVector(forward, climbDistance);
        const low = surface?.(footGoal.x, footGoal.z, origin.y) ?? origin.y;
        const high = origin.y + rise;
        const lift = Math.sin(Math.PI * step) * (lead ? .18 : .13);
        const footHeight = step >= 1 ? low : Math.max(low,
            lerp(origin.y, high, ease(step, 0, lead ? .70 : .83)) + lift);
        footGoal.y = footHeight + .006;
        // Сначала поднимается подошва, затем нога проходит кромку.
        const sole = leg.boot.localToWorld(leg.desired.copy(leg.soleCenter));
        footGoal.sub(sole).add(ankle);
        pole.copy(forward).addScaledVector(right, leg.side * .16);
        const result = aimLimb(leg.thigh, leg.shin, leg.tip, footGoal, pole);
        leg.boot.position.copy(leg.boot.parent.worldToLocal(result.add(soleOffset)));
    }
}
