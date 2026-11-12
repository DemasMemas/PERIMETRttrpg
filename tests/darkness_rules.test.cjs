const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../app/static/js/darknessRules.js'), 'utf8');
const url = `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`;

test('world darkness runs from 22:00 through 05:59', async () => {
    const { isWorldDarkTime } = await import(url);
    assert.equal(isWorldDarkTime(21 * 60 + 59), false);
    assert.equal(isWorldDarkTime(22 * 60), true);
    assert.equal(isWorldDarkTime(5 * 60 + 59), true);
    assert.equal(isWorldDarkTime(6 * 60), false);
});

test('Лист учитывает условные модификаторы зрения и стрельбы в темноте', () => {
    const sheet = fs.readFileSync(path.join(__dirname, '../app/static/js/characterSheet.js'), 'utf8');
    assert.match(sheet, /darkness \? 'darkness_awareness' : 'light_awareness'/);
    assert.match(sheet, /awarenessMode === 'visual' \? 'darkness_visual_immunity' : 'darkness_shooting_immunity'/);
});
