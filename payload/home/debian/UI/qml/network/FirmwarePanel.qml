import QtQuick 2.11
import "../wifi" as Wifi
import "../c64" as C64

Rectangle {
    id: root
    objectName: "network_page"
    width: 1280; height: 720; color: "#000000"
    property var networkStatus: networkHub.status
    readonly property var browserAccess: networkHub.browserAccess
    property string confirming: ""
    property bool editingBrowserPassword: false
    property string wifiAddress: browserAddress("wifi")
    property string ethernetAddress: browserAddress("ethernet")
    property string browserHint: ""

    function privateIPv4(address) {
        var parts = address.trim().split(".")
        if (parts.length !== 4) return false
        for (var i = 0; i < parts.length; i++) {
            if (!/^[0-9]{1,3}$/.test(parts[i]) || Number(parts[i]) > 255) return false
        }
        var first = Number(parts[0]), second = Number(parts[1])
        return first === 10 || (first === 172 && second >= 16 && second <= 31) ||
               (first === 192 && second === 168)
    }

    function browserAddress(transport) {
        if (!networkStatus.online) return ""
        var connection = networkStatus[transport] || ({})
        if (!connection.enabled || connection.status !== "Connected") return ""
        var candidates = transport === "wifi" ? [connection.ip] : (connection.adapters || []).filter(function (adapter) {
            return adapter.status === "Connected"
        }).map(function (adapter) { return adapter.ip })
        for (var i = 0; i < candidates.length; i++) {
            var addresses = (candidates[i] || "").split(",")
            for (var j = 0; j < addresses.length; j++) {
                if (privateIPv4(addresses[j])) return addresses[j].trim()
            }
        }
        return ""
    }

    function browserUrl(address) {
        return "http://" + address + ":" + root.browserAccess.port
    }
    property string activityKey: "overview-" + Math.random().toString(36).substr(2)
    onVisibleChanged: networkHub.activate(activityKey,visible)
    Component.onCompleted: networkHub.activate(activityKey,visible)
    Component.onDestruction: networkHub.activate(activityKey,false)
    C64.Frame { id: frame; anchors.fill: parent }
    Text { x: 58; y: 102; text: "NETWORK CONNECTION"; color: "#8cff73"; font.family: frame.pixelFont; font.pixelSize: 25 }
    Text { x: 58; y: 145; text: "Ethernet, Wi-Fi and your connection to Poly / View."; color: "#81bc6d"; font.family: frame.keyFont; font.pixelSize: 21 }
    Column {
        x: 46; y: 184; spacing: 12
        Repeater {
            model: ["ethernet","wifi"]
            C64.Card {
                id: card
                property string transport: modelData
                property var connection: root.networkStatus[transport] || ({})
                width: 1188; height: 123
                Text { x: 22; y: 16; text: card.transport === "wifi" ? "WI-FI" : "ETHERNET"; font.family: frame.pixelFont; font.pixelSize: 18; color: "#4af626" }
                Text { objectName: "network_" + card.transport + "_status"; x: 22; y: 50; width: 800; text: !root.networkStatus.online ? "Controls unavailable" : card.connection.status || "Checking…"; textFormat: Text.PlainText; font.family: frame.pixelFont; font.pixelSize: 13; color: card.connection.status === "Connected" ? "#8cff73" : "#81bc6d"; elide: Text.ElideRight }
                Text { x: 22; y: 83; width: 800; text: card.connection.error || (card.transport === "wifi" ? card.connection.ssid || "Choose an adapter and set up Wi-Fi." : ((card.connection.adapters || []).length ? card.connection.adapters[0].name : "Connect a supported USB Ethernet adapter.")); textFormat: Text.PlainText; color: card.connection.error ? "#e1c77a" : "#81bc6d"; font.family: frame.keyFont; font.pixelSize: 19; elide: Text.ElideRight }
                Row {
                    x: 850; y: 9; spacing: 10
                    Repeater {
                        model: [false,true]
                        C64.Key {
                            property bool turnOn: modelData
                            objectName: "network_" + card.transport + (turnOn ? "_on" : "_off")
                            width: 151; height: 48; text: turnOn ? "ON" : "OFF"; font.family: frame.heavyFont; font.pixelSize: 21
                            checked: card.connection.enabled === turnOn
                            enabled: root.networkStatus.online && !card.connection.busy && (card.transport === "wifi" || card.connection.online)
                            onClicked: { if (!turnOn && card.connection.enabled) root.confirming=card.transport; else networkHub.setEnabled(card.transport,turnOn) }
                        }
                    }
                }
                C64.Key { objectName: "network_" + card.transport + "_info"; x: 850; y: 66; width: 312; height: 48; beige: true; text: card.transport === "wifi" ? "MORE INFO & SETUP" : "MORE INFO"; font.family: frame.heavyFont; font.pixelSize: 19; onClicked: mainStack.push(Qt.resolvedUrl(card.transport === "wifi" ? "WifiPanel.qml" : "../ethernet/FirmwarePanel.qml")) }
            }
        }
        C64.Card {
            id: browserCard; objectName: "web_browser_connection"
            width: 1188; height: 174
            property bool hasAddress: root.wifiAddress !== "" || root.ethernetAddress !== ""
            property bool twoAddresses: root.wifiAddress !== "" && root.ethernetAddress !== "" && root.wifiAddress !== root.ethernetAddress
            Text { x: 22; y: 16; text: "WEB BROWSER CONNECTION"; color: "#4af626"; font.family: frame.pixelFont; font.pixelSize: 16 }
            Text {
                objectName: "web_browser_address"; x: 22; y: 51; width: 805; elide: Text.ElideRight; textFormat: Text.PlainText
                text: !root.browserAccess.available ? "Storage dashboard is unavailable." : !browserCard.hasAddress ? "Connect to Wi-Fi or Ethernet to get an address." : (root.wifiAddress ? "Wi-Fi · " + root.browserUrl(root.wifiAddress) : "Ethernet · " + root.browserUrl(root.ethernetAddress))
                color: "#8cff73"; font.family: frame.pixelFont; font.pixelSize: browserCard.hasAddress ? 13 : 11
            }
            Text { objectName: "web_browser_detail"; x: 22; y: 80; width: 805; elide: Text.ElideRight; textFormat: Text.PlainText; text: root.browserAccess.available && browserCard.twoAddresses ? "Ethernet · " + root.browserUrl(root.ethernetAddress) : (root.browserHint || "Open the address in a browser on the same network."); color: "#81bc6d"; font.family: frame.keyFont; font.pixelSize: 18 }
            C64.Key { objectName: "web_browser_set_password"; x: 850; y: 32; width: 312; height: 57; beige: true; text: "SET BROWSER PASSWORD"; font.family: frame.heavyFont; font.pixelSize: 19; enabled: root.browserAccess.available && !root.browserAccess.busy; onClicked: root.browserAccess.preparePassword() }
            Rectangle { x: 22; y: 112; width: 1140; height: 1; color: "#23531e" }
            Text { x: 22; y: 130; text: "Remote Desktop"; color: "#a3dc8c"; font.family: frame.keyFont; font.pixelSize: 22 }
            Text { objectName: "remote_desktop_status"; x: 237; y: 136; text: root.browserAccess.remoteDesktopEnabled ? "READY" : "OFF"; color: "#8cff73"; font.family: frame.pixelFont; font.pixelSize: 11 }
            Text { objectName: "remote_desktop_instructions"; x: 345; y: 130; width: 475; text: root.browserAccess.remoteDesktopError || (!root.browserAccess.available ? "Waiting for the dashboard." : root.browserAccess.remoteDesktopEnabled ? "Workspace → Remote Desktop" : !browserCard.hasAddress ? "Connect to enable screen sharing." : "Share the device’s touchscreen."); textFormat: Text.PlainText; font.family: frame.keyFont; font.pixelSize: 17; color: "#81bc6d"; elide: Text.ElideRight }
            C64.Key { objectName: "remote_desktop_toggle"; x: 850; y: 121; width: 312; height: 46; text: root.browserAccess.remoteDesktopEnabled ? "STOP SCREEN SHARING" : "ENABLE SCREEN SHARING"; font.family: frame.heavyFont; font.pixelSize: 18; enabled: root.browserAccess.available && !root.browserAccess.busy && (browserCard.hasAddress || root.browserAccess.remoteDesktopEnabled); onClicked: root.browserAccess.setRemoteDesktopEnabled(!root.browserAccess.remoteDesktopEnabled) }
        }
    }
    C64.Key { objectName: "network_back"; x: 48; y: 642; width: 228; height: 43; beige: true; text: "BACK"; font.family: frame.heavyFont; onClicked: mainStack.pop() }
    Text { x: 312; y: 653; text: "Network settings stay separate from your patch."; color: "#81bc6d"; font.family: frame.keyFont; font.pixelSize: 18 }

    Connections {
        target: root.browserAccess
        function onPasswordPanelReady() {
            root.browserHint = ""
            root.editingBrowserPassword = true
        }
        function onPasswordSaved(message) {
            root.browserHint = message
            root.editingBrowserPassword = false
        }
    }
    Loader {
        anchors.fill: parent; z: 20
        active: root.editingBrowserPassword
        sourceComponent: BrowserPasswordPanel {
            busy: root.browserAccess.busy
            error: root.browserAccess.error
            onCancelled: root.editingBrowserPassword = false
            onSubmitted: function(password) { root.browserAccess.submitPassword(password) }
        }
    }
    Rectangle {
        anchors.fill: parent; visible: root.confirming !== ""; color: "#e8000000"; z: 10
        MouseArea { anchors.fill: parent }
        Rectangle {
            x: 220; y: 190; width: 840; height: 340; radius: 18; color: "#061806"; border.color: "#428731"
            Text { x: 32; y: 28; text: "Turn " + (root.confirming === "wifi" ? "Wi-Fi" : "Ethernet") + " off?"; color: "#8cff73"; font.pixelSize: 34; font.bold: true }
            Text { x: 32; y: 94; width: 770; text: "Devices using this connection will disconnect.\nYou can turn it back on here on this device."; color: "#a3cd91"; font.pixelSize: 25; lineHeight: 1.35 }
            Row {
                x: 32; y: 240; spacing: 28
                Repeater {
                    model: ["Keep on", "Turn off"]
                    Rectangle {
                        objectName: index === 0 ? "network_cancel_off" : "network_confirm_off"
                        width: 370; height: 70; radius: 12; color: index === 0 ? "#282828" : "#ffd645"
                        Text { anchors.centerIn: parent; text: modelData; color: index === 0 ? "white" : "#000000"; font.pixelSize: 28; font.bold: true }
                        MouseArea { anchors.fill: parent; onClicked: { if (index === 1) networkHub.setEnabled(root.confirming,false); root.confirming = "" } }
                    }
                }
            }
        }
    }
}
