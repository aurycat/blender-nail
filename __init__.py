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
    "name": "Align UV To Grid",
    "description": "'Align to Grid' option for UVs, which is like working with brushes in a BSP level editor such as Hammer",
    "author": "aurycat",
    "version": (1, 0),
    "blender": (4, 1, 1), # Minimum tested version. Might work with older.
    "location": "View3D > UV > Align To Grid",
    "warning": "",
    "doc_url": "",
    "tracker_url": "",
    "support": "COMMUNITY",
    "category": "UV",
}

import bpy
import re
import math
from bpy.types import Operator
from bpy_extras import view3d_utils
from mathutils import Vector, Matrix
import bmesh
import time
from bpy.app.handlers import persistent

# bmesh apparently doesn't have an API for per-face '2D Vector'
# attributes, so these need to use 'Vector' (float_vector) instead.
# Vector has 3 dimensions, so 'rotation' and 'alignment' can be
# shoved into those extra Z coordinates.
# Alignment is one of the ALIGN_* constants below
# Rotation is stored in radians
ATTR_SHIFT_ALIGN = "Nail_ShiftAlign" # per-face Vector(X Shift, Y Shift, Alignment)
ATTR_SCALE_ROT   = "Nail_ScaleRot"   # per-face Vector(X Scale, Y Scale, Rotation)

ALIGN_NONE = -1       # This plugin will never change the UVs on this face
ALIGN_WORLD = 0       # World-space UV alignment. This is like Hammer's default behavior for brushes.
                      # (https://developer.valvesoftware.com/wiki/Texture_alignment#World_Alignment)
                      # It is effectively a Cube Projection where the center of the "cube" is the
                      # world origin, and scale and rotation are identity.
ALIGN_OBJECT = 1      # Object-space UV alignment. Again like Cube Projection, except the position,
                      # rotation, and scale of the "cube" are that of the object transform.
ALIGN_WORLD_FACE = 2  # This is like ALIGN_WORLD, except the "cube" is rotated to be aligned
                      # to the normal of the face.
ALIGN_OBJECT_FACE = 3 # This is like ALIGN_OBJECT, except the "cube" is rotated to be aligned
                      # to the normal of the face.
ALIGN_SPACE_BIT = 1
ALIGN_PLANE_BIT = 2


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
    auto_apply: bpy.props.BoolProperty(name="Auto Apply", default=False, update=auto_apply_updated)


############
### Menu ###
############

class NAIL_MT_main_menu(bpy.types.Menu):
    bl_idname = "NAIL_MT_main_menu"
    bl_label = "Nail"

    def draw(self, context):
        layout = self.layout

        layout.label(text="Texture Transform", icon='TRANSFORM_ORIGINS')

        a = layout.operator(NAIL_OT_set_tex_transform.bl_idname,
            text=NAIL_OT_set_tex_transform.bl_label + " (Default Apply All)")

        # Pull default values from active face
        tt = TexTransform.from_active_face(context)
        a.shift = tt.shift
        a.space_align = str(int(tt.alignment) & ALIGN_SPACE_BIT)
        a.plane_align = str(int(tt.alignment) & ALIGN_PLANE_BIT)
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
        layout.prop(bpy.context.window_manager.nail_settings, 'auto_apply', text="Auto-Apply Transforms") 

        layout.separator()
        layout.operator(NAIL_OT_clear_tex_transform.bl_idname)

        layout.separator()
        layout.operator(NAIL_OT_unregister.bl_idname)


################
### Handlers ###
################

def enable_post_depsgraph_update_handler(enable):
    if enable:
        if on_post_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(on_post_depsgraph_update)
    else:
        if on_post_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(on_post_depsgraph_update)

UPDATE_INTERVAL = 1

# https://blender.stackexchange.com/a/283286/154191
@persistent
def on_post_depsgraph_update(scene, depsgraph):
    self = on_post_depsgraph_update

    op = bpy.context.active_operator
    op_changed = op is not self.last_operator
    self.last_operator = op

    print(op)

    now = time.monotonic()
    if not (op_changed or self.last_geom_update_time is None or
            (now - self.last_geom_update_time) > UPDATE_INTERVAL):
        if not bpy.app.timers.is_registered(geom_update_timer):
            bpy.app.timers.register(geom_update_timer, first_interval=UPDATE_INTERVAL)
        return

    for u in depsgraph.updates:
        if not u.is_updated_transform and not u.is_updated_geometry:
            continue
        if u.id is None or u.id.id_type != 'OBJECT' or u.id.type != 'MESH':
            continue
        if not mesh_has_nail_attrs(u.id.data):
            continue

        if bpy.app.timers.is_registered(geom_update_timer):
            bpy.app.timers.unregister(geom_update_timer)
        
        self.last_geom_update_time = now
        apply_tex_transform_one_object(u.id, bpy.context)


