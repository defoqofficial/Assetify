bl_info = {
    "name": "Assetify",
    "description": "Convert objects and geometry nodes into game-ready assets with baked textures for Unreal Engine.",
    "author": "Nino Defoq",
    "version": (2, 0, 1),
    "blender": (4, 3, 0),
    "location": "3D View > Tool Shelf > Assetify",
    "warning": "",
    "support": "Nino Defoq on socials",
    "category": "Object",
}

import importlib
import socket
import sys
import traceback
import bpy
import os
import blf
import gpu
import shutil
import json
import struct
import re
import subprocess
import math as m
from gpu_extras.batch import batch_for_shader
from . import addon_updater_ops
from . import anim_geonode
from . import animation_processor
from . import anim_cloth
import bpy.utils.previews
import numpy
import uuid
from bpy.props import CollectionProperty
import bmesh
import math
from mathutils import Vector
import addon_utils
import glob

# Define a global dictionary to store the custom icon previews
custom_icons = None

# Global list to track the duplicated objects
duplicated_objects = []

# Global list to map original and game-ready collections
collection_mapping = []

# Global variable to track if assets are swapped
assets_swapped = False

def debug_all_virtual_links(assetify_settings):
    """
    Prints the virtual links of all baked assets for debugging.
    """
    print("=== Virtual Links Debug Info ===")
    for baked_asset in assetify_settings.baked_assets:
        obj = bpy.data.objects.get(baked_asset.name)
        if obj:
            debug_virtual_links(obj)
        else:
            print(f"Asset {baked_asset.name} not found.")
    print("===============================")
    
class ASSETIFY_OT_debug_virtual_links(bpy.types.Operator):
    """Debug the virtual links between assets and their root collections"""
    bl_idname = "assetify.debug_virtual_links"
    bl_label = "Debug Virtual Links"

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        debug_all_virtual_links(assetify_settings)
        return {'FINISHED'}

def link_asset_to_root(obj, root_collection_name, game_ready_collection_name):
    """
    Links the asset to its root collection virtually using custom properties
    and links it physically only to the _GameReady collection.
    """
    # Store the root and game-ready collection names in the object's custom properties
    obj["root_collection"] = root_collection_name
    obj["game_ready_collection"] = game_ready_collection_name

    # Debugging information
    print(f"Linked {obj.name} virtually to root collection '{root_collection_name}' and physically to '{game_ready_collection_name}'")

def debug_virtual_links(obj):
    """
    Debug the virtual links by printing the associated root and game-ready collection.
    """
    root_collection = obj.get("root_collection", "Not Linked")
    game_ready_collection = obj.get("game_ready_collection", "Not Linked")
    print(f"Asset '{obj.name}' is virtually linked to root collection: '{root_collection}' and physically linked to game-ready collection: '{game_ready_collection}'")

def update_collection_statuses(assetify_settings):
    """
    Updates the is_baked and is_fbx_exported status for all baked collections and their assets.
    """
    for baked_collection in assetify_settings.baked_collections:
        all_baked = True
        all_exported = True
        
        for asset in baked_collection.assets:
            obj = bpy.data.objects.get(asset.name)
            
            if obj is not None:
                asset.is_baked = check_if_baked(obj)
                asset.is_fbx_exported = check_if_exported(obj)
            else:
                # If the object does not exist, consider it not baked/exported
                asset.is_baked = False
                asset.is_fbx_exported = False
            
            # If any asset is not baked or exported, set the respective flag to False
            if not asset.is_baked:
                all_baked = False
            if not asset.is_fbx_exported:
                all_exported = False

        # Set collection-level status based on all assets
        baked_collection.is_baked = all_baked
        baked_collection.is_fbx_exported = all_exported
        
        print(f"[DEBUG] Collection '{baked_collection.name}' status - Baked: {baked_collection.is_baked}, Exported: {baked_collection.is_fbx_exported}")

    # Force UI refresh to reflect updated statuses
    for area in bpy.context.screen.areas:
        if area.type in {'VIEW_3D', 'PROPERTIES'}:
            area.tag_redraw()

def check_collection_exported(collection):
    """Check if all assets in a collection have been exported as FBX files."""
    # Retrieve export path directory
    export_fbx_path = bpy.path.abspath(bpy.context.scene.assetify_bake_settings.export_fbx_path)
    export_fbx_dir = os.path.dirname(export_fbx_path)

    # Loop through all assets in the collection
    for asset in collection.assets:
        # Get the object and check if it has been exported
        obj = bpy.data.objects.get(asset.name)
        if not obj or not check_if_exported(obj):
            print(f"[DEBUG] Asset '{asset.name}' in collection '{collection.name}' is not exported.")
            return False  # Return False if any asset is not exported
    
    # If all assets are exported, return True
    print(f"[DEBUG] All assets in collection '{collection.name}' are exported.")
    return True

class ASSETIFY_OT_delete_bake_and_fbx_files(bpy.types.Operator):
    """Delete bake files, FBX files, and remove the collection or asset hierarchy for the selected baked asset or collection"""
    bl_idname = "assetify.delete_bake_and_fbx_files"
    bl_label = "Delete Bake, FBX, and Collection/Asset Hierarchy"

    asset_name: bpy.props.StringProperty()

    def execute(self, context):
        bake_folder = bpy.path.abspath(context.scene.assetify_bake_settings.bake_folder)
        textures_folder = os.path.join(bake_folder, "textures")
        assetify_settings = context.scene.assetify_bake_settings

        # Check if it's a collection or an asset
        if self.asset_name.endswith("_GameReady"):
            # Handle baked collections
            collection_name = self.asset_name
            game_ready_collection = bpy.data.collections.get(collection_name)

            if game_ready_collection:
                # Unlink the collection from all scenes based on the name comparison
                for scene in bpy.data.scenes:
                    if game_ready_collection.name in [col.name for col in scene.collection.children]:
                        scene.collection.children.unlink(game_ready_collection)
                        self.report({'INFO'}, f"Unlinked collection: {collection_name} from scene: {scene.name}")

                # Remove the entire collection hierarchy
                bpy.data.collections.remove(game_ready_collection)
                self.report({'INFO'}, f"Deleted entire collection hierarchy: {collection_name}")

                # Remove from the baked collections list
                for index, collection in enumerate(assetify_settings.baked_collections):
                    if collection.name == self.asset_name:
                        assetify_settings.baked_collections.remove(index)
                        break
            else:
                self.report({'WARNING'}, f"Game-ready collection not found: {collection_name}")

        else:
            # Handle baked assets
            files_to_delete = [
                os.path.join(textures_folder, f"{self.asset_name}_BaseColor.png"),
                os.path.join(textures_folder, f"{self.asset_name}_Normal.png"),
                os.path.join(textures_folder, f"{self.asset_name}_Roughness.png"),
                os.path.join(textures_folder, f"{self.asset_name}_Metallic.png"),
                os.path.join(textures_folder, f"{self.asset_name}_Alpha.png"),
                os.path.join(bake_folder, f"{self.asset_name}.fbx")
            ]

            # Attempt to delete each file
            for file_path in files_to_delete:
                try:
                    if os.path.exists(file_path):
                        os.remove(file_path)
                        self.report({'INFO'}, f"Deleted file: {file_path}")
                    else:
                        self.report({'WARNING'}, f"File not found: {file_path}")
                except Exception as e:
                    self.report({'ERROR'}, f"Failed to delete {file_path}: {e}")

            # Find the associated collection and remove it
            original_object = bpy.data.objects.get(self.asset_name)
            if original_object and original_object.users_collection:
                original_collection = original_object.users_collection[0]
                collection_name = original_collection.name

                if not collection_name.endswith("_GameReady"):
                    collection_name += "_GameReady"

                game_ready_collection = bpy.data.collections.get(collection_name)
                if game_ready_collection:
                    for scene in bpy.data.scenes:
                        if game_ready_collection.name in [col.name for col in scene.collection.children]:
                            scene.collection.children.unlink(game_ready_collection)
                            self.report({'INFO'}, f"Unlinked collection: {collection_name} from scene: {scene.name}")

                    bpy.data.collections.remove(game_ready_collection)
                    self.report({'INFO'}, f"Deleted entire collection hierarchy: {collection_name}")
                else:
                    self.report({'WARNING'}, f"Game-ready collection not found: {collection_name}")
            else:
                self.report({'WARNING'}, f"Original object or its collection not found for: {self.asset_name}")

            # Remove the asset from the baked_assets list
            asset_index = assetify_settings.baked_assets.find(self.asset_name)
            if asset_index != -1:
                assetify_settings.baked_assets.remove(asset_index)
                print(f"[Assetify] Deleted asset: {self.asset_name} from baked_assets list")

        # Call purge function to remove all unused data blocks
        bpy.ops.outliner.orphans_purge(do_recursive=True)

        # Refresh the UI
        if assetify_settings.asset_mode:
            # Repopulate the baked assets list
            context.scene.assetify_bake_settings.baked_assets.clear()
            populate_baked_assets_from_scene(assetify_settings)
        else:
            # Repopulate the baked collections list
            context.scene.assetify_bake_settings.baked_collections.clear()
            populate_baked_collections_from_scene(assetify_settings)

        # Force UI redraw
        context.area.tag_redraw()

        return {'FINISHED'}

    def invoke(self, context, event):
        # Show a confirmation dialog
        return context.window_manager.invoke_confirm(self, event)

def check_if_asset_is_exported(asset):
    """Check if the asset has been exported as FBX."""
    return asset.is_fbx_exported

class ASSETIFY_OT_show_unexported_assets(bpy.types.Operator):
    """Show a list of selected assets that have not been exported yet"""
    bl_idname = "assetify.show_unexported_assets"
    bl_label = "Show Unexported Assets"
    bl_description = "Display a list of assets that have not been exported as FBX yet"

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        unexported_asset_names = []

        if assetify_settings.asset_mode == 'ASSET':
            # Asset Mode: Check selected assets
            assets_selected = [
                asset for asset in assetify_settings.baked_assets
                if asset.include_in_send
            ]
            for asset in assets_selected:
                if not asset.is_fbx_exported:
                    unexported_asset_names.append(asset.name)
        else:
            # Collection Mode: Check assets in the selected collection
            collection = assetify_settings.fbx_export_collection
            if collection:
                objects_in_collection = [
                    obj for obj in collection.all_objects
                    if obj.type == 'MESH' and "_gameasset" in obj.name
                ]
                for obj in objects_in_collection:
                    # Assuming 'is_fbx_exported' is a property on the asset linked to the object
                    asset = assetify_settings.get_asset_by_name(obj.name)
                    if asset and not asset.is_fbx_exported:
                        unexported_asset_names.append(obj.name)
            else:
                self.report({'WARNING'}, "No collection selected for import.")
                return {'CANCELLED'}

        if unexported_asset_names:
            message = "Unexported Assets:\n" + "\n".join(unexported_asset_names)
            self.show_message_box(message, "Unexported Assets")
        else:
            message = "All selected assets have been exported."
            self.show_message_box(message, "Unexported Assets")

        return {'FINISHED'}

    def show_message_box(self, message="", title="Message", icon='INFO'):
        def draw(self, context):
            for line in message.split('\n'):
                self.layout.label(text=line)

        bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)

class ASSETIFY_OT_show_unbaked_assets(bpy.types.Operator):
    """Show a list of selected assets or root collections that are not baked yet"""
    bl_idname = "assetify.show_unbaked_assets"
    bl_label = "Show Unbaked Assets"

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings

        if assetify_settings.asset_mode == 'ASSET':
            # Asset Mode: Check selected assets
            assets_selected = [
                asset for asset in assetify_settings.baked_assets
                if asset.include_in_send
            ]
            if not assets_selected:
                self.report({'INFO'}, "No assets selected.")
                self.show_message_box("No assets selected.", "Export Info")
                return {'CANCELLED'}

            unbaked_asset_names = [asset.name for asset in assets_selected if not asset.is_baked]

            # Show unbaked assets or inform that all are baked
            if unbaked_asset_names:
                message = "Not all assets are baked:\n" + "\n".join(unbaked_asset_names)
                self.report({'INFO'}, message)
                self.show_message_box(message, "Export Info")
            else:
                self.report({'INFO'}, "All selected assets are baked.")
                self.show_message_box("All selected assets are baked.", "Export Info")

        elif assetify_settings.asset_mode == 'COLLECTION':
            # Collection Mode: Check selected root collections
            collections_selected = [
                collection for collection in assetify_settings.baked_collections
                if collection.include_in_send and get_collection_level(collection.name) == 0  # Only root collections
            ]
            if not collections_selected:
                self.report({'INFO'}, "No collections selected to be exported.")
                self.show_message_box("No collections selected to be exported.", "Export Info")
                return {'CANCELLED'}

            # Check each selected root collection
            unbaked_collections = [
                collection.name for collection in collections_selected
                if not all(asset.is_baked for asset in collection.assets)
            ]

            # Show message
            if unbaked_collections:
                message = "\n".join(
                    f"Not all assets of collection '{collection_name}' are baked."
                    for collection_name in unbaked_collections
                )
                self.report({'INFO'}, message)
                self.show_message_box(message, "Export Info")
            else:
                self.report({'INFO'}, "All selected collections are baked.")
                self.show_message_box("All selected collections are baked.", "Export Info")

        return {'FINISHED'}

    def show_message_box(self, message="", title="Message", icon='INFO'):
        def draw(self, context):
            for line in message.split('\n'):
                self.layout.label(text=line)

        bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)
    
def delete_blender_object(obj):
    # Unlink the object from all collections
    for collection in obj.users_collection:
        collection.objects.unlink(obj)
    # Remove the object
    bpy.data.objects.remove(obj, do_unlink=True)
    
def purge_unused_data():
    # Remove unused meshes
    for mesh in bpy.data.meshes:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)

    # Remove unused materials
    for material in bpy.data.materials:
        if material.users == 0:
            bpy.data.materials.remove(material)

    # Remove unused images
    for image in bpy.data.images:
        if image.users == 0:
            bpy.data.images.remove(image)

    # Remove unused textures
    for texture in bpy.data.textures:
        if texture.users == 0:
            bpy.data.textures.remove(texture)
    
def delete_asset_files(asset_name, context):
    assetify_settings = context.scene.assetify_bake_settings
    bake_folder = bpy.path.abspath(assetify_settings.bake_folder)
    textures_folder = os.path.join(bake_folder, "textures")
    export_fbx_dir = os.path.dirname(bpy.path.abspath(assetify_settings.export_fbx_path))

    # List of texture types to delete
    texture_types = ["BaseColor", "Normal", "Roughness", "Metallic", "Alpha", "MetallicSmoothness"]

    # Supported texture extensions
    texture_extensions = [".png", ".jpg", ".jpeg", ".tga", ".bmp", ".tif", ".tiff"]

    # Delete texture files
    for tex_type in texture_types:
        for ext in texture_extensions:
            texture_file = f"{asset_name}_{tex_type}{ext}"
            texture_path = os.path.join(textures_folder, texture_file)
            if os.path.exists(texture_path):
                try:
                    os.remove(texture_path)
                    print(f"[Assetify] Deleted texture file: {texture_path}")
                except Exception as e:
                    print(f"[Assetify] Error deleting texture file '{texture_path}': {e}")
            else:
                print(f"[Assetify] Texture file not found (skipped): {texture_path}")

    # Delete FBX file
    fbx_file_name = f"{asset_name}.fbx"
    fbx_path = os.path.join(export_fbx_dir, fbx_file_name)
    if os.path.exists(fbx_path):
        try:
            os.remove(fbx_path)
            print(f"[Assetify] Deleted FBX file: {fbx_path}")
        except Exception as e:
            print(f"[Assetify] Error deleting FBX file '{fbx_path}': {e}")
    else:
        print(f"[Assetify] FBX file not found (skipped): {fbx_path}")

def update_baked_collections_status(context):
    assetify_settings = context.scene.assetify_bake_settings
    print("Updating baked status of collections...")
    for baked_collection in assetify_settings.baked_collections:
        all_assets_baked = True
        print(f"Checking collection: {baked_collection.name}")
        if not baked_collection.assets:
            print(f"Collection {baked_collection.name} has no assets.")
            all_assets_baked = False
        for asset in baked_collection.assets:
            print(f"Asset {asset.name} is_baked: {asset.is_baked}")
            if not asset.is_baked:
                all_assets_baked = False
                break
        baked_collection.is_baked = all_assets_baked
        print(f"Collection {baked_collection.name} is_baked set to: {baked_collection.is_baked}")

def check_if_asset_is_baked(obj):
    bake_folder = bpy.path.abspath(bpy.context.scene.assetify_bake_settings.bake_folder)
    textures_folder = os.path.join(bake_folder, "textures")

    # Check for baked texture files
    baked_files = [
        os.path.join(textures_folder, f"{obj.name}_BaseColor.png"),
        os.path.join(textures_folder, f"{obj.name}_Normal.png"),
        os.path.join(textures_folder, f"{obj.name}_Roughness.png"),
        os.path.join(textures_folder, f"{obj.name}_Metallic.png"),
        os.path.join(textures_folder, f"{obj.name}_Alpha.png"),
    ]

    # If any of the texture files exist, consider the asset as baked
    for file in baked_files:
        if os.path.exists(file):
            return True
    return False

def check_if_asset_is_fbx_exported(obj):
    """Check if the FBX file for the asset exists in the export folder with '_fbx' suffix."""
    export_fbx_path = bpy.path.abspath(bpy.context.scene.assetify_bake_settings.export_fbx_path)
    export_fbx_dir = os.path.dirname(export_fbx_path)
    fbx_file_name = f"{sanitize_name(obj.name)}_fbx.fbx"
    fbx_file_path = os.path.join(export_fbx_dir, fbx_file_name)
    return os.path.exists(fbx_file_path)

def debug_collection(collection, indent=0):
    """
    Debugging function to print out details of a collection and its objects/children.
    
    Args:
        collection (Collection): The Blender collection to debug.
        indent (int): Indentation level for visual clarity (used for nested collections).
    """
    indent_str = "    " * indent
    print(f"{indent_str}[Assetify] Debugging collection '{collection.name}':")
    
    # Print objects in the collection
    if collection.objects:
        print(f"{indent_str}  Objects in collection:")
        for obj in collection.objects:
            print(f"{indent_str}    - {obj.name} (Type: {obj.type})")
    else:
        print(f"{indent_str}  No objects in this collection.")

    # Print child collections (if any)
    if collection.children:
        print(f"{indent_str}  Child collections:")
        for subcol in collection.children:
            print(f"{indent_str}    - {subcol.name}")
            # Recursively debug subcollections
            debug_collection(subcol, indent + 1)
    else:
        print(f"{indent_str}  No child collections.")
    print()


def check_if_collection_is_baked(collection):
    """
    Check if all assets in the collection and its subcollections are baked.
    
    Args:
        collection (Collection): The Blender collection to check.
    
    Returns:
        bool: True if all game assets in the collection and subcollections are baked, False otherwise.
    """
    # Debugging the collection first
    debug_collection(collection)

    # Check all objects in the collection
    for obj in collection.objects:
        if obj.get('is_game_asset'):
            # If any game asset is not baked, return False
            if not check_if_baked(obj):
                print(f"[Assetify] Asset '{obj.name}' in collection '{collection.name}' is not baked.")
                return False

    # Recursively check all subcollections
    for subcollection in collection.children:
        if not check_if_collection_is_baked(subcollection):
            return False

    print(f"[Assetify] All assets in collection '{collection.name}' and its subcollections are baked.")
    return True


def check_if_collection_is_fbx_exported(collection):
    """
    Check if all game assets in the collection and its subcollections have been exported to FBX.
    
    Args:
        collection (Collection): The Blender collection to check.
    
    Returns:
        bool: True if all game assets in the collection and subcollections have been exported as FBX, False otherwise.
    """
    # Ensure the main directory for FBX exports is set
    export_fbx_path = bpy.path.abspath(bpy.context.scene.assetify_bake_settings.export_fbx_path)
    export_fbx_dir = os.path.dirname(export_fbx_path)

    # Check export status for all objects directly within the collection
    for obj in collection.objects:
        # Only check objects that are marked as game assets
        if "_gameasset" in obj.name:
            if not check_if_fbx_file_exists(obj.name.replace("_gameasset", ""), export_fbx_dir):
                print(f"[Assetify] Asset '{obj.name}' in collection '{collection.name}' has not been exported as FBX.")
                return False

    # Recursively check subcollections
    for subcollection in collection.children:
        if not check_if_collection_is_fbx_exported(subcollection):
            return False

    print(f"[Assetify] All assets in collection '{collection.name}' and its subcollections have been exported as FBX.")
    return True

def mark_collection_as_baked(collection):
    """Mark the collection as baked."""
    collection["baked_status"] = True

def mark_collection_as_fbx_exported(collection):
    """Mark the collection as exported to FBX."""
    collection["fbx_export_status"] = True

def populate_baked_assets_from_scene(assetify_settings):
    """
    Populate the baked assets list by checking both the original and game-ready collections
    for any objects that have '_gameasset' in their name or are valid joined assets.
    """
    print("[DEBUG] Populating baked assets from scene.")

    # Clear the current baked assets list
    assetify_settings.baked_assets.clear()

    # Iterate through all collections and subcollections in the scene
    for collection in bpy.data.collections:
        for obj in collection.objects:
            # Include assets with '_gameasset' in their name or linked to baked collections
            if (
                "_gameasset" in obj.name or 
                obj.name in [
                    asset.name for col in assetify_settings.baked_collections for asset in col.assets
                ]
            ):
                # Avoid duplicate entries in the baked assets list
                if obj.name in [asset.name for asset in assetify_settings.baked_assets]:
                    print(f"[DEBUG] Skipping duplicate entry for '{obj.name}'.")
                    continue

                # Create or update the baked asset entry
                baked_asset = assetify_settings.baked_assets.add()
                baked_asset.name = obj.name
                baked_asset.is_game_asset = "_gameasset" in obj.name  # Determine game asset status
                baked_asset.is_baked = check_if_baked(obj) or any(
                    obj.name == asset.name and asset.is_baked
                    for col in assetify_settings.baked_collections
                    for asset in col.assets
                )
                baked_asset.is_fbx_exported = check_if_exported(obj)

                # Maintain include_in_send status if the asset is part of baked collections
                baked_asset.include_in_send = any(
                    obj.name == asset.name and asset.include_in_send
                    for col in assetify_settings.baked_collections
                    for asset in col.assets
                )

                print(f"[DEBUG] Added '{obj.name}' to baked assets list with baked status '{baked_asset.is_baked}'.")

    # Debug final list
    print(f"[DEBUG] Final baked assets: {[asset.name for asset in assetify_settings.baked_assets]}")

    # Redraw the UI
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()

def populate_baked_collections_from_scene(assetify_settings):
    """
    Populate the baked collections list based on the assets present in the main collection,
    focusing strictly on _GameReady collections and preserving existing properties.
    """
    print("[DEBUG] Populating baked collections from scene.")

    # Create a mapping of existing collections to preserve their properties
    existing_collections = {
        collection.name: {
            'include_in_send': collection.include_in_send,
            'assets_swapped': collection.assets_swapped,
            'is_baked': collection.is_baked,
            'is_fbx_exported': collection.is_fbx_exported,
            'assets': {asset.name: asset for asset in collection.assets}
        }
        for collection in assetify_settings.baked_collections
    }

    # Clear the current baked collections list
    assetify_settings.baked_collections.clear()

    # Ensure we are dealing only with _GameReady collections
    for collection in bpy.data.collections:
        if collection.name.endswith("_GameReady"):
            print(f"[DEBUG] Processing game-ready collection: {collection.name}")

            # Create a baked collection entry for the _GameReady collection
            baked_collection = assetify_settings.baked_collections.add()
            baked_collection.name = collection.name

            # Restore existing properties if available
            existing_props = existing_collections.get(collection.name)
            if existing_props:
                baked_collection.include_in_send = existing_props.get('include_in_send', False)
                baked_collection.assets_swapped = existing_props.get('assets_swapped', False)
                baked_collection.is_baked = existing_props.get('is_baked', False)
                baked_collection.is_fbx_exported = existing_props.get('is_fbx_exported', False)
            else:
                # Set default values for new collections
                baked_collection.include_in_send = False
                baked_collection.assets_swapped = False
                baked_collection.is_baked = False
                baked_collection.is_fbx_exported = False

            # Track if all assets are baked and exported
            all_assets_baked = True
            all_assets_exported = True
            asset_count = 0

            # Recursively add assets from the collection and subcollections
            for obj in collect_objects_from_collection(collection):
                if "_gameasset" in obj.name or obj.name in [asset.name for asset in assetify_settings.baked_assets]:
                    collection_asset = baked_collection.assets.add()
                    collection_asset.name = obj.name
                    collection_asset.is_game_asset = "_gameasset" in obj.name
                    collection_asset.is_baked = check_if_baked(obj) or any(
                        obj.name == asset.name and asset.is_baked
                        for col in assetify_settings.baked_collections
                        for asset in col.assets
                    )
                    collection_asset.is_fbx_exported = check_if_exported(obj)

                    # Try to restore asset's include_in_send from existing properties
                    if existing_props and obj.name in existing_props['assets']:
                        existing_asset = existing_props['assets'][obj.name]
                        collection_asset.include_in_send = existing_asset.include_in_send
                    else:
                        collection_asset.include_in_send = False  # Default value

                    asset_count += 1

                    # Update baked/exported status
                    if not collection_asset.is_baked:
                        all_assets_baked = False
                    if not collection_asset.is_fbx_exported:
                        all_assets_exported = False

                    print(f"[DEBUG] Added '{obj.name}' to baked collection '{baked_collection.name}' with baked status '{collection_asset.is_baked}'.")

            # Update the baked collection's overall status based on assets
            baked_collection.is_baked = all_assets_baked if asset_count > 0 else False
            baked_collection.is_fbx_exported = all_assets_exported if asset_count > 0 else False

            print(f"[DEBUG] Baked collection '{baked_collection.name}' populated with {asset_count} assets. Export status: {baked_collection.is_fbx_exported}")
        else:
            print(f"[DEBUG] Skipping original collection: {collection.name}")

    update_collection_statuses(assetify_settings)

    # Debug output: list the baked collections
    print(f"[DEBUG] Baked collections populated. Current collections: {[col.name for col in assetify_settings.baked_collections]}")

    # Redraw the UI to reflect the changes in the UI panel
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()

def collect_objects_from_collection(collection):
    """
    Recursively collect all mesh objects from the specified collection and its subcollections.
    """
    objects = []

    def collect_from_subcollections(col):
        for obj in col.objects:
            if obj.type == 'MESH':
                objects.append(obj)
        for subcol in col.children:
            collect_from_subcollections(subcol)

    collect_from_subcollections(collection)
    return objects
            
def process_asset_in_collection(assetify_settings, obj, baked_collection):
    """
    Process an asset in a collection and add it to the baked collection.
    Ensures the asset is properly linked to the collection in the baked assets list.
    """
    baked_asset = baked_collection.assets.add()
    baked_asset.name = obj.name
    baked_asset.is_game_asset = obj.get("is_game_asset", False)
    baked_asset.is_baked = obj.get("is_baked", False)
    baked_asset.is_fbx_exported = obj.get("is_fbx_exported", False)
    baked_asset.include_in_send = obj.get("include_in_send", True)

    # Debug output for asset processing
    print(f"[DEBUG] Added '{obj.name}' to baked_assets in '{baked_collection.name}' collection.")

class ASSETIFY_OT_switch_mode(bpy.types.Operator):
    """Switch between Asset and Collection modes in the panel"""
    bl_idname = "assetify.switch_mode"
    bl_label = "Switch Mode"

    mode: bpy.props.EnumProperty(
        name="Mode",
        description="Choose between Asset and Collection modes",
        items=[
            ('ASSET', 'Asset', 'Manage individual assets'),
            ('COLLECTION', 'Collection', 'Manage collections of assets')
        ],
        default='ASSET'
    )

    def store_asset_mode_states(self, assetify_settings):
        """Store the current include_in_send states for assets."""
        assetify_settings.asset_include_in_send_cache.clear()
        
        # Save asset mode settings
        for asset in assetify_settings.baked_assets:
            state = assetify_settings.asset_include_in_send_cache.add()
            state.name = asset.name
            state['include_in_send'] = asset.include_in_send

    def restore_asset_mode_states(self, assetify_settings):
        """Restore the include_in_send states for assets."""
        for cached_asset in assetify_settings.asset_include_in_send_cache:
            for asset in assetify_settings.baked_assets:
                if asset.name == cached_asset.name:
                    asset.include_in_send = cached_asset['include_in_send']

    def store_collection_mode_states(self, assetify_settings):
        """Store the current include_in_send states for collections."""
        assetify_settings.collection_include_in_send_cache.clear()

        # Save collection mode settings
        for collection in assetify_settings.baked_collections:
            state = assetify_settings.collection_include_in_send_cache.add()
            state.name = collection.name
            state['include_in_send'] = collection.include_in_send

    def restore_collection_mode_states(self, assetify_settings):
        """Restore the include_in_send states for collections."""
        for cached_collection in assetify_settings.collection_include_in_send_cache:
            for collection in assetify_settings.baked_collections:
                if collection.name == cached_collection.name:
                    collection.include_in_send = cached_collection['include_in_send']

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings

        # Store the current mode's state before switching
        if assetify_settings.asset_mode == 'ASSET':
            self.store_asset_mode_states(assetify_settings)
        elif assetify_settings.asset_mode == 'COLLECTION':
            self.store_collection_mode_states(assetify_settings)

        # Update the mode
        assetify_settings.asset_mode = self.mode
        print(f"[Assetify] Switched to {self.mode} mode.")

        # Repopulate the relevant list based on the selected mode
        if self.mode == 'ASSET':
            populate_baked_assets_from_scene(assetify_settings)
            self.restore_asset_mode_states(assetify_settings)
            print("[Assetify] Baked assets list populated and state restored.")
        elif self.mode == 'COLLECTION':
            populate_baked_collections_from_scene(assetify_settings)
            self.restore_collection_mode_states(assetify_settings)
            print("[Assetify] Baked collections list populated and state restored.")

        # Force UI redraw to reflect changes
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in {'VIEW_3D', 'PROPERTIES', 'OUTLINER'}:
                    area.tag_redraw()

        return {'FINISHED'}

def check_and_remove_empty_collections(assetify_settings):
    """Remove any empty collections from the baked collections list"""
    
    collections_to_remove = []
    
    # Iterate over all baked collections
    for collection in assetify_settings.baked_collections:
        collection_name = collection.name
        col = bpy.data.collections.get(collection_name)
        
        # Check if the collection exists and has no objects
        if col is None or not col.objects:
            collections_to_remove.append(collection_name)
    
    # Remove empty collections from the list and from Blender
    for collection_name in collections_to_remove:
        # Remove collection from the list
        collection_index = assetify_settings.baked_collections.find(collection_name)
        if collection_index != -1:
            assetify_settings.baked_collections.remove(collection_index)
            print(f"Removed empty collection: {collection_name} from baked_collections list")
        
        # Remove collection from Blender data if it still exists
        col = bpy.data.collections.get(collection_name)
        if col:
            bpy.data.collections.remove(col, do_unlink=True)
            print(f"Deleted empty collection: {collection_name} from Blender data")

def purge_unused_data():
    """Purge unused data-blocks from Blender to clean up any residual objects or collections."""
    # Remove all orphaned data-blocks
    bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    print("Purged unused data-blocks.")
    
class ASSETIFY_OT_delete_selected_assets(bpy.types.Operator):
    """Delete selected assets from the baked_assets list and remove corresponding objects"""
    bl_idname = "assetify.delete_selected_assets"
    bl_label = "Delete Selected Assets"

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings

        # Collect the names of selected assets for deletion
        assets_to_delete = [asset.name.strip() for asset in assetify_settings.baked_assets if asset.include_in_send]

        if not assets_to_delete:
            self.report({'INFO'}, "No assets selected for deletion.")
            return {'CANCELLED'}

        # Debug: Print out all assets that are going to be deleted
        print(f"[Assetify] Assets selected for deletion: {assets_to_delete}")

        # Track collections to check for emptiness after asset deletion
        collections_to_check = set()

        # Process the deletion of each asset
        for asset_name in assets_to_delete:
            print(f"[Assetify] Preparing to delete asset: '{asset_name}'")

            if asset_name == "":
                print("[Assetify] Skipping asset with empty name.")
                continue  # Skip if asset name is empty

            # Find and delete the object from Blender's data
            obj = bpy.data.objects.get(asset_name)
            if obj:
                for col in obj.users_collection:
                    collections_to_check.add(col.name)  # Track collection
                bpy.data.objects.remove(obj, do_unlink=True)
                print(f"[Assetify] Deleted object: {asset_name}")
            else:
                print(f"[Assetify] Object not found for asset: {asset_name}")

            # Remove the asset from the baked_assets list
            asset_index = assetify_settings.baked_assets.find(asset_name)
            if asset_index != -1:
                assetify_settings.baked_assets.remove(asset_index)
                print(f"[Assetify] Deleted asset: {asset_name} from baked_assets list")

        # After deleting assets, check if the collections are empty and clean them up
        check_and_remove_empty_collections(assetify_settings)

        # Purge unused data-blocks after deletion
        update_collection_statuses(assetify_settings)
        purge_unused_data()
        
        refresh_baked_collections_list(assetify_settings)
        
        context.area.tag_redraw()

        return {'FINISHED'}

class ASSETIFY_OT_delete_selected_collections(bpy.types.Operator):
    """Delete selected collections from the baked_collections list and remove corresponding collections and assets"""
    bl_idname = "assetify.delete_selected_collections"
    bl_label = "Delete Selected Collections"

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings

        # Collect the names of selected collections for deletion
        collections_to_delete = [col.name.strip() for col in assetify_settings.baked_collections if col.include_in_send and col.name.strip()]

        if not collections_to_delete:
            self.report({'INFO'}, "No collections selected for deletion.")
            return {'CANCELLED'}

        # Debug: Show which collections are selected for deletion
        print(f"[Assetify] Selected collections for deletion: {collections_to_delete}")

        # Track which assets are deleted to ensure we remove corresponding assets
        assets_to_remove = []

        # Process the deletion of each collection and its subcollections
        for collection_name in collections_to_delete:
            print(f"[Assetify] Preparing to delete collection: '{collection_name}'")

            if collection_name == "":
                print("[Assetify] Skipping deletion for collection with empty name.")
                continue  # Skip if collection name is empty

            # Find and delete the collection and subcollections from Blender's data
            self.recursively_delete_collection(collection_name, assetify_settings, assets_to_remove)

        # Now remove the assets from the baked_assets list
        for asset_name in assets_to_remove:
            asset_index = assetify_settings.baked_assets.find(asset_name)
            if asset_index != -1:
                assetify_settings.baked_assets.remove(asset_index)
                print(f"[Assetify] Deleted asset '{asset_name}' from baked_assets list")

        # Purge unused data-blocks after deletion
        purge_unused_data()

        # Refresh baked collections and assets to ensure they are updated after deletion
        refresh_baked_collections_list(assetify_settings)
        populate_baked_assets_from_scene(assetify_settings)  # Force refresh for assets

        update_collection_statuses(assetify_settings)
        
        context.area.tag_redraw()

        return {'FINISHED'}

    def recursively_delete_collection(self, collection_name, assetify_settings, assets_to_remove):
        """Recursively delete the collection and its subcollections"""
        col = bpy.data.collections.get(collection_name)
        if not col:
            print(f"[Assetify] Collection not found in Blender data for name: {collection_name}")
            return

        # Delete subcollections first (recursive deletion)
        subcollections = list(col.children)
        for subcol in subcollections:
            self.recursively_delete_collection(subcol.name, assetify_settings, assets_to_remove)

        # Find and collect the assets linked to this collection
        assets_in_collection = [asset for asset in assetify_settings.baked_assets if asset.collection_name == collection_name]
        print(f"[Assetify] Assets in collection '{collection_name}' before deletion: {[asset.name for asset in assets_in_collection]}")

        # Collect the asset names to be removed later
        for asset in assets_in_collection:
            assets_to_remove.append(asset.name)

        # Unlink from all scenes before deleting the collection
        for scene in bpy.data.scenes:
            if col.name in [scene_col.name for scene_col in scene.collection.children]:
                scene.collection.children.unlink(col)
                print(f"[Assetify] Unlinked collection '{collection_name}' from scene '{scene.name}'")

        # Remove the collection from Blender's data
        bpy.data.collections.remove(col, do_unlink=True)
        print(f"[Assetify] Deleted collection: {collection_name}")

        # Remove the collection from the baked_collections list
        collection_index = assetify_settings.baked_collections.find(collection_name)
        if collection_index != -1:
            assetify_settings.baked_collections.remove(collection_index)
            print(f"[Assetify] Deleted collection: {collection_name} from baked_collections list")

        # Final cleanup and UI update
        purge_unused_data()
        update_collection_statuses(assetify_settings)
        
def check_and_remove_empty_collections(assetify_settings):
    """Check for empty collections and remove them from the baked collections list and Blender."""
    
    # Collect collections that have no remaining assets
    empty_collections = []

    for collection in assetify_settings.baked_collections:
        collection_name = collection.name
        col = bpy.data.collections.get(collection_name)

        # If the collection has no objects left, mark it for removal
        if col is None or not col.objects:
            empty_collections.append(collection_name)

    # Remove empty collections
    for collection_name in empty_collections:
        collection_index = assetify_settings.baked_collections.find(collection_name)
        if collection_index != -1:
            assetify_settings.baked_collections.remove(collection_index)
            print(f"Removed empty collection: {collection_name} from baked_collections list")

        # Also remove the collection from Blender data if it still exists
        col = bpy.data.collections.get(collection_name)
        if col:
            bpy.data.collections.remove(col, do_unlink=True)
            print(f"Deleted empty collection: {collection_name} from Blender data")

