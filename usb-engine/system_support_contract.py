# SPDX-License-Identifier: MIT
"""Exact paired-storage initial-install contract; upgrades have a separately authenticated embedded plan."""
FILES=('usr/local/lib/hector-flash-rx-fix/boot.py', 'usr/local/lib/hector-flash-rx-fix/guard.py', 'usr/local/lib/hector-flash-rx-fix/hector_sun6i_rx_fix.ko', 'usr/local/lib/hector-flash-rx-fix/jffs2-preserve-read-errors.ko', 'usr/local/lib/hector-flash-rx-fix/fix_contract.py', 'usr/local/lib/hector-flash-rx-fix/transaction.py', 'usr/local/lib/hector-flash-rx-fix/remove.py', 'usr/share/doc/hector-flash-rx-fix/GPL-2.0.txt', 'usr/share/doc/hector-flash-rx-fix/NOTICE.txt', 'usr/local/lib/hector-flash-rx-fix/publisher-proof.json', 'etc/systemd/system/hector-flash-rx-guard.service', 'etc/systemd/system/hector-flash-rx-fallback.service', 'etc/systemd/system/hector-flash-rx-confirm.service', 'etc/systemd/system/hector-flash-rx-fix.service', 'etc/systemd/system/multi-user.target.d/96-flash-rx-fix.conf', 'etc/systemd/system/jack.service.d/96-flash-rx-fix.conf', 'etc/systemd/system/lightdm.service.d/96-flash-rx-fix.conf', 'etc/systemd/system/nor_flash.mount.d/96-flash-rx-fix.conf')
GUARDS=('etc/fstab','usr/bin/start_beebo.sh','home/debian/UI/show_widget.py','usr/lib/modules/5.4.2-rt5-001/kernel/fs/jffs2/jffs2.ko')
VERSION='0.1.0-spi-rx.9'
UPGRADE_FILES=()
def validate_manifest(m):
    assert m['schema']=='four-by-four-hector/guarded/1' and m['hardware']=='Hector' and m['firmware']=='407' and m['architecture']=='aarch64'
    assert m['version']==VERSION and not m.get('migration_from')
    assert tuple(r['path'] for r in m['files'])==FILES
    assert m['require_absent']==list(FILES) and set(m['guards'])==set(GUARDS) and set(m['guard_metadata'])==set(GUARDS)
    assert m['guards'][GUARDS[-1]]=='f618062ee8a52faeeabb315a80166cbd517d777b8516288246416357522b24a1'
    for row in m['files']:
        assert row['before_sha256'] is None and row.get('before_metadata') is None
        assert type(row['size']) is int and 0<row['size']<256*1024
        assert len(row['sha256'])==64 and all(c in '0123456789abcdef' for c in row['sha256'])
    assert type(m['published_mtime_ns']) is int and m['published_mtime_ns']>0
    assert all(len(v)==64 and all(c in '0123456789abcdef' for c in v) for v in m['guards'].values())

LEGACY_FILES=('usr/local/lib/hector-flash-rx-fix/boot.py', 'usr/local/lib/hector-flash-rx-fix/guard.py', 'usr/local/lib/hector-flash-rx-fix/hector_sun6i_rx_fix.ko', 'usr/local/lib/hector-flash-rx-fix/fix_contract.py', 'usr/local/lib/hector-flash-rx-fix/transaction.py', 'usr/local/lib/hector-flash-rx-fix/remove.py', 'usr/share/doc/hector-flash-rx-fix/GPL-2.0.txt', 'usr/share/doc/hector-flash-rx-fix/NOTICE.txt', 'usr/local/lib/hector-flash-rx-fix/publisher-proof.json', 'etc/systemd/system/hector-flash-rx-guard.service', 'etc/systemd/system/hector-flash-rx-fallback.service', 'etc/systemd/system/hector-flash-rx-confirm.service', 'etc/systemd/system/hector-flash-rx-fix.service', 'etc/systemd/system/multi-user.target.d/96-flash-rx-fix.conf', 'etc/systemd/system/jack.service.d/96-flash-rx-fix.conf', 'etc/systemd/system/lightdm.service.d/96-flash-rx-fix.conf', 'etc/systemd/system/nor_flash.mount.d/96-flash-rx-fix.conf')
LEGACY_VERSIONS=('0.1.0-spi-rx.1','0.1.0-spi-rx.2')
