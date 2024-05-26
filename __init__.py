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
    bpy.types.VIEW3D_PT_view3d_lock.append(draw_lock_rotation)
    bpy.types.VIEW3D_MT_editor_menus.append(nail_draw_main_menu)
    bpy.app.handlers.depsgraph_update_post.append(depsgraph_update_post_handler)

def unregister():
    bpy.app.handlers.depsgraph_update_post.remove(depsgraph_update_post_handler)
    bpy.types.VIEW3D_PT_view3d_lock.remove(draw_lock_rotation)
    bpy.types.VIEW3D_MT_editor_menus.remove(nail_draw_main_menu)
    bpy.utils.unregister_class(NAIL_OT_unregister)
    bpy.utils.unregister_class(NAIL_OT_set_tex_transform)
    bpy.utils.unregister_class(NAIL_MT_main_menu)
    bpy.utils.unregister_class(NAIL_OT_clear_tex_transform)
    bpy.utils.unregister_class(NAIL_OT_apply_tex_transform)

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


############
### Menu ###
############

class NAIL_MT_main_menu(bpy.types.Menu):
    bl_idname = "NAIL_MT_main_menu"
    bl_label = "Nail"

    def draw(self, context):
        layout = self.layout
        active = context.active_object

        layout.label(text="Texture Transform", icon='TRANSFORM_ORIGINS')

        a = layout.operator(NAIL_OT_set_tex_transform.bl_idname,
            text=NAIL_OT_set_tex_transform.bl_label + " (Default Apply All)")
        a.shift = [0,0]
        a.scale = [1,1]
        a.rotation = 0
        # Pull default values from active face
        if active != None and active.type == 'MESH':
            bm = bmesh.from_edit_mesh(active.data)
            if bm.faces.active != None and bm.faces.active.select:
                get_tex_transform_from_face(bm, bm.faces.active)
                try:
                    shift_align_layer = bm.faces.layers.float_vector[ATTR_SHIFT_ALIGN]
                    scale_rot_layer = bm.faces.layers.float_vector[ATTR_SCALE_ROT]

                    shift_align_attr = bm.faces.active[shift_align_layer]
                    scale_rot_attr = bm.faces.active[scale_rot_layer]

                    a.shift = [shift_align_attr[0], shift_align_attr[1]]
                    a.space_align = str(int(shift_align_attr[2]) & ALIGN_SPACE_BIT)
                    a.plane_align = str(int(shift_align_attr[2]) & ALIGN_PLANE_BIT)

                    scale = [scale_rot_attr[0], scale_rot_attr[1]]
                    if math.isclose(scale[0], 0): scale[0] = 1
                    if math.isclose(scale[1], 0): scale[1] = 1
                    a.scale = scale

                    a.rotation = scale_rot_attr[2]
                except KeyError:
                    pass
            bm.free()
        a.apply = {'SHIFT', 'SCALE', 'ROTATION', 'ALIGNMENT'}

        b = layout.operator(NAIL_OT_set_tex_transform.bl_idname,
            text=NAIL_OT_set_tex_transform.bl_label + " (Default Apply None)")
        b.shift = a.shift
        b.scale = a.scale
        b.rotation = a.rotation
        b.space_align = a.space_align
        b.plane_align = a.plane_align
        b.apply = set()

        layout.separator()
        layout.operator(NAIL_OT_apply_tex_transform.bl_idname)
        layout.label(text='Auto-apply transforms')

        layout.separator()
        layout.operator(NAIL_OT_clear_tex_transform.bl_idname)

        layout.separator()
        layout.operator(NAIL_OT_unregister.bl_idname)


################
### Handlers ###
################

def depsgraph_update_post_handler(scene, depsgraph):
    print("----")
    for u in depsgraph.updates:
        if u.id == None or u.id.id_type != 'OBJECT':
            continue
        if u.id.type != 'MESH':
            continue
        geom = False
        trans = False
        if (u.is_updated_geometry):
            geom = True
        if u.is_updated_transform:
            trans = True
#        print("  ", u.id, u.is_updated_geometry, u.is_updated_shading, u.is_updated_transform)

        if geom or trans:
            print(u.id, "   updated geom:", geom, "    updated trans:", trans)

#################
### Operators ###
#################

def shared_poll(self, context):
    if context.mode != 'EDIT_MESH':
        self.poll_message_set("Must be run in Edit Mode")
        return False
    obj = context.active_object
    if obj == None and obj.type != 'MESH':
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
    def poll(self, context):
        return shared_poll(self, context)

    def execute(self, context):
        if len(self.apply) > 0:
            alignment = int(self.space_align) | int(self.plane_align)
            set_tex_transform(context,
                shift=(self.shift if 'SHIFT' in self.apply else None),
                alignment=(alignment if 'ALIGNMENT' in self.apply else None),
                scale=(self.scale if 'SCALE' in self.apply else None),
                rotation=(self.rotation if 'ROTATION' in self.apply else None))
        return {'FINISHED'}


