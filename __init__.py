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
    "version": (0, 1),
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
import traceback
import numpy
from bpy.types import Operator, Macro, AddonPreferences
from bpy.app.handlers import persistent
from mathutils import Euler, Vector, Matrix, Quaternion
from operator import attrgetter


###############################################################################
#################################  Constants  #################################
###############################################################################

# __name__ is "__main__" and __package__ is None when running via Blender script editor
RUNNING_AS_SCRIPT = (__name__ == "__main__")
PACKAGE_NAME = (__package__ if __package__ is not None else "blender-nail")

face_vec3_getter = attrgetter("faces.layers.float_vector")
# float_color is the only vec4 attribute accessible by BMesh >:(
face_vec4_getter = attrgetter("faces.layers.float_color")

ATTRS = {
    # ShiftFlags and ScaleRot each combine two pieces of data into one attribute.
    # This is because bmesh doesn't support accessing a per-face 2D Vector attribute,
    # so just storing shift or scale separately would leave the z coord unused.
    "Nail_ShiftFlags":     ('FACE', 'FLOAT_VECTOR', face_vec3_getter, 'shift_flags_layer'),
    "Nail_ScaleRot":       ('FACE', 'FLOAT_VECTOR', face_vec3_getter, 'scale_rot_layer'),
    "Nail_LockUAxis":      ('FACE', 'FLOAT_VECTOR', face_vec3_getter, 'lock_uaxis_layer'),
    "Nail_LockVAxis":      ('FACE', 'FLOAT_VECTOR', face_vec3_getter, 'lock_vaxis_layer'),
}

VEC3_ATTR_DEFAULT = Vector((0,0,0)).freeze()
VEC4_ATTR_DEFAULT = Vector((1,1,1,1)).freeze()

# TextureConfig flags. The bitmask is stored per-face, in the z coordinate of Nail_ShiftFlags
TCFLAG_ENABLED = 1        # True to use Nail on this face, otherwise Nail will ignore it.
                          # The default is False, so Nail is "opt-in".
TCFLAG_OBJECT_SPACE = 2   # True to compute UV axis in object-space. Default is world-space.
TCFLAG_ALIGN_FACE = 4     # True to align the UV axes to the face. This helps for rotated
                          # objects, or to ensure the texture isn't flipped. Cannot be combined
                          # with ALIGN_LOCKED.
TCFLAG_ALIGN_LOCKED = 8   # True for manual alignment of UX axes. Cannot be combined with
                          # ALIGN_FACE.

TCFLAG_ALL = TCFLAG_ENABLED | TCFLAG_OBJECT_SPACE | TCFLAG_ALIGN_FACE | TCFLAG_ALIGN_LOCKED

def nail_classes():
    return [
        AURYCAT_OT_nail_unregister,
        NailPreferences,
        AURYCAT_OT_nail_sleep,
        AURYCAT_MT_nail_main_menu,
        # Operators
        AURYCAT_OT_nail_mark_nailface,
        AURYCAT_OT_nail_clear_nailface,
        AURYCAT_OT_nail_edit_texture_config,
        AURYCAT_OT_nail_reset_texture_config,
        AURYCAT_OT_nail_reapply_texture_config,
        AURYCAT_OT_nail_copy_active_to_selected,
        AURYCAT_OT_nail_locked_transform,
        # Interactive Texture-locked Transform Operators
        AURYCAT_OT_nail_modal_locked_translate,
        AURYCAT_OT_nail_modal_locked_rotate,
        AURYCAT_OT_nail_modal_locked_scale,
        AURYCAT_OT_nail_keybind_modal_locked_translate,
        AURYCAT_OT_nail_keybind_modal_locked_rotate,
        AURYCAT_OT_nail_internal_modal_locked_transform,
        AURYCAT_OT_nail_internal_end_locked_translate,
        AURYCAT_OT_nail_internal_end_locked_rotate,
        AURYCAT_OT_nail_internal_end_locked_scale,
    ]


###############################################################################
###########################  Register / Unregister  ###########################
###############################################################################

draw_handler = None

def register():

    # Invoke unregister op on an existing "install" of the plugin before
    # re-registering. Lets you press the "Run Script" button without having
    # to maually unregister or run Blender > Reload Scripts first.
    if ('aurycat' in dir(bpy.ops)) and ('nail_unregister' in dir(bpy.ops.aurycat)):
        if RUNNING_AS_SCRIPT:
            # Running via "Run Script"
            bpy.ops.aurycat.nail_unregister()
        else:
            # Running via real addon -- Blender doesn't let us invoke ops here.
            # This shouldn't happen in normal addon usage, so just abort.
            raise RuntimeError("Nail is already registered! Run bpy.ops.aurycat.nail_unregister() in the Python Console or restart Blender.")

    try:
        for cls in nail_classes():
            bpy.utils.register_class(cls)

        AURYCAT_OT_nail_internal_modal_locked_transform.active = None

        bpy.types.VIEW3D_PT_view3d_lock.append(draw_lock_rotation)
        bpy.types.VIEW3D_MT_editor_menus.append(nail_draw_main_menu)

        set_post_load_handler_enabled(True)

        # This awakens Nail if any NailMeshes exist in the scene.
        # See on_post_reigster() header comment for why this needs
        # to be done via a handler.
        set_post_register_handler_enabled(True)
    except Exception as e:
        unregister()
        raise e


def unregister():
    global nail_is_awake
    nail_is_awake = False
    no_except(lambda: set_post_register_handler_enabled(False))
    no_except(lambda: set_post_load_handler_enabled(False))
    no_except(lambda: enable_post_depsgraph_update_handler(False))
    no_except(lambda: bpy.types.VIEW3D_PT_view3d_lock.remove(draw_lock_rotation))
    no_except(lambda: bpy.types.VIEW3D_MT_editor_menus.remove(nail_draw_main_menu))
    no_except(lambda: bpy.types.SpaceView3D.draw_handler_remove(draw_handler, 'WINDOW'), silent=True)
    no_except(lambda: remove_keymaps())
    for cls in nail_classes():
        no_except(lambda: bpy.utils.unregister_class(cls))


class AURYCAT_OT_nail_unregister(Operator):
    bl_idname = "aurycat.nail_unregister"
    bl_label = "Unregister"
    bl_description = "Unregister Nail addon"
    bl_options = {"REGISTER"}
    def execute(self, context):
        unregister()
        return {'FINISHED'}


###############################################################################
###############################  Awake / Sleep  ###############################
###############################################################################

# When Nail is "awake", the keybinds and depsgraph update handlers are registered.
# Nail tries to be "asleep" when not needed to avoid causing trouble / performance
# issues by running all the time when not necessary.
#
# Nail wakes up when a new blend file has been loaded that contains a NailMesh,
# or when the addon is registered and the existing blend file contains a NailMesh,
# or when a NailMesh is created (e.g. "Mark NailFace").
#
# Nail goes to sleep when the addon is unregistered (duh) or when a new blend file
# is loaded that does not contain a NailMesh. It can also be put to sleep manually
# with `bpy.ops.aurycat.nail_sleep()`.

nail_is_awake = False

def set_post_load_handler_enabled(enable):
    set_handler_enabled(bpy.app.handlers.load_post, on_post_load, enable)


def set_post_register_handler_enabled(enable):
    set_handler_enabled(bpy.app.handlers.depsgraph_update_post, on_post_register, enable)


@persistent
def on_post_load(path):
    if any_nail_meshes():
        nail_wake()
    else:
        nail_sleep()


# This handler is used to detect right after the plug is registered, once bpy.data
# is available, in order to check if Nail should be awake.
#
# While an addon is being Registered, bpy.context/bpy.data are set to dummy
# 'Restricted' objects which don't contain any real information. At register, Nail
# needs to check if it should become awake, and it needs to look at bpy.data.objects
# to do that. So this handler is added to get the first depsgraph update after
# registration. By the time any depsgraph update happens, bpy.data is sure to
# be available. This handler then unregisters itself immediately.
@persistent
def on_post_register(scene, depsgraph):
    # Immediately disable this handler, it just needs to run once after registering
    set_post_register_handler_enabled(False)

    if not nail_is_awake and any_nail_meshes():
        nail_wake()
        # If this event woke up Nail, and auto_apply is enabled, pass the depsgraph update
        # on to the standard depsgraph update handler so we don't miss any frames
        if NailPreferences.get('auto_apply'):
            on_post_depsgraph_update(scene, depsgraph)


def any_nail_meshes():
    for obj in bpy.data.objects:
        if NailMesh.is_nail_object(obj):
            return True
    return False


def nail_wake_if_needed():
    if not nail_is_awake and any_nail_meshes():
        nail_wake()


def nail_wake():
    global nail_is_awake, draw_handler

    if nail_is_awake:
        return

    print("** Nail addon wake")
    nail_is_awake = True

    draw_handler = bpy.types.SpaceView3D.draw_handler_add(debug_draw_3dview, (), 'WINDOW', 'POST_VIEW')
    auto_apply_updated(None, None)
    update_rate_updated(None, None)
    use_locked_transform_keymaps_updated(None, None)


def nail_sleep():
    global nail_is_awake, draw_handler

    if not nail_is_awake:
        return

    print("** Nail addon sleep")
    nail_is_awake = False

    no_except(lambda: enable_post_depsgraph_update_handler(False))
    no_except(lambda: bpy.types.SpaceView3D.draw_handler_remove(draw_handler, 'WINDOW'), silent=True)
    no_except(lambda: remove_keymaps())


class AURYCAT_OT_nail_sleep(Operator):
    bl_idname = "aurycat.nail_sleep"
    bl_label = "Nail Sleep"
    bl_description = "Manually put Nail addon to sleep (unregister handlers and keybinds). This is done automatically when loading a new file which does not contain NailMesh objects. Nail can be awoken by loading a file with NailMesh objects, or by using Mark NailFace"
    bl_options = {"REGISTER"}
    def execute(self, context):
        nail_sleep()
        return {'FINISHED'}


###############################################################################
##################################  Keymaps  ##################################
###############################################################################

def keymapped_ops():
    return [
        {'idname': AURYCAT_OT_nail_keybind_modal_locked_translate.bl_idname, 'type': 'G', 'value': 'PRESS'},
        {'idname': AURYCAT_OT_nail_keybind_modal_locked_rotate.bl_idname, 'type': 'R', 'value': 'PRESS'},
    ]


