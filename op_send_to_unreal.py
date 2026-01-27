import bpy
import os
import sys
import time
import ctypes
import re
import subprocess
from bpy.props import IntProperty
from . import remote_execution 

# =================================================================================
#  HELPER: FOCUS UNREAL WINDOW
# =================================================================================
def focus_unreal_window():
    """Switches focus to Unreal Editor (Windows & Mac)"""
    try:
        if sys.platform == "win32":
            EnumWindows = ctypes.windll.user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
            GetWindowText = ctypes.windll.user32.GetWindowTextW
            GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
            IsWindowVisible = ctypes.windll.user32.IsWindowVisible
            IsIconic = ctypes.windll.user32.IsIconic 

            titles = []
            def foreach_window(hwnd, lParam):
                if IsWindowVisible(hwnd):
                    length = GetWindowTextLength(hwnd)
                    buff = ctypes.create_unicode_buffer(length + 1)
                    GetWindowText(hwnd, buff, length + 1)
                    titles.append((hwnd, buff.value))
                return True

            EnumWindows(EnumWindowsProc(foreach_window), 0)

            unreal_hwnd = None
            for hwnd, title in titles:
                if "Unreal Editor" in title and "Blender" not in title:
                    unreal_hwnd = hwnd
                    break
            
            if unreal_hwnd:
                if IsIconic(unreal_hwnd):
                    ctypes.windll.user32.ShowWindow(unreal_hwnd, 9) 
                else:
                    ctypes.windll.user32.ShowWindow(unreal_hwnd, 5) 
                ctypes.windll.user32.SetForegroundWindow(unreal_hwnd)
        
        elif sys.platform == "darwin":
            script = 'tell application "Unreal Editor" to activate'
            subprocess.run(["osascript", "-e", script], check=False)
            
    except Exception as e:
        print(f"Could not switch focus: {e}")

# =================================================================================
#  HELPER: FIND LODS ONLY
# =================================================================================
def get_lod_group(base_obj, clean_name):
    """Finds only _LODx objects related to the base object"""
    related = [base_obj]
    for candidate in bpy.context.scene.objects:
        if candidate == base_obj: continue
        
        c_name = candidate.name
        if "." in c_name: c_name = c_name.split(".")[0]
        
        if f"{clean_name}_LOD" in c_name:
            related.append(candidate)
    return related

# =================================================================================
#  NEW: SUCCESS DIALOG OPERATOR
# =================================================================================
class ASSETIFY_OT_TransferFinished(bpy.types.Operator):
    """Show success message and ask to switch window"""
    bl_idname = "assetify.transfer_finished"
    bl_label = "Transfer Complete"
    bl_options = {'REGISTER', 'INTERNAL'}
    sent_count: IntProperty(name="Count", default=0)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=350)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        row = col.row()
        row.alignment = 'CENTER'
        row.label(text=f"Successfully sent {self.sent_count} asset(s) to Unreal Engine!", icon='CHECKMARK')
        col.separator()
        row = col.row()
        row.alignment = 'CENTER'
        row.label(text="Check Unreal Content Browser now.")

    def execute(self, context):
        focus_unreal_window()
        return {'FINISHED'}

