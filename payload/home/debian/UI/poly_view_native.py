"""Host adapters for signed community editors published in the boot catalog."""
import importlib.util
import json
import logging
from pathlib import Path
import sys
import types

from PySide2.QtCore import QObject, Slot


def install_catalog(module_info):
    try:
        entries = json.loads(Path('/run/hector-module-catalog/catalog.json').read_bytes())
    except (OSError, ValueError):
        return
    removed = set(module_info.poly_view_community_removed)
    for name, entry in entries.items():
        if not entry.get('managed_native') or name not in removed:
            continue
        module_info.effect_prototypes_models_all[name] = entry['prototype']
        module_info.effect_type_maps['beebo'][name] = entry['uri']
        module_info.poly_view_prototypes[name] = entry['prototype']
    module_info.poly_view_community_removed = tuple(name for name in removed
                                                  if name not in module_info.poly_view_prototypes)


class NativeEditors(QObject):
    def __init__(self, effects, knobs, ingen, loading=None, registry=Path('/run/hector-module-catalog/native-editors.json'), *, connections=None, hardware='hector'):
        super().__init__()
        self.effects = effects
        self.api = dict(effects=effects, knobs=knobs, ingen=ingen, loading=loading,
                        connections=connections if connections is not None else {}, hardware=hardware, native_editor_lifecycle=1)
        self.expected = set()
        self.backends = {}
        self.factories = {}
        try:
            self.editors = json.loads(Path(registry).read_bytes())
        except (OSError, ValueError):
            self.editors = {}

    def factory(self, kind):
        if kind in self.factories:
            return self.factories[kind]
        editor = self.editors[kind]
        root = Path(editor['root'])
        if not editor.get('backend'):
            self.factories[kind] = None
            return None
        path = root / editor['backend']
        name = 'poly_view_editor_' + root.parent.name.replace('.', '_')
        package = types.ModuleType(name)
        package.__path__ = [str(path.parent)]
        sys.modules[name] = package
        spec = importlib.util.spec_from_file_location(name + '.backend', path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        register = getattr(module, 'register_types', None)
        if register is not None:
            register()
        factory = module.create_backend
        self.factories[kind] = factory
        return factory

    def expect_new(self, effect):
        self.expected.add(effect)

    def created(self, effect, kind):
        fresh = effect in self.expected
        self.expected.discard(effect)
        if kind in self.editors and self.editors[kind].get("backend") and self.editors[kind].get("lifecycle")==1:
            self.backend(effect, kind, fresh=fresh)

    def retire(self, effect):
        self.expected.discard(effect)
        if effect in self.backends:self.close_backend(effect)

    def flush(self):
        for effect, backend in list(self.backends.items()):
            if effect not in self.effects:
                self.close_backend(effect)
                continue
            callback=getattr(backend,"flush",None)
            if callback:
                try:callback()
                except Exception:logging.exception("Native editor flush failed for %s",effect)

    @Slot(str, str, result=QObject)
    def backend(self, effect, kind, *, fresh=False):
        effect = str(effect).removeprefix('ingen:')
        if kind not in self.editors or effect not in self.effects:
            return None
        if effect in self.backends:
            return self.backends[effect]
        try:
            factory = self.factory(kind)
            backend = factory(effect, dict(self.api, fresh=fresh)) if factory else QObject(self)
            if not isinstance(backend, QObject):
                raise TypeError('A native editor factory must return a QObject.')
            backend.setParent(self)
            self.backends[effect] = backend
            return backend
        except Exception:
            logging.exception('Could not open native editor for %s', kind)
            return None

    def readback(self, message):
        for effect, backend in list(self.backends.items()):
            if effect not in self.effects:
                self.close_backend(effect)
                continue
            callback = getattr(backend, 'readback', None)
            if callback:
                try:
                    callback(message)
                except Exception:
                    logging.exception('Native editor readback failed for %s', effect)

    def connectionRequested(self, source, destination, port):
        for backend in self.backends.values():
            callback = getattr(backend, 'connectionRequested', None)
            if callback:
                try:
                    callback(source, destination, port)
                except Exception:
                    logging.exception('Native editor connection callback failed')

    def close_backend(self, effect):
        backend = self.backends.pop(effect)
        callback = getattr(backend, 'close', None)
        try:
            if callback:
                callback()
        except Exception:
            logging.exception('Native editor close failed for %s', effect)
        backend.deleteLater()

    def defer_save(self, callback):
        for backend in list(self.backends.values()):
            defer=getattr(backend,"defer_save",None)
            if defer and defer(callback):return True
        return False

    def begin_load(self):
        self.expected.clear()
        self.close()

    def close(self):
        for effect in list(self.backends):
            self.close_backend(effect)
