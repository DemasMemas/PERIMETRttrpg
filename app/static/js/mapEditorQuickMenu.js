import { isTypingTarget } from './cameraPan.js';

const WORLD_TOOLS = [
    ['select', 'Выбор'],
    ['terrain', 'Ландшафт'],
    ['height', 'Высота'],
    ['radiation', 'Радиация'],
    ['erase', 'Ластик'],
    ['objects', 'Объекты'],
];

const LOCATION_TOOLS = [
    ['select', 'Выбор'],
    ['terrain', 'Ландшафт'],
    ['height', 'Высота'],
    ['radiation', 'Радиация'],
    ['erase', 'Ластик'],
    ['assets', 'Ассеты'],
    ['building', 'Строительство'],
    ['light', 'Свет'],
    ['settings', 'Настройки'],
];

const SVG_NS = 'http://www.w3.org/2000/svg';
const SIZE = 352;
const CENTER = SIZE / 2;
const OUTER_RADIUS = 166;
const INNER_RADIUS = 48;
const LABEL_RADIUS = 111;
const SECTOR_GAP = 0.018;
const FULL_CIRCLE = Math.PI * 2;

let quickMenu = null;
let menuData = null;
let highlightedIndex = null;
let qHeld = false;

function pointAt(radius, angle) {
    return [CENTER + Math.cos(angle) * radius, CENTER + Math.sin(angle) * radius];
}

function sectorPath(start, end) {
    const [outerStartX, outerStartY] = pointAt(OUTER_RADIUS, start);
    const [outerEndX, outerEndY] = pointAt(OUTER_RADIUS, end);
    const [innerEndX, innerEndY] = pointAt(INNER_RADIUS, end);
    const [innerStartX, innerStartY] = pointAt(INNER_RADIUS, start);
    return `M ${outerStartX} ${outerStartY} A ${OUTER_RADIUS} ${OUTER_RADIUS} 0 0 1 ${outerEndX} ${outerEndY}`
        + ` L ${innerEndX} ${innerEndY} A ${INNER_RADIUS} ${INNER_RADIUS} 0 0 0 ${innerStartX} ${innerStartY} Z`;
}

export function closeMapEditorQuickMenu() {
    quickMenu?.remove();
    quickMenu = null;
    menuData = null;
    highlightedIndex = null;
    qHeld = false;
}