def add_keymaps():
    remove_keymaps()
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if kc:
        # Calling 'new' here only makes a new keymap if one doesn't already exist
        km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
        for op in keymapped_ops():
            km.keymap_items.new(**op)


def remove_keymaps():
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    keymapped_op_names = [op['idname'] for op in keymapped_ops()]
    if kc:
        km = kc.keymaps.new(name='Mesh', space_type='EMPTY')
        to_remove = []
        for name, kmi in km.keymap_items.items():
            if name in keymapped_op_names:
                to_remove.append(kmi)
        for kmi in to_remove:
            km.keymap_items.remove(kmi)


###############################################################################
################################  Preferences  ################################
###############################################################################

def auto_apply_updated(self, context):
    enable_post_depsgraph_update_handler(nail_is_awake and NailPreferences.get('auto_apply'))


def update_rate_updated(self, context):
    on_post_depsgraph_update.update_interval = NailPreferences.get('update_rate')


def use_locked_transform_keymaps_updated(self, context):
    if nail_is_awake and NailPreferences.get('use_locked_transform_keymaps'):
        add_keymaps()
    else:
        no_except(lambda: remove_keymaps())


class NailPreferences(AddonPreferences):
    bl_idname = PACKAGE_NAME

    auto_apply: bpy.props.BoolProperty(
        name="Auto-Apply Textures",
        description="If checked, automatically applies a NailFaces' texture settings as they are moved or modified. While in edit mode, only applies to selected faces or their adjacent faces, for efficiency",
        default=True,
        update=auto_apply_updated)

    update_rate: bpy.props.FloatProperty(
        name="Update Rate",
        description="How fast to auto-apply. Usually fine to leave at 0 seconds (Immediate), but for very large meshes or slower computers, it may be helpful to specify a slower update rate",
        subtype='TIME_ABSOLUTE',
        default=0, min=0, max=2,
        update=update_rate_updated)

    wrap_uvs: bpy.props.BoolProperty(
        name="Wrap UVs",
        description="If checked, each face's UV island is wrapped to be near (0,0) in UV space. Otherwise, UVs are projected literally from world-space coordinates, meaning the UVs can be very far from (0,0) if the face is far from the world origin",
        default=True)

    use_locked_transform_keymaps: bpy.props.BoolProperty(
        name="Use Texture-Locked Transform keymap overrides for G and R",
        description="If checked, Nail automatically replaces the G (grab/move) and R (rotate) edit-mode keymaps with Nail's locked-transform variants when a project containing a NailMesh is opened. These operators behave like normal G and R for non-NailMeshes. If unchecked, you can still access the locked-transform operators via the Nail menu",
        default=True,
        update=use_locked_transform_keymaps_updated)

    @classmethod
    def get(cls, name):
        try:
            return bpy.context.preferences.addons[PACKAGE_NAME].preferences[name]
        except:
            # Try to get the default value of the property
            return cls.__annotations__[name].keywords['default']

    def draw(self, context):
        layout = self.layout
        auto_apply = NailPreferences.get('auto_apply')
        update_rate = NailPreferences.get('update_rate')

        layout.use_property_split = False
        layout.use_property_decorate = False

        sp = layout.split()
        sp.prop(self, 'auto_apply')
        sp2 = sp.split()
        sp2.enabled = auto_apply
        nam = "Update Rate" + ("    (Immediate)" if update_rate == 0 and auto_apply else "")
        sp2.prop(self, 'update_rate', text=nam)

        layout.prop(self, 'wrap_uvs')
        layout.prop(self, 'use_locked_transform_keymaps')


###############################################################################
###################################  Menu  ####################################
###############################################################################

def nail_draw_main_menu(self, context):
    if context.mode == 'EDIT_MESH':
        self.layout.menu(AURYCAT_MT_nail_main_menu.bl_idname)


def draw_lock_rotation(self, context):
    layout = self.layout
    view = context.space_data
    col = layout.column(align=True)
    col.prop(view.region_3d, "lock_rotation", text="Lock View Rotation")


class AURYCAT_MT_nail_main_menu(bpy.types.Menu):
    bl_idname = "AURYCAT_MT_nail_main_menu"
    bl_label = "Nail"

    def draw(self, context):
        layout = self.layout

        layout.label(text="Enable / Disable Nail (per face)", icon='TOOL_SETTINGS')
        layout.operator(AURYCAT_OT_nail_mark_nailface.bl_idname)
        layout.operator(AURYCAT_OT_nail_clear_nailface.bl_idname)

        layout.separator()
        layout.label(text="Face Edit", icon='UV_DATA')
        layout.operator(AURYCAT_OT_nail_edit_texture_config.bl_idname)
        layout.operator(AURYCAT_OT_nail_reset_texture_config.bl_idname)
        layout.operator(AURYCAT_OT_nail_reapply_texture_config.bl_idname)
        layout.operator(AURYCAT_OT_nail_copy_active_to_selected.bl_idname)

        layout.separator()
        layout.label(text="Texture-Locked Transform", icon='LOCKED')
        layout.operator(AURYCAT_OT_nail_modal_locked_translate.bl_idname)
        layout.operator(AURYCAT_OT_nail_modal_locked_rotate.bl_idname)
        layout.operator(AURYCAT_OT_nail_modal_locked_scale.bl_idname)
        layout.operator(AURYCAT_OT_nail_locked_transform.bl_idname, text="Texture-Locked Transform (Noninteractive)")

        layout.separator()
        layout.operator("preferences.addon_show", text="Preferences")
        # When running from Blender script editor show unregister button for convenience
        if RUNNING_AS_SCRIPT:
            layout.operator(AURYCAT_OT_nail_unregister.bl_idname)

        # As a saftey check to make sure this hacky modal operator can't get
        # too off the rails! If somehow our menu is opened, surely the operator
        # should be cancelled.
        if AURYCAT_OT_nail_internal_modal_locked_transform.active is not None:
            # Check for ReferenceError because 'active' may be non-None but an invalid/destroyed bpy_struct
            try:
                AURYCAT_OT_nail_internal_modal_locked_transform.active.cancelled = True
            except ReferenceError:
                AURYCAT_OT_nail_internal_modal_locked_transform.active = None


###############################################################################
############################  Auto-apply handler  #############################
###############################################################################

def enable_post_depsgraph_update_handler(enable):
    set_handler_enabled(bpy.app.handlers.depsgraph_update_post, on_post_depsgraph_update, enable)


# https://blender.stackexchange.com/a/283286/154191
@persistent
def on_post_depsgraph_update(scene, depsgraph):
    self = on_post_depsgraph_update

    # Updating the mesh triggers a depsgraph update; prevent infinite loop
    if self.timer_ran:
        self.timer_ran = False
        return

    # Don't live update while doing a locked transform
    if AURYCAT_OT_nail_internal_modal_locked_transform.active is not None:
        # Check for invalid/destroyed bpy_struct by trying to access a property
        try:
            foo = AURYCAT_OT_nail_internal_modal_locked_transform.active.cancelled
        except ReferenceError:
            AURYCAT_OT_nail_internal_modal_locked_transform.active = None
        return

    # For ~0 update_interval, do the apply every depsgraph update
    if self.update_interval < 0.04:
        for u in depsgraph.updates:
            if depsgraph_update_is_applicable(u):
                do_auto_apply(u.id)
        return

    op = bpy.context.active_operator
    op_changed = op is not self.last_operator
    self.last_operator = op

    any_geom_updates = False

    for u in depsgraph.updates:
        if depsgraph_update_is_applicable(u):
            if not any_geom_updates:
                any_geom_updates = True
                self.last_obj_list.clear()
            self.last_obj_list.append(u.id.original)

    if any_geom_updates:
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
                bpy.app.timers.register(geom_update_timer, first_interval=self.update_interval)


on_post_depsgraph_update.update_interval = 0
on_post_depsgraph_update.last_operator = None
on_post_depsgraph_update.last_obj_list = []
on_post_depsgraph_update.timer_ran = False

def depsgraph_update_is_applicable(u):
    if not u.is_updated_transform and not u.is_updated_geometry:
        return False
    if u.id is None or u.id.id_type != 'OBJECT' or u.id.type != 'MESH':
        return False
    if not NailMesh.is_nail_mesh(u.id.data):
        return False
    return True


def geom_update_timer():
    on_post_depsgraph_update.timer_ran = True
    for obj in on_post_depsgraph_update.last_obj_list:
        do_auto_apply(obj)
    return None


def do_auto_apply(obj):
    with NailMesh(obj) as nm:
        nm.apply_texture(auto_apply=True)


###############################################################################
#################################  Operators  #################################
###############################################################################

def shared_poll(cls, context, only_face_select=False):
    if context.mode != 'EDIT_MESH':
        if cls is not None: cls.poll_message_set("Must be run in Edit Mode")
        return False
    if only_face_select:
        m = context.tool_settings.mesh_select_mode[:]
        if not (not m[0] and not m[1] and m[2]):
            if cls is not None: cls.poll_message_set("Must be run in face selection mode")
            return False
    return True


class AURYCAT_OT_nail_mark_nailface(Operator):
    bl_idname = "aurycat.nail_mark_nailface"
    bl_label = "Mark NailFace"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Enables Nail on the selected faces. This automatically makes the mesh into a \"NailMesh\" (adds necessary attributes) if it isn't already"

    @classmethod
    def poll(cls, context):
        return shared_poll(cls, context)

    def execute(self, context):
        tc = TextureConfig.new_unset()
        tc.flags_set |= TCFLAG_ENABLED
        tc.flags |= TCFLAG_ENABLED
        set_or_apply_selected_faces(tc, context, set=True, apply=True, only_nailmeshes=False)
        # This action may result in NailMeshes being created; may need wakeup
        nail_wake_if_needed()
        return {'FINISHED'}


class AURYCAT_OT_nail_clear_nailface(Operator):
    bl_idname = "aurycat.nail_clear_nailface"
    bl_label = "Clear NailFace"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Disables Nail on the selected faces"

    @classmethod
    def poll(cls, context):
        return shared_poll(cls, context)

    def execute(self, context):
        tc = TextureConfig.new_unset()
        tc.flags_set |= TCFLAG_ENABLED
        set_or_apply_selected_faces(tc, context, set=True, apply=False, only_nailmeshes=True)
        return {'FINISHED'}


