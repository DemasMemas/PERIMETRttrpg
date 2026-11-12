export function createLocationContactShadows({ container, renderer, scene, camera }) {
    if (!renderer.capabilities?.isWebGL2 || !renderer.extensions.has('EXT_color_buffer_float')) return null;

    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'scene-profile-button scene-contact-shadows-button';
    button.textContent = 'Контактные тени';
    button.title = 'Экранные контактные тени на подлокации';
    button.setAttribute('aria-pressed', 'false');
    container.append(button);

    let composer;
    let enabled = false;
    let disposed = false;
    const autoClear = renderer.autoClear;

    function reset() {
        enabled = false;
        button.setAttribute('aria-pressed', 'false');
        composer?.dispose();
        composer = null;
        renderer.autoClear = autoClear;
    }

    function fail(error) {
        reset();
        button.title = 'Контактные тени недоступны';
        button.disabled = true;
        console.warn('Не удалось отрисовать контактные тени', error);
    }

    button.onclick = async () => {
        if (enabled) {
            reset();
            return;
        }
        button.disabled = true;
        try {
            const [{ EffectComposer, RenderPass }, { N8AOPostPass }] = await Promise.all([
                import('postprocessing'), import('n8ao'),
            ]);
            if (disposed) return;
            const gl = renderer.getContext();
            composer = new EffectComposer(renderer, { multisampling: Math.min(4, gl.getParameter(gl.SAMPLES)) });
            composer.addPass(new RenderPass(scene, camera));
            const shadows = new N8AOPostPass(scene, camera, 1, 1);
            // В подлокации всегда есть прозрачные материалы дождя, стекла и эффектов.
            shadows.autoDetectTransparency = false;
            shadows.configuration.transparencyAware = true;
            const disposePass = shadows.dispose.bind(shadows);
            shadows.dispose = () => {
                for (const {mask} of shadows.transparencyMaskMaterials?.values() || []) mask.dispose();
                shadows.transparencyMaskMaterials?.clear();
                // Базовый Pass освобождает текстуры, но не внутренние обёртки N8AO.
                for (const quad of ['copyQuad', 'accumulationQuad', 'depthDownsampleQuad',
                    'effectShaderQuad', 'poissonBlurQuad', 'effectCompositerQuad']) {
                    shadows[quad]?.dispose();
                }
                disposePass();
            };
            composer.addPass(shadows);
            shadows.setQualityMode('Low');
            Object.assign(shadows.configuration, {
                halfRes: true,
                aoRadius: 0.8,
                distanceFalloff: 0.8,
                intensity: 1.3,
                gammaCorrection: false,
            });
            enabled = true;
            button.disabled = false;
            button.setAttribute('aria-pressed', 'true');
        } catch (error) {
            if (!disposed) fail(error);
        }
    };

    return {
        render() {
            if (!enabled) return false;
            const { info } = renderer;
            const autoReset = info.autoReset;
            info.reset();
            info.autoReset = false;
            try {
                composer.render();
                return true;
            } catch (error) {
                fail(error);
                return false;
            } finally {
                info.autoReset = autoReset;
            }
        },
        resize(width, height) { composer?.setSize(width, height, false); },
        dispose() {
            disposed = true;
            reset();
            button.remove();
        },
    };
}
