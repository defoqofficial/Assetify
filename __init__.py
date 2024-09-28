bl_info = {
    "name": "Assetify",
    "description": "Convert objects and geometry nodes into game-ready assets with baked textures for Unreal Engine.",
    "author": "Nino Defoq",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Tool Shelf > Assetify",
    "warning": "",
    "support": "Nino Defoq on socials",
    "category": "Object",
}

import bpy
import os
import blf
import bgl
import gpu
import math as m
from gpu_extras.batch import batch_for_shader
from . import addon_updater_ops

class AssetifyUpdaterPanel(bpy.types.Panel):
    """Panel to demo popup notice and ignoring functionality"""
    bl_label = "Updater Demo Panel"
    bl_idname = "OBJECT_PT_DemoUpdaterPanel_hello"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'TOOLS' if bpy.app.version < (2, 80) else 'UI'
    bl_context = "objectmode"
    bl_category = "Tools"

    def draw(self, context):
        layout = self.layout

        # Call to check for update in background.
        # Note: built-in checks ensure it runs at most once, and will run in
        # the background thread, not blocking or hanging blender.
        # Internally also checks to see if auto-check enabled and if the time
        # interval has passed.
        addon_updater_ops.check_for_update_background()

        layout.label(text="Demo Updater Addon")
        layout.label(text="")

        col = layout.column()
        col.scale_y = 0.7
        col.label(text="If an update is ready,")
        col.label(text="popup triggered by opening")
        col.label(text="this panel, plus a box ui")

        # Could also use your own custom drawing based on shared variables.
        if addon_updater_ops.updater.update_ready:
            layout.label(text="Custom update message", icon="INFO")
        layout.label(text="")

        # Call built-in function with draw code/checks.
        addon_updater_ops.update_notice_box_ui(self, context)

@addon_updater_ops.make_annotations

class AssetifyPreferences(bpy.types.AddonPreferences):
    """Demo bare-bones preferences"""
    bl_idname = __package__

    # Addon updater preferences.

    auto_check_update = bpy.props.BoolProperty(
        name="Auto-check for Update",
        description="If enabled, auto-check for updates using an interval",
        default=False)

    updater_interval_months = bpy.props.IntProperty(
        name='Months',
        description="Number of months between checking for updates",
        default=0,
        min=0)

    updater_interval_days = bpy.props.IntProperty(
        name='Days',
        description="Number of days between checking for updates",
        default=7,
        min=0,
        max=31)

    updater_interval_hours = bpy.props.IntProperty(
        name='Hours',
        description="Number of hours between checking for updates",
        default=0,
        min=0,
        max=23)

    updater_interval_minutes = bpy.props.IntProperty(
        name='Minutes',
        description="Number of minutes between checking for updates",
        default=0,
        min=0,
        max=59)

    def draw(self, context):
        layout = self.layout

        # Works best if a column, or even just self.layout.
        mainrow = layout.row()
        col = mainrow.column()

        # Updater draw function, could also pass in col as third arg.
        addon_updater_ops.update_settings_ui(self, context)

        # Alternate draw function, which is more condensed and can be
        # placed within an existing draw function. Only contains:
        #   1) check for update/update now buttons
        #   2) toggle for auto-check (interval will be equal to what is set above)
        # addon_updater_ops.update_settings_ui_condensed(self, context, col)

        # Adding another column to help show the above condensed ui as one column
        # col = mainrow.column()
        # col.scale_y = 2
        # ops = col.operator("wm.url_open","Open webpage ")
        # ops.url=addon_updater_ops.updater.website

classes = (
    AssetifyPreferences,
    AssetifyUpdaterPanel
)

# Global variables to track progress and handler
bake_progress = 0
total_bake_items = 0
draw_handler = None

# Initialize the font
font_id = 0  # default Blender font

# Global list to track the duplicated objects
duplicated_objects = []

# Global list to map original and game-ready collections
collection_mapping = []

# Global variable to track if assets are swapped
assets_swapped = False

class AssetifyBakeSettings(bpy.types.PropertyGroup):
    bake_resolution: bpy.props.EnumProperty(
        name="Bake Resolution",
        description="Resolution for the baked textures",
        items=[('512', "512x512", ""),
               ('1024', "1024x1024", ""),
               ('2048', "2048x2048", ""),
               ('4096', "4096x4096", ""),
               ('8192', "8192x8192", "")],
        default='1024'
    )
    
    bake_samples: bpy.props.IntProperty(
        name="Bake Samples",
        description="Number of samples for baking",
        default=8,
        min=1,
        max=4096
    )
    
    # New property for specifying the bake folder
    bake_folder: bpy.props.StringProperty(
        name="Bake Folder",
        description="Folder to save baked textures",
        default="//baked_textures",  # Blender default (relative to .blend file)
        subtype='DIR_PATH'  # This enables the directory selection UI
    )
    
    target_collection: bpy.props.PointerProperty(
        name="Target Collection",
        description="Select the collection to convert to game-ready",
        type=bpy.types.Collection
    )

def set_active_3d_view():
    """Ensures the 3D View is active to display the overlay."""
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            for region in area.regions:
                if region.type == 'WINDOW':
                    # Override context to ensure the active area is 3D View
                    override = {'area': area, 'region': region}
                    return override
    return None

