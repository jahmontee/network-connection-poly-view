import QtQuick 2.11
import "../c64" as C64

Rectangle {
    id: utility
    objectName: "ethernet_page"
    width: 1280; height: 720; color: "#041204"
    C64.Frame { id: frame; anchors.fill: parent }
    property var state: ethernetHub.status
    property bool confirmingOff: false
    Component.onCompleted: ethernetHub.activate(true)
    Component.onDestruction: ethernetHub.activate(false)
        Text {
            x: 58; y: 104; text: "ETHERNET"; color: "#8cff73"; font.family: frame.pixelFont; font.pixelSize: 25
        }
        Text { font.family: frame.keyFont;
            x: 58; y: 147; text: "Connect automatically when Ethernet is on."; color: "#81bc6d"; font.pixelSize: 24
        }
        Row {
            x: 920; y: 103; spacing: 12
            Repeater {
                model: ["Off", "On"]
                C64.Key {
                    objectName: index === 0 ? "ethernet_off" : "ethernet_on"
                    width: 144; height: 60
                    property bool selected: utility.state.enabled === (index === 1)
                    property bool available: utility.state.online && !utility.state.busy
                    enabled: available; checked: selected; text: modelData.toUpperCase(); font.family: frame.heavyFont; font.pixelSize: 22
                    onClicked: {
                            if (index === 0 && utility.state.enabled) utility.confirmingOff = true
                            else if (index === 1) ethernetHub.setEnabled(true)
                    }
                }
            }
        }
        Text { font.family: frame.keyFont;
            x: 58; y: 184; width: 1148; height: 32
            text: !utility.state.online ? "Waiting for Ethernet controls…" : utility.state.busy ? "Updating Ethernet…" : utility.state.enabled ? "Ethernet is on" : "Ethernet is off"
            color: !utility.state.online ? "#e1c77a" : utility.state.enabled ? "#8cff73" : "#81bc6d"; font.pixelSize: 24
        }
        Flickable {
            x: 46; y: 228; width: 1188; height: 318
            contentWidth: width; contentHeight: cards.height; clip: true
            Column {
                id: cards; width: 1188; spacing: 14
                Repeater {
                    model: utility.state.adapters || []
                    Rectangle {
                        width: 1188; height: 226; color: "#061806"; radius: 6; border.color: "#256b21"
                        Text { font.family: frame.keyFont; x: 24; y: 20; width: 820; text: modelData.name; elide: Text.ElideRight; color: "#8cff73"; font.pixelSize: 29; font.bold: true }
                        Text { font.family: frame.pixelFont; x: 830; y: 28; width: 334; text: modelData.status; textFormat: Text.PlainText; horizontalAlignment: Text.AlignRight; elide: Text.ElideRight; color: modelData.status === "Connected" ? "#8cff73" : "#81bc6d"; font.pixelSize: 14 }
                        Text { font.family: frame.keyFont; x: 24; y: 80; text: "IP address"; color: "#81bc6d"; font.pixelSize: 24 }
                        Text { font.family: frame.pixelFont; objectName: "ethernet_ip"; x: 220; y: 86; width: 944; text: utility.state.online ? (modelData.ip || "—") : "Unavailable"; textFormat: Text.PlainText; color: "#8cff73"; font.pixelSize: 19; elide: Text.ElideRight }
                        Text { font.family: frame.keyFont; x: 24; y: 131; text: "MAC address"; color: "#81bc6d"; font.pixelSize: 24 }
                        Text { font.family: frame.pixelFont; objectName: "ethernet_mac"; x: 220; y: 137; width: 944; text: modelData.mac || "—"; textFormat: Text.PlainText; color: "#8cff73"; font.pixelSize: 19; elide: Text.ElideRight }
                    }
                }
                Text { font.family: frame.keyFont;
                    visible: !(utility.state.adapters || []).length; x: 12; width: 1180
                    text: utility.state.online ? "No supported USB Ethernet adapter detected.\nConnect the hub or a supported Ethernet adapter." : "Checking the Ethernet adapter…"
                    color: "#81bc6d"; font.pixelSize: 28; lineHeight: 1.5
                }
            }
        }
        Text { font.family: frame.keyFont;
            x: 58; y: 567; width: 1148; height: 42; wrapMode: Text.WordWrap
            text: utility.state.error || "Ethernet Off disconnects wired networking, including Link and remote access."
            color: utility.state.error ? "#e1c77a" : "#81bc6d"; font.pixelSize: 22
        }
        C64.Key { objectName: "ethernet_back"; x: 48; y: 642; width: 230; height: 43; beige: true; text: "BACK"; font.family: frame.heavyFont; onClicked: { utility.confirmingOff=false;mainStack.pop() } }
        Text { font.family: frame.keyFont; x: 312; y: 653; text: "Network settings are independent of your patch."; color: "#81bc6d"; font.pixelSize: 18 }
        Rectangle {
            anchors.fill: parent; visible: utility.confirmingOff; color: "#e8000000"; z: 10
            MouseArea { anchors.fill: parent }
            Rectangle {
                x: 220; y: 180; width: 840; height: 350; radius: 18; color: "#3e3731"; border.color: "#796a5c"
                Text { font.family: frame.keyFont; x: 34; y: 28; text: "Turn Ethernet off?"; color: "#8cff73"; font.pixelSize: 34; font.bold: true }
                Text { font.family: frame.keyFont; x: 34; y: 92; width: 760; text: "Wired Link traffic and remote access will disconnect.\nYou can turn Ethernet back on here on this device."; color: "#81bc6d"; font.pixelSize: 25; lineHeight: 1.35 }
                Row {
                    x: 34; y: 242; spacing: 28
                    Repeater {
                        model: ["Keep on", "Turn off"]
                        Rectangle {
                            objectName: index === 0 ? "ethernet_cancel_off" : "ethernet_confirm_off"
                            width: 370; height: 72; radius: 12; color: index === 0 ? "#282828" : "#e1c77a"
                            Text { font.family: frame.keyFont; anchors.centerIn: parent; text: modelData; color: index === 0 ? "#8cff73" : "#041204"; font.pixelSize: 28; font.bold: true }
                            MouseArea { anchors.fill: parent; onClicked: { utility.confirmingOff = false; if (index === 1) ethernetHub.setEnabled(false) } }
                        }
                    }
                }
            }
        }
}
