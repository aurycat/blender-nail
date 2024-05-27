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
from bpy.types import Operator
from bpy.app.handlers import persistent
from mathutils import Vector, Matrix

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
    auto_apply: bpy.props.BoolProperty(name="Auto-Apply Transforms", default=False, update=auto_apply_updated,
        description="Automatically applies the current transform as objects are transformed or meshes are updated. While in edit mode, only applies to selected faces or their adjacent faces, for efficiency")
    fast_updates: bpy.props.BoolProperty(name="Fast Update Rate", default=False,
        description="Makes Auto Apply update faster, potentially slowing down Blender while editing larger meshes")


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
        if on_post_depsgraph_update not in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.append(on_post_depsgraph_update)
    else:
        if on_post_depsgraph_update in bpy.app.handlers.depsgraph_update_post:
            bpy.app.handlers.depsgraph_update_post.remove(on_post_depsgraph_update)

UPDATE_INTERVAL = 1

# https://blender.stackexchange.com/a/283286/154191
@persistent
def on_post_depsgraph_update(scene, depsgraph):
    # When fast is set, do the apply every depsgraph update
    fast = bpy.context.window_manager.nail_settings.fast_updates

    if not fast:
        self = on_post_depsgraph_update
        op = bpy.context.active_operator
        op_changed = op is not self.last_operator
        self.last_operator = op
        self.last_obj_list.clear()

    for u in depsgraph.updates:
        if not u.is_updated_transform and not u.is_updated_geometry:
            continue
        if u.id is None or u.id.id_type != 'OBJECT' or u.id.type != 'MESH':
            continue
        if not mesh_has_nail_attrs(u.id.data):
            continue

        if fast:
            apply_tex_transform_one_object(u.id, bpy.context, auto_apply=True)
        else:
            self.last_obj_list.append(u.id)

    if not fast and len(self.last_obj_list) > 0:
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
                apply_tex_transform_one_object(obj, bpy.context, auto_apply=True)
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
    for obj in on_post_depsgraph_update.last_obj_list:
        apply_tex_transform_one_object(obj, bpy.context, auto_apply=True)
    return None


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
            apply_tex_transform_one_mesh(obj, bm, context, auto_apply=False)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            bm.free()


def apply_tex_transform(context):
    for obj in context.objects_in_mode:
        if obj.type == 'MESH':
            apply_tex_transform_one_object(obj, context, auto_apply=False)


def apply_tex_transform_one_object(obj, context, auto_apply=False): # Must be MESH type
    me = obj.data
    if context.mode == 'EDIT_MESH':
        bm = bmesh.from_edit_mesh(me)
        make_nail_attrs(me, bm)
        apply_tex_transform_one_mesh(obj, bm, context, auto_apply=auto_apply)
        bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
        bm.free()
    elif context.mode == 'OBJECT':
        bm = bmesh.new()
        bm.from_mesh(me)
        make_nail_attrs(me, bm)
        apply_tex_transform_one_mesh(obj, bm, context, auto_apply=auto_apply)
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


def apply_tex_transform_one_mesh(obj, bm, context, auto_apply=False):
    matrix_world = obj.matrix_world
    rot_world = matrix_world.to_quaternion()

    uv_layer = bm.loops.layers.uv.active
    shiftalign_layer = bm.faces.layers.float_vector[ATTR_SHIFT_ALIGN]
    scalerot_layer = bm.faces.layers.float_vector[ATTR_SCALE_ROT]

    for face in bm.faces:
        if len(face.loops) == 0: # Not sure if this is possible, but safety check anyway
            continue

        if auto_apply:
            if context.mode == 'EDIT_MESH':
                # In auto-apply mode, apply to selected faces as well as any faces that
                # have a selected vertex, since they could be affected by the changes
                if not face.select:
                    any_selected_verts = False
                    for v in face.verts:
                        if v.select:
                            any_selected_verts = True
                            break
                    if not any_selected_verts:
                        continue
            else:
                pass # In object mode, always update all faces
        else: # Manually applied via menu option
            if context.mode == 'EDIT_MESH':
                if not face.select:
                    continue
            else:
                pass # In object mode, always update all faces

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


def make_nail_attrs(me, bm):
    if len(bm.loops.layers.uv) == 0:
        bm.loops.layers.uv.new("UVMap")
    elif bm.loops.layers.uv.active is None:
        # Not sure if this is possible, but just to be safe
        raise RuntimeError(f"Mesh '{m.name}' has at least one UV Map, but none are marked 'active'. Please make sure a UVMap is selected on this mesh.")

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
    return (len(m.uv_layers) > 0 and
            ATTR_SHIFT_ALIGN in m.attributes and
            ATTR_SCALE_ROT   in m.attributes and
            m.attributes[ATTR_SHIFT_ALIGN].domain    == 'FACE' and
            m.attributes[ATTR_SCALE_ROT].domain      == 'FACE' and
            m.attributes[ATTR_SHIFT_ALIGN].data_type == 'FLOAT_VECTOR' and
            m.attributes[ATTR_SCALE_ROT].data_type   == 'FLOAT_VECTOR')
           

if __name__ == "__main__":
    main()