# =================================================================================
#  THE UNREAL ENGINE PAYLOAD SCRIPT
# =================================================================================
UNREAL_PAYLOAD = """
import unreal
import os

def configure_texture_settings(texture_asset):
    tex_name = texture_asset.get_name().lower()
    needs_save = False
    if any(k in tex_name for k in ["_orm", "_roughness", "_metallic", "_ao", "_occlusion"]):
        texture_asset.set_editor_property("srgb", False)
        texture_asset.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
        needs_save = True
    elif any(k in tex_name for k in ["_normal", "_nrm"]):
        texture_asset.set_editor_property("srgb", False)
        texture_asset.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
        needs_save = True
    elif any(k in tex_name for k in ["_basecolor", "_albedo", "_color", "_diffuse"]):
        texture_asset.set_editor_property("srgb", True)
        texture_asset.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_DEFAULT)
        needs_save = True
    if needs_save:
        unreal.EditorAssetLibrary.save_asset(texture_asset.get_path_name())

def create_and_assign_material(asset_name, game_path, imported_textures, static_mesh):
    try:
        mat_name = f"M_{{asset_name}}"
        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
        mat_factory = unreal.MaterialFactoryNew()
        mat_path = f"{{game_path}}/{{mat_name}}"
        if unreal.EditorAssetLibrary.does_asset_exist(mat_path):
            material = unreal.load_asset(mat_path)
        else:
            material = asset_tools.create_asset(mat_name, game_path, unreal.Material, mat_factory)
        mel = unreal.MaterialEditingLibrary
        mel.delete_all_material_expressions(material)
        node_x = -600
        node_y = -200
        has_orm = any("_orm" in tex.get_name().lower() for tex in imported_textures)
        for tex_obj in imported_textures:
            tex_name = tex_obj.get_name().lower()
            ts_node = mel.create_material_expression(material, unreal.MaterialExpressionTextureSample)
            ts_node.texture = tex_obj
            ts_node.material_expression_editor_x = node_x
            ts_node.material_expression_editor_y = node_y
            node_y += 300 
            try:
                if "_orm" in tex_name:
                    ts_node.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_MASKS
                    mel.connect_material_property(ts_node, "R", unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)
                    mel.connect_material_property(ts_node, "G", unreal.MaterialProperty.MP_ROUGHNESS)
                    mel.connect_material_property(ts_node, "B", unreal.MaterialProperty.MP_METALLIC)
                elif "_normal" in tex_name or "_nrm" in tex_name:
                    ts_node.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL
                    mel.connect_material_property(ts_node, "RGB", unreal.MaterialProperty.MP_NORMAL)
                elif any(k in tex_name for k in ["_basecolor", "_albedo", "_color"]):
                     ts_node.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_COLOR
                     mel.connect_material_property(ts_node, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
                elif ("_metallic" in tex_name or "_metal" in tex_name) and not has_orm:
                    ts_node.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_MASKS
                    mel.connect_material_property(ts_node, "R", unreal.MaterialProperty.MP_METALLIC)
                elif ("_roughness" in tex_name or "_rough" in tex_name) and not has_orm:
                    ts_node.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_MASKS
                    mel.connect_material_property(ts_node, "R", unreal.MaterialProperty.MP_ROUGHNESS)
                elif ("_ao" in tex_name or "_ambient" in tex_name or "_occlusion" in tex_name) and not has_orm:
                    ts_node.sampler_type = unreal.MaterialSamplerType.SAMPLERTYPE_MASKS
                    mel.connect_material_property(ts_node, "R", unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)
            except: pass
        mel.recompile_material(material)
        unreal.EditorAssetLibrary.save_asset(material.get_path_name())
        static_mesh.set_material(0, material)
        unreal.EditorAssetLibrary.save_asset(static_mesh.get_path_name())
        return material
    except: return None

def import_process(file_path, texture_paths, game_path, actor_name):
    tasks = []
    
    mesh_task = unreal.AssetImportTask()
    mesh_task.filename = file_path
    mesh_task.destination_path = game_path
    mesh_task.destination_name = actor_name
    mesh_task.replace_existing = True
    mesh_task.automated = True
    mesh_task.save = True 
    
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ['.fbx', '.obj']:
        mesh_options = unreal.FbxImportUI()
        
        # --- 1. UI SETTINGS ---
        mesh_options.set_editor_property('bImportMesh', True)
        mesh_options.set_editor_property('bImportTextures', False)
        mesh_options.set_editor_property('bImportMaterials', False)
        
        # Force Static Mesh Type
        mesh_options.set_editor_property('original_import_type', unreal.FBXImportType.FBXIT_STATIC_MESH)
        mesh_options.set_editor_property('mesh_type_to_import', unreal.FBXImportType.FBXIT_STATIC_MESH)
        
        # --- 2. LOD SETTINGS (TRY EVERYTHING!) ---
        
        # Attempt 1: Standard UI Property
        try:
            mesh_options.set_editor_property('bImportMeshLODs', True)
        except: pass 
        
        # Attempt 2: Python Attribute
        try:
            mesh_options.import_mesh_lods = True
        except: pass

        # Attempt 3: User Suggestion - Try setting it on Data Object
        # Sometimes properties are exposed here in Python even if they belong to UI in C++
        try:
            mesh_options.static_mesh_import_data.set_editor_property('bImportMeshLODs', True)
        except: pass
        
        try:
            # Try python attribute style on data object
            mesh_options.static_mesh_import_data.import_mesh_lods = True
        except: pass

        # --- 3. DATA SETTINGS ---
        # Combine Meshes = FALSE (For LodGroup workflow)
        mesh_options.static_mesh_import_data.set_editor_property('bCombineMeshes', False)
        
        mesh_options.static_mesh_import_data.set_editor_property('bRemoveDegenerates', False)
        mesh_options.static_mesh_import_data.set_editor_property('bGenerateLightmapUVs', False)
        mesh_options.static_mesh_import_data.set_editor_property('bAutoGenerateCollision', True)
        
        if ext == '.obj':
            mesh_options.override_full_name = True
            
        mesh_task.options = mesh_options
    
    tasks.append(mesh_task)

    for tex_path in texture_paths: 
        tex_task = unreal.AssetImportTask()
        tex_task.filename = tex_path
        tex_task.destination_path = game_path
        tex_task.replace_existing = True
        tex_task.automated = True
        tex_task.save = True
        tasks.append(tex_task)

    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks(tasks)

    # --- POST PROCESS ---
    mesh_asset_path = f"{{game_path}}/{{actor_name}}"
    static_mesh = unreal.load_asset(mesh_asset_path)
    
    imported_textures = []
    for tex_path in texture_paths:
        base_name = os.path.splitext(os.path.basename(tex_path))[0]
        tex_asset_path = f"{{game_path}}/{{base_name}}"
        tex_obj = unreal.load_asset(tex_asset_path)
        if tex_obj:
            if 'configure_texture_settings' in globals():
                configure_texture_settings(tex_obj)
            imported_textures.append(tex_obj)

    if static_mesh:
        if 'create_and_assign_material' in globals():
            create_and_assign_material(actor_name, game_path, imported_textures, static_mesh)

    static_mesh = unreal.load_asset(mesh_asset_path)
    if static_mesh:
        try:
            location = unreal.Vector(0.0, 0.0, 0.0)
            rotation = unreal.Rotator(0.0, 0.0, 0.0)
            actor = unreal.EditorLevelLibrary.spawn_actor_from_object(static_mesh, location, rotation)
            if actor:
                actor.set_actor_label(actor_name)
                unreal.EditorLevelLibrary.select_nothing()
                unreal.EditorLevelLibrary.set_selected_level_actors([actor])
                print(f"Successfully spawned {{actor_name}}") 
        except Exception as e:
            unreal.log_error(f"Failed to spawn actor: {{e}}")

import_process(r"{file_path}", {texture_list_string}, r"{game_path}", "{obj_name}")
"""