class AURYCAT_OT_nail_edit_texture_config(Operator):
    bl_idname = "aurycat.nail_edit_texture_config"
    bl_label = "Edit Texture"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "(Opens in operator property window!) Edits the texture shift, scale, rotation, and/or alignment for all selected NailFaces to the chosen values. The default values are that of the active face. If no selected faces are NailFaces, they are all automatically marked NailFace"

    last_invoke_self = None
    differing_values = set()
    locked_alignment = False

    set_shift: bpy.props.BoolProperty(name="Set Shift")

    shift: bpy.props.FloatVectorProperty(
        name="Shift",
        default=[0,0],
        subtype='XYZ',
        size=2,
        soft_min=-1,
        soft_max=1,
        step=1)

    set_scale: bpy.props.BoolProperty(name="Set Scale")

    scale: bpy.props.FloatVectorProperty(
        name="Scale",
        default=[1,1],
        subtype='XYZ',
        size=2,
        step=1)

    set_rotation: bpy.props.BoolProperty(name="Set Rotation")

    rotation: bpy.props.FloatProperty(
        name="Rotation",
        default=0,
        subtype='ANGLE',
        soft_min=-math.pi*2,
        soft_max=math.pi*2,
        step=50)

    def uv_align_items(self, _):
        cls = AURYCAT_OT_nail_edit_texture_config
        arr = []
        if cls.locked_alignment or 'uv_align' in cls.differing_values:
            arr.append(('unset', "---", "<Don't change>", 0))
        arr.extend([
            ('axis',  "Axis",  "UV projection is aligned to axis planes", 1),
            ('face',  "Face",  "UV projection is aligned to the face plane", 2),
        ])
        return arr

    def space_align_items(self, _):
        cls = AURYCAT_OT_nail_edit_texture_config
        arr = []
        if 'space_align' in cls.differing_values:
            arr.append(('unset', "---", "<Don't change>", 0))
        arr.extend([
            ('world',  "World",  "Determine UVs from world-space cube projection", 1),
            ('object', "Object", "Determine UVs from object-space cube projection", 2),
        ])
        return arr

    uv_align: bpy.props.EnumProperty(
        name="UV Alignment",
        items=uv_align_items,
        default=1)

    space_align: bpy.props.EnumProperty(
        name="Space Alignment",
        items=space_align_items,
        default=1)

    @classmethod
    def poll(cls, context):
        return shared_poll(cls, context)

    def draw(self, context):
        cls = AURYCAT_OT_nail_edit_texture_config

        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        def conditionally_enabled_prop(propname, enabled):
            s = layout.split()
            s.enabled = enabled
            s.prop(self, propname)

        if 'shift' in cls.differing_values:
            if not self.set_shift:
                layout.label(text="Selected faces have differing shift values")
            layout.prop(self, 'set_shift')
        conditionally_enabled_prop('shift', self.set_shift)

        layout.separator()
        if 'scale' in cls.differing_values:
            if not self.set_scale:
                layout.label(text="Selected faces have differing scale values")
            layout.prop(self, 'set_scale')
        conditionally_enabled_prop('scale', self.set_scale)

        layout.separator()
        if 'rotation' in cls.differing_values:
            if not self.set_rotation:
                layout.label(text="Selected faces have differing rotation values")
            layout.prop(self, 'set_rotation')
        conditionally_enabled_prop('rotation', self.set_rotation)

        layout.separator()
        layout.separator()
        if self.uv_align == 'unset':
            if cls.locked_alignment:
                s = layout.column()
                s.scale_y = 0.6
                s.label(text="Selected face(s) have non-standard UV alignment")
                s.label(text="This is caused by using Texture-Locked Rotate")
                s.separator()
            elif 'uv_align' in cls.differing_values:
                layout.label(text="Selected faces have differing UV alignment values")
        layout.prop(self, 'uv_align')

        layout.separator()
        if (self.space_align == 'unset') and ('space_align' in cls.differing_values):
            layout.label(text="Selected faces have differing space alignment values")
        layout.prop(self, 'space_align')

    # The 'invoke' portion of this operator fills in the default values of the operator
    # from the selected faces, before going to execute.
    def invoke(self, context, event):
        cls = AURYCAT_OT_nail_edit_texture_config
        cls.last_invoke_self = self
        cls.differing_values = set()
        cls.locked_alignment = False

        any_selected_faces = [False] # Bool in an array to "pass by reference"
        tc = TextureConfig.from_selected_faces(out_any_selected=any_selected_faces)

        if not any_selected_faces[0]:
            self.report({'WARNING'}, "No selected faces")
            return {'CANCELLED'}

        if not tc.multiple_faces:
            # This implies that no enabled faces were selected
            # Use the default config with enabled set so that these faces become enabled
            tc = TextureConfig.new_default()
            tc.flags |= TCFLAG_ENABLED
            tc.flags_set |= TCFLAG_ENABLED
            set_or_apply_selected_faces(tc, context, set=True, apply=True, only_nailmeshes=False)
            # This action may result in NailMeshes being created; may need wakeup
            nail_wake_if_needed()

        # Set default value for 'shift' property
        if tc.shift is not None:
            self.shift = tc.shift.to_tuple()
            self.set_shift = True
        else:
            cls.differing_values.add('shift')
            self.shift = [0,0]
            self.set_shift = False

        # Set default value for 'scale' property
        if tc.scale is not None:
            self.scale = tc.scale.to_tuple()
            self.set_scale = True
        else:
            cls.differing_values.add('scale')
            self.scale = [1,1]
            self.set_scale = False

        # Set default value for 'rotation' property
        if tc.rotation is not None:
            self.rotation = tc.rotation
            self.set_rotation = True
        else:
            cls.differing_values.add('rotation')
            self.rotation = 0
            self.set_rotation = False

        # Set default value for 'space_align' property
        if flag_is_set(tc.flags_set, TCFLAG_OBJECT_SPACE):
            self.space_align = 'object' if flag_is_set(tc.flags, TCFLAG_OBJECT_SPACE) else 'world'
        else:
            cls.differing_values.add('space_align') # Must add this before setting space_align to 'unset' so that space_align_items() includes 'unset' in the enum
            self.space_align = 'unset'

        # Set default value for 'uv_align' property
        if flag_is_set(tc.flags_set, TCFLAG_ALIGN_FACE) and flag_is_set(tc.flags_set, TCFLAG_ALIGN_LOCKED):
            if flag_is_set(tc.flags, TCFLAG_ALIGN_FACE):
                self.uv_align = 'face'
            elif flag_is_set(tc.flags, TCFLAG_ALIGN_LOCKED):
                cls.locked_alignment = True # Must set this before setting uv_align to 'unset' so that uv_align_items() includes 'unset' in the enum
                self.uv_align = 'unset'
            else:
                self.uv_align = 'axis'
        else:
            cls.differing_values.add('uv_align') # Must add this before setting uv_align to 'unset' so that uv_align_items() includes 'unset' in the enum
            self.uv_align = 'unset'

        return self.execute(context)

    # The 'execute' portion of this operator applies the properties to the selected faces
    def execute(self, context):
        cls = AURYCAT_OT_nail_edit_texture_config
        if self is not cls.last_invoke_self:
            # New invocation, clear this so the "differing value" labels dont show
            cls.differing_values = set()
            cls.locked_alignment = False

        if self.set_shift or self.set_scale or self.set_rotation or self.space_align != 'unset' or self.uv_align != 'unset':
            tc = TextureConfig.new_unset()

            if self.set_shift:
                tc.shift = Vector(self.shift)
            if self.set_scale:
                tc.scale = Vector(self.scale)
            if self.set_rotation:
                tc.rotation = self.rotation
            if self.space_align != 'unset':
                tc.flags_set |= TCFLAG_OBJECT_SPACE
                if self.space_align == 'object':
                    tc.flags |= TCFLAG_OBJECT_SPACE
            if self.uv_align != 'unset':
                tc.flags_set |= TCFLAG_ALIGN_FACE
                tc.flags_set |= TCFLAG_ALIGN_LOCKED
                if self.uv_align == 'face':
                    tc.flags |= TCFLAG_ALIGN_FACE

            set_or_apply_selected_faces(tc, context, set=True, apply=True, only_nailmeshes=True)

        return {'FINISHED'}


class AURYCAT_OT_nail_reset_texture_config(Operator):
    bl_idname = "aurycat.nail_reset_texture_config"
    bl_label = "Reset Texture"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Clears texture shift, scale, rotation, and alignment of selected NailFaces to default values"

    @classmethod
    def poll(cls, context):
        return shared_poll(cls, context)

    def execute(self, context):
        tc = TextureConfig.new_default()
        tc.flags_set &= ~TCFLAG_ENABLED # Don't change enabled state
        set_or_apply_selected_faces(tc, context, set=True, apply=True, only_nailmeshes=True)
        return {'FINISHED'}


class AURYCAT_OT_nail_reapply_texture_config(Operator):
    bl_idname = "aurycat.nail_reapply_texture_config"
    bl_label = "Reapply Texture"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Reapply the existing texture shift, scale, rotation, and alignment of the selected NailFaces.\nUseful to run this after moving or modifying faces, but rarely needed when Auto-Apply Textures is enabled"

    @classmethod
    def poll(cls, context):
        return shared_poll(cls, context)

    def execute(self, context):
        # Doesn't change enabled state
        set_or_apply_selected_faces(None, context, set=False, apply=True, only_nailmeshes=True)
        return {'FINISHED'}


class AURYCAT_OT_nail_copy_active_to_selected(Operator):
    bl_idname = "aurycat.nail_copy_active_to_selected"
    bl_label = "Copy Active to Selected"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Copies the texture shift, scale, rotation, and alignment of the active selected NailFace to all other selected NailFaces"

    @classmethod
    def poll(cls, context):
        return shared_poll(cls, context)

    def execute(self, context):
        tc, errmsg = TextureConfig.from_active_face()
        if errmsg != None:
            self.report({'ERROR'}, errmsg)
            return {'CANCELLED'}

        tc.flags_set &= ~TCFLAG_ENABLED # Don't change enabled state
        set_or_apply_selected_faces(tc, context, set=True, apply=True, only_nailmeshes=True)
        return {'FINISHED'}