def draw_baking_progress():
    """Draws a progress bar in the 3D view showing bake progress."""
    global bake_progress, total_bake_items, font_id

    # Set the position for the text
    x_pos, y_pos = 20, 50  # Lower-left corner of the screen

    # Initialize the font (ensure this runs)
    blf.size(font_id, 24, 72)  # Font size and DPI

    # Set the progress text
    progress_text = f"Baking Progress: {bake_progress}/{total_bake_items} Objects"
    
    # Draw the text
    blf.position(font_id, x_pos, y_pos, 0)
    blf.draw(font_id, progress_text)

    # Draw a simple progress bar
    bar_width = 200
    bar_height = 20
    padding = 10
    progress_fraction = bake_progress / total_bake_items if total_bake_items > 0 else 0

    # Draw background bar (gray)
    bgl.glEnable(bgl.GL_BLEND)
    bgl.glColor4f(0.2, 0.2, 0.2, 0.8)
    bgl.glRectf(x_pos, y_pos - bar_height - padding, x_pos + bar_width, y_pos - padding)

    # Draw progress bar (green)
    bgl.glColor4f(0.0, 0.8, 0.0, 0.8)
    bgl.glRectf(x_pos, y_pos - bar_height - padding, x_pos + bar_width * progress_fraction, y_pos - padding)

    # Restore default state
    bgl.glDisable(bgl.GL_BLEND)

def start_bake_progress_display():
    """Registers the draw handler to display baking progress."""
    global draw_handler

    # Ensure that it's registered once and only in VIEW_3D
    if draw_handler is None:
        # Register the drawing function to the SpaceView3D draw handler
        draw_handler = bpy.types.SpaceView3D.draw_handler_add(draw_baking_progress, (), 'WINDOW', 'POST_PIXEL')
        debug_print("Started bake progress display.")

def stop_bake_progress_display():
    """Unregisters the draw handler to remove the baking progress display."""
    global draw_handler

    if draw_handler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(draw_handler, 'WINDOW')
        draw_handler = None
        debug_print("Stopped bake progress display.")

def debug_print(message):
    """Helper function to print debug information."""
    print(f"[DEBUG]: {message}")

# === Original Functionality from your script ===
def ensure_cycles_render_engine():
    """Ensure the current render engine is Cycles for baking."""
    if bpy.context.scene.render.engine != 'CYCLES':
        bpy.context.scene.render.engine = 'CYCLES'
        debug_print("Switched render engine to Cycles for baking.")
        
def ensure_gpu_rendering():
    """Ensure the GPU is set for rendering if available."""
    prefs = bpy.context.preferences.addons['cycles'].preferences

    # Refresh device list
    prefs.get_devices()
    
    # Get the list of device types
    device_types = {device.type for device in prefs.devices}

    # Set the compute device type based on available devices
    if 'OPTIX' in device_types:
        prefs.compute_device_type = 'OPTIX'
    elif 'CUDA' in device_types:
        prefs.compute_device_type = 'CUDA'
    elif 'OPENCL' in device_types:
        prefs.compute_device_type = 'OPENCL'
    else:
        prefs.compute_device_type = 'NONE'

    # Enable GPU devices
    for device in prefs.devices:
        device.use = (device.type != 'CPU')

    # Ensure the scene is set to use GPU compute if available
    if prefs.compute_device_type != 'NONE':
        bpy.context.scene.cycles.device = 'GPU'
        debug_print(f"GPU rendering enabled using {prefs.compute_device_type}.")
    else:
        bpy.context.scene.cycles.device = 'CPU'
        debug_print("No GPU found, using CPU for baking.")

def ensure_optix_denoiser():
    """Ensure OptiX denoiser is enabled if available."""
    scene = bpy.context.scene

    # Ensure the Cycles engine is active
    if scene.render.engine == 'CYCLES':
        # Get Cycles preferences
        prefs = bpy.context.preferences.addons['cycles'].preferences

        # Refresh device list
        prefs.get_devices()
        
        # Check if OptiX is available among the devices
        if any(device.type == 'OPTIX' and device.use for device in prefs.devices):
            scene.cycles.use_denoising = True
            scene.cycles.denoiser = 'OPTIX'
            debug_print("OptiX denoiser enabled.")
        else:
            scene.cycles.use_denoising = True
            scene.cycles.denoiser = 'NLM'  # Use NLM denoiser as default
            debug_print("OptiX not available, using NLM denoiser.")
    else:
        debug_print("Render engine is not Cycles, cannot enable OptiX denoiser.")

def smart_uv_project(obj):
    """
    Adds a new UV map called 'GameUV' and applies Smart UV Project with specified parameters.
    """
    if obj.type != 'MESH':
        debug_print(f"{obj.name} is not a mesh, skipping UV project.")
        return

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='OBJECT')

    # Check if the 'GameUV' map already exists
    uv_map_name = "GameUV"
    uv_map = obj.data.uv_layers.get(uv_map_name)

    # If no 'GameUV' exists, create a new UV map
    if not uv_map:
        debug_print(f"Creating new UV map '{uv_map_name}' for {obj.name}")
        obj.data.uv_layers.new(name=uv_map_name)

    # Set the new UV map as active
    obj.data.uv_layers.active = obj.data.uv_layers[uv_map_name]
    debug_print(f"Set UV map '{uv_map_name}' as active for {obj.name}")

    # Switch to edit mode for UV unwrapping
    bpy.ops.object.mode_set(mode='EDIT')

    # Select all faces for UV unwrapping
    bpy.ops.mesh.select_all(action='SELECT')

    # Apply Smart UV Project with specified parameters
    bpy.ops.uv.smart_project(
        angle_limit=m.radians(66.0),
        island_margin=0.0,
        area_weight=0.0,
        correct_aspect=True,
        scale_to_bounds=False,
        margin_method='SCALED',
        rotate_method='AXIS_ALIGNED_Y'
    )
    debug_print(f"Smart UV Project applied to {obj.name}")

    # Return to object mode
    bpy.ops.object.mode_set(mode='OBJECT')