export function initMapEditorQuickMenu({ isEnabled, isLocation, selectTool, locationAction = () => {} }) {
    let pointerX = window.innerWidth / 2;
    let pointerY = window.innerHeight / 2;

    const toolsForScene = () => isLocation() ? LOCATION_TOOLS : WORLD_TOOLS;

    const setHighlight = index => {
        highlightedIndex = index;
        menuData?.segments.forEach((segment, segmentIndex) => {
            segment.classList.toggle('is-highlighted', segmentIndex === index);
        });
    };

    const highlightAtPointer = (x, y) => {
        if (!menuData) return;
        const dx = x - menuData.centerX;
        const dy = y - menuData.centerY;
        const distance = Math.hypot(dx, dy);
        if (distance < INNER_RADIUS || distance > OUTER_RADIUS) {
            setHighlight(null);
            return;
        }
        const step = FULL_CIRCLE / menuData.tools.length;
        const angle = (Math.atan2(dy, dx) + Math.PI / 2 + step / 2 + FULL_CIRCLE) % FULL_CIRCLE;
        setHighlight(Math.floor(angle / step));
    };

    const openMenu = (held = false) => {
        closeMapEditorQuickMenu();
        const tools = toolsForScene();
        const centerX = Math.max(CENTER, Math.min(window.innerWidth - CENTER, pointerX));
        const centerY = Math.max(CENTER, Math.min(window.innerHeight - CENTER, pointerY));
        const menu = document.createElement('div');
        menu.className = 'map-editor-wheel';
        menu.style.left = `${centerX - CENTER}px`;
        menu.style.top = `${centerY - CENTER}px`;

        const svg = document.createElementNS(SVG_NS, 'svg');
        svg.setAttribute('viewBox', `0 0 ${SIZE} ${SIZE}`);
        svg.setAttribute('role', 'menu');
        svg.setAttribute('aria-label', 'Быстрый выбор инструмента карты');

        const currentTool = isLocation() ? window.locationEditorTool : window.worldEditorTool;
        const segments = tools.map(([tool, label], index) => {
            const step = FULL_CIRCLE / tools.length;
            const angle = -Math.PI / 2 + index * step;
            const segment = document.createElementNS(SVG_NS, 'g');
            segment.classList.add('map-editor-sector');
            if (tool === currentTool) segment.classList.add('is-current');
            segment.setAttribute('role', 'menuitem');
            segment.setAttribute('tabindex', '0');
            segment.setAttribute('aria-label', `${index + 1}: ${label}`);

            const wedge = document.createElementNS(SVG_NS, 'path');
            wedge.setAttribute('d', sectorPath(angle - step / 2 + SECTOR_GAP, angle + step / 2 - SECTOR_GAP));
            segment.appendChild(wedge);

            const [labelX, labelY] = pointAt(LABEL_RADIUS, angle);
            const name = document.createElementNS(SVG_NS, 'text');
            name.classList.add('map-editor-sector-name');
            name.setAttribute('x', String(labelX));
            name.setAttribute('y', String(labelY - 5));
            name.textContent = label;
            segment.appendChild(name);

            const shortcut = document.createElementNS(SVG_NS, 'text');
            shortcut.classList.add('map-editor-sector-key');
            shortcut.setAttribute('x', String(labelX));
            shortcut.setAttribute('y', String(labelY + 13));
            shortcut.textContent = String(index + 1);
            segment.appendChild(shortcut);

            segment.addEventListener('click', () => {
                selectTool(tool);
                closeMapEditorQuickMenu();
            });
            segment.addEventListener('keydown', event => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                event.preventDefault();
                selectTool(tool);
                closeMapEditorQuickMenu();
            });
            svg.appendChild(segment);
            return segment;
        });

        const centerDisc = document.createElementNS(SVG_NS, 'circle');
        centerDisc.classList.add('map-editor-wheel-center');
        centerDisc.setAttribute('cx', String(CENTER));
        centerDisc.setAttribute('cy', String(CENTER));
        centerDisc.setAttribute('r', '43');
        svg.appendChild(centerDisc);
        const centerLabel = document.createElementNS(SVG_NS, 'text');
        centerLabel.classList.add('map-editor-wheel-label');
        centerLabel.setAttribute('x', String(CENTER));
        centerLabel.setAttribute('y', String(CENTER));
        centerLabel.textContent = 'Q';
        svg.appendChild(centerLabel);

        menu.appendChild(svg);
        document.body.appendChild(menu);
        quickMenu = menu;
        menuData = { tools, segments, centerX, centerY };
        qHeld = held;
    };

    document.addEventListener('pointermove', event => {
        pointerX = event.clientX;
        pointerY = event.clientY;
        if (quickMenu) highlightAtPointer(pointerX, pointerY);
    }, { passive: true });

    document.addEventListener('pointerdown', event => {
        if (!quickMenu || quickMenu.contains(event.target)) return;
        closeMapEditorQuickMenu();
        event.preventDefault();
        event.stopImmediatePropagation();
    }, true);

    document.addEventListener('keyup', event => {
        if (event.code !== 'KeyQ' || !qHeld) return;
        const tool = highlightedIndex === null ? null : menuData?.tools[highlightedIndex]?.[0];
        closeMapEditorQuickMenu();
        if (tool) selectTool(tool);
        event.preventDefault();
        event.stopImmediatePropagation();
    }, true);

    document.addEventListener('keydown', event => {
        if (isTypingTarget(event.target)
            || Array.from(document.querySelectorAll('.modal')).some(modal => modal.getClientRects().length)) return;
        if (event.code === 'Escape' && quickMenu) {
            closeMapEditorQuickMenu();
            event.preventDefault();
            event.stopImmediatePropagation();
            return;
        }
        if (!isEnabled() || event.repeat || event.altKey || event.ctrlKey || event.metaKey) return;
        if (['KeyE', 'KeyR'].includes(event.code) && quickMenu) closeMapEditorQuickMenu();
        if (isLocation() && event.code === 'KeyE'
            && event.target.closest?.('dialog.location-building-dialog')) return;

        if (!isLocation() && ['KeyQ', 'KeyE'].includes(event.code)
            && window.rotateWorldObjectPlacement?.(event.code === 'KeyE' ? 1 : -1)) {
            window.startMapRotationHold?.(event,
                (direction, fraction) => window.rotateWorldObjectPlacement(direction, fraction),
                () => window.getWorldObjectRotationContext?.());
            closeMapEditorQuickMenu();
            event.preventDefault();
            event.stopImmediatePropagation();
            return;
        }

        if (isLocation() && ['KeyQ', 'KeyE'].includes(event.code)
            && window.rotateLocationPlacement?.(event.code === 'KeyE' ? 1 : -1)) {
            window.startMapRotationHold?.(event,
                (direction, fraction) => window.rotateLocationPlacement(direction, fraction),
                () => window.getLocationRotationContext?.(),
                commit => window.finishLocationRotationHold?.(commit));
            closeMapEditorQuickMenu();
            event.preventDefault();
            event.stopImmediatePropagation();
            return;
        }
        if (isLocation() && window.isLocationEditorTransformActive?.()
            && (['KeyQ', 'KeyE', 'KeyG', 'KeyR', 'KeyT', 'KeyV', 'KeyB'].includes(event.code)
                || /^(Digit|Numpad)[1-8]$/.test(event.code))) {
            event.preventDefault();
            event.stopImmediatePropagation();
            return;
        }

        if (event.code === 'KeyQ') {
            if (qHeld) return;
            openMenu(true);
        } else if (/^(Digit|Numpad)[1-8]$/.test(event.code)) {
            const tool = toolsForScene()[Number(event.code.slice(-1)) - 1]?.[0];
            if (!tool) return;
            selectTool(tool);
            closeMapEditorQuickMenu();
        } else if (event.code === 'KeyV') {
            selectTool('select');
            closeMapEditorQuickMenu();
        } else if (isLocation() && event.code === 'KeyB') {
            selectTool('assets');
            closeMapEditorQuickMenu();
        } else if (isLocation() && ['KeyG', 'KeyT'].includes(event.code)) {
            locationAction(event.code === 'KeyG' ? 'move' : 'rotate');
            closeMapEditorQuickMenu();
        } else if (isLocation() && event.code === 'KeyR') {
            if (!locationAction('resize')) return;
            closeMapEditorQuickMenu();
        } else {
            return;
        }
        event.preventDefault();
        event.stopImmediatePropagation();
    }, true);
}
