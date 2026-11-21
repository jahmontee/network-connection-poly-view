import QtQuick 2.11
import "../wifi" as Wifi
import "../c64" as C64

Rectangle {
    id: root
    objectName: "network_wifi_page"
    width: 1280; height: 720; color: "#000000"
    property var connection: networkHub.status.wifi || ({})
    property string activityKey: "wifi-" + Math.random().toString(36).substr(2)
    onVisibleChanged: networkHub.activate(activityKey,visible)
    Component.onCompleted: networkHub.activate(activityKey,visible)
    Component.onDestruction: networkHub.activate(activityKey,false)
    C64.Frame { id: frame; anchors.fill: parent }
    Wifi.WifiPage { id: page; x: 64; y: 84; width: 1280; height: 624; scale: .9; transformOrigin: Item.TopLeft }
    Rectangle {
        x: 44; y: 96; width: 1192; height: 530
        visible: !networkHub.status.online || !root.connection.enabled
        color: "#041204"; z: 10
        MouseArea { anchors.fill: parent }
        Text { x: 22; y: 25; text: "WI-FI SETUP"; color: "#8cff73"; font.family: frame.pixelFont; font.pixelSize: 24 }
        Text { x: 22; y: 100; width: 1100; text: !networkHub.status.online ? "Checking Wi-Fi controls…" : "Wi-Fi is off. Turn it on to choose an adapter and set up a network."; color: "#81bc6d"; font.family: frame.keyFont; font.pixelSize: 24; wrapMode: Text.WordWrap }
        C64.Key {
            objectName: "network_wifi_enable_setup"
            x: 22; y: 220; width: 350; height: 64; beige: true; visible: networkHub.status.online
            enabled: !root.connection.busy; text: root.connection.busy ? "UPDATING…" : "TURN WI-FI ON"; font.family: frame.heavyFont; font.pixelSize: 23
            onClicked: networkHub.setEnabled("wifi",true)
        }
    }
    Rectangle { x: 44; y: 638; width: 1192; height: 48; color: "#041204" }
    Wifi.LinkButton {
        objectName: "network_wifi_back"
        x: 48; y: 642; width: 192; height: 43; text: "BACK"; primary: true
        onClicked: { if (!page.goBack()) mainStack.pop() }
    }
    Text { x: 274; y: 642; width: 900; height: 43; text: "WI-FI SETUP"; color: "#81bc6d"; font.family: frame.pixelFont; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter }
}