def add_realize_instances_node(geometry_node_modifier):
    """
    Adds a 'Realize Instances' node to the Geometry Node tree of the given modifier.
    Inserts the node before the Group Output node and preserves existing connections.
    """
    if not geometry_node_modifier.node_group:
        debug_print(f"Modifier {geometry_node_modifier.name} has no node group.")
        return

    node_tree = geometry_node_modifier.node_group
    debug_print(f"Accessing node tree for modifier: {geometry_node_modifier.name}")
    
    # Find the Group Output node
    output_node = next((node for node in node_tree.nodes if node.type == 'GROUP_OUTPUT'), None)
    if not output_node:
        debug_print(f"No Group Output node found in node tree: {node_tree.name}")
        return

    # Get the input socket of the Group Output node
    geometry_input_socket = output_node.inputs.get('Geometry')
    if not geometry_input_socket or not geometry_input_socket.is_linked:
        debug_print(f"Output node in {node_tree.name} has no geometry input or is not linked.")
        return

    # Find the node currently linked to the Group Output node's Geometry input
    original_link = geometry_input_socket.links[0]
    previous_node_output = original_link.from_socket
    debug_print(f"Found link from {previous_node_output.node.name} to Group Output.")

    # Remove the existing link
    node_tree.links.remove(original_link)
    debug_print("Removed the existing link to Group Output node.")

    # Create a new 'Realize Instances' node
    realize_node = node_tree.nodes.new(type="GeometryNodeRealizeInstances")
    realize_node.location = output_node.location
    realize_node.location.x -= 200  # Position it before the output node
    debug_print(f"Added 'Realize Instances' node in {node_tree.name}.")

    # Re-link the previous node to the Realize Instances node
    node_tree.links.new(previous_node_output, realize_node.inputs['Geometry'])
    debug_print(f"Connected {previous_node_output.node.name} to 'Realize Instances'.")

    # Connect the Realize Instances node to the Group Output
    node_tree.links.new(realize_node.outputs['Geometry'], geometry_input_socket)
    debug_print("Connected 'Realize Instances' node to Group Output.")

def realize_geometry_node_instances(obj):
    """
    Adds 'Realize Instances' node to each Geometry Nodes modifier on the object.
    Handles both meshes and curves.
    """
    if obj.type not in {'MESH', 'CURVE'}:
        debug_print(f"Object {obj.name} is neither a mesh nor a curve. Skipping...")
        return
    
    debug_print(f"Processing object: {obj.name}")

    # Check and print all modifiers on the object
    if not obj.modifiers:
        debug_print(f"Object {obj.name} has no modifiers.")
    else:
        for modifier in obj.modifiers:
            debug_print(f"Object {obj.name} has modifier: {modifier.name} of type {modifier.type}")
            if modifier.type == 'NODES':  # Check if it's a Geometry Nodes modifier
                debug_print(f"Found Geometry Nodes modifier: {modifier.name}")
                add_realize_instances_node(modifier)
    
    # If the object is a curve and should be converted to a mesh:
    if obj.type == 'CURVE':
        debug_print(f"Converting curve object {obj.name} to mesh.")
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.convert(target='MESH')  # Convert curve to mesh before applying modifiers
        debug_print(f"Curve object {obj.name} converted to mesh.")
    elif obj.type == 'MESH':
        debug_print(f"Converting mesh object {obj.name} to final mesh.")
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.convert(target='MESH')  # Convert mesh to a final mesh after realizing instances
        debug_print(f"Mesh object {obj.name} converted to final mesh.")

    # After converting and processing geometry nodes, apply UV unwrap
    smart_uv_project(obj)

def make_materials_unique(obj):
    """
    Make materials unique by using Blender's 'Make Single User' operation, 
    ensuring the material assignments to mesh parts are preserved.
    Afterward, rename the materials to be unique to the object.
    """
    if not obj.data.materials:
        debug_print(f"{obj.name} has no materials to make unique.")
        return

    # Make a single user copy of the object's materials to ensure they're independent of other objects
    bpy.context.view_layer.objects.active = obj  # Ensure the object is active
    bpy.ops.object.make_single_user(object=True, obdata=True, material=True, animation=False)

    # Rename materials to make them unique for this object
    for index, mat in enumerate(obj.data.materials):
        if mat:  # Ensure the material exists
            old_name = mat.name
            mat.name = f"{obj.name}_Mat_{index + 1}"  # Rename material with the object's name and index
            debug_print(f"Renamed material '{old_name}' to '{mat.name}' for {obj.name}")

    debug_print(f"All materials for {obj.name} have been made unique and renamed.")

