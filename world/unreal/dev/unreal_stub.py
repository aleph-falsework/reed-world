"""A stand-in `unreal` module for dry-running import_reed.py outside the editor.

It is NOT Unreal: it only checks the script's own control flow (names, data parsing, the order of calls,
re-run cleanup). It does simulate two things that matter:
  * the glTF importer's axis conversion, glTF (X, Y, Z) m -> UE (X, Z, Y) * 100 cm, so the script's
    Eliot Hall axis self-check runs against the real .glb data;
  * the asset registry: imported .glb meshes appear as StaticMesh assets named after their glTF nodes.
Every `unreal.<name>` the script touches is recorded in USED so it can be checked against the API docs.
"""
import json
import os
import struct
import sys

USED = set()
LEVEL = []          # spawned actors
ASSETS = {}         # path -> object
IMPORTS = []
STATS = dict(instances=0, set_material=0, collision=0, saved=0)


def _glb_json(path):
    with open(path, 'rb') as f:
        head = f.read(20)
        n = struct.unpack('<I', head[12:16])[0]
        return json.loads(f.read(n))


class Obj:
    """Generic UObject/struct: editor properties stored in a dict, unknown methods are no-ops."""
    _defaults = {}

    def __init__(self, *a, **kw):
        self._p = dict(self._defaults)
        self._p.update(kw)
        self._name = kw.get('name', type(self).__name__)

    def set_editor_property(self, k, v):
        self._p[k] = v

    def get_editor_property(self, k):
        if k not in self._p:
            raise Exception('no property %s on %s' % (k, type(self).__name__))
        return self._p[k]

    def get_name(self):
        return self._name

    def get_path_name(self):
        return self._p.get('path', '/Game/' + self._name)

    def __getattr__(self, k):
        if k.startswith('_'):
            raise AttributeError(k)
        USED.add('%s.%s' % (type(self).__name__, k))
        if k in self._p:
            return self._p[k]
        return lambda *a, **kw: None


class Name(str):
    pass


class Text(str):
    pass


class Vector(Obj):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        super().__init__()
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __repr__(self):
        return 'Vector(%.0f, %.0f, %.0f)' % (self.x, self.y, self.z)


class Rotator(Obj):
    def __init__(self, roll=0.0, pitch=0.0, yaw=0.0):
        super().__init__()
        self.roll, self.pitch, self.yaw = roll, pitch, yaw


class Transform(Obj):
    def __init__(self, location=None, rotation=None, scale=None):
        super().__init__()
        self.translation, self.rotation, self.scale3d = location, rotation, scale


class LinearColor(Obj):
    def __init__(self, r=0, g=0, b=0, a=1):
        super().__init__()


class Color(Obj):
    pass


class Box(Obj):
    def __init__(self, mn, mx):
        super().__init__()
        self.min, self.max = mn, mx


class StaticMaterial(Obj):
    pass


class StaticMesh(Obj):
    def __init__(self, name, gltf_mesh, gltf):
        super().__init__(name=name)
        self._mesh, self._gltf = gltf_mesh, gltf
        slots = [StaticMaterial(material_slot_name=Name(gltf['materials'][p['material']]['name']),
                                material_interface=None) for p in gltf_mesh['primitives']]
        self._p.update(static_materials=slots, body_setup=Obj(collision_trace_flag=0))

    def get_bounding_box(self):
        mn, mx = [1e18] * 3, [-1e18] * 3
        for p in self._mesh['primitives']:
            a = self._gltf['accessors'][p['attributes']['POSITION']]
            for i in range(3):
                mn[i], mx[i] = min(mn[i], a['min'][i]), max(mx[i], a['max'][i])
        # the importer's conversion: glTF (X, Y, Z) m -> UE (X, Z, Y) * 100 cm
        return Box(Vector(mn[0] * 100, mn[2] * 100, mn[1] * 100), Vector(mx[0] * 100, mx[2] * 100, mx[1] * 100))

    def set_material(self, i, m):
        STATS['set_material'] += 1
        self._p['static_materials'][i].set_editor_property('material_interface', m)


