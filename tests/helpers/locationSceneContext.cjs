const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const architecture = vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../../app/static/js/locationArchitecture.js'), 'utf8').replaceAll('export ', '') + '\n;({ STOREY_HEIGHT, structureLevelOffset, structurePose })');
// Neutral editor state for unit tests that exercise an isolated scene function.
// History/command integration is covered by location_editor_history tests.
module.exports = function sceneContext(values) {
    return {
        ...architecture,
        currentLocationData: null, editorHeldRotation: null, objectEdit: null, editorPaste: null, eraserMode: false,
        editorHoverLevelOffset: () => 0, locationViewLevel: 0,
        locationSceneLoading: false, locationBuildingViewsDirty: false,
        locationObjectPickCache: { key: null, result: null },
        updateRoofVisibility() {}, updateLocationSculpt() {}, assetSurfacePlacement: () => null, alignFloorRugToSurface() {},
        lampDisplayLevel: () => 0, // These isolated functions operate on the ground floor.
        refreshObjectLightSource() {}, touchStrokeCell() {}, refreshLocationFloorMenu() {},
        runLocationObjectCommand: action => action(),
        editorUpdateObject: (...args) => values.Server.updateLocationObject(...args),
        editorCreateObject: (...args) => values.Server.createLocationObject(...args),
        editorDeleteObject: (...args) => values.Server.deleteLocationObject(...args),
        editorChangeObjects: (...args) => values.Server.changeLocationObjects(...args),
        ...values,
    };
};
