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
from mathutils import Vector
import bmesh

ATTR_SCALE = "BUVScale"
ATTR_SHIFT = "BUVShift"
ATTR_ROT   = "BUVRotation"


############
### Init ###
############

def main():
    # Invoke unregister op on an existing "install" of the plugin before
    # re-registering. Lets you press the "Run Script" button without having
    # to maually unregister or run Blender > Reload Scripts first.
    if ('aurycat' in dir(bpy.ops)) and ('uvg_unregister' in dir(bpy.ops.aurycat)):
            bpy.ops.aurycat.uvg_unregister()
    register()

def register():
    bpy.utils.register_class(UVG_OT_align_uv_to_grid)
    bpy.utils.register_class(UVG_OT_unregister)
    bpy.types.VIEW3D_PT_view3d_lock.append(draw_lock_rotation)
    bpy.types.VIEW3D_MT_uv_map.append(uvg_draw_menu)

def unregister():
    bpy.types.VIEW3D_MT_uv_map.remove(uvg_draw_menu)
    bpy.types.VIEW3D_PT_view3d_lock.remove(draw_lock_rotation)
    bpy.utils.unregister_class(UVG_OT_align_uv_to_grid)
    bpy.utils.unregister_class(UVG_OT_unregister)

class UVG_OT_unregister(Operator):
    bl_idname = "aurycat.uvg_unregister"
    bl_label = "Unregister"
    bl_options = {"REGISTER"}

    def execute(self, context):
        unregister()
        return {'FINISHED'}

def uvg_draw_menu(self, context):
    layout = self.layout
    ob = context.view_layer.objects.active
    if ob.type != 'MESH':
        return

    layout.separator()
    cl = UVG_OT_align_uv_to_grid
    a = layout.operator(cl.bl_idname, text = "Align to Grid (Selected Faces)")
    a.selected_only = True
    b = layout.operator(cl.bl_idname, text = "Align to Grid (Whole Mesh)")
    b.selected_only = False

def draw_lock_rotation(self, context):
    layout = self.layout
    view = context.space_data
    col = layout.column(align=True)
    col.prop(view.region_3d, "lock_rotation", text="Lock View Rotation")


############
### Main ###
############

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

def make_attrs(obj):
    pass


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