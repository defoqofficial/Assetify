import bpy
import bmesh
import re
import numpy as np

class AssetifyCollisionSettings(bpy.types.PropertyGroup):
    collision_mode: bpy.props.EnumProperty(
        name="Collision Mode",
        description="Choose collision generation strategy",
        items=[
            ('AUTO', "Auto (Smart)", "Automatically detect loose parts or concavity and generate optimal hulls"),
            ('COMPOUND_SLICED', "Compound (Doorway / Arch)", "Decompose concave/hollow meshes into multiple convex hulls"),
            ('COMPOUND_PARTS', "Compound (Loose Parts)", "Separate convex hull for each disconnected mesh part"),
            ('SIMPLE', "Simple (Single Hull)", "Single convex hull for the entire object"),
        ],
        default='AUTO'
    )
    max_hulls: bpy.props.IntProperty(
        name="Max Hulls",
        description="Maximum number of convex collision hulls to generate for compound meshes",
        default=4,
        min=2,
        max=16
    )
    push_offset: bpy.props.FloatProperty(
        name="Thickness",
        description="Collision shell thickness push-out percentage (relative to size)",
        default=0.01,
        min=0.0,
        max=0.1,
        precision=3
    )

def get_loose_parts_coords(obj):
    """
    Extracts disconnected face island coordinate clusters from the mesh.
    Returns list of numpy coordinate arrays for each part.
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    visited = set()
    parts_coords = []
    
    for face in bm.faces:
        if face in visited:
            continue
        part_verts = set()
        stack = [face]
        visited.add(face)
        while stack:
            f = stack.pop()
            for v in f.verts:
                part_verts.add(v)
            for edge in f.edges:
                for linked_face in edge.link_faces:
                    if linked_face not in visited:
                        visited.add(linked_face)
                        stack.append(linked_face)
        if len(part_verts) >= 4:
            parts_coords.append(np.array([v.co for v in part_verts], dtype=np.float32))
            
    bm.free()
    return parts_coords

def kmeans_cluster_coords(coords, k=4, max_iter=25):
    """
    Fast K-Means clustering on 3D vertex coordinates in NumPy.
    Returns list of coordinate arrays for each cluster.
    """
    if len(coords) < k * 4:
        return [coords]
        
    rng = np.random.default_rng(42)
    # Initialize centroids spread out
    centroids = [coords[rng.choice(len(coords))]]
    for _ in range(1, k):
        dists = np.min([np.sum((coords - c)**2, axis=1) for c in centroids], axis=0)
        probs = dists / np.sum(dists) if np.sum(dists) > 0 else None
        centroids.append(coords[rng.choice(len(coords), p=probs)])
    centroids = np.array(centroids)
    
    labels = np.zeros(len(coords), dtype=np.int32)
    for _ in range(max_iter):
        dists = np.linalg.norm(coords[:, None, :] - centroids[None, :, :], axis=2)
        new_labels = np.argmin(dists, axis=1)
        if np.array_equal(labels, new_labels):
            break
        labels = new_labels
        for i in range(k):
            mask = (labels == i)
            if np.any(mask):
                centroids[i] = coords[mask].mean(axis=0)
                
    clusters = [coords[labels == i] for i in range(k) if np.sum(labels == i) >= 4]
    return clusters if clusters else [coords]

def is_mesh_concave(obj):
    """
    Heuristic check to determine if a mesh has significant concavity (e.g. arch/doorway/hollow).
    Compares convex hull volume to actual mesh volume or bounding box vertex distribution.
    """
    if len(obj.data.vertices) < 16:
        return False
    
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    try:
        orig_vol = abs(bm.calc_volume())
    except:
        orig_vol = 0.0
        
    # Calculate convex hull volume
    bmesh.ops.convex_hull(bm, input=bm.verts)
    try:
        hull_vol = abs(bm.calc_volume())
    except:
        hull_vol = 0.0
    bm.free()
    
    if orig_vol > 0 and hull_vol > 0:
        ratio = hull_vol / orig_vol
        # If convex hull is > 30% larger than mesh, it has significant concavity
        return ratio > 1.30
        
    return False

def create_hull_object(coords, name, collection, max_dim, push_offset=0.01, source_obj=None):
    """Creates a single water-tight, optimized convex hull object with wireframe preview."""
    bm = bmesh.new()
    for pt in coords:
        bm.verts.new(pt)
    bm.verts.ensure_lookup_table()
    
    # 1. Convex Hull
    bmesh.ops.convex_hull(bm, input=bm.verts)
    
    # Remove interior/unused vertices left by convex_hull op
    interior = [v for v in bm.verts if not v.link_faces]
    for v in interior:
        bm.verts.remove(v)
        
    # 2. Merge by distance
    merge_dist = max_dim / 50.0
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=merge_dist)
    
    # 3. Push Out (Offset)
    if push_offset > 0:
        offset_val = max_dim * push_offset
        for v in bm.verts:
            if v.normal.length > 0:
                v.co += v.normal * offset_val
                
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    
    col_obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(col_obj)
    if source_obj is not None:
        col_obj.matrix_world = source_obj.matrix_world.copy()
    
    # 4. Planar Decimate modifier for low-poly clean hull
    mod_planar = col_obj.modifiers.new(name="PlanarDecimate", type='DECIMATE')
    mod_planar.decimate_type = 'DISSOLVE'
    mod_planar.angle_limit = 0.087
    bpy.context.view_layer.objects.active = col_obj
    bpy.ops.object.modifier_apply(modifier=mod_planar.name)
    
    # 5. Wireframe visualization
    col_obj.display_type = 'WIRE'
    col_obj.show_in_front = True
    col_obj.show_wire = True
    
    return col_obj

class AssetifyGenerateCollision(bpy.types.Operator):
    """Generates simple or compound UCX collision for assets (supports doorways, arches, and multi-part props)"""
    bl_idname = "assetify.generate_collision"
    bl_label = "Generate UCX Collision"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        
        if not hasattr(scene, "assetify_bake_settings"):
            self.report({'ERROR'}, "Assetify Bake Settings not found.")
            return {'CANCELLED'}
            
        bake_settings = scene.assetify_bake_settings
        col_settings = getattr(scene, "assetify_collision_settings", None)
        mode = getattr(col_settings, 'collision_mode', 'AUTO') if col_settings else 'AUTO'
        max_hulls = getattr(col_settings, 'max_hulls', 4) if col_settings else 4
        push_offset = getattr(col_settings, 'push_offset', 0.01) if col_settings else 0.01
        
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

        # Fallback to selected objects if no baked assets are selected
        if not objects_to_process and context.selected_objects:
            objects_to_process = [o for o in context.selected_objects if o.type == 'MESH']

        if not objects_to_process:
            self.report({'ERROR'}, "No objects selected in Process Assets list or viewport.")
            return {'CANCELLED'}

        # --- 2. GENERATE COLLISION ---
        total_hulls = 0
        bpy.ops.object.select_all(action='DESELECT')

        for obj in objects_to_process:
            # Skip existing collision meshes
            if obj.name.startswith(("UCX_", "UBX_", "USP_", "UCP_")):
                continue

            # Skip Lower LODs
            if "_LOD" in obj.name and not obj.name.endswith("_LOD0"):
                continue

            # Clean name: Strip _LOD0 suffix
            clean_base = obj.name
            for suffix in ["_LOD0", "_LOD1", "_LOD2", "_LOD3"]:
                clean_base = clean_base.replace(suffix, "")

            # Target collection
            target_collection = obj.users_collection[0] if obj.users_collection else context.collection

            # Calculate dimensions
            dims = obj.dimensions
            max_dim = max(dims.x, dims.y, dims.z)
            if max_dim <= 0.001:
                max_dim = 1.0

            all_coords = np.array([v.co for v in obj.data.vertices], dtype=np.float32)
            if len(all_coords) < 4:
                continue

            # Determine clusters based on mode
            clusters = []
            
            if mode == 'SIMPLE':
                clusters = [all_coords]
                
            elif mode == 'COMPOUND_PARTS':
                loose = get_loose_parts_coords(obj)
                clusters = loose if len(loose) > 1 else [all_coords]
                if len(clusters) > max_hulls:
                    clusters = clusters[:max_hulls]
                    
            elif mode == 'COMPOUND_SLICED':
                clusters = kmeans_cluster_coords(all_coords, k=max_hulls)
                
            else: # AUTO
                loose = get_loose_parts_coords(obj)
                if len(loose) > 1:
                    clusters = loose[:max_hulls]
                elif is_mesh_concave(obj):
                    clusters = kmeans_cluster_coords(all_coords, k=max_hulls)
                else:
                    clusters = [all_coords]

            # Build hull objects
            if len(clusters) == 1:
                hull_name = f"UCX_{clean_base}"
                create_hull_object(clusters[0], hull_name, target_collection, max_dim, push_offset, source_obj=obj)
                total_hulls += 1
            else:
                for idx, cluster in enumerate(clusters):
                    hull_name = f"UCX_{clean_base}_{idx+1:02d}"
                    create_hull_object(cluster, hull_name, target_collection, max_dim, push_offset, source_obj=obj)
                    total_hulls += 1

        self.report({'INFO'}, f"Generated {total_hulls} collision hull(s) across {len(objects_to_process)} object(s).")
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
        
        # Strip collision prefixes if user selected a collider
        collision_prefixes = ["UCX_", "UBX_", "USP_", "UCP_"]
        for prefix in collision_prefixes:
            if temp_name.startswith(prefix):
                temp_name = temp_name[len(prefix):]
                break
        
        # Strip number suffix (e.g. _01, _02)
        temp_name = re.sub(r'_\d+$', '', temp_name)
        # Strip LOD suffix
        base_name = re.sub(r'_LOD\d+$', '', temp_name)
        
        deleted_count = 0
        
        # 2. Find and delete matches (handles both single and compound hulls)
        for scene_obj in list(context.scene.objects):
            for prefix in collision_prefixes:
                if scene_obj.name == f"{prefix}{base_name}" or scene_obj.name.startswith(f"{prefix}{base_name}_"):
                    bpy.data.objects.remove(scene_obj, do_unlink=True)
                    deleted_count += 1
                    break

        self.report({'INFO'}, f"Deleted {deleted_count} collision objects for '{base_name}'.")
        return {'FINISHED'}

# --- REGISTRATION ---
classes = (
    AssetifyCollisionSettings,
    AssetifyGenerateCollision,
    AssetifyClearCollision,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.assetify_collision_settings = bpy.props.PointerProperty(type=AssetifyCollisionSettings)

def unregister():
    if hasattr(bpy.types.Scene, "assetify_collision_settings"):
        del bpy.types.Scene.assetify_collision_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)