class AURYCAT_OT_nail_locked_transform(Operator):
    bl_idname = "aurycat.nail_locked_transform"
    bl_label = "Texture-Locked Transform"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Transforms selected faces while attempting to retain the same relative texture transform (only object-space transform supported). Note that scaling operations which shear the mesh will not correctly preserve the texture"

    translate: bpy.props.FloatVectorProperty(
        name="Translate",
        default=[0,0,0],
        subtype='TRANSLATION',
        size=3,
        step=10)

    scale: bpy.props.FloatVectorProperty(
        name="Scale",
        default=[1,1,1],
        subtype='XYZ',
        size=3,
        step=10)

    rotate: bpy.props.FloatVectorProperty(
        name="Rotation",
        default=[0,0,0],
        subtype='EULER',
        size=3,
        step=10)

    modal_hack: bpy.props.BoolProperty(
        name="",
        default=False,
        options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
       return shared_poll(cls, context, only_face_select=True)

    def invoke(self, context, event):
        self.translate = [0,0,0]
        self.scale = [1,1,1]
        self.rotate = [0,0,0]
        return self.execute(context)

    def modal(self, context, event):
        return {'FINISHED'}

    def execute(self, context):
        sc = Vector(self.scale)
        if isclose(sc.x, 0): sc.x = 1
        if isclose(sc.y, 0): sc.y = 1
        if isclose(sc.z, 0): sc.z = 1
        mat = Matrix.LocRotScale(self.translate, Euler(self.rotate), sc)

        # I think ideally this would only apply to existing NailMeshes, but
        # the operator still needs to actually move the faces even if they're
        # not part of NailMeshes, and that makes things a bit trickier.
        # Soooo... just turn them into NailMeshes.
        for obj in context.objects_in_mode:
            if obj.type == 'MESH':
                with NailMesh(obj) as nm: # (Turns mesh into NailMesh if not already)
                    nm.locked_transform(mat, obj.matrix_world @ mat)

        # This action may result in NailMeshes being created; may need wakeup
        nail_wake_if_needed()

        return {'FINISHED'}


###############################################################################
#############   Interactive Texture-locked Transform Operators   ##############
###############################################################################

#------------------------------------------------------------------------------
# User-facing operators for menus

class AURYCAT_OT_nail_modal_locked_translate(Operator):
    bl_idname = "aurycat.nail_modal_locked_translate"
    bl_label = "Texture-Locked Move"
    bl_options = {"REGISTER"}
    bl_description = "Move selected faces, adjusting their NailFace shift & scale to keep the texture in the same relative position"
    @classmethod
    def poll(cls, context):
        return shared_poll(cls, context, only_face_select=True) and check_pivot_point(cls, context, True)
    def execute(self, context):
        AURYCAT_OT_nail_internal_modal_locked_transform.getop()('INVOKE_DEFAULT', mode='move')
        return {'FINISHED'}


class AURYCAT_OT_nail_modal_locked_rotate(Operator):
    bl_idname = "aurycat.nail_modal_locked_rotate"
    bl_label = "Texture-Locked Rotate"
    bl_options = {"REGISTER"}
    bl_description = "Rotate selected faces, adjusting their NailFace shift, scale, and UV axis to keep the texture in the same relative position"
    @classmethod
    def poll(cls, context):
        return shared_poll(cls, context, only_face_select=True) and check_pivot_point(cls, context, True)
    def execute(self, context):
        AURYCAT_OT_nail_internal_modal_locked_transform.getop()('INVOKE_DEFAULT', mode='rotate')
        return {'FINISHED'}


class AURYCAT_OT_nail_modal_locked_scale(Operator):
    bl_idname = "aurycat.nail_modal_locked_scale"
    bl_label = "Texture-Locked Scale"
    bl_options = {"REGISTER"}
    bl_description = "Scale selected faces, adjusting their NailFace shift, scale, and UV axis to keep the texture in the same relative position. Note that scaling operations which shear the mesh will not correctly preserve the texture"
    @classmethod
    def poll(cls, context):
        return shared_poll(cls, context, only_face_select=True) and check_pivot_point(cls, context, True)
    def execute(self, context):
        AURYCAT_OT_nail_internal_modal_locked_transform.getop()('INVOKE_DEFAULT', mode='scale')
        return {'FINISHED'}


#------------------------------------------------------------------------------
# Optional user-facing operators for keybinds
# (No 'keybind' version for locked-scale, since it's not common to want that)

def keybind_poll(context):
    if not shared_poll(None, context, only_face_select=True):
        return False
    # Check for at least one NailMesh object in edit mode
    for obj in context.objects_in_mode:
        if NailMesh.is_nail_object(obj):
            return True
    return False


# Use this for keybinds.
# It acts like regular translate when Nail Locked Translate is not applicable
class AURYCAT_OT_nail_keybind_modal_locked_translate(Operator):
    bl_idname = "aurycat.nail_keybind_modal_locked_translate"
    bl_label = "Nail Texture-Locked Move (for Keybind)"
    bl_options = {"REGISTER"}
    def execute(self, context):
        if not keybind_poll(context):
            bpy.ops.transform.translate('INVOKE_DEFAULT')
        elif not check_pivot_point(self, context, False):
            return {'CANCELLED'}
        else:
            AURYCAT_OT_nail_internal_modal_locked_transform.getop()('INVOKE_DEFAULT', mode='move')
        return {'FINISHED'}


# Use this for keybinds.
# It acts like regular rotate when Nail Locked Translate is not applicable
class AURYCAT_OT_nail_keybind_modal_locked_rotate(Operator):
    bl_idname = "aurycat.nail_keybind_modal_locked_rotate"
    bl_label = "Nail Texture-Locked Rotate (for Keybind)"
    bl_options = {"REGISTER"}
    def execute(self, context):
        if not keybind_poll(context):
            bpy.ops.transform.rotate('INVOKE_DEFAULT')
        elif not check_pivot_point(self, context, False):
            return {'CANCELLED'}
        else:
            AURYCAT_OT_nail_internal_modal_locked_transform.getop()(mode='rotate')
        return {'FINISHED'}


#------------------------------------------------------------------------------
# Internal operators, should only be invoked via the user-facing ones

# This is such a hack, I bet it won't work for long. Developed on Blender 4.1.1.
# The issue is that making an interactive move/rotate/scale as good as Blender's
# built-in one would be really hard, so I'm trying to hook into/wrap the default
# transform operators.
class AURYCAT_OT_nail_internal_modal_locked_transform(Operator):
    bl_idname = "aurycat.nail_internal_modal_locked_transform"
    bl_label = "[NAIL INTERNAL] Interactive Texture-Locked Transform"
    bl_options = {"REGISTER", "UNDO"}

    # Note Blenders move/rotate/scale operator can change which they're doing
    # live, by pressing G/R/S again. So, self.mode isn't necessarily accurate
    # at the end. 'final_mode' calculated in modal() should be accurate.
    mode: bpy.props.EnumProperty(
        name="Mode",
        items=[('move', "Move", "Move"),
               ('rotate', "Rotate", "Rotate"),
               ('scale', "Scale", "Scale")],
        default='move',
        options={'HIDDEN'})

    @classmethod
    def getop(cls):
        return attrgetter(cls.bl_idname)(bpy.ops)

    @classmethod
    def poll(cls, context):
        if not shared_poll(cls, context):
            return False
        m = context.tool_settings.mesh_select_mode[:]
        if not (not m[0] and not m[1] and m[2]):
            cls.poll_message_set("Must be run in face selection mode")
            return False
        return True

    def invoke(self, context, event):
        return self.execute(context)

    def execute(self, context):
        if AURYCAT_OT_nail_internal_modal_locked_transform.active is not None:
            # Check for ReferenceError because 'active' may be non-None but an invalid/destroyed bpy_struct
            try:
                if not AURYCAT_OT_nail_internal_modal_locked_transform.active.cancelled:
                    AURYCAT_OT_nail_internal_modal_locked_transform.active.cancelled = True
                    self.report({'ERROR'}, "Interactive texture-locked transform operator already running, cancelling both")
                    return {'CANCELLED'}
            except ReferenceError:
                pass
        AURYCAT_OT_nail_internal_modal_locked_transform.active = self

        self.cancelled = False
        self.finished = False
        self.saved_operator = context.active_operator

        if self.mode == 'move':
            bpy.ops.transform.translate('INVOKE_DEFAULT')
        elif self.mode == 'rotate':
            bpy.ops.transform.rotate('INVOKE_DEFAULT')
        elif self.mode == 'scale':
            bpy.ops.transform.resize('INVOKE_DEFAULT')
        else:
            self.report({'ERROR'}, f"Unrecognized mode {self.mode} for operator {AURYCAT_OT_nail_internal_modal_locked_transform.bl_idname}")
            return {'CANCELLED'}

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if self.cancelled:
            AURYCAT_OT_nail_internal_modal_locked_transform.active = None
            return {'CANCELLED'}

        # Cancel on the next frame
        if self.finished:
            self.cancelled = True

        if event.type in {'RIGHTMOUSE', 'ESC'}:
            # Let the event pass through to the internal/underlying transform
            # operator, so it's actually cancelled. But also mark a flag saying
            # the operation is cancelled, so that on the next modal update of
            # this operator, we'll cancel this operator too.
            self.cancelled = True
            return {'PASS_THROUGH'}

        if event.type in {'LEFTMOUSE', 'RET'}:
            # A leftclick or return/enter should mean the end of the modal operator.
            # Give it one frame for the underlying transform operator to complete,
            # and then mark us as cancelled to avoid this accidentally continuing
            # forever in unexpected circumstances.
            self.finished = True
            return {'PASS_THROUGH'}

        if context.active_operator is not self.saved_operator:
            AURYCAT_OT_nail_internal_modal_locked_transform.active = None

            # context.active_operator would be better named "last_operator", as
            # it only gets set when an operator completes. So, once the underlying
            # transform operator finishes, active_operator will change, and we'll
            # know we're done. Also conveniently, the operator properties will
            # have information about the transform, like the basis matrix and
            # and the operator's value for each axis (e.g. X move amount).
            op = context.active_operator
            if op is not None:
                if op.bl_idname == "TRANSFORM_OT_translate":
                    final_mode = "translate"
                elif op.bl_idname == "TRANSFORM_OT_rotate":
                    final_mode = "rotate"
                elif op.bl_idname == "TRANSFORM_OT_resize":
                    final_mode = "scale"
                else:
                    # Could be vertex slide, edge slide, or trackball
                    # Those can be accessed through the main translate/rotate
                    # operators by double pressing G or double pressing R
                    self.report({'ERROR'}, f"{op.name} is not suppoted for texture-locked transforms. No texture-locking was done.")
                    return {'CANCELLED'}
            else:
                self.report({'ERROR'}, "Something went wrong when determining the transform operation. No texture-locking was done.")
                return {'CANCELLED'}

            self.finalize_transform(context, op, final_mode)
            return {'FINISHED'}

        return {'PASS_THROUGH'}

    # mode is "translate", "rotate", or "scale"
    def finalize_transform(self, context, op, final_mode):
        this_class_name = type(self).__name__

        args = {
            'modal_hack': True,
            'orient_matrix': op.properties.orient_matrix.copy(),
            'orient_type': op.properties.orient_type
        }

        if final_mode == "translate":
            underlying_op = "TRANSFORM_OT_translate"
            finalize_op = AURYCAT_OT_nail_internal_end_locked_translate.getop()
            args['value'] = op.properties.value.copy()
            args['inverted_value'] = False #vec3_is_zero(Vector(op.properties.constraint_axis))
        elif final_mode == "rotate":
            underlying_op = "TRANSFORM_OT_rotate"
            finalize_op = AURYCAT_OT_nail_internal_end_locked_rotate.getop()
            args['value'] = op.properties.value
            args['orient_axis'] = op.properties.orient_axis
            # See comment on 'inverted_value' property for explanation.
            args['inverted_value'] = vec3_is_zero(Vector(op.properties.constraint_axis))
        elif final_mode == "scale":
            underlying_op = "TRANSFORM_OT_resize"
            finalize_op = AURYCAT_OT_nail_internal_end_locked_scale.getop()
            args['value'] = op.properties.value.copy()

        saved_context = {'window': context.window, 'area': context.area, 'region': context.region}
        def do_async():
            # This is hacky so do some sanity checks. Hopefully catches issues in the
            # case of a Blender change breaking this code.
            if bpy.context.active_operator.bl_idname != this_class_name:
                async_report_error(f"Something went wrong finalizing this texture-locked transform operation (unexpected active operator {bpy.context.active_operator.bl_idname})")
                return
            elif len(bpy.context.window_manager.operators) < 2:
                async_report_error(f"Something went wrong finalizing this texture-locked transform operation (expected at least two operators in the operator history)")
                return
            elif bpy.context.window_manager.operators[-1].bl_idname != this_class_name:
                # I think operators[-1] is always the same as the active_operator, but just being sure...
                async_report_error(f"Something went wrong finalizing this texture-locked transform operation (unexpected [-1] operator {bpy.context.window_manager.operators[-1].bl_idname})")
                return
            elif bpy.context.window_manager.operators[-2].bl_idname != underlying_op:
                async_report_error(f"Something went wrong finalizing this texture-locked transform operation (unexpected [-2] operator {bpy.context.window_manager.operators[-2].bl_idname} - expected {underlying_op})")
                return
            with bpy.context.temp_override(**saved_context):
                # Undo this operator
                bpy.ops.ed.undo()
                # Undo the transform operator before it
                bpy.ops.ed.undo()
                # Run the operator
                finalize_op(**args)

        # Run part2 asynchronously (i.e. after a very small delay) so that this operator can finish first
        bpy.app.timers.register(do_async, first_interval=0)


# Operator logic shared across the transform, rotate, and scale finializing operators
class SharedFinalizeInteractiveTexLockedTransform:
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def getop(cls):
        return attrgetter(cls.bl_idname)(bpy.ops)

    def orient_type_enum_items(self, _):
        name = self.orient_type
        if name in {'GLOBAL', 'LOCAL', 'NORMAL', 'GIMBAL', 'VIEW', 'CURSOR', 'PARENT'}:
            name = name.title()
        return [('x', name, "")]

    # orient_type is just set to a string value copied from whatever the underlying
    # transform operator had set. orient_type_enum is a hack to make it possible to
    # draw the orientation string in a style that looks like an enum in the operator
    # property window. The enum is always disabled, so it can't be changed. It's just
    # there to look nice and remind the user what the current orientation is.
    orient_type: bpy.props.StringProperty(
        name="Orient Type",
        default="GLOBAL",
        options={'HIDDEN'})
    orient_type_enum: bpy.props.EnumProperty(
        name="Orientation",
        items=orient_type_enum_items,
        description="This is just for reference. In order to change the orientation, change it for the whole 3D view before starting the texture-locked transform operation")

    orient_matrix: bpy.props.FloatVectorProperty(
        name="Orient",
        subtype='MATRIX',
        size=(3,3),
        options={'HIDDEN'})

    def execute(self, context):
        # Get the local transform of the op
        op_transform = self.get_op_transform_matrix()

        # And get the active pivot point, based on the current selection and pivot mode
        world_pivot_point = compute_pivot_point()

        # I think ideally this would only apply to existing NailMeshes, but
        # the operator still needs to actually move the faces even if they're
        # not part of NailMeshes, and that makes things a bit trickier.
        # Soooo... just turn them into NailMeshes.
        #
        # Note that at least for the keybinds, they only call into this
        # texture-locked transform if at least one object in edit mode is
        # a NailMesh. Otherwise they use standard Blender transform operators.
        for obj in context.objects_in_mode:
            if obj.type == 'MESH':
                # The orient matrix is just rotation, and it's always in world-space.
                # E.g. GLOBAL orientation is always identity, LOCAL is the matrix_world
                # of the object

                object_to_world = obj.matrix_world
                world_to_object = object_to_world.inverted()

                world_orient = self.orient_matrix.to_4x4()

                # Take the translation part out of orient, since that is handled by the pivot
                orient = world_to_object @ world_orient
                orient.translation.xyz = 0

                pivot_point = world_to_object @ world_pivot_point
                plus_pivot = Matrix.Translation(pivot_point)
                minus_pivot = Matrix.Translation(-pivot_point)

                # Compute the final object-space transform matrix for this transform.
                # Applying this matrix to all the selected vertices of the mesh will
                # (hopefully!) perform the exact same transformation that the underlying
                # Blender op did during the modal part of AURYCAT_OT_nail_internal_modal_locked_transform
                mat = plus_pivot @ orient @ op_transform @ orient.inverted() @ minus_pivot

                world_plus_pivot = Matrix.Translation(world_pivot_point)
                world_minus_pivot = Matrix.Translation(-world_pivot_point)
                world_mat = world_plus_pivot @ world_orient @ op_transform @ world_orient.inverted() @ world_minus_pivot

                print("mat", mat)
                print("world_mat", world_mat)

                with NailMesh(obj) as nm: # (Turns mesh into NailMesh if not already)
                    nm.locked_transform(mat, world_mat)

        # This action may result in NailMeshes being created; may need wakeup
        nail_wake_if_needed()

        if self.    modal_hack:
            self.modal_hack = False
            context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        else:
            return {'FINISHED'}

    # Somehow the (very hacky) AURYCAT_OT_nail_internal_modal_locked_transform operator that
    # runs right before this one causes Blender to not show the operator HUD/property popup
    # of this operator. Showing this operator as modal for one frame fixes it.
    modal_hack: bpy.props.BoolProperty(name="", default=False, options={'HIDDEN'})
    def modal(self, context, event):
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, 'value')
        if hasattr(self, 'orient_axis'):
            layout.prop(self, 'orient_axis')
        s = layout.split()
        s.enabled = False
        s.prop(self, 'orient_type_enum')