def process_object(obj):
    """
    Processes the object: applies geometry nodes, converts curves to meshes, unwraps UVs,
    makes materials unique, and renames materials.
    """
    # Handle geometry nodes and UV project
    realize_geometry_node_instances(obj)

    # Make the materials unique for the duplicated object
    make_materials_unique(obj)

    # Rename materials to match object name
    rename_materials(obj)

def rename_materials(obj):
    """
    Renames all materials of the object to match the object's name with an increment number.
    """
    if not obj.data.materials:
        debug_print(f"{obj.name} has no materials.")
        return

    debug_print(f"Renaming materials for {obj.name}")
    for index, material in enumerate(obj.data.materials):
        new_material_name = f"{obj.name}_Mat_{index+1}"
        material.name = new_material_name
        debug_print(f"Renamed material to {new_material_name}")

def duplicate_objects_in_collection(collection, new_collection, mapping_info):
    """
    Recursively duplicates all objects inside a collection and its subcollections,
    links them to a new collection, supports mesh and curve objects, applies modifiers,
    geometry nodes, UV unwrapping, and renames materials.
    """
    global duplicated_objects
    for obj in collection.objects:
        if obj.type in {'MESH', 'CURVE'}:
            new_obj = obj.copy()
            new_obj.data = obj.data.copy()
            new_obj.name = obj.name + "_gameasset"
            new_collection.objects.link(new_obj)
            debug_print(f"Duplicated object: {new_obj.name}")
            
            # Track the duplicated objects
            duplicated_objects.append(new_obj)
            
            # Set as active object and process it (geometry, UV, materials)
            bpy.context.view_layer.objects.active = new_obj
            new_obj.select_set(True)
            process_object(new_obj)
            new_obj.select_set(False)

    # Recursively process subcollections
    for subcollection in collection.children:
        new_subcollection_name = subcollection.name + "_gameasset"
        new_subcollection = bpy.data.collections.new(new_subcollection_name)
        new_collection.children.link(new_subcollection)
        debug_print(f"Duplicated subcollection: {new_subcollection.name}")

        # Store mapping information for subcollections
        sub_mapping_info = {
            'original_collection': subcollection,
            'game_ready_collection': new_subcollection,
            'original_name': subcollection.name,
            'game_ready_name': new_subcollection_name
        }
        collection_mapping.append(sub_mapping_info)

        duplicate_objects_in_collection(subcollection, new_subcollection, sub_mapping_info)

def duplicate_mossify_collection():
    """
    Finds the user-selected collection, duplicates it along with all its objects and subcollections, and renames it.
    """
    global duplicated_objects, collection_mapping
    duplicated_objects.clear()  # Clear the list before duplicating objects
    collection_mapping.clear()  # Clear the mapping before duplicating collections

    # Get the collection selected by the user
    target_collection = bpy.context.scene.mossify_bake_settings.target_collection
    
    if not target_collection:
        debug_print("No target collection selected.")
        return {'CANCELLED'}

    # Proceed with duplicating the selected collection
    new_collection_name = "Game-Ready Asset Collection"
    new_collection = bpy.data.collections.new(new_collection_name)
    bpy.context.scene.collection.children.link(new_collection)
    debug_print(f"Created new collection: {new_collection.name}")

    # Store mapping information
    mapping_info = {
        'original_collection': target_collection,
        'game_ready_collection': new_collection,
        'original_name': target_collection.name,
        'game_ready_name': new_collection.name
    }
    collection_mapping.append(mapping_info)

    duplicate_objects_in_collection(target_collection, new_collection, mapping_info)
    return {'FINISHED'}

def swap_objects_between_collections(collection_a, collection_b, swap_state):
    """
    Swaps objects and subcollections between two collections.
    Based on the swap state, moves objects with "_gameasset" or "_originalasset" and renames subcollections.
    """
    # Move game assets (objects with "_gameasset" in their name) from collection_b to collection_a
    if swap_state:
        for obj in collection_b.objects[:]:
            if "_gameasset" in obj.name:
                collection_a.objects.link(obj)
                collection_b.objects.unlink(obj)

        # Move original assets (objects without "_gameasset" in their name) from collection_a to collection_b
        for obj in collection_a.objects[:]:
            if "_gameasset" not in obj.name:
                collection_b.objects.link(obj)
                collection_a.objects.unlink(obj)

    else:
        # Moving original assets back to the original collection
        for obj in collection_b.objects[:]:
            if "_gameasset" not in obj.name:
                collection_a.objects.link(obj)
                collection_b.objects.unlink(obj)

        # Moving game assets back to the game-ready collection
        for obj in collection_a.objects[:]:
            if "_gameasset" in obj.name:
                collection_b.objects.link(obj)
                collection_a.objects.unlink(obj)

    # Handle subcollections recursively
    subcollections_a = {col.name: col for col in collection_a.children}
    subcollections_b = {col.name: col for col in collection_b.children}

    # For each subcollection, ensure proper renaming and swapping occurs
    for subcol_name in subcollections_a.keys() | subcollections_b.keys():
        subcol_a = subcollections_a.get(subcol_name)
        subcol_b = subcollections_b.get(subcol_name)

        # Rename the subcollections based on the swap state
        if swap_state:
            # Rename "_gameasset" to "_originalasset" in subcollections
            if subcol_b and "_gameasset" in subcol_b.name:
                new_name = subcol_b.name.replace("_gameasset", "_originalasset")
                subcol_b.name = new_name
                bpy.context.view_layer.update()  # Force context update after renaming
                debug_print(f"Renamed subcollection {subcol_b.name} to {new_name}")
            # Rename "_originalasset" back to "_gameasset" in subcollections
            if subcol_a and "_originalasset" in subcol_a.name:
                new_name = subcol_a.name.replace("_originalasset", "_gameasset")
                subcol_a.name = new_name
                bpy.context.view_layer.update()  # Force context update after renaming
                debug_print(f"Renamed subcollection {subcol_a.name} to {new_name}")
        else:
            # Rename back: rename "_originalasset" to "_gameasset" and vice versa
            if subcol_b and "_originalasset" in subcol_b.name:
                new_name = subcol_b.name.replace("_originalasset", "_gameasset")
                subcol_b.name = new_name
                bpy.context.view_layer.update()  # Force context update after renaming
                debug_print(f"Renamed subcollection {subcol_b.name} to {new_name}")
            if subcol_a and "_gameasset" in subcol_a.name:
                new_name = subcol_a.name.replace("_gameasset", "_originalasset")
                subcol_a.name = new_name
                bpy.context.view_layer.update()  # Force context update after renaming
                debug_print(f"Renamed subcollection {subcol_a.name} to {new_name}")

        # Recursive call to ensure all subcollections are swapped and renamed properly
        if subcol_a and subcol_b:
            swap_objects_between_collections(subcol_a, subcol_b, swap_state)

