import QtQuick 2.11
import QtQuick.Controls 2.4
import QtQuick.Templates 2.4 as T

Rectangle {
    id: page
    color: "transparent"
    FontLoader { id: keyFontLoader; source: "file:///opt/hector-storage-manager/app/static/fonts/Inter-700.ttf" }
    FontLoader { id: pixelFontLoader; source: "file:///opt/hector-storage-manager/app/static/fonts/PressStart2P-Regular.ttf" }
    readonly property string keyFont: keyFontLoader.name
    readonly property string pixelFont: pixelFontLoader.name
    property var hub: wifiHub
    property var state: hub.status
    property var connection: state.connection || ({})
    property var adapters: state.adapters || []
    property var selected: null
    property bool editing: selected !== null
    property bool busy: state.busy || false
    property bool recoveryNeeded: state.phase === "recovery-needed"
    property bool reveal: false
    property bool remember: true
    property bool adapterPicked: false
    property bool radioDetails: false
    property string selectedAdapter: connection.adapter || ""
    function securityLabel(network) {
        if (network.modes.indexOf("psk") >= 0 && network.modes.indexOf("sae") >= 0) return "WPA2/WPA3 Personal";
        return network.security === "psk" ? "WPA2 Personal" : network.security === "sae" ? "WPA3 Personal" : network.security === "open" ? "Open network" : "Unsupported security";
    }
    function signalLabel(signal) { return signal >= -60 ? "Strong signal" : signal >= -70 ? "Good signal" : signal >= -80 ? "Fair signal" : "Weak signal" }
    onAdaptersChanged: {
        if (!adapterPicked) {
            for (var i = 0; i < adapters.length; ++i)
                if (adapters[i].mac === connection.adapter) { adapterChoice.currentIndex = i; break }
        }
    }
    function clearSecret() { password.text = ""; reveal = false; keyboard.reset() }
    function leave() { radioDetails = false; clearSecret(); selected = null }
    function goBack() {
        if (radioDetails) { radioDetails = false; return true; }
        if (editing) { leave(); return true; }
        return false;
    }
    onVisibleChanged: { hub.activate(visible); if (!visible) leave() }
    Component.onCompleted: hub.activate(visible)
    Component.onDestruction: { clearSecret(); hub.activate(false) }
    Text { font.family: page.keyFont;
        x: 24; y: 12; width: 1200; height: 62; font.pixelSize: 25; color: "#8cff73"; textFormat: Text.PlainText
        text: busy ? (recoveryNeeded ? "Wi-Fi needs recovery. Tap Retry recovery." : state.phase === "restoring" ? "Restoring the previous network…" : "Connecting… Wi-Fi and Link may briefly disconnect.") :
              connection.wpa_state === "COMPLETED" ? (connection.ssid || "Connected") + "   ·   " + (Number(connection.freq) >= 4900 ? "5 GHz" : "2.4 GHz") + "   ·   " + (connection.ip_address || "Getting an address…") : "Choose an adapter, then scan for networks."
        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
    }
    LinkChoice {
        id: adapterChoice; objectName: "wifi_adapter"; x: 24; y: 84; width: 728; height: 62
        model: page.adapters.map(function(a) { return a.name + " · " + a.mac + (a.supported ? "" : " · driver needed") })
        displayText: currentIndex >= 0 && currentIndex < page.adapters.length ? model[currentIndex] : "No Wi-Fi adapter found"
        enabled: !page.busy && page.adapters.length > 0
        onActivated: { page.adapterPicked = true; page.selectedAdapter = page.adapters[index].mac }
        Component.onCompleted: currentIndex = 0
    }
    LinkButton {
        objectName: "wifi_use_adapter"; x: 768; y: 84; width: 270; height: 62; text: "Use adapter"
        enabled: !page.busy && page.adapters.length > 0 && adapterChoice.currentIndex >= 0 && page.adapters[adapterChoice.currentIndex].supported && page.selectedAdapter !== page.connection.adapter
        onClicked: page.hub.useAdapter(page.selectedAdapter)
    }
    LinkButton { objectName: "wifi_scan"; x: 1054; y: 84; width: 202; height: 62; text: "Scan"; enabled: !page.busy && !!page.connection.adapter; onClicked: page.hub.scan() }
    Text { font.family: page.keyFont; x: 24; y: 160; height: 30; width: 1216; font.pixelSize: 22; color: "#81bc6d"; text: "AVAILABLE NETWORKS   ·   strongest access point is remembered with each connection" }
    ListView {
        id: list; objectName: "wifi_networks"; x: 24; y: 200; width: 1232; height: 338
        clip: true; spacing: 9; model: page.state.networks || []
        delegate: Rectangle {
            width: list.width; height: 78; radius: 9; color: modelData.supported ? "#061806" : "#041204"
            border.width: 2; border.color: "#256b21"
            Text { font.family: page.keyFont; x: 20; y: 8; width: 820; height: 34; font.pixelSize: 27; color: "#8cff73"; textFormat: Text.PlainText; text: modelData.name; elide: Text.ElideRight }
            Text { font.family: page.keyFont; x: 20; y: 44; width: 1150; height: 28; font.pixelSize: 20; color: "#81bc6d"; elide: Text.ElideRight; text: (modelData.frequency >= 4900 ? "5 GHz" : "2.4 GHz") + "   ·   " + page.securityLabel(modelData) + "   ·   " + page.signalLabel(modelData.signal) + (modelData.access_points > 1 ? "   ·   " + modelData.access_points + " access points" : "") + (modelData.bssid === page.connection.bssid ? "   ·   Connected" : "") }
            Text { font.family: page.keyFont; anchors.right: parent.right; anchors.rightMargin: 24; anchors.verticalCenter: parent.verticalCenter; font.pixelSize: 27; color: "#8cff73"; text: "›" }
            MouseArea {
                objectName: "wifi_network_" + index; anchors.fill: parent; enabled: modelData.supported && !page.busy
                onClicked: { page.clearSecret(); security.currentIndex = 0; page.selected = modelData; password.forceActiveFocus() }
            }
        }
        Text { font.family: page.keyFont; anchors.centerIn: parent; visible: list.count === 0; width: 1100; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 26; color: "#81bc6d"; text: page.busy ? "Your last working connection is retained until the new one succeeds." : "Tap Scan to find networks. Only adapters with an installed driver can be used." }
    }
    Text { font.family: page.keyFont; objectName: "wifi_message"; x: 24; y: 548; width: page.busy ? 975 : 1232; height: 66; wrapMode: Text.WordWrap; font.pixelSize: 22; color: hub.error ? "#e1c77a" : "#81bc6d"; textFormat: Text.PlainText; text: hub.error || "Network choices are private to this device and stay separate from saved patches." }
    LinkButton { objectName: "wifi_cancel_job"; x: 1030; y: 548; width: 225; height: 64; text: page.recoveryNeeded ? "Retry recovery" : "Cancel attempt"; visible: page.busy; onClicked: page.hub.cancel() }
    Rectangle {
        id: sheet; anchors.fill: parent; visible: page.editing; color: "#041204"
        Text { x: 24; y: 14; width: 362; height: 46; font.family: page.pixelFont; font.pixelSize: 22; color: "#8cff73"; text: "WI-FI PASSWORD" }
        Text { x: 410; y: 16; width: 600; height: 42; font.family: page.keyFont; font.pixelSize: 24; color: "#81bc6d"; textFormat: Text.PlainText; elide: Text.ElideRight; text: page.selected ? page.selected.name : "" }
        LinkButton { objectName: "wifi_radio_details"; x: 1040; y: 8; width: 216; height: 56; text: "Details"; visible: page.selected && (page.selected.radios || []).length > 1; onClicked: page.radioDetails = true }
        T.TextField {
            id: password; objectName: "wifi_password"; x: 24; y: 72; width: 1000; height: 64
            leftPadding: 18; rightPadding: 18; topPadding: 0; bottomPadding: 0
            verticalAlignment: TextInput.AlignVCenter
            font.family: page.pixelFont; font.pixelSize: 20; color: "#8cff73"; selectByMouse: true; maximumLength: 63
            readOnly: true; cursorVisible: activeFocus
            echoMode: page.reveal ? TextInput.Normal : TextInput.Password; passwordMaskDelay: 0
            inputMethodHints: Qt.ImhSensitiveData | Qt.ImhNoPredictiveText | Qt.ImhNoAutoUppercase
            background: Rectangle { color: "#061806"; radius: 10; border.color: "#256b21"; border.width: 2 }
            Text { font.family: page.keyFont; anchors.fill: parent; anchors.leftMargin: 18; verticalAlignment: Text.AlignVCenter; visible: !password.text; text: page.selected && page.selected.security === "open" ? "Open network" : "Wi-Fi password"; font.pixelSize: 25; color: "#81bc6d" }
        }
        LinkButton { objectName: "wifi_reveal"; x: 1040; y: 72; width: 216; height: 64; text: page.reveal ? "Hide password" : "Show password"; onClicked: page.reveal = !page.reveal }
        LinkChoice { id: security; objectName: "wifi_security"; x: 24; y: 152; width: 298; height: 62; model: page.selected ? page.selected.modes.map(function(m) { return m === "auto" ? "Security: Auto" : m === "psk" ? "WPA2 Personal" : "WPA3 Personal" }) : [] }
        LinkButton { objectName: "wifi_remember"; x: 338; y: 152; width: 350; height: 62; text: "Remember network"; checkable: true; checked: page.remember; onClicked: page.remember = checked }
        LinkButton {
            objectName: "wifi_connect"; x: 704; y: 152; width: 260; height: 62; text: "Connect"; enabled: !page.busy
            onClicked: { page.hub.joinNetwork(page.connection.adapter, page.selected.id, password.text, page.selected.modes[Math.max(0, security.currentIndex)], page.remember); page.leave() }
        }
        LinkButton { objectName: "wifi_close_sheet"; x: 980; y: 152; width: 276; height: 62; text: "Cancel"; onClicked: page.leave() }
        Text { font.family: page.keyFont; x: 24; y: 226; width: 1232; height: 32; color: "#81bc6d"; font.pixelSize: 21; text: "A failed attempt returns to the previous connection. Case and symbols are controlled below." }
        Rectangle { x: 16; y: 268; width: 1248; height: 324; radius: 9; color: "#6e5c4c"; border.color: "#57483a" }
        WifiKeyboard {
            id: keyboard; objectName: "wifi_keyboard"; x: 24; y: 276; width: 1232; height: 308
            revealed: page.reveal
            onRevealRequested: page.reveal=!page.reveal
            onClearRequested: password.text=""
            onSubmitRequested: { if(!page.busy) { page.hub.joinNetwork(page.connection.adapter,page.selected.id,password.text,page.selected.modes[Math.max(0,security.currentIndex)],page.remember);page.leave() } }
            onNextField: password.forceActiveFocus()
            onMoveCursor: function(direction) {
                var cursor=password.cursorPosition;
                var pair=direction<0 ? /[\ud800-\udbff][\udc00-\udfff]$/.test(password.text.slice(0,cursor)) : /^[\ud800-\udbff][\udc00-\udfff]/.test(password.text.slice(cursor));
                password.cursorPosition=Math.max(0,Math.min(password.text.length,cursor+direction*(pair?2:1)));
            }
            onTyped: function(value) {
                var first=password.selectionStart,last=password.selectionEnd;
                var next=password.text.slice(0,first)+value+password.text.slice(last);
                if(next.replace(/[\ud800-\udbff][\udc00-\udfff]/g,"_").length>63)return;
                if(first!==last)password.remove(first,last);
                password.insert(first,value);
            }
            onErase: { if (password.selectedText) password.remove(password.selectionStart, password.selectionEnd); else if (password.cursorPosition > 0) { var cursor=password.cursorPosition;var pair=/[\ud800-\udbff][\udc00-\udfff]$/.test(password.text.slice(0,cursor));password.remove(cursor-(pair?2:1),cursor) } }
        }
    }
    Rectangle {
        objectName: "wifi_radios_page"; anchors.fill: parent; visible: page.editing && page.radioDetails; color: "#041204"
        Text { font.family: page.keyFont; x: 24; y: 16; width: 970; height: 54; font.pixelSize: 29; color: "#8cff73"; textFormat: Text.PlainText; elide: Text.ElideRight; text: "Access points · " + (page.selected ? page.selected.name : "") }
        LinkButton { objectName: "wifi_radios_close"; x: 1040; y: 8; width: 216; height: 64; text: "Back to network"; onClicked: page.radioDetails = false }
        Text { font.family: page.keyFont; x: 24; y: 88; width: 1232; height: 54; wrapMode: Text.WordWrap; font.pixelSize: 22; color: "#81bc6d"; text: "Connect uses the strongest access point compatible with your security choice." }
        ListView {
            objectName: "wifi_radios"; x: 24; y: 154; width: 1232; height: 434; clip: true; spacing: 10
            model: page.selected ? page.selected.radios || [] : []
            delegate: Rectangle {
                width: 1232; height: 100; color: "#061806"; radius: 9
                Text { font.family: page.keyFont; x: 20; y: 10; width: 1192; height: 34; font.pixelSize: 25; color: "#8cff73"; text: page.signalLabel(modelData.signal) + "   ·   " + page.securityLabel(modelData) + (index === 0 ? "   ·   Strongest" : "") }
                Text { font.family: page.keyFont; x: 20; y: 54; width: 1192; height: 30; font.pixelSize: 21; color: "#81bc6d"; text: modelData.bssid + "   ·   " + modelData.frequency + " MHz   ·   " + modelData.signal + " dBm" }
            }
        }
    }
}