def remove_assets_of_collection(collection_name, assetify_settings):
    # Find and remove all assets belonging to the deleted collection
    assets_to_remove = [asset for asset in assetify_settings.baked_assets if asset.collection_name == collection_name]
    
    for asset in assets_to_remove:
        asset_index = assetify_settings.baked_assets.find(asset.name)
        if asset_index != -1:
            assetify_settings.baked_assets.remove(asset_index)
            print(f"Removed asset '{asset.name}' from baked_assets list (belonged to {collection_name})")

def check_and_remove_empty_assets(assetify_settings):
    """Check if all assets of a collection are removed, and if so, remove the collection as well."""
    
    collections_to_check = set()
    
    # Check each baked asset and gather its collection
    for asset in assetify_settings.baked_assets:
        asset_name = asset.name
        obj = bpy.data.objects.get(asset_name)
        if obj:
            for col in obj.users_collection:
                collections_to_check.add(col.name)
    
    # Remove collections with no more assets
    for collection_name in collections_to_check:
        col = bpy.data.collections.get(collection_name)
        if col and not col.objects:
            collection_index = assetify_settings.baked_collections.find(collection_name)
            if collection_index != -1:
                assetify_settings.baked_collections.remove(collection_index)
                print(f"Removed empty collection: {collection_name} from baked_collections list")
            
            # Also remove from Blender's data
            bpy.data.collections.remove(col, do_unlink=True)
            print(f"Deleted empty collection: {collection_name} from Blender data")

def refresh_baked_collections_list(assetify_settings):
    """Repopulate the baked_collections list from the scene after deletion."""
    assetify_settings.baked_collections.clear()  # Clear the existing list
    
    # Repopulate the list with remaining collections
    for collection in bpy.data.collections:
        if "_GameReady" in collection.name:
            baked_collection = assetify_settings.baked_collections.add()
            baked_collection.name = collection.name
            print(f"Added baked collection: {collection.name}")

def sanitize_name(name):
    """Sanitize object name to create valid folder and file names."""
    sanitized_name = re.sub(r'[<>:"/\\|?*]', '_', name)
    return sanitized_name

def validate_baked_assets(scene):
    """Validate the baked assets list, removing any that are no longer valid."""
    settings = scene.assetify_bake_settings
    export_fbx_path = bpy.path.abspath(settings.export_fbx_path)
    export_fbx_dir = os.path.dirname(export_fbx_path)
    bake_folder = bpy.path.abspath(settings.bake_folder)
    textures_folder = os.path.join(bake_folder, "textures")
    valid_assets = []

    for asset in settings.baked_assets:
        # Check if the object still exists in the scene by its name
        obj = bpy.data.objects.get(asset.name)
        if obj is None:
            print(f"Removing {asset.name} from baked assets; object does not exist.")
            continue

        # Check if the associated textures exist
        texture_paths = [
            f"{sanitize_name(obj.name)}_BaseColor.png",
            f"{sanitize_name(obj.name)}_Normal.png",
            f"{sanitize_name(obj.name)}_Roughness.png",
            f"{sanitize_name(obj.name)}_Metallic.png",
            f"{sanitize_name(obj.name)}_Alpha.png",
            f"{sanitize_name(obj.name)}_MetallicSmoothness.png"
        ]

        # Validate if all textures exist
        textures_exist = all(os.path.exists(os.path.join(textures_folder, texture)) for texture in texture_paths)

        if not textures_exist:
            print(f"Removing {asset.name} from baked assets; one or more textures are missing.")
            continue

        # Check for the FBX file with '_fbx' suffix
        fbx_file_name = f"{sanitize_name(obj.name)}_fbx.fbx"
        fbx_file_path = os.path.join(export_fbx_dir, fbx_file_name)
        asset.is_fbx_exported = os.path.exists(fbx_file_path)

        # If valid, keep the asset
        valid_assets.append(asset)

    # Update the FBX export status for each collection
    for collection in settings.baked_collections:
        if collection.include_in_send:
            collection.is_fbx_exported = all(asset.is_fbx_exported for asset in collection.assets)    

    # Clear the existing baked assets and repopulate with valid assets
    settings.baked_assets.clear()
    for asset in valid_assets:
        new_asset = settings.baked_assets.add()
        new_asset.name = asset.name
        new_asset.object_ref = asset.object_ref  # keep object_ref
        new_asset.is_baked = asset.is_baked
        new_asset.is_fbx_exported = asset.is_fbx_exported
        new_asset.include_in_send = asset.include_in_send
        new_asset.collection_name = asset.collection_name

    print("Baked assets validation complete.")

def load_post_handler(scene):
    """This handler runs after loading a new Blender file."""
    validate_baked_assets(scene)

def clear_baked_assets_on_startup(scene):
    """Clear the baked assets list on Blender startup."""
    settings = scene.assetify_bake_settings
    settings.baked_assets.clear()
    print("Baked assets list cleared at startup.")

class CustomAssetItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty()
    is_baked: bpy.props.BoolProperty(default=False)
    include_in_send: bpy.props.BoolProperty(default=False)

class BakedAssetItem(bpy.types.PropertyGroup):
    """PropertyGroup for individual baked assets"""
    name: bpy.props.StringProperty(name="Asset Name")
    is_game_asset: bpy.props.BoolProperty(name="Is Game Asset", default=True)
    is_baked: bpy.props.BoolProperty(name="Is Baked", default=False)
    is_fbx_exported: bpy.props.BoolProperty(name="Is FBX Exported", default=False)
    include_in_send: bpy.props.BoolProperty(name="Include in Send", default=False)
    collection_name: bpy.props.StringProperty(name="Collection Name")

def update_include_in_send(self, context):
    """Update include_in_send status for collection and propagate to its hierarchy."""
    toggle_include_in_send_for_hierarchy(self.name, self.include_in_send)

class BakedCollectionItem(bpy.types.PropertyGroup):
    """
    Represents a baked collection within the Assetify add-on.
    
    Properties:
        name (StringProperty): The name of the collection.
        collection_ref (PointerProperty): Reference to the Blender Collection.
        is_baked (BoolProperty): Indicates if the collection has been baked.
        is_fbx_exported (BoolProperty): Indicates if the collection has been exported as FBX.
        include_in_send (BoolProperty): Flag to include the collection in operations.
        assets (CollectionProperty): List of assets within the collection.
    """
    
    name: bpy.props.StringProperty(
        name="Collection Name",
        description="Name of the baked collection",
        default=""
    )
    
    collection_ref: bpy.props.PointerProperty(
        name="Collection Reference",
        type=bpy.types.Collection,
        description="Reference to the Blender Collection"
    )
    
    is_baked: bpy.props.BoolProperty(
        name="Is Baked",
        description="Indicates if the collection has been baked",
        default=False
    )
    
    is_fbx_exported: bpy.props.BoolProperty(
        name="Is FBX Exported",
        description="Indicates if the collection has been exported as FBX",
        default=False
    )
    
    include_in_send: bpy.props.BoolProperty(
        name="Include in Send",
        description="Flag to include the collection in operations like deletion or export",
        default=False,
        update=update_include_in_send
    )
    
    assets: bpy.props.CollectionProperty(
        name="Assets",
        description="List of assets within the collection",
        type=BakedAssetItem
    )
    
    swap_status: bpy.props.StringProperty(
        name="Swap Status", 
        default="OG"
    )
    
    assets_swapped: bpy.props.BoolProperty(
        name="Assets Swapped",
        description="Indicates if the collection's assets have been swapped",
        default=False
    )

def update_baked_asset_list(context):
    assetify_settings = context.scene.assetify_bake_settings
    # Update baked_assets list
    for baked_asset in assetify_settings.baked_assets:
        obj = bpy.data.objects.get(baked_asset.name)
        if obj:
            baked_asset.is_baked = check_if_baked(obj)
    
    # Update baked status of collections
    update_baked_collections_status(context)

def check_if_baked(obj):
    if obj is None:
        print("[DEBUG] Object is None in check_if_baked")
        return False

    textures_folder = os.path.join(bpy.path.abspath(bpy.context.scene.assetify_bake_settings.bake_folder), "textures")
    required_maps = ["BaseColor", "Roughness", "Metallic", "Normal"]

    return all(os.path.exists(os.path.join(textures_folder, f"{obj.name}_{map_type}.png"))
               for map_type in required_maps)

def check_if_exported(obj):
    """Checks if the FBX export exists with the '_fbx' suffix."""
    # Retrieve the export path and sanitize
    export_fbx_path = bpy.path.abspath(bpy.context.scene.assetify_bake_settings.export_fbx_path)
    export_fbx_dir = os.path.dirname(export_fbx_path)
    
    # Sanitize object name and create expected file path
    fbx_file_name = f"{sanitize_name(obj.name.replace('_gameasset', ''))}_fbx.fbx"
    fbx_file_path = os.path.join(export_fbx_dir, fbx_file_name)
    
    # Debugging: Check file path being looked for
    print(f"[DEBUG] Checking FBX export path: {fbx_file_path}")
    
    # Check if the file exists at the specified path
    file_exists = os.path.exists(fbx_file_path)
    print(f"[DEBUG] File exists: {file_exists}")  # Print result of existence check
    return file_exists

class ASSETIFY_UL_baked_assets(bpy.types.UIList):
    """Custom UI list to display baked assets with checkboxes."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        assetify_settings = context.scene.assetify_bake_settings
        baked_asset = item

        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)

            # First column: Include in send checkbox
            split = row.split(factor=0.15)
            split.prop(baked_asset, "include_in_send", text="")

            # Second column: Asset name
            split = split.split(factor=0.4 / 0.85)
            display_name = baked_asset.name.replace("_gameasset", "")
            split.label(text=display_name)

            # Third column: Baked status
            remaining = split.split(factor=0.5)
            icon = 'CHECKMARK' if baked_asset.is_baked else 'X'
            remaining.label(icon=icon)

            # Fourth column: Exported status (dynamic based on format)
            import_format = assetify_settings.import_format.lower()
            valid_extensions = {
                'fbx': '.fbx',
                'obj': '.obj',
                'gltf': ['.glb', '.gltf'],
                'stl': '.stl',
            }
            selected_extension = valid_extensions.get(import_format)

            export_status = False
            if isinstance(selected_extension, list):
                export_status = any(
                    check_if_file_exists(baked_asset.name.replace("_gameasset", ""), bpy.path.abspath(assetify_settings.export_fbx_path), ext)
                    for ext in selected_extension
                )
            else:
                export_status = check_if_file_exists(
                    baked_asset.name.replace("_gameasset", ""),
                    bpy.path.abspath(assetify_settings.export_fbx_path),
                    selected_extension
                )

            icon = 'CHECKMARK' if export_status else 'X'
            remaining.label(icon=icon)

        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            display_name = baked_asset.name.replace("_gameasset", "")
            layout.label(text=display_name, icon='FILE_BLEND')

def toggle_include_in_send_for_hierarchy(collection_name, status):
    """Recursively set include_in_send for the given collection and all child collections."""
    # Look up the collection in bpy.data.collections by name
    collection = bpy.data.collections.get(collection_name)
    
    if collection is None:
        print(f"[DEBUG] Collection '{collection_name}' not found in bpy.data.collections.")
        return

    # Iterate over the child collections and update their status
    for child in collection.children:
        # Find the corresponding baked_collection entry
        for baked_collection in bpy.context.scene.assetify_bake_settings.baked_collections:
            if baked_collection.name == child.name:
                # Set the status for the child collection
                baked_collection.include_in_send = status
                # Recursively call to propagate to further descendants
                toggle_include_in_send_for_hierarchy(baked_collection.name, status)

class ASSETIFY_UL_collection_list(bpy.types.UIList):
    """Custom UI list to display baked collections with checkboxes, icons, and status."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        assetify_settings = context.scene.assetify_bake_settings
        baked_collection = item
        row = layout.row(align=True)

        # First column: Include in send checkbox
        split = row.split(factor=0.15)
        split.prop(baked_collection, "include_in_send", text="")

        # Second column: Collection name
        split = split.split(factor=0.4 / 0.9)
        display_name = baked_collection.name.replace("_GameReady", "")
        split.label(text=display_name)

        # Third column: Swap status
        remaining = split.split(factor=0.33)
        icon = 'CHECKMARK' if baked_collection.assets_swapped else 'X'
        remaining.label(icon=icon)

        # Fourth column: Baked status
        icon = 'CHECKMARK' if baked_collection.is_baked else 'X'
        remaining.label(icon=icon)

        # Fifth column: Exported status (dynamic based on format)
        import_format = assetify_settings.import_format.lower()
        valid_extensions = {
            'fbx': '.fbx',
            'obj': '.obj',
            'gltf': ['.glb', '.gltf'],
            'stl': '.stl',
        }
        selected_extension = valid_extensions.get(import_format)

        export_status = False
        for asset in baked_collection.assets:
            sanitized_name = asset.name.replace("_gameasset", "")
            if isinstance(selected_extension, list):
                export_status = any(
                    check_if_file_exists(sanitized_name, bpy.path.abspath(assetify_settings.export_fbx_path), ext)
                    for ext in selected_extension
                )
            else:
                export_status = check_if_file_exists(
                    sanitized_name,
                    bpy.path.abspath(assetify_settings.export_fbx_path),
                    selected_extension
                )
            if export_status:
                break  # Stop checking once a match is found

        icon = 'CHECKMARK' if export_status else 'X'
        remaining.label(icon=icon)
        
    def filter_items(self, context, data, propname):
        """Filter items based on search string and hierarchy level."""
        assetify_settings = context.scene.assetify_bake_settings
        items = getattr(assetify_settings, propname)

        # Get the current filter name (search string entered by the user)
        filter_name = self.filter_name.lower().strip()

        # Flags to filter items (1: visible, 0: hidden)
        filter_flags = [0] * len(items)

        for index, collection in enumerate(items):
            # Check if the collection name matches the filter and is a top-level collection
            if filter_name in collection.name.lower() and get_collection_level(collection.name) == 0:
                filter_flags[index] = self.bitflag_filter_item

        # Do not sort; return items in their original order
        return filter_flags, list(range(len(items)))
        
def get_collection_level(collection_name):
    """
    Get the hierarchy level of the collection.
    Level 0 is a top-level collection.
    """
    collection = bpy.data.collections.get(collection_name)
    if not collection:
        return -1  # Return -1 if collection not found

    level = 0
    current_collection = collection

    while True:
        parent_found = False
        for parent in bpy.data.collections:
            if current_collection.name in [child.name for child in parent.children]:
                level += 1
                current_collection = parent
                parent_found = True
                break  # Found a parent, move up the hierarchy
        if not parent_found:
            break  # No more parents found, reached top level

    return level

def apply_particle_systems(emitter_object):
    """
    Applies particle systems and preceding modifiers in the correct stack order.
    Converts instances to real objects and joins them with the emitter mesh.

    Args:
        emitter_object (bpy.types.Object): The emitter object with particle systems.

    Returns:
        bpy.types.Object: The resulting object with particle instances applied and joined.
    """
    if not emitter_object.particle_systems:
        print(f"[DEBUG] No particle systems found on {emitter_object.name}.")
        return emitter_object  # No particle systems to apply

    print(f"[DEBUG] Processing particle systems and modifiers on {emitter_object.name}.")

    # Ensure the object is active
    bpy.context.view_layer.objects.active = emitter_object

    # Apply all modifiers up to and including the particle system
    while emitter_object.modifiers:
        modifier = emitter_object.modifiers[0]
        print(f"[DEBUG] Applying modifier: {modifier.name} of type {modifier.type}")

        if modifier.type == 'PARTICLE_SYSTEM':
            # Make particle system instances real
            bpy.ops.object.select_all(action='DESELECT')
            emitter_object.select_set(True)
            bpy.ops.object.duplicates_make_real()

            # Collect all created objects
            created_objects = [obj for obj in bpy.context.scene.objects if obj.select_get() and obj != emitter_object]
            if not created_objects:
                print(f"[DEBUG] No instances created for particle system on {emitter_object.name}.")
                break

            # Join created instances with the emitter object
            bpy.ops.object.select_all(action='DESELECT')
            for obj in created_objects:
                obj.select_set(True)
            emitter_object.select_set(True)
            bpy.context.view_layer.objects.active = emitter_object
            bpy.ops.object.join()

            print(f"[DEBUG] Joined particle instances with {emitter_object.name}.")

            # Remove the particle system modifier
            emitter_object.modifiers.remove(modifier)
        else:
            # Apply the current modifier
            bpy.ops.object.modifier_apply(modifier=modifier.name)

    print(f"[DEBUG] Finished applying particle systems and modifiers for {emitter_object.name}.")
    return emitter_object

def find_layer_collection(layer_coll, coll):
    """
    Recursively search for a LayerCollection with the matching collection.
    """
    if layer_coll.collection == coll:
        return layer_coll
    for child in layer_coll.children:
        found = find_layer_collection(child, coll)
        if found:
            return found
    return None

def ensure_file_saved(operator, context):
    """
    Ensures the Blender file is saved before proceeding.
    - If the file has never been saved, opens the native Save As dialog.
    - If there are unsaved changes, prompts the user to save or proceed without saving.
    """
    # Force Blender to update its state
    bpy.context.view_layer.update()
    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

    if not bpy.data.filepath:
        # File has never been saved
        def draw(self, context):
            self.layout.label(text="This file has never been saved. Please save it before proceeding.")
            row = self.layout.row(align=True)
            # "Save As" button
            row.operator("assetify.save_as_mainfile", text="Save As", icon='FILE_TICK')
            # "Cancel" button
            row.operator("assetify.cancel_operation", text="Cancel", icon='CANCEL')

        context.window_manager.popup_menu(draw, title="Save File", icon='INFO')
        return False

    elif bpy.data.is_dirty:
        # File has unsaved changes
        def draw(self, context):
            self.layout.label(text="You have unsaved changes. Save before continuing?")
            row = self.layout.row(align=True)
            # "Save Now" button
            save_op = row.operator("assetify.save_and_continue", text="Save & Proceed", icon='FILE_TICK')
            if operator:
                save_op.operator_id = operator.__class__.bl_idname
            else:
                print("Warning: 'operator' is None when attempting to set 'operator_id'.")

            # "Don't Save & Proceed" button
            proceed_op = row.operator("assetify.proceed_without_saving", text="Don't Save & Proceed", icon='PLAY')
            if operator:
                proceed_op.operator_id = operator.__class__.bl_idname
            else:
                print("Warning: 'operator' is None when attempting to set 'operator_id'.")

            # "Cancel" button
            cancel_op = row.operator("assetify.cancel_operation", text="Cancel", icon='CANCEL')

        context.window_manager.popup_menu(draw, title="Unsaved Changes", icon='ERROR')
        return False

    return True

class ASSETIFY_OT_save_as_mainfile(bpy.types.Operator):
    """Open the native Save As dialog"""
    bl_idname = "assetify.save_as_mainfile"
    bl_label = "Save As"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # Open Blender's native Save As dialog
        bpy.ops.wm.save_as_mainfile('INVOKE_DEFAULT')
        return {'FINISHED'}

class ASSETIFY_OT_save_and_continue(bpy.types.Operator):
    """Save the file and continue the current operation"""
    bl_idname = "assetify.save_and_continue"
    bl_label = "Save and Continue"

    operator_id: bpy.props.StringProperty()  # Store the original operator's ID

    def execute(self, context):
        # Save the file
        try:
            bpy.ops.wm.save_mainfile()
        except Exception as e:
            self.report({'ERROR'}, f"Failed to save file: {e}")
            return {'CANCELLED'}

        # Update the skip_save_check property directly
        context.scene.assetify_bake_settings.skip_save_check = True

        # Ensure the operator ID is valid
        if not self.operator_id or '.' not in self.operator_id:
            self.report({'ERROR'}, f"Invalid operator ID: {self.operator_id}")
            return {'CANCELLED'}

        # Dynamically call the operator using the correct ID
        try:
            # Safely access the operator without using eval
            operator_path = self.operator_id.split('.')
            op = bpy.ops
            for attr in operator_path:
                op = getattr(op, attr)

            # Call the operator with 'INVOKE_DEFAULT'
            result = op('INVOKE_DEFAULT')

            if 'CANCELLED' not in result:
                # Operator is running or has finished successfully
                return result
            else:
                self.report({'WARNING'}, f"Operator did not finish successfully: {self.operator_id}")
                return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error continuing operation: {e}")
            return {'CANCELLED'}

class ASSETIFY_OT_proceed_without_saving(bpy.types.Operator):
    """Proceed without saving the file"""
    bl_idname = "assetify.proceed_without_saving"
    bl_label = "Don't Save & Proceed"

    operator_id: bpy.props.StringProperty()  # Store the original operator's ID

    def execute(self, context):
        # Set a flag to indicate the user chose to proceed without saving
        context.scene.assetify_bake_settings.skip_save_check = True

        # Ensure the operator ID is valid
        if not self.operator_id or '.' not in self.operator_id:
            self.report({'ERROR'}, f"Invalid operator ID: {self.operator_id}")
            return {'CANCELLED'}

        # Dynamically call the operator using the correct ID
        try:
            # Access the operator path
            operator_path = self.operator_id.split('.')
            op = bpy.ops
            for attr in operator_path:
                op = getattr(op, attr)

            # Call the operator without extra keyword arguments
            result = op('INVOKE_DEFAULT')

            if 'CANCELLED' not in result:
                # Operator ran successfully
                return result
            else:
                self.report({'WARNING'}, f"Operator did not finish successfully: {self.operator_id}")
                return {'CANCELLED'}
        except Exception as e:
            self.report({'ERROR'}, f"Error proceeding without saving: {e}")
            return {'CANCELLED'}

class ASSETIFY_OT_cancel_operation(bpy.types.Operator):
    """Cancel the current operation"""
    bl_idname = "assetify.cancel_operation"
    bl_label = "Cancel Operation"

    def execute(self, context):
        self.report({'INFO'}, "Operation cancelled.")
        return {'CANCELLED'}

class OBJECT_OT_convert_to_game_ready(bpy.types.Operator):
    """Convert the user-selected collection to Game-Ready format with unique objects"""
    bl_idname = "object.convert_to_game_ready"
    bl_label = "Convert Selected to Game-Ready"
    bl_options = {'REGISTER', 'UNDO'}

    # Variables for modal operation
    _timer = None
    _collections_to_process = []
    _current_index = 0
    total_collections = 0
    progress_value: bpy.props.FloatProperty(name="Progress", default=0.0, min=0.0, max=1.0)
    current_operation = ""
    current_sub_operation = ""

    draw_handler = None
    space_reference = None

    def invoke(self, context, event):
        # Access the assetify_bake_settings for the current scene
        bake_settings = context.scene.assetify_bake_settings

        # Show the save popup if skip_save_check is False
        if not bake_settings.skip_save_check:
            if not ensure_file_saved(self, context):
                return {'CANCELLED'}

        # Proceed to execute the operation
        return self.execute(context)

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        asset_collections = assetify_settings.asset_collections

        # Ensure viewport shading is set to solid
        set_viewport_shading_to_solid(context)

        if not asset_collections:
            self.report({'WARNING'}, "No asset collections selected.")
            return {'CANCELLED'}

        # Continue with the regular game-ready conversion
        self.report({'INFO'}, "Running Game Ready Conversion...")
        return self.run_still_process(context)

    def run_still_process(self, context):
        """Handle the still process for game-ready asset conversion."""
        self._assets_to_process = []
        self._current_asset_index = 0
        self.total_assets = 0
        self.progress_value = 0.0
        self.current_operation = "Setting Up Game Assets..."
        self.current_sub_operation = ""
        self.game_ready_collections = {}
        self.main_collections = set()

        # Initialize tracking structures
        self.collection_asset_counts = {}
        self.baked_collections_data = {}
        self.collections_processed = set()

        # Collect assets and create game-ready collections
        assetify_settings = context.scene.assetify_bake_settings
        for item in assetify_settings.asset_collections:
            collection = item.collection
            if collection:
                self.main_collections.add(collection)
                # Create game-ready collection for main collection
                self.create_game_ready_collection(collection, context, assetify_settings)
                # Collect assets and build game-ready subcollections
                self.collect_assets(
                    collection,
                    context,
                    parent_game_ready_collection=self.game_ready_collections[collection]
                )

        self.total_assets = len(self._assets_to_process)

        if self.total_assets == 0:
            self.report({'WARNING'}, "No assets to process.")
            return {'CANCELLED'}

        # Start progress bar
        start_progress_bar(self, initial_message="Processing Selected Assets")

        # Start modal timer
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'TIMER':
            if self._current_asset_index < self.total_assets:
                asset_info = self._assets_to_process[self._current_asset_index]
                obj = asset_info['object']
                original_collection = asset_info['original_collection']
                self.current_sub_operation = f"Processing {obj.name}..."

                # Process the asset
                self.process_asset(obj, original_collection, context)

                # Update progress
                self._current_asset_index += 1
                self.progress_value = self._current_asset_index / self.total_assets
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
            else:
                # Processing complete
                self.current_operation = "Processing Complete."
                remove_progress_bar(self)
                context.window_manager.event_timer_remove(self._timer)

                assetify_settings = context.scene.assetify_bake_settings

                if assetify_settings.disable_original_collections:
                    # Exclude original collections from all view layers
                    for view_layer in bpy.context.scene.view_layers:
                        # Exclude original collections
                        for original_collection in self.main_collections:
                            layer_coll = find_layer_collection(view_layer.layer_collection, original_collection)
                            if layer_coll:
                                layer_coll.exclude = True
                                print(f"Excluded collection from {view_layer.name}: {original_collection.name}")
                            else:
                                print(f"Could not find LayerCollection for {original_collection.name} in {view_layer.name}")
                else:
                    print("Original collections will remain visible.")

                # Update statuses
                assetify_settings = context.scene.assetify_bake_settings
                assetify_settings.assets_baked = False

                populate_baked_assets_from_scene(assetify_settings)
                populate_baked_collections_from_scene(assetify_settings)

                update_collection_statuses(assetify_settings)
                self.report({'INFO'}, "Game assets set and collection statuses updated.")
                
                assetify_settings.skip_save_check = False
                
                return {'FINISHED'}

        elif event.type in {'ESC'}:
            # User cancelled the operation
            self.cancel(context)
            return {'CANCELLED'}

        return {'PASS_THROUGH'}

    def process_collection(self, collection, context):
        """
        Processes a collection by creating a game-ready collection and converting its assets.
        Handles particle systems in the collection's objects.
        
        Args:
            collection (bpy.types.Collection): The original collection to process.
            context (bpy.types.Context): Blender's context.
        """
        # Create the main game-ready collection
        game_ready_collection_name = self.generate_unique_collection_name(f"{collection.name}_GameReady")
        game_ready_collection = bpy.data.collections.new(game_ready_collection_name)
        context.scene.collection.children.link(game_ready_collection)
        print(f"Created new game-ready collection: {game_ready_collection.name}")

        # Process each object in the collection
        for obj in collection.objects:
            if obj.type in {'MESH', 'CURVE', 'FONT'}:
                if obj.particle_systems:
                    print(f"Applying particle systems on {obj.name}")
                    obj = apply_particle_systems(obj)

                # Add the processed asset to the game-ready collection
                self.process_asset(obj, collection, context)

        # Recursively process subcollections
        for subcollection in collection.children:
            self.process_collection(subcollection, context)
  
    def generate_unique_collection_name(self, base_name):
        """
        Generate a unique collection name by appending '_#_GameReady' if needed.

        Args:
            base_name (str): The desired base name for the collection.

        Returns:
            str: A unique collection name.
        """
        suffix = "_GameReady"
        # Strip any existing `_GameReady` suffix from the base name
        if base_name.endswith(suffix):
            base_name = base_name[:-len(suffix)]

        unique_name = f"{base_name}{suffix}"
        counter = 1

        # Check if the name already exists in bpy.data.collections
        while unique_name in bpy.data.collections.keys():
            unique_name = f"{base_name}_{counter}{suffix}"
            counter += 1

        return unique_name
    
    def generate_unique_asset_name(self, base_name, suffix="_gameasset"):
        """
        Generate a unique asset name by appending '_#_gameasset' if needed.

        Args:
            base_name (str): The desired base name for the asset.
            suffix (str): The suffix to append to the asset name.

        Returns:
            str: A unique asset name.
        """
        # Create a list of existing game asset names
        existing_gameasset_names = [obj.name for obj in bpy.data.objects if obj.name.endswith(suffix)]

        # Initial unique name
        unique_name = base_name + suffix

        # If the initial name does not exist in game assets, return it directly
        if unique_name not in existing_gameasset_names:
            return unique_name

        # If it exists, start appending a counter
        counter = 1
        while True:
            unique_name = f"{base_name}_{counter}{suffix}"
            if unique_name not in existing_gameasset_names:
                return unique_name
            counter += 1
  
    def process_asset(self, obj, original_collection, context):
        """
        Processes an individual asset, converting it to a game-ready version.
        Handles particle systems, modifier stacks, and text objects (converting them to meshes).
        Ensures the 3D viewport is set to solid shading to prevent crashes.

        Args:
            obj (bpy.types.Object): The object to process.
            original_collection (bpy.types.Collection): The collection the object belongs to.
            context (bpy.types.Context): Blender's context.
        """
        # Ensure the 3D viewport is set to solid shading
        game_ready_collection = self.game_ready_collections.get(original_collection)
        if not game_ready_collection:
            print(f"[WARNING] No game-ready collection found for {obj.name}")
            return

        # Duplicate the original object to create a game asset
        new_obj = obj.copy()
        if obj.data:
            new_obj.data = obj.data.copy()
        new_obj.name = f"{obj.name}_gameasset"
        game_ready_collection.objects.link(new_obj)
        print(f"[DEBUG] Linked object: {new_obj.name} to {game_ready_collection.name}")
        
        assetify_settings = context.scene.assetify_bake_settings

        # Determine skip conditions based on bake mode and skip_conversion
        skip_conditions = animation_processor.get_skip_conditions()
        animation_type = context.scene.assetify_animation_settings.animation_type
        file_format = context.scene.assetify_animation_settings.file_format

        # Make sure only new_obj is selected and set as active
        bpy.ops.object.select_all(action='DESELECT')
        new_obj.select_set(True)
        bpy.context.view_layer.objects.active = new_obj
        print(f"[DEBUG] New Objectz: {new_obj.name}")

        debug_print(f"[DEBUG] Animation Type: {animation_type}, File Format: {file_format}")
        debug_print(f"[DEBUG] Skip Conditions: {skip_conditions}")
        
        # Check if in animation mode and process accordingly
        if assetify_settings.bake_mode == 'ANIMATION' and not (animation_type, file_format) in skip_conditions:
            self.report({'INFO'}, f"Checking animation processing conditions for {new_obj.name}...")
            from .animation_processor import process_animation_conditions
            result = process_animation_conditions(context, report_func=self.report, obj=new_obj)
            if result == {'CANCELLED'}:
                self.report({'WARNING'}, f"Animation preprocessing was cancelled for {new_obj.name}.")
                return {'CANCELLED'}

        elif assetify_settings.bake_mode == 'ANIMATION' and (animation_type, file_format) in skip_conditions:
            from .animation_processor import process_animation_conditions
            result = process_animation_conditions(context, report_func=self.report, obj=new_obj)
            debug_print(f"[INFO] Condition met to Skip Conversion. Skipping conversion for {new_obj.name} due to conditions: ({animation_type}, {file_format}).")
            
            # Remove any empty material slots before making materials unique
            remove_empty_material_slots(new_obj)
            debug_print(f"[DEBUG] Removed empty material slots for {new_obj.name}.")

            # Make the materials unique for the duplicated object
            make_materials_unique(new_obj)
            debug_print(f"[DEBUG] Made materials unique for {new_obj.name}.")

            # Rename materials to match object name
            rename_materials(new_obj)
            debug_print(f"[DEBUG] Renamed materials for {new_obj.name}.")

            # Convert UVMap attribute to an actual UV map layer
            convert_uvmap_attribute_to_uv_layer(new_obj)
            debug_print(f"[DEBUG] Converted UVMap attributes for {new_obj.name}.")
            
            simplify_materials_and_uv_maps(obj)
            finalize_uv_maps(obj)
            
            return {'SKIPPED'}

        # Convert text objects to meshes if necessary
        if new_obj.type == 'FONT':
            print(f"[DEBUG] Converting text object {new_obj.name} to mesh.")
            bpy.context.view_layer.objects.active = new_obj
            bpy.ops.object.convert(target='MESH')
            print(f"[DEBUG] Converted text object {new_obj.name} to mesh.")

        # Process particle systems and modifiers
        #if new_obj.particle_systems or new_obj.modifiers:
         #   print(f"[DEBUG] Processing particle systems and modifiers on {new_obj.name}")
          #  new_obj = apply_particle_systems(new_obj)

        # Process the object for game readiness
        process_object(
            new_obj,
            context.scene.custom_object,
            context.scene.custom_name,
            context.scene.custom_value_name
        )

        # Add the asset to the baked assets list
        baked_asset = assetify_settings.baked_assets.add()
        baked_asset.name = new_obj.name
        baked_asset.is_game_asset = True
        baked_asset.is_baked = check_if_baked(new_obj)
        baked_asset.is_fbx_exported = check_if_exported(new_obj)
        baked_asset.include_in_send = False
        baked_asset.collection_name = game_ready_collection.name
        print(f"[DEBUG] Added {new_obj.name} to baked assets.")

        # Decrement the asset count for the collection
        self.collection_asset_counts[original_collection] -= 1

        # Manage baked collections
        self.manage_baked_collections(original_collection, baked_asset, game_ready_collection, context)

    def manage_baked_collections(self, original_collection, baked_asset, game_ready_collection, context):
        """
        Updates and manages the baked collections list and related data.
        """
        assetify_settings = context.scene.assetify_bake_settings

        # Initialize baked collection data if not already done
        if original_collection not in self.baked_collections_data:
            self.baked_collections_data[original_collection] = {
                'name': game_ready_collection.name,
                'is_baked': False,
                'include_in_send': False,
                'assets': []
            }

        baked_collection_data = self.baked_collections_data[original_collection]
        baked_collection_data['assets'].append({
            'name': baked_asset.name,
            'is_game_asset': baked_asset.is_game_asset,
            'is_baked': baked_asset.is_baked,
            'is_fbx_exported': baked_asset.is_fbx_exported,
            'include_in_send': baked_asset.include_in_send
        })

        # Update collection status
        if baked_asset.is_baked:
            baked_collection_data['is_baked'] = True
        if baked_asset.is_fbx_exported:
            baked_collection_data['include_in_send'] = True

        # Finalize baked collection if all assets are processed
        if self.collection_asset_counts[original_collection] == 0 and original_collection not in self.collections_processed:
            baked_collection = assetify_settings.baked_collections.add()
            baked_collection.name = baked_collection_data['name']
            baked_collection.is_baked = baked_collection_data['is_baked']
            baked_collection.include_in_send = baked_collection_data['include_in_send']

            # Add assets to baked collection
            for asset_data in baked_collection_data['assets']:
                collection_asset = baked_collection.assets.add()
                collection_asset.name = asset_data['name']
                collection_asset.is_game_asset = asset_data['is_game_asset']
                collection_asset.is_baked = asset_data['is_baked']
                collection_asset.is_fbx_exported = asset_data['is_fbx_exported']
                collection_asset.include_in_send = asset_data['include_in_send']
                print(f"[DEBUG] Added {asset_data['name']} to baked collection: {baked_collection.name}")

            print(f"[DEBUG] Added baked collection entry: {baked_collection.name}")
            self.collections_processed.add(original_collection)

    def cancel(self, context):
        context.window_manager.event_timer_remove(self._timer)
        remove_progress_bar(self)
        self.report({'INFO'}, "Set Game Assets operation canceled.")

    def count_total_assets(self, collections):
        total_assets = 0
        for collection in collections:
            total_assets += self.count_assets_in_collection(collection)
        return total_assets

    def count_assets_in_collection(self, collection):
        count = len([obj for obj in collection.objects if obj.type in {'MESH', 'CURVE', 'FONT'}])
        for subcol in collection.children:
            count += self.count_assets_in_collection(subcol)
        return count
    
    def collect_assets(self, collection, context, parent_game_ready_collection):
        # Create game-ready collection for this collection if not main collection
        if collection in self.main_collections:
            # Game-ready collection already created
            game_ready_collection = self.game_ready_collections[collection]
        else:
            # Create game-ready collection for subcollection
            game_ready_collection_name = f"{collection.name}_GameReady"
            game_ready_collection = bpy.data.collections.new(game_ready_collection_name)
            parent_game_ready_collection.children.link(game_ready_collection)
            self.game_ready_collections[collection] = game_ready_collection
            print(f"Created game-ready subcollection: {game_ready_collection.name}")

        # Collect assets
        for obj in collection.objects:
            if obj.type in {'MESH', 'CURVE', 'FONT'}:
                self._assets_to_process.append({'object': obj, 'original_collection': collection})

                # Update asset count for the collection
                if collection not in self.collection_asset_counts:
                    self.collection_asset_counts[collection] = 0
                self.collection_asset_counts[collection] += 1

        # Recursively collect from subcollections
        for subcol in collection.children:
            self.collect_assets(subcol, context, parent_game_ready_collection=game_ready_collection)
            
    def create_game_ready_collection(self, collection, context, assetify_settings):
        game_ready_collection_name = self.generate_unique_collection_name(f"{collection.name}_GameReady")
        game_ready_collection = bpy.data.collections.new(game_ready_collection_name)
        context.scene.collection.children.link(game_ready_collection)
        self.game_ready_collections[collection] = game_ready_collection
        print(f"Created game-ready collection: {game_ready_collection.name}")
            
    def get_main_collection(self, collection):
        if collection in self.main_collections:
            return collection
        for parent_col in bpy.data.collections:
            for child in parent_col.children:
                if child == collection:
                    return self.get_main_collection(parent_col)
        return None

