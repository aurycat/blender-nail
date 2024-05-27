# MIT License
#
# Copyright (c) 2024 aurycat
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# History:


bl_info = {
    "name": "Nail UVs",
    "description": "Implements world-space automatic UV unwrapping similar to Valve's Hammer level editor",
    "author": "aurycat",
    "version": (1, 0),
    "blender": (4, 1, 1), # Minimum tested version. Might work with older.
    "location": "View3D > Nail (Edit mode)",
    "warning": "",
    "doc_url": "",
    "tracker_url": "",
    "support": "COMMUNITY",
    "category": "UV",
}

import bpy
import bmesh
import math
import enum
from bpy.types import Operator
from bpy.app.handlers import persistent
from mathutils import Vector, Matrix

# bmesh apparently doesn't have an API for per-face '2D Vector'
# attributes, so these need to use 'Vector' (float_vector) instead.
# Vector has 3 dimensions, so 'rotation' and 'alignment' can be
# shoved into those extra Z coordinates.
# Flags are a bitmask of the TCFLAG_* constants below (yes it's a bitmask stored
# in the z axis of a float vector, I'm sorry okay!)
# Rotation is stored in radians
ATTR_SHIFT_FLAGS = "Nail_ShiftFlags" # per-face Vector(X Shift, Y Shift, Flags)
ATTR_SCALE_ROT   = "Nail_ScaleRot"   # per-face Vector(X Scale, Y Scale, Rotation)
ATTR_UAXIS       = "Nail_UAxis"      # per-face Vector U axis
ATTR_VAXIS       = "Nail_VAxis"      # per-face Vector V axis

FACE_FLOAT_VECTOR_ATTRS = [ATTR_SHIFT_FLAGS, ATTR_SCALE_ROT, ATTR_UAXIS, ATTR_VAXIS]

# TextureConfig flags. The bitmask is stored per-face, in the z coordinate of Nail_ShiftFlags
TCFLAG_ENABLED = 1        # True to use Nail on this face, otherwise Nail will ignore it.
                          # The default is False, so Nail is "opt-in".
TCFLAG_LOCK_AXIS = 2      # True to not recompute the UV axis when (re)applying a face's
                          # texture. The default is to recompute it every apply.
TCFLAG_OBJECT_SPACE = 4   # True to compute UV axis in object-space. Default is world-space.
TCFLAG_ALIGN_FACE = 8     # True to have the UV axis aligned to the face instead. The default
                          # is to use the coordinate system (world or object-space) axes.

#ALIGN_NONE = -1       # This plugin will never change the UVs on this face
#ALIGN_WORLD = 0       # World-space UV alignment. This is like Hammer's default behavior for brushes.
#                      # (https://developer.valvesoftware.com/wiki/Texture_alignment#World_Alignment)
#                      # It is effectively a Cube Projection where the center of the "cube" is the
#                      # world origin, and scale and rotation are identity.
#ALIGN_OBJECT = 1      # Object-space UV alignment. Again like Cube Projection, except the position,
#                      # rotation, and scale of the "cube" are that of the object transform.
#ALIGN_WORLD_FACE = 2  # This is like ALIGN_WORLD, except the "cube" is rotated to be aligned
#                      # to the normal of the face.
#ALIGN_OBJECT_FACE = 3 # This is like ALIGN_OBJECT, except the "cube" is rotated to be aligned
#                      # to the normal of the face.
#ALIGN_ENABLE_BIT = 1
#ALIGN_SPACE_BIT = 2
#ALIGN_PLANE_BIT = 4


############
### Init ###
############

def main():
    # Invoke unregister op on an existing "install" of the plugin before
    # re-registering. Lets you press the "Run Script" button without having
    # to maually unregister or run Blender > Reload Scripts first.
    if ('aurycat' in dir(bpy.ops)) and ('nail_unregister' in dir(bpy.ops.aurycat)):
            bpy.ops.aurycat.nail_unregister()
    register()

def register():
    bpy.utils.register_class(NAIL_OT_set_tex_transform)
    bpy.utils.register_class(NAIL_OT_unregister)
    bpy.utils.register_class(NAIL_MT_main_menu)
    bpy.utils.register_class(NAIL_OT_clear_tex_transform)
    bpy.utils.register_class(NAIL_OT_apply_tex_transform)
    bpy.utils.register_class(NailSettings)
    bpy.types.VIEW3D_PT_view3d_lock.append(draw_lock_rotation)
    bpy.types.VIEW3D_MT_editor_menus.append(nail_draw_main_menu)

    bpy.types.WindowManager.nail_settings = bpy.props.PointerProperty(name='Nail Settings', type=NailSettings)
    auto_apply_updated(None, bpy.context)
    WRAP_UVS = bpy.context.window_manager.nail_settings.wrap_uvs