class AURYCAT_OT_nail_internal_end_locked_translate(SharedFinalizeInteractiveTexLockedTransform, Operator):
    bl_idname = "aurycat.nail_internal_end_locked_translate"
    bl_label = "Nail Texture-Locked Move"
    uses_pivot_point = False
    value: bpy.props.FloatVectorProperty(
        name="Move", default=[0]*3, subtype='TRANSLATION')
    inverted_value: bpy.props.BoolProperty(name="Inverted Value", options={'HIDDEN'})
    def get_op_transform_matrix(self):
        v = self.value
        # # I'm not sure why the value needs to be negated for 'VIEW' orientations
        # if self.orient_type == 'VIEW': v = -v
        if self.inverted_value:
            v = -v
        return Matrix.Translation(v)


class AURYCAT_OT_nail_internal_end_locked_rotate(SharedFinalizeInteractiveTexLockedTransform, Operator):
    bl_idname = "aurycat.nail_internal_end_locked_rotate"
    bl_label = "Nail Texture-Locked Rotate"
    uses_pivot_point = True
    orient_axis: bpy.props.EnumProperty(
        name="Axis", items=[('X',"X","X"), ('Y',"Y","Y"), ('Z',"Z","Z")])
    value: bpy.props.FloatProperty(
        name="Angle", default=0, subtype='ANGLE')
    # When rotating without any constraint axis specified (e.g. pressing
    # R without then pressing X, Y, or Z), the axis is listed as Z, but
    # it's actually the negative value compared to using VIEW orientation
    # and pressing Z when rotating. Both show up with the same 'orient_axis'
    # value being 'Z', but you can tell the difference by looking at the
    # 'constraint_axis' property, which is (0,0,0) when an axis is not
    # specificed explicitly.
    inverted_value: bpy.props.BoolProperty(name="Inverted Value", options={'HIDDEN'})
    def get_op_transform_matrix(self):
        v = self.value
        if self.inverted_value:
            v = -v
        return Matrix.Rotation(v, 4, self.orient_axis)


class AURYCAT_OT_nail_internal_end_locked_scale(SharedFinalizeInteractiveTexLockedTransform, Operator):
    bl_idname = "aurycat.nail_internal_end_locked_scale"
    bl_label = "Nail Texture-Locked Scale"
    uses_pivot_point = True
    value: bpy.props.FloatVectorProperty(
        name="Scale", default=[0]*3, subtype='XYZ')
    def get_op_transform_matrix(self):
        return Matrix.Diagonal(self.value[:] + (1,))


###############################################################################
#################################   Utils   ###################################
###############################################################################

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

def isclose(a, b):
    return math.isclose(a, b, abs_tol=1e-5)

def vec3_isclose(a, b):
    return isclose(a.x, b.x) and isclose(a.y, b.y) and isclose(a.z, b.z)

def vec3_is_zero(v):
    return isclose(v.x, 0) and isclose(v.y, 0) and isclose(v.z, 0)

# https://developer.download.nvidia.com/cg/frac.html
# The output is always in the range  0 <= out < 1 .
# Negative values are wrapped around, for example,
#    frac( 0.2) = 0.2
#    frac(-0.2) = 0.8
def frac(f):
    return f - math.floor(f)

