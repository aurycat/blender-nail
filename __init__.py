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
# Those have 3 dimensions, so 'rotation' can be shoved into the Z
# coord of the 'scale' attribute. That just leaves the 'shift'
# attribute with an extra unused Z coord.
ATTR_SCALEROT = "Nail_ScaleRot" # per-face Vector(X Scale, Y Scale, Rotation)
ATTR_SHIFT    = "Nail_Shift"    # per-face Vector(X Shift, Y Shift, Unused)
#ATTR_MAP      = "BUVMap"      # per-face-corner (aka per loop)  Vector2D(U, V)


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
    bpy.utils.register_class(UVG_OT_align_uv_to_grid)
    bpy.utils.register_class(NAIL_OT_unregister)
    bpy.utils.register_class(NAIL_MT_main_menu)
    bpy.types.VIEW3D_PT_view3d_lock.append(draw_lock_rotation)
#    bpy.types.VIEW3D_MT_uv_map.append(uvg_draw_menu)
    bpy.types.VIEW3D_MT_editor_menus.append(nail_draw_main_menu)

def unregister():
#    bpy.types.VIEW3D_MT_uv_map.remove(uvg_draw_menu)
    bpy.types.VIEW3D_PT_view3d_lock.remove(draw_lock_rotation)
    bpy.types.VIEW3D_MT_editor_menus.remove(nail_draw_main_menu)
    bpy.utils.unregister_class(UVG_OT_align_uv_to_grid)
    bpy.utils.unregister_class(NAIL_OT_unregister)
    bpy.utils.unregister_class(NAIL_OT_set_tex_transform)
    bpy.utils.unregister_class(NAIL_MT_main_menu)

class NAIL_OT_unregister(Operator):
    bl_idname = "aurycat.nail_unregister"
    bl_label = "Unregister"
    bl_options = {"REGISTER"}

    def execute(self, context):
        unregister()
        return {'FINISHED'}

#def uvg_draw_menu(

#def uvg_draw_menu(self, context):
#    layout = self.layout
#    ob = context.view_layer.objects.active
#    if ob.type != 'MESH':
#        return

#    layout.separator()
#    cl = UVG_OT_align_uv_to_grid
#    a = layout.operator(cl.bl_idname, text = "Align to Grid (Selected Faces)")
#    a.selected_only = True
#    b = layout.operator(cl.bl_idname, text = "Align to Grid (Whole Mesh)")
#    b.selected_only = False

def nail_draw_main_menu(self, context):
    if context.mode == 'EDIT_MESH':
        self.layout.menu(NAIL_MT_main_menu.bl_idname)

def draw_lock_rotation(self, context):
    layout = self.layout
    view = context.space_data
    col = layout.column(align=True)
    col.prop(view.region_3d, "lock_rotation", text="Lock View Rotation")


############
### Main ###
############

class NAIL_MT_main_menu(bpy.types.Menu):
    bl_idname = "NAIL_MT_main_menu"
    bl_label = "Nail"

    def draw(self, context):
        layout = self.layout
        active = context.active_object

        a = layout.operator(NAIL_OT_set_tex_transform.bl_idname,
            text=NAIL_OT_set_tex_transform.bl_label + " (Default Apply None)")
        a.shift = [0,0]
        a.scale = [1,1]
        a.rotation = 0
        # Pull default values from active face
        if active != None and active.type == 'MESH':
            bm = bmesh.from_edit_mesh(active.data)
            if bm.faces.active != None:
                try:
                    shift_layer = bm.faces.layers.float_vector[ATTR_SHIFT]
                    scalerot_layer = bm.faces.layers.float_vector[ATTR_SCALEROT]

                    shift = bm.faces.active[shift_layer]
                    scalerot = bm.faces.active[scalerot_layer]

                    a.shift = [shift[0], shift[1]]

                    scale = [scalerot[0], scalerot[1]]
                    if math.isclose(scale[0], 0): scale[0] = 1
                    if math.isclose(scale[1], 0): scale[1] = 1
                    a.scale = scale

                    a.rotation = scalerot[2]
                except KeyError:
                    pass
            bm.free()
        a.apply = set()

        
        b = layout.operator(NAIL_OT_set_tex_transform.bl_idname,
            text=NAIL_OT_set_tex_transform.bl_label + " (Default Apply All)")
        b.shift = a.shift
        b.scale = a.scale
        b.rotation = a.rotation
        b.apply = {'SHIFT', 'SCALE', 'ROTATION'}

        layout.separator()
        layout.operator(NAIL_OT_unregister.bl_idname)

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
    bl_label = "Set Texture Transform"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Sets the texture shift, scale, and/or rotation for all selected faces to the chosen value, which defaults to the current shift value of the active face"

    enum_items = (
        ('SHIFT', "Shift", "Update shift"),
        ('SCALE', "Scale", "Update scale"),
        ('ROTATION', "Rotation", "Update rotation"),
    )
    apply: bpy.props.EnumProperty(name="Apply", items=enum_items, options={'ENUM_FLAG'})

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

    @classmethod
    def poll(self, context):
        if context.mode != 'EDIT_MESH':
            self.poll_message_set("Must be run in Edit Mode")
            return False
        if context.active_object == None and context.active_object.type != 'MESH':
            self.poll_message_set("Must have an active (selected) mesh object")
            return False
        return True

    def execute(self, context):
        if len(self.apply) > 0:
            set_tex_attrs(context,
                shift=(self.shift if 'SHIFT' in self.apply else None),
                scale=(self.scale if 'SCALE' in self.apply else None),
                rotation=(self.rotation if 'ROTATION' in self.apply else None))
        return {'FINISHED'}


