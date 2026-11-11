const groupFields = ['skill_physical', 'skill_other'];
const skillFields = {
    strength: 'skill_strength',
    will: 'skill_will',
    awareness: 'skill_awareness',
    stealth: 'skill_stealth',
};

function bonusValue(profile, field) {
    const value = Number(profile?.[field]);
    return Number.isFinite(value) ? value : 0;
}

export function artifactSkillBonus(profile, path) {
    const groupField = path.startsWith('physical.') ? 'skill_physical' : 'skill_other';
    const skillField = skillFields[path.split('.').at(-1)];
    return bonusValue(profile, groupField) + bonusValue(profile, skillField);
}

export function artifactSkillBonusesChanged(previous, next) {
    return [...groupFields, ...Object.values(skillFields)].some(field => bonusValue(previous, field) !== bonusValue(next, field));
}
