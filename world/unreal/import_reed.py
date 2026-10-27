"""Reed College -> Unreal Engine level builder (editor script).

Run inside the Unreal Editor (target: UE 5.8; written to also work on 5.4+):
    Tools > Execute Python Script... > pick this file
(needs the "Python Editor Script Plugin"; Interchange and the glTF importer are on by default in 5.x).

It reads the kit next to this file (the folder written by scripts/export_unreal.py) and:
  1. imports the textures and .glb files into /Game/ReedWorld/ (Interchange)
  2. creates materials (walls, roofs, ground with the albedo texture, water, trees, signs)
  3. opens/creates the level /Game/ReedWorld/Maps/L_ReedCampus (CREATE_LEVEL)
  4. places the terrain mesh tiles (TERRAIN_MODE = "mesh"), or prints the Landscape numbers ("landscape")
  5. spawns the buildings, water, 10k+ trees as Hierarchical Instanced Static Meshes, and the signs
     (TextRender with the building name; the full sign text is stored in the actor tags)
  6. adds sun, sky atmosphere, sky light, height fog, clouds and a PlayerStart facing Eliot Hall

Safe to re-run: every actor it makes is tagged "ReedWorld" and removed at the start of the next run;
assets are re-imported in place.

Coordinates (see reed_kit.json): UE.X = east*100, UE.Y = -north*100 (south), UE.Z = elevation*100, cm.
"""
import csv
import json
import math
import os
import random
import re

import unreal

# ------------------------------------------------------------------ settings
TERRAIN_MODE = 'mesh'          # 'mesh' = spawn reed_terrain.glb tiles; 'landscape' = you import the heightmap
CREATE_LEVEL = True            # build into /Game/ReedWorld/Maps/L_ReedCampus (created if missing)
SPAWN_NEIGHBOURHOOD = True     # the ~490 houses around campus
TREES = True
SIGNS = True
REIMPORT = True                # False = reuse already-imported assets (faster second runs)
TEXT_YAW_OFFSET = 0.0          # if sign text reads mirrored/backwards in your version, set to 180.0
TREE_COLLISION = False         # trees are walk-through by default (their crowns are low ellipsoids)

ROOT = '/Game/ReedWorld'
LEVEL_PATH = ROOT + '/Maps/L_ReedCampus'
TAG = 'ReedWorld'


# ------------------------------------------------------------------ logging / helpers
def log(msg):
    unreal.log('[ReedWorld] ' + msg)


def warn(msg):
    unreal.log_warning('[ReedWorld] ' + msg)


def err(msg):
    unreal.log_error('[ReedWorld] ' + msg)


def kit_dir():
    """Folder holding reed_kit.json: next to this script, else <Project>/ReedWorldKit."""
    cands = []
    try:
        cands.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    try:
        cands.append(os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()),
                                  'ReedWorldKit'))
    except Exception:
        pass
    for c in cands:
        if os.path.isfile(os.path.join(c, 'reed_kit.json')):
            return c
    raise RuntimeError('reed_kit.json not found next to import_reed.py or in <Project>/ReedWorldKit: '
                       'copy the whole world/unreal folder and run the script from inside it')


def read_csv(path):
    with open(path, newline='', encoding='utf-8') as f:
        return list(csv.DictReader(f))


def vec(x, y, z):
    return unreal.Vector(x=float(x), y=float(y), z=float(z))


def rot(yaw=0.0, pitch=0.0, roll=0.0):
    return unreal.Rotator(roll=float(roll), pitch=float(pitch), yaw=float(yaw))


def set_props(obj, **props):
    """set_editor_property for each, logging (not raising) on a version difference."""
    for k, v in props.items():
        try:
            obj.set_editor_property(k, v)
        except Exception as e:
            warn('could not set %s.%s (%s)' % (type(obj).__name__, k, e))


def actor_sub():
    try:
        return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    except Exception:
        return None


def level_sub():
    try:
        return unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    except Exception:
        return None


def all_actors():
    s = actor_sub()
    if s:
        return list(s.get_all_level_actors())
    return list(unreal.EditorLevelLibrary.get_all_level_actors())


