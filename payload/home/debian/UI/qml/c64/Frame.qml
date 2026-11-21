import QtQuick 2.11

Item {
    id: frame
    width: 1280; height: 720
    property string badge: "NETWORK CONNECTION"
    readonly property string pixelFont: pixelTypeface.name
    readonly property string keyFont: interTypeface.name
    readonly property string heavyFont: heavyTypeface.name
    FontLoader { id: pixelTypeface; objectName: "network_pixel_font"; source: "file:///opt/hector-storage-manager/app/static/fonts/PressStart2P-Regular.ttf" }
    FontLoader { id: interTypeface; objectName: "network_key_font"; source: "file:///opt/hector-storage-manager/app/static/fonts/Inter-700.ttf" }
    FontLoader { id: heavyTypeface; objectName: "network_heavy_font"; source: "file:///opt/hector-storage-manager/app/static/fonts/Inter-800.ttf" }
    Rectangle { anchors.fill: parent; color: "#17130f" }
    Rectangle {
        x: 10; y: 10; width: 1260; height: 700; radius: 18
        border.color: "#d2c3af"; border.width: 2
        gradient: Gradient { GradientStop { position: 0; color: "#c7b8a5" } GradientStop { position: .07; color: "#a79682" } GradientStop { position: .95; color: "#8d7c69" } GradientStop { position: 1; color: "#5f5143" } }
    }
    Rectangle {
        x: 30; y: 25; width: 352; height: 30; radius: 4; color: "#262019"; border.color: "#635344"
        Column { x: 9; y: 7; Repeater { model: ["#d9383a","#e87a24","#e8c524","#49b649","#3676c8"]; Rectangle { width: 16; height: 3; color: modelData } } }
        Text { x: 36; anchors.verticalCenter: parent.verticalCenter; text: frame.badge; textFormat: Text.PlainText; color: "#e8e0d5"; font.family: frame.heavyFont; font.pixelSize: 14; font.weight: Font.ExtraBold; font.letterSpacing: 1.5 }
    }
    Row { x: 470; y: 36; spacing: 8; Repeater { model: 10; Rectangle { width: 38; height: 7; radius: 3; color: "#534536"; border.color: "#c3af95" } } }
    Rectangle { x: 1160; y: 31; width: 11; height: 11; radius: 6; color: "#ec3930"; border.color: "#6d130c" }
    Text { x: 1180; y: 29; text: "POWER"; color: "#392d20"; font.family: frame.heavyFont; font.pixelSize: 12; font.weight: Font.ExtraBold; font.letterSpacing: 1 }
    Rectangle { x: 24; y: 76; width: 1232; height: 620; radius: 14; color: "#211b16"; border.color: "#5e4c3b"; border.width: 3 }
    Rectangle { x: 32; y: 84; width: 1216; height: 604; radius: 9; color: "#041204"; border.color: "#224b1c"; border.width: 2 }
    Text {
        objectName: "network_installed_version"
        anchors.right: parent.right; anchors.rightMargin: 38
        anchors.bottom: parent.bottom; anchors.bottomMargin: 13
        text: "v" + networkHub.installedVersion; textFormat: Text.PlainText
        color: "#e8e0d5"; font.family: frame.pixelFont; font.pixelSize: 11
    }
}
