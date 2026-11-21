import QtQuick 2.11
import "../wifi" as Wifi
import "../c64" as C64

Rectangle {
    id: root
    objectName: "browser_password_panel"
    color: "#17130f"
    signal cancelled()
    signal submitted(string password)
    readonly property int maxPasswordLength: 24
    readonly property int passwordLength: characterCount(passwordField.text)
    readonly property bool validPassword: passwordLength > 0 && passwordLength <= maxPasswordLength
    property bool revealPassword: false
    property bool busy: false
    property string error: ""

    function characterCount(value) {
        return value.replace(/[\ud800-\udbff][\udc00-\udfff]/g, "_").length
    }

    MouseArea { anchors.fill: parent }
    C64.Frame { id: frame; anchors.fill: parent }
    Text {
        x: 58; y: 104
        text: "SET BROWSER PASSWORD"; color: "#8cff73"
        font.family: frame.pixelFont; font.pixelSize: 24
    }
    Text {
        x: 58; y: 147
        text: "Choose 1–" + root.maxPasswordLength + " characters for signing in to Poly / View."
        color: "#81bc6d"; font.family: frame.keyFont; font.pixelSize: 21
    }
    Rectangle {
        x: 58; y: 191; width: 954; height: 60; radius: 5
        color: "#031003"; border.color: "#428731"; border.width: 2
        TextInput {
            id: passwordField
            objectName: "web_browser_password"
            anchors.fill: parent; anchors.leftMargin: 20; anchors.rightMargin: 20
            verticalAlignment: TextInput.AlignVCenter
            echoMode: root.revealPassword ? TextInput.Normal : TextInput.Password
            // Qt measures this limit in UTF-16 units. The counter uses characters.
            passwordMaskDelay: 0; maximumLength: root.maxPasswordLength * 2
            inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
            color: "#a5ff83"; font.family: frame.pixelFont; font.pixelSize: 21; selectByMouse: true; readOnly: root.busy
            Accessible.name: "Browser password"
        }
    }
    C64.Key {
        objectName: "web_browser_reveal_password"
        x: 1030; y: 191; width: 190; height: 60; beige: true; font.family: frame.heavyFont
        text: root.revealPassword ? "Hide" : "Show"
        onClicked: root.revealPassword = !root.revealPassword
    }
    Text {
        x: 58; y: 265
        text: root.passwordLength + " / " + root.maxPasswordLength + " characters"
        color: root.validPassword ? "#8cff73" : "#81bc6d"; font.family: frame.pixelFont; font.pixelSize: 12
    }
    Rectangle { x: 46; y: 294; width: 1188; height: 310; radius: 9; color: "#6e5c4c"; border.color: "#57483a" }
    Wifi.WifiKeyboard {
        id: keyboard
        x: 58; y: 302; width: 1170; height: 294; enabled: !root.busy
        revealed: root.revealPassword
        onRevealRequested: root.revealPassword=!root.revealPassword
        onClearRequested: passwordField.text=""
        onSubmitRequested: { if(root.validPassword && !root.busy)root.submitted(passwordField.text) }
        onNextField: passwordField.forceActiveFocus()
        onMoveCursor: function(direction) {
            var cursor=passwordField.cursorPosition;
            var pair=direction<0 ? /[\ud800-\udbff][\udc00-\udfff]$/.test(passwordField.text.slice(0,cursor)) : /^[\ud800-\udbff][\udc00-\udfff]/.test(passwordField.text.slice(cursor));
            passwordField.cursorPosition=Math.max(0,Math.min(passwordField.text.length,cursor+direction*(pair?2:1)));
        }
        onTyped: function(value) {
            const first = passwordField.selectionStart
            const last = passwordField.selectionEnd
            const next = passwordField.text.slice(0, first) + value + passwordField.text.slice(last)
            if (root.characterCount(next) > root.maxPasswordLength)
                return
            if (first !== last)
                passwordField.remove(first, last)
            passwordField.insert(first, value)
        }
        onErase: {
            if (passwordField.selectionStart !== passwordField.selectionEnd)
                passwordField.remove(passwordField.selectionStart, passwordField.selectionEnd)
            else if (passwordField.cursorPosition > 0) {
                const cursor = passwordField.cursorPosition
                const pair = /[\ud800-\udbff][\udc00-\udfff]$/.test(passwordField.text.slice(0, cursor))
                passwordField.remove(cursor - (pair ? 2 : 1), cursor)
            }
        }
    }
    Text {
        x: 58; y: 610; width: 1152
        visible: root.error !== ""
        text: root.error; textFormat: Text.PlainText; color: "#e1c77a"; font.family: frame.keyFont; font.pixelSize: 18; elide: Text.ElideRight
    }
    Rectangle {
        x: 46; y: 636; width: 1188; height: 50; color: "transparent"
        C64.Key {
            objectName: "web_browser_cancel"
            x: 0; y: 0; width: 240; height: 50
            text: "CANCEL"; beige: true; font.family: frame.heavyFont
            enabled: !root.busy
            onClicked: root.cancelled()
        }
        C64.Key {
            objectName: "web_browser_save_password"
            x: 870; y: 0; width: 318; height: 50
            text: "SAVE BROWSER PASSWORD"; font.pixelSize: 18; beige: true; font.family: frame.heavyFont
            enabled: root.validPassword && !root.busy
            onClicked: root.submitted(passwordField.text)
        }
    }
    onVisibleChanged: { if(!visible) { passwordField.text="";root.revealPassword=false;keyboard.reset() } }
}