class UVG_OT_align_uv_to_grid(Operator):
    bl_idname = "aurycat.uvg_align_uv_to_grid"
    bl_label = "Align UVs to Grid"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Map UVs of faces to world-space. Similar to Cube Projection"

    selected_only: bpy.props.BoolProperty(name="Selected Only", default=True)
    world_space: bpy.props.BoolProperty(name="World Space", default=True, description="True to align UVs in world/scene space, false to align in object space")

    @classmethod
    def poll(self, context):
        if context.mode != 'EDIT_MESH':
            self.poll_message_set("Must be run in Edit Mode")
            return False
#    if not context.tool_settings.mesh_select_mode[2]:
#        self.poll_message_set("Must be run in face selection mode")
#        return False
        return True

    def execute(self, context):
        print("--- uvg_align_uv_to_grid(selected_only=" + str(self.selected_only) + ", world_space=" + str(self.world_space))
        align_objects(context, self.selected_only, self.world_space)
        return {'FINISHED'}


x_axis = Vector((1,0,0))
y_axis = Vector((0,1,0))
z_axis = Vector((0,0,1))

def set_tex_attrs(context, shift=None, scale=None, rotation=None):
    for obj in context.objects_in_mode:
        if obj.type == 'MESH':
            me = obj.data
            bm = bmesh.from_edit_mesh(me)
            make_attrs(bm)
            set_tex_attrs_one_object(bm, shift, scale, rotation)
            apply_tex_attrs(obj, bm)
            bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
            bm.free()


def set_tex_attrs_one_object(bm, shift=None, scale=None, rotation=None):
    shift_layer = bm.faces.layers.float_vector[ATTR_SHIFT]
    scalerot_layer = bm.faces.layers.float_vector[ATTR_SCALEROT]

    if scale != None:
        if math.isclose(scale[0], 0): scale[0] = 1
        if math.isclose(scale[1], 0): scale[1] = 1

    if shift != None:
        for face in bm.faces:
            if face.select:
                v = Vector((shift[0], shift[1], 0))
                face[shift_layer] = v

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


def apply_tex_attrs(obj, bm):
    matrix_world = obj.matrix_world
    rot_world = matrix_world.to_quaternion()

    uv_layer = bm.loops.layers.uv.active
    shift_layer = bm.faces.layers.float_vector[ATTR_SHIFT]
    scalerot_layer = bm.faces.layers.float_vector[ATTR_SCALEROT]

    world_space = True

    for face in bm.faces:
        if not face.select or len(face.loops) == 0:
            continue

        attr_shift = face[shift_layer]
        attr_scalerot = face[scalerot_layer]
        shift = Vector((attr_shift[0], attr_shift[1]))
        scale = Vector((attr_scalerot[0], attr_scalerot[1]))
        if math.isclose(scale[0], 0): scale[0] = 1
        if math.isclose(scale[1], 0): scale[1] = 1
        rotation = attr_scalerot[2]

        normal = face.normal
        if world_space:
            normal = rot_world @ normal

