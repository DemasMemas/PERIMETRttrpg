const CYCLE_SECONDS = 7.4;

const TURN_KEYS = [
    [0, 0], [0.62, 0], [1.02, 0.68], [1.46, 0.68], [1.88, 0],
    [2.45, 0], [2.9, -0.74], [3.34, -0.74], [3.78, 0],
    [4.48, 0], [4.83, 0.86], [5.12, 0.86], [5.48, 0], [CYCLE_SECONDS, 0],
];

const DIP_KEYS = [
    [0, 0], [4.04, 0], [4.4, 0.82], [4.76, 0.82], [5.12, 0], [CYCLE_SECONDS, 0],
];

function sampleTrack(keys, time) {
    for (let i = 1; i < keys.length; i += 1) {
        const [endTime, endValue] = keys[i];
        if (time > endTime) continue;
        const [startTime, startValue] = keys[i - 1];
        const span = endTime - startTime;
        const linear = span > 0 ? (time - startTime) / span : 1;
        const eased = linear * linear * (3 - 2 * linear);
        return startValue + (endValue - startValue) * eased;
    }
    return keys[keys.length - 1][1];
}

export function createCrowIdle(root) {
    const drivenMeshes = [];
    root?.traverse?.((object) => {
        const dictionary = object.morphTargetDictionary;
        const influences = object.morphTargetInfluences;
        if (!object.isMesh || !dictionary || !influences) return;
        const turnLeft = dictionary.Crow_Head_Turn_Left;
        const turnRight = dictionary.Crow_Head_Turn_Right;
        const dip = dictionary.Crow_Head_Dip;
        if (turnLeft === undefined || turnRight === undefined || dip === undefined) return;
        object.userData.staticIdleShadow = true;
        drivenMeshes.push({ influences, turnLeft, turnRight, dip });
    });

    let elapsed = 0;
    return {
        update(deltaSeconds) {
            if (!Number.isFinite(deltaSeconds) || deltaSeconds <= 0) return;
            elapsed = (elapsed + Math.min(deltaSeconds, 0.1)) % CYCLE_SECONDS;
            const turn = sampleTrack(TURN_KEYS, elapsed);
            const dip = sampleTrack(DIP_KEYS, elapsed);
            for (const mesh of drivenMeshes) {
                mesh.influences[mesh.turnLeft] = Math.max(0, turn);
                mesh.influences[mesh.turnRight] = Math.max(0, -turn);
                mesh.influences[mesh.dip] = dip;
            }
        },
        dispose() {
            for (const mesh of drivenMeshes) {
                mesh.influences[mesh.turnLeft] = 0;
                mesh.influences[mesh.turnRight] = 0;
                mesh.influences[mesh.dip] = 0;
            }
            elapsed = 0;
        },
    };
}