def unregister():
    # Set to None before unregistering NailSceneSettings to avoid Blender crash
    bpy.types.WindowManager.nail_settings = None

    enable_post_depsgraph_update_handler(False)

    bpy.types.VIEW3D_PT_view3d_lock.remove(draw_lock_rotation)
    bpy.types.VIEW3D_MT_editor_menus.remove(nail_draw_main_menu)
    safe_unregister_class(NAIL_OT_unregister)
    safe_unregister_class(NAIL_OT_set_tex_transform)
    safe_unregister_class(NAIL_MT_main_menu)
    safe_unregister_class(NAIL_OT_clear_tex_transform)
    safe_unregister_class(NAIL_OT_apply_tex_transform)
    safe_unregister_class(NailSettings)

# Don't error out if the class wasn't registered
def safe_unregister_class(cls):
    try:
        bpy.utils.unregister_class(cls)
    except RuntimeError:
        pass

class NAIL_OT_unregister(Operator):
    bl_idname = "aurycat.nail_unregister"
    bl_label = "Unregister"
    bl_options = {"REGISTER"}

    def execute(self, context):
        unregister()
        return {'FINISHED'}

def nail_draw_main_menu(self, context):
    if context.mode == 'EDIT_MESH':
        self.layout.menu(NAIL_MT_main_menu.bl_idname)

def draw_lock_rotation(self, context):
    layout = self.layout
    view = context.space_data
    col = layout.column(align=True)
    col.prop(view.region_3d, "lock_rotation", text="Lock View Rotation")


################
### Settings ###
################

def auto_apply_updated(_, context):
    auto_apply = False
    if 'nail_settings' in context.window_manager:
        if 'auto_apply' in context.window_manager.nail_settings:
            auto_apply = context.window_manager.nail_settings.auto_apply
    enable_post_depsgraph_update_handler(auto_apply)

class NailSettings(bpy.types.PropertyGroup):
    auto_apply: bpy.props.BoolProperty(name="Auto-Apply Transforms", default=False, update=auto_apply_updated,
        description="Automatically applies the current transform as objects are transformed or meshes are updated. While in edit mode, only applies to selected faces or their adjacent faces, for efficiency")
    fast_updates: bpy.props.BoolProperty(name="Fast Update Rate", default=False,
        description="Makes Auto Apply update faster, potentially slowing down Blender while editing larger meshes")
    wrap_uvs: bpy.props.BoolProperty(name="Wrap UVs", default=True,
        description="If True, each face's UV island is wrapped to be near (0,0) in UV space. Otherwise, UVs are projected literally from world-space coordinates, meaning the UVs can be very far from (0,0) if the face is far from the world origin")


############
### Menu ###
############

class NAIL_MT_main_menu(bpy.types.Menu):
    bl_idname = "NAIL_MT_main_menu"
    bl_label = "Nail"

    def draw(self, context):
        layout = self.layout

        layout.label(text="Nail - Texture Transforms", icon='TRANSFORM_ORIGINS')

        layout.separator()

        a = layout.operator(NAIL_OT_set_tex_transform.bl_idname,
            text=NAIL_OT_set_tex_transform.bl_label + " (Default Apply All)")

        # Pull default values from active face
        tt = TextureConfig.from_active_face(context)
        a.shift = tt.shift
        a.space_align = str(TCFLAG_OBJECT_SPACE if tt.object_space else 0)
        a.plane_align = str(TCFLAG_ALIGN_FACE if tt.align_face else 0)
        a.scale = tt.scale
        a.rotation = tt.rotation
        a.apply = {'SHIFT', 'SCALE', 'ROTATION', 'ALIGNMENT'}

        b = layout.operator(NAIL_OT_set_tex_transform.bl_idname,
            text=NAIL_OT_set_tex_transform.bl_label + " (Default Apply None)")
        b.shift = a.shift
        b.space_align = a.space_align
        b.plane_align = a.plane_align
        b.scale = a.scale
        b.rotation = a.rotation
        b.apply = set()

        layout.separator()
        layout.operator(NAIL_OT_apply_tex_transform.bl_idname)
        layout.prop(bpy.context.window_manager.nail_settings, 'auto_apply')
        s = layout.split()
        s.prop(bpy.context.window_manager.nail_settings, 'fast_updates')
        s.enabled = bpy.context.window_manager.nail_settings.auto_apply

        layout.separator()
        layout.operator(NAIL_OT_clear_tex_transform.bl_idname)

