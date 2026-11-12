const pending = new WeakMap();
let motion;

export function animatePanelEntry(panel) {
    if (!panel || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    pending.get(panel)?.animation?.cancel();
    const entry = {};
    pending.set(panel, entry);
    motion ??= import('/static/vendor/motion-12.23.24/dist/motion.js')
        .then(() => globalThis.Motion).catch(() => null);
    void motion.then(engine => {
        if (!engine || pending.get(panel) !== entry || !panel.isConnected || panel.hidden) return;
        entry.animation = engine.animate(panel, { opacity: [0.78, 1] }, { duration: 0.14, ease: 'easeOut' });
    });
}
