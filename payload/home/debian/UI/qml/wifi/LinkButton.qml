import QtQuick 2.11
import "../c64" as C64

C64.Key {
    property bool primary: false
    beige: primary
    implicitWidth: 160; implicitHeight: 64
    font.family: "Inter"; font.pixelSize: 23; font.weight: Font.ExtraBold
}
