function releaseContext(context) {
    try {
        context?.getExtension('WEBGL_lose_context')?.loseContext();
    } catch (error) {
        // Some browsers expose WebGL but block context management.
    }
}

export function createCompatibleWebGLRenderer(THREE, options = {}) {
    const attempts = [
        { type: 'webgl2', antialias: options.antialias !== false },
        { type: 'webgl2', antialias: false, safe: true },
        { type: 'webgl', antialias: options.antialias !== false },
        { type: 'experimental-webgl', antialias: false, safe: true },
    ];

    for (const attempt of attempts) {
        const canvas = document.createElement('canvas');
        let context = null;
        try {
            context = canvas.getContext(attempt.type, {
                alpha: options.alpha !== false,
                antialias: attempt.antialias,
                depth: true,
                stencil: true,
                failIfMajorPerformanceCaveat: false,
                powerPreference: 'default',
                preserveDrawingBuffer: false,
            });
            if (!context) continue;
            const renderer = new THREE.WebGLRenderer({
                ...options,
                canvas,
                context,
                antialias: attempt.antialias,
                logarithmicDepthBuffer: attempt.safe
                    ? false
                    : Boolean(options.logarithmicDepthBuffer),
                failIfMajorPerformanceCaveat: false,
                powerPreference: 'default',
            });
            renderer.outputColorSpace = THREE.LinearSRGBColorSpace;
            renderer.useLegacyLights = true;
            return renderer;
        } catch (error) {
            releaseContext(context);
        }
    }
    return null;
}

export function createUnavailableRenderer() {
    const canvas = document.createElement('canvas');
    canvas.className = 'webgl-unavailable-canvas';
    canvas.style.cssText = 'display:block; width:100%; height:100%; background:#080b0c;';
    return {
        isUnavailableRenderer: true,
        domElement: canvas,
        shadowMap: { enabled: false, type: null },
        setSize(width, height) {
            canvas.width = Math.max(1, Number(width) || 1);
            canvas.height = Math.max(1, Number(height) || 1);
            canvas.style.width = `${canvas.width}px`;
            canvas.style.height = `${canvas.height}px`;
        },
        setPixelRatio() {},
        render() {},
        dispose() {},
    };
}

export function showWebGLUnavailable(container) {
    if (!container || container.querySelector(':scope > .webgl-unavailable-message')) return;
    const message = document.createElement('div');
    message.className = 'webgl-unavailable-message';
    message.style.cssText = `
        position:absolute;
        inset:0;
        z-index:5;
        display:flex;
        align-items:center;
        justify-content:center;
        padding:24px;
        background:
            radial-gradient(circle at 70% 20%, rgba(108,108,78,.12), transparent 36%),
            #080b0c;
        color:#d8d3c2;
        font-family:Arial, sans-serif;
    `;
    message.innerHTML = `
        <div style="width:min(620px, 100%); padding:22px; border:1px solid #555447; background:rgba(18,21,20,.96); box-shadow:0 18px 50px rgba(0,0,0,.5);">
            <div style="font-size:20px; font-weight:700; color:#e2d59d;">Не удалось запустить 3D-карту</div>
            <p style="margin:12px 0 8px; line-height:1.5;">
                Браузер не разрешил создать WebGL-контекст. Соединение с сервером работает,
                но для карты требуется WebGL.
            </p>
            <ol style="margin:8px 0 0; padding-left:22px; line-height:1.6;">
                <li>Включите аппаратное ускорение в настройках браузера и полностью перезапустите его.</li>
                <li>Обновите драйвер видеокарты и попробуйте Chrome, Edge или Firefox.</li>
                <li>В Chrome/Edge откройте <strong>chrome://gpu</strong> и проверьте, что WebGL не отмечен как Disabled.</li>
                <li>Если используется удалённый рабочий стол или виртуальная машина, запустите браузер непосредственно на компьютере.</li>
            </ol>
            <button type="button" style="margin-top:16px; padding:9px 14px; border:1px solid #77725a; background:#302f27; color:#eee7ce; cursor:pointer;">
                Повторить после перезапуска
            </button>
        </div>
    `;
    message.querySelector('button').onclick = () => window.location.reload();
    container.appendChild(message);
}
