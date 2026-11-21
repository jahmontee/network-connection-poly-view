"""One signed module release with an independently signed offline runtime.

The browser reads these archives as data and only queues module.zip. Physical
USB startup is the separate entry point that may install the offline runtime.
"""
import io
import json
import stat
import zipfile
from core import MIB,Problem,safe_parts
from publisher_release import canonical,extract_proof,sha256
import native_module
import platform_release

SCHEMA='poly-view-offline-module/1'
FILES={'usb-module.json','module.zip','runtime.zip','debug.sh','poly-view/install.py','READ-ME-FIRST.txt'}
LIMIT=16*MIB


def wrapped(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:return 'usb-module.json' in archive.namelist()
    except zipfile.BadZipFile:return False


def archive_files(raw):
    if not 0<len(raw)<=LIMIT:raise Problem('The complete module installer exceeds its size limit.',413)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries=archive.infolist();files={};total=0
            if len(entries)!=len(FILES)+2:raise Problem('The offline installer has unexpected files.')
            for entry in entries:
                safe_parts(entry.filename)
                if (entry.filename in files or entry.orig_filename!=entry.filename or entry.is_dir()
                        or stat.S_IFMT(entry.external_attr>>16) not in (0,stat.S_IFREG) or entry.flag_bits&1
                        or entry.compress_type not in (0,8) or not 0<entry.file_size<=16*MIB):
                    raise Problem('Unsupported offline installer archive entry.')
                total+=entry.file_size
                if total>32*MIB:raise Problem('The offline installer exceeds its unpacked limit.')
                files[entry.filename]=archive.read(entry)
            return files
    except (zipfile.BadZipFile,ValueError,RuntimeError,EOFError,OSError) as error:
        raise Problem('The offline module installer could not be read.') from error


def read(raw,policy):
    files=archive_files(raw);proof=extract_proof(files)
    if set(files)!=FILES:raise Problem('The offline installer is incomplete.')
    try:info=json.loads(files['usb-module.json'])
    except ValueError as error:raise Problem('Invalid offline installer information.') from error
    if (not isinstance(info,dict) or set(info)!={'schema','id','version','target','module_sha256','runtime_sha256'}
            or info.get('schema')!=SCHEMA or info['target']!='hector-407-aarch64'
            or info['module_sha256']!=sha256(files['module.zip']) or info['runtime_sha256']!=sha256(files['runtime.zip'])):
        raise Problem('The offline module installer does not match its signed payload.')
    # Import lazily to avoid coupling the archive reader to a running manager.
    from module_manager import check_binary,check_manifest
    try:
        with zipfile.ZipFile(io.BytesIO(files['module.zip'])) as archive:
            entry=archive.getinfo('module.json')
            if entry.file_size>MIB:raise Problem('Oversized module manifest.')
            portable=native_module.is_native(json.loads(archive.read(entry)))
    except (zipfile.BadZipFile,KeyError,ValueError) as error:raise Problem('Unsupported module payload in the USB installer.') from error
    if portable:module,_,inner,_=native_module.read(files['module.zip'],policy)
    else:
        module,binary,inner=policy.read(files['module.zip']);check_manifest(module);check_binary(module,binary)
    owner,inventory=policy.verify_manifest(proof,module['id'],native=portable)
    if (owner!=inner['publisher'] or module['id']!=info['id'] or module['version']!=info['version']
            or inventory!={name:sha256(content) for name,content in files.items()}):
        raise Problem('The offline installer differs from its verified module publisher.')
    runtime,_,_=platform_release.read(files['runtime.zip'],policy)
    if runtime['id']!='poly-view-module-runtime':raise Problem('Use an offline module runtime, without Network Connection.')
    return files['module.zip'],module,files
