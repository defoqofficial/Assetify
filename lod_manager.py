import bpy
import re

class AssetifyLODSettings(bpy.types.PropertyGroup):
    generate_lod1: bpy.props.BoolProperty(
        name="LOD 1", 
        description="Generate LOD 1",
        default=True
    )
    lod1_ratio: bpy.props.FloatProperty(
        name="Ratio", 
        description="Decimation ratio for LOD 1 (0.0 - 1.0)",
        default=0.5, min=0.01, max=1.0, precision=2
    )
    
    generate_lod2: bpy.props.BoolProperty(
        name="LOD 2", 
        description="Generate LOD 2", 
        default=True
    )
    lod2_ratio: bpy.props.FloatProperty(
        name="Ratio", 
        description="Decimation ratio for LOD 2 (0.0 - 1.0)",
        default=0.25, min=0.01, max=1.0, precision=2
    )
    
    generate_lod3: bpy.props.BoolProperty(
        name="LOD 3", 
        description="Generate LOD 3", 
        default=False
    )
    lod3_ratio: bpy.props.FloatProperty(
        name="Ratio", 
        description="Decimation ratio for LOD 3 (0.0 - 1.0)",
        default=0.125, min=0.01, max=1.0, precision=2
    )

    auto_rename_original: bpy.props.BoolProperty(
        name="Rename Original to LOD0",
        description="Renames the source object to end with _LOD0",
        default=True
    )

class AssetifyGenerateLODs(bpy.types.Operator):
    bl_idname = "assetify.generate_lods"
    bl_label = "Generate LODs"
    bl_options = {'REGISTER', 'UNDO'}

    # Helper to grab meshes from collections
    def collect_objects_from_collection(self, collection):
        objects = [obj for obj in collection.objects if obj.type == 'MESH']
        for child in collection.children:
            objects.extend(self.collect_objects_from_collection(child))
        return objects

    def execute(self, context):
        scene = context.scene
        
        # Check settings
        if not hasattr(scene, "assetify_lod_settings") or not hasattr(scene, "assetify_bake_settings"):
            self.report({'ERROR'}, "Settings not found.")
            return {'CANCELLED'}

        lod_settings = scene.assetify_lod_settings
        bake_settings = scene.assetify_bake_settings

        objects_to_process = []

        # --- 1. COLLECT OBJECTS ---
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
                        objects_to_process.extend(self.collect_objects_from_collection(collection))

        if not objects_to_process:
            self.report({'ERROR'}, "No objects selected in Process Assets list.")
            return {'CANCELLED'}

        # --- 2. PROCESS EACH OBJECT ---
        generated_count = 0
        
        # Deselect all initially
        bpy.ops.object.select_all(action='DESELECT')

        for obj in objects_to_process:
            if obj.type != 'MESH':
                continue

            # --- NEW FIX: Skip Collision Objects ---
            # Do not generate LODs for objects that are already collision meshes
            if obj.name.startswith(("UCX_", "UBX_", "USP_", "UCP_")):
                continue
            
            # Select current object
            obj.select_set(True)
            context.view_layer.objects.active = obj

            # Process LOD 1
            if lod_settings.generate_lod1:
                self.create_lod(context, obj, "_LOD1", lod_settings.lod1_ratio)
                generated_count += 1

            # Process LOD 2
            if lod_settings.generate_lod2:
                self.create_lod(context, obj, "_LOD2", lod_settings.lod2_ratio)
                generated_count += 1

            # Process LOD 3
            if lod_settings.generate_lod3:
                self.create_lod(context, obj, "_LOD3", lod_settings.lod3_ratio)
                generated_count += 1
                
            # Rename original if requested
            if lod_settings.auto_rename_original and not obj.name.endswith("_LOD0"):
                obj.name = f"{obj.name}_LOD0"
            
            # Deselect for next iteration
            obj.select_set(False)

        self.report({'INFO'}, f"Generated LODs for {len(objects_to_process)} objects.")
        return {'FINISHED'}

    def create_lod(self, context, original_obj, suffix, ratio):
        """Creates a single LOD object with a Decimate modifier."""
        
        # Determine the name
        # If original already has _LOD0, strip it to get base name
        base_name = original_obj.name.replace("_LOD0", "")
        lod_name = base_name + suffix

        # Remove existing LOD if it exists to avoid duplicates
        if lod_name in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[lod_name], do_unlink=True)

        # 1. Duplicate the object
        new_obj = original_obj.copy()
        new_obj.data = original_obj.data.copy()
        new_obj.name = lod_name
        
        # 2. Link to the SAME collection as the original object
        if original_obj.users_collection:
            original_obj.users_collection[0].objects.link(new_obj)
        else:
            # Fallback to current collection if original is somehow orphaned
            context.collection.objects.link(new_obj)
        
        # 3. Add Decimate Modifier
        mod = new_obj.modifiers.new(name="Decimate_LOD", type='DECIMATE')
        mod.ratio = ratio
        
        # 4. Apply the modifier
        context.view_layer.objects.active = new_obj
        bpy.ops.object.modifier_apply(modifier=mod.name)
        
        return new_obj

class AssetifyClearLODs(bpy.types.Operator):
    """Delete all LOD objects associated with the selected object"""
    bl_idname = "assetify.clear_lods"
    bl_label = "Delete Existing LODs"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        
        # 1. Clean the name to find the true "Base"
        temp_name = obj.name
        
        # Remove collision prefixes if present (so we can find the visual mesh LODs even if collision is selected)
        collision_prefixes = ["UCX_", "UBX_", "USP_", "UCP_"]
        for prefix in collision_prefixes:
            if temp_name.startswith(prefix):
                temp_name = temp_name[len(prefix):] # Strip prefix
                break
        
        # Remove LOD suffix if present
        match = re.search(r'^(.*)(_LOD\d+)$', temp_name)
        
        if match:
            base_name = match.group(1) 
        else:
            base_name = temp_name

        deleted_count = 0
        renamed = False
        
        # 2. Iterate through scene objects to find matches
        for scene_obj in list(context.scene.objects):
            # Optimization: Check starts with first
            if not scene_obj.name.startswith(base_name):
                continue
            
            # Strict regex check: Must equal base_name + _LOD + digits
            lod_match = re.fullmatch(re.escape(base_name) + r'_LOD(\d+)', scene_obj.name)
            
            if lod_match:
                lod_level = int(lod_match.group(1))
                
                if lod_level > 0:
                    # DELETE: LOD levels greater than 0
                    bpy.data.objects.remove(scene_obj, do_unlink=True)
                    deleted_count += 1
                
                elif lod_level == 0:
                    # RENAME: LOD level 0 becomes the original object
                    scene_obj.name = base_name
                    renamed = True

        self.report({'INFO'}, f"Deleted {deleted_count} LODs. Renamed original: {renamed}")
        return {'FINISHED'}

# --- REGISTRATION ---
def register():
    bpy.utils.register_class(AssetifyLODSettings)
    bpy.utils.register_class(AssetifyGenerateLODs)
    bpy.utils.register_class(AssetifyClearLODs)
    bpy.types.Scene.assetify_lod_settings = bpy.props.PointerProperty(type=AssetifyLODSettings)

def unregister():
    if hasattr(bpy.types.Scene, "assetify_lod_settings"):
        del bpy.types.Scene.assetify_lod_settings
    bpy.utils.unregister_class(AssetifyClearLODs)
    bpy.utils.unregister_class(AssetifyGenerateLODs)
    bpy.utils.unregister_class(AssetifyLODSettings)