class ASSETIFY_OT_import_selected_fbx(bpy.types.Operator):
    """Import files for selected assets with progress bar"""
    bl_idname = "assetify.import_selected_fbx"
    bl_label = "Import Selected Files"

    _timer = None
    _import_index = 0
    _assets_to_import = []
    total_import_steps = 0
    progress_value = 0.0
    current_operation = ""
    current_sub_operation = ""

    draw_handler = None
    space_reference = None

    @classmethod
    def poll(cls, context):
        assetify_settings = context.scene.assetify_bake_settings
        export_path = bpy.path.abspath(assetify_settings.export_fbx_path)
        bake_folder = bpy.path.abspath(assetify_settings.bake_folder)
        texture_folder = os.path.join(bake_folder, "textures")

        if not export_path or not os.path.isdir(export_path):
            print("[DEBUG] Export path is invalid or does not exist.")
            return False

        # Determine the valid extension based on the mode
        if assetify_settings.export_mode == 'STILL':
            selected_extension = assetify_settings.import_format.lower()
            suffix = "_still"
        elif assetify_settings.export_mode == 'ANIMATION':
            selected_extension = assetify_settings.animation_import_format.lower()
            suffix = "_anim"
        else:
            print("[DEBUG] Invalid export mode selected.")
            return False

        # Use the full animation export format name for file naming
        format_label = assetify_settings.animation_import_format.upper()

        valid_extensions = {
            'fbx': '.fbx',
            'obj': '.obj',
            'gltf': ['.glb', '.gltf'],
            'stl': '.stl',
            'alembic': '.abc',
        }
        selected_extension = valid_extensions.get(selected_extension)

        # Check for assets
        if assetify_settings.asset_mode == 'ASSET':
            for asset in assetify_settings.baked_assets:
                if asset.include_in_send:
                    if cls.check_asset_files(asset, export_path, selected_extension, suffix, format_label):
                        return True

        # Check for collections
        elif assetify_settings.asset_mode == 'COLLECTION':
            for collection in assetify_settings.baked_collections:
                if collection.include_in_send and get_collection_level(collection.name) == 0:
                    for asset in collection.assets:
                        if cls.check_asset_files(asset, export_path, selected_extension, suffix, format_label):
                            return True
        return False

    @staticmethod
    def check_asset_files(asset, export_path, selected_extension, suffix, format_label):
        """
        Check if a file exists for a given asset with the expected naming convention.
        Supports both single and multiple extensions (e.g., GLTF: .glb, .gltf).

        Args:
            asset (bpy.types.PropertyGroup): The asset to check.
            export_path (str): The path where exported files are located.
            selected_extension (str or list): The expected file extension(s).
            suffix (str): The suffix appended to the file name (_still or _ANIM).
            format_label (str): The export format label (e.g., ALEMBIC, FBX).

        Returns:
            bool: True if a matching file is found, False otherwise.
        """
        sanitized_name = sanitize_name(asset.name.replace("_gameasset", ""))

        # Standardize format_label and suffix for consistent comparison
        format_label = format_label.upper().replace(" ", "_")
        suffix = suffix.upper()

        # Handle extension types (single or multiple)
        if isinstance(selected_extension, list):
            extensions = [ext.lstrip(".").lower() for ext in selected_extension]
        else:
            extensions = [selected_extension.lstrip(".").lower()]

        # Iterate through possible extensions to find the file
        for ext in extensions:
            # Correct check for OBJ animation sequences (no underscore before 0001)
            if format_label == "OBJ" and suffix == "_ANIM":
                full_name = f"{sanitized_name}_{format_label}{suffix}0001.{ext}"
            else:
                full_name = f"{sanitized_name}_{format_label}{suffix}.{ext}"

            file_path = os.path.join(export_path, full_name)

            # Check if the file exists
            if os.path.isfile(file_path):
                print(f"[DEBUG] Found file: {file_path}")
                return True
            else:
                print(f"[DEBUG] File not found: {file_path}")

        print(f"[DEBUG] No matching files found for asset: {sanitized_name}")
        return False

    def execute(self, context):
        """Prepare for importing files based on the selected format."""
        assetify_settings = context.scene.assetify_bake_settings
        export_path = bpy.path.abspath(assetify_settings.export_fbx_path)

        # Determine the valid extension and suffix based on the mode
        if assetify_settings.export_mode == 'STILL':
            selected_format = assetify_settings.import_format.lower()
            suffix = "_still"
        elif assetify_settings.export_mode == 'ANIMATION':
            selected_format = assetify_settings.animation_import_format.lower()
            suffix = "_anim"
        else:
            self.report({'WARNING'}, "Invalid export mode selected.")
            return {'CANCELLED'}

        # Define valid extensions for import formats
        valid_extensions = {
            'fbx': '.fbx',
            'obj': '.obj',
            'gltf': ['.glb', '.gltf'],
            'stl': '.stl',
            'alembic': '.abc',  # Added Alembic support
        }
        selected_extension = valid_extensions.get(selected_format)

        if not selected_extension:
            self.report({'WARNING'}, f"Unsupported format: {selected_format.upper()}")
            return {'CANCELLED'}

        # Prepare a readable label for the format (handles both single and list extensions)
        if isinstance(selected_extension, list):
            extension_label = ", ".join(ext.upper() for ext in selected_extension)
        else:
            extension_label = selected_extension.upper()

        self._assets_to_import = []

        for asset in assetify_settings.baked_assets:
            sanitized_name = sanitize_name(asset.name.replace("_gameasset", ""))
            full_name = f"{sanitized_name}_{selected_format.upper()}{suffix}"  # Include format

            if isinstance(selected_extension, list):
                for ext in selected_extension:
                    # ✅ Adjust check for OBJ sequences
                    if selected_format == 'obj' and assetify_settings.export_mode == 'ANIMATION':
                        # Check for the OBJ sequence with frame numbers
                        if check_for_obj_sequence(full_name, export_path, ext):
                            self._assets_to_import.append((asset, ext))
                            break
                    else:
                        if check_if_file_exists(full_name, export_path, ext):
                            self._assets_to_import.append((asset, ext))
                            break
            else:
                if selected_format == 'obj' and assetify_settings.export_mode == 'ANIMATION':
                    # ✅ Check for OBJ sequence when in animation mode
                    if check_for_obj_sequence(full_name, export_path, selected_extension):
                        self._assets_to_import.append((asset, selected_extension))
                else:
                    if check_if_file_exists(full_name, export_path, selected_extension):
                        self._assets_to_import.append((asset, selected_extension))

        if not self._assets_to_import:
            self.report({'WARNING'}, f"No files found for selected assets in {extension_label} format.")
            return {'CANCELLED'}

        # Initialize progress tracking
        self.total_import_steps = len(self._assets_to_import)
        start_progress_bar(self, initial_message=f"Importing {extension_label} Files")
        self.progress_value = 0.0
        self.current_operation = f"Importing {extension_label} Files..."

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        """Handle the modal import process."""
        if event.type == 'TIMER':
            if self._import_index < len(self._assets_to_import):
                asset, ext = self._assets_to_import[self._import_index]
                self.current_sub_operation = f"Importing {asset.name}..."
                self.import_file_for_asset(asset.name, context, ext)

                # Update progress after each import
                self._import_index += 1
                self.progress_value = self._import_index / self.total_import_steps
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

            else:
                # Import complete
                self.current_operation = "Import Complete."
                remove_progress_bar(self)
                context.window_manager.event_timer_remove(self._timer)
                self.report({'INFO'}, "Files imported successfully.")
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def import_file_for_asset(self, asset_name, context, format_ext):
        """Import a file for the given asset based on its format and fix alpha texture links."""
        assetify_settings = context.scene.assetify_bake_settings
        export_path = bpy.path.abspath(assetify_settings.export_fbx_path)

        # Append _still or _ANIM based on the export mode
        if assetify_settings.export_mode == 'STILL':
            suffix = "_still"
            mode_format = assetify_settings.import_format.upper()
        elif assetify_settings.export_mode == 'ANIMATION':
            suffix = "_ANIM"
            mode_format = assetify_settings.animation_import_format.upper()
        else:
            self.report({'WARNING'}, "Invalid export mode. Cannot determine naming convention.")
            return
        
        # Debugging: Check the format extension and export mode
        print(f"[DEBUG] format_ext: {format_ext}")
        print(f"[DEBUG] export_mode: {assetify_settings.export_mode}")
        print(f"[DEBUG] mode_format: {mode_format}")
        print(f"[DEBUG] suffix: {suffix}")

        # Match exported naming convention
        sanitized_name = sanitize_name(asset_name.replace('_gameasset', ''))
        full_name = f"{sanitized_name}_{mode_format}{suffix}"
        file_path = os.path.join(export_path, f"{full_name}{format_ext}")

        if not os.path.exists(file_path):
            self.report({'WARNING'}, f"File not found for asset '{asset_name}': {file_path}")
            print(f"[DEBUG] Looking for file: {file_path}")
            return

        print(f"[DEBUG] Importing {format_ext.upper()} for asset: {full_name}")
        objects_before = set(obj.name for obj in bpy.context.scene.objects)

        folder_path = os.path.join(export_path, f"{sanitized_name}_{mode_format}{suffix}")

        if format_ext == '.obj' and not os.path.exists(folder_path):
            self.report({'WARNING'}, f"OBJ sequence folder not found: {folder_path}")
            return

        if format_ext == '.obj' and assetify_settings.export_mode == 'ANIMATION':
            print(f"[DEBUG] Importing OBJ sequence as shape keys for {asset_name}")

            target_obj = context.active_object
            if not target_obj or target_obj.type != 'MESH':
                self.report({'WARNING'}, f"No active mesh object selected for importing {asset_name}")
                return

            # Load the OBJ sequence
            load_obj_sequence_as_shape_keys(target_obj, folder_path, full_name)
        else:
            # Standard imports for other formats
            file_path = os.path.join(export_path, f"{sanitized_name}_{mode_format}{suffix}{format_ext}")
            if format_ext == '.fbx':
                bpy.ops.import_scene.fbx(filepath=file_path)
            elif format_ext in ['.glb', '.gltf']:
                print("[DEBUG] Importing GLTF...")
                if not os.path.exists(file_path):
                    print(f"[ERROR] GLTF file not found: {file_path}")
                else:
                    try:
                        bpy.ops.import_scene.gltf(filepath=file_path)
                        print("[DEBUG] GLTF import executed successfully.")
                    except Exception as e:
                        print(f"[ERROR] GLTF import failed: {e}")
            elif format_ext == '.abc':
                bpy.ops.wm.alembic_import(filepath=file_path)
            elif format_ext == '.stl':
                bpy.ops.wm.stl_import(filepath=file_path)
            
        # Update object names in the scene
        objects_after = set(obj.name for obj in bpy.context.scene.objects)
        new_object_names = objects_after - objects_before

        for obj_name in new_object_names:
            obj = bpy.context.scene.objects.get(obj_name)
            if obj:
                # Append the full naming convention to the object name
                obj.name = full_name
                obj.location = (0.0, 0.0, 0.0)
                print(f"[DEBUG] Imported and renamed: {obj.name}")

    def sanitize_name(self, name):
        """Sanitize object name to create valid folder and file names."""
        return re.sub(r'[<>:"/\\|?*]', '_', name)

    def cancel(self, context):
        """Handle canceling the import operation."""
        context.window_manager.event_timer_remove(self._timer)
        remove_progress_bar(self)
        self.report({'INFO'}, "Import canceled.")

def check_for_obj_sequence(asset_name, export_path, extension):
    """
    Check if an OBJ sequence (with frame numbers) exists.

    Args:
        asset_name (str): Sanitized asset name.
        export_path (str): Path to the exported files.
        extension (str): File extension (.obj).

    Returns:
        bool: True if at least one OBJ file with a frame number exists.
    """
    # Look for files like Plane_OBJ_ANIM0001.obj, Plane_OBJ_ANIM0002.obj
    search_pattern = os.path.join(export_path, f"{asset_name}????{extension}")
    matching_files = glob.glob(search_pattern)

    if matching_files:
        print(f"[DEBUG] Found OBJ sequence for {asset_name}: {matching_files[0]}")
        return True
    else:
        print(f"[DEBUG] No OBJ sequence found for {asset_name} with pattern: {search_pattern}")
        return False

def load_obj_sequence_as_shape_keys(target_obj, export_path, full_name):
    """
    Loads an OBJ sequence and applies each as a shape key.

    Args:
        target_obj (bpy.types.Object): The object to apply the shape keys to.
        export_path (str): Path to the exported files.
        full_name (str): Base name for OBJ sequence files.
    """
    search_pattern = os.path.join(export_path, f"{full_name}????.obj")
    obj_files = sorted(glob.glob(search_pattern))

    if not obj_files:
        print(f"[WARNING] No OBJ sequence found with pattern: {search_pattern}")
        return

    print(f"[DEBUG] Found {len(obj_files)} OBJ files for {target_obj.name}")

    # ✅ Deselect all objects before importing
    bpy.ops.object.select_all(action='DESELECT')

    for obj_file in obj_files:
        frame_number = os.path.splitext(os.path.basename(obj_file))[0][-4:]
        import_obj_as_shape_key(obj_file, target_obj, frame_number)

    print(f"[INFO] Successfully imported OBJ sequence as shape keys.")

def import_obj_as_shape_key(filepath, target_obj, frame_number):
    """
    Imports a single OBJ file and applies it as a shape key.

    Args:
        filepath (str): File path of the OBJ file.
        target_obj (bpy.types.Object): Target object for shape keys.
        frame_number (str): Frame number for naming the shape key.
    """
    # Debug: File being imported
    print(f"[DEBUG] Importing OBJ file: {filepath}")

    # ✅ Ensure proper context for importing
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = target_obj

    # ✅ Import OBJ
    bpy.ops.import_scene.obj(filepath=filepath, split_mode='OFF')
    
    # ✅ Check if the OBJ imported
    imported_objects = bpy.context.selected_objects
    if not imported_objects:
        print(f"[ERROR] Failed to import {filepath}")
        return

    imported_obj = imported_objects[0]
    print(f"[DEBUG] Imported object: {imported_obj.name}")

    # ✅ Ensure vertex count matches
    if len(imported_obj.data.vertices) != len(target_obj.data.vertices):
        print(f"[WARNING] Vertex count mismatch. Skipping {filepath}.")
        bpy.data.objects.remove(imported_obj, do_unlink=True)
        return

    # ✅ Apply as shape key
    bpy.context.view_layer.objects.active = target_obj
    imported_obj.select_set(True)
    bpy.ops.object.join_shapes()

    # ✅ Rename shape key
    target_obj.data.shape_keys.key_blocks[-1].name = f"Frame_{frame_number}"

    # ✅ Cleanup
    bpy.data.objects.remove(imported_obj, do_unlink=True)
    print(f"[DEBUG] Applied {filepath} as shape key 'Frame_{frame_number}'")

def check_if_file_exists(asset_name, export_path, extension, mode_format="OBJ", suffix="_ANIM"):
    """
    Checks if the export file or OBJ sequence (starting with 0001) exists for the given asset.

    Args:
        asset_name (str): The name of the asset to check.
        export_path (str): The directory where exported files are stored.
        extension (str): The file extension to look for (e.g., '.obj').
        mode_format (str): The export format (e.g., 'OBJ', 'FBX').
        suffix (str): The suffix to append (_still or _ANIM).

    Returns:
        bool: True if the file or folder with 0001 exists, False otherwise.
    """
    sanitized_name = sanitize_name(asset_name.replace("_gameasset", ""))

    # OBJ Animation Sequence Check → Look in the dedicated subfolder for 0001
    if extension == ".obj" and suffix == "_ANIM":
        folder_name = f"{sanitized_name}_{mode_format}{suffix}"
        folder_path = os.path.join(export_path, folder_name)

        # Expect the first OBJ file to be 0001 (e.g., Plane_OBJ_ANIM0001.obj)
        first_frame_file = f"{sanitized_name}_{mode_format}{suffix}0001{extension}"
        first_frame_path = os.path.join(folder_path, first_frame_file)

        # Debug: Check if the first frame OBJ exists
        print(f"[DEBUG] Checking for first frame OBJ: {first_frame_path}")

        if os.path.isfile(first_frame_path):
            print(f"[DEBUG] Found first frame OBJ file: {first_frame_path}")
            return True
        else:
            print(f"[DEBUG] First frame OBJ not found: {first_frame_path}")
            return False

    # Standard file check for other formats (FBX, GLTF, etc.)
    else:
        file_name = f"{sanitized_name}_{mode_format}{suffix}{extension}"
        file_path = os.path.join(export_path, file_name)

        exists = os.path.isfile(file_path)
        print(f"[DEBUG] Checking file existence - Asset: {asset_name}, Path: {file_path}, Exists: {exists}")
        return exists

def update_bake_folder(self, context):
    """Update the export FBX path to match the bake folder when the bake folder changes."""
    if self.bake_folder:
        # Ensure that the bake folder is an absolute path
        bake_folder_abs = bpy.path.abspath(self.bake_folder)
        
        # Set the export path to the same folder as the bake folder with a default FBX filename
        self.export_fbx_path = bpy.path.abspath(os.path.join(bake_folder_abs))

def load_custom_icons():
    """Load custom icons from the specified file paths."""
    global custom_icons
    custom_icons = bpy.utils.previews.new()

    # Define paths to your custom icon images
    icons_dir = os.path.join(os.path.dirname(__file__), "icons")  # Icons directory within the add-on folder
    icon_files = {
        "youtube_icon": "youtube.png",
        "instagram_icon": "instagram.png",
        "x_icon": "x.png",
        "discord_icon": "discord.png",
        "patreon_icon": "patreon.png",
        "website_icon": "website.png",
        "tiktok_icon": "tiktok.png",
        "linkedin_icon": "linkedin.png",
    }

    # Load each icon, with error handling if the file is not found
    for icon_name, file_name in icon_files.items():
        icon_path = os.path.join(icons_dir, file_name)
        if os.path.exists(icon_path):
            custom_icons.load(icon_name, icon_path, 'IMAGE')
        else:
            print(f"Warning: Icon file not found - {file_name}")
    
def unload_custom_icons():
    """Unload custom icons when the add-on is unregistered."""
    global custom_icons
    if custom_icons is not None:
        bpy.utils.previews.remove(custom_icons)
        custom_icons = None

class ASSETIFY_UL_custom_attributes(bpy.types.UIList):
    """Custom UI list to display custom attributes with name, type, and domain."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        # Ensure we're dealing with a CustomAttributeItem
        custom_attribute = item

        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            # Display the attribute's name, type, and domain in a row
            row = layout.row(align=True)
            
            # Add a label for the index to help visually separate the items
            row.label(text=f"{index + 1}.", icon='DOT')
            
            # Name input with some padding
            row.prop(custom_attribute, "name", text="", emboss=True)
            
            # Add some spacing between elements
            row.separator(factor=0.5)
            
            # Type dropdown menu
            row.prop(custom_attribute, "type", text="", emboss=True)
            
            # Add some spacing between elements
            row.separator(factor=0.5)
            
            # Domain dropdown menu
            row.prop(custom_attribute, "domain", text="", emboss=True)

        elif self.layout_type in {'GRID'}:
            # Display the attribute name in grid layout
            layout.label(text=custom_attribute.name)

def add_custom_attributes_to_geometry(obj, custom_object):
    """
    Add necessary processing steps for geometry, even if custom attributes are disabled.
    """

    assetify_settings = bpy.context.scene.assetify_bake_settings

    # Ensure we're in object mode
    if bpy.context.active_object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # Perform UV unwrapping regardless of custom attribute settings
    # smart_uv_project(obj)

    # If custom attributes are enabled, proceed with setting up the geometry nodes
    if assetify_settings.enable_custom_attributes and assetify_settings.custom_attributes:
        # Add a Geometry Nodes modifier if it doesn't exist
        geo_nodes = obj.modifiers.get("CustomGeometryNodes")
        if not geo_nodes:
            geo_nodes = obj.modifiers.new(name="CustomGeometryNodes", type='NODES')

        # Create a new node group if one doesn't exist
        if not geo_nodes.node_group:
            geo_nodes.node_group = bpy.data.node_groups.new("CustomGeometryNodes", 'GeometryNodeTree')

        # Get the node group attached to the object
        node_tree = geo_nodes.node_group

        # Clear existing nodes
        node_tree.nodes.clear()

        # Create input and output nodes
        group_input = node_tree.nodes.new(type='NodeGroupInput')
        group_output = node_tree.nodes.new(type='NodeGroupOutput')

        # Add input and output sockets for geometry
        node_tree.interface.new_socket(name="GEO", in_out='INPUT', socket_type='NodeSocketGeometry')
        node_tree.interface.new_socket(name="GEO", in_out='OUTPUT', socket_type='NodeSocketGeometry')

        # Position the input and output nodes
        group_input.location = (-300, 0)
        group_output.location = (800, 0)

        # Create Object Info node
        object_info_node = node_tree.nodes.new(type='GeometryNodeObjectInfo')
        object_info_node.transform_space = 'RELATIVE'
        object_info_node.location = (-100, 100)
        object_info_node.inputs['Object'].default_value = custom_object  # Set the custom object

        # Get custom attributes from the add-on UI
        custom_attributes = assetify_settings.custom_attributes

        if custom_attributes:
            previous_node = group_input

            for index, custom_attribute in enumerate(custom_attributes):
                # Create nodes for the custom attribute
                named_attribute = node_tree.nodes.new(type='GeometryNodeInputNamedAttribute')
                sample_index_node = node_tree.nodes.new(type='GeometryNodeSampleIndex')
                store_named_attribute = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')

                # Set locations for the nodes
                y_offset = -index * 300  # Offset each node vertically
                named_attribute.location = (-100, y_offset)
                sample_index_node.location = (200, y_offset)
                store_named_attribute.location = (500, y_offset)

                # Configure the named attribute node
                named_attribute.inputs['Name'].default_value = custom_attribute.name
                named_attribute.data_type = custom_attribute.type

                # Configure the sample index node
                sample_index_node.data_type = custom_attribute.type
                sample_index_node.domain = custom_attribute.domain

                # Configure the store named attribute node
                store_named_attribute.data_type = custom_attribute.type
                store_named_attribute.inputs['Name'].default_value = custom_attribute.name
                store_named_attribute.domain = custom_attribute.domain

                # Link the nodes together
                node_tree.links.new(named_attribute.outputs['Attribute'], sample_index_node.inputs['Value'])
                node_tree.links.new(sample_index_node.outputs['Value'], store_named_attribute.inputs['Value'])
                node_tree.links.new(object_info_node.outputs['Geometry'], sample_index_node.inputs['Geometry'])

                # For the first attribute, connect the Group Input to the Store Named Attribute node's Geometry input
                if index == 0:
                    node_tree.links.new(group_input.outputs['GEO'], store_named_attribute.inputs['Geometry'])
                else:
                    # For subsequent attributes, link the previous Store Named Attribute node's output to the current one
                    node_tree.links.new(previous_node.outputs['Geometry'], store_named_attribute.inputs['Geometry'])

                # Update previous_node to be the current store_named_attribute node
                previous_node = store_named_attribute

            # Link the last store_named_attribute node to the group output
            node_tree.links.new(previous_node.outputs['Geometry'], group_output.inputs['GEO'])

        # Make sure the modifier is applied to the object
        obj.modifiers.update()

def update_enable_custom_attributes(self, context):
    """Update function to refresh the UI when custom attributes are enabled or disabled."""
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()

class ASSETIFY_OT_show_custom_attributes_info(bpy.types.Operator):
    """Shows an explanation of what 'Enable Custom Attributes' does."""
    bl_idname = "assetify.show_custom_attributes_info"
    bl_label = "Custom Attributes Info"

    def invoke(self, context, event):
        # This will call the draw function to display the popup
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.label(text="You can transfer geometry node modifier inputs")
        layout.label(text="that are used to control elements of materials")
        layout.label(text="directly to the materials before baking happens.")
        layout.label(text="This ensures you get to bake the assets with")
        layout.label(text="the customizations made in the geometry node")
        layout.label(text="modifier panel.")
        
        # Add some space before the buttons
        layout.separator()
        
        # Add "Show YouTube Tutorial" button
        layout.operator("wm.url_open", text="Show YouTube Tutorial").url = "https://www.youtube.com/watch?v=ZgdlRGKfEPA&ab_channel=Nino&t=24m00s"

    def execute(self, context):
        # This does nothing as the dialog box is used for displaying info
        return {'FINISHED'}

class CustomAttributeItem(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(
        name="Attribute Name",
        description="Name of the attribute to transfer",
        default=""
    )
    
    type: bpy.props.EnumProperty(
        name="Type",
        description="Data type of the attribute",
        items=[
            ('FLOAT', "Float", ""),
            ('FLOAT_VECTOR', "Float Vector", ""),
            ('FLOAT_COLOR', "Float Color", ""),
            ('INT', "Integer", ""),
            ('STRING', "String", "")
        ],
        default='FLOAT'
    )
    
    domain: bpy.props.EnumProperty(
        name="Domain",
        description="Domain of the attribute",
        items=[
            ('POINT', "Point", ""),
            ('EDGE', "Edge", ""),
            ('FACE', "Face", ""),
            ('CORNER', "Corner", "")
        ],
        default='POINT'
    )

class AssetCollectionItem(bpy.types.PropertyGroup):
    """PropertyGroup for managing asset collections"""
    
    name: bpy.props.StringProperty(
        name="Name",
        description="Name of the baked collection",
        default=""
    )
    
    collection: bpy.props.PointerProperty(
        name="Collection",
        type=bpy.types.Collection,
        description="Collection to include in the asset processing"
    )
    
    is_baked: bpy.props.BoolProperty(
        name="Is Baked",
        description="Indicates if the collection has been baked",
        default=False
    )
    
    is_fbx_exported: bpy.props.BoolProperty(
        name="Is FBX Exported",
        description="Indicates if the collection has been exported as FBX",
        default=False
    )
    
    include_in_send: bpy.props.BoolProperty(
        name="Include in Send",
        description="Flag to include this collection in send operations",
        default=False
    )
    
    assets: bpy.props.CollectionProperty(type=BakedAssetItem)

class ASSETIFY_UL_asset_collections(bpy.types.UIList):
    """Custom UI list to display asset collections with a single collection input per item."""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        # Ensure we're dealing with an AssetCollectionItem
        collection_item = item

        row = layout.row(align=True)
        # Use a single row for the collection input
        row.prop_search(
            collection_item,                      # The PropertyGroup item
            "collection",                         # The property to assign
            bpy.data,                             # The data to search within
            "collections",                        # The collection to search
            text="",                              # No label text
            icon='OUTLINER_COLLECTION'            # Icon representing collections
        )

        # Add a remove button for this specific item
        row.operator(
            "assetify.remove_asset_collection",   # Operator to call
            text="",                              # No text for the button
            icon='REMOVE'                         # Use a remove icon
        ).index = index  # Pass the index to the operator

class ASSETIFY_OT_add_asset_collection(bpy.types.Operator):
    """Add a new asset collection"""
    bl_idname = "assetify.add_asset_collection"
    bl_label = "Add Asset Collection"

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        new_item = assetify_settings.asset_collections.add()
        new_item.collection = None  # Explicitly set to None (optional)
        assetify_settings.active_asset_collection_index = len(assetify_settings.asset_collections) - 1
        return {'FINISHED'}
    
class ASSETIFY_OT_remove_asset_collection(bpy.types.Operator):
    """Remove the selected asset collection"""
    bl_idname = "assetify.remove_asset_collection"
    bl_label = "Remove Asset Collection"

    index: bpy.props.IntProperty()  # Property to store the index

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        index = self.index

        if 0 <= index < len(assetify_settings.asset_collections):
            assetify_settings.asset_collections.remove(index)
            assetify_settings.active_asset_collection_index = min(max(0, index - 1), len(assetify_settings.asset_collections) - 1)

        return {'FINISHED'}

# Update the AssetifyBakeSettings to include the list of collections
def update_assetify_bake_settings():
    bpy.types.Scene.assetify_bake_settings.asset_collections = bpy.props.CollectionProperty(type=AssetCollectionItem)
    bpy.types.Scene.assetify_bake_settings.active_asset_collection_index = bpy.props.IntProperty(default=0)

def customize_color(obj, custom_object):

    # Ensure we're in object mode
    if bpy.context.active_object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # Get the active object
    obj = bpy.context.active_object

    # Add a Geometry Nodes modifier if it doesn't exist
    geo_nodes = obj.modifiers.new(name="GeometryNodes", type='NODES')

    # Create a new node group if one doesn't exist
    if not geo_nodes.node_group:
        geo_nodes.node_group = bpy.data.node_groups.new("Geometry Nodes", 'GeometryNodeTree')

    # Get the node group attached to the active object dynamically
    node_tree = geo_nodes.node_group

    # Clear existing nodes (if any)
    node_tree.nodes.clear()

    # Create new input and output nodes
    group_input = node_tree.nodes.new(type='NodeGroupInput')
    group_output = node_tree.nodes.new(type='NodeGroupOutput')

    # Set their locations in the node editor (optional, for better layout in UI)
    group_input.location = (-300, 0)
    group_output.location = (2400, 0)

    # Add input and output sockets for geometry and object
    node_tree.interface.new_socket(name="GEO", in_out='INPUT', socket_type='NodeSocketGeometry')
    node_tree.interface.new_socket(name="GEO", in_out='OUTPUT', socket_type='NodeSocketGeometry')

    # Create Object Info node
    object_info_node = node_tree.nodes.new(type='GeometryNodeObjectInfo')
    object_info_node.transform_space = 'RELATIVE'
    object_info_node.location = (-100, 100)
    object_info_node.inputs['Object'].default_value = custom_object  # Set the custom object

    # Create a Sample Index node (set to color)
    sample_index_node_1 = node_tree.nodes.new(type='GeometryNodeSampleIndex')
    sample_index_node_1.location = (100, 100)
    sample_index_node_1.data_type = 'FLOAT_COLOR'  # Set to sample color

    # Create a Sample Index node (set to color)
    sample_index_node_2 = node_tree.nodes.new(type='GeometryNodeSampleIndex')
    sample_index_node_2.location = (100, -100)
    sample_index_node_2.data_type = 'FLOAT'  # Set to sample color
    
    # Create a Sample Index node (set to color)
    sample_index_node_3 = node_tree.nodes.new(type='GeometryNodeSampleIndex')
    sample_index_node_3.location = (100, -300)
    sample_index_node_3.data_type = 'FLOAT_COLOR'  # Set to sample color
    
    # Create a Sample Index node (set to color)
    sample_index_node_4 = node_tree.nodes.new(type='GeometryNodeSampleIndex')
    sample_index_node_4.location = (100, -500)
    sample_index_node_4.data_type = 'FLOAT'  # Set to sample color
    
    # Create a Sample Index node (set to color)
    sample_index_node_5 = node_tree.nodes.new(type='GeometryNodeSampleIndex')
    sample_index_node_5.location = (100, -700)
    sample_index_node_5.data_type = 'FLOAT'  # Set to sample color
    
    # Create a Sample Index node (set to color)
    sample_index_node_6 = node_tree.nodes.new(type='GeometryNodeSampleIndex')
    sample_index_node_6.location = (100, -900)
    sample_index_node_6.data_type = 'FLOAT'  # Set to sample color
    
        # Create a Sample Index node (set to color)
    sample_index_node_7 = node_tree.nodes.new(type='GeometryNodeSampleIndex')
    sample_index_node_7.location = (100, -1100)
    sample_index_node_7.data_type = 'FLOAT'  # Set to sample color
    
        # Create a Sample Index node (set to color)
    sample_index_node_8 = node_tree.nodes.new(type='GeometryNodeSampleIndex')
    sample_index_node_8.location = (100, -1300)
    sample_index_node_8.data_type = 'FLOAT'  # Set to sample color
    
        # Create a Sample Index node (set to color)
    sample_index_node_9 = node_tree.nodes.new(type='GeometryNodeSampleIndex')
    sample_index_node_9.location = (100, -1500)
    sample_index_node_9.data_type = 'FLOAT'  # Set to sample color
    
        # Create a Sample Index node (set to color)
    sample_index_node_10 = node_tree.nodes.new(type='GeometryNodeSampleIndex')
    sample_index_node_10.location = (100, -1700)
    sample_index_node_10.data_type = 'FLOAT'  # Set to sample color
    
        # Create a Sample Index node (set to color)
    sample_index_node_11 = node_tree.nodes.new(type='GeometryNodeSampleIndex')
    sample_index_node_11.location = (100, -1900)
    sample_index_node_11.data_type = 'FLOAT'  # Set to sample color
    
            # Create a Sample Index node (set to color)
    sample_index_node_12 = node_tree.nodes.new(type='GeometryNodeSampleIndex')
    sample_index_node_12.location = (100, -2100)
    sample_index_node_12.data_type = 'FLOAT'  # Set to sample color

    # Create the first Store Named Attribute node for color
    store_named_attribute_1 = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')
    store_named_attribute_1.location = (275, 0)  # Adjust location for better layout
    store_named_attribute_1.data_type = 'FLOAT_COLOR'
    store_named_attribute_1.inputs["Name"].default_value = 'mosscolor'

    # Create the second Store Named Attribute node (for scalar attribute)
    store_named_attribute_2 = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')
    store_named_attribute_2.location = (450, 0)  # Place it next to the first one
    store_named_attribute_2.inputs["Value"].default_value = 2.0  # Set default value for second attribute
    store_named_attribute_2.inputs["Name"].default_value = 'mosscolorvalue'
    
    # Create the first Store Named Attribute node for color
    store_named_attribute_3 = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')
    store_named_attribute_3.location = (625, 0)  # Adjust location for better layout
    store_named_attribute_3.data_type = 'FLOAT_COLOR'
    store_named_attribute_3.inputs["Name"].default_value = 'extraassetcolor'
    
    # Create the first Store Named Attribute node for color
    store_named_attribute_4 = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')
    store_named_attribute_4.location = (800, 0)  # Adjust location for better layout
    store_named_attribute_4.data_type = 'FLOAT'
    store_named_attribute_4.inputs["Name"].default_value = 'extraassetcolorvalue'
    
    # Create the first Store Named Attribute node for color
    store_named_attribute_5 = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')
    store_named_attribute_5.location = (975, 0)  # Adjust location for better layout
    store_named_attribute_5.data_type = 'FLOAT'
    store_named_attribute_5.inputs["Name"].default_value = 'dryness3'
    
    # Create the first Store Named Attribute node for color
    store_named_attribute_6 = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')
    store_named_attribute_6.location = (1150, 0)  # Adjust location for better layout
    store_named_attribute_6.data_type = 'FLOAT'
    store_named_attribute_6.inputs["Name"].default_value = 'assetdryness'
    
        # Create the first Store Named Attribute node for color
    store_named_attribute_7 = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')
    store_named_attribute_7.location = (1325, 0)  # Adjust location for better layout
    store_named_attribute_7.data_type = 'FLOAT'
    store_named_attribute_7.inputs["Name"].default_value = 'dryness1'
    
        # Create the first Store Named Attribute node for color
    store_named_attribute_8 = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')
    store_named_attribute_8.location = (1500, 0)  # Adjust location for better layout
    store_named_attribute_8.data_type = 'FLOAT'
    store_named_attribute_8.inputs["Name"].default_value = 'dryness2'
    
        # Create the first Store Named Attribute node for color
    store_named_attribute_9 = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')
    store_named_attribute_9.location = (1675, 0)  # Adjust location for better layout
    store_named_attribute_9.data_type = 'FLOAT'
    store_named_attribute_9.inputs["Name"].default_value = 'dryness4'
    
        # Create the first Store Named Attribute node for color
    store_named_attribute_10 = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')
    store_named_attribute_10.location = (1850, 0)  # Adjust location for better layout
    store_named_attribute_10.data_type = 'FLOAT'
    store_named_attribute_10.inputs["Name"].default_value = 'dryness5'
    
        # Create the first Store Named Attribute node for color
    store_named_attribute_11 = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')
    store_named_attribute_11.location = (2025, 0)  # Adjust location for better layout
    store_named_attribute_11.data_type = 'FLOAT'
    store_named_attribute_11.inputs["Name"].default_value = 'dryness6'
    
            # Create the first Store Named Attribute node for color
    store_named_attribute_12 = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')
    store_named_attribute_12.location = (2200, 0)  # Adjust location for better layout
    store_named_attribute_12.data_type = 'FLOAT'
    store_named_attribute_12.inputs["Name"].default_value = 'dryness7'

    named_attribute_1 = node_tree.nodes.new(type='GeometryNodeInputNamedAttribute')
    named_attribute_1.location = (-100, -120)  # Place it next to the first one
    named_attribute_1.data_type = 'FLOAT_COLOR'
    named_attribute_1.inputs["Name"].default_value = 'mosscolor'

    named_attribute_2 = node_tree.nodes.new(type='GeometryNodeInputNamedAttribute')
    named_attribute_2.location = (-100, -250)  # Place it next to the first one
    named_attribute_2.data_type = 'FLOAT'
    named_attribute_2.inputs["Name"].default_value = 'mosscolorvalue'
    
    named_attribute_3 = node_tree.nodes.new(type='GeometryNodeInputNamedAttribute')
    named_attribute_3.location = (-100, -380)  # Place it next to the first one
    named_attribute_3.data_type = 'FLOAT_COLOR'
    named_attribute_3.inputs["Name"].default_value = 'extraassetcolor'
    
    named_attribute_4 = node_tree.nodes.new(type='GeometryNodeInputNamedAttribute')
    named_attribute_4.location = (-100, -510)  # Place it next to the first one
    named_attribute_4.data_type = 'FLOAT'
    named_attribute_4.inputs["Name"].default_value = 'extraassetcolorvalue'
    
    named_attribute_5 = node_tree.nodes.new(type='GeometryNodeInputNamedAttribute')
    named_attribute_5.location = (-100, -640)  # Place it next to the first one
    named_attribute_5.data_type = 'FLOAT'
    named_attribute_5.inputs["Name"].default_value = 'dryness3'
    
    named_attribute_6 = node_tree.nodes.new(type='GeometryNodeInputNamedAttribute')
    named_attribute_6.location = (-100, -770)  # Place it next to the first one
    named_attribute_6.data_type = 'FLOAT'
    named_attribute_6.inputs["Name"].default_value = 'assetdryness'
    
    named_attribute_7 = node_tree.nodes.new(type='GeometryNodeInputNamedAttribute')
    named_attribute_7.location = (-100, -900)  # Place it next to the first one
    named_attribute_7.data_type = 'FLOAT'
    named_attribute_7.inputs["Name"].default_value = 'dryness1'
    
    named_attribute_8 = node_tree.nodes.new(type='GeometryNodeInputNamedAttribute')
    named_attribute_8.location = (-100, -1030)  # Place it next to the first one
    named_attribute_8.data_type = 'FLOAT'
    named_attribute_8.inputs["Name"].default_value = 'dryness2'
    
    named_attribute_9 = node_tree.nodes.new(type='GeometryNodeInputNamedAttribute')
    named_attribute_9.location = (-100, -1160)  # Place it next to the first one
    named_attribute_9.data_type = 'FLOAT'
    named_attribute_9.inputs["Name"].default_value = 'dryness4'
    
    named_attribute_10 = node_tree.nodes.new(type='GeometryNodeInputNamedAttribute')
    named_attribute_10.location = (-100, -1290)  # Place it next to the first one
    named_attribute_10.data_type = 'FLOAT'
    named_attribute_10.inputs["Name"].default_value = 'dryness5'
    
    named_attribute_11 = node_tree.nodes.new(type='GeometryNodeInputNamedAttribute')
    named_attribute_11.location = (-100, -1420)  # Place it next to the first one
    named_attribute_11.data_type = 'FLOAT'
    named_attribute_11.inputs["Name"].default_value = 'dryness6'
    
    named_attribute_12 = node_tree.nodes.new(type='GeometryNodeInputNamedAttribute')
    named_attribute_12.location = (-100, -1550)  # Place it next to the first one
    named_attribute_12.data_type = 'FLOAT'
    named_attribute_12.inputs["Name"].default_value = 'dryness7'

    node_tree.links.new(group_input.outputs['GEO'], store_named_attribute_1.inputs['Geometry'])

    node_tree.links.new(object_info_node.outputs['Geometry'], sample_index_node_1.inputs['Geometry'])
    node_tree.links.new(object_info_node.outputs['Geometry'], sample_index_node_2.inputs['Geometry'])
    node_tree.links.new(object_info_node.outputs['Geometry'], sample_index_node_3.inputs['Geometry'])
    node_tree.links.new(object_info_node.outputs['Geometry'], sample_index_node_4.inputs['Geometry'])
    node_tree.links.new(object_info_node.outputs['Geometry'], sample_index_node_5.inputs['Geometry'])
    node_tree.links.new(object_info_node.outputs['Geometry'], sample_index_node_6.inputs['Geometry'])
    node_tree.links.new(object_info_node.outputs['Geometry'], sample_index_node_7.inputs['Geometry'])
    node_tree.links.new(object_info_node.outputs['Geometry'], sample_index_node_8.inputs['Geometry'])
    node_tree.links.new(object_info_node.outputs['Geometry'], sample_index_node_9.inputs['Geometry'])
    node_tree.links.new(object_info_node.outputs['Geometry'], sample_index_node_10.inputs['Geometry'])
    node_tree.links.new(object_info_node.outputs['Geometry'], sample_index_node_11.inputs['Geometry'])
    node_tree.links.new(object_info_node.outputs['Geometry'], sample_index_node_12.inputs['Geometry'])

    node_tree.links.new(named_attribute_1.outputs['Attribute'], sample_index_node_1.inputs['Value'])
    node_tree.links.new(named_attribute_2.outputs['Attribute'], sample_index_node_2.inputs['Value'])
    node_tree.links.new(named_attribute_3.outputs['Attribute'], sample_index_node_3.inputs['Value'])
    node_tree.links.new(named_attribute_4.outputs['Attribute'], sample_index_node_4.inputs['Value'])
    node_tree.links.new(named_attribute_5.outputs['Attribute'], sample_index_node_5.inputs['Value'])
    node_tree.links.new(named_attribute_6.outputs['Attribute'], sample_index_node_6.inputs['Value'])
    node_tree.links.new(named_attribute_7.outputs['Attribute'], sample_index_node_7.inputs['Value'])
    node_tree.links.new(named_attribute_8.outputs['Attribute'], sample_index_node_8.inputs['Value'])
    node_tree.links.new(named_attribute_9.outputs['Attribute'], sample_index_node_9.inputs['Value'])
    node_tree.links.new(named_attribute_10.outputs['Attribute'], sample_index_node_10.inputs['Value'])
    node_tree.links.new(named_attribute_11.outputs['Attribute'], sample_index_node_11.inputs['Value'])
    node_tree.links.new(named_attribute_12.outputs['Attribute'], sample_index_node_12.inputs['Value'])

    node_tree.links.new(sample_index_node_1.outputs['Value'], store_named_attribute_1.inputs['Value'])
    node_tree.links.new(sample_index_node_2.outputs['Value'], store_named_attribute_2.inputs['Value'])
    node_tree.links.new(sample_index_node_3.outputs['Value'], store_named_attribute_3.inputs['Value'])
    node_tree.links.new(sample_index_node_4.outputs['Value'], store_named_attribute_4.inputs['Value'])
    node_tree.links.new(sample_index_node_5.outputs['Value'], store_named_attribute_5.inputs['Value'])
    node_tree.links.new(sample_index_node_6.outputs['Value'], store_named_attribute_6.inputs['Value'])
    node_tree.links.new(sample_index_node_7.outputs['Value'], store_named_attribute_7.inputs['Value'])
    node_tree.links.new(sample_index_node_8.outputs['Value'], store_named_attribute_8.inputs['Value'])
    node_tree.links.new(sample_index_node_9.outputs['Value'], store_named_attribute_9.inputs['Value'])
    node_tree.links.new(sample_index_node_10.outputs['Value'], store_named_attribute_10.inputs['Value'])
    node_tree.links.new(sample_index_node_11.outputs['Value'], store_named_attribute_11.inputs['Value'])
    node_tree.links.new(sample_index_node_12.outputs['Value'], store_named_attribute_12.inputs['Value'])

    node_tree.links.new(store_named_attribute_1.outputs['Geometry'], store_named_attribute_2.inputs['Geometry'])
    node_tree.links.new(store_named_attribute_2.outputs['Geometry'], store_named_attribute_3.inputs['Geometry'])
    node_tree.links.new(store_named_attribute_3.outputs['Geometry'], store_named_attribute_4.inputs['Geometry'])
    node_tree.links.new(store_named_attribute_4.outputs['Geometry'], store_named_attribute_5.inputs['Geometry'])
    node_tree.links.new(store_named_attribute_5.outputs['Geometry'], store_named_attribute_6.inputs['Geometry'])
    node_tree.links.new(store_named_attribute_6.outputs['Geometry'], store_named_attribute_7.inputs['Geometry'])
    node_tree.links.new(store_named_attribute_7.outputs['Geometry'], store_named_attribute_8.inputs['Geometry'])
    node_tree.links.new(store_named_attribute_8.outputs['Geometry'], store_named_attribute_9.inputs['Geometry'])
    node_tree.links.new(store_named_attribute_9.outputs['Geometry'], store_named_attribute_10.inputs['Geometry'])
    node_tree.links.new(store_named_attribute_10.outputs['Geometry'], store_named_attribute_11.inputs['Geometry'])
    node_tree.links.new(store_named_attribute_11.outputs['Geometry'], store_named_attribute_12.inputs['Geometry'])

    node_tree.links.new(store_named_attribute_12.outputs['Geometry'], group_output.inputs['GEO'])

    # Add custom attribute nodes if enabled
    assetify_settings = bpy.context.scene.assetify_bake_settings
    if assetify_settings.enable_custom_attributes:
        for custom_attribute in assetify_settings.custom_attributes:
            # Create nodes to sample the custom attribute from the emitter
            sample_node = node_tree.nodes.new(type='GeometryNodeSampleIndex')
            sample_node.data_type = custom_attribute.type
            sample_node.location = (100, -2200)  # Adjust location as needed

            store_node = node_tree.nodes.new(type='GeometryNodeStoreNamedAttribute')
            store_node.data_type = custom_attribute.type
            store_node.inputs["Name"].default_value = custom_attribute.name
            store_node.location = (400, -2200)  # Adjust location as needed

            # Connect the sampled attribute to the store node
            node_tree.links.new(sample_node.outputs['Value'], store_node.inputs['Value'])
            node_tree.links.new(store_node.outputs['Geometry'], group_output.inputs['GEO'])

class AssetifyPreferences(bpy.types.AddonPreferences):
    """Demo bare-bones preferences"""
    bl_idname = __package__

    # Addon updater preferences.

    auto_check_update: bpy.props.BoolProperty(
        name="Auto-check for Updates",
        description="If enabled, checks for updates automatically at intervals.",
        default=True,
    )

    updater_interval_months: bpy.props.IntProperty(
        name='Months',
        description="Number of months between checking for updates",
        default=0,
        min=0)

    updater_interval_days: bpy.props.IntProperty(
        name='Days',
        description="Number of days between checking for updates",
        default=7,
        min=0,
        max=31)

    updater_interval_hours: bpy.props.IntProperty(
        name='Hours',
        description="Number of hours between checking for updates",
        default=0,
        min=0,
        max=23)

    updater_interval_minutes: bpy.props.IntProperty(
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
        
def apply_global_animation_setting(assetify_settings):
    """
    Apply the global animation setting to all selected assets.
    """
    for asset in assetify_settings.baked_assets:
        if asset.include_in_send:  # Only apply to selected assets
            asset.process_animations = assetify_settings.process_animations_global
            print(f"[DEBUG] Set animation processing for '{asset.name}' to {assetify_settings.process_animations_global}.")        

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
    
    # New property for the Asset List collapsible menu
    show_asset_list_menu: bpy.props.BoolProperty(
        name="Show Asset List Menu",
        description="Toggle the visibility of the Asset List menu",
        default=False
    )
    
    show_bake_mode_menu: bpy.props.BoolProperty(
        name="Show Bake Mode Menu",
        description="Toggle visibility of the bake mode settings menu",
        default=True
    )
    
    texturebake_mode: bpy.props.EnumProperty(
        name="Texture Bake Mode",
        description="Choose whether to bake still textures or an animated sequence",
        items=[
            ('STILL', "Still", "Bake a single set of textures"),
            ('ANIMATION', "Animation", "Bake textures as a sequence (one set per frame)")
        ],
        default='STILL',
    )
    
    bake_mode: bpy.props.EnumProperty(
        name="Bake Mode",
        description="Choose between baking for still or animation mode",
        items=[
            ('STILL', "Still", "Bake for still assets"),
            ('ANIMATION', "Animation", "Bake animations for assets")
        ],
        default='STILL'
    )
    
    export_mode: bpy.props.EnumProperty(
        name="Export Mode",
        description="Switch between Still and Animation exporting",
        items=[
            ('STILL', "Still", "Export still models only"),
            ('ANIMATION', "Animation", "Export models with animations")
        ],
        default='STILL',
    )

    skip_uv_unwrap: bpy.props.BoolProperty(
        name="Skip UV Unwrapping",
        description="Skip the UV unwrapping process during baking",
        default=False
    )
    
    export_format: bpy.props.EnumProperty(
        name="Export Format",
        description="Choose the export format for assets",
        items=[
            ('FBX', "FBX", "Export assets as FBX"),
            ('OBJ', "OBJ", "Export assets as OBJ"),
            ('GLTF', "GLTF", "Export assets as GLTF"),
            ('STL', "STL", "Export assets as STL")
        ],
        default='FBX',
    )
    
    animation_export_format: bpy.props.EnumProperty(
        name="Animation Export Format",
        description="Choose the format for exporting animations",
        items=[
            ('FBX', "FBX (.fbx)", "Export to FBX format"),
            ('ALEMBIC', "Alembic (.abc)", "Export to Alembic format"),
            ('GLTF', "glTF (.gltf .glb)", "Export to glTF format"),
            ('OBJ', "Wavefront (.obj)", "Export to OBJ format"),
            ('MDD', "Point Cache (.mdd)", "Export to MDD format"),
        ],
        default='FBX',
    )
    
    import_format: bpy.props.EnumProperty(
        name="Import Format",
        description="Choose the format to import assets",
        items=[
            ('FBX', "FBX", "Import assets as FBX"),
            ('OBJ', "OBJ", "Import assets as OBJ"),
            ('GLTF', "GLTF", "Import assets as GLTF"),
            ('STL', "STL", "Import assets as STL")
        ],
        default='FBX',  # Default import format
        update=lambda self, context: update_export_status(context)
    )
    
    # Import formats for animation mode
    animation_import_format: bpy.props.EnumProperty(
        name="Animation Import Format",
        description="Choose the format for importing animations",
        items=[
            ('FBX', "FBX", "Import FBX files"),
            ('ALEMBIC', "Alembic", "Import Alembic files"),
            ('GLTF', "glTF", "Import glTF files"),
            ('OBJ', "Wavefront (OBJ)", "Import OBJ files"),
        ],
        default='FBX',
    )
    
    skip_save_check: bpy.props.BoolProperty(
        name="Skip Save Check",
        description="Skip checking if the file is saved before executing",
        default=False
    )
    
    disable_original_collections: bpy.props.BoolProperty(
        name="Disable Original Collections",
        description="Exclude original collections from the view layer after conversion",
        default=True,
    )
    
        # Properties to control fold-out menus
    bake_menu_expanded: bpy.props.BoolProperty(
        name="Bake Settings Expanded",
        description="Toggle the visibility of Bake Settings",
        default=False  # Expanded by default
    )

    export_menu_expanded: bpy.props.BoolProperty(
        name="Export Settings Expanded",
        description="Toggle the visibility of Export Settings",
        default=False  # Collapsed by default
    )
    
    use_tiling: bpy.props.BoolProperty(
        name="Use Tiling",
        description="Enable or disable tiling for baking",
        default=False  # Default to enabled
    )
    
    # Add property for selecting CPU or GPU rendering
    render_device: bpy.props.EnumProperty(
        name="Render Device",
        description="Choose the render device for baking",
        items=[
            ('CPU', "CPU", "Use the CPU for baking"),
            ('GPU', "GPU", "Use the GPU for baking"),
        ],
        default='GPU'  # Default to GPU
    )

    # Add property for setting the tile size
    tile_size: bpy.props.IntProperty(
        name="Tile Size",
        description="Set the tile size for baking",
        default=64,  # Default tile size
        min=16,  # Minimum tile size
        max=512  # Maximum tile size
    )
    
    platform_target: bpy.props.EnumProperty(
        name="Platform Target",
        description="Select the target platform (Unreal Engine or Unity)",
        items=[
            ('UE5', "Unreal Engine 5", "Settings for Unreal Engine 5"),
            ('UNITY', "Unity", "Settings for Unity"),
        ],
        default='UE5'  # Default to Unreal Engine 5
    )

    use_mossify: bpy.props.BoolProperty(
        name="Use Mossify",
        description="Toggle Mossify Mode",
        default=False
    )

    bake_samples: bpy.props.IntProperty(
        name="Bake Samples",
        description="Number of samples for baking",
        default=2,
        min=1,
        max=4096
    )

    bake_folder: bpy.props.StringProperty(
        name="Bake Folder",
        description="Folder to save baked textures",
        default="//baked_textures",
        subtype='DIR_PATH',
        update=update_bake_folder  # Update export path when this changes
    )

    export_fbx_path: bpy.props.StringProperty(
        name="Export FBX Path",
        description="File path to export the FBX",
        default="//",
        subtype='FILE_PATH'
    )

    fbx_export_collection: bpy.props.PointerProperty(
        name="FBX Export Collection",
        description="Select the collection to export as FBX",
        type=bpy.types.Collection
    )

    target_collection: bpy.props.PointerProperty(
        name="Target Collection",
        description="Select the collection to convert to game-ready",
        type=bpy.types.Collection
    )

    assets_baked: bpy.props.BoolProperty(
        name="Assets Baked",
        description="Indicates if the assets have been set and baked",
        default=False
    )
    
    asset_mode: bpy.props.EnumProperty(
        name="Mode",
        description="Choose between Asset and Collection modes",
        items=[
            ('ASSET', "Asset", "Asset Mode"),
            ('COLLECTION', "Collection", "Collection Mode"),
        ],
        default='COLLECTION'
    )
    
    target_collection: bpy.props.PointerProperty(
        name="Target Collection",
        description="Select the collection to work with in collection mode",
        type=bpy.types.Collection
    )
    
    enable_custom_attributes: bpy.props.BoolProperty(
        name="Transfer Attributes to Materials",
        description="Enable transferring custom attributes",
        default=False,
        update=lambda self, context: update_enable_custom_attributes(context)
    )
    
    asset_collections: bpy.props.CollectionProperty(type=AssetCollectionItem)
    active_asset_collection_index: bpy.props.IntProperty(default=0)
    
    custom_attributes: bpy.props.CollectionProperty(type=CustomAttributeItem)
    active_custom_attribute_index: bpy.props.IntProperty(default=0)
    
    baked_assets: bpy.props.CollectionProperty(type=BakedAssetItem)
    active_baked_asset_index: bpy.props.IntProperty(default=0)
    
    baked_collections: bpy.props.CollectionProperty(type=BakedCollectionItem)
    active_baked_collection_index: bpy.props.IntProperty()

    asset_include_in_send_cache: bpy.props.CollectionProperty(
        type=bpy.types.PropertyGroup,  # Or a custom type
        name="Asset Include in Send Cache",
        description="Cache to store the state of the 'include_in_send' flag for assets"
    )

    collection_include_in_send_cache: bpy.props.CollectionProperty(
        type=bpy.types.PropertyGroup,  # Or a custom type
        name="Collection Include in Send Cache",
        description="Cache to store the state of the 'include_in_send' flag for collections"
    )
    
    unreal_editor_path: bpy.props.StringProperty(
        name="Unreal Editor Path",
        description="Path to UnrealEditor.exe",
        default="",
        subtype='FILE_PATH'
    )

    unreal_project_path: bpy.props.StringProperty(
        name="Unreal Project Path",
        description="Path to your Unreal project file (.uproject)",
        default="",
        subtype='FILE_PATH'
    )

    main_collection: bpy.props.StringProperty(
        name="Main Collection",
        description="Name of the main collection to track game-ready assets",
        default=""
    )    

    # Use a Python list to store the main collection assets
    main_collection_assets = []

    def get_asset_by_name(self, name):
        """Retrieve an asset by its name."""
        for asset in self.baked_assets:
            if asset.name == name:
                return asset
        return None

    def get_collection_by_name(self, name):
        """Retrieve a collection by its name."""
        for collection in self.baked_collections:
            if collection.name == name:
                return collection
        return None
    
def set_active_3d_view():
    """Ensures the 3D View is active to display the overlay."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'WINDOW':
                        # Find the VIEW_3D space
                        for space in area.spaces:
                            if space.type == 'VIEW_3D':
                                # Complete override context
                                override = {
                                    'window': window,
                                    'screen': window.screen,
                                    'area': area,
                                    'region': region,
                                    'space': space
                                }
                                return override
    return None

