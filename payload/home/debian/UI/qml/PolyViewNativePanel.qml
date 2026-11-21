import QtQuick 2.9
import QtQuick.Controls 2.3
import "module_info.js" as ModuleInfo

Item {
    id: panel
    width: 1280
    height: malaFullPanel ? 720 : 700
    readonly property bool malaFullPanel: !!editor.item && editor.item.malaFullPanel === true
    property string effect_id: ""
    property string module_type: ""
    property var backend: polyViewNativeHub.backend(effect_id, module_type)
    Loader {
        id: editor
        anchors.fill: parent
    }
    Label {
        anchors.centerIn: parent
        visible: editor.status === Loader.Error || !panel.backend
        text: "The module editor could not open."
        color: "#eceef3"
        font.pixelSize: 24
    }
    Component.onCompleted: {
        if (backend) {
            editor.setSource(ModuleInfo.effectPrototypes[module_type].poly_view_editor,
                             {"effect_id": effect_id, "backend": backend})
        }
    }
}
