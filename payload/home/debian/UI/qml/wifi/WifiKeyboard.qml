import QtQuick 2.11
import "../c64" as C64

Item {
    id: root
    property bool upper: false
    property bool symbols: false
    property bool caps: false
    property bool revealed: false
    signal typed(string value)
    signal erase()
    signal moveCursor(int direction)
    signal revealRequested()
    signal clearRequested()
    signal submitRequested()
    signal nextField()
    function character(base,shift,symbol,units) { return {base:base,shift:shift || base,symbol:symbol || shift || base,units:units || 1} }
    function action(id,label,units,beige) { return {action:id,label:label,units:units,beige:beige !== false} }
    function letterRow(text,marks) { var keys=[]; for (var i=0;i<text.length;i++) keys.push(character(text[i],text[i].toUpperCase(),marks[i])); return keys }
    function makeRows() {
        var number=[character("`","~")],digits="1234567890",shifted="!@#$%^&*()";
        for(var i=0;i<digits.length;i++) number.push(character(digits[i],shifted[i]));
        return [number.concat([character("-","_"),character("=","+"),action("erase","BACKSPACE",2)]),
            [action("tab","TAB",1.5)].concat(letterRow("qwertyuiop","!@#$%^&*()"),[character("[","{"),character("]","}"),character("\\","|","|",1.5)]),
            [action("caps","CAPS LOCK",1.75,false)].concat(letterRow("asdfghjkl","[]{}<>/\\|"),[character(";",":"),character("'","\""),action("return","RETURN",2.25)]),
            [action("upper","SHIFT",2.25,false)].concat(letterRow("zxcvbnm","-_=+;:~"),[character(",","<"),character(".",">"),character("/","?"),action("upper_right","SHIFT",2.75,false)]),
            [action("symbols","SYMBOLS",2),action("reveal","SHOW",2),action("space","SPACE",6.25,false),action("left","←",1.25),action("right","→",1.25),action("clear","CLEAR",2.25)]];
    }
    function reset() { upper=false;caps=false;symbols=false }
    function value(spec) {
        if(spec.action) return spec.action === "symbols" && symbols ? "ABC" : spec.action === "reveal" ? (revealed ? "HIDE" : "SHOW") : spec.label;
        return symbols ? spec.symbol : (/[a-z]/.test(spec.base) ? (upper !== caps ? spec.shift : spec.base) : (upper ? spec.shift : spec.base));
    }
    function press(spec) {
        if(!spec.action) { root.typed(value(spec));if(upper)upper=false;return }
        if(spec.action === "upper" || spec.action === "upper_right") upper=!upper;
        else if(spec.action === "caps") caps=!caps;
        else if(spec.action === "symbols") symbols=!symbols;
        else if(spec.action === "space") root.typed(" ");
        else if(spec.action === "erase") root.erase();
        else if(spec.action === "clear") root.clearRequested();
        else if(spec.action === "reveal") root.revealRequested();
        else if(spec.action === "return") root.submitRequested();
        else if(spec.action === "left") root.moveCursor(-1);
        else if(spec.action === "right") root.moveCursor(1);
        else if(spec.action === "tab") root.nextField();
    }
    property var rows: makeRows()
    Column {
        anchors.fill: parent; spacing: 7
        Repeater {
            model: root.rows
            Row {
                property var keys: modelData
                property int rowIndex: index
                spacing: 7; width: root.width
                Repeater {
                    model: parent.keys
                    C64.Key {
                        property var spec: modelData
                        objectName: spec.action ? "keyboard_" + (spec.action === "upper" ? "case" : spec.action) : "key_" + spec.base
                        width: spec.units * root.width / 15 - 7; height: (root.height - 28) / 5
                        beige: spec.beige || false
                        font.pixelSize: spec.action ? 16 : 24
                        font.capitalization: Font.MixedCase
                        text: root.value(spec)
                        secondary: !spec.action && !root.symbols && !/[a-z]/.test(spec.base) && spec.base !== spec.shift ? (root.upper ? spec.base : spec.shift) : ""
                        checkable: spec.action === "upper" || spec.action === "upper_right" || spec.action === "caps" || spec.action === "symbols"
                        checked: spec.action === "upper" || spec.action === "upper_right" ? root.upper : spec.action === "caps" ? root.caps : spec.action === "symbols" ? root.symbols : false
                        onClicked: root.press(spec)
                    }
                }
            }
        }
    }
}
