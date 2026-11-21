"""Read signed native LV2 packages with a portable editor and stable module URI."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

from core import MIB, Problem, safe_parts
from publisher_release import canonical, extract_proof, native_archive_files


SCHEMA = 'poly-view-hector-native-module/1'
SCHEMA_V2 = 'poly-view-hector-native-module/2'
SCHEMA_V3 = 'poly-view-hector-native-module/3'
SCHEMA_V4 = 'poly-view-hector-native-module/4'
SCHEMA_V5 = 'poly-view-hector-native-module/5'
FIELDS = {'schema', 'id', 'name', 'version', 'uri', 'ports', 'prototype', 'editor'}
OPTIONAL = {'effect', 'support', 'requirements'}
BASE_FILES = {'module.json', 'module.so', 'module.ttl', 'LICENSE', 'POLY-VIEW-INSTALL.txt'}
TYPE_NAMES = {'audio': 'AudioPort', 'cv': 'CVPort', 'control': 'ControlPort', 'atom': 'AtomPort'}
AUDIO_PORT_CONTRACTS = {
    'send': (19, '4efb17d74249b58611eb56bac9aef6c7d8df2ce6272fb6d926d15ece6198fe1e'),
    'receive': (18, '1628807ca45c53ca77af6fc344aa136aa6014203cff098c8e13bf2dceb36429b'),
}


def validate_audio_ports(module):
    if module.get('support'):
        kind = module['support']['kind'].removeprefix('link-audio-')
        count, digest = AUDIO_PORT_CONTRACTS[kind]
        if hashlib.sha256(canonical(module['ports'][:count])).hexdigest() != digest:
            raise Problem('The Audio support port contract has changed.')


def is_native(module):
    return isinstance(module, dict) and module.get('schema') in (SCHEMA,SCHEMA_V2,SCHEMA_V3,SCHEMA_V4,SCHEMA_V5)


def effect_name(module):
    return module.get('effect', 'poly_view_' + module['id'].replace('-', '_'))


def editor_path(value, extension):
    return (isinstance(value, str) and len(value) <= 180 and value.endswith(extension)
            and all(re.fullmatch(r'[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*', part) for part in value.split('/')))


def validate(module):
    if (not isinstance(module, dict) or module.keys() - (FIELDS | OPTIONAL) or not FIELDS <= module.keys()
            or module.get('schema') not in (SCHEMA,SCHEMA_V2,SCHEMA_V3,SCHEMA_V4,SCHEMA_V5) or not re.fullmatch('[a-z][a-z0-9-]{0,47}', module.get('id', ''))):
        raise Problem('Invalid native module manifest.')
    if module.get('schema') in (SCHEMA_V2,SCHEMA_V3,SCHEMA_V4,SCHEMA_V5):
        required=module.get('requirements')
        fields={'shared_runtime','flash_repair_api','network_connection'}
        if module['schema'] in (SCHEMA_V3,SCHEMA_V4,SCHEMA_V5):fields.add('repair_readiness')
        if module['schema'] in (SCHEMA_V4,SCHEMA_V5):fields.add('preset_preservation')
        if module['schema']==SCHEMA_V5:fields.add('native_editor_lifecycle')
        if module['schema']==SCHEMA_V5 and isinstance(required,dict) and 'midi_bound_recall' in required:fields.add('midi_bound_recall')
        if (not isinstance(required,dict) or set(required)!=fields
                or (module['schema'] in (SCHEMA_V3,SCHEMA_V4,SCHEMA_V5) and required.get('repair_readiness')!=1)
                or (module['schema'] in (SCHEMA_V4,SCHEMA_V5) and required.get('preset_preservation')!=1)
                or (module['schema']==SCHEMA_V5 and required.get('native_editor_lifecycle')!=1)
                or ('midi_bound_recall' in required and required['midi_bound_recall']!=1)
                or required['shared_runtime']!=2 or required['flash_repair_api']!=1 or type(required['network_connection']) is not bool):
            raise Problem('The module requires the current shared installer and flash repair support.')
    elif 'requirements' in module:raise Problem('New requirements need a supported native package format.')
    for field, limit in (('name', 80), ('version', 48), ('uri', 200)):
        value = module[field]
        if (not isinstance(value, str) or not 0 < len(value) <= limit or value != value.strip()
                or not value.isprintable() or any(char in value for char in '<>"|')):
            raise Problem('Invalid native module ' + field + '.')
    if not re.fullmatch(r'(?:urn:|https?://)[^\s<>"\\]+', module['uri']):
        raise Problem('The native module needs a stable LV2 URI.')
    if not re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}', effect_name(module)):
        raise Problem('Invalid native module picker ID.')
    if 'support' in module:
        kind=module['support']
        if (not isinstance(kind,dict) or set(kind)!={'api','kind'} or kind['api']!=1
                or kind['kind'] not in ('link-audio-send','link-audio-receive')
                or module['id']!='ableton-'+kind['kind']
                or module['uri']!='urn:hector-community:link:'+kind['kind'].removeprefix('link-audio-')
                or effect_name(module)!='link_audio_'+kind['kind'].removeprefix('link-audio-')):
            raise Problem('Unsupported native module service contract.')
    ports = module['ports']
    if not isinstance(ports, list) or not 1 <= len(ports) <= 4096:
        raise Problem('A native module must describe 1–4096 LV2 ports.')
    symbols = set()
    for port in ports:
        if (not isinstance(port, dict) or set(port) != {'symbol', 'direction', 'type'}
                or not isinstance(port['symbol'], str) or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,63}', port['symbol'])
                or port['symbol'] in symbols or port['direction'] not in ('input', 'output')
                or port['type'] not in TYPE_NAMES):
            raise Problem('Invalid or repeated native module port.')
        symbols.add(port['symbol'])
    validate_audio_ports(module)
    prototype = module['prototype']
    by_symbol = {port['symbol']: port for port in ports}
    if not isinstance(prototype, dict) or not {'controls', 'inputs', 'outputs'} <= prototype.keys():
        raise Problem('The native module needs a Hector prototype.')
    if 'category' in prototype and (type(prototype['category']) is not int or not 0 <= prototype['category'] <= 32):
        raise Problem('Invalid native module category.')
    if 'tags' in prototype and (not isinstance(prototype['tags'], list) or len(prototype['tags']) > 32
            or any(not isinstance(tag, str) or not 0 < len(tag) <= 64 for tag in prototype['tags'])):
        raise Problem('Invalid native module tags.')
    for field in ('controls', 'inputs', 'outputs','broadcast_ports'):
        if field=='broadcast_ports' and field not in prototype:continue
        entries = prototype[field]
        if not isinstance(entries, dict) or entries.keys() - symbols:
            raise Problem('The native prototype refers to unknown ports.')
        for symbol, values in entries.items():
            if not isinstance(values, list) or len(values) < (4 if field in ('controls','broadcast_ports') else 2):
                raise Problem('Invalid native prototype port description.')
            port = by_symbol[symbol]
            if not isinstance(values[0], str) or not 0 < len(values[0]) <= 128 or not values[0].isprintable():
                raise Problem('Invalid native port label.')
            if field in ('controls','broadcast_ports'):
                if (port['type'] != 'control' or port['direction'] != ('input' if field=='controls' else 'output')
                        or any(type(value) not in (int, float) or not math.isfinite(value) for value in values[1:4])
                        or not values[2] <= values[1] <= values[3]):
                    raise Problem('Native controls need finite defaults and ranges.')
            elif port['direction'] + 's' != field or values[1] != TYPE_NAMES[port['type']]:
                raise Problem('The native prototype changes a port direction or type.')
    editor = module['editor']
    if (not isinstance(editor, dict) or editor.keys() - {'entry', 'backend'}
            or not editor_path(editor.get('entry'), '.qml')
            or ('backend' in editor and not editor_path(editor['backend'], '.py'))):
        raise Problem('Choose a native QML editor and an optional Python backend.')


def port_blocks(text):
    """Keep nested scale-point nodes inside their enclosing LV2 port block."""
    blocks=[];depth=0;start=0;quote=None;uri=False;comment=False;escaped=False
    for index,char in enumerate(text):
        if comment:
            if char=='\n':comment=False
            continue
        if quote:
            if escaped:escaped=False
            elif char=='\\':escaped=True
            elif char==quote:quote=None
            continue
        if uri:
            if char=='>':uri=False
            continue
        if char=='#':comment=True
        elif char in ('"',"'"):quote=char
        elif char=='<':uri=True
        elif char=='[':
            if depth==0:start=index+1
            depth+=1
        elif char==']':
            depth-=1
            if depth<0:raise Problem('Unbalanced native LV2 metadata.')
            if depth==0:blocks.append(text[start:index])
    if depth or quote or uri:raise Problem('Incomplete native LV2 metadata.')
    return blocks


def validate_turtle(module, raw):
    try:
        text = raw.decode('utf-8')
    except UnicodeError as error:
        raise Problem('Native LV2 metadata must use UTF-8.') from error
    if '<' + module['uri'] + '>' not in text or not re.search(r'lv2:binary\s+<module\.so>', text):
        raise Problem('The native LV2 metadata uses a different URI or library.')
    observed = {}
    for block in port_blocks(text):
        index = re.search(r'\blv2:index\s+(\d+)\b', block)
        if index is None:
            continue
        symbol = re.search(r'\blv2:symbol\s+"([A-Za-z_][A-Za-z0-9_]*)"', block)
        types = [kind for kind, name in TYPE_NAMES.items() if re.search(r'\b(?:lv2|atom):' + name + r'\b', block)]
        directions = [direction for direction in ('input', 'output') if 'lv2:' + direction.title() + 'Port' in block]
        number = int(index[1])
        if symbol is None or len(types) != 1 or len(directions) != 1 or number in observed:
            raise Problem('Invalid native LV2 port metadata.')
        observed[number] = {'symbol': symbol[1], 'direction': directions[0], 'type': types[0]}
    if (len(observed) != len(re.findall(r'\blv2:index\b', text)) or set(observed) != set(range(len(module['ports'])))
            or [observed[index] for index in sorted(observed)] != module['ports']):
        raise Problem('Native LV2 ports differ from the signed port layout.')


def read(raw, policy):
    files = native_archive_files(raw)
    proof = extract_proof(files)
    try:
        if len(files['module.json']) > MIB:
            raise Problem('The native module manifest exceeds its size limit.')
        module = json.loads(files['module.json'])
        validate(module)
        if files['module.json'] != canonical(module):
            raise Problem('The native manifest must use the canonical release format.')
        owner, checksums = policy.verify_manifest(proof, module['id'], native=True)
        if module.get('support') and owner!='26e23e5cf387aed68bfd58bdf366f602760acadb072e7609224387e29f862a31':
            raise Problem('This system service requires the reviewed platform publisher.')
    except (KeyError, TypeError, ValueError) as error:
        raise Problem('The signed native module manifest could not be read.') from error
    if checksums != {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}:
        raise Problem('The native module files differ from the signed release.')
    if not {'module.so', 'module.ttl', 'module.json'} <= files.keys():
        raise Problem('The native package needs its library and LV2 metadata.')
    for name, content in files.items():
        if name not in BASE_FILES and not name.startswith('editor/'):
            raise Problem('The native package contains an unsupported destination.')
        if name.startswith('editor/') and name.endswith('.py'):
            try:
                compile(content, name, 'exec')
            except (SyntaxError, ValueError) as error:
                raise Problem('A native editor backend has a syntax error.') from error
    for name in module['editor'].values():
        if 'editor/' + name not in files:
            raise Problem('The native editor is missing a declared file.')
    binary = files['module.so']
    if (len(binary) < 64 or binary[:7] != b'\x7fELF\x02\x01\x01'
            or binary[16:24] != b'\x03\x00\xb7\x00\x01\x00\x00\x00'
            or b'\0lv2_descriptor\0' not in binary or module['uri'].encode() + b'\0' not in binary):
        raise Problem('Choose a Hector ARM64 LV2 library with the declared native URI.')
    validate_turtle(module, files['module.ttl'])
    if module.get('support') and any('editor/support/' + name not in files
            for name in ('manager.py', 'client.py', 'helper', 'control', 'network_binding.py')):
        raise Problem('The Audio package is missing required service support.')
    return module, binary, {'publisher': owner, 'publisher_proof': proof, 'native_files': checksums}, files


def manifest(record):
    return {name: record[name] for name in FIELDS | OPTIONAL if name in record}


def checked_file(root, name):
    safe_parts(name)
    path = Path(root)
    for part in name.split('/'):
        path = path / part
        if path.is_symlink():
            raise Problem('A native package contains a filesystem link.')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 8 * MIB:
            raise Problem('A native package file exceeds its size limit.')
        return source.read(8 * MIB + 1)


def verify_files(root, key, record, callback=lambda applied, total: None):
    checksums = record['native_files']
    for index, (name, expected) in enumerate(checksums.items()):
        path = key + '.so' if name == 'module.so' else key + '.pkg/' + name
        raw = checked_file(root, path)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise Problem('A native editor or library failed its integrity check.')
        callback(index + 1, len(checksums))


def prototype(record, root, key):
    result = dict(category=0, tags=['community'], description=record['name'],
                  long_description=record['name'] + ' · ' + record['version'],
                  num_cv_in=sum(port['direction'] == 'input' and port['type'] == 'cv' for port in record['ports']))
    result.update(record['prototype'], display_name=record['name'])
    result['default_panel'] = 'PolyViewNativePanel.qml'
    result['poly_view_editor'] = (Path(root) / (key + '.pkg/editor') / record['editor']['entry']).as_uri()
    return result