# Like frac but returns values in the range -1 < out < 1 . In particular, if
# the result has the same sign as the input. This can result in slightly more
# human-friendly shift values.
#    frag_n1to1( 0.2) =  0.2
#    frag_n1to1(-0.2) = -0.2
#    frag_n1to1(-1.2) = -0.2
def frac_n1to1(f):
    x = frac(math.fabs(f))
    # Copies sign of f onto value of x. The '+ 0' forces a negative zero
    # to be converted to normal positive zero.
    return math.copysign(x, f) + 0

def repr_flags(f):
    return f"{f:04b}" if f is not None else "None"

def set_or_apply_selected_faces(tc, context, set=False, apply=False, only_nailmeshes=False):
    for obj in context.objects_in_mode:
        if only_nailmeshes:
            # Only apply to existing NailMeshes
            ok = NailMesh.is_nail_object(obj)
        else:
            # Apply to all meshes in editmode (turns them into NailMeshes if not already)
            ok = (obj.type == 'MESH')

        if ok:
            with NailMesh(obj) as nm:
                if set:
                    nm.set_texture_config(tc)
                if apply:
                    nm.apply_texture()

def mesh_has_any_selected_faces(me): # must be in editmode
    bm = bmesh.from_edit_mesh(me)
    try:
        if bm.faces.active is not None and bm.faces.active.select:
            return True
        for face in bm.faces:
            if face.select:
                return True
    finally:
        bm.free()
    return False

def flag_is_set(a, b):
    return (a & b) == b

def flag_set(a, b):
    return a | b

def flag_clear(a, b):
    return a & ~b

# Report error when not in an operator
def async_report_error(msg):
    def draw(self, _):
        self.layout.label(text=msg)
    bpy.context.window_manager.popup_menu(draw, title="Report: Error", icon='ERROR')

def set_handler_enabled(handler_list, func, enable):
    if enable:
        if func not in handler_list:
            handler_list.append(func)
    else:
        if func in handler_list:
            handler_list.remove(func)

def no_except(func, silent=False):
    try:
        func()
    except Exception as e:
        if not silent:
            print("Error in Nail addon:")
            print(traceback.format_exc())

# Used as operator poll to validate the context before compute_pivot_point can be called
#
# Note! For a GLOBAL transformation orientation, using Individual Origins for translations
# is fine, the axis/axes will be the same for all parts. But for other orientations, e.g.
# NORMAL, then the axes will be different for different parts. So, just to be safe, always
# prohibit Individual Origins even for translation.
def check_pivot_point(self_or_cls, context, is_poll):
    if context.scene.tool_settings.transform_pivot_point == 'INDIVIDUAL_ORIGINS':
        msg = "'Individual Origins' pivot point mode is not supported for modal Nail Texture-Locked Transforms. Please pick a different pivot mode, or disable / don't use Texture-Locked Transform."
        if (not is_poll) and NailPreferences.get('use_locked_transform_keymaps'):
            msg += "\n(Note Nail G and R keymap overrides can be disabled in Nail addon preferences!)"
        if is_poll:
            self_or_cls.poll_message_set(msg)
        else:
            self_or_cls.report({'ERROR'}, msg)
        return False
    return True

# Computes the current pivot point using the active selection and pivot mode.
# Only supports edit mode & face selection mode. Returns point in world-space.
#
# This needs to compute the pivot points in the same way that Blender does for
# its transform operations, in order for the modal Texture-Locked Transform
# operators to work correctly.
def compute_pivot_point(pivot_mode = None):
    if pivot_mode is None:
        pivot_mode = bpy.context.scene.tool_settings.transform_pivot_point

    def get_selected_verts():
        vert_coords = []
        for obj in bpy.context.objects_in_mode:
            if obj.type == 'MESH' and obj.data.is_editmode:
                bm = bmesh.from_edit_mesh(obj.data)
                if bm.select_mode != {'FACE'}:
                    raise NotImplementedError("compute_pivot_point needs face selection mode")
                object_to_world = obj.matrix_world
                for v in bm.verts:
                    if v.select:
                        world_co = object_to_world @ v.co
                        vert_coords.append(world_co.to_tuple())
                bm.free()
        return vert_coords

    if pivot_mode == 'BOUNDING_BOX_CENTER':
        vert_coords = get_selected_verts()
        if len(vert_coords) == 0:
            return Vector()
        mn = numpy.amin(vert_coords, axis=0)
        mx = numpy.amax(vert_coords, axis=0)
        bb_center = numpy.mean([mn, mx], axis=0)
        return Vector(bb_center)

    elif pivot_mode == 'CURSOR':
        return bpy.context.scene.cursor.location

    elif pivot_mode == 'INDIVIDUAL_ORIGINS':
        # Not supported because there will be multiple pivot points.
        # Also Blender groups connected faces together as "individuals"
        # and determining which sets of faces are connected sounds slow.
        # .. and also I don't feel like it.
        raise NotImplementedError("compute_pivot_point does not support Individual Origins pivot mode")

    elif pivot_mode == 'MEDIAN_POINT':
        vert_coords = get_selected_verts()
        if len(vert_coords) == 0:
            return Vector()
        # Blender calls it 'Median', but its really just the mean/average position
        # of the vertices. Also it's based on vertices even in face selection mode.
        #
        # Surprisingly numpy.mean is a tad slower than just adding together all the
        # v.co Vectors together and dividing by the total, but that can get significant
        # precision loss if there's a lot of verts. numpy.mean has better precision.
        median_point = numpy.mean(vert_coords, axis=0)
        return Vector(median_point)

    elif pivot_mode == 'ACTIVE_ELEMENT':
        obj = bpy.context.active_object
        pos = None
        if obj.type == 'MESH' and obj.data.is_editmode:
            bm = bmesh.from_edit_mesh(obj.data)
            if bm.select_mode != {'FACE'}:
                raise NotImplementedError("compute_pivot_point needs face selection mode")
            if bm.faces.active is not None:
                object_to_world = obj.matrix_world
                pos = object_to_world @ bm.faces.active.calc_center_median()
            bm.free()
        if pos is None:
            # If there is no active face, Blender uses the median point
            return compute_pivot_point(pivot_mode='MEDIAN_POINT')
        return pos

    else:
        raise NotImplementedError(f"compute_pivot_point does not support {pivot_mode} pivot mode")


###############################################################################
############################   Main Behavior   ################################
###############################################################################

# This class is not for storage of a face's texture config, it's just an
# intermediate representation between NailMesh and the operators to make
# coding the operators simpler
class TextureConfig:
    def __init__(tc):
        # The default None value means that the value is "unset", which is important
        # when taking input values from a user. Unset values are left unchanged on the
        # face being modified.
        tc.shift = None
        tc.scale = None
        tc.rotation = None
        tc.flags = 0
        tc.flags_set = 0  # flags_set is a 2nd bitmask to indicate which flags have a valid/set value
        tc.uaxis = None
        tc.vaxis = None

        # Set True when this TextureConfig represents the common values of multiple
        # faces, from NailMesh.get_texture_config. In that case, None values or
        # unset flags means different faces have different values.
        tc.multiple_faces = False

    @classmethod
    def new_unset(cls):
        return TextureConfig()

    @classmethod
    def new_default(cls):
        tc = TextureConfig()
        tc.flags = 0
        tc.flags_set = TCFLAG_ALL
        tc.shift = Vector((0,0))
        tc.scale = Vector((1,1))
        tc.rotation = 0
        return tc

    # None/unset values in the result means different faces had different values
    # Also this function has a side gig of checking if any faces are selected at all
    @classmethod
    def from_selected_faces(cls, out_any_selected=[False]):
        tc = TextureConfig.new_unset()
        for obj in bpy.context.objects_in_mode:
            if NailMesh.is_nail_object(obj):
                with NailMesh(obj, readonly=True) as nm:
                    nm.get_texture_config(tc, out_any_selected=out_any_selected)
            elif not out_any_selected[0] and obj.type == 'MESH' and obj.data.is_editmode:
                out_any_selected[0] = mesh_has_any_selected_faces(obj.data)
        return tc

    # Returns (tc, None) or (None, error_message_str)
    @classmethod
    def from_active_face(cls):
        active = bpy.context.active_object
        if active is None or active.type != 'MESH' or not active.data.is_editmode:
            return None, "No active mesh in edit mode"
        if not NailMesh.is_nail_object(active):
            return None, "Active mesh is not a Nail mesh"
        with NailMesh(active, readonly=True) as nm:
            if nm.bm.faces.active is None or not nm.bm.faces.active.select:
                return None, "No active selected face"
            tc = TextureConfig.new_unset()
            if not nm.get_texture_config_one_face(nm.bm.faces.active, tc):
                return None, "Active face is not a NailFace"
            return tc, None

    def __repr__(self):
        f  = repr_flags(self.flags)
        fs = repr_flags(self.flags_set)
        return f"<TextureConfig, f:{f}, fs:{fs}, sh:{self.shift}, sc:{self.scale}, ro:{self.rotation}, mf:{self.multiple_faces}>"


