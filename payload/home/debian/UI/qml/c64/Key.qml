import QtQuick 2.11
import QtQuick.Templates 2.4 as T

T.Button {
    id: key
    implicitWidth: 160; implicitHeight: 60
    property bool beige: false
    property string secondary: ""
    font.family: "Inter"; font.pixelSize: 20; font.weight: Font.ExtraBold
    opacity: enabled ? 1 : .45
    background: Item {
        property int travel: key.down ? 3 : key.checked ? 2 : 0
        Rectangle { x: 0; y: 5; width: parent.width; height: parent.height-4; radius: 7; color: "#020502" }
        Rectangle {
            x: 0; y: parent.travel; width: parent.width; height: parent.height-parent.travel-2; radius: 6
            color: key.beige ? "#4d473f" : "#211d19"
            border.color: key.visualFocus || key.checked ? "#8cff73" : key.beige ? "#c4b9a9" : "#796a5c"; border.width: key.visualFocus ? 2 : 1
            Rectangle {
                x: 3; y: 2; width: parent.width-6; height: parent.height-9; radius: 4
                gradient: Gradient { GradientStop { position: 0; color: key.beige ? "#b8aea0" : "#665b51" } GradientStop { position: .16; color: key.beige ? "#9b9284" : "#4a423b" } GradientStop { position: 1; color: key.beige ? "#7e7569" : "#302922" } }
                Rectangle { anchors.fill: parent; anchors.margins: 5; radius: 4; color: key.beige ? "#92897b" : "#3e3731"; border.color: key.beige ? "#a59b8d" : "#51483e" }
                Text { objectName: key.objectName + "_caption"; anchors.fill: parent; anchors.margins: 8; text: key.text; textFormat: Text.PlainText; font: key.font; color: key.checked ? "#b4ffa1" : key.beige ? "#211b16" : "#f5ede2"; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                Text { anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 7; text: key.secondary; textFormat: Text.PlainText; font.family: key.font.family; font.pixelSize: 11; color: key.beige ? "#3d3329" : "#b6a791" }
            }
        }
    }
    contentItem: Item { }
    Accessible.name: text + (checkable ? (checked ? " on" : " off") : "")
}
