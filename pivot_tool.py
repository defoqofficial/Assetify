import bpy
from mathutils import Vector

class AssetifySetPivotBottom(bpy.types.Operator):
    """Sets the pivot point to the bottom-center of selected assets"""
    bl_idname = "assetify.set_pivot_bottom"
    bl_label = "Set Pivot to Bottom"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        
        if not hasattr(scene, "assetify_bake_settings"):
            self.report({'ERROR'}, "Assetify settings not found.")
            return {'CANCELLED'}
            
        bake_settings = scene.assetify_bake_settings
        
        # --- 1. COLLECT OBJECTS (Standard Assetify Logic) ---
        objects_to_process = []
        if bake_settings.asset_mode == 'ASSET':
            objects_to_process = [
                bpy.data.objects.get(asset.name)
                for asset in bake_settings.baked_assets
                if asset.include_in_send and bpy.data.objects.get(asset.name) is not None
            ]
        elif bake_settings.asset_mode == 'COLLECTION':
            for baked_collection in bake_settings.baked_collections:
                if baked_collection.include_in_send:
                    collection = bpy.data.collections.get(baked_collection.name)
                    if collection:
                        objects_to_process.extend([obj for obj in collection.all_objects if obj.type == 'MESH'])

        if not objects_to_process:
            self.report({'ERROR'}, "No objects selected in Process Assets list.")
            return {'CANCELLED'}

        # --- 2. PROCESS PIVOTS ---
        count = 0
        
        # Save current cursor location to restore later
        saved_cursor_loc = context.scene.cursor.location.copy()
        
        # We need to be in Object Mode to manipulate origins
        if context.active_object and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
            
        # Deselect all first
        bpy.ops.object.select_all(action='DESELECT')

        for obj in objects_to_process:
            # Calculate the bounding box in world space
            # The bounding box is a list of 8 coordinates (corners)
            bbox_corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
            
            # Find Min Z (Bottom) and Average X/Y (Center)
            min_z = min([v.z for v in bbox_corners])
            center_x = sum([v.x for v in bbox_corners]) / 8
            center_y = sum([v.y for v in bbox_corners]) / 8
            
            # Set the 3D Cursor to this calculated point
            context.scene.cursor.location = Vector((center_x, center_y, min_z))
            
            # Set the object as active/selected for the operator
            context.view_layer.objects.active = obj
            obj.select_set(True)
            
            # Move Origin to Cursor
            bpy.ops.object.origin_set(type='ORIGIN_CURSOR', center='MEDIAN')
            
            # Cleanup selection for next loop
            obj.select_set(False)
            count += 1

        # Restore cursor
        context.scene.cursor.location = saved_cursor_loc

        self.report({'INFO'}, f"Updated pivots for {count} assets.")
        return {'FINISHED'}

# --- REGISTRATION ---
def register():
    bpy.utils.register_class(AssetifySetPivotBottom)

def unregister():
    bpy.utils.unregister_class(AssetifySetPivotBottom)