import bpy
from .anim_geonode import process_geonode_animation
from .anim_cloth import process_cloth_animation

def get_available_formats(self, context):
    formats = {
        'RIGGED': [('FBX', "FBX (.fbx)", ""), ('GLTF', "glTF (.gltf)", "")],
        'PHYSICS': [('CLOTH', "Cloth Shape Keys Animation", ""), ('ALEMBIC', "Alembic (.abc)", ""), ('MDD', "MDD (.mdd)", ""), ('OBJ', "Wavefront (.obj)", "")],
        'GEOMETRY_NODES': [('GLTF', "Shape Keys Animation (.gltf)", "Bake Geometry Nodes to shape keys"), ('ALEMBIC', "Geometry Nodes Animation (.abc)", "Bake Geometry Nodes to per-frame geometry")],
        'SHAPE_KEYS': [('FBX', "FBX (.fbx)", ""), ('GLTF', "glTF (.gltf)", ""), ('MDD', "MDD (.mdd)", "")],
        'KEYFRAMES': [('FBX', "FBX (.fbx)", ""), ('GLTF', "glTF (.gltf)", ""), ('ALEMBIC', "Alembic (.abc)", "")],
        'PARTICLES': [('ALEMBIC', "Alembic (.abc)", ""), ('MDD', "MDD (.mdd)", "")]
    }
    animation_type = getattr(self, "animation_type", 'RIGGED')  # Default to 'RIGGED'
    return formats.get(animation_type, [('NONE', "None Available", "")])  # Default fallback

class AssetifyAnimationSettings(bpy.types.PropertyGroup):
    animation_type: bpy.props.EnumProperty(
        name="Animation Type",
        description="Choose the type of animation to process",
        items=[
            ('RIGGED', "Rigged Animations", "Process rigged (armature-based) animations"),
            ('PHYSICS', "Physics Animations", "Process physics-based animations"),
            ('GEOMETRY_NODES', "Geometry Node Animations", "Process geometry node-driven animations"),
            ('SHAPE_KEYS', "Shape Key Animations", "Process shape key-based animations"),
            ('KEYFRAMES', "Keyframe Animations", "Process standard keyframe animations"),
            ('PARTICLES', "Particle Animations", "Process particle-based animations"),
        ],
        default='RIGGED',
        update=lambda self, context: self.update_file_format(context)  # Call update_file_format when animation_type changes
    )

    file_format: bpy.props.EnumProperty(
        name="Export Format",
        description="Choose the export file format",
        items=get_available_formats,
        default=None  # Avoid setting an invalid default
    )

    def update_file_format(self, context):
        """Ensure the selected file format is valid for the animation type."""
        available_formats = [item[0] for item in get_available_formats(self, context)]
        if self.file_format not in available_formats:
            self.file_format = available_formats[0] if available_formats else ''

class AssetifyAnimationSettings(bpy.types.PropertyGroup):
    animation_type: bpy.props.EnumProperty(
        name="Animation Type",
        description="Choose the type of animation to process",
        items=[
            ('RIGGED', "Rigged Animations", "Process rigged (armature-based) animations"),
            ('PHYSICS', "Physics Animations", "Process physics-based animations"),
            ('GEOMETRY_NODES', "Geometry Node Animations", "Process geometry node-driven animations"),
            ('SHAPE_KEYS', "Shape Key Animations", "Process shape key-based animations"),
            ('KEYFRAMES', "Keyframe Animations", "Process standard keyframe animations"),
            ('PARTICLES', "Particle Animations", "Process particle-based animations"),
        ],
        default='RIGGED',
        update=lambda self, context: self.update_file_format(context)  # Call update_file_format when animation_type changes
    )

    file_format: bpy.props.EnumProperty(
        name="Export Format",
        description="Choose the export file format",
        items=get_available_formats,
        default=None  # Avoid setting an invalid default
    )

    def update_file_format(self, context):
        """Ensure the selected file format is valid for the animation type."""
        available_formats = [item[0] for item in get_available_formats(self, context)]
        if self.file_format not in available_formats:
            self.file_format = available_formats[0] if available_formats else ''

def get_skip_conditions():
    """
    Returns a list of conditions to determine if conversion should be skipped.
    Each condition is a tuple (animation_type, file_format).
    """
    return [
        ('GEOMETRY_NODES', 'GLTF'),
        ('RIGGED', 'FBX'),
        ('PHYSICS', 'ALEMBIC', 'FBX'),
        ('PHYSICS', 'MDD'),  
        ('PHYSICS', 'CLOTH')
    ]

class ANIMATION_OT_bake_geometry_assets(bpy.types.Operator):
    """Add Bake Node to Geometry Nodes Assets"""
    bl_idname = "animation.bake_geometry_assets"
    bl_label = "Add Bake Node to Geometry Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    # Define a property to accept the object name
    obj_name: bpy.props.StringProperty(name="Object Name", default="")

    def execute(self, context):
        # Access the object by name
        obj = bpy.data.objects.get(self.obj_name)
        if not obj:
            self.report({'ERROR'}, f"Object {self.obj_name} not found.")
            return {'CANCELLED'}

        # Process the bake node for the given object
        assetify_settings = context.scene.assetify_bake_settings
        export_fbx_path = bpy.path.abspath(assetify_settings.export_fbx_path)

        geo_node_modifiers = [mod for mod in obj.modifiers if mod.type == 'NODES']
        for mod in geo_node_modifiers:
            add_bake_node_with_settings(mod, obj, export_fbx_path)
            print(f"[INFO] Successfully added and baked Bake Node for {obj.name}.")

        self.report({'INFO'}, f"Bake Node added and configured for {obj.name}.")
        return {'FINISHED'}
    