class Texture2D(Obj):
    pass


class Material(Obj):
    pass


class MaterialInstanceConstant(Obj):
    pass


class Component(Obj):
    def set_static_mesh(self, m):
        self._p['static_mesh'] = m


class StaticMeshComponent(Component):
    pass


class HierarchicalInstancedStaticMeshComponent(Component):
    def add_instances(self, transforms, return_indices, world_space):
        assert isinstance(transforms, list) and all(isinstance(t, Transform) for t in transforms)
        STATS['instances'] += len(transforms)


class TextRenderComponent(Component):
    pass


class DirectionalLightComponent(Component):
    pass


class SkyLightComponent(Component):
    pass


class ExponentialHeightFogComponent(Component):
    pass


class Actor(Obj):
    _comp = None

    def __init__(self, location=None, rotation=None):
        super().__init__(tags=[])
        self.loc, self.rot, self.label, self.folder = location, rotation, None, None
        self._components = []
        if self._comp:
            self._components.append(self._comp())

    def get_component_by_class(self, cls):
        for c in self._components:
            if isinstance(c, cls):
                return c
        return None

    def set_actor_label(self, s):
        self.label = s

    def set_folder_path(self, p):
        self.folder = p

    def set_actor_scale3d(self, v):
        assert isinstance(v, Vector)

    def get_actor_scale3d(self):
        return Vector(1, 1, 1)

    def attach_to_actor(self, parent, socket, a, b, c, weld):
        assert isinstance(parent, Actor)


class StaticMeshActor(Actor):
    _comp = StaticMeshComponent

    @property
    def static_mesh_component(self):
        return self._components[0]


class TextRenderActor(Actor):
    _comp = TextRenderComponent

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._p['text_render'] = self._components[0]


class DirectionalLight(Actor):
    _comp = DirectionalLightComponent


class SkyLight(Actor):
    _comp = SkyLightComponent


class ExponentialHeightFog(Actor):
    _comp = ExponentialHeightFogComponent


class SkyAtmosphere(Actor):
    pass


class VolumetricCloud(Actor):
    pass


class PlayerStart(Actor):
    pass


class LandscapeProxy(Actor):
    pass


class EditorActorSubsystem(Obj):
    def spawn_actor_from_class(self, cls, loc, rot):
        assert isinstance(loc, Vector) and isinstance(rot, Rotator), (loc, rot)
        a = cls(loc, rot)
        LEVEL.append(a)
        return a

    def get_all_level_actors(self):
        return list(LEVEL)

    def destroy_actors(self, actors):
        for a in actors:
            LEVEL.remove(a)
        return True


class LevelEditorSubsystem(Obj):
    def new_level(self, path, partitioned=False):
        ASSETS[path] = Obj(name='level')
        return True

    def load_level(self, path):
        return True

    def save_current_level(self):
        return True


class StaticMeshEditorSubsystem(Obj):
    def remove_collisions(self, m):
        STATS['collision'] += 1
        return True


class SubobjectDataSubsystem(Obj):
    def k2_gather_subobject_data_for_instance(self, actor):
        return [Obj(actor=actor)]

    def add_new_subobject(self, params):
        cls = params.get_editor_property('new_class')
        actor = params.get_editor_property('parent_handle').get_editor_property('actor')
        c = cls()
        actor._components.append(c)
        return (Obj(comp=c), Text(''))


class AddNewSubobjectParams(Obj):
    pass


class SubobjectDataBlueprintFunctionLibrary:
    @staticmethod
    def get_data(handle):
        return handle

    @staticmethod
    def get_object(data):
        return data.get_editor_property('comp')


def get_editor_subsystem(cls):
    USED.add('get_editor_subsystem(%s)' % cls.__name__)
    return _SUBS.setdefault(cls, cls())


def get_engine_subsystem(cls):
    USED.add('get_engine_subsystem(%s)' % cls.__name__)
    return _SUBS.setdefault(cls, cls())


_SUBS = {}


class AssetImportTask(Obj):
    _defaults = dict(imported_object_paths=[])