#        face_center = face.calc_center_median()
#        int_face_center = Vector((math.floor(face_center[0]), math.floor(face_center[1]), math.floor(face_center[2])))

        # The axis to which the normal is closest will be the one with
        # the largest coordinate value.
        dots = [(abs(normal[0]),0), (abs(normal[1]),1), (abs(normal[2]),2)]
        dots = sorted(dots, key = lambda x: x[0])
        best_fit_axis = dots[2][1] # 0, 1, or 2 for x, y, or z

        for loop in face.loops:
            vert_coord = matrix_world @ loop.vert.co

            if best_fit_axis == 0:
                uv_coord = Vector((vert_coord[1], vert_coord[2]))
            elif best_fit_axis == 1:
                uv_coord = Vector((vert_coord[0], vert_coord[2]))
            else:
                uv_coord = Vector((vert_coord[0], vert_coord[1]))

            uv_coord.rotate(Matrix.Rotation(rotation, 2))
            uv_coord *= scale
            uv_coord += shift

            loop[uv_layer].uv = uv_coord
            


#        new_uv_coords = [None]*len(face.loops)
#        min_u = float(‘inf’)
#        min_v = float(‘inf’)

#        i = 0
#        for loop in face.loops:
#            vert_pos = loop.vert.co.copy()
#            vert_pos -= int_face_center  # Move UV island to near center
#            if world_space:
#                vert_pos = matrix_world @ vert_pos
#            if best_fit_axis == 0:
#                coord = [vert_pos[1], vert_pos[2]]
#            elif best_fit_axis == 1:
#                coord = [vert_pos[0], vert_pos[2]]
#            else:
#                coord = [vert_pos[0], vert_pos[1]]
#            if coord[0] < min_u:
#                min_u = coord[0]
#            if coord[0] < min_v:
#                min_v = coord[1]
#            new_uv_coords[i] = coord
#            i += 1

#        for coord in new_uv_coords:
#            rel_u = coord[0] - min_u
#            rel_v = coord[1] - min_v
#            rel_u 
#            

#            coord += shift

#            loop_uv = loop[uv_layer]
#            loop_uv.uv = coord



def align_objects(context, selected_only, world_space):
    for obj in context.objects_in_mode:
        if obj.type == 'MESH':
            align_one_object(obj, selected_only, world_space)

def align_one_object(obj, selected_only, world_space):
    print("--align " + str(obj))
    bm = bmesh.from_edit_mesh(obj.data)
    uv_layer = bm.loops.layers.uv.active

    matrix_world = obj.matrix_world
    rot_world = matrix_world.to_quaternion()

    for face in bm.faces:
        if selected_only and not face.select:
            continue

        normal = face.normal
        if world_space:
            normal = rot_world @ normal

        face_center = face.calc_center_median()
        int_face_center = Vector((math.floor(face_center[0]), math.floor(face_center[1]), math.floor(face_center[2])))

        # The axis to which the normal is closest will be the one with
        # the largest coordinate value.
        dots = [(abs(normal[0]),0), (abs(normal[1]),1), (abs(normal[2]),2)]
        dots = sorted(dots, key = lambda x: x[0])
        best_fit_axis = dots[2][1] # 0, 1, or 2 for x, y, or z

        for loop in face.loops:
            vert_pos = loop.vert.co.copy()
            vert_pos -= int_face_center  # Move UV island to near center
            if world_space:
                vert_pos = matrix_world @ vert_pos

            loop_uv = loop[uv_layer]
            if best_fit_axis == 0:
                loop_uv.uv = Vector((vert_pos[1], vert_pos[2]))
            elif best_fit_axis == 1:
                loop_uv.uv = Vector((vert_pos[0], vert_pos[2]))
            else:
                loop_uv.uv = Vector((vert_pos[0], vert_pos[1]))

    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    bm.free()

def make_attrs(bm):
    if ATTR_SHIFT not in bm.faces.layers.float_vector:
        bm.faces.layers.float_vector.new(ATTR_SHIFT)
    if ATTR_SCALEROT not in bm.faces.layers.float_vector:
        bm.faces.layers.float_vector.new(ATTR_SCALEROT)


#    me = obj.data
#    uv_layer = me.uv_layers.active.data
#    loops = me.loops

#    for poly in me.polygons:
#        for loop_index in poly.loop_indices:
#            loop = me.loops[loop_index]
#            uv_layer_loop = uv_layer[loop_index]

#            print("    Vertex: %d" % me.loops[loop_index].vertex_index)
#            print("    UV: %r" % uv_layer[loop_index].uv)

if __name__ == "__main__":
    main()