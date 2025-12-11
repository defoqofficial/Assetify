import bpy
import bmesh
import re

class AssetifyGenerateCollision(bpy.types.Operator):
    """Generates UCX collision: Convex Hull -> Merge by Distance -> Push Out"""
    bl_idname = "assetify.generate_collision"
    bl_label = "Generate UCX Collision"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        
        if not hasattr(scene, "assetify_bake_settings"):
            self.report({'ERROR'}, "Assetify Bake Settings not found.")
            return {'CANCELLED'}
            
        bake_settings = scene.assetify_bake_settings
        
        # --- 1. COLLECT OBJECTS ---
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

        # --- 2. GENERATE COLLISION ---
        generated_count = 0
        bpy.ops.object.select_all(action='DESELECT')

        for obj in objects_to_process:
            # Skip existing collision meshes
            if obj.name.startswith(("UCX_", "UBX_", "USP_", "UCP_")):
                continue

            # Skip Lower LODs
            if "_LOD" in obj.name and not obj.name.endswith("_LOD0"):
                continue

            # A. Duplicate & Setup
            col_obj = obj.copy()
            col_obj.data = obj.data.copy()
            col_obj.name = f"UCX_{obj.name}"
            
            # Clean name: Strip _LOD0 suffix
            for suffix in ["_LOD0", "_LOD1", "_LOD2", "_LOD3"]:
                col_obj.name = col_obj.name.replace(suffix, "")
            
            # --- LINK TO SAME COLLECTION ---
            if obj.users_collection:
                obj.users_collection[0].objects.link(col_obj)
            else:
                context.collection.objects.link(col_obj)
            
            col_obj.data.materials.clear()
            
            context.view_layer.objects.active = col_obj
            col_obj.select_set(True)

            # Calculate dimensions
            dims = col_obj.dimensions
            max_dim = max(dims.x, dims.y, dims.z)
            if max_dim <= 0.001: max_dim = 1.0

            # --- B. CORE OPERATIONS ---
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')

            # 1. Convex Hull
            bpy.ops.mesh.convex_hull(join_triangles=True, make_holes=False) 
            
            # Cleanup internal geometry
            bpy.ops.mesh.select_all(action='INVERT')
            bpy.ops.mesh.delete(type='VERT')
            
            # Select everything again
            bpy.ops.mesh.select_all(action='SELECT')

            # 2. Merge by Distance
            merge_dist = max_dim / 50.0 
            bpy.ops.mesh.remove_doubles(threshold=merge_dist)

            # 3. Push Out
            push_offset = max_dim * 0.02 
            bpy.ops.transform.shrink_fatten(value=push_offset)

            bpy.ops.object.mode_set(mode='OBJECT')

            # --- C. OPTIMIZATION ---
            mod_planar = col_obj.modifiers.new(name="PlanarDecimate", type='DECIMATE')
            mod_planar.decimate_type = 'DISSOLVE'
            mod_planar.angle_limit = 0.087 
            bpy.ops.object.modifier_apply(modifier=mod_planar.name)

            # --- D. VISUAL CLEANUP ---
            col_obj.display_type = 'WIRE'
            col_obj.show_in_front = True
            col_obj.show_wire = True
            
            generated_count += 1

        self.report({'INFO'}, f"Generated collision for {generated_count} objects.")
        return {'FINISHED'}

class AssetifyClearCollision(bpy.types.Operator):
    """Delete collision objects (UCX/UBX/USP/UCP) for the selected asset"""
    bl_idname = "assetify.clear_collision"
    bl_label = "Delete Collision"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        
        # 1. Identify Base Name
        temp_name = obj.name
        
        # If user selected the collision object itself, strip the prefix
        collision_prefixes = ["UCX_", "UBX_", "USP_", "UCP_"]
        for prefix in collision_prefixes:
            if temp_name.startswith(prefix):
                temp_name = temp_name[len(prefix):]
                break
        
        # Strip LOD suffix to find the true base name
        base_name = re.sub(r'_LOD\d+$', '', temp_name)
        
        deleted_count = 0
        
        # 2. Find and delete matches
        for scene_obj in list(context.scene.objects):
            # Check exact match for PREFIX + BaseName
            # This prevents deleting "UCX_ChairArm" when BaseName is "Chair"
            is_match = False
            for prefix in collision_prefixes:
                if scene_obj.name == f"{prefix}{base_name}":
                    is_match = True
                    break
            
            if is_match:
                bpy.data.objects.remove(scene_obj, do_unlink=True)
                deleted_count += 1

        self.report({'INFO'}, f"Deleted {deleted_count} collision objects for '{base_name}'.")
        return {'FINISHED'}

# --- REGISTRATION ---
classes = (
    AssetifyGenerateCollision,
    AssetifyClearCollision,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in classes:
        bpy.utils.unregister_class(cls)