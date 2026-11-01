// Удержание не зависит от автоповтора ОС; короткое нажатие остаётся у вызывающего кода.
export function createRotationHold({ now, requestFrame, cancelFrame, allowed = () => true }) {
    let held = null, frame = null;
    const stop = (commit = false) => {
        const previous = held;
        if (frame !== null) cancelFrame(frame);
        frame = null;
        held = null;
        previous?.finish?.(commit === true);
    };
    const tick = time => {
        frame = null;
        const state = held;
        if (!state || !allowed() || state.context() !== state.identity) { stop(); return; }
        const elapsed = Math.max(0, Math.min(50, time - Math.max(state.last, state.begin + 180)));
        state.last = time;
        if (elapsed && state.rotate(state.direction, elapsed * 90 / 1000 / 15) === false) { stop(); return; }
        if (held === state) frame = requestFrame(tick);
    };
    return {
        start(event, rotate, context, finish) {
            if (event.repeat || held?.code === event.code) return;
            stop(true);
            const identity = context();
            if (identity == null) return;
            const begin = now();
            held = { code: event.code, direction: event.code === 'KeyE' ? 1 : -1,
                rotate, context, identity, begin, last: begin, finish };
            frame = requestFrame(tick);
        },
        release(code) { if (held?.code === code) stop(true); },
        stop,
    };
}

export function initMapRotationHold(isTypingTarget) {
    const hold = createRotationHold({ now: () => performance.now(),
        requestFrame: callback => requestAnimationFrame(callback),
        cancelFrame: id => cancelAnimationFrame(id),
        allowed: () => !document.hidden && !isTypingTarget(document.activeElement)
            && !Array.from(document.querySelectorAll('.modal')).some(modal => modal.getClientRects().length),
    });
    window.startMapRotationHold = hold.start;
    window.stopMapRotationHold = hold.stop;
    window.addEventListener('keyup', event => hold.release(event.code), true);
    window.addEventListener('keydown', event => {
        if (!['KeyQ', 'KeyE'].includes(event.code) || event.ctrlKey || event.altKey || event.metaKey) hold.stop();
    }, true);
    window.addEventListener('blur', hold.stop);
    window.addEventListener('pagehide', hold.stop);
    document.addEventListener('visibilitychange', hold.stop);
    document.addEventListener('focusin', hold.stop);
    document.addEventListener('pointerdown', hold.stop, true);
    return hold;
}
