const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../app/static/js/chatPanel.js'), 'utf8');

function harness(saved) {
    const values = new Map();
    values.set('user_id', '42');
    const key = 'chat-panel:19:42';
    if (saved) values.set(key, JSON.stringify(saved));
    const localStorage = { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) };
    const element = () => {
        const classes = new Set(), handlers = {}, attrs = {};
        return { dataset: {}, style: {}, handlers, attrs,
            classList: { contains: name => classes.has(name), toggle(name, enabled) { if (enabled) classes.add(name); else classes.delete(name); } },
            addEventListener(name, handler) { handlers[name] = handler; },
            setAttribute(name, value) { attrs[name] = value; }, focus() { this.focused = true; },
        };
    };
    const panel = element(); panel.offsetWidth = 340; panel.offsetHeight = 350;
    const collapse = element(), messages = {scrollTop:0,scrollHeight:1200};
    panel.querySelector = selector => selector === '.chat-messages' ? messages : collapse;
    panel.getBoundingClientRect = () => ({ left: parseFloat(panel.style.left) || 0, top: parseFloat(panel.style.top) || 0 });
    const toggle = element(), pin = element(), close = element(), actions = element();
    const documentHandlers = {}, windowHandlers = {};
    let observer;
    const frames=[];
    const context = vm.createContext({ localStorage,requestAnimationFrame:callback=>frames.push(callback),
        document: { getElementById: id => ({ 'panel-chat': panel, 'chat-toggle': toggle, 'actions-toggle': actions, 'chat-pin': pin, 'chat-close': close })[id],
            addEventListener: (event, handler) => { documentHandlers[event] = handler; } },
        window: { currentLobbyId: 19, innerWidth: 1440, innerHeight: 900, addEventListener: (event, handler) => { windowHandlers[event] = handler; } },
        MutationObserver: class { constructor(callback) { observer = callback; } observe() {} },
    });
    vm.runInContext(source.replaceAll('export ', ''), context);
    return { context, panel, toggle, pin, close, actions, collapse, messages,flushFrames:()=>{while(frames.length)frames.shift()();},documentHandlers, windowHandlers,
        observe: () => observer(), state: () => JSON.parse(values.get(key)) };
}

test('chat separates human messages, dice commands, skill rolls and system/game events', () => {
    const { context } = harness();
    for (const [username, text, expected] of [
        ['Player', 'Привет', 'human'], ['Player', '/roll 2d6: 7', 'roll'],
        ['System (Roll)', 'Проверка навыка', 'roll'], ['System', 'Ошибка броска', 'event'],
        ['Бой', 'Начался раунд', 'event'], ['Событие', 'Событие путешествия', 'event'],
        ['Система', 'Ремонт завершён', 'event'], ['Аномалия', 'Получен урон', 'event'], ['Systematic', 'A player name', 'human'],
    ]) assert.equal(context.chatMessageKind(username, text), expected);
});

test('История действий и чат открываются отдельно, режим сохраняется после перезагрузки',()=>{
    const h=harness();h.context.initChatPanel();h.actions.handlers.click();
    assert.equal(h.panel.hidden,false);assert.equal(h.panel.dataset.chatView,'actions');
    assert.equal(h.actions.attrs['aria-expanded'],'true');
    h.toggle.handlers.click();assert.equal(h.panel.hidden,false);assert.equal(h.panel.dataset.chatView,'all');
    h.actions.handlers.click();h.context.initChatPanel();h.windowHandlers.pagehide();
    const restored=harness(h.state());restored.context.initChatPanel();
    assert.equal(restored.panel.dataset.chatView,'actions');assert.equal(restored.panel.hidden,false);
    restored.actions.handlers.click();assert.equal(restored.panel.hidden,true);
});

