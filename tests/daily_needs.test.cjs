const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');
const start = source.indexOf('function normalizeDailyNeeds(');
const end = source.indexOf('\nconst BASE_HEALTH_MAXIMUMS', start);
assert.ok(start >= 0 && end > start);
const normalize = vm.runInNewContext(source.slice(start, end) + '\nnormalizeDailyNeeds');
const plain = value => JSON.parse(JSON.stringify(value));

test('лист сохраняет серии неудовлетворённых потребностей после открытия и повторной загрузки', () => {
    const needs = {
        day: 4, mealsToday: 2, drinksToday: 3, sleptToday: true,
        missedDays: { food: 3, water: 0, sleep: 1 },
        lastDay: { day: 3, missed: ['еда', 'сон'] },
    };
    assert.deepEqual(plain(normalize(needs)), needs);
    assert.deepEqual(plain(normalize(plain(normalize(needs)))), needs);
});

test('старые сохранения получают нулевые серии, некорректные числа не проходят нормализацию', () => {
    assert.deepEqual(plain(normalize({}).missedDays), { food: 0, water: 0, sleep: 0 });
    assert.deepEqual(plain(normalize({ missedDays: {
        food: '2', water: -3, sleep: Infinity,
    } }).missedDays), { food: 2, water: 0, sleep: 0 });
});