#        layout.separator()
#        layout.operator(NAIL_OT_unregister.bl_idname)


################
### Handlers ###
################

def enable_post_depsgraph_update_handler(enable):
    if enable:
        if on_post_depsgraph_update not in bpy.app.handlers.depsgraph_update_pre:
            bpy.app.handlers.depsgraph_update_pre.append(on_post_depsgraph_update)
    else:
        if on_post_depsgraph_update in bpy.app.handlers.depsgraph_update_pre:
            bpy.app.handlers.depsgraph_update_pre.remove(on_post_depsgraph_update)

    if enable:
        if on_post_depsgraph_update2 not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(on_post_depsgraph_update2)
    else:
        if on_post_depsgraph_update2 in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(on_post_depsgraph_update2)

#    if enable:
#        if not bpy.app.timers.is_registered(timer):
#            bpy.app.timers.register(timer, first_interval=1)
#    else:
#        if bpy.app.timers.is_registered(timer):
#            bpy.app.timers.unregister(timer)

#def timer():
#    print("timer start")
#    bmesh.update_edit_mesh(bpy.data.meshes['Cube.001'], loop_triangles=False)
#    print("timer end")    
#    return 1

UPDATE_INTERVAL = 1

post_count = 0
@persistent
def on_post_depsgraph_update2(scene):
    global post_count
    print("postupdate", post_count, bpy.context.active_operator)
    post_count += 1
    
# https://blender.stackexchange.com/a/283286/154191
count = 0
@persistent
def on_post_depsgraph_update(scene):
    global count
    print("preupdate start", count)
    
    depsgraph = bpy.context.view_layer.depsgraph
    print("preupdate end", count, bpy.context.active_operator, depsgraph.updates[0].is_updated_transform, depsgraph.updates[0].is_updated_geometry)
    count += 1
    return
    self = on_post_depsgraph_update

    # When fast is set, do the apply every depsgraph update
    fast = bpy.context.window_manager.nail_settings.fast_updates

    if fast:
        self.last_operator = None
    else:
        op = bpy.context.active_operator
        op_changed = op is not self.last_operator
        self.last_operator = op

    any_geom_updates = False

    for u in depsgraph.updates:
        if not u.is_updated_transform and not u.is_updated_geometry:
            continue
        if u.id is None or u.id.id_type != 'OBJECT' or u.id.type != 'MESH':
            continue
        if not NailMesh.is_nail_mesh(u.id.data):
            continue
        if not any_geom_updates:
            any_geom_updates = True
            self.last_obj_list.clear()
        if fast:
            do_auto_apply(u.id)
        else:
            self.last_obj_list.append(u.id)

    if not fast and any_geom_updates:
        if op_changed:
            # Operator change indicates the user probably just completed an action,
            # like finished a Move, mode-switch, etc. We can cancel any timers and
            # update the objects now. Unfortunately sometimes (seemingly randomly)
            # modal operations like Move don't send a depsgraph event at the end with
            # an updated Operator, therefore we won't see an op_changed and we can't
            # tell the modal operation has ended. In that case, the timer below will
            # apply the tex transform ~1 second later.
            if bpy.app.timers.is_registered(geom_update_timer):
                bpy.app.timers.unregister(geom_update_timer)
            for obj in self.last_obj_list:
                do_auto_apply(obj)
        else:
            # Operator was the same as last time. Start a timer to update every second.
            # Note that during a modal operation like Move, on_post_depsgraph_update
            # will be called constantly, so the instant the timer fires and self-
            # unregisters, this will register it again (if the modal operation is
            # still ongoing).
            if not bpy.app.timers.is_registered(geom_update_timer):
                bpy.app.timers.register(geom_update_timer, first_interval=UPDATE_INTERVAL)


on_post_depsgraph_update.last_operator = None
on_post_depsgraph_update.last_obj_list = []

def geom_update_timer():
#    print("update timer", on_post_depsgraph_update.last_obj_list)
#    if not bpy.context.window_manager.nail_settings.fast_updates:
    print("a")
    print (on_post_depsgraph_update.last_obj_list)
    print(bpy.context.objects_in_mode)
    for obj in bpy.context.objects_in_mode:#on_post_depsgraph_update.last_obj_list:
        do_auto_apply(obj)
    print("b")
    return None

def do_auto_apply(obj):
    with NailMesh(obj) as nm:
        print("doing auto apply")
        nm.apply_texture(auto_apply=True)