class NAIL_OT_apply_tex_transform(Operator):
    bl_idname = "aurycat.nail_apply_tex_transform"
    bl_label = "Apply Current Transform"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Reapplies the selected faces' texture transforms. Useful to run after moving or modifying faces"

    @classmethod
    def poll(self, context):
        return shared_poll(self, context)

    def execute(self, context):
        apply_tex_transform(context)
        return {'FINISHED'}


class NAIL_OT_clear_tex_transform(Operator):
    bl_idname = "aurycat.nail_clear_tex_transform"
    bl_label = "Clear Transform"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Clears shift/scale/rotation to default values on all selected faces"

    @classmethod
    def poll(self, context):
        return shared_poll(self, context)

    def execute(self, context):
        set_tex_transform(context, shift=[0,0], scale=[1,1], rotation=0)
        return {'FINISHED'}


############
### Main ###
############

def split_align_attr(alignment):
    return (int(alignment) & ALIGN_SPACE_BIT,
            int(shift_align_attr[2]) & ALIGN_PLANE_BIT)


def get_tex_transform_from_active_face(context);
    

def get_tex_transform_from_face(bm, face):
             # shift, align, scale, rotation
    result = ([0,0], 0, [1,1], 0)
    try:
        shift_align_layer = bm.faces.layers.float_vector[ATTR_SHIFT_ALIGN]
        scale_rot_layer = bm.faces.layers.float_vector[ATTR_SCALE_ROT]

        shift_align_attr = face[shift_align_layer]
        scale_rot_attr = face[scale_rot_layer]
    except KeyError:
        return result

    result[0] = [shift_align_attr[0], shift_align_attr[1]]
    result[1] = shift_align_attr[2]

    scale = [scale_rot_attr[0], scale_rot_attr[1]]
    if math.isclose(scale[0], 0): scale[0] = 1
    if math.isclose(scale[1], 0): scale[1] = 1
    result[2] = scale
    result[3] = scale_rot_attr[2]

    return result

def set_tex_transform(context, shift=None, alignment=None, scale=None, rotation=None):
    for obj in context.objects_in_mode:
        if obj.type == 'MESH':
            me = obj.data
            bm = bmesh.from_edit_mesh(me)
            make_attrs(bm)
            set_tex_transform_one_object(bm, shift, alignment, scale, rotation)
            apply_tex_transform_one_object(obj, bm)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            bm.free()


def apply_tex_transform(context):
    for obj in context.objects_in_mode:
        if obj.type == 'MESH':
            me = obj.data
            bm = bmesh.from_edit_mesh(me)
            make_attrs(bm)
            apply_tex_transform_one_object(obj, bm)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            bm.free()


def set_tex_transform_one_object(bm, shift=None, alignment=None, scale=None, rotation=None):
    shiftalign_layer = bm.faces.layers.float_vector[ATTR_SHIFT_ALIGN]
    scalerot_layer = bm.faces.layers.float_vector[ATTR_SCALE_ROT]

    # Shift & alignment
    if shift != None and alignment != None:
        for face in bm.faces:
            if face.select:
                cur = face[shiftalign_layer]
                v = Vector((shift[0], shift[1], alignment))
                face[shiftalign_layer] = v
    elif shift != None:
        for face in bm.faces:
            if face.select:
                cur = face[shiftalign_layer]
                v = Vector((shift[0], shift[1], cur[2]))
                face[shiftalign_layer] = v
    elif alignment != None:
        for face in bm.faces:
            if face.select:
                cur = face[shiftalign_layer]
                v = Vector((cur[0], cur[1], alignment))
                face[shiftalign_layer] = v

    # Scale & rotation
    if scale != None:
        if math.isclose(scale[0], 0): scale[0] = 1
        if math.isclose(scale[1], 0): scale[1] = 1

    if scale != None and rotation != None:
        for face in bm.faces:
            if face.select:
                v = Vector((scale[0], scale[1], rotation))
                face[scalerot_layer] = v
    elif scale != None:
        for face in bm.faces:
            if face.select:
                cur = face[scalerot_layer]
                v = Vector((scale[0], scale[1], cur[2]))
                face[scalerot_layer] = v
    elif rotation != None:
        for face in bm.faces:
            if face.select:
                cur = face[scalerot_layer]
                v = Vector((cur[0], cur[1], rotation))
                face[scalerot_layer] = v


def apply_tex_transform_one_object(obj, bm):
    matrix_world = obj.matrix_world
    rot_world = matrix_world.to_quaternion()

    uv_layer = bm.loops.layers.uv.active
    shiftalign_layer = bm.faces.layers.float_vector[ATTR_SHIFT_ALIGN]
    scalerot_layer = bm.faces.layers.float_vector[ATTR_SCALE_ROT]

    for face in bm.faces:
        if not face.select or len(face.loops) == 0:
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


def make_attrs(bm):
    if ATTR_SHIFT_ALIGN not in bm.faces.layers.float_vector:
        bm.faces.layers.float_vector.new(ATTR_SHIFT_ALIGN)
    if ATTR_SCALE_ROT not in bm.faces.layers.float_vector:
        bm.faces.layers.float_vector.new(ATTR_SCALE_ROT)


if __name__ == "__main__":
    main()