on_post_depsgraph_update.last_operator = None
on_post_depsgraph_update.last_geom_update_time = None

def geom_update_timer():
    for obj in bpy.context.selected_objects:
        apply_tex_transform_one_object(obj, bpy.context)
    return None
    

#        if op is None:
#            return
##        if 'properties' in op and 'value' in op.properties:
##            val = op.properties.value
##            if val is last_operator_value:
##                retur
#    last_operator = op

#    geom_updates = [
#        u.id for u in depsgraph.updates if
#            (u.is_updated_transform or u.is_updated_geometry)
#            and u.id is not None and u.id.id_type == 'OBJECT' and u.id.type == 'MESH']

#    if len(geom_updates) > 0:
#        print(geom_updates)
#        if op_changed:
#            print(time.monotonic(), "update")
#            if bpy.app.timers.is_registered(geom_update_timer):
#                bpy.app.timers.unregister(geom_update_timer)
#        else:
#            if not bpy.app.timers.is_registered(geom_update_timer):
#                print("register")
#                bpy.app.timers.register(geom_update_timer, first_interval=1)
#        on_geometry_change(geom_updates)

##    if op is not None and op.bl_idname == 'TRANSFORM_OT_translate':
##        print(op.properties.value)
#    if op is not None and 'value' in op.properties:
#        print(op.properties.value)
#    print(geom_updates)
#    for u in depsgraph.updates:
#        if not u.is_updated_transform and not u.is_updated_geometry:
#            continue
#        if u.id is None or u.id.id_type != 'OBJECT' or u.id.type != 'MESH':
#            continue
#        print("mesh changed")

#def geom_update_timer():
#    print(time.monotonic(), "timer update")
#    return None

#def on_geometry_change(geom_updates):



#def depsgraph_update_post_handler(scene, depsgraph):
#    print(bpy.context.active_operator, bpy.context.active_operator.id_data)
#    return
#    print("----")
#    for u in depsgraph.updates:
#        if u.id == None or u.id.id_type != 'OBJECT':
#            continue
#        if u.id.type != 'MESH':
#            continue
#        geom = False
#        trans = False
#        if (u.is_updated_geometry):
#            geom = True
#        if u.is_updated_transform:
#            trans = True
##        print("  ", u.id, u.is_updated_geometry, u.is_updated_shading, u.is_updated_transform)

