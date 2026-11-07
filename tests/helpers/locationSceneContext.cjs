const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const architecture = vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/locationArchitecture.js'), 'utf8').replaceAll('export ', '') + '\n;({ STOREY_HEIGHT, structureLevelOffset, structurePose })');
const objectNames = vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, '../../app/static/js/locationAssetCatalog.js'), 'utf8').replaceAll('export ', '')
    + '\n' + fs.readFileSync(path.join(__dirname, '../../app/static/js/locationAssetVariants.js'), 'utf8')
        .replace(/^import .*;$/m, '').replaceAll('export ', '') + '\n;({locationObjectLabel})');
// Neutral editor state for unit tests that exercise an isolated scene function.
// History/command integration is covered by location_editor_history tests.
module.exports = function sceneContext(values) {
    const browserWindow=values.window||{};
    if(!browserWindow.dispatchEvent)browserWindow.dispatchEvent=()=>{};
    return {
        ...architecture,
        ...objectNames,
        currentLocationData: null, editorHeldRotation: null, objectEdit: null, editorPaste: null, eraserMode: false,
        editorHoverLevelOffset: () => 0, locationViewLevel: 0,
        pendingFacingSelection: null, facingPreviewPointer: null, pendingCharacterPlacement: null,
        window: {dispatchEvent() {}}, Event: class {},
        locationSceneLoading: false, locationBuildingViewsDirty: false,
        locationObjectPickCache: { key: null, result: null },
        updateRoofVisibility() {}, updateLocationSculpt() {}, assetSurfacePlacement: () => null, alignFloorRugToSurface() {},
        refreshSniperCoverMarks() {},
        updateAnomalyLabelVisibility() {},
        lampDisplayLevel: () => 0, // These isolated functions operate on the ground floor.
        refreshObjectLightSource() {}, touchStrokeCell() {}, refreshLocationFloorMenu() {},
        runLocationObjectCommand: action => action(),
        editorUpdateObject: (...args) => values.Server.updateLocationObject(...args),
        editorCreateObject: (...args) => values.Server.createLocationObject(...args),
        editorDeleteObject: (...args) => values.Server.deleteLocationObject(...args),
        editorChangeObjects: (...args) => values.Server.changeLocationObjects(...args),
        ...values,
        window: browserWindow,
    };
};