#################
### Operators ###
#################

def shared_poll(self, context):
    if context.mode != 'EDIT_MESH':
        self.poll_message_set("Must be run in Edit Mode")
        return False
    obj = context.active_object
    if obj is None or obj.type != 'MESH':
        self.poll_message_set("Must have an active (selected) mesh object")
        return False
    return True


in_update = False
def update_apply(self, key):
    global in_update
    if not in_update:
        try:
            in_update = True
            self.apply = self.apply.union({key})
        finally:
            in_update = False


class NAIL_OT_set_tex_transform(Operator):
    bl_idname = "aurycat.nail_set_tex_transform"
    bl_label = "Set Transform"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Sets the texture shift, scale, and/or rotation for all selected faces to the chosen value. The default values are that of the active face"

    apply_items = (
        ('SHIFT', "Shift", "Update shift"),
        ('SCALE', "Scale", "Update scale"),
        ('ROTATION', "Rotation", "Update rotation"),
        ('ALIGNMENT', "Alignment", "Update alignment"),
    )
    apply: bpy.props.EnumProperty(name="Apply", items=apply_items, options={'ENUM_FLAG'})

    shift: bpy.props.FloatVectorProperty(
        name="Shift",
        default=[0,0],
        subtype='XYZ',
        size=2,
        soft_min=-1,
        soft_max=1,
        step=1,
        update=(lambda s,c: update_apply(s, 'SHIFT')))

    scale: bpy.props.FloatVectorProperty(
        name="Scale",
        default=[0,0],
        subtype='XYZ',
        size=2,
        step=1,
        update=(lambda s,c: update_apply(s, 'SCALE')))

    rotation: bpy.props.FloatProperty(
        name="Rotation",
        default=0,
        subtype='ANGLE',
        soft_min=-math.pi*2,
        soft_max=math.pi*2,
        step=50,
        update=(lambda s,c: update_apply(s, 'ROTATION')))

    space_align_items = (
        (str(0),                   "World", "Determine UVs from world-space cube projection"),
        (str(TCFLAG_OBJECT_SPACE), "Object", "Determine UVs from object-space cube projection"),
    )
    space_align: bpy.props.EnumProperty(
        name="Space Alignment",
        items=space_align_items,
        update=(lambda s,c: update_apply(s, 'ALIGNMENT')))

    plane_align_items = (
        (str(0),                 "Axis", "Projection is aligned to axis planes"),
        (str(TCFLAG_ALIGN_FACE), "Face", "Projection is aligned to the face plane"),
    )
    plane_align: bpy.props.EnumProperty(
        name="Plane Alignment",
        items=plane_align_items,
        update=(lambda s,c: update_apply(s, 'ALIGNMENT')))

    @classmethod
    def poll(cls, context):
        return shared_poll(cls, context)

    def execute(self, context):
        if len(self.apply) > 0:
            tt = TextureConfig()
            tt.enabled   = True
#            tt.lock_axis = False
            if 'ALIGNMENT' in self.apply:
                tt.object_space = (int(self.space_align) == TCFLAG_OBJECT_SPACE)
                tt.align_face = (int(self.plane_align) == TCFLAG_ALIGN_FACE)
            if 'SHIFT' in self.apply:
                tt.shift = Vector(self.shift)
            if 'SCALE' in self.apply:
                tt.scale = Vector(self.scale)
            if 'ROTATION' in self.apply:
                tt.rotation = self.rotation

            for obj in context.objects_in_mode:
                if obj.type == 'MESH':
                    with NailMesh(obj) as nm:
                        nm.set_texture(tt)
                        nm.apply_texture()
                
        return {'FINISHED'}


class NAIL_OT_apply_tex_transform(Operator):
    bl_idname = "aurycat.nail_apply_tex_transform"
    bl_label = "Apply Current Transform"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Reapplies the selected faces' texture transforms. Useful to run after moving or modifying faces"

    @classmethod
    def poll(cls, context):
        return shared_poll(cls, context)

    def execute(self, context):
        for obj in context.objects_in_mode:
            if obj.type == 'MESH':
                with NailMesh(obj) as nm:
                    nm.apply_texture()
        return {'FINISHED'}


class NAIL_OT_clear_tex_transform(Operator):
    bl_idname = "aurycat.nail_clear_tex_transform"
    bl_label = "Clear Transform"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Clears texture transforms for the selected faces, so they are no longer affected by Nail"

    @classmethod
    def poll(cls, context):
        return shared_poll(cls, context)

    def execute(self, context):
        for obj in context.objects_in_mode:
            if obj.type == 'MESH':
                with NailMesh(obj) as nm:
                    nm.set_texture(TextureConfig.cleared())
        return {'FINISHED'}