class _AssetTools:
    def import_asset_tasks(self, tasks):
        for t in tasks:
            fn, dest = t.get_editor_property('filename'), t.get_editor_property('destination_path')
            assert os.path.isfile(fn), fn
            IMPORTS.append((os.path.basename(fn), dest))
            paths = []
            if fn.endswith('.png'):
                name = t._p.get('destination_name') or os.path.splitext(os.path.basename(fn))[0]
                ASSETS[dest + '/' + name] = Texture2D(name=name)
                paths.append(dest + '/' + name)
            else:
                g = _glb_json(fn)
                for node in g['nodes']:
                    if 'mesh' in node:
                        p = dest + '/' + node['name']
                        ASSETS[p] = StaticMesh(node['name'], g['meshes'][node['mesh']], g)
                        paths.append(p)
            t.set_editor_property('imported_object_paths', paths)

    def create_asset(self, name, folder, cls, factory):
        a = cls(name=name)
        ASSETS[folder + '/' + name] = a
        return a


class AssetToolsHelpers:
    @staticmethod
    def get_asset_tools():
        return _AssetTools()


class EditorAssetLibrary:
    @staticmethod
    def list_assets(folder, recursive=True, include_folder=False):
        return [p for p in ASSETS if p.startswith(folder + '/')]

    @staticmethod
    def load_asset(p):
        return ASSETS.get(p)

    @staticmethod
    def does_asset_exist(p):
        return p in ASSETS

    @staticmethod
    def save_loaded_asset(a):
        STATS['saved'] += 1
        return True

    @staticmethod
    def save_directory(path, only_if_is_dirty=True, recursive=True):
        return True


class MaterialEditingLibrary:
    @staticmethod
    def create_material_expression(mat, cls, x, y):
        return cls()

    @staticmethod
    def connect_material_property(expr, out, prop):
        return True

    @staticmethod
    def connect_material_expressions(a, ao, b, bi):
        return True

    @staticmethod
    def recompile_material(m):
        pass

    @staticmethod
    def set_material_instance_parent(mi, p):
        pass

    @staticmethod
    def set_material_instance_vector_parameter_value(mi, n, v):
        return True

    @staticmethod
    def set_material_instance_scalar_parameter_value(mi, n, v):
        return True


class ScopedSlowTask:
    def __init__(self, n, msg):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def make_dialog(self, cancel=False):
        pass

    def enter_progress_frame(self, n=1):
        pass


class SystemLibrary:
    @staticmethod
    def get_engine_version():
        return 'stub'


class Paths:
    @staticmethod
    def project_dir():
        return '/nonexistent/'

    @staticmethod
    def convert_relative_path_to_full(p):
        return p


class _Enum:
    def __getattr__(self, k):
        return k


for _n in ('MaterialProperty', 'CollisionTraceFlag', 'CollisionEnabled', 'ComponentMobility', 'AttachmentRule',
           'HorizTextAligment', 'VerticalTextAligment', 'TextureAddress', 'ScriptingCollisionShapeType'):
    setattr(sys.modules[__name__], _n, _Enum())

for _n in ('MaterialExpressionVectorParameter', 'MaterialExpressionScalarParameter', 'MaterialExpressionWorldPosition',
           'MaterialExpressionComponentMask', 'MaterialExpressionConstant2Vector', 'MaterialExpressionAdd',
           'MaterialExpressionMultiply', 'MaterialExpressionTextureSampleParameter2D', 'MaterialExpressionConstant',
           'MaterialFactoryNew', 'MaterialInstanceConstantFactoryNew'):
    setattr(sys.modules[__name__], _n, type(_n, (Obj,), {}))

LOG = []


def log(m):
    LOG.append(('I', m))
    print(m)


def log_warning(m):
    LOG.append(('W', m))
    print('WARNING', m)


def log_error(m):
    LOG.append(('E', m))
    print('ERROR', m)


def __getattr__(name):           # module-level: anything else the script asks for is an error
    raise AttributeError('stub has no unreal.%s - add it only after checking it exists in the UE 5.8 API' % name)