def swap_collections():
    """
    Swaps the assets between the original and game-ready collections, and renames the collections.
    1. Renames "Game-Ready Moss Collection" to "Original Moss Collection".
    2. Renames subcollections from "_gameasset" to "_originalasset" (and back).
    Moves game assets into the original subcollections and original assets into the game-ready subcollections.
    """
    global collection_mapping, assets_swapped

    if not collection_mapping:
        debug_print("No collections have been duplicated yet.")
        return

    # Swap assets between collections based on the current swap state
    for mapping_info in collection_mapping:
        original_collection = mapping_info['original_collection']
        game_ready_collection = mapping_info['game_ready_collection']

        if not original_collection or not game_ready_collection:
            continue

        # Rename the root collection and swap the assets based on the swap state
        if not assets_swapped:
            # First swap: rename "Game-Ready Moss Collection" to "Original Moss Collection"
            if "Game-Ready Asset Collection" in game_ready_collection.name:
                game_ready_collection.name = "Original Asset Collection"
        else:
            # Swap back: rename "Original Moss Collection" to "Game-Ready Moss Collection"
            if "Original Asset Collection" in game_ready_collection.name:
                game_ready_collection.name = "Game-Ready Asset Collection"

        # Swap the objects and rename subcollections based on the current swap state
        swap_objects_between_collections(original_collection, game_ready_collection, not assets_swapped)

        debug_print(f"Swapped assets between {original_collection.name} and {game_ready_collection.name}")

    # Toggle the swapped state after each swap
    assets_swapped = not assets_swapped

    # Force a viewport update
    bpy.context.view_layer.update()

# === Baking Functionality for Unreal Engine ===

def create_bake_image(obj, map_type, resolution):
    """Create a new blank image to use for baking."""
    width = height = int(resolution)
    image_name = f"{obj.name}_{map_type}"
    image = bpy.data.images.new(image_name, width=width, height=height)
    return image

def assign_image_to_material(obj, image, map_type):
    """Assign a bake image to the active material's shader node for baking."""
    if not obj.data.materials:
        debug_print(f"{obj.name} has no materials, skipping image assignment.")
        return

    for mat in obj.data.materials:
        if not mat.use_nodes:
            continue
        node_tree = mat.node_tree
        image_node = node_tree.nodes.new('ShaderNodeTexImage')
        image_node.image = image
        image_node.name = f"Bake_{map_type}"
        node_tree.nodes.active = image_node  # Set this node as the active node for baking

