"""Deliver complete key presses to the focused native text editor."""
from PySide2.QtCore import QCoreApplication, QEvent, Qt
from PySide2.QtGui import QKeyEvent

from desktop_keys import checked_key


KEYS = {'Backspace': Qt.Key_Backspace, 'Delete': Qt.Key_Delete, 'Enter': Qt.Key_Return,
        'ArrowLeft': Qt.Key_Left, 'ArrowRight': Qt.Key_Right, 'ArrowUp': Qt.Key_Up,
        'ArrowDown': Qt.Key_Down, 'Home': Qt.Key_Home, 'End': Qt.Key_End,
        'Tab': Qt.Key_Tab, 'Escape': Qt.Key_Escape, 'SelectAll': Qt.Key_A}


def send_key(window, message):
    fields = checked_key(message)
    target = window.activeFocusItem()
    if (target is None or not (target.inherits('QQuickTextInput') or target.inherits('QQuickTextEdit'))
            or target.property('readOnly')):
        return False
    text = fields.get('text', '')
    key = Qt.Key_unknown if fields['key'] == 'text' else KEYS[fields['key']]
    modifiers = Qt.ShiftModifier if fields.get('shift') else Qt.NoModifier
    if fields['key'] == 'SelectAll':
        modifiers = Qt.ControlModifier
    for event in (QEvent.KeyPress, QEvent.KeyRelease):
        QCoreApplication.sendEvent(target, QKeyEvent(event, key, modifiers, text))
    return False