def destroy(actors):
    s = actor_sub()
    if not actors:
        return
    if s:
        try:
            s.destroy_actors(actors)
            return
        except Exception:
            for a in actors:
                s.destroy_actor(a)
            return
    for a in actors:
        unreal.EditorLevelLibrary.destroy_actor(a)


def spawn(cls, location, rotation=None):
    rotation = rotation or rot()
    s = actor_sub()
    if s:
        return s.spawn_actor_from_class(cls, location, rotation)
    return unreal.EditorLevelLibrary.spawn_actor_from_class(cls, location, rotation)


def tag(actor, folder, label=None, extra=()):
    tags = [unreal.Name(TAG)] + [unreal.Name(t[:1000]) for t in extra]
    set_props(actor, tags=tags)
    if label:
        try:
            actor.set_actor_label(label)
        except Exception:
            pass
    try:
        actor.set_folder_path(unreal.Name('ReedWorld/' + folder))
    except Exception:
        pass


def mesh_component(actor):
    try:
        return actor.static_mesh_component
    except Exception:
        return actor.get_component_by_class(unreal.StaticMeshComponent)


# ------------------------------------------------------------------ assets
def asset_tools():
    return unreal.AssetToolsHelpers.get_asset_tools()


def import_file(path, dest, name=None):
    """Import one file with an automated AssetImportTask (Interchange handles .glb and .png in 5.x)."""
    t = unreal.AssetImportTask()
    set_props(t, filename=path, destination_path=dest, replace_existing=True, automated=True, save=True)
    if name:
        set_props(t, destination_name=name)
    asset_tools().import_asset_tasks([t])
    paths = []
    try:
        paths = list(t.get_editor_property('imported_object_paths'))
    except Exception:
        pass
    if not paths:
        try:
            paths = [o.get_path_name() for o in t.get_objects()]
        except Exception:
            pass
    log('imported %s -> %s (%d objects)' % (os.path.basename(path), dest, len(paths)))
    return paths


def static_meshes_in(folder):
    out = {}
    for p in unreal.EditorAssetLibrary.list_assets(folder, recursive=True, include_folder=False):
        a = unreal.EditorAssetLibrary.load_asset(p)
        if isinstance(a, unreal.StaticMesh):
            out[a.get_name()] = a
    return out


def find_mesh(meshes, key):
    """Interchange may prefix (SM_) or suffix asset names; match on the glTF node/mesh name."""
    if key in meshes:
        return meshes[key]
    pat = re.compile(r'(^|[^A-Za-z0-9])' + re.escape(key) + r'($|[^0-9])')
    for n, m in meshes.items():
        if pat.search(n):
            return m
    return None


def ensure_material(name, folder, build):
    path = folder + '/' + name
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.EditorAssetLibrary.load_asset(path)
    mat = asset_tools().create_asset(name, folder, unreal.Material, unreal.MaterialFactoryNew())
    build(mat)
    unreal.MaterialEditingLibrary.recompile_material(mat)
    unreal.EditorAssetLibrary.save_loaded_asset(mat)
    return mat


def build_base(mat):
    """M_ReedBase: VectorParameter Color -> Base Color, ScalarParameter Roughness."""
    MEL = unreal.MaterialEditingLibrary
    c = MEL.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -400, 0)
    set_props(c, parameter_name='Color', default_value=unreal.LinearColor(0.6, 0.6, 0.6, 1.0))
    MEL.connect_material_property(c, '', unreal.MaterialProperty.MP_BASE_COLOR)
    r = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -400, 200)
    set_props(r, parameter_name='Roughness', default_value=0.85)
    MEL.connect_material_property(r, '', unreal.MaterialProperty.MP_ROUGHNESS)