#############
### Utils ###
#############

ORIENTATION_PX = 0  # +X
ORIENTATION_PY = 1  # +Y
ORIENTATION_PZ = 2  # +Z
ORIENTATION_NX = 3  # -X
ORIENTATION_NY = 4  # -Y
ORIENTATION_NZ = 5  # -Z

NORMAL_VECTORS = [
    Vector((1,0,0)),  # +X
    Vector((0,1,0)),  # +Y
    Vector((0,0,1)),  # +Z
    Vector((-1,0,0)), # -X
    Vector((0,-1,0)), # -Y
    Vector((0,0,-1)), # -Z
]

UP_VECTORS = [
    Vector((0,0,1)), # +X
    Vector((0,0,1)), # +Y
    Vector((0,1,0)), # +Z
    Vector((0,0,1)), # -X
    Vector((0,0,1)), # -Y
    Vector((0,1,0)), # -Z
]

RIGHT_VECTORS = [
    Vector((0,-1,0)), # +X
    Vector((-1,0,0)), # +Y
    Vector((-1,0,0)), # +Z
    Vector((0,-1,0)), # -X
    Vector((-1,0,0)), # -Y
    Vector((-1,0,0)), # -Z
]

def face_orientation(v):
    ax, ay, az = abs(v.x), abs(v.y), abs(v.z)
    if ax >= ay and ax >= az:
        return ORIENTATION_PX if v.x >= 0 else ORIENTATION_NX
    elif ay >= ax and ay >= az:
        return ORIENTATION_PY if v.y >= 0 else ORIENTATION_NY
    else:
        return ORIENTATION_PZ if v.z >= 0 else ORIENTATION_NZ

# For a normalized 3D vector, the largest of the values is the axis to which the
# vector is most closely pointing, the dominant axis. The other two axes, the
# nondominant axes, represent the XY, XZ, or YZ plane for which the dominant axis
# is that plane's normal.
# This returns a tuple of the dominant axis index, followed by the two non dominant
# axis indices.
def dominant_axis(v):
    ax, ay, az = abs(v[0]), abs(v[1]), abs(v[2])
    if ax >= ay and ax >= az:
        return (0, 1, 2)
    elif ay >= ax and ay >= az:
        return (1, 0, 2)
    else:
        return (2, 0, 1)

def dominant_axis_vec(dax):
    return Vector((1 if dax == 0 else 0,
                   1 if dax == 1 else 0,
                   1 if dax == 2 else 0))

# Project 3D Vector 'point' onto the plane made of normalized 3D Vector 'normal'
# and 3D Vector 'origin'. Returns the closest point on the plane (the projection)
# as a 3D Vector in the same coordinate system.
def project_point_onto_plane(point, normal, origin):
    return point - (normal.dot(point - origin))*normal

def vec_is_zero(v):
    return math.isclose(v.x, 0) and math.isclose(v.y, 0) and math.isclose(v.z, 0)

# https://developer.download.nvidia.com/cg/frac.html
def frac(f):
    return f - math.floor(f)


############
### Main ###
############

class TextureConfig:
    # The default None value means that the value is "unset", which is important
    # when taking input values from a user. Unset values are left unchanged on the
    # face being modified.

    # Flags
    enabled = None
    lock_axis = None
    object_space = None
    align_face = None

    # Transform
    shift = None
    scale = None
    rotation = None

    @classmethod
    def cleared(cls):
        tt = TextureConfig()
        tt.enabled = False
        tt.lock_axis = False
        tt.object_space = False
        tt.align_face = False
        tt.shift = Vector((0,0))
        tt.scale = Vector((1,1))
        tt.rotation = 0
        return tt

    @classmethod
    def from_active_face(cls, context):
        active = context.active_object
        if active is not None and active.type == 'MESH':
            me = active.data
            if NailMesh.is_nail_mesh(me):
                bm = bmesh.from_edit_mesh(me)
                if bm.faces.active is not None and bm.faces.active.select:
                    tt = TextureConfig.from_face(bm, bm.faces.active)
                else:
                    tt = TextureConfig.cleared()
                bm.free()
            else:
                tt = TextureConfig.cleared()
        else:
            tt = TextureConfig.cleared()

        return tt

    @classmethod
    def from_face(cls, bm, face):
        shift_flags_layer = bm.faces.layers.float_vector[ATTR_SHIFT_FLAGS]
        scale_rot_layer = bm.faces.layers.float_vector[ATTR_SCALE_ROT]
        shift_flags_attr = face[shift_flags_layer]
        scale_rot_attr = face[scale_rot_layer]

        tt = TextureConfig()

        flags = int(shift_flags_attr[2])
        tt.enabled = (flags & TCFLAG_ENABLED) == TCFLAG_ENABLED
        tt.lock_axis = (flags & TCFLAG_LOCK_AXIS) == TCFLAG_LOCK_AXIS
        tt.object_space = (flags & TCFLAG_OBJECT_SPACE) == TCFLAG_OBJECT_SPACE
        tt.align_face = (flags & TCFLAG_ALIGN_FACE) == TCFLAG_ALIGN_FACE

        tt.shift = shift_flags_attr.xy

        scale = scale_rot_attr.xy
        if math.isclose(scale.x, 0): scale.x = 1
        if math.isclose(scale.y, 0): scale.y = 1
        tt.scale = scale

        tt.rotation = scale_rot_attr.z

        return tt


