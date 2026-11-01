const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');

function sheet(data) {
    const container = { innerHTML: '' };
    let saves = 0;
    const context = vm.createContext({
        window: { isGM: true }, currentCharacterData: data,
        document: { getElementById: () => container },
        loadTemplatesForLobby: async () => [], getExoskeletonPowerProfile: () => ({ powered: false }),
        renderSpecialTraits() {}, showNotification() {}, scheduleAutoSave() { saves++; },
    });
    vm.runInContext(source.slice(source.indexOf('function getRequiredXp('), source.indexOf('// ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ')), context);
    vm.runInContext(source.slice(source.indexOf('async function renderSkillsTab('), source.indexOf('function renderSpecialTraits(')), context);
    return { context, container, saves: () => saves, render: () => context.renderSkillsTab(data) };
}

test('цена повышения определяется целевым значением, включая 12→13 за 3 XP', () => {
    const { context } = sheet({});
    for (const [base, expected] of [[5, 1], [10, 1], [11, 2], [12, 3], [13, 4], [19, 10]]) {
        assert.equal(context.getRequiredXp(base), expected);
    }
});

test('отрисованный навык по умолчанию доступен кнопкам всех трёх категорий', async () => {
    const data = {};
    const ui = sheet(data);
    await ui.render();
    for (const skill of ['physical.strength', 'social.charisma', 'other.medicine']) {
        ui.context.window.addSkillXpFromPoints(skill);
        const [category, name] = skill.split('.');
        assert.equal(data.skills[category][name].base, 6);
        assert.equal(data.skills[category][name].xp, 0);
    }
    assert.equal(data.skills.skillPoints, 27);
    assert.equal(ui.saves(), 3);
});

test('двух XP не хватает на 13, третье очко повышает навык без потери остатка', async () => {
    const data = { skills: { skillPoints: 1, physical: { will: { base: 12, bonus: 0, xp: 1 } } } };
    const ui = sheet(data);
    await ui.render();
    ui.context.window.addSkillXpFromUse('physical.will');
    assert.equal(data.skills.physical.will.base, 12);
    assert.equal(data.skills.physical.will.xp, 2);
    ui.context.window.addSkillXpFromPoints('physical.will');
    assert.equal(data.skills.physical.will.base, 13);
    assert.equal(data.skills.physical.will.xp, 0);
    assert.equal(data.skills.skillPoints, 0);
    ui.context.window.addSkillXpFromPoints('physical.will');
    assert.equal(data.skills.physical.will.xp, 0);
    assert.equal(ui.saves(), 2);
});