test('Повторная доставка использует идентификатор события, разные реальные сообщения не склеиваются',()=>{
    const {context}=harness();
    assert.equal(context.chatMessageIdentity('Бой','Выстрел','t',12),'id:12');
    assert.notEqual(context.chatMessageIdentity('Бой','Выстрел','t',12),context.chatMessageIdentity('Бой','Выстрел','t',13));
    assert.equal(context.chatMessageIdentity('Бой','Выстрел','t'),context.chatMessageIdentity('Бой','Выстрел','t'));
    assert.equal(context.chatMessageIdentity('Бой','Выстрел',null),null);
});

test('открытие и разворачивание чата прокручивает к последним сообщениям после раскладки',()=>{
    const h=harness();h.context.initChatPanel();h.toggle.handlers.click();
    assert.equal(h.messages.scrollTop,0);h.flushFrames();assert.equal(h.messages.scrollTop,1200);
    h.messages.scrollTop=0;h.close.handlers.click();h.toggle.handlers.click();h.flushFrames();
    assert.equal(h.messages.scrollTop,1200);
    h.messages.scrollTop=0;h.panel.classList.toggle('collapsed',true);h.observe();
    h.panel.classList.toggle('collapsed',false);h.observe();h.flushFrames();
    assert.equal(h.messages.scrollTop,1200);
});

test('chat dock, close and reopen retain floating position without reinitializing controls', () => {
    const h = harness(); h.context.initChatPanel();
    assert.equal(h.panel.hidden, true);
    assert.equal(h.panel.dataset.chatDocked, 'true');
    h.toggle.handlers.click();
    assert.equal(h.panel.hidden, false); assert.equal(h.toggle.attrs['aria-expanded'], 'true');
    h.pin.handlers.click(); assert.equal(h.panel.dataset.chatDocked, 'false');
    h.panel.style.left = '170px'; h.panel.style.top = '140px'; h.documentHandlers.mouseup();
    h.pin.handlers.click(); assert.equal(h.panel.dataset.chatDocked, 'true');
    h.pin.handlers.click(); assert.equal(h.panel.style.left, '170px'); assert.equal(h.panel.style.top, '140px');
    h.close.handlers.click(); assert.equal(h.panel.hidden, true); assert.equal(h.toggle.focused, true);
    h.toggle.handlers.click(); assert.equal(h.panel.style.left, '170px');
    const handler = h.pin.handlers.click; h.context.initChatPanel(); assert.equal(h.pin.handlers.click, handler);
    assert.deepEqual(h.state().floatingPosition, { left: 170, top: 140 });
});

test('reload restores mode, collapse and bounded floating position; bad preferences use defaults', () => {
    const h = harness({ open: true, docked: false, collapsed: true, floatingPosition: { left: 9999, top: 9999 } });
    h.context.initChatPanel();
    assert.equal(h.panel.hidden, false); assert.equal(h.panel.classList.contains('collapsed'), true);
    assert.equal(h.panel.style.left, '1100px'); assert.equal(h.panel.style.top, '550px');
    h.panel.classList.toggle('collapsed', false); h.observe(); assert.equal(h.state().collapsed, false);
    const storage = { getItem: () => '{' };
    assert.equal(h.context.readChatPanelState(storage, 'unused').docked, true);
    assert.equal(h.context.readChatPanelState(storage, 'unused').open, false);
});

test('closing through either launcher or cross stays closed after reload', () => {
    for (const closeControl of ['toggle', 'close']) {
        const h = harness({ open: true, docked: false, collapsed: true, floatingPosition: { left: 170, top: 140 } });
        h.context.initChatPanel();
        h[closeControl].handlers.click();
        h.windowHandlers.pagehide();
        const restored = harness(h.state()); restored.context.initChatPanel();
        assert.equal(restored.panel.hidden, true);
        assert.equal(restored.toggle.attrs['aria-expanded'], 'false');
        restored.toggle.handlers.click();
        assert.equal(restored.panel.classList.contains('collapsed'), true);
        assert.equal(restored.collapse.textContent, '▶');
        assert.equal(restored.panel.style.left, '170px');
    }
});