class NailMesh:

    def __init__(self, obj):
        if obj.type != 'MESH':
            raise RuntimeError("Invalid object type used to initialize NailMesh: " + str(obj))
        self.obj = obj

    def __enter__(self):
        self.matrix_world = self.obj.matrix_world
        self.rot_world = self.matrix_world.to_quaternion()
        self.wrap_uvs = bpy.context.window_manager.nail_settings.wrap_uvs
        self.me = self.obj.data
        if self.me.is_editmode:
            print("from editmesh")
            self.bm = bmesh.from_edit_mesh(self.me)
        else:
            self.bm = bmesh.new()
            self.bm.from_mesh(self.me)
        self.init_attrs()
        self.uv_layer = self.bm.loops.layers.uv.active
        self.shift_flags_layer = self.bm.faces.layers.float_vector[ATTR_SHIFT_FLAGS]
        self.scale_rot_layer = self.bm.faces.layers.float_vector[ATTR_SCALE_ROT]
        self.uaxis_layer = self.bm.faces.layers.float_vector[ATTR_UAXIS]
        self.vaxis_layer = self.bm.faces.layers.float_vector[ATTR_VAXIS]
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        print("__exit__")
        if exc_type is None and self.bm is not None and self.me is not None:
            if self.me.is_editmode:
                print("update edit mesh")
                bmesh.update_edit_mesh(self.me, loop_triangles=False, destructive=False)
            else:
                self.bm.to_mesh(self.me)
        if self.bm is not None:
            self.bm.free()
        self.bm = None
        self.me = None

    def init_attrs(self):
        if len(self.bm.loops.layers.uv) == 0:
            self.bm.loops.layers.uv.new("UVMap")
        elif self.bm.loops.layers.uv.active is None:
            # Not sure if this is possible, but just to be safe
            raise RuntimeError(f"Mesh '{self.me.name}' has at least one UV Map, but none are marked 'active'. Please make sure a UVMap is selected on this mesh.")

        for attr in FACE_FLOAT_VECTOR_ATTRS:
            if attr not in self.bm.faces.layers.float_vector:
                if attr in self.me.attributes:
                    # Not in faces.layers.float_vector, but it is in me.attributes, which
                    # implies the attribute already exists with some other domain/type
                    a = self.me.attributes[attr]
                    raise RuntimeError(f"Mesh '{self.me.name}' has an existing '{attr}' attribute that is the wrong domain or type. Expected FACE/FLOAT_VECTOR, got {a.domain}/{a.data_type}. Please remove or rename the existing attribute.")
                self.bm.faces.layers.float_vector.new(attr)

    @classmethod
    def is_nail_mesh(cls, me):
        if len(me.uv_layers) == 0:
            return False
        for attr in FACE_FLOAT_VECTOR_ATTRS:
            if (attr not in me.attributes or
                me.attributes[attr].domain != 'FACE' or
                me.attributes[attr].data_type != 'FLOAT_VECTOR'):
                return False
        return True

    def set_texture(self, tc, editmode_only_selected=True):
        only_selected = self.me.is_editmode and editmode_only_selected

        for face in self.bm.faces:
            if only_selected and not face.select:
                continue
            self.set_texture_one_face(tc, face)

    def set_texture_one_face(self, tc, face):
        shift_flags_attr = face[self.shift_flags_layer]
        scale_rot_attr = face[self.scale_rot_layer]

        flags = int(shift_flags_attr.z)

        if tc.enabled is not None:
            if tc.enabled: flags |= TCFLAG_ENABLED
            else:          flags &= ~TCFLAG_ENABLED

        if tc.lock_axis is not None:
            if tc.lock_axis: flags |= TCFLAG_LOCK_AXIS
            else:            flags &= ~TCFLAG_LOCK_AXIS

        if tc.object_space is not None:
            if tc.lock_axis: flags |= TCFLAG_OBJECT_SPACE
            else:            flags &= ~TCFLAG_OBJECT_SPACE

        if tc.align_face is not None:
            if tc.align_face: flags |= TCFLAG_ALIGN_FACE
            else:             flags &= ~TCFLAG_ALIGN_FACE

        shift_flags_attr.z = float(flags)

        if tc.shift is not None:
            shift_flags_attr.xy = tc.shift
        if tc.scale is not None:
            scale_rot_attr.xy = tc.scale
        if tc.rotation is not None:
            scale_rot_attr.z = tc.rotation

    def apply_texture(self, auto_apply=False, editmode_only_selected=True):
        apply_mode = 0 # All faces
        if self.me.is_editmode and editmode_only_selected:
            if auto_apply:
                # In auto-apply mode, apply to selected faces as well as any faces that
                # have a selected vertex, since they could be affected by the changes
                apply_mode = 1
            else:
                # Only selected faces
                apply_mode = 2

        for face in self.bm.faces:
            if len(face.loops) == 0: # Not sure if this is possible, but safety check anyway
                continue

            if apply_mode == 2:
                if not face.select:
                    continue
            elif apply_mode == 1:
                if not face.select:
                    any_selected_verts = False
                    for v in face.verts:
                        if v.select:
                            any_selected_verts = True
                            break
                    if not any_selected_verts:
                        continue

            self.apply_texture_one_face(face)

    def apply_texture_one_face(self, face):
        shift_flags_attr = face[self.shift_flags_layer]
        scale_rot_attr = face[self.scale_rot_layer]

        flags = int(shift_flags_attr.z)
        if (flags & TCFLAG_ENABLED) != TCFLAG_ENABLED:
            # Nail is disabled on this face
            return

        shift = shift_flags_attr.xy
        scale = scale_rot_attr.xy
        if math.isclose(scale.x, 0): scale.x = 1
        if math.isclose(scale.y, 0): scale.y = 1
        rotation_mat = Matrix.Rotation(scale_rot_attr.z, 2)

        world_space = (flags & TCFLAG_OBJECT_SPACE) != TCFLAG_OBJECT_SPACE

        # If axis lock is set for this face, reuse existing UV Axis (which
        # implies not checking the align_face flag). But still apply the
        # shift, scale, and rotation.
        axis_lock = ((flags & TCFLAG_LOCK_AXIS) == TCFLAG_LOCK_AXIS and
                     # If axes aren't set yet, ignore axis lock
                     not (vec_is_zero(uaxis_attr) or vec_is_zero(vaxis_atr)))

        if axis_lock:
            uaxis = face[self.uaxis_layer]
            vaxis = face[self.vaxis_layer]
        else:
            # TODO: Investigate what happens if smooth shading is on!
            # I think normals need to be unsmoothed for this to work right
            normal = face.normal
            if world_space:
                normal = self.rot_world @ normal

            orientation = face_orientation(normal)

            vaxis = UP_VECTORS[orientation]

            align_face = (flags & TCFLAG_ALIGN_FACE) == TCFLAG_ALIGN_FACE
            if align_face:
                uaxis = normal.cross(vaxis)
                uaxis.normalize()
                vaxis = uaxis.cross(normal)
                vaxis.normalize()
                uaxis.negate()
            else:
                uaxis = RIGHT_VECTORS[orientation]

            face[self.uaxis_layer] = uaxis
            face[self.vaxis_layer] = vaxis

        uv_layer = self.uv_layer
        for loop in face.loops:
            vert_coord = loop.vert.co
            if world_space:
                vert_coord = self.matrix_world @ vert_coord

            uv_coord = Vector((vert_coord.dot(uaxis), vert_coord.dot(vaxis)))
            uv_coord.rotate(rotation_mat)
            uv_coord *= scale
            uv_coord += shift
            loop[uv_layer].uv = uv_coord

        if self.wrap_uvs:
            coord0 = face.loops[0][uv_layer].uv
            wrapped_coord0 = Vector((frac(coord0.x), frac(coord0.y)))
            diff_coord0 = wrapped_coord0 - coord0
            for loop in face.loops:
                loop[uv_layer].uv += diff_coord0



