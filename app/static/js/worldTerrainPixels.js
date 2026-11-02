// Ограниченный пул только на начальную подготовку; после неё все потоки закрыты.
export async function prepareWorldTerrainPixelImages(tasks, { signal, groundPixels, onResult,
    onProgress, onStarted, concurrency = Math.min(4, Math.max(1, Math.floor((globalThis.navigator?.hardwareConcurrency || 2) / 2))),
    createWorker = () => new Worker(new URL('./worldTerrainPixelsWorker.js', import.meta.url), { type: 'module' }),
} = {}) {
    if (!tasks.length || signal?.aborted) return;
    const count = Math.max(1, Math.min(4, tasks.length, Math.floor(Number(concurrency) || 1)));
    const workers = [];
    let completed = 0, started = 0, settled = false, finish, reject;
    const done = new Promise((resolve, fail) => { finish = resolve; reject = fail; });
    const abort = () => { settled = true; finish(); };
    let timeout;
    try {
        signal?.addEventListener('abort', abort, { once: true });
        const dispatch = (worker, workerIndex) => {
            const jobs = [];
            worker.pendingIndices = new Set();
            for (let index = workerIndex; index < tasks.length; index += count) {
                jobs.push({ index, task: tasks[index] });
                worker.pendingIndices.add(index);
            }
            // Поток продолжает расчёт всего пакета, пока основной поток готовит шейдеры.
            worker.postMessage({ jobs });
        };
        const fail = error => {
            if (settled) return;
            settled = true;
            reject(error instanceof Error ? error : new Error('Не удалось подготовить текстуры карты'));
        };
        const armTimeout = () => {
            clearTimeout(timeout);
            timeout = setTimeout(() => fail(new Error('Истекло время подготовки текстур карты')), 30000);
        };
        armTimeout();
        for (let i = 0; i < count; i++) {
            const worker = createWorker();
            workers.push(worker);
            worker.onmessage = ({ data }) => {
                if (settled) return;
                armTimeout();
                try {
                    if (data.error) { fail(new Error(data.error)); return; }
                    if (data.ready) {
                        dispatch(worker, i);
                        if (++started === count) onStarted?.();
                        return;
                    }
                    if (!worker.pendingIndices.has(data.index)) { fail(new Error('Неверный ответ подготовки текстуры')); return; }
                    worker.pendingIndices.delete(data.index);
                    onResult?.(tasks[data.index], data);
                    completed++;
                    onProgress?.(completed / tasks.length);
                    if (completed === tasks.length) { settled = true; finish(); }
                } catch (error) { fail(error); }
            };
            worker.onerror = fail;
            worker.onmessageerror = fail;
            worker.postMessage({ initialize: true, groundPixels });
        }
        await done;
    } finally {
        settled = true;
        clearTimeout(timeout);
        signal?.removeEventListener('abort', abort);
        workers.forEach(worker => worker.terminate());
    }
}