def bake_and_save(obj, bake_type, map_type, resolution, save_dir):
    """Bake the specified map and save it as an image in the given directory."""
    # Ensure Cycles render engine is active
    ensure_cycles_render_engine()

    # Ensure GPU rendering if available
    ensure_gpu_rendering()

    # Ensure OptiX denoiser if available
    ensure_optix_denoiser()

    if map_type == "Metallic":
        # Create a black image for the metallic map
        width = height = int(resolution)
        image_name = f"{obj.name}_{map_type}"
        image = bpy.data.images.new(image_name, width=width, height=height, alpha=True)

        # Fill the image with black pixels
        black_color = [0.0, 0.0, 0.0, 1.0]  # RGBA
        pixels = black_color * (width * height)
        image.pixels = pixels

        # Save the image
        image.filepath_raw = os.path.join(save_dir, f"{obj.name}_{map_type}.png")
        image.file_format = 'PNG'
        image.save()

        debug_print(f"Created black image for {map_type} of {obj.name} and saved as {image.filepath_raw}")
        return
    else:
        # Proceed with the regular baking process for other map types
        # Create a new image for the bake
        image = create_bake_image(obj, map_type, resolution)
        assign_image_to_material(obj, image, map_type)

        # Store previous bake settings
        prev_bake_type = bpy.context.scene.cycles.bake_type
        prev_use_pass_direct = bpy.context.scene.render.bake.use_pass_direct
        prev_use_pass_indirect = bpy.context.scene.render.bake.use_pass_indirect
        prev_use_pass_color = bpy.context.scene.render.bake.use_pass_color

        # Set the appropriate bake type and settings
        if map_type == "BaseColor":  # Diffuse map
            bpy.context.scene.cycles.bake_type = 'DIFFUSE'
            # Disable Direct and Indirect contributions
            bpy.context.scene.render.bake.use_pass_direct = False
            bpy.context.scene.render.bake.use_pass_indirect = False
            bpy.context.scene.render.bake.use_pass_color = True
            bake_type_used = 'DIFFUSE'
        elif map_type == "Roughness":
            bpy.context.scene.cycles.bake_type = 'ROUGHNESS'
            bake_type_used = 'ROUGHNESS'
        elif map_type == "Normal":
            bpy.context.scene.cycles.bake_type = 'NORMAL'
            bake_type_used = 'NORMAL'
        else:
            # Default to the provided bake_type
            bpy.context.scene.cycles.bake_type = bake_type
            bake_type_used = bake_type

        # Perform the bake
        bpy.ops.object.bake(type=bake_type_used)

        # Restore previous bake settings
        bpy.context.scene.cycles.bake_type = prev_bake_type
        bpy.context.scene.render.bake.use_pass_direct = prev_use_pass_direct
        bpy.context.scene.render.bake.use_pass_indirect = prev_use_pass_indirect
        bpy.context.scene.render.bake.use_pass_color = prev_use_pass_color

        # Save the image
        image.filepath_raw = os.path.join(save_dir, f"{obj.name}_{map_type}.png")
        image.file_format = 'PNG'
        image.save()

        debug_print(f"Baked {map_type} for {obj.name} and saved as {image.filepath_raw}")
                    
def bake_alpha_map(obj, resolution, save_dir):
    """Bake the alpha channel as an emission map and restore the original shader setup after baking."""
    
    # Ensure Cycles render engine is active
    ensure_cycles_render_engine()

    # Create an image to bake the alpha
    image = create_bake_image(obj, "Alpha", resolution)
    
    # Store the materials and their original links to restore later
    materials_original_links = {}
    for mat in obj.data.materials:
        if not mat.use_nodes:
            continue

        node_tree = mat.node_tree

        # Find the Principled BSDF node
        principled_node = None
        for node in node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                principled_node = node
                break
        
        if not principled_node:
            debug_print(f"No Principled BSDF found in material {mat.name}, skipping alpha bake for this material.")
            continue

        # Store the original shader connections (connections to Material Output)
        material_output_node = node_tree.nodes.get('Material Output')
        original_links = []
        for link in node_tree.links:
            if link.to_node == material_output_node:
                original_links.append((link.from_socket, link.to_socket))

        # Store the original links to restore later
        materials_original_links[mat.name] = original_links

        # Create an Emission shader
        emission_node = node_tree.nodes.new(type='ShaderNodeEmission')
        emission_node.location = principled_node.location
        emission_node.location.x -= 200  # Position it before the Principled BSDF node

        alpha_input = principled_node.inputs.get('Alpha')
        if alpha_input and alpha_input.is_linked:
            # Link the Alpha input source to the Emission shader's Color input
            node_tree.links.new(alpha_input.links[0].from_socket, emission_node.inputs['Color'])
            debug_print(f"Alpha input linked for material {mat.name}, using connected alpha for baking.")
        else:
            # Set the Emission shader's Color input to white (fully opaque)
            emission_node.inputs['Color'].default_value = (1.0, 1.0, 1.0, 1.0)
            debug_print(f"Alpha input not linked for material {mat.name}, setting emission to white.")

        # Set up the material output node to use the Emission shader for baking
        node_tree.links.new(emission_node.outputs['Emission'], material_output_node.inputs['Surface'])

        # Assign the image to the active material for baking
        image_node = node_tree.nodes.new('ShaderNodeTexImage')
        image_node.image = image
        node_tree.nodes.active = image_node  # Set this node as active for baking

    # Perform the bake with emission type
    bpy.context.scene.cycles.bake_type = 'EMIT'
    bpy.ops.object.bake(type='EMIT')

    # Save the image
    image.filepath_raw = os.path.join(save_dir, f"{obj.name}_Alpha.png")
    image.file_format = 'PNG'
    image.save()
    debug_print(f"Baked Alpha for {obj.name} and saved as {image.filepath_raw}")

    # Clean up: Remove the emission nodes and restore the original material setup
    for mat in obj.data.materials:
        if not mat.use_nodes:
            continue

        node_tree = mat.node_tree

        # Remove the Emission and image nodes
        nodes_to_remove = [node for node in node_tree.nodes if node.type == 'EMISSION' or node.name.startswith("Bake_Alpha")]
        for node in nodes_to_remove:
            node_tree.nodes.remove(node)

        # Restore original shader connections
        original_links = materials_original_links.get(mat.name, [])
        # First, remove all links to the Material Output node
        material_output_node = node_tree.nodes.get('Material Output')
        for link in list(node_tree.links):
            if link.to_node == material_output_node:
                node_tree.links.remove(link)
        # Now, restore the original links
        for from_socket, to_socket in original_links:
            node_tree.links.new(from_socket, to_socket)
        debug_print(f"Restored original shader connections for material {mat.name}")

