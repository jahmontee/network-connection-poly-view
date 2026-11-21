"""Host-side state hooks for verified native modules, without editor visibility."""
import importlib.util
import hashlib
import json
import logging
from pathlib import Path
import sys
import types

REGISTRY=Path('/run/hector-module-catalog/native-editors.json')
_restorers={}

def begin_load(transport,commands,graph,registry=REGISTRY):
    global _restorers
    old=_restorers;_restorers={}
    for restorer in old.values():
        # Cancel commands queued by the previous graph generation.
        restorer.begin_load('ingen:/__retired_native_module_graph__')
    try:
        with Path(registry).open('rb') as source:entries=json.loads(source.read(1024*1024+1))
    except (OSError,ValueError):return
    for kind,entry in entries.items():
        if entry.get('support',{}).get('kind') not in ('link-audio-send','link-audio-receive'):continue
        try:
            root=Path(entry['root']);path=root/entry['backend']
            name='poly_view_state_'+hashlib.sha256(str(root).encode('utf-8')).hexdigest()
            package=types.ModuleType(name);package.__path__=[str(root)];sys.modules[name]=package
            spec=importlib.util.spec_from_file_location(name+'.backend',path)
            module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
            restorer=module.create_restorer(dict(transport=transport,commands=commands))
            restorer.begin_load(graph);_restorers[kind]=restorer
        except Exception:
            logging.exception('Could not prepare native saved-state restore for %s',kind)

def handle(message):
    for kind,restorer in list(_restorers.items()):
        try:restorer.handle(message)
        except Exception:logging.exception('Native saved-state restore failed for %s',kind)