def draw_progress_bar(operator, space, region):
    """Draws a simple progress bar in the Blender UI with informational texts below."""
    try:
        # Get window width and height from the region
        width = region.width
        height = region.height

        # Position of the progress bar
        bar_width = 300
        bar_height = 30
        x_pos = (width - bar_width) / 2
        y_pos = height - 100  # 100px from the top

        # Background for the progress bar (gray)
        vertices = [
            (x_pos, y_pos),
            (x_pos + bar_width, y_pos),
            (x_pos + bar_width, y_pos + bar_height),
            (x_pos, y_pos + bar_height)
        ]

        # GPU shader to draw rectangles
        shader_type = 'UNIFORM_COLOR'  # Valid shader type
        shader = gpu.shader.from_builtin(shader_type)
        batch = batch_for_shader(shader, 'TRI_FAN', {"pos": vertices})

        # Drawing background (gray)
        shader.bind()
        shader.uniform_float("color", (0.2, 0.2, 0.2, 0.8))  # Gray color
        batch.draw(shader)

        # Drawing progress (green)
        progress_vertices = [
            (x_pos, y_pos),
            (x_pos + bar_width * operator.progress_value, y_pos),
            (x_pos + bar_width * operator.progress_value, y_pos + bar_height),
            (x_pos, y_pos + bar_height)
        ]

        progress_batch = batch_for_shader(shader, 'TRI_FAN', {"pos": progress_vertices})
        shader.uniform_float("color", (0.0, 0.8, 0.0, 0.8))  # Green color
        progress_batch.draw(shader)

        # Set text color to white
        blf.color(0, 1.0, 1.0, 1.0, 1.0)  # White color (RGBA)

        # Draw progress percentage on top of the progress bar
        blf.position(0, x_pos + 10, y_pos + 5, 0)
        blf.size(0, 24)  # Set font size
        blf.draw(0, f"Progress: {int(operator.progress_value * 100)}%")

        # Draw current operation below the progress bar
        if operator.current_operation:
            blf.position(0, x_pos, y_pos - 30, 0)  # 30px below the progress bar
            blf.size(0, 20)  # Slightly smaller font
            blf.draw(0, operator.current_operation)

        # Draw current subprocess below the current operation
        if operator.current_sub_operation:
            blf.position(0, x_pos, y_pos - 60, 0)  # 60px below the progress bar
            blf.size(0, 18)  # Smaller font
            blf.draw(0, operator.current_sub_operation)

    except Exception as e:
        print(f"Error in draw_progress_bar: {e}")

def update_progress(operator):
    """Updates the progress based on bake_progress and redraws the overlay."""
    if operator.total_bake_steps > 0:
        operator.progress_value = operator.bake_progress / operator.total_bake_steps
        operator.progress_value = min(operator.progress_value, 1.0)
    else:
        operator.progress_value = 0.0

    if operator.progress_value >= 1.0:
        operator.progress_value = 1.0
        remove_progress_bar(operator)
    else:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        return 0.1  # Continue the timer

    return None  # Stop the timer if progress is complete

def start_progress_bar(operator, initial_message="Initializing Process..."):
    """Starts the progress bar and the update loop."""
    operator.progress_value = 0.0
    operator.current_operation = initial_message

    override = set_active_3d_view()
    if override is None:
        print("No active 3D View found.")
        operator.report({'WARNING'}, "No active 3D View found. Progress bar not displayed.")
        return

    space = override['space']
    region = override['region']

    # Add the draw handler with the operator instance
    operator.draw_handler = space.draw_handler_add(draw_progress_bar, (operator, space, region), 'WINDOW', 'POST_PIXEL')

    operator.space_reference = space

    # Remove the following line to eliminate the separate timer
    # bpy.app.timers.register(lambda: update_progress(operator), first_interval=0.1)
    
def remove_progress_bar(operator):
    """Removes the progress bar and stops updating."""
    if operator.draw_handler is not None and operator.space_reference is not None:
        operator.space_reference.draw_handler_remove(operator.draw_handler, 'WINDOW')
        operator.draw_handler = None
        operator.space_reference = None
        print("Progress bar draw handler removed.")
    operator.current_operation = ""
    operator.current_sub_operation = ""
    
    print("Progress bar removed and baking complete.")

class ASSETIFY_OT_refresh_asset_collection_list(bpy.types.Operator):
    """Refresh the Asset and Collection Lists"""
    bl_idname = "assetify.refresh_asset_collection_list"
    bl_label = "Refresh Asset/Collection List"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings

        # Repopulate both assets and collections
        populate_baked_assets_from_scene(assetify_settings)
        populate_baked_collections_from_scene(assetify_settings)

        self.report({'INFO'}, "Asset and Collection lists refreshed.")
        return {'FINISHED'}

import bpy

def ungroup_nodes(material):
    """Ungroup all node groups in the material using Blender's built-in ungrouping system."""
    if not material or not material.use_nodes:
        return

    node_tree = material.node_tree

    # Try to find an existing NODE_EDITOR area
    found_node_editor = False
    for window in bpy.context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type == 'NODE_EDITOR':
                found_node_editor = True
                override_context = {
                    'window': window,
                    'screen': screen,
                    'area': area,
                    'region': next((region for region in area.regions if region.type == 'WINDOW'), None),
                    'space_data': area.spaces.active,
                }

                # Ensure the space data is set to the correct node tree
                space = area.spaces.active
                space.tree_type = 'ShaderNodeTree'
                space.node_tree = node_tree

                # Select and ungroup all node groups
                while any(node.type == 'GROUP' for node in node_tree.nodes):
                    # Deselect all nodes
                    for node in node_tree.nodes:
                        node.select = False

                    # Select all group nodes
                    group_nodes = [node for node in node_tree.nodes if node.type == 'GROUP']
                    for node in group_nodes:
                        node.select = True
                    if group_nodes:
                        node_tree.nodes.active = group_nodes[0]  # Set one group node as active

                    try:
                        with bpy.context.temp_override(**override_context):
                            bpy.ops.node.group_ungroup()
                    except RuntimeError as e:
                        print(f"Failed to ungroup nodes: {e}")
                        return

                print(f"Ungrouped all nodes in material: {material.name}")
                return

    # If no NODE_EDITOR area is found, create one temporarily
    if not found_node_editor:
        # Find an area to temporarily switch to NODE_EDITOR
        for window in bpy.context.window_manager.windows:
            screen = window.screen
            for area in screen.areas:
                original_type = area.type
                # Skip areas that should not be changed
                if original_type in {'TOPBAR', 'STATUSBAR'}:
                    continue
                # Temporarily change the area to NODE_EDITOR
                area.type = 'NODE_EDITOR'
                space = area.spaces.active
                space.tree_type = 'ShaderNodeTree'
                space.node_tree = node_tree

                override_context = {
                    'window': window,
                    'screen': screen,
                    'area': area,
                    'region': next((region for region in area.regions if region.type == 'WINDOW'), None),
                    'space_data': space,
                }

                try:
                    # Select and ungroup all node groups
                    while any(node.type == 'GROUP' for node in node_tree.nodes):
                        # Deselect all nodes
                        for node in node_tree.nodes:
                            node.select = False

                        # Select all group nodes
                        group_nodes = [node for node in node_tree.nodes if node.type == 'GROUP']
                        for node in group_nodes:
                            node.select = True
                        if group_nodes:
                            node_tree.nodes.active = group_nodes[0]  # Set one group node as active

                        with bpy.context.temp_override(**override_context):
                            bpy.ops.node.group_ungroup()

                    print(f"Ungrouped all nodes in material: {material.name}")
                except RuntimeError as e:
                    print(f"Failed to ungroup nodes: {e}")
                finally:
                    # Restore the original area type
                    area.type = original_type

                return

        # If no suitable area is found to change, print a message
        print("No suitable area found to switch to NODE_EDITOR. Cannot ungroup nodes.")

def store_original_materials(obj):
    """Store the original material setups for the given object."""
    original_materials = {}
    for slot in obj.material_slots:
        if slot.material:
            original_materials[slot.material.name] = slot.material.copy()
    return original_materials

def restore_original_materials(obj, original_materials):
    """Restore the original materials for the given object."""
    for slot in obj.material_slots:
        if slot.material and slot.material.name in original_materials:
            slot.material = original_materials[slot.material.name]
            
def store_material_links(obj):
    """Store all original links for each material."""
    material_links = {}
    for mat_slot in obj.material_slots:
        if not mat_slot.material or not mat_slot.material.use_nodes:
            continue
        
        node_tree = mat_slot.material.node_tree
        material_links[mat_slot.material.name] = [
            (link.from_socket, link.to_socket) for link in node_tree.links
        ]
    return material_links

def restore_material_links(obj, material_links):
    """Restore all original links for each material."""
    for mat_slot in obj.material_slots:
        if not mat_slot.material or not mat_slot.material.use_nodes:
            continue
        
        mat_name = mat_slot.material.name
        if mat_name not in material_links:
            continue
        
        node_tree = mat_slot.material.node_tree
        # Clear all existing links
        for link in list(node_tree.links):
            node_tree.links.remove(link)
        # Restore original links
        for from_socket, to_socket in material_links[mat_name]:
            node_tree.links.new(from_socket, to_socket)

def remove_temporary_nodes(obj, node_types=("TEX_IMAGE", "EMISSION", "COMBRGB")):
    """Remove temporary nodes added for baking."""
    for mat_slot in obj.material_slots:
        if not mat_slot.material or not mat_slot.material.use_nodes:
            continue
        
        node_tree = mat_slot.material.node_tree
        nodes_to_remove = [
            node for node in node_tree.nodes if node.type in node_types
        ]
        for node in nodes_to_remove:
            node_tree.nodes.remove(node)

class OBJECT_OT_bake_textures_modal(bpy.types.Operator):
    """Bake Textures for Unreal Engine with Progress Bar (Modal)"""
    bl_idname = "object.bake_textures_modal"
    bl_label = "Bake Textures Modal"
    bl_options = {'REGISTER'}

    _timer = None
    _bake_index = 0
    _step_index = 0
    _objects_to_bake = []
    _steps_per_object = 8  # Number of baking steps per object (updated to match baking steps)

    # Progress tracking
    progress_value = 0.0
    total_bake_steps = 0
    bake_progress = 0

    current_operation = ""
    current_sub_operation = ""

    draw_handler = None
    space_reference = None
    
    skip_save_check: bpy.props.BoolProperty(default=False)
    
    def invoke(self, context, event):
        assetify_settings = context.scene.assetify_bake_settings
        if not assetify_settings.skip_save_check:
            if not ensure_file_saved(self, context):
                return {'CANCELLED'}
        return self.execute(context)

    def execute(self, context):
        # Reset UV unwrapped objects tracker
        self._uv_unwrapped_objects = set()
        
        assetify_settings = context.scene.assetify_bake_settings
        texture_path = bpy.path.abspath(assetify_settings.bake_folder)

        # Ensure viewport shading is set to solid
        set_viewport_shading_to_solid(context)
        
        self.original_material_links = {}

        # Check if we are in 'ASSET' or 'COLLECTION' mode
        if assetify_settings.asset_mode == 'ASSET':
            # Asset Mode: Collect individual assets marked for baking
            self._objects_to_bake = [
                bpy.data.objects.get(asset.name)
                for asset in assetify_settings.baked_assets
                if asset.include_in_send and bpy.data.objects.get(asset.name) is not None
            ]
        elif assetify_settings.asset_mode == 'COLLECTION':
            # Collection Mode: Collect all assets within selected collections
            self._objects_to_bake = []
            for baked_collection in assetify_settings.baked_collections:
                if baked_collection.include_in_send:
                    collection = bpy.data.collections.get(baked_collection.name)
                    if collection:
                        self._objects_to_bake.extend(self.collect_objects_from_collection(collection))

        # Debug output to verify selected objects
        if not self._objects_to_bake:
            mode_type = 'asset' if assetify_settings.asset_mode == 'ASSET' else 'collection'
            self.report({'ERROR'}, f"No objects to bake in {mode_type} mode.")
            return {'CANCELLED'}
        
        # Prepare for animation mode
        if assetify_settings.texturebake_mode == 'ANIMATION':
            self._frame_index = context.scene.frame_start
            self._current_frame = context.scene.frame_current
            self.total_bake_steps = len(self._objects_to_bake) * self._steps_per_object * (context.scene.frame_end - context.scene.frame_start + 1)
        else:
            self.total_bake_steps = len(self._objects_to_bake) * self._steps_per_object

        for obj in self._objects_to_bake:
            if obj and obj.data.materials:
                self.original_material_links[obj.name] = store_material_links(obj)

        # Run cleanup for images before baking
        self.cleanup_images_for_baking()

        # Initialize frame tracking for ANIMATION mode
        if assetify_settings.texturebake_mode == 'ANIMATION':
            self._total_frames = context.scene.frame_end - context.scene.frame_start + 1
        else:
            self._total_frames = 1

        # Initialize progress tracking and start modal baking process
        self.bake_progress = 0
        self.total_bake_steps = len(self._objects_to_bake) * self._steps_per_object

        start_progress_bar(self, initial_message="Baking Selected Assets")
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)

        # Update collection statuses before starting bake
        update_collection_statuses(assetify_settings)

        return {'RUNNING_MODAL'}

    def cleanup_images_for_baking(self):
        """Remove all images associated with the objects in self._objects_to_bake."""
        # Ensure self._objects_to_bake is populated
        if not self._objects_to_bake:
            print("No objects to bake, skipping image cleanup.")
            return

        # Iterate through all images and remove those associated with objects in _objects_to_bake
        for image in bpy.data.images:
            # Check if any object name in _objects_to_bake matches part of the image name
            if any(obj.name in image.name for obj in self._objects_to_bake):
                image_name = image.name  # Store the name before deleting
                image.user_clear()       # Clear users of the image
                bpy.data.images.remove(image)  # Remove the image from Blender
                print(f"Removed image: {image_name}")

    def collect_objects_from_collection(self, collection):
        """Recursively collect all mesh objects from the collection and its subcollections."""
        objects = []
        
        def collect_from_collection(col):
            for obj in col.objects:
                if obj.type == 'MESH':
                    objects.append(obj)
            for subcol in col.children:
                collect_from_collection(subcol)
        
        collect_from_collection(collection)
        return objects

    def modal(self, context, event):
        if event.type == 'ESC':
            # User pressed ESC to cancel
            self.report({'WARNING'}, "Baking process canceled by user.")
            self.cancel(context)
            return {'CANCELLED'}
        
        if event.type == 'TIMER':
            if context.scene.assetify_bake_settings.texturebake_mode == 'ANIMATION':
                total_frames = context.scene.frame_end - context.scene.frame_start + 1
                total_steps = total_frames * len(self._objects_to_bake) * self._steps_per_object

                if self._frame_index > context.scene.frame_end:
                    # Restore original frame
                    context.scene.frame_set(self._current_frame)

                    # Finalize baking
                    self.current_operation = "Baking Complete."
                    self.current_sub_operation = ""
                    print(f"Baking complete for all frames. Removing timer.")
                    remove_progress_bar(self)
                    switch_to_solid_shading_and_back()
                    bpy.context.window_manager.event_timer_remove(self._timer)

                    # Update baked asset list and statuses
                    update_baked_asset_list(context)
                    update_baked_collections_status(context)
                    context.scene.assetify_bake_settings.assets_baked = True

                    # Cleanup
                    if hasattr(self, 'original_materials'):
                        del self.original_materials  # Clean up memory

                    self.report({'INFO'}, "Baking operation completed successfully.")
                    return {'FINISHED'}

                # Update progress based on current frame, object, and step
                current_step = (
                    (self._frame_index - context.scene.frame_start) * len(self._objects_to_bake) * self._steps_per_object
                ) + (self._bake_index * self._steps_per_object) + self._step_index + 1
                self.progress_value = current_step / total_steps
                self.progress_value = min(self.progress_value, 1.0)  # Clamp to 1.0

                # Request a redraw for progress bar updates
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

                # Set current frame
                context.scene.frame_set(self._frame_index)
                print(f"Baking frame {self._frame_index} - Progress: {self.progress_value * 100:.2f}%")
                
            if self._bake_index < len(self._objects_to_bake):
                obj = self._objects_to_bake[self._bake_index]

                # Deselect all objects without using bpy.ops
                for obj_to_deselect in bpy.context.selected_objects:
                    obj_to_deselect.select_set(False)

                # Select and set the current object as active
                context.view_layer.objects.active = obj
                obj.select_set(True)
                          # Select the object

                platform = context.scene.assetify_bake_settings.platform_target

                if self._step_index == 0:
                    # Store original materials before ungrouping
                    if not hasattr(self, '_original_materials'):
                        self._original_materials = store_original_materials(obj)

                    if not hasattr(self, '_uv_unwrapped_objects'):
                        self._uv_unwrapped_objects = set()

                    # Ungroup nodes in all materials
                    for slot in obj.material_slots:
                        if slot.material:
                            ungroup_nodes(slot.material)

                    # Perform UV unwrapping only if the object hasn't been unwrapped yet
                    if obj.name not in self._uv_unwrapped_objects:
                        if not context.scene.assetify_bake_settings.skip_uv_unwrap:
                            self.current_sub_operation = "Applying UV Unwrap..."
                            smart_uv_project(obj)
                            print(f"UV unwrapped for {obj.name}")
                            self._uv_unwrapped_objects.add(obj.name)  # Mark as unwrapped
                        else:
                            print(f"Skipping UV unwrapping for {obj.name}")
                    else:
                        print(f"Object {obj.name} already UV unwrapped, skipping.")

                if platform == 'UNITY':
                    baking_steps = [
                        {"name": "Baking BaseColor", "func": lambda: bake_and_save(obj, 'DIFFUSE', 'BaseColor', context.scene.assetify_bake_settings.bake_resolution, bpy.path.abspath(context.scene.assetify_bake_settings.bake_folder), platform)},
                        {"name": "Baking MetallicSmoothness", "func": lambda: bake_and_save(obj, 'COMBINED', 'MetallicSmoothness', context.scene.assetify_bake_settings.bake_resolution, bpy.path.abspath(context.scene.assetify_bake_settings.bake_folder), platform)},
                        {"name": "Baking Normal", "func": lambda: bake_and_save(obj, 'NORMAL', 'Normal', context.scene.assetify_bake_settings.bake_resolution, bpy.path.abspath(context.scene.assetify_bake_settings.bake_folder), platform)},
                        {"name": "Baking Alpha", "func": lambda: bake_alpha_map(obj, context.scene.assetify_bake_settings.bake_resolution, bpy.path.abspath(context.scene.assetify_bake_settings.bake_folder))},
                    ]

                    if context.scene.assetify_bake_settings.texturebake_mode == 'STILL':
                        baking_steps.extend([
                            {"name": "Simplifying Materials and UV Maps", "func": lambda: simplify_materials_and_uv_maps(obj)},
                            {"name": "Finalizing UV Map naming", "func": lambda: finalize_uv_maps(obj)},
                            {"name": "Applying Baked Textures", "func": lambda: apply_baked_textures(obj, bpy.path.abspath(context.scene.assetify_bake_settings.bake_folder), platform)},
                        ])
                else:
                    baking_steps = [
                        {"name": "Baking BaseColor", "func": lambda: bake_and_save(obj, 'DIFFUSE', 'BaseColor', context.scene.assetify_bake_settings.bake_resolution, bpy.path.abspath(context.scene.assetify_bake_settings.bake_folder), platform)},
                        {"name": "Baking Roughness", "func": lambda: bake_and_save(obj, 'ROUGHNESS', 'Roughness', context.scene.assetify_bake_settings.bake_resolution, bpy.path.abspath(context.scene.assetify_bake_settings.bake_folder), platform)},
                        {"name": "Baking Metallic", "func": lambda: bake_and_save(obj, 'COMBINED', 'Metallic', context.scene.assetify_bake_settings.bake_resolution, bpy.path.abspath(context.scene.assetify_bake_settings.bake_folder), platform)},
                        {"name": "Baking Normal", "func": lambda: bake_and_save(obj, 'NORMAL', 'Normal', context.scene.assetify_bake_settings.bake_resolution, bpy.path.abspath(context.scene.assetify_bake_settings.bake_folder), platform)},
                        {"name": "Baking Alpha", "func": lambda: bake_alpha_map(obj, context.scene.assetify_bake_settings.bake_resolution, bpy.path.abspath(context.scene.assetify_bake_settings.bake_folder))},
                    ]

                    if context.scene.assetify_bake_settings.texturebake_mode == 'STILL':
                        baking_steps.extend([
                            {"name": "Simplifying Materials and UV Maps", "func": lambda: simplify_materials_and_uv_maps(obj)},
                            {"name": "Finalizing UV Map naming", "func": lambda: finalize_uv_maps(obj)},
                            {"name": "Applying Baked Textures", "func": lambda: apply_baked_textures(obj, bpy.path.abspath(context.scene.assetify_bake_settings.bake_folder), platform)},
                        ])

                if self._step_index < len(baking_steps):
                    step = baking_steps[self._step_index]

                    # Debug logs to see the progress
                    print(f"Starting step {self._step_index + 1}/{len(baking_steps)}: {step['name']} for object {obj.name}")
                    self.current_operation = f"Baking textures for {obj.name}..."
                    self.current_sub_operation = step["name"]

                    try:
                        step["func"]()  # Execute the step
                        print(f"Completed step {self._step_index + 1}/{len(baking_steps)}: {step['name']} for object {obj.name}")
                        self.bake_progress += 1
                        
                        # Update progress value
                        self.progress_value = self.bake_progress / self.total_bake_steps
                        self.progress_value = min(self.progress_value, 1.0)
                        
                        # Request a redraw
                        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
                    except Exception as e:
                        self.report({'ERROR'}, f"Failed to bake textures for {obj.name}: {str(e)}")
                        self.current_operation = "Baking Failed."
                        self.current_sub_operation = "Error encountered."
                        remove_progress_bar(self)
                        bpy.context.window_manager.event_timer_remove(self._timer)
                        return {'CANCELLED'}

                    self._step_index += 1
                    
                else:
                    if context.scene.assetify_bake_settings.texturebake_mode == 'ANIMATION':
                        # In ANIMATION mode, restore original materials
                        if obj.name in self.original_material_links:
                            try:
                                restore_material_links(obj, self.original_material_links[obj.name])
                            except Exception as e:
                                print(f"[ERROR] Failed to restore material links for {obj.name}: {e}")
                            remove_temporary_nodes(obj)  # Clean up temporary nodes
                    else:
                        # In STILL mode, KEEP baked textures and skip restoring original materials
                        print(f"[INFO] Skipping material restoration for {obj.name} in STILL mode.")

                    # Move to the next object or frame
                    self._bake_index += 1  # Proceed to the next object
                    self._step_index = 0   # Reset the step index for the new object

                    # Check if all objects have been baked
                    if self._bake_index >= len(self._objects_to_bake):
                        if context.scene.assetify_bake_settings.texturebake_mode == 'ANIMATION':
                            # For animation mode, move to the next frame
                            self._frame_index += 1
                            self._bake_index = 0  # Reset the object index for the new frame
                            return {'PASS_THROUGH'}
                        else:
                            # Still mode: Finalize the process
                            self.current_operation = "Baking Complete."
                            self.current_sub_operation = ""
                            print(f"Baking complete for all objects. Removing timer.")
                            remove_progress_bar(self)
                            switch_to_solid_shading_and_back()
                            bpy.context.window_manager.event_timer_remove(self._timer)

                            # Restore original frame if necessary
                            if hasattr(self, '_current_frame'):
                                context.scene.frame_set(self._current_frame)

                            update_baked_asset_list(context)
                            update_baked_collections_status(context)
                            context.scene.assetify_bake_settings.assets_baked = True

                            # Cleanup
                            if hasattr(self, 'original_materials'):
                                del self.original_materials  # Clean up memory

                            self.report({'INFO'}, "Baking operation completed successfully.")
                            return {'FINISHED'}
                    
            return {'PASS_THROUGH'}

        return {'PASS_THROUGH'}

    def cancel(self, context):
        # Cleanup in case of cancellation
        self.current_operation = "Baking Cancelled."
        self.current_sub_operation = ""
        remove_progress_bar(self)

        for obj in self._objects_to_bake:
            if obj.name in self.original_material_links:
                restore_material_links(obj, self.original_material_links[obj.name])
            remove_temporary_nodes(obj)  # Clean up temporary nodes

        if self._timer:
            bpy.context.window_manager.event_timer_remove(self._timer)

        # Reset baking state
        self._step_index = 0
        self._bake_index = 0
        self.bake_progress = 0.0
        self.progress_value = 0.0

        self.skip_save_check = False

        self.report({'INFO'}, "Baking operation canceled.")
            
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
    """Ensure the GPU is set for rendering if available, including support for macOS Metal."""
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
    elif 'METAL' in device_types:  # Add support for macOS Metal
        prefs.compute_device_type = 'METAL'
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
    """Ensure OptiX denoiser is enabled if available, otherwise fallback to OpenImageDenoise or disable if unsupported."""
    scene = bpy.context.scene

    # Ensure the Cycles engine is active
    if scene.render.engine != 'CYCLES':
        print("[DEBUG] Render engine is not Cycles, skipping denoiser setup.")
        return

    # Get Cycles preferences safely
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.get_devices()  # Refresh device list
    except KeyError:
        print("[ERROR] Cycles preferences not found. Make sure Cycles is installed and active.")
        return
    except AttributeError:
        print("[ERROR] Could not retrieve Cycles preferences, possibly due to a compatibility issue.")
        return

    # Check system type for platform-specific adjustments
    is_mac = sys.platform == "darwin"

    # Ensure Cycles denoising settings exist
    if not hasattr(scene.cycles, "denoiser"):
        print("[ERROR] Scene does not have denoiser settings. This Blender version might not support denoising.")
        return

    # Check if OptiX is available among devices
    optix_available = any(device.type == 'OPTIX' and device.use for device in prefs.devices)
    oidn_available = hasattr(scene.cycles, 'denoiser') and 'OPENIMAGEDENOISE' in scene.cycles.denoiser

    # Apply denoising settings based on availability
    if optix_available and not is_mac:  # OptiX is not available on macOS
        scene.cycles.use_denoising = True
        scene.cycles.denoiser = 'OPTIX'
        print("[DEBUG] OptiX denoiser enabled.")
    elif oidn_available:
        scene.cycles.use_denoising = True
        scene.cycles.denoiser = 'OPENIMAGEDENOISE'
        print("[DEBUG] OptiX not available, using OpenImageDenoise.")
    else:
        scene.cycles.use_denoising = False
        print("[DEBUG] No compatible denoiser available, disabling denoising.")
        