def bake_all_maps_for_object(obj, resolution, save_dir):
    """
    Bake all the necessary maps (diffuse, roughness, metallic, normal, alpha) for the object,
    then apply them to the object by setting up a Principled BSDF shader.
    Once all maps are baked and applied, simplify materials and UV maps.
    """
    global bake_progress

    # Ensure the object is active and selected
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    # Define the map types and corresponding bake types
    maps_to_bake = {
        'BaseColor': 'DIFFUSE',
        'Roughness': 'ROUGHNESS',
        'Metallic': 'COMBINED',  # We handle Metallic as a black texture
        'Normal': 'NORMAL',
    }

    # Bake each map type
    for map_type, bake_type in maps_to_bake.items():
        bake_and_save(obj, bake_type, map_type, resolution, save_dir)

    # Bake the alpha map as emission
    bake_alpha_map(obj, resolution, save_dir)

    # Apply the baked textures to the object's material
    apply_baked_textures(obj, save_dir)

    # Simplify materials and UV maps after baking and applying textures
    simplify_materials_and_uv_maps(obj)

    # Update the progress after baking this object
    bake_progress += 1

    # Force UI to refresh (to update the overlay)
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

    # Deselect the object after baking
    obj.select_set(False)

def apply_baked_textures(obj, save_dir):
    """
    Apply the baked textures to the object's material by setting up a Principled BSDF shader.
    The textures (BaseColor, Roughness, Normal, etc.) are loaded from the save directory.
    This function does not remove material slots or rename UV maps, which are handled after the application.
    """
    # Ensure the object has a material, or create a new one
    if not obj.data.materials:
        new_material = bpy.data.materials.new(name=f"{obj.name}_Material")
        obj.data.materials.append(new_material)
    else:
        new_material = obj.data.materials[0]  # Assuming we use the first material for simplicity

    # Enable 'Use Nodes' if not already enabled
    if not new_material.use_nodes:
        new_material.use_nodes = True

    # Get the node tree of the material
    node_tree = new_material.node_tree

    # Clear existing nodes
    node_tree.nodes.clear()

    # Create new Principled BSDF node
    bsdf_node = node_tree.nodes.new(type='ShaderNodeBsdfPrincipled')
    bsdf_node.location = (0, 0)

    # Create a Material Output node
    output_node = node_tree.nodes.new(type='ShaderNodeOutputMaterial')
    output_node.location = (400, 0)

    # Connect the BSDF node to the Material Output node
    node_tree.links.new(bsdf_node.outputs['BSDF'], output_node.inputs['Surface'])

    # Load and connect the baked textures
    texture_types = {
        'BaseColor': 'base_color',
        'Roughness': 'roughness',
        'Normal': 'normal',
        'Metallic': 'metallic',
        'Alpha': 'alpha'
    }

    # Load and assign each texture
    for map_type, socket_name in texture_types.items():
        texture_path = os.path.join(save_dir, f"{obj.name}_{map_type}.png")
        if os.path.exists(texture_path):
            # Create Image Texture node
            tex_image_node = node_tree.nodes.new('ShaderNodeTexImage')
            tex_image_node.image = bpy.data.images.load(texture_path)
            tex_image_node.location = (-400, len(texture_types) * -150)

            # Connect the texture to the appropriate BSDF input
            if map_type == 'BaseColor':
                node_tree.links.new(tex_image_node.outputs['Color'], bsdf_node.inputs['Base Color'])
            elif map_type == 'Roughness':
                node_tree.links.new(tex_image_node.outputs['Color'], bsdf_node.inputs['Roughness'])
            elif map_type == 'Normal':
                normal_map_node = node_tree.nodes.new('ShaderNodeNormalMap')
                normal_map_node.location = (-200, -150)
                node_tree.links.new(tex_image_node.outputs['Color'], normal_map_node.inputs['Color'])
                node_tree.links.new(normal_map_node.outputs['Normal'], bsdf_node.inputs['Normal'])
            elif map_type == 'Metallic':
                node_tree.links.new(tex_image_node.outputs['Color'], bsdf_node.inputs['Metallic'])
            elif map_type == 'Alpha':
                node_tree.links.new(tex_image_node.outputs['Color'], bsdf_node.inputs['Alpha'])

def simplify_materials_and_uv_maps(obj):
    """
    Removes all material slots except the first one.
    Removes the default UVMap.
    Renames 'GameUV' to 'UVMap'.
    """
    # Remove all material slots except the first one
    if len(obj.data.materials) > 1:
        for i in range(len(obj.data.materials) - 1, 0, -1):  # Start from the end and remove backwards
            obj.data.materials.pop(index=i)
        debug_print(f"Removed all material slots except the first one for {obj.name}")
    else:
        debug_print(f"No extra material slots to remove for {obj.name}")
    
    # Remove the default UVMap
    default_uv_map = obj.data.uv_layers.get("UVMap")
    if default_uv_map:
        obj.data.uv_layers.remove(default_uv_map)
        debug_print(f"Removed default UVMap from {obj.name}")
    else:
        debug_print(f"No default UVMap found in {obj.name}")
    
    # Rename 'GameUV' to 'UVMap'
    game_uv = obj.data.uv_layers.get("GameUV")
    if game_uv:
        game_uv.name = "UVMap"
        obj.data.uv_layers.active = game_uv
        debug_print(f"Renamed 'GameUV' to 'UVMap' for {obj.name}")
    else:
        debug_print(f"'GameUV' not found in {obj.name}")