def make_ground_builder(texture, kit):
    """M_ReedGround: albedo sampled by world position, so it works on the mesh tiles AND on a Landscape.
    U = X / 127400, V = (Y + 81100) / 81100 (the texture covers world x 0..1274 m, y 0..811 m)."""
    gx = kit['ground_texture']['covers_world_m']['x'][1] * 100.0
    gy = kit['ground_texture']['covers_world_m']['y'][1] * 100.0

    def build(mat):
        MEL = unreal.MaterialEditingLibrary
        wp = MEL.create_material_expression(mat, unreal.MaterialExpressionWorldPosition, -1100, 0)
        mask = MEL.create_material_expression(mat, unreal.MaterialExpressionComponentMask, -900, 0)
        set_props(mask, r=True, g=True, b=False, a=False)
        off = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant2Vector, -900, 150)
        set_props(off, r=0.0, g=gy)
        add = MEL.create_material_expression(mat, unreal.MaterialExpressionAdd, -700, 0)
        scl = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant2Vector, -700, 150)
        set_props(scl, r=1.0 / gx, g=1.0 / gy)
        mul = MEL.create_material_expression(mat, unreal.MaterialExpressionMultiply, -500, 0)
        tex = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -300, 0)
        set_props(tex, parameter_name='Albedo', texture=texture)
        MEL.connect_material_expressions(wp, '', mask, '')
        MEL.connect_material_expressions(mask, '', add, 'A')
        MEL.connect_material_expressions(off, '', add, 'B')
        MEL.connect_material_expressions(add, '', mul, 'A')
        MEL.connect_material_expressions(scl, '', mul, 'B')
        MEL.connect_material_expressions(mul, '', tex, 'UVs')
        MEL.connect_material_property(tex, 'RGB', unreal.MaterialProperty.MP_BASE_COLOR)
        r = MEL.create_material_expression(mat, unreal.MaterialExpressionConstant, -300, 250)
        set_props(r, r=0.95)
        MEL.connect_material_property(r, '', unreal.MaterialProperty.MP_ROUGHNESS)
    return build


def material_instance(name, folder, parent, color=None, rough=None):
    path = folder + '/' + name
    MEL = unreal.MaterialEditingLibrary
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        mi = unreal.EditorAssetLibrary.load_asset(path)
    else:
        mi = asset_tools().create_asset(name, folder, unreal.MaterialInstanceConstant,
                                        unreal.MaterialInstanceConstantFactoryNew())
    MEL.set_material_instance_parent(mi, parent)
    if color is not None:
        MEL.set_material_instance_vector_parameter_value(mi, 'Color', unreal.LinearColor(color[0], color[1], color[2], 1.0))
    if rough is not None:
        MEL.set_material_instance_scalar_parameter_value(mi, 'Roughness', rough)
    unreal.EditorAssetLibrary.save_loaded_asset(mi)
    return mi


def assign_slots(mesh, rules, default=None):
    """rules: list of (substring, material). Matches the slot name, then the imported material's name."""
    try:
        slots = list(mesh.get_editor_property('static_materials'))
    except Exception as e:
        warn('cannot read material slots of %s (%s)' % (mesh.get_name(), e))
        return
    for i, s in enumerate(slots):
        names = [str(s.get_editor_property('material_slot_name'))]
        try:
            mi = s.get_editor_property('material_interface')
            if mi:
                names.append(mi.get_name())
        except Exception:
            pass
        pick = default
        for sub, mat in rules:
            if any(sub.lower() in n.lower() for n in names):
                pick = mat
                break
        if pick is not None:
            try:
                mesh.set_material(i, pick)
            except Exception:
                s.set_editor_property('material_interface', pick)
                slots[i] = s
                mesh.set_editor_property('static_materials', slots)


def collision(mesh, complex_as_simple=True):
    """Walkable meshes: drop any generated simple hull and collide against the triangles."""
    sub = None
    try:
        sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
    except Exception:
        pass
    try:
        if sub:
            sub.remove_collisions(mesh)
        else:
            unreal.EditorStaticMeshLibrary.remove_collisions(mesh)
    except Exception:
        pass
    try:
        body = mesh.get_editor_property('body_setup')
        body.set_editor_property('collision_trace_flag',
                                 unreal.CollisionTraceFlag.CTF_USE_COMPLEX_AS_SIMPLE if complex_as_simple
                                 else unreal.CollisionTraceFlag.CTF_USE_DEFAULT)
        return True
    except Exception as e:
        warn('could not set complex-as-simple collision on %s (%s); falling back to a box' % (mesh.get_name(), e))
        try:
            shape = unreal.ScriptingCollisionShapeType.BOX
            if sub:
                sub.add_simple_collisions(mesh, shape)
            else:
                unreal.EditorStaticMeshLibrary.add_simple_collisions(mesh, shape)
        except Exception:
            pass
        return False