#            dax, ndax0, ndax1 = dominant_axis(normal)

#            if align_face:
#                dax_vec = dominant_axis_vec(dax)
#                if dax != 2: # face normal is most closely pointing towards X or Y axis
#                    normal_xy_proj = Vector((normal[0], normal[1], 0))
#                    # Find rotation around z-axis first
#                    z_rot = normal_xy_proj.rotation_difference(dax_vec)

        # If the dominant axis of the normal is 1, then this face is already axis aligned.
        # Face alignment and axis alignment will be identical in this case, so do axis
        # alignment because it's simpler.
#        if math.isclose(normal[dax], 1):
#            align_face = False


                
                
                
#            print("dax_vec", dax_vec)
#            face_rot = normal.rotation_difference(dax_vec)
#            print("face_rot", face_rot.to_axis_angle())

#        for loop in face.loops:
#            vert_coord = loop.vert.co
#            if align_world:
#                vert_coord = matrix_world @ vert_coord

#            if align_face:
#                vert_coord = face_rot @ vert_coord

#            uv_coord = Vector((vert_coord[ndax0], vert_coord[ndax1]))
#            uv_coord.rotate(rotation_mat)
#            uv_coord *= scale
#            uv_coord += shift
#            loop[uv_layer].uv = uv_coord