# === Operator Classes and Panel ===

class OBJECT_OT_swap_collections(bpy.types.Operator):
    """Swap between Original and Game-Ready Moss Collections"""
    bl_idname = "object.swap_collections"
    bl_label = "Swap Collections"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Call the function to swap collections
        swap_collections()
        return {'FINISHED'}

class OBJECT_OT_bake_textures_for_unreal(bpy.types.Operator):
    """Bake Textures for Unreal Engine and save them to disk"""
    bl_idname = "object.bake_textures_for_unreal"
    bl_label = "Bake Textures for Unreal Engine"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        global bake_progress, total_bake_items

        # Ensure the 3D View is active for the overlay
        set_active_3d_view()

        # Get bake settings from the context
        bake_settings = context.scene.mossify_bake_settings
        bake_resolution = bake_settings.bake_resolution
        bake_samples = bake_settings.bake_samples

        # Apply the bake sample settings
        bpy.context.scene.cycles.samples = bake_samples

        if not duplicated_objects:
            self.report({'ERROR'}, "No objects to bake. Ensure objects are created first.")
            return {'CANCELLED'}

        # Set up the progress display
        bake_progress = 0
        total_bake_items = len([obj for obj in duplicated_objects if obj.type == 'MESH'])
        start_bake_progress_display()

        # Define the directory where you want to save the baked textures
        save_dir = bpy.path.abspath("//baked_textures")
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)

        # Bake all the necessary maps for each duplicated object
        for obj in duplicated_objects:
            if obj.type == 'MESH':
                self.report({'INFO'}, f"Baking textures for {obj.name}")
                try:
                    bake_all_maps_for_object(obj, bake_resolution, save_dir)
                    self.report({'INFO'}, f"Textures baked and saved for {obj.name}")
                except Exception as e:
                    self.report({'ERROR'}, f"Failed to bake textures for {obj.name}: {str(e)}")
                    stop_bake_progress_display()
                    return {'CANCELLED'}
            else:
                self.report({'WARNING'}, f"Skipping non-mesh object: {obj.name}")

        # Stop the progress display once baking is done
        stop_bake_progress_display()

        return {'FINISHED'}

class OBJECT_OT_convert_to_game_ready(bpy.types.Operator):
    """Convert the user-selected collection to Game-Ready format with unique objects"""
    bl_idname = "object.convert_to_game_ready"
    bl_label = "Convert Selected to Game-Ready"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        result = duplicate_mossify_collection()
        if result == {'FINISHED'}:
            self.report({'INFO'}, "Selected collection duplicated and objects made game-ready!")
        else:
            self.report({'WARNING'}, "No suitable collection found!")
        return result

class ASSETIFY_PT_tools_panel(bpy.types.Panel):
    """Creates a Panel in the 3D Viewport Tool Shelf"""
    bl_label = "Assetify"
    bl_idname = "ASSETIFY_PT_tools_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Assetify"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # Check for updates in the background
        addon_updater_ops.check_for_update_background()

        # First thing: Collection selection
        layout.label(text="Asset Collection:")
        layout.prop_search(context.scene.mossify_bake_settings, "target_collection",
                           bpy.data, "collections", text="Select Collection")

        # Operator button to execute the 'convert_to_game_ready' operation
        layout.operator("object.convert_to_game_ready", text="Convert to Game Assets")

        # Bake settings (resolution and samples)
        layout.label(text="Bake Settings:")
        # Add a new field for specifying the bake folder
        layout.prop(context.scene.mossify_bake_settings, "bake_folder")
        layout.prop(context.scene.mossify_bake_settings, "bake_resolution")
        layout.prop(context.scene.mossify_bake_settings, "bake_samples")

        # Operator button to execute the 'bake_textures_for_unreal' operation
        layout.operator("object.bake_textures_for_unreal", text="Bake Materials for Unreal Engine")

        # Operator button to swap collections
        layout.operator("object.swap_collections", text="Swap Original & Game Assets")
        
        addon_updater_ops.update_notice_box_ui(self, context)

def register():
    """Registers the operators and the panel."""
    addon_updater_ops.register(bl_info)
    for cls in classes:
        addon_updater_ops.make_annotations(cls)  # Avoid blender 2.8 warnings.
        bpy.utils.register_class(cls)
    bpy.utils.register_class(OBJECT_OT_convert_to_game_ready)
    bpy.utils.register_class(OBJECT_OT_bake_textures_for_unreal)
    bpy.utils.register_class(OBJECT_OT_swap_collections)
    bpy.utils.register_class(ASSETIFY_PT_tools_panel)
    bpy.utils.register_class(AssetifyBakeSettings)

    bpy.types.Scene.mossify_bake_settings = bpy.props.PointerProperty(type=AssetifyBakeSettings)

def unregister():
    """Unregisters the operators and the panel."""
    addon_updater_ops.unregister()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    bpy.utils.unregister_class(OBJECT_OT_convert_to_game_ready)
    bpy.utils.unregister_class(OBJECT_OT_bake_textures_for_unreal)
    bpy.utils.unregister_class(OBJECT_OT_swap_collections)
    bpy.utils.unregister_class(ASSETIFY_PT_tools_panel)
    bpy.utils.unregister_class(AssetifyBakeSettings)

    del bpy.types.Scene.assetify_bake_settings

if __name__ == "__main__":
    register()