# ------------------------------------------------------------------ axis/unit self-check
def check_axes(mesh, ref):
    """Compare the imported Eliot Hall bounds with reed_kit.json; return the actor scale that corrects
    a units or handedness difference in the importer (expected: (1, 1, 1))."""
    try:
        box = mesh.get_bounding_box()
        mn, mx = box.min, box.max
    except Exception as e:
        warn('cannot read bounds for the axis self-check (%s); assuming the documented glTF mapping' % e)
        return (1.0, 1.0, 1.0)
    rmin, rmax = ref['min_cm'], ref['max_cm']
    got = [mn.x, mn.y, mn.z, mx.x, mx.y, mx.z]

    def close(a, b, tol):
        return all(abs(p - q) <= tol for p, q in zip(a, b))
    # candidate importer behaviours: units off by 100 either way, and/or the Y axis mirrored.
    # The correcting actor scale is (k, k*sy, k).
    cands = []
    for k in (1.0, 100.0, 0.01):
        for sy in (1.0, -1.0):
            ys = sorted([sy * rmin[1] / k, sy * rmax[1] / k])
            exp = [rmin[0] / k, ys[0], rmin[2] / k, rmax[0] / k, ys[1], rmax[2] / k]
            cands.append(((k, k * sy, k), exp, 60.0 / k))
    for s, exp, tol in cands:
        if close(got, exp, tol):
            if s == (1.0, 1.0, 1.0):
                log('axis self-check OK: Eliot Hall bounds match reed_kit.json within %.0f cm' % tol)
            else:
                warn('axis self-check: importer output differs from the documented mapping; correcting with '
                     'actor scale %s' % (s,))
            return s
    err('axis self-check FAILED: Eliot Hall imported with bounds %s, expected %s..%s. Placing at the origin '
        'anyway; check the glTF import options (Interchange: no extra rotation/scale, "Bake Meshes" on).'
        % ([round(v) for v in got], rmin, rmax))
    return (1.0, 1.0, 1.0)


# ------------------------------------------------------------------ actor helpers
def spawn_mesh(mesh, folder, label, scale=(1.0, 1.0, 1.0), loc=(0, 0, 0), yaw=0.0, extra_tags=(),
               collide=True, visible=True):
    a = spawn(unreal.StaticMeshActor, vec(*loc), rot(yaw=yaw))
    c = mesh_component(a)
    c.set_static_mesh(mesh)
    if scale != (1.0, 1.0, 1.0):
        a.set_actor_scale3d(vec(*scale))
    if not collide:
        try:
            c.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        except Exception:
            pass
    if not visible:
        try:
            c.set_visibility(False, False)
        except Exception:
            set_props(c, visible=False)
    tag(a, folder, label, extra_tags)
    return a


def add_hism(host, mesh):
    """Add a HierarchicalInstancedStaticMeshComponent to an actor through the SubobjectDataSubsystem."""
    sds = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    handles = sds.k2_gather_subobject_data_for_instance(host)
    params = unreal.AddNewSubobjectParams(parent_handle=handles[0],
                                          new_class=unreal.HierarchicalInstancedStaticMeshComponent,
                                          blueprint_context=None)
    res = sds.add_new_subobject(params)
    handle = res[0] if isinstance(res, (tuple, list)) else res
    BFL = unreal.SubobjectDataBlueprintFunctionLibrary
    comp = BFL.get_object(BFL.get_data(handle))
    if comp is None:
        raise RuntimeError('SubobjectDataSubsystem returned no component')
    comp.set_static_mesh(mesh)
    return comp


def add_instances(comp, transforms):
    try:
        comp.add_instances(transforms, False, True)       # (transforms, return indices, world space)
        return
    except Exception as e:
        warn('add_instances failed (%s); adding one by one' % e)
    for t in transforms:
        try:
            comp.add_instance(t, True)
        except TypeError:
            comp.add_instance(t)


