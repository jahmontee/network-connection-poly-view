"""Record the native module catalog as the same unprivileged user as Hector's UI."""
import json
import os
from pathlib import Path
import sys

APP = Path(__file__).resolve().parent
NATIVE_UI = Path('/home/debian/UI')

COMMUNITY = {
    'BiPoSeq', 'BiPoSeq_ext', 'mala', 'ableton_link', 'ableton_link_audio',
    'link_audio_receive', 'link_audio_send', 'poly_rhythm_legacy', 'poly_rhythm',
    'poly_rhythm_next', 'poly303', 'fourbyfour', 'fourbyfour_legacy',
    'sample_box', 'oscilloscope', 'sample_box_legacy', 'cardinal_module',
    'echo_field', 'echo_field_xl',
}


def inventory(registry, picker, display_name, stock, managed):
    stock_uris = set(stock.values())
    entries = []
    for module_id, uri in registry.items():
        if module_id in managed:
            continue
        if module_id in stock or uri in stock_uris:
            origin = 'stock'
        elif module_id == 'network_connection':
            origin = 'device'
        elif module_id in COMMUNITY:
            origin = 'community'
        else:
            origin = 'unknown'
        entries.append(dict(id=module_id, uri=uri, name=display_name(module_id),
                            in_picker=module_id in picker, origin=origin))
    return sorted(entries, key=lambda entry: (entry['name'].casefold(), entry['id']))


def main():
    if os.geteuid() == 0:
        raise SystemExit('Use the unprivileged native UI account to export the module catalog.')
    sys.path.insert(0, str(APP))
    from module_manager import PREFIX, RUNTIME, canonical, publish
    factory = json.loads((APP / 'stock407_modules.json').read_bytes())['modules']
    sys.path.insert(0, str(NATIVE_UI))
    os.chdir(NATIVE_UI)
    import module_info
    import module_browser_model
    registry = module_info.effect_type_maps['beebo']
    managed = set(module_info.poly_view_prototypes)
    display_name = getattr(module_browser_model, 'module_display_name', None)
    if display_name is None:
        def display_name(name):
            return module_info.effect_prototypes_models_all.get(name, {}).get('display_name', name.replace('_', ' '))
    entries = inventory(registry, module_browser_model.effect_prototypes,
                        display_name, factory, managed)
    publish(RUNTIME / 'native-inventory.json', canonical({'schema': 1, 'entries': entries}))


if __name__ == '__main__':
    main()