def process_animation_conditions(context, report_func=None, obj=None):
    """
    Checks conditions for processing animations and executes necessary operators or logic.
    Args:
        context (bpy.types.Context): Blender context object.
        report_func (function): Function for reporting messages.
        obj (bpy.types.Object): The object to process (optional).
    """
    settings = context.scene.assetify_animation_settings

    if obj is None:
        obj = context.active_object

    # Log object information
    print(f"[DEBUG] process_animation_conditions called for object: {obj.name if obj else 'None'}")
    print(f"[DEBUG] Object type: {obj.type if obj else 'None'}")

    animation_type = settings.animation_type
    file_format = settings.file_format

    # Log animation settings
    print(f"[DEBUG] Animation Type: {animation_type}, File Format: {file_format}")

    if animation_type == 'GEOMETRY_NODES' and file_format == 'GLTF':
        if report_func:
            report_func({'INFO'}, f"Processing Geometry Nodes Animation: {file_format} for {obj.name}")
        process_geonode_animation(file_format, obj)
        return {'FINISHED'}
    
    # Cloth Simulation Processing
    elif animation_type == 'PHYSICS' and file_format == 'CLOTH':
        if report_func:
            report_func({'INFO'}, f"Processing Cloth Simulation: {file_format} for {obj.name}")
        
        # Define start and end frames from the scene
        start_frame = bpy.context.scene.frame_start
        end_frame = bpy.context.scene.frame_end

        process_cloth_animation(obj, start_frame, end_frame)
        return {'FINISHED'}
    
    else:
        if report_func:
            report_func({'WARNING'}, f"No specific preprocessing for {animation_type} with {file_format} on {obj.name}.")
        return {'CANCELLED'} 

def register():
    print("[INFO] Registering AssetifyAnimationSettings and related classes...")

    bpy.types.Scene.is_processing = bpy.props.BoolProperty(
        name="Is Processing",
        description="Flag to indicate if a process is running",
        default=False
    )

    # Register AssetifyAnimationSettings first
    try:
        bpy.utils.register_class(AssetifyAnimationSettings)
        print("[INFO] AssetifyAnimationSettings registered successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to register AssetifyAnimationSettings: {e}")

    # Add PointerProperty and initialize settings
    try:
        bpy.types.Scene.assetify_animation_settings = bpy.props.PointerProperty(type=AssetifyAnimationSettings)
        print("[INFO] assetify_animation_settings added to bpy.types.Scene.")

        # Initialize settings and ensure file_format is valid
        settings = bpy.context.scene.assetify_animation_settings
        settings.update_file_format(bpy.context)
        print("[INFO] assetify_animation_settings initialized successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to initialize assetify_animation_settings: {e}")

    # Register other classes
    try:
        bpy.utils.register_class(ASSETIFY_OT_process_animation)
        print("[INFO] ASSETIFY_OT_process_animation registered successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to register ASSETIFY_OT_process_animation: {e}")

    # Safely register additional animation operators if needed
    try:
        bpy.utils.register_class(ANIMATION_OT_bake_geometry_assets)
        bpy.utils.register_class(ANIMATION_OT_apply_bake_to_keyframes)
        print("[INFO] Additional animation operators registered successfully.")
    except Exception as e:
        print(f"[ERROR] Failed to register additional animation operators: {e}")

def unregister():
    print("[INFO] Unregistering AssetifyAnimationSettings and related classes...")

    if hasattr(bpy.types.Scene, "is_processing"):
        del bpy.types.Scene.is_processing

    # Remove property if it exists
    if hasattr(bpy.types.Scene, "assetify_animation_settings"):
        try:
            del bpy.types.Scene.assetify_animation_settings
            print("[INFO] assetify_animation_settings removed from bpy.types.Scene.")
        except Exception as e:
            print(f"[ERROR] Failed to remove assetify_animation_settings: {e}")

    # Unregister anim_geonode classes
    try:
        bpy.utils.unregister_class(ANIMATION_OT_apply_bake_to_keyframes)
        print("[INFO] ANIMATION_OT_apply_bake_to_keyframes unregistered.")
    except Exception as e:
        print(f"[INFO] ANIMATION_OT_apply_bake_to_keyframes was not registered: {e}")

    try:
        bpy.utils.unregister_class(ANIMATION_OT_bake_geometry_assets)
        print("[INFO] ANIMATION_OT_bake_geometry_assets unregistered.")
    except Exception as e:
        print(f"[INFO] ANIMATION_OT_bake_geometry_assets was not registered: {e}")

    # Unregister other classes
    try:
        bpy.utils.unregister_class(ASSETIFY_OT_process_animation)
        print("[INFO] ASSETIFY_OT_process_animation unregistered.")
    except Exception as e:
        print(f"[INFO] ASSETIFY_OT_process_animation was not registered: {e}")

    try:
        bpy.utils.unregister_class(AssetifyAnimationSettings)
        print("[INFO] AssetifyAnimationSettings unregistered.")
    except Exception as e:
        print(f"[INFO] AssetifyAnimationSettings was not registered: {e}")

if __name__ == "__main__":
    register()