#        if geom or trans:
#            print(u.id, "   updated geom:", geom, "    updated trans:", trans)


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
        (str(0),               "World", "Determine UVs from world-space cube projection"),
        (str(ALIGN_SPACE_BIT), "Object", "Determine UVs from object-space cube projection"),
    )
    space_align: bpy.props.EnumProperty(
        name="Space Alignment",
        items=space_align_items,
        update=(lambda s,c: update_apply(s, 'ALIGNMENT')))

    plane_align_items = (
        (str(0),               "Axis", "Projection is aligned to axis planes"),
        (str(ALIGN_PLANE_BIT), "Face", "Projection is aligned to the face plane"),
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
            alignment = int(self.space_align) | int(self.plane_align)

            tt = TexTransform()
            tt.shift     = (self.shift     if 'SHIFT'     in self.apply else None)
            tt.alignment = (     alignment if 'ALIGNMENT' in self.apply else None)
            tt.scale     = (self.scale     if 'SCALE'     in self.apply else None)
            tt.rotation  = (self.rotation  if 'ROTATION'  in self.apply else None)

            set_tex_transform(context, tt)
                
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
        apply_tex_transform(context)
        return {'FINISHED'}


class NAIL_OT_clear_tex_transform(Operator):
    bl_idname = "aurycat.nail_clear_tex_transform"
    bl_label = "Clear Transform"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Clears shift/scale/rotation/alignment to default values on all selected faces"

    @classmethod
    def poll(cls, context):
        return shared_poll(cls, context)

    def execute(self, context):
        set_tex_transform(context, TexTransform.cleared())
        return {'FINISHED'}


############
### Main ###
############

class TexTransform:
    shift = None
    alignment = 0
    scale = None
    rotation = None

    @classmethod
    def cleared(cls):
        tt = TexTransform()
        tt.shift = [0,0]
        tt.alignment = 0
        tt.scale = [1,1]
        tt.rotation = 0
        return tt

    @classmethod
    def from_active_face(cls, context):
        active = context.active_object
        if active is not None and active.type == 'MESH':
            me = active.data
            if mesh_has_nail_attrs(me):
                bm = bmesh.from_edit_mesh(me)
                if bm.faces.active is not None and bm.faces.active.select:
                    tt = TexTransform.from_face(bm, bm.faces.active)
                    if math.isclose(tt.scale[0], 0): tt.scale[0] = 1
                    if math.isclose(tt.scale[1], 0): tt.scale[1] = 1
                else:
                    tt = TexTransform.cleared()
                bm.free()
            else:
                tt = TexTransform.cleared()
        else:
            tt = TexTransform.cleared()

        return tt

    @classmethod
    def from_face(cls, bm, face):
        tt = TexTransform()

        try:
            shift_align_layer = bm.faces.layers.float_vector[ATTR_SHIFT_ALIGN]
            scale_rot_layer = bm.faces.layers.float_vector[ATTR_SCALE_ROT]

            shift_align_attr = face[shift_align_layer]
            scale_rot_attr = face[scale_rot_layer]
        except KeyError:
            return result

        tt.shift = [shift_align_attr[0], shift_align_attr[1]]
        tt.alignment = shift_align_attr[2]

        scale = [scale_rot_attr[0], scale_rot_attr[1]]
        if math.isclose(scale[0], 0): scale[0] = 1
        if math.isclose(scale[1], 0): scale[1] = 1
        tt.scale = scale
        tt.rotation = scale_rot_attr[2]

        return tt


def set_tex_transform(context, tt):
    for obj in context.objects_in_mode:
        if obj.type == 'MESH':
            me = obj.data
            bm = bmesh.from_edit_mesh(me)
            make_nail_attrs(me, bm)
            set_tex_transform_one_mesh(bm, tt)
            apply_tex_transform_one_object(obj, bm)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            bm.free()


def apply_tex_transform(context):
    for obj in context.objects_in_mode:
        if obj.type == 'MESH':
            apply_tex_transform_one_object(obj, context)


def apply_tex_transform_one_object(obj, context): # Must be MESH type
    me = obj.data
    if context.mode == 'EDIT_MESH':
        bm = bmesh.from_edit_mesh(me)
        make_nail_attrs(me, bm)
        apply_tex_transform_one_mesh(obj, bm, use_selection=True)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        bm.free()
    elif context.mode == 'OBJECT':
        bm = bmesh.new()
        bm.from_mesh(me)
        make_nail_attrs(me, bm)
        apply_tex_transform_one_mesh(obj, bm,  use_selection=False)
        bm.to_mesh(me)
        bm.free()


def set_tex_transform_one_mesh(bm, tt):
    shiftalign_layer = bm.faces.layers.float_vector[ATTR_SHIFT_ALIGN]
    scalerot_layer = bm.faces.layers.float_vector[ATTR_SCALE_ROT]

    shift, alignment, scale, rotation = tt.shift, tt.alignment, tt.scale, tt.rotation

    # Shift & alignment
    if shift is not None and alignment is not None:
        for face in bm.faces:
            if face.select:
                cur = face[shiftalign_layer]
                v = Vector((shift[0], shift[1], alignment))
                face[shiftalign_layer] = v
    elif shift is not None:
        for face in bm.faces:
            if face.select:
                cur = face[shiftalign_layer]
                v = Vector((shift[0], shift[1], cur[2]))
                face[shiftalign_layer] = v
    elif alignment is not None:
        for face in bm.faces:
            if face.select:
                cur = face[shiftalign_layer]
                v = Vector((cur[0], cur[1], alignment))
                face[shiftalign_layer] = v

    # Scale & rotation
    if scale is not None:
        if math.isclose(scale[0], 0): scale[0] = 1
        if math.isclose(scale[1], 0): scale[1] = 1

    if scale is not None and rotation is not None:
        for face in bm.faces:
            if face.select:
                v = Vector((scale[0], scale[1], rotation))
                face[scalerot_layer] = v
    elif scale is not None:
        for face in bm.faces:
            if face.select:
                cur = face[scalerot_layer]
                v = Vector((scale[0], scale[1], cur[2]))
                face[scalerot_layer] = v
    elif rotation is not None:
        for face in bm.faces:
            if face.select:
                cur = face[scalerot_layer]
                v = Vector((cur[0], cur[1], rotation))
                face[scalerot_layer] = v


def apply_tex_transform_one_mesh(obj, bm, use_selection):
    matrix_world = obj.matrix_world
    rot_world = matrix_world.to_quaternion()

    uv_layer = bm.loops.layers.uv.active
    shiftalign_layer = bm.faces.layers.float_vector[ATTR_SHIFT_ALIGN]
    scalerot_layer = bm.faces.layers.float_vector[ATTR_SCALE_ROT]

    for face in bm.faces:
        if use_selection and not face.select:
            continue
        if len(face.loops) == 0:
            continue

        shift_align_attr = face[shiftalign_layer]
        scale_rot_attr = face[scalerot_layer]

        alignment = shift_align_attr[2]
        if alignment == ALIGN_NONE:
            continue
        align_world = (alignment == ALIGN_WORLD or alignment == ALIGN_WORLD_FACE)
        align_face = (alignment == ALIGN_WORLD_FACE or alignment == ALIGN_OBJECT_FACE)

        shift = Vector((shift_align_attr[0], shift_align_attr[1]))
        scale = Vector((scale_rot_attr[0], scale_rot_attr[1]))
        if math.isclose(scale[0], 0): scale[0] = 1
        if math.isclose(scale[1], 0): scale[1] = 1
        rotation = scale_rot_attr[2]
        rotation_mat = Matrix.Rotation(rotation, 2)

        normal = face.normal
        if align_world:
            normal = rot_world @ normal

        dax, ndax0, ndax1 = dominant_axis(normal)

        # If the dominant axis of the normal is 1, then this face is already axis aligned.
        # Face alignment and axis alignment will be identical in this case, so do axis
        # alignment because it's simpler.
        if math.isclose(normal[dax], 1):
            align_face = False

        if align_face:
            dax_vec = dominant_axis_vec(dax)
            face_rot = normal.rotation_difference(dax_vec)

        for loop in face.loops:
            vert_coord = loop.vert.co
            if align_world:
                vert_coord = matrix_world @ vert_coord

            if align_face:
                vert_coord = face_rot @ vert_coord

            uv_coord = Vector((vert_coord[ndax0], vert_coord[ndax1]))
            uv_coord.rotate(rotation_mat)
            uv_coord *= scale
            uv_coord += shift
            loop[uv_layer].uv = uv_coord


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


def make_nail_attrs(me, bm):
    if ATTR_SHIFT_ALIGN not in bm.faces.layers.float_vector:
        if ATTR_SHIFT_ALIGN in me.attributes:
            # Not in faces.layers.float_vector, but it is in me.attributes, which
            # implies the attribute already exists with some other domain/type
            a = me.attributes[ATTR_SHIFT_ALIGN]
            raise RuntimeError(f"Mesh '{m.name}' has an existing '{ATTR_SHIFT_ALIGN}' attribute that is the wrong domain or type. Expected FACE/FLOAT_VECTOR, got {a.domain}/{a.data_type}. Please remove or rename the existing attribute.")
        bm.faces.layers.float_vector.new(ATTR_SHIFT_ALIGN)

    if ATTR_SCALE_ROT not in bm.faces.layers.float_vector:
        if ATTR_SCALE_ROT in me.attributes:
            a = me.attributes[ATTR_SCALE_ROT]
            raise RuntimeError(f"Mesh '{m.name}' has an existing '{ATTR_SCALE_ROT}' attribute that is the wrong domain or type. Expected FACE/FLOAT_VECTOR, got {a.domain}/{a.data_type}. Please remove or rename the existing attribute.")
        bm.faces.layers.float_vector.new(ATTR_SCALE_ROT)


def mesh_has_nail_attrs(m):
    return (ATTR_SHIFT_ALIGN in m.attributes and
            ATTR_SCALE_ROT   in m.attributes and
            m.attributes[ATTR_SHIFT_ALIGN].domain    == 'FACE' and
            m.attributes[ATTR_SCALE_ROT].domain      == 'FACE' and
            m.attributes[ATTR_SHIFT_ALIGN].data_type == 'FLOAT_VECTOR' and
            m.attributes[ATTR_SCALE_ROT].data_type   == 'FLOAT_VECTOR')
           

if __name__ == "__main__":
    main()