#def set_tex_transform(context, tt):


#def apply_tex_transform(context):
#    for obj in context.objects_in_mode:
#        if obj.type == 'MESH':
#            with NailMesh(obj) as nm:
#                nm.apply_texture()
#            apply_tex_transform_one_object(obj, context, auto_apply=False)


#def apply_tex_transform_one_object(obj, context, auto_apply=False): # Must be MESH type
#    me = obj.data
#    if me.is_editmode:
#        bm = bmesh.from_edit_mesh(me)
#        make_nail_attrs(me, bm)
#        apply_tex_transform_one_mesh(obj, bm, context, auto_apply=auto_apply)
#        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
#        bm.free()
#    elif context.mode == 'OBJECT':
#        bm = bmesh.new()
#        bm.from_mesh(me)
#        make_nail_attrs(me, bm)
#        apply_tex_transform_one_mesh(obj, bm, context, auto_apply=auto_apply)
#        bm.to_mesh(me)
#        bm.free()


#def set_tex_transform_one_mesh(bm, tt):
#    shiftalign_layer = bm.faces.layers.float_vector[ATTR_SHIFT_FLAGS]
#    scalerot_layer = bm.faces.layers.float_vector[ATTR_SCALE_ROT]

#    shift, alignment, scale, rotation = tt.shift, tt.alignment, tt.scale, tt.rotation

#    # Unless alignment is specified, assume all faces are to set enabled for alignment
#    if alignment is None:
#        for face in bm.faces:
#            if face.select:
#                cur = face[shiftalign_layer]
#                a = float(int(cur[2]) | ALIGN_ENABLE_BIT)
#                v = Vector((cur[0], cur[1], a))
#                face[shiftalign_layer] = v

#    # Shift & alignment
#    if shift is not None and alignment is not None:
#        for face in bm.faces:
#            if face.select:
#                cur = face[shiftalign_layer]
#                v = Vector((shift[0], shift[1], alignment))
#                face[shiftalign_layer] = v
#    elif shift is not None:
#        for face in bm.faces:
#            if face.select:
#                cur = face[shiftalign_layer]
#                v = Vector((shift[0], shift[1], cur[2]))
#                face[shiftalign_layer] = v
#    elif alignment is not None:
#        for face in bm.faces:
#            if face.select:
#                cur = face[shiftalign_layer]
#                v = Vector((cur[0], cur[1], alignment))
#                face[shiftalign_layer] = v

#    # Scale & rotation
#    if scale is not None:
#        if math.isclose(scale[0], 0): scale[0] = 1
#        if math.isclose(scale[1], 0): scale[1] = 1

#    if scale is not None and rotation is not None:
#        for face in bm.faces:
#            if face.select:
#                v = Vector((scale[0], scale[1], rotation))
#                face[scalerot_layer] = v
#    elif scale is not None:
#        for face in bm.faces:
#            if face.select:
#                cur = face[scalerot_layer]
#                v = Vector((scale[0], scale[1], cur[2]))
#                face[scalerot_layer] = v
#    elif rotation is not None:
#        for face in bm.faces:
#            if face.select:
#                cur = face[scalerot_layer]
#                v = Vector((cur[0], cur[1], rotation))
#                face[scalerot_layer] = v










if __name__ == "__main__":
    main()