def smart_uv_project(obj):
    """
    Adds a new UV map called 'GameUV', applies Smart UV Project, and packs UV islands efficiently.
    If the object has an attribute named 'UVMap', it converts that to a real UV map first unless a 'UVMap' already exists.
    """
    if obj.type != 'MESH':
        debug_print(f"{obj.name} is not a mesh, skipping UV project.")
        return

    # Ensure we're in object mode
    bpy.ops.object.mode_set(mode='OBJECT')

    # UV map name to be used for this object
    uv_map_name = "GameUV"

    # Create 'GameUV' if it doesn't exist and ensure it's activated
    if uv_map_name not in obj.data.uv_layers:
        obj.data.uv_layers.new(name=uv_map_name)
        debug_print(f"Created new UV map '{uv_map_name}' for {obj.name}")
    
    # Ensure that the 'GameUV' UV layer is active
    uv_layer_index = obj.data.uv_layers.find(uv_map_name)
    if uv_layer_index != obj.data.uv_layers.active_index:
        obj.data.uv_layers.active_index = uv_layer_index
        debug_print(f"Set UV map '{uv_map_name}' as active for {obj.name}")

    # Switch to edit mode
    bpy.ops.object.mode_set(mode='EDIT')

    # Ensure that the 'GameUV' UV layer is active in edit mode
    obj.data.uv_layers.active_index = uv_layer_index
    debug_print(f"Set UV map '{uv_map_name}' as active in edit mode for {obj.name}")

    # Select all faces
    bpy.ops.mesh.select_all(action='SELECT')

    # Apply Smart UV Project
    bpy.ops.uv.smart_project(
        angle_limit=m.radians(66.0),
        island_margin=0.0,
        area_weight=0.0,
        correct_aspect=False,
        scale_to_bounds=False,
        margin_method='SCALED',
        rotate_method='AXIS_ALIGNED_Y'
    )
    
    debug_print(f"Smart UV Project applied to {obj.name}")

    # Pack UV islands
    bpy.ops.uv.pack_islands(
        udim_source='CLOSEST_UDIM',
        rotate=True,
        rotate_method='ANY',
        scale=True,
        merge_overlap=False,
        margin_method='SCALED',
        margin=0.001,
        pin=False,
        pin_method='LOCKED',
        shape_method='CONCAVE',
    )
    debug_print(f"Packed UV islands for {obj.name}")

    # Return to object mode
    bpy.ops.object.mode_set(mode='OBJECT')
    
def convert_uvmap_attribute_to_uv_layer(obj):
    """
    Converts the 'UVMap' attribute from geometry nodes to an actual UV layer.
    Ensures that the new UV layer is named 'UVMap' and no duplicates are created.
    """
    uvmap_name = "UVMap"
    
    # Check if the object has a 'uv_layers' attribute (only meshes support UV layers)
    if not hasattr(obj.data, 'uv_layers'):
        print(f"Object '{obj.name}' does not support UV layers (type: {obj.type}). Skipping.")
        return

    # Check if an existing UVMap with the same name exists
    existing_uvmap = obj.data.uv_layers.get(uvmap_name)
    if existing_uvmap:
        print(f"{obj.name} already has a UV layer named '{uvmap_name}'.")

        # If there's already a UVMap, don't add a new one but ensure it's active
        obj.data.uv_layers.active = existing_uvmap
        print(f"'{uvmap_name}' is set as the active UV layer for {obj.name}.")
        return

    # Check if the object has a geometry node attribute called 'UVMap'
    uv_attr = obj.data.attributes.get(uvmap_name)
    if uv_attr and uv_attr.data_type == 'FLOAT2':
        # Create a new UV layer
        new_uv_layer = obj.data.uv_layers.new(name=uvmap_name)
        print(f"Created new UV layer '{uvmap_name}' for {obj.name} from geometry node attribute.")

        # Copy the UV coordinates from the attribute to the UV layer
        for loop_index, loop in enumerate(obj.data.loops):
            new_uv_layer.data[loop_index].uv = uv_attr.data[loop.vertex_index].vector.xy

        # Remove the UVMap attribute after converting
        obj.data.attributes.remove(uv_attr)
        print(f"Converted 'UVMap' attribute to UV layer for {obj.name}.")
    else:
        print(f"{obj.name} has no geometry node attribute called '{uvmap_name}'.")

def remove_empty_material_slots(obj):
    """
    Removes any empty material slots from the object's material slots.
    """
    # Access the object's material slots directly
    materials = obj.data.materials

    # List to keep track of indices of empty material slots
    slots_to_remove = []

    # Find indices of empty material slots
    for i, mat in enumerate(materials):
        if mat is None:
            slots_to_remove.append(i)

    # Remove empty material slots in reverse order to avoid index shifting
    for i in reversed(slots_to_remove):
        materials.pop(index=i)
        debug_print(f"Removed empty material slot {i} from {obj.name}")

def add_realize_instances_node(geometry_node_modifier):
    """
    Adds a 'Realize Instances' node to the Geometry Node tree of the given modifier,
    inserts it before the Group Output node, and provides a cleanup function to restore the original state.
    """
    if not geometry_node_modifier.node_group:
        debug_print(f"Modifier {geometry_node_modifier.name} has no node group.")
        return None  # Return None if no node group is present

    node_tree = geometry_node_modifier.node_group
    debug_print(f"Accessing node tree for modifier: {geometry_node_modifier.name}")

    # Find the Group Output node
    output_node = next((node for node in node_tree.nodes if node.type == 'GROUP_OUTPUT'), None)
    if not output_node:
        debug_print(f"No Group Output node found in node tree: {node_tree.name}")
        return None

    # Get the input socket of the Group Output node
    geometry_input_socket = output_node.inputs.get('Geometry')
    if not geometry_input_socket or not geometry_input_socket.is_linked:
        debug_print(f"Output node in {node_tree.name} has no geometry input or is not linked.")
        return None

    # Find the node currently linked to the Group Output node's Geometry input
    original_link = geometry_input_socket.links[0]
    previous_node_output = original_link.from_socket
    debug_print(f"Found link from {previous_node_output.node.name} to Group Output.")

    # Store the original link for restoration
    original_link_info = {
        "from_node": previous_node_output.node,
        "from_socket": previous_node_output,
        "to_node": output_node,
        "to_socket": geometry_input_socket
    }

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

    # Return a cleanup function to revert the changes
    def revert_changes():
        debug_print("Reverting changes in the node tree...")
        # Remove the Realize Instances node and restore the original link
        node_tree.links.remove(realize_node.outputs['Geometry'].links[0])
        node_tree.nodes.remove(realize_node)
        node_tree.links.new(original_link_info["from_socket"], original_link_info["to_socket"])
        debug_print("Original connections restored. Realize Instances node removed.")

    return revert_changes
 
def realize_geometry_node_instances(obj, skip_conversion=False):
    """
    Adds 'Realize Instances' node to each Geometry Nodes modifier on the object
    and optionally skips conversion to mesh based on centralized conditions.
    """
    # Assign animation type and file format for the object
    scene = bpy.context.scene
    obj['animation_type'] = scene.assetify_animation_settings.animation_type
    obj['file_format'] = scene.assetify_animation_settings.file_format
    skip_conditions = animation_processor.get_skip_conditions()
    assetify_settings = bpy.context.scene.assetify_bake_settings
    debug_print(f"[DEBUG] Assigned animation_type: {obj['animation_type']}, file_format: {obj['file_format']} to {obj.name}")
    
    animation_type = obj['animation_type']
    file_format = obj['file_format']

    # Debug: Log object properties and conditions
    debug_print(f"[DEBUG] Object: {obj.name}, Animation Type: {animation_type}, File Format: {file_format}")
    debug_print(f"[DEBUG] Skip Conditions: {skip_conditions}")
    
    # Set skip_conditions based on the bake mode
    if assetify_settings.bake_mode == 'STILL':
        skip_conversion = False
        print("[INFO] Bake mode is STILL. Skipping conditions disabled.")
    else:
        skip_conditions = animation_processor.get_skip_conditions()
        if (animation_type, file_format) in skip_conditions:
            debug_print(f"[INFO] Skipping conversion for {obj.name} ({animation_type}, {file_format}).")
            return {'SKIPPED'}
            print("[INFO] Bake mode is ANIMATION. Using skip conditions from animation processor.")

    if obj.type not in {'MESH', 'CURVE'}:
        debug_print(f"Object {obj.name} is neither a mesh nor a curve. Skipping...")
        return {'SKIPPED'}

    debug_print(f"Processing object: {obj.name}")

    cleanup_functions = []  # Store cleanup functions for reverting changes

    # Check and process all modifiers on the object
    if not obj.modifiers:
        debug_print(f"Object {obj.name} has no modifiers.")
    else:
        for modifier in obj.modifiers:
            debug_print(f"Object {obj.name} has modifier: {modifier.name} of type {modifier.type}")
            if modifier.type == 'NODES':  # Check if it's a Geometry Nodes modifier
                debug_print(f"Found Geometry Nodes modifier: {modifier.name}")
                cleanup = add_realize_instances_node(modifier)
                if callable(cleanup):
                    cleanup_functions.append(cleanup)
                else:
                    debug_print(f"[WARNING] Cleanup function for modifier {modifier.name} is not callable.")

    # Skip the conversion section if the flag is set
    if not skip_conversion:
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
    else:
        debug_print(f"Skipping mesh conversion for object {obj.name}.")

    # Ensure the object is still selected after conversion
    obj = bpy.context.active_object

    # Remove any empty material slots
    remove_empty_material_slots(obj)

    # Revert changes to the Geometry Node tree
    for cleanup in cleanup_functions:
        cleanup()
    debug_print(f"All changes reverted for geometry nodes on {obj.name}.")

    return {'FINISHED'}
    
def process_object(obj, custom_object, custom_name, custom_value_name):
    """
    Processes the object: applies geometry nodes, converts curves to meshes, makes materials unique,
    renames materials, and removes empty material slots.
    """
    
    scene = bpy.context.scene  # Access the scene
    
    # Set animation type and file format
    obj['animation_type'] = scene.assetify_animation_settings.animation_type
    obj['file_format'] = scene.assetify_animation_settings.file_format

    debug_print(f"[DEBUG] Assigned animation_type: {obj['animation_type']}, file_format: {obj['file_format']} to {obj.name}")
    debug_print(f"[INFO] Starting process for object: {obj.name}")

    # Deselect all objects
    bpy.ops.object.select_all(action='DESELECT')

    # Set the duplicated object as the active object and select it
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    debug_print(f"[DEBUG] Object {obj.name} is set as active and selected.")

    # Only call customize_color if Mossify mode is active
    if scene.assetify_bake_settings.use_mossify:
        debug_print(f"[DEBUG] Mossify mode active. Applying customize_color to {obj.name}.")
        customize_color(obj, custom_object)
    else:
        debug_print(f"[DEBUG] Adding custom attributes to geometry for {obj.name}.")
        add_custom_attributes_to_geometry(obj, custom_object)

    # Handle geometry nodes
    cleanup_functions = []  # Store cleanup functions for reverting changes
    try:
        debug_print(f"[DEBUG] Calling realize_geometry_node_instances for {obj.name}.")
        cleanup = realize_geometry_node_instances(obj)

        if cleanup == {'SKIPPED'}:
            debug_print(f"[INFO] Skipped processing object {obj.name} due to skip conditions.")
        elif cleanup and callable(cleanup):
            debug_print(f"[DEBUG] Adding cleanup function for {obj.name}.")
            cleanup_functions.append(cleanup)
        else:
            debug_print(f"[DEBUG] No cleanup function returned for {obj.name}.")

        # Remove any empty material slots before making materials unique
        remove_empty_material_slots(obj)
        debug_print(f"[DEBUG] Removed empty material slots for {obj.name}.")

        # Make the materials unique for the duplicated object
        make_materials_unique(obj)
        debug_print(f"[DEBUG] Made materials unique for {obj.name}.")

        # Rename materials to match object name
        rename_materials(obj)
        debug_print(f"[DEBUG] Renamed materials for {obj.name}.")

        # Convert UVMap attribute to an actual UV map layer
        convert_uvmap_attribute_to_uv_layer(obj)
        debug_print(f"[DEBUG] Converted UVMap attributes for {obj.name}.")

    finally:
        # Revert changes to the Geometry Nodes tree
        for cleanup in cleanup_functions:
            debug_print(f"[DEBUG] Reverting changes for {obj.name}.")
            cleanup()
        debug_print(f"[INFO] All temporary changes reverted for {obj.name}.")

    # Deselect the object after processing
    obj.select_set(False)
    debug_print(f"[INFO] Completed processing for object: {obj.name}.")

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

def duplicate_objects_in_collection(original_collection, game_ready_collection, mapping_info, baked_collection, custom_object, custom_name, custom_value_name):
    assetify_settings = bpy.context.scene.assetify_bake_settings
    
    for obj in original_collection.objects:
        if obj.type in {'MESH', 'CURVE', 'FONT'}:
            new_obj = obj.copy()
            new_obj.data = obj.data.copy()
            new_obj.name = obj.name + "_gameasset"
            game_ready_collection.objects.link(new_obj)
            duplicated_objects.append(new_obj)
    
            process_object(new_obj, custom_object, custom_name, custom_value_name)
    
            # Mark object as a game asset
            new_obj['is_game_asset'] = True
    
            # Add the object to the baked_assets list
            baked_asset = assetify_settings.baked_assets.add()
            baked_asset.name = new_obj.name
            baked_asset.is_game_asset = True
            baked_asset.is_baked = check_if_baked(new_obj)
            baked_asset.is_fbx_exported = check_if_exported(new_obj)
            baked_asset.include_in_send = False
            baked_asset.collection_name = original_collection.name
    
            # Add to baked_collection.assets
            baked_collection.assets.add()
            baked_collection.assets[-1].name = baked_asset.name
            baked_collection.assets[-1].is_game_asset = baked_asset.is_game_asset
            baked_collection.assets[-1].is_baked = baked_asset.is_baked
            baked_collection.assets[-1].is_fbx_exported = baked_asset.is_fbx_exported
            baked_collection.assets[-1].include_in_send = baked_asset.include_in_send
            baked_collection.assets[-1].collection_name = baked_asset.collection_name

def process_collection(collection, game_ready_collection, assetify_settings, main_collection=None, operator=None):
    """
    Recursively processes the collection and its subcollections to register all assets
    into the game-ready collection, maintain the original collection structure, and update 
    the list of main collection assets even when assets are moved between collections.

    Args:
        collection (Collection): The current collection being processed.
        game_ready_collection (Collection): The current game-ready subcollection.
        assetify_settings (Settings): The assetify settings to register baked assets.
        main_collection (Collection, optional): The main game-ready collection to which all assets are logically linked.
    """
    # Set the main collection to link all assets to, if not provided
    if main_collection is None:
        main_collection = game_ready_collection
    
    scene = bpy.context.scene  # Get the current scene

    # Create a baked collection entry for this collection
    baked_collection = assetify_settings.baked_collections.add()
    baked_collection.name = game_ready_collection.name
    baked_collection.is_baked = False  # Assume baked, will update based on assets
    baked_collection.include_in_send = False  # Assume ready, will update based on assets

    # Track the number of assets in the collection
    asset_count = 0
    asset_names = []

    print(f"[DEBUG] Processing collection: {collection.name}")
    print(f"[DEBUG] Created baked collection entry: {baked_collection.name}")

    # Process all objects in the current collection
    for obj in collection.objects:
        if obj.type in {'MESH', 'CURVE', 'FONT'}:
            print(f"[DEBUG] Found object: {obj.name} of type {obj.type}")

            # Check if the asset is already in the baked_assets list to avoid duplication
            if not any(asset.name == obj.name + "_gameasset" for asset in assetify_settings.baked_assets):
                # Duplicate and prepare the game-ready asset
                new_obj = obj.copy()
                new_obj.data = obj.data.copy()
                new_obj.name = obj.name + "_gameasset"
                game_ready_collection.objects.link(new_obj)
                print(f"[DEBUG] Linked object: {new_obj.name} to {game_ready_collection.name}")

                # **Logical Linking**: Add the object to the main collection logically (no physical linking)
                if game_ready_collection != main_collection:
                    print(f"[DEBUG] Logically adding {new_obj.name} to main collection: {main_collection.name}")
                    
                    # Ensure the asset is added to the main_collection_assets
                    if new_obj.name not in assetify_settings.main_collection_assets:
                        assetify_settings.main_collection_assets.append(new_obj.name)
                        print(f"[DEBUG] Added {new_obj.name} to main_collection_assets")

                # We keep track of the asset in the baked_assets list
                baked_asset = assetify_settings.baked_assets.add()
                baked_asset.name = new_obj.name
                baked_asset.is_game_asset = True
                baked_asset.is_baked = check_if_baked(new_obj)
                baked_asset.is_fbx_exported = check_if_exported(new_obj)
                baked_asset.include_in_send = False
                baked_asset.collection_name = main_collection.name
                print(f"[DEBUG] Logically added {new_obj.name} to the main baked_assets list")

                # Process the object for game-ready status
                process_object(
                    new_obj, 
                    scene.custom_object,       # Retrieve custom_object from scene
                    scene.custom_name,         # Retrieve custom_name from scene
                    scene.custom_value_name    # Retrieve custom_value_name from scene
                )

                # Add the asset to the baked collection's asset list
                collection_asset = baked_collection.assets.add()
                collection_asset.name = baked_asset.name
                collection_asset.is_game_asset = baked_asset.is_game_asset
                collection_asset.is_baked = baked_asset.is_baked
                collection_asset.is_fbx_exported = baked_asset.is_fbx_exported
                collection_asset.include_in_send = baked_asset.include_in_send
                print(f"[DEBUG] Added {new_obj.name} to baked collection: {baked_collection.name}")

                # Update the collection's baked and export status based on the asset
                if not baked_asset.is_baked:
                    baked_collection.is_baked = False
                if not baked_asset.is_fbx_exported:
                    baked_collection.include_in_send = False

                # Track asset name for debugging purposes
                asset_names.append(new_obj.name)

                # Increment asset count
                asset_count += 1
                
                if operator:
                    operator.processed_assets += 1
                    operator.progress_value = operator.processed_assets / operator.total_assets
                    operator.current_sub_operation = f"Processing {obj.name}..."
                    bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

    # Log how many assets were found and processed
    print(f"[DEBUG] Processed {asset_count} assets for collection: {game_ready_collection.name}")
    print(f"[DEBUG] Baked collection '{baked_collection.name}' contains assets: {asset_names}")

    # Recursively process subcollections
    for subcol in collection.children:
        subcol_game_ready = bpy.data.collections.new(subcol.name + "_GameReady")
        game_ready_collection.children.link(subcol_game_ready)
        process_collection(subcol, subcol_game_ready, assetify_settings, main_collection)

    # After processing all objects and subcollections, log the number of assets
    print(f"[DEBUG] Registered baked collection: {game_ready_collection.name} with {len(baked_collection.assets)} assets")

    # Final debug: Print the full asset list for the main collection
    main_collection_assets = assetify_settings.main_collection_assets
    print(f"[DEBUG] Main collection '{main_collection.name}' now contains assets: {main_collection_assets}")

    populate_baked_assets_from_scene(assetify_settings)
    populate_baked_collections_from_scene(assetify_settings)
            
# === Baking Functionality for Unreal Engine ===

def create_bake_image(obj, map_type, resolution):
    """Create a new blank image to use for baking."""
    width = height = int(resolution)
    image_name = f"{obj.name}_{map_type}"
    image = bpy.data.images.new(image_name, width=width, height=height)
    return image

def assign_image_to_material(obj, image, map_type):
    """Assign a bake image to each material's shader node for baking, reusing the node if it already exists."""
    if not obj.data.materials:
        debug_print(f"{obj.name} has no materials, skipping image assignment.")
        return

    for mat in obj.data.materials:
        if not mat.use_nodes:
            continue

        node_tree = mat.node_tree

        # Check if a bake image node already exists for this map type, and reuse it if found
        image_node = node_tree.nodes.get(f"Bake_{map_type}")
        if not image_node:
            image_node = node_tree.nodes.new('ShaderNodeTexImage')
            image_node.name = f"Bake_{map_type}"  # Name the node for easy identification
        image_node.image = image  # Set the image for baking

        # Set this node as active for baking
        node_tree.nodes.active = image_node

def debug_print_image_assignments(obj, map_type):
    """Prints out the image assignments for each material's bake node."""
    debug_print(f"\n--- Image Assignments for '{map_type}' Before Baking ---")
    
    if not obj.data.materials:
        debug_print(f"{obj.name} has no materials.")
        return

    for mat in obj.data.materials:
        if not mat.use_nodes:
            debug_print(f"Material '{mat.name}' does not use nodes.")
            continue

        node_tree = mat.node_tree
        image_node = node_tree.nodes.get(f"Bake_{map_type}")

        if image_node and image_node.image:
            debug_print(f"Material '{mat.name}': Image Node '{image_node.name}' is assigned to Image '{image_node.image.name}'.")
        else:
            debug_print(f"Material '{mat.name}': No image node found for '{map_type}' or no image assigned.")
    
    debug_print(f"--- End of Image Assignments ---\n")
        
def bake_and_save(obj, bake_type, map_type, resolution, save_dir, platform="UE5"):
    """Bake the specified map and save it as an image in the given directory, with override functionality."""
    assetify_settings = bpy.context.scene.assetify_bake_settings

    # Set render device (CPU or GPU)
    bpy.context.scene.cycles.device = assetify_settings.render_device.upper()
    
        # Ensure resolution is an integer
    resolution = int(resolution)

    # Set tile size for Cycles
    if assetify_settings.use_tiling:
        bpy.context.scene.cycles.tile_x = assetify_settings.tile_size
        bpy.context.scene.cycles.tile_y = assetify_settings.tile_size
        print(f"[DEBUG] Using tiling with size {assetify_settings.tile_size}x{assetify_settings.tile_size}")
    else:
        bpy.context.scene.cycles.tile_x = int(resolution)
        bpy.context.scene.cycles.tile_y = int(resolution)
        print("[DEBUG] Tiling disabled. Baking in one pass.")

    print(f"[DEBUG] Using {assetify_settings.render_device} with tile size {assetify_settings.tile_size}")

    # Ensure Cycles render engine is active
    ensure_cycles_render_engine()

    # Ensure GPU rendering if available and selected
    if assetify_settings.render_device == 'GPU':
        ensure_gpu_rendering()

    # Ensure OptiX denoiser if available
    ensure_optix_denoiser()

    # Determine file path and frame-specific folder structure
    if assetify_settings.texturebake_mode == 'ANIMATION':
        # Create subfolder for the object
        object_folder = os.path.join(save_dir, f"{obj.name}_textures")
        os.makedirs(object_folder, exist_ok=True)

        # Create subfolder for the map type
        map_folder = os.path.join(object_folder, map_type)
        os.makedirs(map_folder, exist_ok=True)

        # Generate file name based on the current frame
        frame_number = bpy.context.scene.frame_current
        file_name = f"Frame{frame_number:04d}.png"
        texture_file_path = os.path.join(map_folder, file_name)
    else:
        # For STILL mode, save in a single "textures" folder
        textures_folder = os.path.join(save_dir, "textures")
        os.makedirs(textures_folder, exist_ok=True)

        # Save the map directly in the "textures" folder
        texture_file_path = os.path.join(textures_folder, f"{obj.name}_{map_type}.png")

    # Delete the file if it already exists
    if os.path.exists(texture_file_path):
        os.remove(texture_file_path)
        print(f"[DEBUG] Removed existing file: {texture_file_path}")

    # Create a new bake image for each frame
    image_name = f"{obj.name}_{map_type}_Frame{bpy.context.scene.frame_current:04d}"
    image = bpy.data.images.get(image_name) or bpy.data.images.new(
        name=image_name,
        width=resolution,
        height=resolution,
        alpha=True
    )
    image.colorspace_settings.name = 'Non-Color' if map_type in ["Roughness", "Normal", "Metallic"] else 'sRGB'

    # Store original material links for restoration
    original_material_links = {}

    for mat_slot in obj.material_slots:
        if mat_slot.material and mat_slot.material.use_nodes:
            node_tree = mat_slot.material.node_tree

            # Save original links for restoration
            material_output = next((node for node in node_tree.nodes if node.type == 'OUTPUT_MATERIAL'), None)
            if material_output:
                original_material_links[mat_slot.material.name] = [
                    (link.from_socket, link.to_socket)
                    for link in node_tree.links
                    if link.to_node == material_output
                ]

            # Remove previously added texture nodes for this bake
            nodes_to_remove = [
                node for node in node_tree.nodes
                if node.type == 'TEX_IMAGE' and node.image and node.image.name.startswith(f"{obj.name}_{map_type}_Frame")
            ]
            for node in nodes_to_remove:
                node_tree.nodes.remove(node)

            # Create a new texture node for the current frame
            tex_node = node_tree.nodes.new(type='ShaderNodeTexImage')
            tex_node.image = image
            node_tree.nodes.active = tex_node  # Set as active for baking

    if platform == "UNITY" and map_type == "MetallicSmoothness":
        # Step 1: Bake the roughness map (but don't save the individual roughness map)
        roughness_image = create_bake_image(obj, "Roughness", resolution)
        assign_image_to_material(obj, roughness_image, "Roughness")
        roughness_image.colorspace_settings.name = 'Non-Color'
        # Bake roughness (in Blender, roughness is already in the right form)
        bpy.context.scene.cycles.bake_type = 'ROUGHNESS'
        bpy.ops.object.bake(type='ROUGHNESS')

        # Step 2: Check if metallic input is connected
        metallic_image = create_bake_image(obj, "Metallic", resolution)
        metallic_image.colorspace_settings.name = 'Non-Color'
        if is_metallic_input_connected(obj):
            # If metallic input is connected, bake the metallic map using emission
            bake_metallic_as_emission(obj, metallic_image)
        else:
            # If metallic input is not connected, bake a black metallic map
            assign_image_to_material(obj, metallic_image, "Metallic")
            bpy.context.scene.cycles.bake_type = 'EMIT'
            bpy.ops.object.bake(type='EMIT')

        # Step 3: Pack metallic into Red and inverted roughness into Alpha channel
        packed_image = pack_metallic_roughness(metallic_image, roughness_image, output_image_name=f"{obj.name}_MetallicSmoothness")

        # Save the packed image
        packed_image.filepath_raw = os.path.join(textures_folder, f"{obj.name}_MetallicSmoothness.png")
        packed_image.file_format = 'PNG'
        packed_image.save()

        debug_print(f"Packed Roughness/Metallic for {obj.name} and saved as {packed_image.filepath_raw}")
        
            # Restore original material setup
        for mat_slot in obj.material_slots:
            if mat_slot.material and mat_slot.material.use_nodes:
                node_tree = mat_slot.material.node_tree

                # Remove texture nodes added for baking
                nodes_to_remove = [
                    node for node in node_tree.nodes if node.type == 'TEX_IMAGE'
                ]
                for node in nodes_to_remove:
                    node_tree.nodes.remove(node)

                # Restore original links to the material output node
                material_output = next((node for node in node_tree.nodes if node.type == 'OUTPUT_MATERIAL'), None)
                original_links = original_material_links.get(mat_slot.material.name, [])
                for link in list(node_tree.links):
                    if link.to_node == material_output:
                        node_tree.links.remove(link)
                for from_socket, to_socket in original_links:
                    node_tree.links.new(from_socket, to_socket)
        
        return packed_image

    # Handle regular baking for UE5 or other platforms
    else:
        # Proceed with the regular baking process for other map types (e.g., BaseColor, Normal, etc.)
        image = create_bake_image(obj, map_type, resolution)
        
        if map_type in ["Roughness", "Normal", "Metallic"]:
            image.colorspace_settings.name = 'Non-Color'
        
        assign_image_to_material(obj, image, map_type)

        # Store original metallic settings to restore after baking
        metallic_settings = {}
        if map_type == "BaseColor":
            # Disconnect metallic inputs and set to 0
            for mat in obj.data.materials:
                if not mat.use_nodes:
                    continue

                node_tree = mat.node_tree
                principled_node = next((node for node in node_tree.nodes if node.type == 'BSDF_PRINCIPLED'), None)
                
                if principled_node:
                    metallic_input = principled_node.inputs['Metallic']
                    if metallic_input.is_linked:
                        # Store the link to reconnect later
                        metallic_settings[mat.name] = metallic_input.links[0]
                        node_tree.links.remove(metallic_input.links[0])
                    else:
                        # Store the original value to restore later
                        metallic_settings[mat.name] = metallic_input.default_value
                    metallic_input.default_value = 0  # Set metallic to 0 for baking

            bpy.context.scene.cycles.bake_type = 'DIFFUSE'
            bpy.context.scene.render.bake.use_pass_direct = False
            bpy.context.scene.render.bake.use_pass_indirect = False
            bpy.context.scene.render.bake.use_pass_color = True
            bake_type_used = 'DIFFUSE'
        elif map_type == "Normal":
            bpy.context.scene.cycles.bake_type = 'NORMAL'
            bake_type_used = 'NORMAL'
            bpy.context.scene.render.bake.normal_space = 'TANGENT'
        elif map_type == "Roughness":
            bpy.context.scene.cycles.bake_type = 'ROUGHNESS'
            bake_type_used = 'ROUGHNESS'
        elif map_type == "Metallic":
            # Correctly bake the metallic map using emission
            bake_metallic_as_emission(obj, image)
            image.filepath_raw = texture_file_path
            image.file_format = 'PNG'
            image.save()
            debug_print(f"Baked {map_type} for {obj.name} and saved as {image.filepath_raw}")
            return image
        else:
            bpy.context.scene.cycles.bake_type = bake_type
            bake_type_used = bake_type

        # Perform the bake if not handled above
        if map_type != "Metallic":
            bpy.ops.object.bake(type=bake_type_used)
            image.filepath_raw = texture_file_path
            image.file_format = 'PNG'
            image.save()
            debug_print(f"Baked {map_type} for {obj.name} and saved as {image.filepath_raw}")
            
            # Restore metallic settings
            if map_type == "BaseColor":
                for mat in obj.data.materials:
                    if not mat.use_nodes:
                        continue

                    node_tree = mat.node_tree
                    principled_node = next((node for node in node_tree.nodes if node.type == 'BSDF_PRINCIPLED'), None)
                    
                    if principled_node and mat.name in metallic_settings:
                        metallic_input = principled_node.inputs['Metallic']
                        saved_setting = metallic_settings[mat.name]
                        
                        if isinstance(saved_setting, bpy.types.NodeLink):
                            # Reconnect the original link
                            node_tree.links.new(saved_setting.from_socket, metallic_input)
                        else:
                            # Restore the original value
                            metallic_input.default_value = saved_setting
            
                # Restore original material setup
                for mat_slot in obj.material_slots:
                    if mat_slot.material and mat_slot.material.use_nodes:
                        node_tree = mat_slot.material.node_tree

                        # Remove texture nodes added for baking
                        nodes_to_remove = [
                            node for node in node_tree.nodes if node.type == 'TEX_IMAGE'
                        ]
                        for node in nodes_to_remove:
                            node_tree.nodes.remove(node)

                        # Restore original links to the material output node
                        material_output = next((node for node in node_tree.nodes if node.type == 'OUTPUT_MATERIAL'), None)
                        original_links = original_material_links.get(mat_slot.material.name, [])
                        for link in list(node_tree.links):
                            if link.to_node == material_output:
                                node_tree.links.remove(link)
                        for from_socket, to_socket in original_links:
                            node_tree.links.new(from_socket, to_socket)
            
            return image
        
    # After baking, update the status in the baked asset list
    assetify_settings = bpy.context.scene.assetify_bake_settings
    for baked_asset in assetify_settings.baked_assets:
        if baked_asset.name == obj.name:
            baked_asset.is_baked = True
            break