# ------------------------------------------------------------------ level
def open_level():
    if not CREATE_LEVEL:
        return
    ls = level_sub()
    if unreal.EditorAssetLibrary.does_asset_exist(LEVEL_PATH):
        if ls:
            ls.load_level(LEVEL_PATH)
        else:
            unreal.EditorLevelLibrary.load_level(LEVEL_PATH)
        log('opened ' + LEVEL_PATH)
        return
    ok = False
    if ls:
        try:
            ok = ls.new_level(LEVEL_PATH, False)        # (path, is_partitioned_world) in 5.1+
        except TypeError:
            ok = ls.new_level(LEVEL_PATH)
    else:
        ok = unreal.EditorLevelLibrary.new_level(LEVEL_PATH)
    log(('created ' if ok else 'could not create ') + LEVEL_PATH)


def save_level():
    ls = level_sub()
    try:
        if ls:
            ls.save_current_level()
        else:
            unreal.EditorLevelLibrary.save_current_level()
    except Exception as e:
        warn('save the level yourself (File > Save Current Level): %s' % e)


def environment(kit):
    """Sun, sky atmosphere, real-time sky light, height fog, clouds."""
    ps = kit['player_start']['location_cm']
    sun = spawn(unreal.DirectionalLight, vec(ps[0], ps[1], ps[2] + 2000), rot(yaw=-45.0, pitch=-38.0))
    lc = sun.get_component_by_class(unreal.DirectionalLightComponent)
    if lc:
        set_props(lc, atmosphere_sun_light=True, intensity=10.0)
        try:
            lc.set_mobility(unreal.ComponentMobility.MOVABLE)
        except Exception:
            pass
    tag(sun, 'Lighting', 'Reed_Sun')
    sky = spawn(unreal.SkyAtmosphere, vec(ps[0], ps[1], 0))
    tag(sky, 'Lighting', 'Reed_SkyAtmosphere')
    sl = spawn(unreal.SkyLight, vec(ps[0], ps[1], ps[2] + 1500))
    slc = sl.get_component_by_class(unreal.SkyLightComponent)
    if slc:
        try:
            slc.set_mobility(unreal.ComponentMobility.MOVABLE)
        except Exception:
            pass
        set_props(slc, real_time_capture=True)
    tag(sl, 'Lighting', 'Reed_SkyLight')
    fog = spawn(unreal.ExponentialHeightFog, vec(ps[0], ps[1], 1500))
    fc = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
    if fc:
        set_props(fc, fog_density=0.02, fog_height_falloff=0.2)
    tag(fog, 'Lighting', 'Reed_HeightFog')
    try:
        cl = spawn(unreal.VolumetricCloud, vec(ps[0], ps[1], 0))
        tag(cl, 'Lighting', 'Reed_Clouds')
    except Exception as e:
        warn('no VolumetricCloud (%s)' % e)


def landscape_help(kit):
    log('---- Landscape option (TERRAIN_MODE = "landscape") -------------------------------')
    for size, L in sorted(kit['landscape'].items()):
        log('%s: %s  Location %s  Rotation 0,0,0  Scale X %s Y %s Z %s  (%s)' % (
            size, L['file'], L['ui_location_cm'], L['scale']['x'], L['scale']['y'], L['scale']['z'],
            L['component_layout']))
        log('     after creation the Landscape actor Location should read %s' % (L['actor_location_cm'],))
    log('Re-run this script after creating the Landscape: it assigns MI_ReedGround and fixes the location.')


def fix_landscapes(kit, ground_mi):
    found = [a for a in all_actors() if isinstance(a, unreal.LandscapeProxy)]
    for a in found:
        s = a.get_actor_scale3d()
        for size, L in kit['landscape'].items():
            if abs(s.x - L['scale']['x']) < 0.01 and abs(s.z - L['scale']['z']) < 0.01:
                loc = L['actor_location_cm']
                cur = a.get_actor_location()
                if abs(cur.x - loc['x']) > 1 or abs(cur.y - loc['y']) > 1 or abs(cur.z - loc['z']) > 1:
                    warn('moving Landscape %s from %s to the kit location %s' % (a.get_name(), cur, loc))
                    a.set_actor_location(vec(loc['x'], loc['y'], loc['z']), False, False)
        set_props(a, landscape_material=ground_mi)
        log('Landscape %s: material set to %s' % (a.get_name(), ground_mi.get_name()))
    return found