class NailMesh:
    def __init__(self, obj, readonly=False):
        if obj.type != 'MESH':
            raise RuntimeError("Invalid object type used to initialize NailMesh: " + str(obj))
        self.obj = obj
        self.readonly = readonly
        if self.readonly and not NailMesh.is_nail_mesh(obj.data):
            raise RuntimeError("Readonly NailMesh object initialized with non-nail mesh")

    def __enter__(self):
        self.matrix_world = self.obj.matrix_world
        self.rot_world = self.matrix_world.to_quaternion()
        self.wrap_uvs = NailPreferences.get('wrap_uvs')
        self.me = self.obj.data
        if self.me.is_editmode:
            self.bm = bmesh.from_edit_mesh(self.me)
        else:
            self.bm = bmesh.new()
            self.bm.from_mesh(self.me)
        if not self.readonly:
            self.init_attrs()
        self.uv_layer = self.bm.loops.layers.uv.active
        for attr_name, attr_info in ATTRS.items():
            layer = attr_info[2](self.bm)
            setattr(self, attr_info[3], layer[attr_name])
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if not self.readonly  and  exc_type is None  and  self.bm is not None  and  self.me is not None:
            if self.me.is_editmode:
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

        for attr_name, attr_info in ATTRS.items():
            layer = attr_info[2](self.bm)
            if attr_name not in layer:
                if attr_name in self.me.attributes:
                    # Not in faces.layers.float_vector, but it is in me.attributes, which
                    # implies the attribute already exists with some other domain/type
                    a = self.me.attributes[attr]
                    raise RuntimeError(f"Mesh '{self.me.name}' has an existing '{attr_name}' attribute that is the wrong domain or type. Expected {attr_info[0]}/{attr_info[1]}, got {a.domain}/{a.data_type}. Please remove or rename the existing attribute.")
                layer.new(attr_name)

    @classmethod
    def is_nail_object(cls, obj):
        if obj.type != 'MESH':
            return False
        return NailMesh.is_nail_mesh(obj.data)

    @classmethod
    def is_nail_mesh(cls, me):
        if len(me.uv_layers) == 0:
            return False
        for attr_name, attr_info in ATTRS.items():
            if ( attr_name not in me.attributes or
                 me.attributes[attr_name].domain != attr_info[0] or
                 me.attributes[attr_name].data_type != attr_info[1] ):
                return False
        return True

    def set_texture_config(self, tc, only_selected=True):
        only_selected = self.me.is_editmode and only_selected
        for face in self.bm.faces:
            if only_selected and not face.select:
                continue
            self.set_texture_config_one_face(tc, face)

    def set_texture_config_one_face(self, tc, face):
        shift_flags_attr = face[self.shift_flags_layer]
        scale_rot_attr = face[self.scale_rot_layer]

        # Any set flags from tc will overwrite existing flags
        # Any others will remain unchanged
        flags = int(shift_flags_attr.z)
        new_flags = (flags & ~tc.flags_set) | (tc.flags & tc.flags_set)
        shift_flags_attr.z = float(new_flags)

        if tc.shift is not None:
            shift_flags_attr.xy = tc.shift
        if tc.scale is not None:
            scale_rot_attr.xy = tc.scale
        if tc.rotation is not None:
            scale_rot_attr.z = tc.rotation
        if tc.uaxis is not None:
            face[self.lock_uaxis_layer] = tc.uaxis
        if tc.vaxis is not None:
            face[self.lock_vaxis_layer] = tc.vaxis

    # tc is an in-out parameter
    # Pass in a blank TextureConfig to start with, multiple objects can
    # be collected together by passing the same tc back in each time
    def get_texture_config(self, tc, only_selected=True, out_any_selected=[False]):
        only_selected = self.me.is_editmode and only_selected
        for face in self.bm.faces:
            if only_selected and not face.select:
                continue
            out_any_selected[0] = True
            if self.get_texture_config_one_face(face, tc):
                tc.multiple_faces = True

    # tc is an in-out parameter
    # Returns True if the face has Nail enabled, False otherwise (tc not modified)
    def get_texture_config_one_face(self, face, tc):
        f = self.unpack_face_data(face, calc_normal=False)
        if f is None:
            return False

        if tc.multiple_faces:
            # Find all the bits that are different between tc.flags and this face's flags
            flag_diff = tc.flags ^ f.flags
            # and mark them unset
            tc.flags &= ~flag_diff
            tc.flags_set &= ~flag_diff

            # Unset any differing values between tc and this face
            if tc.shift is not None:
                if tc.shift != f.shift:
                    tc.shift = None
            if tc.scale is not None:
                if tc.scale != f.scale:
                    tc.scale = None
            if tc.rotation is not None:
                if tc.rotation != f.rotation:
                    tc.rotation = None
            if tc.uaxis is not None:
                if tc.uaxis != f.lock_uaxis_attr:
                    tc.uaxis = None
            if tc.vaxis is not None:
                if tc.vaxis != f.lock_vaxis_attr:
                    tc.vaxis = None
        else:
            tc.flags = f.flags
            tc.flags_set = TCFLAG_ALL
            tc.shift = f.shift
            tc.scale = f.scale
            tc.rotation = f.rotation
            tc.uaxis = f.lock_uaxis_attr
            tc.vaxis = f.lock_vaxis_attr

        return True

    # Applies the existing saved shift/scale/rotation uv axis configurations
    # of selected faces. See apply_texture_one_face for more detail.
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

    # Applies a face's existing saved shift/scale/rotation/uv axis configuration
    # to the face's UVs. When auto-apply is enabled, this is called constantly,
    # for every face each time the face is modified. Also this is called when the
    # 'Reapply Texture' operator is manually invoked.
    def apply_texture_one_face(self, face):
        f = self.unpack_face_data(face)
        if f is None:
            return

        uaxis, vaxis = self.get_face_uv_axes(f)

        rotation_mat = Matrix.Rotation(f.rotation, 2)
        uv_layer = self.uv_layer

        for loop in face.loops:
            vert_coord = loop.vert.co
            if f.world_space:
                vert_coord = self.matrix_world @ vert_coord

            uv_coord = Vector((vert_coord.dot(uaxis), vert_coord.dot(vaxis)))
            uv_coord.rotate(rotation_mat)
            uv_coord.x /= f.scale.x
            uv_coord.y /= f.scale.y
            uv_coord += f.shift
            loop[uv_layer].uv = uv_coord

        if self.wrap_uvs:
            coord0 = face.loops[0][uv_layer].uv
            wrapped_coord0 = Vector((frac(coord0.x), frac(coord0.y)))
            diff_coord0 = wrapped_coord0 - coord0
            for loop in face.loops:
                loop[uv_layer].uv += diff_coord0

    # Updates the texture shift, scale, and uv axes of selected faces by the given
    # transform matrix.
    # mat is an object-space transformation matrix, and world_mat is the same
    # transformation but in world-space. Note world_mat is NOT the same as just
    # `obj.matrix_world @ mat`.
    def locked_transform(self, mat, world_mat, only_selected=True):
        only_selected = self.me.is_editmode and only_selected
        verts = set()

        # Separate out translation as required by locked_transform_one_face
        translation = mat.translation.xyz
        mat.translation.xyz = 0

        world_translation = world_mat.translation.xyz
        world_mat.translation.xyz = 0

        for face in self.bm.faces:
            if only_selected and not face.select:
                continue
            self.locked_transform_one_face(face, translation, mat, world_translation, world_mat)
            verts.update(face.verts) # Add face.verts to set

        mat.translation.xyz = translation # Put back translation
        bmesh.ops.transform(self.bm, matrix=mat, verts=list(verts))
        # self.bm.transform(mat)

    # Updates the texture shift, scale, and uv axes of the given face by the given
    # transform matrix.
    # The transformation is passed separately for object and world space
    # (see header comment for locked_transform). Additionally, the transformation
    # is passed separately for translation and rotation/scale
    def locked_transform_one_face(self, face, obj_t_vec, obj_rs_mat, world_t_vec, world_rs_mat):
        f = self.unpack_face_data(face)
        if f is None:
            return

        if f.world_space:
            rs_mat = world_rs_mat
            translation = world_t_vec
        else:
            rs_mat = obj_rs_mat
            translation = obj_t_vec

        # Translation is passed separately, so if the matrix is identity it means
        # the transformation is translation-only. In that case we don't need to
        # calculate new uv axes, since they won't change, and consequently we don't
        # need to set the ALIGN_LOCKED flag
        translation_only = rs_mat.is_identity

        # Get the existing uv axes
        # Note these vectors are always normalized
        uaxis, vaxis = self.get_face_uv_axes(f)

        if not translation_only:
            # Rotate & scale the uv axes by the matrix. The new length of uaxis and
            # vaxis gives us a multiplication factor for the scale of the texture
            uaxis = rs_mat @ uaxis
            vaxis = rs_mat @ vaxis

            # Apply scale changes
            f.scale.x *= uaxis.length # These should be divided by the original u and vaxis
            f.scale.y *= vaxis.length # lengths, but those are always 1, so it's not necessary.
            # Save updated scale
            f.scale_rot_attr.xy = f.scale

            # Scale has been applied, so the UV axes can be normalized again
            uaxis.normalize()
            vaxis.normalize()

            # Before "locking in" as ALIGNED_LOCKED, check to see if the new UV axes
            # are the same as Axis or Face alignment modes would be. That can happen
            # if a face is rotated 90 degrees, for example. If so, we can switch to
            # Axis or Face alignment instead, which makes future texture projection
            # more likely to do "what the user expects"(TM).

            # Update the cached face normal manually since the face hasn't actually
            # been transformed yet. (normal used by get_axis/face_aligned_uv_axes() )
            f.normal = rs_mat @ f.normal
            f.normal.normalize()

            flags = f.flags

            aa_uaxis, aa_vaxis = self.get_axis_aligned_uv_axes(f)
            if vec3_isclose(uaxis, aa_uaxis) and vec3_isclose(vaxis, aa_vaxis): #uaxis == aa_uaxis and vaxis == aa_vaxis:
                # Great news, the new axes are the same as axis-aligned mode! Switch to that
                flags = flag_clear(flags, TCFLAG_ALIGN_LOCKED)
                flags = flag_clear(flags, TCFLAG_ALIGN_FACE)
            else:
                fa_uaxis, fa_vaxis = self.get_face_aligned_uv_axes(f)
                if vec3_isclose(uaxis, fa_uaxis) and vec3_isclose(vaxis, fa_vaxis):
                    # Same as face-aligned mode, slightly less great but still nice
                    flags = flag_clear(flags, TCFLAG_ALIGN_LOCKED)
                    flags = flag_set(flags, TCFLAG_ALIGN_FACE)
                else:
                    # Ok need to use locked axes :(
                    flags = flag_set(flags, TCFLAG_ALIGN_LOCKED)
                    flags = flag_clear(flags, TCFLAG_ALIGN_FACE)
                    # Save updated UV axes
                    f.lock_uaxis_attr.xyz = uaxis
                    f.lock_vaxis_attr.xyz = vaxis

            # Save updated flags
            f.shift_flags_attr.z = flags

        # Compute the new texture shift value by projecting the translation
        # onto the new UV axes.
        f.shift.x -= translation.dot(uaxis) / f.scale.x
        f.shift.y -= translation.dot(vaxis) / f.scale.y
        f.shift.x = frac_n1to1(f.shift.x) # Wrap the values to the range (-1, 1)
        f.shift.y = frac_n1to1(f.shift.y)
        # Save updated shift value
        f.shift_flags_attr.xy = f.shift

    # Expects f.normal to be set (unpack_face_data calc_normal=True)
    def get_face_uv_axes(self, f):
        if f.align_locked:
            return (f.lock_uaxis_attr, f.lock_vaxis_attr)
        else:
            # In face-aligned or axis-aligned mode, the UV axes aren't saved.
            # (The saved lock_u/vaxis_attr values are ignored.) Instead, they
            # are computed based on the face's normal
            if f.align_face:
                return self.get_face_aligned_uv_axes(f)
            else:
                return self.get_axis_aligned_uv_axes(f)

    # Axis-aligned mode is the same as a standard box projection.
    # (Expects f.normal to be set -- unpack_face_data calc_normal=True)
    def get_axis_aligned_uv_axes(self, f):
        orientation = face_orientation(f.normal)
        return (RIGHT_VECTORS[orientation], UP_VECTORS[orientation])

    # Face-aligned mode is similar but takes rotation into account,
    # somewhat. It's a little weird but it's how Hammer does it!
    # (Expects f.normal to be set -- unpack_face_data calc_normal=True)
    def get_face_aligned_uv_axes(self, f):
        orientation = face_orientation(f.normal)
        vaxis = UP_VECTORS[orientation]
        uaxis = f.normal.cross(vaxis)
        uaxis.normalize()
        vaxis = uaxis.cross(f.normal)
        vaxis.normalize()
        uaxis.negate()
        return (uaxis, vaxis)

    def unpack_face_data(self, face, calc_normal=True):
        class NailFace:
            pass

        shift_flags_attr = face[self.shift_flags_layer]

        flags = int(shift_flags_attr.z)
        if not flag_is_set(flags, TCFLAG_ENABLED):
            return None

        f = NailFace()
        f.shift_flags_attr = shift_flags_attr
        f.scale_rot_attr = face[self.scale_rot_layer]
        f.lock_uaxis_attr = face[self.lock_uaxis_layer]
        f.lock_vaxis_attr = face[self.lock_vaxis_layer]

        # Initialize default (0,0,0) values to reasonable uv axes
        if f.lock_uaxis_attr == VEC3_ATTR_DEFAULT:
            f.lock_uaxis_attr.xyz = RIGHT_VECTORS[0]
        if f.lock_vaxis_attr == VEC3_ATTR_DEFAULT:
            f.lock_vaxis_attr.xyz = UP_VECTORS[0]

        # Prevent scale from being 0 on either axis (also initializes for default values)
        if isclose(f.scale_rot_attr.x, 0):
            f.scale_rot_attr.x = 1
        if isclose(f.scale_rot_attr.y, 0):
            f.scale_rot_attr.y = 1

        # Note f.shift, f.scale, and f.rotation are only copies of the data
        # saved in the mesh attributes (accessing a Vector via the .xyzw
        # swizzle components returns a copy of the Vector, although assigning
        # to them modifies the vector). To modify the actual saved values,
        # modify the f.***_attr variables directly.
        f.shift = shift_flags_attr.xy
        f.scale = f.scale_rot_attr.xy
        f.rotation = f.scale_rot_attr.z

        f.flags = flags
        f.world_space = not flag_is_set(flags, TCFLAG_OBJECT_SPACE)
        # Note, align_face and align_locked are mutually exclusive
        f.align_face = flag_is_set(flags, TCFLAG_ALIGN_FACE)
        f.align_locked = flag_is_set(flags, TCFLAG_ALIGN_LOCKED)

        # Calculate normal in advance since it's usually needed
        if calc_normal:
            normal = face.normal
            if f.world_space:
                normal = self.rot_world @ normal
            f.normal = normal

        return f