def update_baked_status(obj, is_baked):
    """Update the baked status of an asset in the baked_assets list and in collections."""
    assetify_settings = bpy.context.scene.assetify_bake_settings

    # Update in baked_assets
    for asset in assetify_settings.baked_assets:
        if asset.name == obj.name:
            asset.is_baked = is_baked
            print(f"Updated baked_assets: {asset.name} is_baked set to {is_baked}")
            break

    # Update in baked_collections
    for baked_collection in assetify_settings.baked_collections:
        for asset in baked_collection.assets:
            if asset.name == obj.name:
                asset.is_baked = is_baked
                print(f"Updated baked_collection {baked_collection.name}: {asset.name} is_baked set to {is_baked}")
                break
        
def is_metallic_input_connected(obj):
    """Check if the metallic input is connected in the active material."""
    for mat in obj.data.materials:
        if not mat.use_nodes:
            continue

        node_tree = mat.node_tree
        principled_node = None

        # Find the Principled BSDF node
        for node in node_tree.nodes:
            if node.type == 'BSDF_PRINCIPLED':
                principled_node = node
                break

        if not principled_node:
            continue

        # Check if the Metallic input is connected
        metallic_input = principled_node.inputs.get('Metallic')
        if metallic_input and metallic_input.is_linked:
            return True

    return False

def pack_metallic_roughness(metallic_image, roughness_image, output_image_name="MetallicSmoothness"):
    """
    Combines the Metallic and Roughness maps into one image.
    Metallic goes to the Red channel.
    Inverted Roughness (Smoothness) goes to the Alpha channel.
    """
    # Initialize arrays
    dst_array = None
    has_alpha = True  # We'll always need an alpha channel for Unity's format
    
    # Set the dimensions of the images (assuming both images have the same size)
    w, h = metallic_image.size
    src_array_metallic = numpy.empty(w * h * 4, dtype=numpy.float32)
    src_array_roughness = numpy.empty(w * h * 4, dtype=numpy.float32)
    dst_array = numpy.zeros(w * h * 4, dtype=numpy.float32)  # Initialize the output array (RGBA)

    # Get pixels from the source images
    metallic_image.pixels.foreach_get(src_array_metallic)
    roughness_image.pixels.foreach_get(src_array_roughness)

    # Fill in the destination array:
    # Metallic -> Red channel
    # Inverted Roughness -> Alpha channel
    for i in range(0, w * h * 4, 4):
        dst_array[i] = src_array_metallic[i]   # Red channel (Metallic)
        dst_array[i + 3] = 1.0 - src_array_roughness[i]  # Alpha channel (1 - Roughness for Smoothness)

    # Create the output image from the packed pixels
    dst_image = bpy.data.images.new(output_image_name, w, h, alpha=has_alpha)
    dst_image.pixels.foreach_set(dst_array)

    # Pack the image to ensure it's saved in the .blend file
    dst_image.pack()

    return dst_image

def bake_metallic_as_emission(obj, image):
    """Bakes the metallic map into the provided image using the emission method."""
    debug_print(f"[DEBUG] Starting metallic bake for object {obj.name}")

    # Dictionary to store original links for each material
    original_links_dict = {}

    # Dictionary to store added nodes for each material
    added_nodes_dict = {}

    # Iterate through all materials on the object
    for mat in obj.data.materials:
        if not mat or not mat.use_nodes:
            debug_print(f"[WARNING] Material {mat.name if mat else 'None'} does not use nodes, skipping.")
            continue

        debug_print(f"[DEBUG] Processing material {mat.name}")

        node_tree = mat.node_tree

        # Find the Principled BSDF node
        principled_node = next((node for node in node_tree.nodes if node.type == 'BSDF_PRINCIPLED'), None)
        if not principled_node:
            debug_print(f"[WARNING] No Principled BSDF found in material {mat.name}, skipping.")
            continue

        # Find the Material Output node by type
        material_output_node = next((node for node in node_tree.nodes if node.type == 'OUTPUT_MATERIAL'), None)
        if not material_output_node:
            debug_print(f"[ERROR] No Material Output node found in material {mat.name}, skipping.")
            continue

        # Store original links to the Material Output node
        original_links = [
            (link.from_socket, link.to_socket)
            for link in node_tree.links
            if link.to_node == material_output_node
        ]
        original_links_dict[mat] = original_links

        # Create a list to store added nodes
        added_nodes = []

        # Create an Emission shader for baking
        emission_node = node_tree.nodes.new(type='ShaderNodeEmission')
        emission_node.location = principled_node.location
        emission_node.location.x -= 200
        added_nodes.append(emission_node)

        # Get the Metallic input from the Principled BSDF
        metallic_input = principled_node.inputs.get('Metallic')
        if metallic_input:
            if metallic_input.is_linked:
                try:
                    # Link the Metallic input source to the Emission shader
                    node_tree.links.new(metallic_input.links[0].from_socket, emission_node.inputs['Color'])
                    debug_print(f"[DEBUG] Metallic input linked for material {mat.name}.")
                except Exception as e:
                    debug_print(f"[ERROR] Failed to link Metallic input for material {mat.name}: {e}")
                    continue
            else:
                try:
                    # Use the Metallic value from the Principled BSDF
                    metallic_value = metallic_input.default_value
                    emission_node.inputs['Color'].default_value = (metallic_value, metallic_value, metallic_value, 1.0)
                    debug_print(f"[DEBUG] Metallic input not linked for material {mat.name}, using value {metallic_value}.")
                except AttributeError as e:
                    debug_print(f"[ERROR] Failed to access Metallic input value for material {mat.name}: {e}")
                    continue
        else:
            # Fallback if Metallic input doesn't exist
            emission_node.inputs['Color'].default_value = (0.0, 0.0, 0.0, 1.0)
            debug_print(f"[WARNING] No Metallic input found in material {mat.name}, using default value (0.0).")

        # Link the Emission shader to the Material Output node
        try:
            node_tree.links.new(emission_node.outputs['Emission'], material_output_node.inputs['Surface'])
        except Exception as e:
            debug_print(f"[ERROR] Failed to connect Emission shader to Material Output for material {mat.name}: {e}")
            continue

        # Assign the bake image to the active material for baking
        try:
            image_node = node_tree.nodes.new(type='ShaderNodeTexImage')
            image_node.image = image
            node_tree.nodes.active = image_node  # Set this node as active for baking
            added_nodes.append(image_node)
        except Exception as e:
            debug_print(f"[ERROR] Failed to assign image node for material {mat.name}: {e}")
            continue

        # Store the added nodes for cleanup
        added_nodes_dict[mat] = added_nodes

    # Perform the bake for the entire object
    bpy.context.scene.cycles.bake_type = 'EMIT'
    try:
        bpy.ops.object.bake(type='EMIT')
        debug_print(f"[DEBUG] Successfully baked metallic map for {obj.name}")
    except RuntimeError as e:
        debug_print(f"[ERROR] Error during baking for object {obj.name}: {e}")

    # Restore the original material links and remove added nodes
    for mat in obj.data.materials:
        if not mat or not mat.use_nodes:
            continue

        node_tree = mat.node_tree

        # Remove only the nodes that were added
        added_nodes = added_nodes_dict.get(mat, [])
        for node in added_nodes:
            node_tree.nodes.remove(node)

        # Restore original links to the Material Output node
        material_output_node = next((node for node in node_tree.nodes if node.type == 'OUTPUT_MATERIAL'), None)
        original_links = original_links_dict.get(mat, [])
        # Remove all links to the Material Output node
        for link in list(node_tree.links):
            if link.to_node == material_output_node:
                node_tree.links.remove(link)
        # Restore the original links
        for from_socket, to_socket in original_links:
            node_tree.links.new(from_socket, to_socket)
        debug_print(f"[DEBUG] Restored original shader connections for material {mat.name}")

def bake_metallic_map(obj, image):
    """Bakes the metallic map into the provided image."""
    assign_image_to_material(obj, image, "Metallic")
    bpy.context.scene.cycles.bake_type = 'COMBINED'  # Use COMBINED for metallic
    bpy.ops.object.bake(type='COMBINED')

def bake_roughness_map(obj, image):
    """Bakes the roughness map into the provided image."""
    assign_image_to_material(obj, image, "Roughness")
    bpy.context.scene.cycles.bake_type = 'ROUGHNESS'
    bpy.ops.object.bake(type='ROUGHNESS')
 
def bake_alpha_map(obj, resolution, save_dir):
    """Bake the alpha channel for all materials on the object into a single image."""
    assetify_settings = bpy.context.scene.assetify_bake_settings
    ensure_cycles_render_engine()
    
    resolution = int(resolution)
    
    if assetify_settings.texturebake_mode == 'ANIMATION':
        # Create a subfolder for the object being baked
        object_folder = os.path.join(save_dir, f"{obj.name}_textures")
        os.makedirs(object_folder, exist_ok=True)

        # Create a subfolder for the Alpha map
        map_folder = os.path.join(object_folder, "Alpha")
        os.makedirs(map_folder, exist_ok=True)

        # Generate a file name based on the current frame
        frame_number = bpy.context.scene.frame_current
        file_name = f"Frame{frame_number:04d}.png"
        texture_file_path = os.path.join(map_folder, file_name)
    else:
        # For STILL mode, save directly in the "textures" folder
        textures_folder = os.path.join(save_dir, "textures")
        os.makedirs(textures_folder, exist_ok=True)

        # Save the Alpha map as a single file
        texture_file_path = os.path.join(textures_folder, f"{obj.name}_Alpha.png")

    # Delete the file if it already exists
    if os.path.exists(texture_file_path):
        os.remove(texture_file_path)
        print(f"[DEBUG] Removed existing file: {texture_file_path}")

    # Create a new bake image for each frame
    image_name = f"{obj.name}_Alpha_Frame{bpy.context.scene.frame_current:04d}"
    image = bpy.data.images.get(image_name) or bpy.data.images.new(
        name=image_name,
        width=resolution,
        height=resolution,
        alpha=True
    )
    image.colorspace_settings.name = 'Non-Color'

    # Clean up previously added image nodes and add a new one
    for mat_slot in obj.material_slots:
        if mat_slot.material and mat_slot.material.use_nodes:
            node_tree = mat_slot.material.node_tree

            # Remove previously added texture nodes for this bake
            nodes_to_remove = [
                node for node in node_tree.nodes
                if node.type == 'TEX_IMAGE' and node.image and node.image.name.startswith(f"{obj.name}_Alpha_Frame")
            ]
            for node in nodes_to_remove:
                node_tree.nodes.remove(node)

            # Create a new texture node for the current frame
            tex_node = node_tree.nodes.new(type='ShaderNodeTexImage')
            tex_node.image = image
            node_tree.nodes.active = tex_node  # Set as active for baking

    # Store original connections and modifications
    materials_original_links = {}
    temporary_output_nodes = {}

    for mat_slot in obj.material_slots:
        mat = mat_slot.material
        if not mat or not mat.use_nodes:
            debug_print(f"[WARNING] Material {mat.name if mat else 'None'} does not use nodes, skipping.")
            continue

        debug_print(f"[DEBUG] Processing material {mat.name}")

        node_tree = mat.node_tree

        # Collect all Principled BSDF nodes in the material's node tree
        principled_nodes = [node for node in node_tree.nodes if node.type == 'BSDF_PRINCIPLED']
        if not principled_nodes:
            debug_print(f"[WARNING] No Principled BSDF nodes found in the material's node tree.")
            continue
        else:
            debug_print(f"[DEBUG] Found {len(principled_nodes)} Principled BSDF node(s) in the material.")

        # Function to count the number of connected inputs for a node
        def count_connected_inputs(node):
            return sum(1 for input in node.inputs if input.is_linked)

        # Find the node with the most connected inputs
        principled_node = max(principled_nodes, key=count_connected_inputs)
        connected_inputs_count = count_connected_inputs(principled_node)
        debug_print(f"[DEBUG] Selected Principled BSDF node '{principled_node.name}' with {connected_inputs_count} connected input(s).")

        # Access the 'Alpha' input of the selected Principled BSDF node
        alpha_input = principled_node.inputs.get('Alpha')
        if alpha_input:
            is_linked = alpha_input.is_linked
            debug_print(f"[DEBUG] 'Alpha' input is linked: {is_linked}")
            if is_linked:
                # There might be multiple links; iterate over them
                for link in alpha_input.links:
                    from_node = link.from_node
                    from_socket = link.from_socket
                    debug_print(f"[DEBUG] 'Alpha' input is linked to node: '{from_node.name}' (type: {from_node.type})")
                    debug_print(f"[DEBUG] From socket: '{from_socket.name}' (type: {from_socket.type})")
                # Use the first linked socket as the alpha source
                alpha_source = alpha_input.links[0].from_socket
            else:
                # If not linked, use the default value
                default_value = alpha_input.default_value
                debug_print(f"[DEBUG] 'Alpha' input is not linked. Default value: {default_value}")
                alpha_source = None
        else:
            debug_print("[WARNING] 'Alpha' input not found on the Principled BSDF node.")
            alpha_source = None

        # Find the Material Output node
        material_output_node = next((node for node in node_tree.nodes if node.type == 'OUTPUT_MATERIAL'), None)
        if not material_output_node:
            # Create a temporary Material Output node if missing
            material_output_node = node_tree.nodes.new(type='ShaderNodeOutputMaterial')
            material_output_node.location = (0, 0)
            temporary_output_nodes[mat.name] = material_output_node
            debug_print(f"[INFO] Created temporary Material Output node for material {mat.name}.")

        # Store original links to the Material Output node
        original_links = [
            (link.from_socket, link.to_socket)
            for link in node_tree.links
            if link.to_node == material_output_node
        ]
        materials_original_links[mat.name] = original_links

        # Disconnect existing links to the Material Output node
        for from_socket, to_socket in original_links:
            for link in node_tree.links:
                if link.from_socket == from_socket and link.to_socket == to_socket:
                    node_tree.links.remove(link)
                    break

        # Create an Emission shader
        emission_node = node_tree.nodes.new(type='ShaderNodeEmission')
        emission_node.location = (principled_node.location.x - 200, principled_node.location.y)

        if alpha_source:
            try:
                # Handle float to color conversion if needed
                if alpha_source.type in {'VALUE', 'FLOAT'}:
                    combine_node = node_tree.nodes.new(type='ShaderNodeCombineRGB')
                    combine_node.location = (alpha_source.node.location.x - 200, alpha_source.node.location.y)
                    node_tree.links.new(alpha_source, combine_node.inputs['R'])
                    node_tree.links.new(alpha_source, combine_node.inputs['G'])
                    node_tree.links.new(alpha_source, combine_node.inputs['B'])
                    node_tree.links.new(combine_node.outputs['Image'], emission_node.inputs['Color'])
                    debug_print(f"[DEBUG] Connected Alpha source through CombineRGB node for material {mat.name}.")
                else:
                    node_tree.links.new(alpha_source, emission_node.inputs['Color'])
                    debug_print(f"[DEBUG] Linked Alpha source to Emission shader for material {mat.name}.")
            except Exception as e:
                debug_print(f"[ERROR] Failed to link Alpha source for material {mat.name}: {e}")
        else:
            # Use default alpha value
            if alpha_input:
                default_value = alpha_input.default_value
            else:
                default_value = 1.0
            emission_node.inputs['Color'].default_value = (default_value, default_value, default_value, 1.0)
            debug_print(f"[DEBUG] Using default Alpha value {default_value} for material {mat.name}.")

        # Link the Emission shader to the Material Output node
        try:
            node_tree.links.new(emission_node.outputs['Emission'], material_output_node.inputs['Surface'])
            debug_print(f"[DEBUG] Connected Emission shader to Material Output for material {mat.name}.")
        except Exception as e:
            debug_print(f"[ERROR] Failed to connect Emission shader to Material Output for material {mat.name}: {e}")

        # Assign the bake image to the material
        image_node = node_tree.nodes.new(type='ShaderNodeTexImage')
        image_node.image = image
        node_tree.nodes.active = image_node  # Set this node as active for baking

    # Perform the bake for all materials on the object
    bpy.context.scene.cycles.bake_type = 'EMIT'
    try:
        bpy.ops.object.bake(type='EMIT')
        debug_print(f"[DEBUG] Successfully baked Alpha map for {obj.name}")
    except RuntimeError as e:
        debug_print(f"[ERROR] Error during baking for object {obj.name}: {e}")

    # Save the baked image
    try:
        image.filepath_raw = texture_file_path
        image.file_format = 'PNG'
        image.save()
        debug_print(f"Baked Alpha map saved at {image.filepath_raw}")
    except Exception as e:
        debug_print(f"[ERROR] Error saving Alpha map for {obj.name}: {e}")
        
    # Restore original material setup
    for mat_slot in obj.material_slots:
        mat = mat_slot.material
        if not mat or not mat.use_nodes:
            continue

        node_tree = mat.node_tree

        # Remove Emission, CombineRGB, and Image nodes
        nodes_to_remove = [node for node in node_tree.nodes if node.type in {'EMISSION', 'TEX_IMAGE', 'COMBRGB'}]
        for node in nodes_to_remove:
            node_tree.nodes.remove(node)

        # Remove temporary Material Output nodes
        if mat.name in temporary_output_nodes:
            node_tree.nodes.remove(temporary_output_nodes[mat.name])

        # Restore original links to the Material Output node
        material_output_node = next((node for node in node_tree.nodes if node.type == 'OUTPUT_MATERIAL'), None)
        original_links = materials_original_links.get(mat.name, [])
        # Remove all links to the Material Output node
        for link in list(node_tree.links):
            if link.to_node == material_output_node:
                node_tree.links.remove(link)
        # Restore the original links
        for from_socket, to_socket in original_links:
            node_tree.links.new(from_socket, to_socket)
        debug_print(f"[DEBUG] Restored original shader connections for material {mat.name}")        

def apply_baked_textures(obj, save_dir, platform="UE5"):
    """Apply the baked textures to the object's material by setting up a Principled BSDF shader."""
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
    bsdf_node.location = (200, 0)
    
    # Create a new Translucent BSDF node
    #translucent_node = node_tree.nodes.new(type='ShaderNodeBsdfTranslucent')
    #translucent_node.location = (0, 400)  # Position it below the Principled BSDF
    
    # Create an Add Shader node
    #add_shader_node = node_tree.nodes.new(type='ShaderNodeAddShader')
    #add_shader_node.location = (550, 0)

    # Create a Material Output node
    output_node = node_tree.nodes.new(type='ShaderNodeOutputMaterial')
    output_node.location = (700, 0)
    
    # Add a H/S/V node
    #hsv_node = node_tree.nodes.new(type='ShaderNodeHueSaturation')
    #hsv_node.location = (-200, 400)
    #hsv_node.inputs['Value'].default_value = 0.5
    
    # Connect the Principled BSDF node to the Add Shader node
    #node_tree.links.new(bsdf_node.outputs['BSDF'], add_shader_node.inputs[0])
    
    # Connect the Principled BSDF node to the Add Shader node
    node_tree.links.new(bsdf_node.outputs['BSDF'], output_node.inputs['Surface'])

    # Connect the Translucent BSDF node to the Add Shader node
    #node_tree.links.new(translucent_node.outputs['BSDF'], add_shader_node.inputs[1])
    
    # Connect the Add Shader node to the Material Output node
    #node_tree.links.new(add_shader_node.outputs['Shader'], output_node.inputs['Surface'])

    # Define the path for the texture folder (inside the main save directory)
    textures_folder = os.path.join(save_dir, "textures")

    # Load and assign the BaseColor texture
    basecolor_path = os.path.join(textures_folder, f"{obj.name}_BaseColor.png")
    print(f"Looking for BaseColor texture at: {basecolor_path}")

    if os.path.exists(basecolor_path):
        basecolor_node = node_tree.nodes.new('ShaderNodeTexImage')
        basecolor_node.image = bpy.data.images.load(basecolor_path)
        basecolor_node.location = (-200, 250)
        basecolor_node.image.colorspace_settings.name = 'sRGB'
        node_tree.links.new(basecolor_node.outputs['Color'], bsdf_node.inputs['Base Color'])
        #node_tree.links.new(basecolor_node.outputs['Color'], hsv_node.inputs['Color'])
        #node_tree.links.new(hsv_node.outputs['Color'], translucent_node.inputs['Color'])
        print(f"Loaded BaseColor texture from {basecolor_path}")
    else:
        print(f"BaseColor texture not found at {basecolor_path}")

    # Load and assign the Normal map
    normal_path = os.path.join(textures_folder, f"{obj.name}_Normal.png")
    print(f"Looking for Normal texture at: {normal_path}")

    if os.path.exists(normal_path):
        normal_node = node_tree.nodes.new('ShaderNodeTexImage')
        normal_node.image = bpy.data.images.load(normal_path)
        normal_node.location = (-200, -500)
        normal_node.image.colorspace_settings.name = 'Non-Color'
        normal_map_node = node_tree.nodes.new('ShaderNodeNormalMap')
        normal_map_node.location = (0, -500)
        node_tree.links.new(normal_node.outputs['Color'], normal_map_node.inputs['Color'])
        node_tree.links.new(normal_map_node.outputs['Normal'], bsdf_node.inputs['Normal'])
        print(f"Loaded Normal texture from {normal_path}")
    else:
        print(f"Normal texture not found at {normal_path}")

    if platform == 'UNITY':
        metallic_smoothness_path = os.path.join(textures_folder, f"{obj.name}_MetallicSmoothness.png")
        print(f"Looking for MetallicSmoothness texture at: {metallic_smoothness_path}")

        if os.path.exists(metallic_smoothness_path):
            metallic_smoothness_node = node_tree.nodes.new('ShaderNodeTexImage')
            metallic_smoothness_node.image = bpy.data.images.load(metallic_smoothness_path)
            metallic_smoothness_node.location = (-200, 0)
            metallic_smoothness_node.image.colorspace_settings.name = 'Non-Color'

            # Check if the image has an alpha channel (4 channels: RGBA)
            if metallic_smoothness_node.image.depth == 32:  # 32 bits = RGBA
                # Add Separate RGBA node
                separate_rgba_node = node_tree.nodes.new('ShaderNodeSeparateColor')
                separate_rgba_node.location = (0, 0)
                node_tree.links.new(metallic_smoothness_node.outputs['Color'], separate_rgba_node.inputs['Color'])

                # Connect Metallic (R channel)
                node_tree.links.new(separate_rgba_node.outputs['Red'], bsdf_node.inputs['Metallic'])

                # Connect Roughness (1 - A channel)
                invert_node = node_tree.nodes.new('ShaderNodeInvert')
                invert_node.location = (200, 0)
                node_tree.links.new(metallic_smoothness_node.outputs['Alpha'], invert_node.inputs['Color'])
                node_tree.links.new(invert_node.outputs['Color'], bsdf_node.inputs['Roughness'])

                print(f"Loaded and applied MetallicSmoothness texture from {metallic_smoothness_path}")
            else:
                print(f"Warning: {metallic_smoothness_path} does not contain an Alpha channel.")
        else:
            print(f"MetallicSmoothness texture not found at {metallic_smoothness_path}")

    else:
        # Handle separate Metallic and Roughness maps for Unreal Engine
        roughness_path = os.path.join(textures_folder, f"{obj.name}_Roughness.png")
        metallic_path = os.path.join(textures_folder, f"{obj.name}_Metallic.png")

        print(f"Looking for Roughness texture at: {roughness_path}")
        print(f"Looking for Metallic texture at: {metallic_path}")

        if os.path.exists(roughness_path):
            roughness_node = node_tree.nodes.new('ShaderNodeTexImage')
            roughness_node.image = bpy.data.images.load(roughness_path)
            roughness_node.location = (-200, 0)
            roughness_node.image.colorspace_settings.name = 'Non-Color'
            node_tree.links.new(roughness_node.outputs['Color'], bsdf_node.inputs['Roughness'])
            print(f"Loaded Roughness texture from {roughness_path}")
        else:
            print(f"Roughness texture not found at {roughness_path}")

        if os.path.exists(metallic_path):
            metallic_node = node_tree.nodes.new('ShaderNodeTexImage')
            metallic_node.image = bpy.data.images.load(metallic_path)
            metallic_node.location = (-200, -250)
            metallic_node.image.colorspace_settings.name = 'Non-Color'
            node_tree.links.new(metallic_node.outputs['Color'], bsdf_node.inputs['Metallic'])
            print(f"Loaded Metallic texture from {metallic_path}")
        else:
            print(f"Metallic texture not found at {metallic_path}")

    # Load and assign the Alpha map
    alpha_path = os.path.join(textures_folder, f"{obj.name}_Alpha.png")
    print(f"Looking for Alpha texture at: {alpha_path}")

    if os.path.exists(alpha_path):
        alpha_node = node_tree.nodes.new('ShaderNodeTexImage')
        alpha_node.image = bpy.data.images.load(alpha_path)
        alpha_node.location = (-200, -750)
        alpha_node.image.colorspace_settings.name = 'Non-Color'
        node_tree.links.new(alpha_node.outputs['Color'], bsdf_node.inputs['Alpha'])
        print(f"Loaded Alpha texture from {alpha_path}")
    else:
        print(f"Alpha texture not found at {alpha_path}")

    print(f"Applied baked textures to material of {obj.name}")

def simplify_materials_and_uv_maps(obj):
    
    """
    Removes all UV maps except 'GameUV' and skips renaming.
    """
    
    # Remove all material slots except the first one
    if len(obj.data.materials) > 1:
        for i in range(len(obj.data.materials) - 1, 0, -1):  # Start from the end and remove backwards
            obj.data.materials.pop(index=i)
        debug_print(f"Removed all material slots except the first one for {obj.name}")
    else:
        debug_print(f"No extra material slots to remove for {obj.name}")
    
    game_uv_name = "GameUV"
    
    # Check if the object supports UV layers
    if not hasattr(obj.data, 'uv_layers'):
        debug_print(f"Object '{obj.name}' does not support UV layers (type: {obj.type}). Skipping.")
        return

    # Confirm 'GameUV' exists to avoid issues
    game_uv = obj.data.uv_layers.get(game_uv_name)
    if not game_uv:
        debug_print(f"Error: Expected 'GameUV' not found for {obj.name}")
        return

    # Gather names of all UV maps
    uv_map_names = [uv_layer.name for uv_layer in obj.data.uv_layers]
    debug_print(f"Initial UV maps for {obj.name}: {uv_map_names}")

    # Progressively remove UV maps that are not 'GameUV'
    for uv_name in uv_map_names:
        if uv_name != game_uv_name:
            uv_layer = obj.data.uv_layers.get(uv_name)
            if uv_layer:
                obj.data.uv_layers.remove(uv_layer)
                debug_print(f"Removed UV map '{uv_name}' from {obj.name}")

    # Final list of remaining UV maps for verification
    remaining_uv_maps = [uv.name for uv in obj.data.uv_layers]
    debug_print(f"Final UV maps for {obj.name}: {remaining_uv_maps}")

def finalize_uv_maps(obj):
    """
    Ensures 'GameUV' is renamed to 'UVMap' and only one UV map is left at the end.
    """
    
    # Check if the object supports UV layers
    if not hasattr(obj.data, 'uv_layers'):
        debug_print(f"Object '{obj.name}' does not support UV layers (type: {obj.type}). Skipping.")
        return
    
    uvmap_name = "UVMap"
    gameuv_name = "GameUV"

    # Check if 'GameUV' exists and rename it to 'UVMap'
    game_uv = obj.data.uv_layers.get(gameuv_name)
    if game_uv:
        game_uv.name = uvmap_name
        obj.data.uv_layers.active = game_uv  # Ensure it is the active UV map
        debug_print(f"Renamed 'GameUV' to 'UVMap' for {obj.name}")
    else:
        # If 'GameUV' doesn't exist, ensure we don't accidentally delete all UV maps
        active_uv = obj.data.uv_layers.active
        if active_uv:
            active_uv.name = uvmap_name
            debug_print(f"Set '{active_uv.name}' as 'UVMap' for {obj.name}")

# === Operator Classes and Panel ===

class ASSETIFY_OT_swap_collections(bpy.types.Operator):
    """Swap assets between Original and Game-Ready collections, including all subcollections"""
    bl_idname = "assetify.swap_collections"
    bl_label = "Swap Original & Game Assets"

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings

        # Flag to check if any collections are marked for swapping
        any_collections_marked = False

        # Iterate over all baked collections
        for baked_collection in assetify_settings.baked_collections:
            if baked_collection.include_in_send:
                any_collections_marked = True
                print(f"[Assetify] Swapping assets for collection: {baked_collection.name}")

                # Get the original collection and game-ready collection
                original_collection_name = baked_collection.name.replace("_GameReady", "")
                original_collection = bpy.data.collections.get(original_collection_name)
                game_ready_collection = bpy.data.collections.get(baked_collection.name)

                if not original_collection or not game_ready_collection:
                    print(f"[Assetify] Either original or game-ready collection not found for: {baked_collection.name}")
                    self.report({'WARNING'}, f"Collections for swapping not found: {original_collection_name} <-> {baked_collection.name}")
                    continue

                # Swap objects between original and game-ready collections recursively
                self.swap_objects_recursive(
                    original_collection,
                    game_ready_collection,
                    baked_collection.assets_swapped,
                    assetify_settings
                )

                # Toggle the collection's assets_swapped status
                baked_collection.assets_swapped = not baked_collection.assets_swapped

        # If no collections were marked, provide feedback and cancel the operation
        if not any_collections_marked:
            self.report({'INFO'}, "No collections marked for swapping.")
            return {'CANCELLED'}

        # Update the UI to reflect changes
        for area in bpy.context.screen.areas:
            area.tag_redraw()

        self.report({'INFO'}, "Swapped Original & Game Assets successfully.")
        return {'FINISHED'}
    
    def swap_objects_recursive(self, original_collection, game_ready_collection, assets_swapped, assetify_settings):
        print(f"[Assetify] Swapping objects in collection: '{original_collection.name}' <-> '{game_ready_collection.name}'")

        if not assets_swapped:
            # First swap: Swap from Original to Game-Ready
            # Move original objects to game-ready collection
            for obj in list(original_collection.objects):
                if "_gameasset" not in obj.name:
                    game_ready_collection.objects.link(obj)
                    original_collection.objects.unlink(obj)
                    print(f"[Assetify] Moved '{obj.name}' to Game-Ready collection.")

            # Move game-ready objects to original collection
            for obj in list(game_ready_collection.objects):
                if "_gameasset" in obj.name:
                    original_collection.objects.link(obj)
                    game_ready_collection.objects.unlink(obj)
                    print(f"[Assetify] Moved '{obj.name}' back to Original collection.")

        else:
            # Swap back from Game-Ready to Original
            # Move original objects back to original collection
            for obj in list(game_ready_collection.objects):
                if "_gameasset" not in obj.name:
                    original_collection.objects.link(obj)
                    game_ready_collection.objects.unlink(obj)
                    print(f"[Assetify] Moved '{obj.name}' back to Original collection.")

            # Move game-ready objects back to game-ready collection
            for obj in list(original_collection.objects):
                if "_gameasset" in obj.name:
                    game_ready_collection.objects.link(obj)
                    original_collection.objects.unlink(obj)
                    print(f"[Assetify] Moved '{obj.name}' to Game-Ready collection.")

        # Handle subcollections recursively
        for original_subcol in original_collection.children:
            game_ready_subcol = game_ready_collection.children.get(original_subcol.name + "_GameReady")
            if game_ready_subcol:
                self.swap_objects_recursive(
                    original_subcol,
                    game_ready_subcol,
                    assets_swapped,
                    assetify_settings
                )

        print(f"[Assetify] Completed swapping for '{original_collection.name}' and '{game_ready_collection.name}'.")

    def update_baked_collection_asset(self, assetify_settings, obj_name, new_collection):
        """
        Update the baked_collection entry for the given asset to reflect its new collection after a swap.
        This keeps the asset logically linked to the collection, regardless of where it's moved.
        """
        for baked_collection in assetify_settings.baked_collections:
            if obj_name in baked_collection.assets:
                # Update the asset's collection reference
                print(f"[DEBUG] Updating collection for asset '{obj_name}' to '{new_collection.name}'")
                baked_collection.collection = new_collection.name
                break

def rebuild_main_collection_assets(assetify_settings):
    """
    After a swap, rebuild the main collection asset list to ensure all game assets
    are correctly linked to their respective collections.
    """
    print(f"[DEBUG] Rebuilding main collection assets for collection: {assetify_settings.main_collection}")
    assetify_settings.main_collection_assets.clear()

    # Iterating through all collections
    for collection in bpy.data.collections:
        # Iterating through the objects in each collection
        for obj in collection.objects:
            if "_gameasset" in obj.name:
                assetify_settings.main_collection_assets.append(obj.name)
                print(f"[DEBUG] Added '{obj.name}' to main collection assets.")

    print(f"[DEBUG] Main collection assets: {assetify_settings.main_collection_assets}")

def switch_to_solid_shading_and_back():
    """Switches to solid shading and back to rendered if user is in rendered mode."""
    # Get the current 3D view area
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            space = area.spaces.active
            current_shading_type = space.shading.type
            
            if current_shading_type == 'RENDERED':
                # Switch to solid view
                space.shading.type = 'SOLID'
                return_to_rendered = True
            else:
                return_to_rendered = False
            
            return return_to_rendered

def set_viewport_shading_to_solid(context):
    """
    Sets the 3D Viewport shading to solid mode to prevent GPU crashes during baking.
    """
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    space.shading.type = 'SOLID'
                    print("[DEBUG] Viewport shading set to SOLID.")

def restore_viewport_shading(return_to_rendered):
    """Restores the viewport shading to rendered mode if it was changed."""
    # Get the current 3D view area
    for area in bpy.context.screen.areas:
        if area.type == 'VIEW_3D':
            space = area.spaces.active
            if return_to_rendered:
                # Switch back to rendered view
                space.shading.type = 'RENDERED'

class OBJECT_OT_ApplyCustomGeometryNodes(bpy.types.Operator):
    bl_idname = "object.apply_custom_geometry_nodes"
    bl_label = "Apply Custom Geometry Nodes"
    bl_description = "Apply custom Geometry Nodes setup with color and value attributes"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        scene = context.scene
        obj = context.object
        custom_object = scene.custom_object
        
        if obj is None or custom_object is None:
            self.report({'ERROR'}, "Active object or custom object input is missing.")
            return {'CANCELLED'}
        
        customize_color(obj, custom_object)
        return {'FINISHED'}

class ASSETIFY_OT_confirm_export_with_unbaked(bpy.types.Operator):
    """Confirmation popup to export assets even if some are unbaked"""
    bl_idname = "assetify.confirm_export_with_unbaked"
    bl_label = "Unbaked Assets Found"

    def execute(self, context):
        # Set the flag to confirm export
        context.window_manager.confirm_export = True
        # Re-invoke the export operator
        bpy.ops.assetify.export_selected_assets_as_fbx('EXEC_DEFAULT')
        return {'FINISHED'}

    def invoke(self, context, event):
        # Show the dialog for unbaked assets confirmation
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        unbaked_assets = context.window_manager.unbaked_assets.split(", ")
        layout.label(text="The following assets are unbaked:")
        for asset in unbaked_assets:
            layout.label(text=f" - {asset}")
        layout.label(text="Are you sure you want to proceed with the export?")

