import QtQuick 2.11
import "../c64" as C64
Rectangle {
    id: panel; width: 1280; height: 720; color: "#17130f"
    C64.Frame { id: frame; anchors.fill: parent }
    WifiPage { id: page; objectName: "wifi_page"; x: 64; y: 84; width: 1280; height: 624; scale: .9; transformOrigin: Item.TopLeft }
    Rectangle { x: 44; y: 638; width: 1192; height: 48; color: "#041204" }
    LinkButton { objectName: "wifi_back"; x: 48; y: 642; width: 192; height: 43; text: "BACK"; primary: true; onClicked: { if(!page.goBack())mainStack.pop() } }
    Text { x: 274; y: 642; width: 900; height: 43; text: "WI-FI SETUP"; color: "#81bc6d"; font.family: frame.pixelFont; font.pixelSize: 13; verticalAlignment: Text.AlignVCenter }
}
