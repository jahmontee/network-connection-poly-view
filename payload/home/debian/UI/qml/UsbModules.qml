import QtQuick 2.9
import QtQuick.Controls 2.3
import QtQuick.Controls.Material 2.3
import QtQuick.Layouts 1.3

Pane {
    id: screen
    width: 1280
    height: 700
    font.pixelSize: 22
    Material.theme: Material.Dark
    Material.accent: "#3ed279"
    palette.windowText: "#eceef3"
    palette.text: "#eceef3"
    palette.buttonText: "#eceef3"
    palette.button: "#292d35"
    palette.base: "#181b22"
    palette.window: "#111216"
    palette.highlight: "#3ed279"
    palette.highlightedText: "#111216"
    background: Rectangle { color: "#111216" }
    property var controller
    property var moduleState: ({usb: [], queue: [], installed: [], revision: ""})
    property string action: ""
    property string message: ""
    property string selected: ""
    property string selectedName: ""
    property string removalToken: ""
    property var affected: []
    property bool restarting: false
    signal closed()

    function request(next, key, confirmation) {
        action = next
        message = ""
        controller.module_usb(next, key || "", moduleState.revision, confirmation || "")
    }
    function ask(next, key, name) {
        selected = key
        selectedName = name
        confirmation.operation = next
        confirmation.open()
    }

    Connections {
        target: screen.controller
        function onModule_usb_result(result) {
            const completed = screen.action
            screen.action = ""
            const reply = JSON.parse(result)
            if (reply.error) {
                if (reply.revision) screen.moduleState = reply
                screen.message = reply.error
            } else if (completed === "review") {
                screen.removalToken = reply.review
                screen.affected = reply.references[screen.selected] || []
                screen.ask("remove", screen.selected, screen.selectedName)
            } else if (reply.restarting) {
                screen.restarting = true
                screen.message = "Applying changes. Hector will restart automatically."
            } else {
                screen.moduleState = reply
                screen.message = reply.working ? "Hector is applying module changes." : ""
            }
        }
    }
    Component.onCompleted: request("list")

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 28
        RowLayout {
            Label { text: "Module Manager"; font.pixelSize: 34; Layout.fillWidth: true }
            Button { text: "Refresh"; enabled: !screen.action; onClicked: screen.request("list") }
            Button { text: "Back"; onClicked: screen.closed() }
        }
        Label {
            Layout.fillWidth: true
            text: screen.message || "Put a signed module ZIP at the top level of the USB drive."
            wrapMode: Text.WordWrap
            font.pixelSize: 22
        }
        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            ColumnLayout {
                width: scroll.availableWidth
                enabled: !screen.action && !screen.moduleState.working && !screen.restarting
                Label { text: "USB drive"; font.pixelSize: 26 }
                Label { visible: !screen.moduleState.usb.length; text: "No module ZIPs found. Insert the drive and tap Refresh." }
                Repeater {
                    model: screen.moduleState.usb
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: modelData; elide: Text.ElideRight; Layout.fillWidth: true }
                        Button { text: "Check ZIP"; onClicked: screen.request("stage", modelData) }
                    }
                }
                Label { text: "Release packages"; font.pixelSize: 26 }
                Repeater {
                    model: screen.moduleState.queue
                    RowLayout {
                        Layout.fillWidth: true
                        Label {
                            Layout.fillWidth: true
                            text: (modelData.name || "AetherScope") + "  " +
                                  (modelData.previous_version ? modelData.previous_version + " → " : "") +
                                  (modelData.version || "test.13")
                        }
                        Button {
                            text: modelData.previous_version ? "Update" : "Install"
                            palette.button: "#3ed279"
                            palette.buttonText: "#111216"
                            Material.background: "#3ed279"
                            Material.foreground: "#111216"
                            onClicked: screen.ask("install", modelData.key, text + " " + (modelData.name || "AetherScope"))
                        }
                        Button {
                            text: "Discard"
                            palette.buttonText: "#ff8b91"
                            Material.foreground: "#ff8b91"
                            onClicked: screen.request("discard", modelData.key)
                        }
                    }
                }
                Label { text: "Installed modules"; font.pixelSize: 26 }
                Repeater {
                    model: screen.moduleState.installed
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text: modelData.name; Layout.fillWidth: true }
                        Button {
                            text: modelData.status === "installed" ? "Remove" : "Undo"
                            visible: !modelData.platform
                            palette.buttonText: "#ff8b91"
                            Material.foreground: "#ff8b91"
                            onClicked: {
                                if (modelData.status !== "installed") {
                                    screen.request("undo", modelData.key)
                                    return
                                }
                                screen.selected = modelData.key
                                screen.selectedName = modelData.name
                                screen.request("review", modelData.key)
                            }
                        }
                    }
                }
            }
        }
        Button {
            text: "Restart Hector"
            enabled: screen.moduleState.restart_required && !screen.action && !screen.moduleState.working && !screen.restarting
            onClicked: screen.ask("restart", "", text)
        }
    }

    Dialog {
        id: confirmation
        property string operation: ""
        modal: true
        x: (screen.width - width) / 2
        y: (screen.height - height) / 2
        width: 620
        title: operation === "remove" ? "Remove " + screen.selectedName + "?" : screen.selectedName + "?"
        ColumnLayout {
            width: parent.width
            Label {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: confirmation.operation === "remove" ? (screen.affected.length ?
                    "Some saved presets use this module and may open with missing connections. The preset files will stay on Hector.\n\n" +
                        screen.affected.slice(0, 8).join("\n") + (screen.affected.length > 8 ? "\nAnd " + (screen.affected.length - 8) + " more." : "") :
                    "No references found in saved presets. Removal takes effect after restarting Hector.") :
                    "Save your patch first. Hector will restart automatically."
            }
            CheckBox { id: saved; visible: confirmation.operation !== "remove"; text: "My current patch is saved" }
            RowLayout {
                Button { text: "Cancel"; onClicked: confirmation.close() }
                Button {
                    text: "Confirm"
                    enabled: confirmation.operation === "remove" || saved.checked
                    onClicked: {
                        const proof = confirmation.operation === "remove" ? screen.removalToken : "saved"
                        confirmation.close()
                        screen.request(confirmation.operation, screen.selected, proof)
                    }
                }
            }
        }
        onOpened: saved.checked = false
    }
}
