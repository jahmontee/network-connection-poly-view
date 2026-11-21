import QtQuick 2.11
import QtQuick.Window 2.3
import QtQuick.Templates 2.4 as T

T.ComboBox {
    id: control
    implicitWidth: 240; implicitHeight: 62
    leftPadding: 18; rightPadding: 52; topPadding: 8; bottomPadding: 8
    font.family: "Inter"; font.pixelSize: 22; font.weight: Font.ExtraBold
    property int bottomClearance: 16
    background: Rectangle {
        color: control.enabled ? "#3e3731" : "#302922"; radius: 10
        border.width: 2; border.color: control.enabled ? "#796a5c" : "#796a5c"
    }
    contentItem: Text {
        text: control.displayText; font: control.font
        color: control.enabled ? "#f5ede2" : "#c8bbab"
        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
    }
    indicator: Text {
        x: control.width - 38; y: (control.height - height) / 2
        text: "▾"; font.pixelSize: 28
        color: control.enabled ? "#f5ede2" : "#c8bbab"
    }
    delegate: T.ItemDelegate {
        width: control.width; height: 60
        highlighted: control.highlightedIndex === index
        contentItem: Text {
            text: control.textRole ? modelData[control.textRole] : modelData
            font: control.font; color: "#f5ede2"; elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter; leftPadding: 18; rightPadding: 18
        }
        background: Rectangle { color: parent.highlighted ? "#365a27" : "#3e3731" }
    }
    popup: T.Popup {
        y: control.height + 6; width: control.width
        height: Math.min(360, contentItem.implicitHeight + 4)
        topMargin: 16; bottomMargin: control.bottomClearance
        padding: 2
        contentItem: ListView {
            clip: true; implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            boundsBehavior: Flickable.StopAtBounds
        }
        background: Rectangle { color: "#3e3731"; radius: 10; border.width: 2; border.color: "#796a5c" }
    }
}