class ASSETIFY_OT_separate_by_material(bpy.types.Operator):
    """Separate selected assets or collection assets by material with progress bar"""
    bl_idname = "assetify.separate_by_material"
    bl_label = "Separate by Material"
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _separation_index = 0
    _current_material_index = 0
    _objects_to_separate = []
    total_separation_steps = 0
    progress_value = 0.0

    current_operation = "Separating by Material..."
    draw_handler = None
    space_reference = None

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings

        # Collect assets to separate based on the mode
        if assetify_settings.asset_mode == 'ASSET':
            self._objects_to_separate = [
                bpy.data.objects.get(asset.name) for asset in assetify_settings.baked_assets
                if asset.include_in_send  # Ensure asset is marked for processing
            ]
        elif assetify_settings.asset_mode == 'COLLECTION':
            self._objects_to_separate = []
            for baked_collection in assetify_settings.baked_collections:
                if baked_collection.include_in_send:
                    collection = bpy.data.collections.get(baked_collection.name)
                    if collection:
                        self._objects_to_separate.extend(self.collect_objects_from_collection(collection))

        # Filter out invalid objects
        self._objects_to_separate = [
            obj for obj in self._objects_to_separate if obj and obj.type == 'MESH'
        ]

        # Check if any objects are available for separation
        if not self._objects_to_separate:
            self.report({'ERROR'}, "No valid assets found to separate.")
            return {'CANCELLED'}

        # Initialize progress tracking
        self.total_separation_steps = len(self._objects_to_separate)
        self.progress_value = 0.0
        self.current_operation = "Separating by Material..."

        # Start the progress bar and modal timer
        self.start_progress_bar(context)
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'TIMER':
            if self._separation_index < len(self._objects_to_separate):
                obj = self._objects_to_separate[self._separation_index]

                # Separate object by each material
                if self._current_material_index < len(obj.material_slots):
                    self.separate_material_step(obj)
                    self._current_material_index += 1
                    self.progress_value = (self._separation_index + self._current_material_index / len(obj.material_slots)) / self.total_separation_steps
                else:
                    # Move to next object after finishing all materials
                    self._separation_index += 1
                    self._current_material_index = 0
            else:
                # Clean up: Delete original objects after separation is complete
                for original_obj in self._objects_to_separate:
                    if original_obj and original_obj.name in bpy.data.objects:
                        bpy.data.objects.remove(original_obj, do_unlink=True)

                # Refresh baked assets list after separation
                populate_baked_assets_from_scene(context.scene.assetify_bake_settings)

                # Separation complete
                self.end_progress_bar()
                context.window_manager.event_timer_remove(self._timer)
                self.report({'INFO'}, "Separation by material complete.")
                return {'FINISHED'}

            # Update UI
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

        return {'PASS_THROUGH'}

    def separate_material_step(self, obj):
        """Separates the object by the current material index, renames, and assigns only relevant material."""
        material_slot = obj.material_slots[self._current_material_index]
        material = material_slot.material
        self.current_material = material.name  # Update the material name
        
        if not material:
            return  # Skip empty material slots

        # Set the active material index
        obj.active_material_index = self._current_material_index

        # Enter edit mode to separate based on material
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='DESELECT')
        bpy.ops.object.material_slot_select()
        bpy.ops.mesh.separate(type='SELECTED')
        bpy.ops.object.mode_set(mode='OBJECT')
        
        # Get the new object and set its name and material
        new_obj = [o for o in bpy.context.selected_objects if o != obj][0]
        new_obj.name = f"{obj.name}_{material.name}".replace(f"{obj.name}_", "", 1)

        # Clear and reassign materials
        new_obj.data.materials.clear()
        new_obj.data.materials.append(material)

        # Deselect new object to prepare for next separation
        new_obj.select_set(False)

    def start_progress_bar(self, context):
        """Initialize progress bar."""
        override = self.set_active_3d_view()
        if override:
            space = override['space']
            region = override['region']
            self.draw_handler = space.draw_handler_add(self.draw_progress_bar, (space, region), 'WINDOW', 'POST_PIXEL')
            self.space_reference = space

    def end_progress_bar(self):
        """End and remove progress bar."""
        if self.draw_handler and self.space_reference:
            self.space_reference.draw_handler_remove(self.draw_handler, 'WINDOW')
            self.draw_handler = None
            self.space_reference = None

    def draw_progress_bar(self, space, region):
        """Draws the progress bar overlay in the 3D view."""
        width, height = region.width, region.height
        bar_width, bar_height = 300, 30
        x_pos, y_pos = (width - bar_width) / 2, height - 100

        shader = gpu.shader.from_builtin('UNIFORM_COLOR')  # Ensure the correct shader type
        
        # Background rectangle
        vertices = [(x_pos, y_pos), (x_pos + bar_width, y_pos), (x_pos + bar_width, y_pos + bar_height), (x_pos, y_pos + bar_height)]
        batch = batch_for_shader(shader, 'TRI_FAN', {"pos": vertices})
        shader.bind()
        shader.uniform_float("color", (0.2, 0.2, 0.2, 0.8))
        batch.draw(shader)

        # Progress rectangle
        progress_width = bar_width * self.progress_value
        progress_vertices = [(x_pos, y_pos), (x_pos + progress_width, y_pos), (x_pos + progress_width, y_pos + bar_height), (x_pos, y_pos + bar_height)]
        progress_batch = batch_for_shader(shader, 'TRI_FAN', {"pos": progress_vertices})
        shader.uniform_float("color", (0.0, 0.8, 0.0, 0.8))
        progress_batch.draw(shader)

        # Progress text
        blf.position(0, x_pos + 10, y_pos + 5, 0)
        blf.size(0, 24)  # Set font size correctly with only two arguments
        blf.draw(0, f"Progress: {int(self.progress_value * 100)}%")
        
        # Draw current material name below the progress bar
        if self.current_material:
            blf.position(0, x_pos, y_pos - 25, 0)  # 60px below the progress bar
            blf.size(0, 18)  # Set font size for material name
            blf.draw(0, f"Separating Material: {self.current_material}")

    def collect_objects_from_collection(self, collection):
        objects = []
        def collect_from_collection(col):
            for obj in col.objects:
                if obj.type == 'MESH':
                    objects.append(obj)
            for subcol in col.children:
                collect_from_collection(subcol)
        collect_from_collection(collection)
        return objects

    def set_active_3d_view(self):
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    for region in area.regions:
                        if region.type == 'WINDOW':
                            for space in area.spaces:
                                if space.type == 'VIEW_3D':
                                    return {
                                        'window': window,
                                        'screen': window.screen,
                                        'area': area,
                                        'region': region,
                                        'space': space
                                    }
        return None

class ASSETIFY_OT_join_assets(bpy.types.Operator):
    """Join selected assets or collection assets into one object"""
    bl_idname = "assetify.join_assets"
    bl_label = "Join Assets"
    bl_options = {'REGISTER', 'UNDO'}

    joined_name: bpy.props.StringProperty(name="Joined Object Name", default="JoinedAsset")

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        objects_to_join = []

        # Determine which objects to join based on the mode
        if assetify_settings.asset_mode == 'ASSET':
            objects_to_join = [
                bpy.data.objects.get(asset.name) for asset in assetify_settings.baked_assets
                if asset.include_in_send
            ]
        elif assetify_settings.asset_mode == 'COLLECTION':
            for baked_collection in assetify_settings.baked_collections:
                if baked_collection.include_in_send:
                    collection = bpy.data.collections.get(baked_collection.name)
                    if collection:
                        objects_to_join.extend(self.collect_objects_from_collection(collection))

        # Filter out any invalid objects
        objects_to_join = [obj for obj in objects_to_join if obj and obj.type == 'MESH']
        
        # Ensure there are objects to join
        if not objects_to_join:
            self.report({'ERROR'}, "No valid assets found to join.")
            return {'CANCELLED'}
        
        # Join the selected objects
        self.join_objects(context, objects_to_join)

        return {'FINISHED'}

    def invoke(self, context, event):
        # Open the pop-up for naming
        return context.window_manager.invoke_props_dialog(self)

    def join_objects(self, context, objects):
        assetify_settings = context.scene.assetify_bake_settings

        # Ensure there's at least one object to join and set an active object
        if not objects:
            self.report({'ERROR'}, "No objects selected for joining.")
            return

        # Gather object names and collections before joining
        object_names = [obj.name for obj in objects]
        collections_to_link = set()
        all_baked = True  # Track if all assets being joined are baked

        for obj in objects:
            # Collect all collections this object is part of
            for collection in obj.users_collection:
                collections_to_link.add(collection)
                print(f"[DEBUG] Asset '{obj.name}' is part of collection '{collection.name}'.")

            # Check the baked status of each object
            for asset in assetify_settings.baked_assets:
                if asset.name == obj.name:
                    if not asset.is_baked:
                        all_baked = False

        # Add the joined asset to baked collections before the join operation
        for collection in collections_to_link:
            for baked_collection in assetify_settings.baked_collections:
                if baked_collection.name == collection.name:
                    joined_asset_entry = baked_collection.assets.add()
                    joined_asset_entry.name = self.joined_name
                    print(f"[DEBUG] Pre-joining: Added '{self.joined_name}' to collection '{baked_collection.name}'.")

        # Deselect all objects first, then select the objects to join
        bpy.ops.object.select_all(action='DESELECT')
        for obj in objects:
            obj.select_set(True)
        context.view_layer.objects.active = objects[0]  # Set the first object as the active object

        # Ensure we're in Object mode to perform the join operation
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Join selected objects
        bpy.ops.object.join()

        # Get the newly joined object and rename it
        joined_object = context.view_layer.objects.active
        joined_object.name = self.joined_name

        # Link the joined object to all the collections the original objects were part of
        for collection in collections_to_link:
            if joined_object.name not in [obj.name for obj in collection.objects]:
                collection.objects.link(joined_object)
                print(f"[DEBUG] Joined object '{joined_object.name}' linked to collection '{collection.name}'.")

        # Update the baked assets list: Remove old entries by index and add a new entry for the joined object
        if assetify_settings.asset_mode == 'ASSET':
            # Remove old entries based on pre-join object names
            for asset_name in object_names:
                for i, asset in enumerate(assetify_settings.baked_assets):
                    if asset.name == asset_name:
                        print(f"[DEBUG] Removing asset '{asset.name}' from baked assets list.")
                        assetify_settings.baked_assets.remove(i)
                        break  # Exit the inner loop to avoid altering list during iteration

            # Add a new entry for the joined object
            new_asset = assetify_settings.baked_assets.add()
            new_asset.name = self.joined_name
            new_asset.is_baked = all_baked  # Set baked status based on all components
            new_asset.is_game_asset = True
            print(f"[DEBUG] Added joined asset '{new_asset.name}' to baked assets list with baked status '{new_asset.is_baked}'.")

        # Handle collection mode: Update the collection assets list if applicable
        if assetify_settings.asset_mode == 'COLLECTION':
            for baked_collection in assetify_settings.baked_collections:
                collection_assets = [asset.name for asset in baked_collection.assets]
                if any(name in collection_assets for name in object_names):
                    print(f"[DEBUG] Clearing assets in collection '{baked_collection.name}'.")
                    baked_collection.assets.clear()  # Clear existing assets
                    joined_asset = baked_collection.assets.add()
                    joined_asset.name = self.joined_name
                    joined_asset.is_baked = all_baked  # Set baked status based on all components
                    joined_asset.is_game_asset = True
                    print(f"[DEBUG] Added joined asset '{joined_asset.name}' to collection '{baked_collection.name}' with baked status '{joined_asset.is_baked}'.")

        # Verify and log the final state of collections
        for collection in collections_to_link:
            linked_objects = [obj.name for obj in collection.objects]
            print(f"[DEBUG] Final objects in collection '{collection.name}': {linked_objects}")

        # Report the completion
        self.report({'INFO'}, f"Assets joined as '{self.joined_name}' and lists updated.")

    def collect_objects_from_collection(self, collection):
        """Recursively collect all mesh objects from the collection and its subcollections."""
        objects = []
        def collect_from_collection(col):
            for obj in col.objects:
                if obj.type == 'MESH':
                    objects.append(obj)
            for subcol in col.children:
                collect_from_collection(subcol)
        collect_from_collection(collection)
        return objects

def update_export_status(context):
    """Update the export status in the UI list based on the selected import format."""
    assetify_settings = context.scene.assetify_bake_settings
    export_path = bpy.path.abspath(assetify_settings.export_fbx_path)
    import_format = assetify_settings.import_format.lower()

    valid_extensions = {
        'fbx': '.fbx',
        'obj': '.obj',
        'gltf': ['.glb', '.gltf'],
        'stl': '.stl',
    }
    selected_extension = valid_extensions.get(import_format)

    # Update baked assets
    for asset in assetify_settings.baked_assets:
        sanitized_name = sanitize_name(asset.name.replace("_gameasset", ""))
        if isinstance(selected_extension, list):
            asset.is_exported = any(
                check_if_file_exists(sanitized_name, export_path, ext)
                for ext in selected_extension
            )
        else:
            asset.is_exported = check_if_file_exists(sanitized_name, export_path, selected_extension)

    # Update baked collections
    for collection in assetify_settings.baked_collections:
        for asset in collection.assets:
            sanitized_name = sanitize_name(asset.name.replace("_gameasset", ""))
            if isinstance(selected_extension, list):
                asset.is_exported = any(
                    check_if_file_exists(sanitized_name, export_path, ext)
                    for ext in selected_extension
                )
            else:
                asset.is_exported = check_if_file_exists(sanitized_name, export_path, selected_extension)

    # Refresh the UI
    context.area.tag_redraw()

class OBJECT_OT_export_collection_as_fbx(bpy.types.Operator):
    """Export selected assets from the baked assets list as separate files in the chosen format with progress bar"""
    bl_idname = "assetify.export_selected_assets"
    bl_label = "Export Selected Assets"
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _export_index = 0
    _objects_to_export = []
    total_export_steps = 0
    progress_value = 0.0
    current_operation = ""
    current_sub_operation = ""

    draw_handler = None
    space_reference = None

    @classmethod
    def poll(cls, context):
        assetify_settings = context.scene.assetify_bake_settings
        export_path = bpy.path.abspath(assetify_settings.export_fbx_path)

        if not export_path or not os.path.isdir(export_path):
            return False

        if assetify_settings.asset_mode == 'ASSET':
            return any(asset.include_in_send for asset in assetify_settings.baked_assets)

        elif assetify_settings.asset_mode == 'COLLECTION':
            return any(collection.include_in_send for collection in assetify_settings.baked_collections)

        return False

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        wm = context.window_manager

        # Collect objects to export based on selected mode
        if assetify_settings.asset_mode == 'ASSET':
            self._objects_to_export = [
                bpy.data.objects.get(asset.name) for asset in assetify_settings.baked_assets
                if asset.include_in_send
            ]
        elif assetify_settings.asset_mode == 'COLLECTION':
            self._objects_to_export = []
            for baked_collection in assetify_settings.baked_collections:
                if baked_collection.include_in_send:
                    collection = bpy.data.collections.get(baked_collection.name)
                    if collection:
                        self._objects_to_export.extend(self.collect_objects_from_collection(collection))

        # Filter out invalid objects
        self._objects_to_export = [obj for obj in self._objects_to_export if obj]

        if not self._objects_to_export:
            self.report({'ERROR'}, "No valid assets to export.")
            return {'CANCELLED'}

        # Initialize progress tracking
        self.total_export_steps = len(self._objects_to_export)
        start_progress_bar(self, initial_message="Exporting Selected Assets")
        self.progress_value = 0.0
        self.current_operation = "Exporting Files..."

        # Start modal timer
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'TIMER':
            if self._export_index < len(self._objects_to_export):
                obj = self._objects_to_export[self._export_index]
                assetify_settings = context.scene.assetify_bake_settings
                export_path = bpy.path.abspath(assetify_settings.export_fbx_path)

                # Export the object
                self.current_sub_operation = f"Exporting {obj.name}..."
                self.export_object(obj, export_path, assetify_settings)

                # Update progress
                self._export_index += 1
                self.progress_value = self._export_index / self.total_export_steps
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

            else:
                # Export complete
                self.current_operation = "Export Complete."
                remove_progress_bar(self)
                context.window_manager.event_timer_remove(self._timer)
                self.finalize_export(context)
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def finalize_export(self, context):
        """Finalize the export, update statuses, and refresh UI"""
        assetify_settings = context.scene.assetify_bake_settings
        update_collection_statuses(assetify_settings)
        populate_baked_assets_from_scene(assetify_settings)
        populate_baked_collections_from_scene(assetify_settings)
        self.report({'INFO'}, "Exported selected assets/collections.")

    def export_object(self, obj, export_path, assetify_settings):
        """Export the object in the selected format."""
        export_format = assetify_settings.export_format
        sanitized_name = self.sanitize_name(obj.name.replace('_gameasset', ''))
        export_file_path = os.path.join(export_path, f"{sanitized_name}_{export_format}_still.{export_format.lower()}")

        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)

        if export_format == 'FBX':
            bpy.ops.export_scene.fbx(
                filepath=export_file_path,
                use_selection=True,
                apply_scale_options='FBX_SCALE_UNITS',
                apply_unit_scale=False,
                mesh_smooth_type='EDGE',
                use_tspace=True,
                bake_space_transform=False,
                object_types={'MESH'},
                path_mode='COPY',
                embed_textures=True,
                bake_anim=False,
                add_leaf_bones=False,
                axis_forward='-Z',
                axis_up='Y'
            )
        elif export_format == 'OBJ':
            bpy.ops.wm.obj_export(
                filepath=export_file_path,
                check_existing=True,
                forward_axis='NEGATIVE_Z',
                up_axis='Y',
                global_scale=1.0,
                apply_modifiers=True,
                export_selected_objects=True,
                export_uv=True,
                export_normals=True,
                export_colors=False,
                export_materials=True,
                path_mode='AUTO',
                export_triangulated_mesh=False,
                export_object_groups=False,
                export_material_groups=False,
                export_vertex_groups=False
            )
        elif export_format == 'GLTF':
            bpy.ops.export_scene.gltf(
                filepath=export_file_path,
                check_existing=True,
                export_format='GLB',  # GLB is a binary GLTF file for compactness
                export_texcoords=True,
                export_normals=True,
                export_tangents=False,
                export_materials='EXPORT',  # Export full material definitions
                export_draco_mesh_compression_enable=False,  # Disable mesh compression for simplicity
                export_cameras=False,
                use_selection=True,
                export_lights=False,
                export_yup=True,
                export_apply=True  # Apply modifiers
            )
        elif export_format == 'STL':
            bpy.ops.wm.stl_export(
                filepath=export_file_path,
                check_existing=True,
                forward_axis='Y',
                up_axis='Z',
                global_scale=1.0,
                apply_modifiers=True,
                export_selected_objects=True
            )

        print(f"[DEBUG] Exported {obj.name} as {export_format} to {export_file_path}")

        # Update export status
        for asset in assetify_settings.baked_assets:
            if asset.name == obj.name:
                asset.is_fbx_exported = export_format == 'FBX'  # Only update for FBX format
                break

    def collect_objects_from_collection(self, collection):
        """Recursively collect all mesh objects from the collection and its subcollections."""
        objects = []
        def collect_from_collection(col):
            for obj in col.objects:
                if obj.type == 'MESH':
                    objects.append(obj)
            for subcol in col.children:
                collect_from_collection(subcol)
        collect_from_collection(collection)
        return objects

    def sanitize_name(self, name):
        """Sanitize object name to create valid folder and file names."""
        return re.sub(r'[<>:"/\\|?*]', '_', name)

    def cancel(self, context):
        context.window_manager.event_timer_remove(self._timer)
        remove_progress_bar(self)
        self.report({'INFO'}, "Export canceled.")

class OBJECT_OT_export_collection_with_animations(bpy.types.Operator):
    """Export selected assets or collections with animations"""
    bl_idname = "assetify.export_selected_animations"
    bl_label = "Export Selected Animations"
    bl_options = {'REGISTER', 'UNDO'}

    _timer = None
    _export_index = 0
    _objects_to_export = []
    total_export_steps = 0
    progress_value = 0.0
    current_operation = ""
    current_sub_operation = ""

    draw_handler = None
    space_reference = None

    @classmethod
    def poll(cls, context):
        assetify_settings = context.scene.assetify_bake_settings
        export_path = bpy.path.abspath(assetify_settings.export_fbx_path)

        if not export_path or not os.path.isdir(export_path):
            return False

        if assetify_settings.asset_mode == 'ASSET':
            return any(asset.include_in_send for asset in assetify_settings.baked_assets)

        elif assetify_settings.asset_mode == 'COLLECTION':
            return any(collection.include_in_send for collection in assetify_settings.baked_collections)

        return False

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        wm = context.window_manager

        # Collect objects to export based on selected mode
        if assetify_settings.asset_mode == 'ASSET':
            self._objects_to_export = [
                bpy.data.objects.get(asset.name) for asset in assetify_settings.baked_assets
                if asset.include_in_send
            ]
        elif assetify_settings.asset_mode == 'COLLECTION':
            self._objects_to_export = []
            for baked_collection in assetify_settings.baked_collections:
                if baked_collection.include_in_send:
                    collection = bpy.data.collections.get(baked_collection.name)
                    if collection:
                        self._objects_to_export.extend(self.collect_objects_from_collection(collection))

        # Filter out invalid objects
        self._objects_to_export = [obj for obj in self._objects_to_export if obj]

        if not self._objects_to_export:
            self.report({'ERROR'}, "No valid assets with animations to export.")
            return {'CANCELLED'}

        # Initialize progress tracking
        self.total_export_steps = len(self._objects_to_export)
        start_progress_bar(self, initial_message="Exporting Selected Animations")
        self.progress_value = 0.0
        self.current_operation = "Exporting Animation Files..."

        # Start modal timer
        self._timer = wm.event_timer_add(0.1, window=context.window)
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'TIMER':
            if self._export_index < len(self._objects_to_export):
                obj = self._objects_to_export[self._export_index]
                assetify_settings = context.scene.assetify_bake_settings
                export_path = bpy.path.abspath(assetify_settings.export_fbx_path)

                # Export the object
                self.current_sub_operation = f"Exporting {obj.name}..."
                self.export_animation_object(obj, export_path, assetify_settings, context)

                # Update progress
                self._export_index += 1
                self.progress_value = self._export_index / self.total_export_steps
                bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

            else:
                # Export complete
                self.current_operation = "Export Complete."
                remove_progress_bar(self)
                context.window_manager.event_timer_remove(self._timer)
                self.finalize_export(context)
                return {'FINISHED'}

        return {'PASS_THROUGH'}

    def finalize_export(self, context):
        """Finalize the export, update statuses, and refresh UI"""
        assetify_settings = context.scene.assetify_bake_settings
        update_collection_statuses(assetify_settings)
        populate_baked_assets_from_scene(assetify_settings)
        populate_baked_collections_from_scene(assetify_settings)
        self.report({'INFO'}, "Exported animations for selected assets/collections.")

    def export_animation_object(self, obj, export_path, assetify_settings, context):
        """Export the object with animations in the selected format."""
        animation_export_format = assetify_settings.animation_export_format
        sanitized_name = self.sanitize_name(obj.name.replace('_gameasset', ''))
        export_file_path = os.path.join(export_path, f"{sanitized_name}_{animation_export_format}_ANIM.{animation_export_format.lower()}")

        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)

        if animation_export_format == 'FBX':
            bpy.ops.export_scene.fbx(
                filepath=export_file_path,
                use_selection=True,
                bake_anim=True,
                bake_anim_force_startend_keying=True,
                bake_anim_simplify_factor=0.0,
                object_types={'MESH', 'ARMATURE'},
                path_mode='COPY',
                embed_textures=True,
                axis_forward='-Z',
                axis_up='Y'
            )
            
        elif animation_export_format == 'OBJ':
            # Create a dedicated subfolder for OBJ animation frames
            obj_anim_folder = os.path.join(export_path, f"{sanitized_name}_OBJ_ANIM")

            if not os.path.exists(obj_anim_folder):
                os.makedirs(obj_anim_folder)
                print(f"[DEBUG] Created folder for OBJ animation export: {obj_anim_folder}")

            # Loop through each frame and export as OBJ
            for frame in range(bpy.context.scene.frame_start, bpy.context.scene.frame_end + 1):
                bpy.context.scene.frame_set(frame)

                # Export OBJ with proper naming (e.g., Plane_OBJ_ANIM0001.obj)
                frame_file_name = f"{sanitized_name}_OBJ_ANIM{str(frame).zfill(4)}.obj"
                frame_file_path = os.path.join(obj_anim_folder, frame_file_name)
            
                bpy.ops.wm.obj_export(
                    filepath=frame_file_path,
                    export_animation=True,  # OBJ itself does not support built-in animations
                    start_frame=bpy.context.scene.frame_start,  # Scene's start frame
                    end_frame=bpy.context.scene.frame_end,  # Scene's end frame
                    export_selected_objects=True,  # Export selected objects
                    apply_modifiers=True,  # Apply modifiers
                    export_eval_mode='DAG_EVAL_VIEWPORT',  # Use viewport visibility
                    export_uv=True,  # Export UVs
                    export_normals=True,  # Export normals
                    export_materials=True,  # Export materials
                    export_pbr_extensions=False,  # Disable PBR extensions
                    path_mode='COPY',  # Copy paths for any external files
                    forward_axis='NEGATIVE_Z',  # Forward axis configuration
                    up_axis='Y'  # Up axis configuration
                )
        
        elif animation_export_format == 'ALEMBIC':
            export_file_path = os.path.join(export_path, f"{sanitized_name}_ALEMBIC_ANIM.abc")
            bpy.ops.wm.alembic_export(
                filepath=export_file_path,
                selected=True,
                start=bpy.context.scene.frame_start,
                end=bpy.context.scene.frame_end
            )
        elif animation_export_format == 'GLTF':
            if obj.type == 'MESH':
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_all(action='SELECT')
                bpy.ops.mesh.quads_convert_to_tris()
                bpy.ops.object.mode_set(mode='OBJECT')
            
            bpy.ops.export_scene.gltf(
                filepath=export_file_path,
                export_format='GLB',
                use_selection=True,
                export_animations=True,
                export_yup=True,
                export_apply=True,
                export_normals=True,
                export_tangents=True,
                export_materials='NONE',
                export_morph=True,  # Include shape keys
                export_morph_normal=True,  # Export shape key normal deltas
                export_morph_tangent=True,  # Exclude shape key tangent deltas
                export_lights=False,  # Exclude lights
                export_all_vertex_colors=False
            )
            
        elif animation_export_format == 'MDD':
            
            ensure_mdd_extension_enabled()
            
            # Create the export path for MDD
            export_file_path = os.path.join(export_path, f"{sanitized_name}_MDD_ANIM.mdd")
            
            bpy.context.view_layer.objects.active = obj

            # Export MDD (Requires an OBJ sequence or baked animation)
            bpy.ops.export_shape.mdd(
                filepath=export_file_path,
                frame_start=bpy.context.scene.frame_start,
                frame_end=bpy.context.scene.frame_end,
                fps=bpy.context.scene.render.fps,
            )

        print(f"[DEBUG] Exported {obj.name} with animations as {animation_export_format} to {export_file_path}") 

    def collect_objects_from_collection(self, collection):
        """Recursively collect all objects (mesh and armature) from the collection and its subcollections."""
        objects = []
        def collect_from_collection(col):
            for obj in col.objects:
                if obj.type in {'MESH', 'ARMATURE', 'CURVE', 'FONT'}:
                    objects.append(obj)
            for subcol in col.children:
                collect_from_collection(subcol)
        collect_from_collection(collection)
        return objects

    def sanitize_name(self, name):
        """Sanitize object name to create valid folder and file names."""
        return re.sub(r'[<>:"/\\|?*]', '_', name)

    def cancel(self, context):
        context.window_manager.event_timer_remove(self._timer)
        remove_progress_bar(self)
        self.report({'INFO'}, "Animation export canceled.")

import bpy
import addon_utils

def ensure_mdd_extension_enabled():
    """
    Ensures that the 'NewTek MDD format' exporter is enabled in all Blender versions.
    - Uses 'io_mesh_mdd' for versions < 4.3
    - Uses 'bl_ext.blender_org.newtek_mdd_format' for Blender 4.3+
    """
    
    # Define the correct module/extension names
    legacy_addon_module = "io_mesh_mdd"  # For Blender 4.2 and below
    extension_id = "bl_ext.blender_org.newtek_mdd_format"  # For Blender 4.3+

    # Check Blender version
    if bpy.app.version >= (4, 3, 0):
        # Blender 4.3+ → Use the extension system
        is_enabled = addon_utils.check(extension_id)[1]

        if is_enabled:
            print(f"[DEBUG] 'NewTek MDD format' extension is already enabled in Blender 4.3+.")
        else:
            try:
                addon_utils.enable(extension_id, default_set=True, persistent=True)
                print(f"[DEBUG] Successfully enabled 'NewTek MDD format' extension for Blender 4.3+.")
            except Exception as e:
                print(f"[ERROR] Failed to enable 'NewTek MDD format' extension: {e}")

    else:
        # Blender 4.2 and earlier → Use the legacy add-on system
        is_enabled = addon_utils.check(legacy_addon_module)[1]

        if is_enabled:
            print(f"[DEBUG] 'NewTek MDD format' add-on is already enabled in Blender < 4.3.")
        else:
            try:
                addon_utils.enable(legacy_addon_module, default_set=True, persistent=True)
                print(f"[DEBUG] Successfully enabled 'NewTek MDD format' add-on for Blender < 4.3.")
            except Exception as e:
                print(f"[ERROR] Failed to enable 'NewTek MDD format' add-on: {e}")

class ASSETIFY_OT_show_unimportable_assets(bpy.types.Operator):
    """Show a list of selected root collections or assets with details of missing textures or format files"""
    bl_idname = "assetify.show_unimportable_assets"
    bl_label = "Show Missing Files for Import"

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        missing_files = []

        # Get paths
        bake_folder = bpy.path.abspath(assetify_settings.bake_folder)
        texture_folder = os.path.join(bake_folder, "textures")
        export_path = bpy.path.abspath(assetify_settings.export_fbx_path)
        import_format = assetify_settings.import_format.lower()

        print(f"[DEBUG] Bake Folder: {bake_folder}")
        print(f"[DEBUG] Texture Folder: {texture_folder}")
        print(f"[DEBUG] Export Path: {export_path}")
        print(f"[DEBUG] Selected Import Format: {import_format}")

        # Valid extensions for different formats
        valid_extensions = {
            'fbx': '.fbx',
            'obj': '.obj',
            'gltf': ['.glb', '.gltf'],
            'stl': '.stl',
        }
        selected_extension = valid_extensions.get(import_format)
        print(f"[DEBUG] Valid Extensions for Selected Format: {selected_extension}")

        # Check mode and validate files
        if assetify_settings.asset_mode == 'ASSET':
            assets_selected = [
                asset for asset in assetify_settings.baked_assets
                if asset.include_in_send
            ]
            print(f"[DEBUG] Selected Assets for Validation: {[asset.name for asset in assets_selected]}")

            for asset in assets_selected:
                missing_files_for_asset = self.check_missing_files_for_asset(
                    asset, texture_folder, export_path, selected_extension
                )
                if missing_files_for_asset:
                    missing_files.append(f"{asset.name}: {', '.join(missing_files_for_asset)}")

        elif assetify_settings.asset_mode == 'COLLECTION':
            print("[DEBUG] Validating collections...")
            for collection in assetify_settings.baked_collections:
                if collection.include_in_send and get_collection_level(collection.name) == 0:  # Root collection
                    collection_missing = []
                    for asset in collection.assets:
                        missing_files_for_asset = self.check_missing_files_for_asset(
                            asset, texture_folder, export_path, selected_extension
                        )
                        if missing_files_for_asset:
                            collection_missing.append(f"{asset.name}: {', '.join(missing_files_for_asset)}")
                    if collection_missing:
                        missing_files.append(f"Collection '{collection.name}' is missing:\n" +
                                             "\n".join(collection_missing))

        # Display report
        if missing_files:
            message = "\n\n".join(missing_files)
            print(f"[DEBUG] Missing Files Report: {message}")
            self.report({'INFO'}, message)
            self.show_message_box(message, "Import Info")
        else:
            print("[DEBUG] All selected items have necessary files.")
            self.report({'INFO'}, "All selected items have necessary files.")
            self.show_message_box("All selected items have necessary files.", "Missing Files for Import")

        return {'FINISHED'}

    def check_missing_files_for_asset(self, asset, texture_folder, export_path, selected_extension):
        """Helper function to check for missing texture and format-specific files for an asset."""
        missing_files_for_asset = []

        # Check for baked texture files
        texture_path = os.path.join(texture_folder, f"{asset.name}_BaseColor.png")
        if not os.path.exists(texture_path):
            print(f"[DEBUG] Texture missing for {asset.name}: {texture_path}")
            missing_files_for_asset.append("BaseColor texture")
        else:
            print(f"[DEBUG] Texture exists for {asset.name}: {texture_path}")

        # Check for format-specific file
        sanitized_name = sanitize_name(asset.name.replace("_gameasset", ""))
        print(f"[DEBUG] Checking files for {sanitized_name} in {export_path} with extensions: {selected_extension}")

        if isinstance(selected_extension, list):
            file_exists = any(
                check_if_file_exists(sanitized_name, export_path, ext)
                for ext in selected_extension
            )
        else:
            file_exists = check_if_file_exists(sanitized_name, export_path, selected_extension)

        if not file_exists:
            missing_files_for_asset.append(f"{selected_extension.upper()} file")
        else:
            print(f"[DEBUG] Format-specific file exists for {sanitized_name}.")

        return missing_files_for_asset

    def show_message_box(self, message="", title="Message", icon='INFO'):
        def draw(self, context):
            for line in message.split('\n'):
                self.layout.label(text=line)

        bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)

def check_if_file_exists(asset_name, export_path, extension):
    """Check if a file with the specified extension exists for the given asset."""
    sanitized_name = sanitize_name(asset_name)
    file_name = f"{sanitized_name}{extension}"
    file_path = os.path.join(export_path, file_name)

    exists = os.path.isfile(file_path)  # Use isfile for better specificity
    #print(f"[DEBUG] Checking file for '{asset_name}': {file_path} (Exists: {exists})")
    return exists

class ASSETIFY_OT_show_swap_info(bpy.types.Operator):
    """Show info about swapping original and game assets"""
    bl_idname = "assetify.show_swap_info"
    bl_label = "Swap Info"

    def execute(self, context):
        message = (
            "Swap the processed and original assets. "
            "For example, use this when you want to use the new assets "
            "in a previously set up scatter system."
        )
        self.show_message_box(message, "Swap Info")
        return {'FINISHED'}

    def show_message_box(self, message="", title="Message", icon='INFO'):
        def draw(self, context):
            for line in message.split('\n'):
                self.layout.label(text=line)
        bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)

class ASSETIFY_OT_show_bake_info(bpy.types.Operator):
    """Show info about selected assets/collections for baking"""
    bl_idname = "assetify.show_bake_info"
    bl_label = "Bake Info"

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        selected_items = []

        if assetify_settings.asset_mode == 'ASSET':
            selected_items = [
                asset.name for asset in assetify_settings.baked_assets
                if asset.include_in_send
            ]
        elif assetify_settings.asset_mode == 'COLLECTION':
            selected_items = [
                collection.name for collection in assetify_settings.baked_collections
                if collection.include_in_send and get_collection_level(collection.name) == 0  # Only root collections
            ]

        if selected_items:
            message = "The following collections are selected for baking:\n" + "\n".join(selected_items)
        else:
            message = "No assets or collections are selected for baking."

        self.show_message_box(message, "Bake Info")
        return {'FINISHED'}

    def show_message_box(self, message="", title="Message", icon='INFO'):
        def draw(self, context):
            for line in message.split('\n'):
                self.layout.label(text=line)
        bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)

class ASSETIFY_OT_show_set_game_assets_info(bpy.types.Operator):
    """Show info about setting game assets"""
    bl_idname = "assetify.show_set_game_assets_info"
    bl_label = "Set Game Assets Info"

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        if len(assetify_settings.asset_collections) > 0:
            message = (
                "Process your selected collections and set them up for Baking & Exporting."
            )
        else:
            message = (
                "No asset collections are set.\n"
                "Please add collections to the Asset Collections list before setting up game assets."
            )
        self.show_message_box(message, "Process Info")
        return {'FINISHED'}

    def show_message_box(self, message="", title="Message", icon='INFO'):
        def draw(self, context):
            for line in message.split('\n'):
                self.layout.label(text=line)
        bpy.context.window_manager.popup_menu(draw, title=title, icon=icon)
        
def count_top_level_collections(collections):
    """
    Count the number of top-level collections from a list of BakedCollectionItem objects.
    """
    return sum(1 for collection in collections if get_collection_level(collection.name) == 0)

class ASSETIFY_OT_switch_export_mode(bpy.types.Operator):
    """Switch between Still and Animation export modes"""
    bl_idname = "assetify.switch_export_mode"
    bl_label = "Switch Export Mode"

    mode: bpy.props.EnumProperty(
        items=[
            ('STILL', "Still", "Switch to Still export mode"),
            ('ANIMATION', "Animation", "Switch to Animation export mode")
        ]
    )

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        assetify_settings.export_mode = self.mode
        self.report({'INFO'}, f"Switched to {self.mode} export mode.")
        return {'FINISHED'}
    
class ASSETIFY_OT_switch_texturebake_mode(bpy.types.Operator):
    """Switch between Still and Animation texture bake modes"""
    bl_idname = "assetify.switch_texturebake_mode"
    bl_label = "Switch Texture Bake Mode"

    mode: bpy.props.EnumProperty(
        items=[
            ('STILL', "Still", "Switch to Still texture bake mode"),
            ('ANIMATION', "Animation", "Switch to Animation texture bake mode")
        ]
    )

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        assetify_settings.texturebake_mode = self.mode
        self.report({'INFO'}, f"Switched to {self.mode} texture bake mode.")
        return {'FINISHED'}
    
class ASSETIFY_OT_switch_bake_mode(bpy.types.Operator):
    """Switch between Still and Animation bake modes"""
    bl_idname = "assetify.switch_bake_mode"
    bl_label = "Switch Bake Mode"

    mode: bpy.props.StringProperty()

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        assetify_settings.bake_mode = self.mode
        self.report({'INFO'}, f"Bake Mode set to {self.mode}")
        return {'FINISHED'}