# =================================================================================

class ASSETIFY_OT_SendToUnreal(bpy.types.Operator):
    """Send to Unreal (LODs + Collision + Popup)"""
    bl_idname = "assetify.send_to_unreal"
    bl_label = "Send to Unreal"
    bl_description = "Sends assets to Unreal. (Saves FBX next to Blend file)"

    def execute(self, context):
        wm = context.window_manager
        wm.progress_begin(0, 100)
        context.window.cursor_set('WAIT')
        
        assetify_settings = context.scene.assetify_bake_settings
        export_format = assetify_settings.export_format
        
        objects_to_send = []
        if assetify_settings.asset_mode == 'ASSET':
            for asset in assetify_settings.baked_assets:
                if asset.include_in_send:
                    obj = bpy.data.objects.get(asset.name)
                    if obj: objects_to_send.append(obj)
        elif assetify_settings.asset_mode == 'COLLECTION':
            for collection_item in assetify_settings.baked_collections:
                if collection_item.include_in_send:
                    col = bpy.data.collections.get(collection_item.name)
                    if col: objects_to_send.extend(self.get_mesh_objects_recursive(col))
        
        if not objects_to_send and context.selected_objects:
             objects_to_send = [o for o in context.selected_objects if o.type == 'MESH']

        if not objects_to_send:
            self.report({'WARNING'}, "No assets selected to send.")
            context.window.cursor_set('DEFAULT')
            wm.progress_end()
            return {'CANCELLED'}

        remote_exec = remote_execution.RemoteExecution()
        remote_exec.start()
        
        remote_node = None
        for _ in range(10): 
            time.sleep(0.1)
            if remote_exec.remote_nodes:
                remote_node = remote_exec.remote_nodes[0]
                break
        
        if not remote_node:
            self.report({'ERROR'}, "Unreal Engine not found.")
            remote_exec.stop()
            context.window.cursor_set('DEFAULT')
            wm.progress_end()
            return {'CANCELLED'}

        remote_exec.open_command_connection(remote_node.get("node_id"))
        
        if bpy.data.is_saved:
            temp_dir = os.path.dirname(bpy.data.filepath)
        else:
            temp_dir = bpy.app.tempdir
            self.report({'INFO'}, "Blend not saved. Using Temp folder.")
            
        sent_count = 0
        original_active = context.view_layer.objects.active
        original_selected = context.selected_objects

        try:
            asset_groups = {}
            for obj in objects_to_send:
                clean_name = obj.name
                if "." in clean_name: clean_name = clean_name.split(".")[0]
                clean_name = re.split(r'_LOD\d+', clean_name)[0]
                
                if clean_name not in asset_groups: asset_groups[clean_name] = []
                asset_groups[clean_name].append(obj)

            total_assets = len(asset_groups)
            wm.progress_begin(0, total_assets)
            
            for i, (base_name, members) in enumerate(asset_groups.items()):
                wm.progress_update(i)
                
                root_name = base_name 
                root_empty = bpy.data.objects.new(root_name, None)
                bpy.context.collection.objects.link(root_empty)
                
                main_mesh = next((m for m in members if "_LOD0" in m.name), members[0])
                root_empty.location = main_mesh.location
                root_empty.rotation_euler = main_mesh.rotation_euler
                root_empty.scale = main_mesh.scale
                root_empty.empty_display_type = 'PLAIN_AXES'
                
                root_empty["fbx_type"] = "LodGroup"
                root_empty.id_properties_ui("fbx_type").update(default="LodGroup")
                
                saved_state = {} 
                bpy.ops.object.select_all(action='DESELECT')
                
                for member in members:
                    saved_state[member] = {
                        "parent": member.parent,
                        "matrix": member.matrix_world.copy(),
                        "name": member.name
                    }
                    member.parent = root_empty
                    member.matrix_world = saved_state[member]["matrix"]
                    
                    if "." in member.name:
                        member.name = member.name.split(".")[0]
                        
                    member.select_set(True)
                
                root_empty.select_set(True)
                context.view_layer.objects.active = root_empty
                context.view_layer.update()
                
                full_path = ""
                if export_format == 'OBJ':
                    filename = f"{base_name}.obj"
                    full_path = os.path.join(temp_dir, filename)
                    bpy.ops.wm.obj_export(filepath=full_path, export_selected_objects=True)
                
                elif export_format == 'GLTF':
                    filename = f"{base_name}.glb"
                    full_path = os.path.join(temp_dir, filename)
                    bpy.ops.export_scene.gltf(filepath=full_path, use_selection=True, export_format='GLB')
                
                elif export_format == 'STL':
                    filename = f"{base_name}.stl"
                    full_path = os.path.join(temp_dir, filename)
                    bpy.ops.wm.stl_export(filepath=full_path, export_selected_objects=True)
                
                else: 
                    filename = f"{base_name}.fbx"
                    full_path = os.path.join(temp_dir, filename)
                    
                    bpy.ops.export_scene.fbx(
                        filepath=full_path, 
                        use_selection=True, 
                        axis_forward='-Z', axis_up='Y', 
                        apply_scale_options='FBX_SCALE_UNITS', 
                        bake_anim=False, 
                        object_types={'MESH', 'EMPTY'}, 
                        use_custom_props=True 
                    )

                for member in members:
                    member_data = saved_state[member]
                    member.parent = member_data["parent"]
                    member.matrix_world = member_data["matrix"]
                    if member.name != member_data["name"]:
                        member.name = member_data["name"]
                        
                bpy.data.objects.remove(root_empty)

                texture_files = [] 
                for slot in obj.material_slots:
                    if slot.material and slot.material.use_nodes:
                        for node in slot.material.node_tree.nodes:
                            if node.type == 'TEX_IMAGE' and node.image:
                                try:
                                    abspath = bpy.path.abspath(node.image.filepath)
                                    if os.path.exists(abspath):
                                        texture_files.append(abspath.replace("\\", "/"))
                                except: pass
                texture_files = list(set(texture_files))
                
                dynamic_game_path = f"/Game/Assetify_Imports/{base_name}"
                clean_file_path = full_path.replace("\\", "/")
                
                script_to_run = UNREAL_PAYLOAD.format(
                    file_path=clean_file_path, 
                    texture_list_string=str(texture_files),
                    game_path=dynamic_game_path, 
                    obj_name=base_name
                )

                remote_exec.run_command(script_to_run, exec_mode='ExecuteFile')
                sent_count += 1
                time.sleep(0.05) 

        except Exception as e:
            self.report({'ERROR'}, f"Error: {str(e)}")
        finally:
            if original_active: context.view_layer.objects.active = original_active
            for obj in original_selected:
                try: obj.select_set(True)
                except: pass
            
            remote_exec.stop()
            wm.progress_end()
            context.window.cursor_set('DEFAULT')
            
            if sent_count > 0:
                bpy.ops.assetify.transfer_finished('INVOKE_DEFAULT', sent_count=sent_count)

        self.report({'INFO'}, f"Sent {sent_count} Assets to Unreal!")
        return {'FINISHED'}

    def get_mesh_objects_recursive(self, collection):
        meshes = []
        for obj in collection.objects:
            if obj.type == 'MESH': meshes.append(obj)
        for child in collection.children:
            meshes.extend(self.get_mesh_objects_recursive(child))
        return meshes