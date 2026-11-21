"""Validate text-editing keystrokes sent to Hector's focused text field."""
SPECIAL_KEYS = frozenset(('Backspace', 'Delete', 'Enter', 'ArrowLeft', 'ArrowRight',
                          'ArrowUp', 'ArrowDown', 'Home', 'End', 'Tab', 'Escape', 'SelectAll'))


def checked_key(message):
    if not isinstance(message, dict) or message.keys() - {'action', 'key', 'text', 'shift'}:
        raise ValueError('Invalid keyboard input.')
    key = message.get('key')
    shift = message.get('shift', False)
    if type(shift) is not bool:
        raise ValueError('Invalid keyboard modifier.')
    if key == 'text':
        text = message.get('text')
        if not isinstance(text, str) or not 0 < len(text) <= 32 or not text.isprintable():
            raise ValueError('Enter up to 32 printable characters at a time.')
        return {'key': key, 'text': text}
    if key not in SPECIAL_KEYS or message.get('text') not in (None, ''):
        raise ValueError('Unsupported text-editing key.')
    return {'key': key, 'shift': shift}