class ASSETIFY_OT_show_mossify_mode_info(bpy.types.Operator):
    """Show information about Mossify Mode"""
    bl_idname = "assetify.show_mossify_mode_info"
    bl_label = "Mossify Mode Info"

    def invoke(self, context, event):
        # Call the dialog popup
        return context.window_manager.invoke_popup(self)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Mossify Mode enables advanced customization options")
        layout.label(text="for moss effects, including geometry nodes and textures.")

    def execute(self, context):
        return {'FINISHED'}
    
class ASSETIFY_OT_show_apply_frame_attributes_info(bpy.types.Operator):
    """Show information about applying frame attributes"""
    bl_idname = "assetify.show_apply_frame_attributes_info"
    bl_label = "Apply Attributes Info"

    def invoke(self, context, event):
        # This calls the popup dialog
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        # Add the informational text here
        layout.label(text="Apply baked data to object attributes")
        layout.label(text="for shape-key processed geometry node animations.")
        layout.label(text="Ensures shader animations match the baked data.")

    def execute(self, context):
        # No additional actions; the popup is only informational
        return {'FINISHED'}

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
        assetify_settings = scene.assetify_bake_settings
        
        addon_updater_ops.check_for_update_background()
        
        # Draw social links
        self.draw_social_links(layout)
        
        # Ensure has_asset_collections and all_collections_assigned are defined before use
        has_asset_collections = len(assetify_settings.asset_collections) > 0
        all_collections_assigned = all(
            item.collection is not None for item in assetify_settings.asset_collections
        )

        # Process Settings Section
        box = layout.box()
        row = box.row()
        row.prop(
            assetify_settings,
            "show_bake_mode_menu",  # Controls the expansion of Process Section
            text="",
            icon="TRIA_DOWN" if assetify_settings.show_bake_mode_menu else "TRIA_RIGHT",
            emboss=False
        )
        row.label(text="Process Assets")

        if assetify_settings.show_bake_mode_menu:            
            # Still Mode button
            texbake_button = row.operator(
                "assetify.switch_bake_mode",
                text="Still",
                depress=assetify_settings.bake_mode == 'STILL'
            )
            texbake_button.mode = 'STILL'

            animbake_button = row.operator(
                "assetify.switch_bake_mode",
                text="Animation",
                depress=assetify_settings.bake_mode == 'ANIMATION'
            )
            animbake_button.mode = 'ANIMATION'

            # Asset Collections list with add/remove buttons side by side
            box.label(text="Asset Collections:")
            row = box.row()
            split = row.split(factor=0.8)
            col = split.column()

            # Calculate the number of rows based on the number of items in the list, with a min of 2 and max of 5
            asset_collections_count = len(assetify_settings.asset_collections)
            asset_collections_rows = min(max(asset_collections_count, 1), 100)

            col.template_list(
                "ASSETIFY_UL_asset_collections",
                "",
                assetify_settings,
                "asset_collections",
                assetify_settings,
                "active_asset_collection_index",
                rows=asset_collections_rows
            )

            col = split.column(align=True)
            col.operator("assetify.add_asset_collection", icon='ADD', text="")
            col.operator("assetify.remove_asset_collection", icon='REMOVE', text="")
            
            # Mossify Mode option with Info button
            row = box.row(align=True)
            row.operator(
                "assetify.show_mossify_mode_info",  # Create an operator for the info popup
                text="",
                icon='INFO',
                emboss=False
            )
            row.prop(
                assetify_settings,
                "use_mossify",
                text="Mossify Mode"
            )

            # Show the "Enable Custom Attributes" option only if Mossify is not enabled
            if not assetify_settings.use_mossify:
                row = box.row(align=True)
                row.operator("assetify.show_custom_attributes_info", text="", icon='INFO', emboss=False)  # Set emboss=False
                row.prop(assetify_settings, "enable_custom_attributes", text="Enable Custom Attributes")

            # Show the emitter input if Mossify is enabled or if custom attributes are enabled
            if assetify_settings.use_mossify or assetify_settings.enable_custom_attributes:
                box.prop_search(scene, "custom_object", bpy.data, "objects", text="Emitter", icon='OUTLINER_OB_EMPTY')

            # Show the custom attributes list and settings if "Enable Custom Attributes" is active
            if not assetify_settings.use_mossify and assetify_settings.enable_custom_attributes:
                box.label(text="Custom Attributes")
                row = box.row()
                # Calculate the number of rows based on the number of items in the list, with a min of 3 and max of 6
                custom_attributes_count = len(assetify_settings.custom_attributes)
                custom_attributes_rows = min(max(custom_attributes_count, 1), 100)

                row.template_list(
                    "ASSETIFY_UL_custom_attributes",
                    "",
                    assetify_settings,
                    "custom_attributes",
                    assetify_settings,
                    "active_custom_attribute_index",
                    rows=custom_attributes_rows
                )

                col = row.column(align=True)
                col.operator("assetify.add_custom_attribute", icon='ADD', text="")
                col.operator("assetify.remove_custom_attribute", icon='REMOVE', text="")

            # Disable Original Collections option
            row = box.row(align=True)
            row.operator(
                "assetify.show_disable_original_collections_info",
                text="",
                icon='INFO',
                emboss=False
            )
            row.prop(
                assetify_settings,
                "disable_original_collections",
                text="Disable Original Collections"
            )

            # Show options based on the selected bake mode
            if assetify_settings.bake_mode == 'ANIMATION':
                # Animation Mode settings
                if hasattr(scene, "assetify_animation_settings"):
                    settings = scene.assetify_animation_settings

                    # Animation Type dropdown
                    row = box.row(align=True)
                    split = row.split(factor=0.5, align=True)
                    split.label(text="Animation Type:")
                    split.prop(settings, "animation_type", text="")

                    # File Format dropdown
                    row = box.row(align=True)
                    split = row.split(factor=0.5, align=True)
                    split.label(text="Desired Format:")
                    split.prop(settings, "file_format", text="")

                    # Process Animation button
                    row = box.row(align=True)
                    animation_type = settings.animation_type
                    file_format = settings.file_format
                    button_label = f"Process Animation ({file_format})"

                    # Enable button only if asset collections exist and are assigned
                    row.enabled = has_asset_collections and all_collections_assigned
                    row.operator("object.convert_to_game_ready", text=button_label)
                else:
                    box.label(text="Animation Settings not found. Please reinitialize.")
            else:
                # Still Mode settings
                row = box.row(align=True)
                button_label = "Process Assets"

                # Enable button only if asset collections exist and are assigned
                row.enabled = has_asset_collections and all_collections_assigned
                row.operator("object.convert_to_game_ready", text=button_label)

                # Ensure everything remains functional and consistent
                process_assets_row = layout.row(align=True)
                process_button_col = process_assets_row.column()
                process_button_col.enabled = has_asset_collections and all_collections_assigned

        box = layout.box()
        row = box.row()

        # Collapsible triangle icon and label for the Asset List
        row.prop(
            assetify_settings,
            "show_asset_list_menu",
            text="",
            icon="TRIA_DOWN" if assetify_settings.show_asset_list_menu else "TRIA_RIGHT",
            emboss=False
        )
        row.label(text="Processed Asset List")

        # Add Asset/Collection switch buttons if the menu is expanded
        if assetify_settings.show_asset_list_menu:
            # Asset/Collection switch buttons (placed next to the label)
            row.operator(
                "assetify.switch_mode",
                text="Asset",
                depress=assetify_settings.asset_mode == 'ASSET'
            ).mode = 'ASSET'

            row.operator(
                "assetify.switch_mode",
                text="Collection",
                depress=assetify_settings.asset_mode == 'COLLECTION'
            ).mode = 'COLLECTION'

            if assetify_settings.asset_mode == 'ASSET':
                # Draw headers for Asset List
                header = box.row(align=True)
                split = header.split(factor=0.15)
                split.label(text="", icon='CHECKMARK')
                split = split.split(factor=0.4 / 0.9)
                split.label(text="Asset")
                remaining = split.split(factor=0.5)
                remaining.label(text="", icon='TEXTURE')
                remaining.label(text="", icon='FILE_TICK')

                # Draw asset list
                num_rows = max(min(len(assetify_settings.baked_assets), 5), 1)
                row = box.row()
                row.template_list(
                    "ASSETIFY_UL_baked_assets",
                    "",
                    assetify_settings,
                    "baked_assets",
                    assetify_settings,
                    "active_baked_asset_index",
                    rows=num_rows
                )

                box.operator("assetify.refresh_asset_collection_list", text="Refresh List", icon='FILE_REFRESH')

                # Buttons for delete, separate, and join
                delete_assets_row = box.row()
                delete_assets_row.enabled = any(asset.include_in_send for asset in assetify_settings.baked_assets)
                delete_assets_row.operator("assetify.delete_selected_assets", text="Delete Selected Assets", icon='TRASH')

                separate_assets_row = box.row()
                separate_assets_row.enabled = any(asset.include_in_send for asset in assetify_settings.baked_assets)
                separate_assets_row.operator("assetify.separate_by_material", text="Separate by Material", icon='OUTLINER_OB_MESH')

                join_assets_row = box.row()
                join_assets_row.enabled = any(asset.include_in_send for asset in assetify_settings.baked_assets)
                join_assets_row.operator("assetify.join_assets", text="Join Assets", icon='OBJECT_DATA')

            else:
                # Draw headers for Collection List
                header = box.row(align=True)
                split = header.split(factor=0.15)
                split.label(text="", icon='CHECKMARK')
                split = split.split(factor=0.4 / 0.9)
                split.label(text="Collection")
                remaining = split.split(factor=0.33)
                remaining.label(text="", icon='ARROW_LEFTRIGHT')
                remaining = remaining.split(factor=0.5)
                remaining.label(text="", icon='TEXTURE')
                remaining.label(text="", icon='FILE_TICK')

                # Draw collection list
                num_top_level_collections = count_top_level_collections(assetify_settings.baked_collections)
                num_collection_rows = max(min(num_top_level_collections, 5), 1)
                row = box.row()
                row.template_list(
                    "ASSETIFY_UL_collection_list",
                    "",
                    assetify_settings,
                    "baked_collections",
                    assetify_settings,
                    "active_baked_collection_index",
                    rows=num_collection_rows
                )

                box.operator("assetify.refresh_asset_collection_list", text="Refresh List", icon='FILE_REFRESH')

                # Enable buttons for collections
                collection_in_send = any(
                    collection.include_in_send for collection in assetify_settings.baked_collections
                )

                delete_collections_row = box.row()
                delete_collections_row.enabled = collection_in_send
                delete_collections_row.operator("assetify.delete_selected_collections", text="Delete Selected Collections", icon='TRASH')

                separate_collections_row = box.row()
                separate_collections_row.enabled = collection_in_send
                separate_collections_row.operator("assetify.separate_by_material", text="Separate by Material", icon='OUTLINER_OB_MESH')

                join_collections_row = box.row()
                join_collections_row.enabled = collection_in_send
                join_collections_row.operator("assetify.join_assets", text="Join Assets", icon='OBJECT_DATA')

                if assetify_settings.asset_mode == 'COLLECTION':
                    # Swap button with info icon
                    swap_row = box.row(align=True)
                    info_col = swap_row.column()
                    info_col.operator("assetify.show_swap_info", text="", icon='INFO', emboss=False)
                    swap_button_col = swap_row.column()
                    swap_button_col.enabled = collection_in_send
                    swap_button_col.operator("assetify.swap_collections", text="Swap Selected Original & Game Assets")

        # Bake Settings Section
        box = layout.box()
        row = box.row()
        row.prop(assetify_settings, "bake_menu_expanded", text="", icon="TRIA_DOWN" if assetify_settings.bake_menu_expanded else "TRIA_RIGHT", emboss=False)
        row.label(text="Bake Assets")
        
        if assetify_settings.bake_menu_expanded:
            
            # Button for Still Mode (Highlight if selected)
            texbake_button = row.operator(
                "assetify.switch_texturebake_mode",
                text="Still",
                depress=assetify_settings.texturebake_mode == 'STILL'
            )
            texbake_button.mode = 'STILL'

            # Button for Animation Mode (Highlight if selected)
            animbake_button = row.operator(
                "assetify.switch_texturebake_mode",
                text="Animation",
                depress=assetify_settings.texturebake_mode == 'ANIMATION'
            )
            animbake_button.mode = 'ANIMATION'
            
            # Show "Apply Attributes" button only in Animation mode
            if assetify_settings.texturebake_mode == 'ANIMATION':
                row = box.row(align=True)
                
                # Add an info button with emboss disabled
                row.operator(
                    "assetify.show_apply_frame_attributes_info",  # Info operator for explanation
                    text="",
                    icon='INFO',
                    emboss=False
                )
                
                # Add label for the button
                split = row.split(factor=0.45, align=True)
                split.label(text="Apply Attributes")
                
                # Add the "Apply Attributes" button with enabled logic
                apply_button_col = split.column()
                apply_button_col.enabled = (
                    (assetify_settings.asset_mode == 'ASSET' and any(
                        asset.include_in_send for asset in assetify_settings.baked_assets
                    )) or
                    (assetify_settings.asset_mode == 'COLLECTION' and any(
                        collection.include_in_send for collection in assetify_settings.baked_collections
                    ))
                )
                apply_button_col.operator("animation.add_frame_dependent_attributes", text="Apply Attributes")
                        
            row = box.row(align=True)
            split = row.split(factor=0.5, align=True)  # Adjust factor for alignment
            split.label(text="Skip UV Unwrap")
            split.prop(assetify_settings, "skip_uv_unwrap")
            
            row = box.row(align=True)
            split = row.split(factor=0.5, align=True)
            split.label(text="Target Platform")
            split.prop(assetify_settings, "platform_target", text="")
            
            row = box.row(align=True)
            split = row.split(factor=0.5, align=True)
            split.label(text="Render Device")
            split.prop(assetify_settings, "render_device", text="")
            
            row = box.row(align=True)
            split = row.split(factor=0.5, align=True)  # Adjust factor for alignment
            split.label(text="Use Tiling")
            split.prop(assetify_settings, "use_tiling", text="")  # Checkbox only
            
            if assetify_settings.use_tiling:  # Show Tile Size only if Use Tiling is enabled
                row = box.row(align=True)
                split = row.split(factor=0.5, align=True)
                split.label(text="Tile Size")
                split.prop(assetify_settings, "tile_size", text="")  # Numeric input on the right
                
            # Bake Folder (50/50 split)
            row = box.row(align=True)
            split = row.split(factor=0.5, align=True)
            split.label(text="Bake Folder")
            split.prop(assetify_settings, "bake_folder", text="")
            
            # Bake Resolution (50/50 split)
            row = box.row(align=True)
            split = row.split(factor=0.5, align=True)
            split.label(text="Bake Resolution")
            split.prop(assetify_settings, "bake_resolution", text="")
            
            # Bake Samples (two-column layout)
            row = box.row(align=True)
            split = row.split(factor=0.5, align=True)
            split.label(text="Bake Samples")
            split.prop(assetify_settings, "bake_samples", text="")  # Numeric input on the right

            # Bake button row with info icon
            bake_row = box.row(align=True)

            # Info button (always enabled)
            info_col = bake_row.column()
            info_col.operator("assetify.show_bake_info", text="", icon='INFO', emboss=False)
            
            bake_button_label = (
                "Bake Selected Assets (STILL)" if assetify_settings.texturebake_mode == 'STILL'
                else "Bake Selected Assets (ANIM)"
            )

            # Bake button (enabled based on selected items)
            bake_button_col = bake_row.column()
            bake_button_col.enabled = (
                (assetify_settings.asset_mode == 'ASSET' and any(
                    asset.include_in_send for asset in assetify_settings.baked_assets
                )) or
                (assetify_settings.asset_mode == 'COLLECTION' and any(
                    collection.include_in_send for collection in assetify_settings.baked_collections
                ))
            )
            bake_button_col.operator("object.bake_textures_modal", text=bake_button_label)

        # Export Settings Section
        box = layout.box()
        row = box.row()
        row.prop(assetify_settings, "export_menu_expanded", text="", icon="TRIA_DOWN" if assetify_settings.export_menu_expanded else "TRIA_RIGHT", emboss=False)
        row.label(text="Export/Import Assets")

        if assetify_settings.export_menu_expanded:
            
            # Button for Still Mode (Highlight if selected)
            still_button = row.operator(
                "assetify.switch_export_mode",
                text="Still",
                depress=assetify_settings.export_mode == 'STILL'
            )
            still_button.mode = 'STILL'

            # Button for Animation Mode (Highlight if selected)
            animation_button = row.operator(
                "assetify.switch_export_mode",
                text="Animation",
                depress=assetify_settings.export_mode == 'ANIMATION'
            )
            animation_button.mode = 'ANIMATION'
                
            # Export Section
            export_box = layout.box()  # Create a dedicated container for export elements

            # Display export_fbx_path, which is automatically updated
            row = export_box.row(align=True)
            split = row.split(factor=0.5, align=True)
            split.label(text="Export Path")
            split.prop(assetify_settings, "export_fbx_path", text="")

            # Export Format Dropdown Row
            dropdown_row = export_box.row(align=True)
            if assetify_settings.export_mode == 'STILL':
                dropdown_row.label(text="Still Export Format")
                dropdown_row.prop(assetify_settings, "export_format", text="")
            elif assetify_settings.export_mode == 'ANIMATION':
                dropdown_row.label(text="Anim Export Format")
                dropdown_row.prop(assetify_settings, "animation_export_format", text="")

            # Export Info Button and Export Button in the same row
            export_row = export_box.row(align=True)

            # Info button always enabled to check missing files
            info_col = export_row.column()
            info_col.operator("assetify.show_unbaked_assets", text="", icon='INFO', emboss=False)

            # Export button with dynamic operator and label
            export_button_col = export_row.column()

            if assetify_settings.export_mode == 'STILL':
                export_format = assetify_settings.export_format.upper()
                export_label = f"Export Selected Still ({export_format})"
                export_operator = "assetify.export_selected_assets"
            elif assetify_settings.export_mode == 'ANIMATION':
                export_format = assetify_settings.animation_export_format.upper()
                export_label = f"Export Selected Anim ({export_format})"
                export_operator = "assetify.export_selected_animations"

            export_button_col.enabled = self.check_export_button_enabled(assetify_settings)
            export_button_col.operator(export_operator, text=export_label)

            # Import Section
            import_box = layout.box()  # Create a dedicated container for import elements

            # Dynamically display the appropriate import format dropdown
            if assetify_settings.export_mode == 'STILL':
                dropdown_row = import_box.row(align=True)
                dropdown_row.label(text="Still Import Format")
                dropdown_row.prop(assetify_settings, "import_format", text="")
            elif assetify_settings.export_mode == 'ANIMATION':
                dropdown_row = import_box.row(align=True)
                dropdown_row.label(text="Anim Import Format")
                dropdown_row.prop(assetify_settings, "animation_import_format", text="")

            # Import Settings Row
            import_row = import_box.row(align=True)

            # Info button always enabled for checking missing files
            info_col = import_row.column()
            info_col.operator("assetify.show_unimportable_assets", text="", icon='INFO', emboss=False)

            # Import button conditional based on file existence check
            import_button_col = import_row.column()

            # Dynamically update the import button label
            if assetify_settings.export_mode == 'STILL':
                import_label = f"Import Selected Still ({assetify_settings.import_format.upper()})"
            elif assetify_settings.export_mode == 'ANIMATION':
                import_label = f"Import Selected Anim ({assetify_settings.animation_import_format.upper()})"

            import_button_col.operator("assetify.import_selected_fbx", text=import_label)
    
    def check_import_button_enabled(self, assetify_settings):
        """Returns True if the Import button should be enabled based on include_in_send and file existence."""
        export_fbx_path = bpy.path.abspath(assetify_settings.export_fbx_path)
        print(f"[DEBUG] Export FBX Path: {export_fbx_path}")

        # Determine suffix and format based on the export mode
        if assetify_settings.export_mode == 'STILL':
            suffix = "_still"
            selected_format = assetify_settings.import_format.upper()
        elif assetify_settings.export_mode == 'ANIMATION':
            suffix = "_ANIM"
            selected_format = assetify_settings.animation_import_format.upper()
        else:
            print("[DEBUG] Invalid export mode.")
            return False

        print(f"[DEBUG] Selected Format: {selected_format}, Suffix: {suffix}")

        if assetify_settings.asset_mode == 'ASSET':
            print("[DEBUG] Checking in Asset Mode...")
            for asset in assetify_settings.baked_assets:
                if asset.include_in_send:
                    # Construct the file name with the format and suffix
                    sanitized_name = asset.name.replace("_gameasset", "")
                    fbx_name = f"{sanitized_name}_{selected_format}{suffix}.{selected_format.lower()}"
                    fbx_path = os.path.join(export_fbx_path, fbx_name)
                    
                    print(f"[DEBUG] Asset Name: {asset.name}")
                    print(f"[DEBUG] Expected File Name: {fbx_name}")
                    print(f"[DEBUG] Expected File Path: {fbx_path}")
                    print(f"[DEBUG] File Exists: {os.path.exists(fbx_path)}")
                    
                    if os.path.exists(fbx_path):
                        print("[DEBUG] Valid file found for asset.")
                        return True

            print("[DEBUG] No valid files found in Asset Mode.")
            return False

        elif assetify_settings.asset_mode == 'COLLECTION':
            print("[DEBUG] Checking in Collection Mode...")
            for collection in assetify_settings.baked_collections:
                if collection.include_in_send:
                    print(f"[DEBUG] Checking collection: {collection.name}")
                    for asset in collection.assets:
                        # Construct the file name with the format and suffix
                        sanitized_name = asset.name.replace("_gameasset", "")
                        fbx_name = f"{sanitized_name}_{selected_format}{suffix}.{selected_format.lower()}"
                        fbx_path = os.path.join(export_fbx_path, fbx_name)
                        
                        # Debug output for each asset
                        print(f"[DEBUG] Asset Name: {asset.name} in Collection: {collection.name}")
                        print(f"[DEBUG] Expected File Name: {fbx_name}")
                        print(f"[DEBUG] Expected File Path: {fbx_path}")
                        print(f"[DEBUG] File Exists: {os.path.exists(fbx_path)}")

                        if os.path.exists(fbx_path):
                            print("[DEBUG] Valid file found for a collection asset.")
                            return True

            print("[DEBUG] No valid files found in Collection Mode.")
            return False

        return False

    def check_export_button_enabled(self, assetify_settings):
        """Returns True if the Export button should be enabled based on selected assets or collections."""
        if assetify_settings.asset_mode == 'ASSET':
            # Enable if any asset is selected for export
            return any(asset.include_in_send for asset in assetify_settings.baked_assets)
        else:
            # Enable if any collection is selected for export
            return any(collection.include_in_send for collection in assetify_settings.baked_collections)
        
    def get_unbaked_assets(self, assetify_settings):
        """Retrieve a list of unbaked assets."""
        unbaked_asset_names = []
        for asset in assetify_settings.baked_assets:
            if asset.include_in_send and not asset.is_baked:
                unbaked_asset_names.append(asset.name)
        return unbaked_asset_names

    def get_unexported_assets(self, assetify_settings):
        """Retrieve a list of unexported assets."""
        unexported_asset_names = []
        for asset in assetify_settings.baked_assets:
            if asset.include_in_send and not asset.is_fbx_exported:
                unexported_asset_names.append(asset.name)
        return unexported_asset_names

    def draw_social_links(self, layout):
        # Define the URLs and icon values for the social buttons
        socials = [
            ("https://www.youtube.com/@NinoDefoq", "youtube_icon"),
            ("https://www.instagram.com/defo.q", "instagram_icon"),
            ("https://x.com/DefoNino", "x_icon"),
            ("https://discord.gg/tgbuXp3eav", "discord_icon"),
            ("https://www.patreon.com/NinoDefoQ", "patreon_icon"),
            ("https://www.ninodefoq.com", "website_icon"),
            ("https://www.linkedin.com/in/ninobolink/", "linkedin_icon"),
            ("https://www.tiktok.com/@defo.q", "tiktok_icon")
        ]

        # Create a grid flow layout that adjusts based on the available width
        flow = layout.grid_flow(row_major=True, columns=len(socials), even_columns=True, even_rows=True, align=True)

        # Iterate over the list of socials and add each icon
        for url, icon_key in socials:
            if icon_key in custom_icons:
                icon_id = custom_icons[icon_key].icon_id
                # Add an operator for each social link
                flow.operator("wm.url_open", text="", icon_value=icon_id).url = url
            else:
                print(f"Warning: Icon '{icon_key}' not found in custom_icons.")

class ASSETIFY_OT_show_disable_original_collections_info(bpy.types.Operator):
    """Show information about the 'Disable Original Collections' option"""
    bl_idname = "assetify.show_disable_original_collections_info"
    bl_label = ""
    bl_description = "Disables & hides the Original Collections to prevent overlapping assets."
    bl_options = {'INTERNAL'}

    def execute(self, context):
        return {'FINISHED'}

    # Optional: If you want to display a popup when clicked
    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Disables & hides the Original Collections")
        layout.label(text="to prevent overlapping assets.")

class ASSETIFY_OT_toggle_mossify_mode(bpy.types.Operator):
    """Toggle between Mossify mode and standard mode"""
    bl_idname = "assetify.toggle_mossify_mode"
    bl_label = "Toggle Mossify Mode"

    def execute(self, context):
        scene = context.scene
        # Toggle the use_mossify property
        scene.assetify_bake_settings.use_mossify = not scene.assetify_bake_settings.use_mossify
        return {'FINISHED'}

class ASSETIFY_OT_add_custom_attribute(bpy.types.Operator):
    """Add a new custom attribute"""
    bl_idname = "assetify.add_custom_attribute"
    bl_label = "Add Custom Attribute"

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        new_item = assetify_settings.custom_attributes.add()
        new_item.name = "NewAttribute"
        new_item.type = 'FLOAT'
        new_item.domain = 'POINT'
        assetify_settings.active_custom_attribute_index = len(assetify_settings.custom_attributes) - 1
        self.report({'INFO'}, f"Added custom attribute '{new_item.name}'")
        return {'FINISHED'}

class ASSETIFY_OT_remove_custom_attribute(bpy.types.Operator):
    """Remove the selected custom attribute"""
    bl_idname = "assetify.remove_custom_attribute"
    bl_label = "Remove Custom Attribute"

    @classmethod
    def poll(cls, context):
        assetify_settings = context.scene.assetify_bake_settings
        return assetify_settings.custom_attributes and assetify_settings.active_custom_attribute_index >= 0

    def execute(self, context):
        assetify_settings = context.scene.assetify_bake_settings
        index = assetify_settings.active_custom_attribute_index
        removed_attribute = assetify_settings.custom_attributes[index].name
        assetify_settings.custom_attributes.remove(index)
        assetify_settings.active_custom_attribute_index = min(max(0, index - 1), len(assetify_settings.custom_attributes) - 1)
        self.report({'INFO'}, f"Removed custom attribute '{removed_attribute}'")
        return {'FINISHED'}

def load_post_handler(scene):
    """This handler runs after loading a new Blender file to populate and validate baked assets and update collection statuses."""
    if hasattr(scene, 'assetify_bake_settings') and scene.assetify_bake_settings:
        assetify_settings = scene.assetify_bake_settings
        
        # Populate baked assets and collections
        print("[Assetify] Populating baked assets and collections for the loaded scene.")
        update_collection_statuses(assetify_settings)
        populate_baked_assets_from_scene(assetify_settings)
        populate_baked_collections_from_scene(assetify_settings)
        
        # Validate baked assets for the scene
        print("[Assetify] Validating baked assets for the loaded scene.")
        validate_baked_assets(scene)
    else:
        print("[Assetify] Scene does not have 'assetify_bake_settings'. Skipping asset validation and population.")
    
    # Loop over all scenes to ensure all collection statuses are updated
    for scene in bpy.data.scenes:
        if hasattr(scene, 'assetify_bake_settings'):
            assetify_settings = scene.assetify_bake_settings
            print(f"[Assetify] Updating collection statuses for scene: {scene.name}")
            update_collection_statuses(assetify_settings)
        else:
            print(f"[Assetify] No 'assetify_bake_settings' found in scene: {scene.name}")

    return None  # Ensures the handler is not called repeatedly

def clear_baked_assets_on_startup(scene):
    """Clear the baked assets list on Blender startup."""
    if hasattr(scene, 'assetify_bake_settings') and scene.assetify_bake_settings:
        settings = scene.assetify_bake_settings
        settings.baked_assets.clear()
        print("Baked assets list cleared at startup.")
    else:
        print("Scene does not have 'assetify_bake_settings' yet.")
        
def initialize_skip_save_check():
    """Ensure skip_save_check is set to False for all scenes."""
    for scene in bpy.data.scenes:
        if not hasattr(scene, "assetify_bake_settings"):
            continue
        scene.assetify_bake_settings.skip_save_check = False
        print(f"[Assetify] Initialized skip_save_check to False for scene '{scene.name}'")
    return None  # Stop the timer
        
classes = (
    BakedAssetItem,
    AssetCollectionItem,  
    BakedCollectionItem,
    AssetifyPreferences,
    ASSETIFY_UL_baked_assets,
    ASSETIFY_OT_show_set_game_assets_info,
    ASSETIFY_UL_collection_list,
    ASSETIFY_OT_separate_by_material,
    ASSETIFY_OT_join_assets,
    ASSETIFY_OT_show_bake_info,
    ASSETIFY_OT_show_swap_info,
    ASSETIFY_OT_confirm_export_with_unbaked,
    ASSETIFY_OT_show_unimportable_assets,
    ASSETIFY_OT_refresh_asset_collection_list,
    ASSETIFY_OT_swap_collections,
    ASSETIFY_OT_debug_virtual_links,
    ASSETIFY_OT_delete_selected_assets,
    ASSETIFY_OT_delete_selected_collections,
    ASSETIFY_OT_switch_mode,
    ASSETIFY_OT_delete_bake_and_fbx_files,
    ASSETIFY_OT_import_selected_fbx,
    ASSETIFY_OT_show_unbaked_assets,
    ASSETIFY_OT_show_unexported_assets,
    ASSETIFY_OT_show_disable_original_collections_info,
    ASSETIFY_OT_save_and_continue,
    ASSETIFY_OT_cancel_operation,
    ASSETIFY_OT_proceed_without_saving,
    ASSETIFY_OT_save_as_mainfile,
    ASSETIFY_OT_switch_export_mode,
    ASSETIFY_OT_switch_bake_mode,
    ASSETIFY_OT_switch_texturebake_mode,
    ASSETIFY_OT_show_mossify_mode_info,
    ASSETIFY_OT_show_apply_frame_attributes_info,
    OBJECT_OT_export_collection_as_fbx,
    OBJECT_OT_export_collection_with_animations,
    CustomAttributeItem,          # Added        # Added
    AssetifyBakeSettings,         # Ensure these are after the above two
    ASSETIFY_OT_add_custom_attribute,
    ASSETIFY_OT_remove_custom_attribute,
    ASSETIFY_OT_toggle_mossify_mode,
    OBJECT_OT_ApplyCustomGeometryNodes,
    OBJECT_OT_bake_textures_modal,
    OBJECT_OT_convert_to_game_ready,
    ASSETIFY_PT_tools_panel,
    ASSETIFY_UL_custom_attributes,
    ASSETIFY_UL_asset_collections,
    ASSETIFY_OT_add_asset_collection,
    ASSETIFY_OT_remove_asset_collection,
    ASSETIFY_OT_show_custom_attributes_info,
)

def register():
    import importlib

    # Reload dependent modules
    importlib.reload(animation_processor)
    
    animation_processor.register()
    bpy.types.Scene.assetify_animation_settings = bpy.props.PointerProperty(type=animation_processor.AssetifyAnimationSettings)

    print(animation_processor.AssetifyAnimationSettings)

    # Register AssetifyAnimationSettings first
    try:
        bpy.utils.register_class(animation_processor.AssetifyAnimationSettings)
        print("[INFO] AssetifyAnimationSettings registered successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to register AssetifyAnimationSettings: {e}")

    # Add PointerProperty for assetify_animation_settings
    try:
        bpy.types.Scene.assetify_animation_settings = bpy.props.PointerProperty(
            type=animation_processor.AssetifyAnimationSettings
        )
        print("[INFO] assetify_animation_settings added to bpy.types.Scene.")
    except Exception as e:
        print(f"[ERROR] Failed to add assetify_animation_settings: {e}")

    # Register animation operators
    try:
        bpy.utils.register_class(animation_processor.ASSETIFY_OT_process_animation)
        bpy.utils.register_class(animation_processor.ANIMATION_OT_bake_geometry_assets)
        bpy.utils.register_class(animation_processor.ANIMATION_OT_apply_bake_to_keyframes)
        print("[INFO] Animation operators registered successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to register animation operators: {e}")

    # Debug checks
    print("[DEBUG] Registered Scene properties:", dir(bpy.types.Scene))
    print("[DEBUG] Registered Operators:", [op for op in dir(bpy.ops) if "assetify" in op])

    # Register all custom classes
    for cls in classes:
        try:
            bpy.utils.unregister_class(cls)  # Unregister if already registered
        except Exception as e:
            print(f"[INFO] Class {cls.__name__} was not previously registered: {e}")
        try:
            bpy.utils.register_class(cls)
            print(f"[INFO] Class {cls.__name__} registered successfully.")
        except Exception as e:
            print(f"[ERROR] Failed to register class {cls.__name__}: {e}")

    # Load custom icons
    load_custom_icons()

    # Add other properties
    try:
        if not hasattr(bpy.types.Scene, "assetify_bake_settings"):
            bpy.types.Scene.assetify_bake_settings = bpy.props.PointerProperty(type=AssetifyBakeSettings)
            print("[INFO] assetify_bake_settings added.")
        if not hasattr(bpy.types.Scene, "custom_object"):
            bpy.types.Scene.custom_object = bpy.props.PointerProperty(type=bpy.types.Object)
            print("[INFO] custom_object added.")
        if not hasattr(bpy.types.Scene, "custom_name"):
            bpy.types.Scene.custom_name = bpy.props.StringProperty(name="Custom Name", default="mosscolor")
            print("[INFO] custom_name added.")
        if not hasattr(bpy.types.Scene, "custom_value_name"):
            bpy.types.Scene.custom_value_name = bpy.props.StringProperty(name="Custom Value Name", default="mosscolorvalue")
            print("[INFO] custom_value_name added.")
        if not hasattr(bpy.types.WindowManager, "unbaked_assets"):
            bpy.types.WindowManager.unbaked_assets = bpy.props.StringProperty(name="Unbaked Assets")
            print("[INFO] unbaked_assets added.")
        if not hasattr(bpy.types.WindowManager, "confirm_export"):
            bpy.types.WindowManager.confirm_export = bpy.props.BoolProperty(name="Confirm Export", default=False)
            print("[INFO] confirm_export added.")
    except Exception as e:
        print(f"[ERROR] Failed to add properties: {e}")

    # Initialize skip save check
    bpy.app.timers.register(initialize_skip_save_check)

    # Register other dependent modules
    try:
        anim_geonode.register()
        print("[INFO] anim_geonode registered successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to register anim_geonode: {e}")

    # Register addon updater operations
    try:
        addon_updater_ops.register(bl_info)
        print("[INFO] Addon updater operations registered successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to register addon updater operations: {e}")

    # Delay handler registration
    bpy.app.timers.register(register_handlers, first_interval=1.0)

def register_handlers():
    bpy.app.handlers.load_post.append(load_post_handler)
    bpy.app.handlers.load_post.append(clear_baked_assets_on_startup)

def unregister():
    # Unregister dependent modules
    try:
        anim_geonode.unregister()
    except Exception as e:
        print(f"[INFO] anim_geonode was not previously registered: {e}")

    try:
        animation_processor.unregister()
    except Exception as e:
        print(f"[INFO] animation_processor was not previously registered: {e}")

    print("[INFO] Unregistering Assetify addon...")

    # Remove PointerProperty from Scene
    if hasattr(bpy.types.Scene, "assetify_animation_settings"):
        del bpy.types.Scene.assetify_animation_settings
        print("[INFO] assetify_animation_settings removed from bpy.types.Scene.")

    # Unregister classes
    try:
        bpy.utils.unregister_class(animation_processor.ASSETIFY_OT_process_animation)
        bpy.utils.unregister_class(animation_processor.ANIMATION_OT_bake_geometry_assets)
        bpy.utils.unregister_class(animation_processor.ANIMATION_OT_apply_bake_to_keyframes)
        print("[INFO] Animation operators unregistered successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to unregister animation operators: {e}")

    try:
        bpy.utils.unregister_class(animation_processor.AssetifyAnimationSettings)
        print("[INFO] AssetifyAnimationSettings unregistered successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to unregister AssetifyAnimationSettings: {e}")

    # Unregister addon updater operations
    addon_updater_ops.unregister()

    # Unload custom icons
    unload_custom_icons()

    # Unregister all custom classes in reverse order
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            print(f"[INFO] Class {cls.__name__} was not registered: {e}")

    # Remove other properties
    if hasattr(bpy.types.Scene, "assetify_bake_settings"):
        del bpy.types.Scene.assetify_bake_settings
    if hasattr(bpy.types.Scene, "custom_object"):
        del bpy.types.Scene.custom_object
    if hasattr(bpy.types.Scene, "custom_name"):
        del bpy.types.Scene.custom_name
    if hasattr(bpy.types.Scene, "custom_value_name"):
        del bpy.types.Scene.custom_value_name
    if hasattr(bpy.types.WindowManager, "unbaked_assets"):
        del bpy.types.WindowManager.unbaked_assets
    if hasattr(bpy.types.WindowManager, "confirm_export"):
        del bpy.types.WindowManager.confirm_export

    # Remove handlers safely
    if load_post_handler in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(load_post_handler)
    if clear_baked_assets_on_startup in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(clear_baked_assets_on_startup)

if __name__ == "__main__":
    register()