import bpy
import gpu
from gpu_extras.batch import batch_for_shader

coords = []
coords_color = []
shader = gpu.shader.from_builtin('FLAT_COLOR')

did_draw = False
vec_changed = False

# Finds one arbitrary orthogonal vector to v (must be normalized)
def find_orthogonal(v):
    r = Vector((0.5407058596611023, 0.642538845539093, 0.5429373383522034)) # random normalized
    r -= r.dot(v) * v
    return r.normalized()

def draw_vec(origin, direction, color):
    global coords, coords_color
    global did_draw
    global vec_changed
    if did_draw:
        coords = []
        coords_color = []
        did_draw = False
    vec_changed = True
    o = origin.to_3d()
    d = direction.to_3d()
    e = o+d

    dn = d.normalized()

    o1 = find_orthogonal(dn)
    o2 = dn.cross(o1)
    o3 = -o1
    o4 = -o2

    axl = max(d.length-1, d.length*0.5)
    al = (d.length-axl)*0.1
    ax = o + dn*axl
    a1 = ax + o1*al
    a2 = ax + o2*al
    a3 = ax + o3*al
    a4 = ax + o4*al

    et = e.to_tuple()
    coords.extend([
        # Main line
        o.to_tuple(), et,
        # Arrow head
        et, a1.to_tuple(),
        et, a2.to_tuple(),
        et, a3.to_tuple(),
        et, a4.to_tuple()])

    c = color
    coords_color.extend([c,c, c,c, c,c, c,c, c,c])


def debug_draw_3dview():
    global did_draw
    global coords, coords_color
    global batch
    global vec_changed
    global shader
    if len(coords) == 0:
        return
    if vec_changed:
        batch = batch_for_shader(shader, 'LINES', {"pos": coords, "color": coords_color})
        vec_changed = False
    batch.draw(shader)
    did_draw = True


if RUNNING_AS_SCRIPT:
    register()



#tan_len, bitan_len, fiiiirst, M1 = 0, 0, False, None



#    def lock(self, only_selected=True):
#        only_selected = self.me.is_editmode and only_selected
#        for face in self.bm.faces:
#            if only_selected and not face.select:
#                continue
#        self.lock_face(face)

#    def unlock(self, only_selected=True):
#        only_selected = self.me.is_editmode and only_selected
#        for face in self.bm.faces:
#            if only_selected and not face.select:
#                continue
#            self.unlock_face(face)

#    def lock_face(self, face):
#        f = self.unpack_face_data(face)
#        if f is None or f.lock_axis:
#            return

#    def unlock_face(self, face):
#        f = self.unpack_face_data(face)
#        if f is None or f.lock_axis:
#            return



#        f.transform_attr = rot_mat @ f.transform_attr
#        row = f.transform_attr.row
#        face[self.transform_r1_layer] = row[0]
#        face[self.transform_r2_layer] = row[1]
#        face[self.transform_r3_layer] = row[2]

#        self.face_offset_texture(face, f, moveDelta, uaxis, vaxis)

#        f.axis_rot_attr.xyzw = rotateAngles[:]
##        return

#        bIsLocking = True
#        bIsMoving = moveDelta.length_squared > 0.00001

#        normal = face.normal
#        normal = f.transform_attr.to_quaternion() @ normal
#        if f.world_space:
#            normal = self.rot_world @ normal

#        uaxis, vaxis = self.calc_uvaxes(Vector((0,0,1)), False)
#        tq = f.transform_attr.to_quaternion()
#        uaxis = tq @ uaxis
#        vaxis = tq @ vaxis

#        uaxis, vaxis = self.calc_uvaxes(normal, f.align_face)

##        if mat.is_identity:
#        print(f.shift_flags_attr.xy)
#            return

#        fscaleU = uaxis.length
#        fscaleV = vaxis.length
#        if isclose(fscaleU, 0): fscaleU = 1
#        if isclose(fscaleV, 0): fscaleV = 1

#        vU = mat @ uaxis
#        vV = mat @ vaxis

#        bUVAxisSameScale = isclose(fscaleU, 1) and isclose(fscaleV, 1)
#        bUVAxisPerpendicular = math.isclose(vU.dot(vV), 0, abs_tol=0.0025)

#        if bUVAxisPerpendicular:
#            uaxis = vU / fscaleU
#            vaxis = vV / fscaleV

#        if not bUVAxisSameScale: # we stretch / scale axes



#    def generate_face_axes(self, face, f):
#        global tan_len, bitan_len, fiiiirst, M1
#        center = face.calc_center_median()
#        vert0 = face.verts[0].co
#        vert1 = face.verts[1].co
#        normal = face.normal
#        if f.world_space:
#            normal = self.rot_world @ normal
#            center = self.matrix_world @ center
#            vert0 = self.matrix_world @ vert0
#            vert1 = self.matrix_world @ vert1
#        tangent = vert0 - center
#        tangent2 = vert1 - center
#        bitangent = normal.cross(tangent.normalized())
##        draw_vec(center, normal, (0,0,1))
##        draw_vec(center, tangent, (0,1,0))
##        draw_vec(center, tangent2, (0,1,0))
##        draw_vec(center, bitangent, (0,0,1))
#        bitangent = tangent2.project(bitangent)
##        draw_vec(center, bitangent, (1,0,0))

#        M2 = Matrix([(tangent.x,tangent.y,tangent.z,0),
#                     (bitangent.x,bitangent.y,bitangent.z,0),
#                     (normal.x,normal.y,normal.z,0),
#                     (0,0,0,1)])

#        if not fiiiirst:
#            tan_len = tangent.length
#            bitan_len = bitangent.length
#            fiiiirst = True
#            M1 = M2
#        else:
#            M_diff = M1.inverted() @ M2
#            M_diff.invert()
##            print(M_diff)
#            #print(tangent.length/tan_len, " --- ", bitangent.length/bitan_len)

#            up = Vector((0,0,1))
#            left = Vector((1,0,0))
#            forward = Vector((0,1,0))
#            up = M_diff @ up
#            left = M_diff @ left
#            forward = M_diff @ forward
#            origin = Vector((0,0,0))
#            draw_vec(origin, up, (0,0,1))
#            draw_vec(origin, left, (1,0,0))
#            draw_vec(origin, forward, (0,1,0))



        # get center, vert0, vert1, normal
        # tangent = vert0-center
        # tangent2 = vert1-center
        # bitangent = normal.cross(tangent.normalized())
        # bitangent scaled by the projection of tangent2 onto bitangent
        # make a transformation matrix M1
        #   n0 n1 n2 0    # or something like this idk
        #   t0 t1 t2 0
        #   b0 b1 b2 0
        #   0  0  0  1
        # repeat that process for the end, get M2
        # M2 = M? @ M1
        # M2 @ M1-1 = M? @ M1 @ M1-1
        # M2 @ M1-1 = M?

#        M2 = M? @ M1
#        M2 @ M1^-1 = M? @ M1 @ M1^-1
#        M2 @ M1^-1 = M? @ (M1 @ M1^-1)
#        M2 @ M1^-1 = M?
        

        # M? = M1^-1 @ M2
        # M? = M2^-1 @ M1