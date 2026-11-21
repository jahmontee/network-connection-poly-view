"""Root-reviewed adoption of a legacy community identity into signed packages.

Uploaded archives cannot add or replace this policy. Originals are retained;
activation publishes one identity and commits its retirement only on success.
"""
import hashlib
import json
from pathlib import Path
from core import Problem, safe_parts
import native_module

DEFAULT_POLICY=Path(__file__).with_name('native-migrations.json')

class MigrationPolicy:
    def __init__(self,path=DEFAULT_POLICY,firmware_root=Path('/')):
        self.firmware_root=Path(firmware_root)
        try:
            value=json.loads(Path(path).read_bytes())
        except FileNotFoundError:
            value=dict(schema='poly-view-native-migrations/1',rules=[])
        if value.get('schema')!='poly-view-native-migrations/1' or not isinstance(value.get('rules'),list):
            raise Problem('The native migration policy is invalid.')
        self.rules={}
        for rule in value['rules']:
            fields={'id','effect','uri','publisher','legacy_key','previous_version','ports','legacy_files','dependencies'}
            if set(rule)!=fields or rule['legacy_key'] in self.rules:
                raise Problem('The native migration policy is invalid or ambiguous.')
            self.rules[rule['legacy_key']]=rule

    @staticmethod
    def identity(rule):
        return {key:rule[key] for key in ('id','effect','uri','publisher')}

    def matches(self,module,key):
        rule=self.rules.get(key)
        if not rule:return False
        return (module.get('id')==rule['id'] and module.get('effect')==rule['effect']
                and module.get('uri')==rule['uri'] and module.get('publisher')==rule['publisher'])

    def reserve(self,module):
        # A reviewed identity stays owned by its publisher even before the
        # legacy registration is introduced by later networking installation.
        for key,rule in self.rules.items():
            collision=(module.get('id')==rule['id'] or module.get('uri')==rule['uri']
                       or module.get('effect')==rule['effect'])
            if not collision:continue
            if not self.matches(module,key):
                raise Problem('This reserved module identity requires its reviewed publisher and migration.',409)
            if module.get('ports',[])[:len(rule['ports'])]!=rule['ports']:
                raise Problem('The migration changes existing ports used by saved patches.',409)

    def tombstones(self,state):
        result=state.get('community_migrations',{})
        if not isinstance(result,dict):raise Problem('The community migration record is invalid.')
        for key,identity in result.items():
            rule=self.rules.get(key)
            if not rule or identity!=self.identity(rule):
                raise Problem('The community migration record does not match the reviewed policy.')
        return dict(result)

    def validate(self,manager,module,state,key):
        if not self.matches(module,key):
            raise Problem('This legacy module needs a reviewed migration from its verified publisher.',409)
        rule=self.rules[key]
        package=manager.community[key]
        if (package.get('files')!=rule['legacy_files'] or set(package.get('members',{}))!={rule['effect']}
                or package['members'][rule['effect']]['uri']!=rule['uri']):
            raise Problem('The existing community module differs from its reviewed migration baseline.',409)
        if module['ports'][:len(rule['ports'])]!=rule['ports']:
            raise Problem('The migration changes existing ports used by saved patches.',409)
        if key in state.get('community_pending',{}):
            raise Problem('Undo the pending removal before updating this module.',409)
        # Exact host/baseline admission applies to the one-time adoption.
        # Once committed, normal signed update/port checks take over; later
        # compatible platform updates need not reproduce historical file bytes.
        if key not in self.tombstones(state):
            for name,expected in rule['dependencies'].items():
                safe_parts(name)
                path=self.firmware_root/name
                if not path.is_file() or path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:
                    raise Problem('The module needs a different native host compatibility update.',409)
            manager.community_assets(package)
        return rule

    def adoption(self,manager,module,state):
        result=[]
        for key,package in manager.community.items():
            for effect,member in package['members'].items():
                if module.get('uri')!=member['uri'] and module.get('effect')!=effect:continue
                if key in state.get('community_removed',[]) and key not in self.tombstones(state):continue
                self.validate(manager,module,state,key)
                result.append(key)
        if len(result)>1:raise Problem('More than one community registration uses this module identity.',409)
        return result[0] if result else None

    def applied(self,manager,active,state):
        result=self.tombstones(state)
        for module in active.values():
            key=self.adoption(manager,module,state) if native_module.is_native(module) else None
            if key:result[key]=self.identity(self.rules[key])
            elif native_module.is_native(module):
                # Persist ownership while a signed module is active, including
                # on an offline host with no legacy community entry. Its later
                # removal must not revive an old registration when networking
                # is installed. Only the root-reviewed identity/port policy can
                # create this retirement record; archives cannot supply rules.
                self.reserve(module)
                for reserved,rule in self.rules.items():
                    if reserved not in manager.community and self.matches(module,reserved):
                        result[reserved]=self.identity(rule)
        return result