# ------------------------------------------------------------------ main
def wrap(text, width=16, lines=3):
    out, cur = [], ''
    for w in text.split():
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = (cur + ' ' + w).strip()
    if cur:
        out.append(cur)
    if len(out) > lines:
        out = out[:lines]
        out[-1] = out[-1][:width - 3] + '...'
    return '\n'.join(out)


def main():
    K = kit_dir()
    kit = json.load(open(os.path.join(K, 'reed_kit.json'), encoding='utf-8'))
    log('kit: %s (%s)' % (K, kit.get('source_manifest')))
    try:
        log('engine %s' % unreal.SystemLibrary.get_engine_version())
    except Exception:
        pass

    # ---- 1. import
    folders = dict(tex=ROOT + '/Textures', mat=ROOT + '/Materials', terrain=ROOT + '/Terrain',
                   bld=ROOT + '/Buildings', water=ROOT + '/Water', props=ROOT + '/Props')
    tex_path = folders['tex'] + '/T_ReedGround'
    if REIMPORT or not unreal.EditorAssetLibrary.does_asset_exist(tex_path):
        import_file(os.path.join(K, 'T_ReedGround.png'), folders['tex'], 'T_ReedGround')
    texture = unreal.EditorAssetLibrary.load_asset(tex_path)
    if texture is None:
        raise RuntimeError('T_ReedGround did not import')
    set_props(texture, address_x=unreal.TextureAddress.TA_CLAMP, address_y=unreal.TextureAddress.TA_CLAMP)

    glbs = [('reed_buildings.glb', folders['bld']), ('reed_water.glb', folders['water']),
            ('reed_tree.glb', folders['props']), ('reed_sign.glb', folders['props'])]
    if TERRAIN_MODE == 'mesh':
        glbs.insert(0, ('reed_terrain.glb', folders['terrain']))
    for fn, dest in glbs:
        if REIMPORT or not static_meshes_in(dest):
            with unreal.ScopedSlowTask(1, 'Importing ' + fn) as task:
                task.make_dialog(False)
                import_file(os.path.join(K, fn), dest)
                task.enter_progress_frame(1)
    meshes = {}
    for dest in set(d for _, d in glbs):
        meshes.update(static_meshes_in(dest))
    log('%d static meshes under %s' % (len(meshes), ROOT))

    # ---- 2. materials
    base = ensure_material('M_ReedBase', folders['mat'], build_base)
    ground = ensure_material('M_ReedGround', folders['mat'], make_ground_builder(texture, kit))
    MI = dict(
        wall_c=material_instance('MI_Wall_Campus', folders['mat'], base, (0.50, 0.36, 0.27), 0.85),
        roof_c=material_instance('MI_Roof_Campus', folders['mat'], base, (0.20, 0.18, 0.18), 0.8),
        wall_n=material_instance('MI_Wall_Neighbourhood', folders['mat'], base, (0.62, 0.60, 0.55), 0.9),
        roof_n=material_instance('MI_Roof_Neighbourhood', folders['mat'], base, (0.30, 0.28, 0.28), 0.85),
        water=material_instance('MI_Water', folders['mat'], base, (0.04, 0.12, 0.14), 0.05),
        bark=material_instance('MI_Bark', folders['mat'], base, (0.12, 0.07, 0.04), 0.95),
        leaves=material_instance('MI_Leaves', folders['mat'], base, (0.06, 0.15, 0.04), 0.9),
        board=material_instance('MI_SignBoard', folders['mat'], base, (0.85, 0.82, 0.70), 0.7),
        post=material_instance('MI_SignPost', folders['mat'], base, (0.08, 0.06, 0.04), 0.9),
        edge=material_instance('MI_EdgeWall', folders['mat'], base, (0.8, 0.1, 0.1), 1.0))
    MI['ground'] = ground

    # ---- 3. level
    open_level()
    old = [a for a in all_actors() if TAG in [str(t) for t in a.get_editor_property('tags')]]
    if old:
        log('removing %d actors from the previous run' % len(old))
        destroy(old)

    # ---- axis self-check on Eliot Hall
    ref = kit['reference']
    eliot = find_mesh(meshes, ref['mesh'])
    S = check_axes(eliot, ref) if eliot else (1.0, 1.0, 1.0)
    if eliot is None:
        warn('reference mesh %s not found; skipping the axis self-check' % ref['mesh'])
    inst_scale = abs(S[0])

    # ---- 4. terrain
    if TERRAIN_MODE == 'mesh':
        n = 0
        for name in kit['terrain']['tiles']:
            m = find_mesh(meshes, name)
            if m is None:
                warn('terrain tile %s missing' % name)
                continue
            assign_slots(m, [('Ground', ground)], default=ground)
            collision(m)
            spawn_mesh(m, 'Terrain', name, scale=S)
            n += 1
        for side in 'NSEW':
            m = find_mesh(meshes, 'T_EdgeWall_' + side)
            if m:
                assign_slots(m, [], default=MI['edge'])
                collision(m)
                spawn_mesh(m, 'Terrain', 'T_EdgeWall_' + side, scale=S, visible=False)
        log('terrain: %d mesh tiles + invisible edge walls' % n)
        if [a for a in all_actors() if isinstance(a, unreal.LandscapeProxy)]:
            warn('this level also has a Landscape; set TERRAIN_MODE = "landscape" or delete one of them')
    else:
        if not fix_landscapes(kit, ground):
            landscape_help(kit)

    # ---- 5. buildings
    rows = read_csv(os.path.join(K, 'buildings.csv'))
    nb = 0
    for r in rows:
        if r['realm'] == 'neighbourhood' and not SPAWN_NEIGHBOURHOOD:
            continue
        m = find_mesh(meshes, r['mesh'])
        if m is None:
            warn('building mesh %s missing' % r['mesh'])
            continue
        campus = r['realm'] == 'campus'
        assign_slots(m, [('Wall', MI['wall_c'] if campus else MI['wall_n']),
                         ('Roof', MI['roof_c'] if campus else MI['roof_n'])])
        collision(m)
        label = r['mesh'] + (' ' + r['name'] if r['name'] else '')
        spawn_mesh(m, 'Buildings/' + ('Campus' if campus else 'Neighbourhood'), label, scale=S,
                   extra_tags=['osm:' + r['osm_id']] + (['name:' + r['name']] if r['name'] else []))
        nb += 1
    log('buildings: %d' % nb)

    # ---- water (no collision: the lake bed is carved 1.5 m into the terrain, you can wade)
    for r in read_csv(os.path.join(K, 'water.csv')):
        m = find_mesh(meshes, r['mesh'])
        if m is None:
            warn('water mesh %s missing' % r['mesh'])
            continue
        assign_slots(m, [], default=MI['water'])
        spawn_mesh(m, 'Water', r['mesh'] + (' ' + r['name'] if r['name'] else ''), scale=S, collide=False,
                   extra_tags=['osm:' + r['osm_id'], 'surface_z_cm:' + r['surface_z_cm']])

    # ---- trees: one HISM per form
    if TREES:
        trees = read_csv(os.path.join(K, 'trees.csv'))
        rng = random.Random(7)
        for form, mname in (('broadleaf', 'SM_Tree_Broadleaf'), ('conifer', 'SM_Tree_Conifer')):
            m = find_mesh(meshes, mname)
            sel = [t for t in trees if t['form'] == form]
            if m is None or not sel:
                warn('no %s trees (%s)' % (form, 'mesh missing' if m is None else 'none in csv'))
                continue
            assign_slots(m, [('Bark', MI['bark']), ('Leaves', MI['leaves'])])
            xf = [unreal.Transform(location=vec(t['x_cm'], t['y_cm'], t['z_cm']),
                                   rotation=rot(yaw=rng.uniform(0, 360)),
                                   scale=vec(float(t['radius_cm']) / 100.0 * inst_scale,
                                             float(t['radius_cm']) / 100.0 * inst_scale,
                                             float(t['height_cm']) / 100.0 * inst_scale)) for t in sel]
            host = spawn(unreal.StaticMeshActor, vec(0, 0, 0))
            tag(host, 'Trees', 'Trees_' + form, ['trees:%d' % len(sel)])
            try:
                comp = add_hism(host, m)
                if not TREE_COLLISION:
                    comp.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
                add_instances(comp, xf)
                log('trees: %d %s instances in one HISM' % (len(sel), form))
            except Exception as e:
                warn('HISM path failed (%s); falling back to individual actors for the 400 tallest %s trees'
                     % (e, form))
                destroy([host])
                sel_t = sorted(zip(sel, xf), key=lambda p: -float(p[0]['height_cm']))[:400]
                for t, x in sel_t:
                    a = spawn_mesh(m, 'Trees', None, collide=TREE_COLLISION,
                                   loc=(float(t['x_cm']), float(t['y_cm']), float(t['z_cm'])))
                    a.set_actor_scale3d(x.scale3d)

    # ---- signs
    if SIGNS:
        sm = find_mesh(meshes, 'SM_SignPost')
        if sm is not None:
            assign_slots(sm, [('Board', MI['board']), ('Post', MI['post'])])
            collision(sm)
        board = kit['sign_board']
        for s in read_csv(os.path.join(K, 'signs.csv')):
            x, y, z, yaw = float(s['x_cm']), float(s['y_cm']), float(s['z_cm']), float(s['yaw_deg'])
            tags_ = ['ReedSign', 'osm:' + s['osm_id'], 'name:' + s['name'], 'map_no:' + s['map_no'],
                     'door_estimated:' + s['door_estimated'], 'text:' + s['text']]
            if sm is not None:
                post = spawn_mesh(sm, 'Signs', 'Sign ' + s['name'], loc=(x, y, z), yaw=yaw, extra_tags=tags_,
                                  scale=(inst_scale, inst_scale, inst_scale))
            else:
                post = None
            # text just in front of the board face (+X of the sign), at board height
            th = math.radians(yaw)
            dx = board['thick_m'] * 50 + 1.5
            tx, ty = x + dx * math.cos(th), y + dx * math.sin(th)
            tz = z + board['center_z_m'] * 100
            ta = spawn(unreal.TextRenderActor, vec(tx, ty, tz), rot(yaw=yaw + TEXT_YAW_OFFSET))
            tc = None
            try:
                tc = ta.get_editor_property('text_render')
            except Exception:
                tc = ta.get_component_by_class(unreal.TextRenderComponent)
            label = s['name'] + ('  (' + s['map_no'] + ')' if s['map_no'] else '')
            txt = wrap(label)
            try:
                tc.set_editor_property('text', unreal.Text(txt))
            except Exception:
                try:
                    tc.k2_set_text(unreal.Text(txt))
                except Exception:
                    tc.set_text(txt)
            set_props(tc, world_size=15.0, text_render_color=unreal.Color(r=30, g=26, b=22, a=255),
                      horizontal_alignment=unreal.HorizTextAligment.EHTA_CENTER,
                      vertical_alignment=unreal.VerticalTextAligment.EVRTA_TEXT_CENTER)
            tag(ta, 'Signs', 'SignText ' + s['name'], tags_)
            if post is not None:
                try:
                    ta.attach_to_actor(post, unreal.Name(''), unreal.AttachmentRule.KEEP_WORLD,
                                       unreal.AttachmentRule.KEEP_WORLD, unreal.AttachmentRule.KEEP_WORLD, False)
                except Exception as e:
                    warn('could not attach sign text (%s)' % e)
        log('signs: %d' % len(read_csv(os.path.join(K, 'signs.csv'))))

    # ---- 6. light + player start
    environment(kit)
    p = kit['player_start']
    ps = spawn(unreal.PlayerStart, vec(*p['location_cm']), rot(yaw=p['yaw_deg']))
    tag(ps, 'Gameplay', 'PlayerStart_EliotCircle')
    log('PlayerStart at %s yaw %.1f (south of Eliot Circle, facing Eliot Hall)' % (p['location_cm'], p['yaw_deg']))

    unreal.EditorAssetLibrary.save_directory(ROOT, only_if_is_dirty=True, recursive=True)
    save_level()
    log('done. Press Play (Alt+P). With the Third Person template the character spawns at the PlayerStart.')


main()
