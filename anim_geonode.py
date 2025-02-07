import bpy
import json
import os
from mathutils import Vector, Matrix
import struct


# Global variable to store the last bake ID
_last_bake_id = None

def set_last_bake_id(bake_id):
    """Set the last bake ID."""
    global _last_bake_id
    _last_bake_id = bake_id
    print(f"[INFO] Bake ID set to: {_last_bake_id}")

def get_last_bake_id():
    """Get the last bake ID."""
    return _last_bake_id

def add_bake_node_with_settings(geometry_node_modifier, obj, export_fbx_path):
    """
    Adds a 'Realize Instances' node and a 'Bake' node to the Geometry Node tree of the given modifier,
    ensuring geometry is realized before baking, and sets up the bake operation.
    """
    if not geometry_node_modifier.node_group:
        print(f"[DEBUG] Modifier {geometry_node_modifier.name} has no node group.")
        return

    node_tree = geometry_node_modifier.node_group
    print(f"[DEBUG] Accessing node tree for modifier: {geometry_node_modifier.name}")

    # Find the Group Output node
    output_node = next((node for node in node_tree.nodes if node.type == 'GROUP_OUTPUT'), None)
    if not output_node:
        print(f"[DEBUG] No Group Output node found in node tree: {node_tree.name}")
        return

    # Get the input socket of the Group Output node
    geometry_input_socket = output_node.inputs.get('Geometry')
    if not geometry_input_socket or not geometry_input_socket.is_linked:
        print(f"[DEBUG] Output node in {node_tree.name} has no geometry input or is not linked.")
        return

    # Find the node currently linked to the Group Output node's Geometry input
    original_link = geometry_input_socket.links[0]
    previous_node_output = original_link.from_socket
    print(f"[DEBUG] Found link from {previous_node_output.node.name} to Group Output.")

    # Remove the existing link
    node_tree.links.remove(original_link)
    print(f"[DEBUG] Removed the existing link to Group Output node in {node_tree.name}.")

    # Create a 'Realize Instances' node
    realize_node = node_tree.nodes.new(type="GeometryNodeRealizeInstances")
    realize_node.name = "Realize Instances"
    realize_node.label = "Realize Instances"
    realize_node.location = output_node.location
    realize_node.location.x -= 400
    print(f"[DEBUG] Added 'Realize Instances' node in {node_tree.name}.")

    # Link the previous node to the Realize Instances node
    node_tree.links.new(previous_node_output, realize_node.inputs['Geometry'])
    print(f"[DEBUG] Connected {previous_node_output.node.name} to 'Realize Instances' node.")

    # Create a new 'Bake' node
    bake_node = node_tree.nodes.new(type="GeometryNodeBake")
    bake_node.name = "Bake"
    bake_node.label = "Bake"
    bake_node.location = output_node.location
    bake_node.location.x -= 200
    print(f"[DEBUG] Added 'Bake' node in {node_tree.name}.")

    # Connect the Realize Instances node to the Bake node
    node_tree.links.new(realize_node.outputs['Geometry'], bake_node.inputs['Geometry'])
    print(f"[DEBUG] Connected 'Realize Instances' node to 'Bake' node.")

    # Connect the Bake node to the Group Output
    node_tree.links.new(bake_node.outputs['Geometry'], geometry_input_socket)
    print(f"[DEBUG] Connected 'Bake' node to Group Output.")

    # Set the bake directory on the modifier
    geometry_node_modifier.bake_target = 'DISK'
    geometry_node_modifier.bake_directory = export_fbx_path
    print(f"[DEBUG] Set bake path on modifier to: {export_fbx_path}")

    # Fetch session UID
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated_obj = obj.evaluated_get(depsgraph)
    session_uid = obj.id_data.session_uid
    print(f"[INFO] Found Session UID: {session_uid}")

    # Retrieve bake_id and set bake_mode
    print("[DEBUG] Modifier Bakes:")
    for b in geometry_node_modifier.bakes:
        print(f" - Bake ID: {b.bake_id}")

    try:
        bakes = geometry_node_modifier.bakes
        bake_id = bakes[-1].bake_id  # Assume last entry corresponds to the added Bake Node
        print(f"[INFO] Found Bake Node ID: {bake_id}")

        # Save the bake_id
        set_last_bake_id(bake_id)

        # Set the mode to Animation
        bakes[-1].bake_mode = 'ANIMATION'
        print(f"[INFO] Set Bake Node mode to Animation.") 

    except IndexError:
        print("[ERROR] Bake Node ID not found in modifier's bake list.")
        return

    # Trigger bake operation
    try:
        bpy.ops.object.geometry_node_bake_single(
            session_uid=session_uid,
            modifier_name=geometry_node_modifier.name,
            bake_id=bake_id
        )
        print(f"[INFO] Successfully triggered bake for Bake Node in {obj.name}.")
    except Exception as e:
        print(f"[ERROR] Failed to bake Geometry Nodes: {e}")
        return

    # Optional: Remove the modifier (if required)
    try:
        print(f"[DEBUG] Removing Geometry Nodes modifier: {geometry_node_modifier.name}")
        # obj.modifiers.remove(geometry_node_modifier)
        print(f"[INFO] Removed Geometry Nodes modifier from {obj.name}.")
    except Exception as e:
        print(f"[ERROR] Failed to remove Geometry Nodes modifier: {e}")

class ANIMATION_OT_bake_geometry_assets(bpy.types.Operator):
    """Add Bake Node to Geometry Nodes Assets"""
    bl_idname = "animation.bake_geometry_assets"
    bl_label = "Add Bake Node to Geometry Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    # Register obj_name as a property
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

def populate_frame_data(bake_directory, frame_start, frame_end, bake_id):
    """Populate the global frame_data dictionary with parsed JSON and blob data."""
    global frame_data, blob_directory

    # Directories for meta and blob files
    meta_directory = os.path.join(bake_directory, str(bake_id), "meta")
    blob_directory = os.path.join(bake_directory, str(bake_id), "blobs")

    frame_data = {}  # Clear existing data

    for frame in range(frame_start, frame_end + 1):
        try:
            json_data, _ = parse_json_blob_per_frame(meta_directory, blob_directory, frame)
            if json_data is None:
                print(f"[WARNING] No data for frame {frame}. Skipping...")
                continue

            # Store the parsed JSON data for the current frame
            frame_data[frame] = json_data
            print(f"[INFO] Loaded frame data for frame {frame}.")

        except Exception as e:
            print(f"[ERROR] Failed to load data for frame {frame}: {e}")

    # Verify data population
    if not frame_data:
        print("[ERROR] No frame data was populated. Check your JSON and blob files.")
    else:
        print(f"[INFO] Frame data populated for {len(frame_data)} frames.")

class ANIMATION_OT_add_frame_dependent_attributes(bpy.types.Operator):
    """Add frame-dependent custom attributes from blob/meta files"""
    bl_idname = "animation.add_frame_dependent_attributes"
    bl_label = "Add Frame-Dependent Attributes"
    bl_options = {'REGISTER', 'UNDO'}

    bake_directory: bpy.props.StringProperty(
        name="Bake Directory",
        description="Path to the bake files",
        default=""
    )
    frame_start: bpy.props.IntProperty(name="Frame Start")
    frame_end: bpy.props.IntProperty(name="Frame End")
    
    def __init__(self):
        # Set defaults based on the current scene frame range
        scene = bpy.context.scene
        self.frame_start = scene.frame_start
        self.frame_end = scene.frame_end

    def invoke(self, context, event):
        # Update the properties dynamically when the operator is invoked
        scene = context.scene
        self.frame_start = scene.frame_start
        self.frame_end = scene.frame_end
        return self.execute(context)

    def execute(self, context):
        global obj  # Make `obj` available globally
        obj = context.object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "No mesh object selected.")
            return {'CANCELLED'}

        # Validate the bake directory
        bake_directory = self.bake_directory or validate_bake_directory(context)
        bake_id = get_last_bake_id()
        if not bake_id:
            self.report({'ERROR'}, "No Bake ID found.")
            return {'CANCELLED'}

        # Populate frame data
        populate_frame_data(bake_directory, self.frame_start, self.frame_end, bake_id)

        if not frame_data:
            self.report({'ERROR'}, "No frame data populated. Check the input files.")
            return {'CANCELLED'}

        # Ensure attributes are created only once
        attributes_created = set()
        for frame, json_data in frame_data.items():
            for item_id, item in json_data.get('items', {}).items():
                mesh_data = item.get('data', {}).get('mesh', {})
                attributes = mesh_data.get('attributes', [])

                for attribute in attributes:
                    name = attribute['name']
                    domain = attribute['domain']
                    # Check if the attribute already exists on the object
                    
                    if name in obj.data.attributes:
                        print(f"[INFO] Attribute '{name}' already exists. Skipping creation.")
                        continue

                    if name in attributes_created:
                        continue

                    attribute_type = 'FLOAT_VECTOR'  # Default to 3D vectors
                    if 'FLOAT2' in attribute.get('type', ''):
                        attribute_type = 'FLOAT2'
                    elif 'FLOAT' in attribute.get('type', ''):
                        attribute_type = 'FLOAT'

                    obj.data.attributes.new(name=name, type=attribute_type, domain=domain.upper())
                    attributes_created.add(name)
                    print(f"[INFO] Created attribute '{name}' with domain '{domain}'.")

        # Update attributes for the current frame
        update_attributes_on_frame(context.scene)

        # Register the frame change handler
        register_frame_change_handler()

        self.report({'INFO'}, "Frame-dependent attributes added and handler registered.")
        return {'FINISHED'}

    def get_dimensions_from_type(self, data_type):
        if data_type == 'FLOAT_VECTOR':
            return 3
        elif data_type == 'FLOAT2':
            return 2
        elif data_type == 'FLOAT':
            return 1
        else:
            print(f"[WARNING] Unknown data type '{data_type}', defaulting to 3 dimensions.")
            return 3

def update_attributes_on_frame(scene):
    """Dynamically update object attributes based on the current frame and print 'dist' attribute values."""
    global frame_data, blob_directory, obj  # Ensure these are accessible globally

    if obj is None:
        print("[ERROR] No object defined. Ensure the operator has been executed.")
        return

    current_frame = scene.frame_current

    # Check if data for the current frame exists
    if current_frame not in frame_data:
        print(f"[INFO] No data available for frame {current_frame}.")
        return

    json_data = frame_data[current_frame]
    print(f"[INFO] Updating attributes for frame {current_frame}.")

    for item_id, item in json_data.get('items', {}).items():
        mesh_data = item.get('data', {}).get('mesh', {})
        attributes = mesh_data.get('attributes', [])

        for attribute in attributes:
            name = attribute['name']
            data_info = attribute.get('data', {})
            start = data_info.get('start', -1)
            size = data_info.get('size', -1)
            data_type = attribute.get('type', 'FLOAT')

            # Determine the dimensions of the data type
            dimensions = 1 if data_type == "FLOAT" else 3  # Adjust for other types if needed

            # Skip invalid attribute definitions
            if start < 0 or size <= 0:
                print(f"[WARNING] Attribute '{name}' has invalid start/size data.")
                continue

            try:
                # Parse the binary data for the current attribute
                attribute_data = parse_attribute_blob_data(
                    os.path.join(blob_directory, f"{current_frame:05d}_00000.blob"),
                    start,
                    size,
                    data_type,
                    dimensions
                )
                if not attribute_data:
                    print(f"[ERROR] Failed to parse attribute '{name}' for frame {current_frame}.")
                    continue

                # Update the attribute values dynamically
                if name in obj.data.attributes:
                    attr_data = obj.data.attributes[name].data

                    # Debug specific attribute: 'dist'
                    if name == 'dist':
                        print(f"[DEBUG] Frame {current_frame} - 'dist' attribute values:")

                    for i, value in enumerate(attribute_data):
                        if i >= len(attr_data):
                            break  # Avoid exceeding the length of the attribute data

                        if dimensions == 1:
                            attr_data[i].value = value[0]  # Single float value
                            # Print 'dist' value if applicable
                            if name == 'dist':
                                print(f"  Vertex {i}: {value[0]}")
                        elif dimensions == 2:
                            attr_data[i].vector = (value[0], value[1], 0.0)  # Extend 2D to 3D if needed
                        elif dimensions == 3:
                            attr_data[i].vector = value  # Assign 3D vector directly

                    if name == 'dist':
                        print(f"[DEBUG] Total vertices updated for 'dist': {len(attribute_data)}")
                else:
                    print(f"[WARNING] Attribute '{name}' does not exist on the object and will be skipped.")

            except Exception as e:
                print(f"[ERROR] Exception while parsing attribute '{name}': {e}")

def get_dimensions_from_type(data_type):
    """Get the number of dimensions for the given data type."""
    if data_type == 'FLOAT_VECTOR':
        return 3
    elif data_type == 'FLOAT2':
        return 2
    elif data_type == 'FLOAT':
        return 1
    else:
        print(f"[WARNING] Unknown data type '{data_type}', defaulting to 3 dimensions.")
        return 3

def parse_json_blob_per_frame(meta_directory, blob_directory, frame_number):
    """Parse the .json and .blob files for a specific frame."""
    json_path = os.path.join(meta_directory, f"{frame_number:05d}_00000.json")
    blob_path = os.path.join(blob_directory, f"{frame_number:05d}_00000.blob")

    if not os.path.exists(json_path):
        print(f"[ERROR] JSON file not found: {json_path}")
        return None, None

    if not os.path.exists(blob_path):
        print(f"[ERROR] Blob file not found: {blob_path}")
        return None, None

    # Load JSON metadata
    with open(json_path, 'r') as json_file:
        json_data = json.load(json_file)

    print(f"[INFO] Loaded JSON Metadata for frame {frame_number}: {json_path}")

    # Open Blob file for reading
    with open(blob_path, 'rb') as blob_file:
        blob_data = blob_file.read()

    print(f"[INFO] Loaded Blob Data for frame {frame_number}: {blob_path}")
    return json_data, blob_data

def parse_blob_data(blob_path, start, size, data_type="f", dimensions=3):
    """
    Parse a specific range of binary data from a blob file.
    """
    with open(blob_path, "rb") as blob_file:
        blob_file.seek(start)
        data = blob_file.read(size)
    
    num_elements = size // (struct.calcsize(data_type) * dimensions)
    unpack_format = f"{num_elements * dimensions}{data_type}"
    raw_data = struct.unpack(unpack_format, data)
    
    # Group data into tuples of the specified dimensions
    return [tuple(raw_data[i:i + dimensions]) for i in range(0, len(raw_data), dimensions)]

def parse_attribute_blob_data(blob_path, start, size, data_type="FLOAT", dimensions=3):
    """
    Parse a specific range of binary data from a blob file for attributes.
    """
    type_mapping = {
        "FLOAT": ("f", 1),
        "FLOAT_VECTOR": ("f", 3),
        "FLOAT2": ("f", 2),
        "BOOLEAN": ("?", 1),
        "INT": ("i", 1),
        "INT32_2D": ("i", 2),
    }

    if data_type not in type_mapping:
        print(f"[WARNING] Unknown data type '{data_type}', defaulting to FLOAT_VECTOR.")
        data_type, dimensions = "FLOAT_VECTOR", 3

    format_char, expected_dimensions = type_mapping[data_type]
    if dimensions != expected_dimensions:
        print(f"[WARNING] Overriding dimensions for {data_type}. Expected: {expected_dimensions}, Got: {dimensions}")
        dimensions = expected_dimensions

    element_size = struct.calcsize(format_char) * dimensions
    num_elements = size // element_size

    if size % element_size != 0:
        print(f"[ERROR] Size mismatch for {data_type}: Expected multiple of {element_size}, Got {size}")
        return []

    unpack_format = f"{num_elements * dimensions}{format_char}"

    with open(blob_path, "rb") as blob_file:
        blob_file.seek(start)
        data = blob_file.read(size)

    if len(data) != size:
        print(f"[ERROR] Buffer size mismatch: Expected {size}, Got {len(data)}")
        return []

    raw_data = struct.unpack(unpack_format, data)
    parsed_data = [tuple(raw_data[i:i + dimensions]) for i in range(0, len(raw_data), dimensions)]

    print(f"[DEBUG] Parsed {len(parsed_data)} elements for data type '{data_type}' with dimensions {dimensions}.")
    return parsed_data

def show_vertex_mismatch_popup(num_positions, num_vertices, frame):
    def draw(self, context):
        self.layout.label(text=f"Vertex count mismatch at frame {frame}:")
        self.layout.label(text=f"Mismatch at {num_positions} positions vs {num_vertices} vertices.")
        self.layout.label(text="This indicates your original object vertex quantity does not match the geometry node output vertex quantity.")
        self.layout.label(text="Check your Geometry Nodes for nodes that change your vertex quantity.")
        self.layout.label(text="The process will now stop and revert changes.")
    bpy.context.window_manager.popup_menu(draw, title="Vertex Count Mismatch", icon='INFO')

def validate_bake_directory(context):
    """
    Validates the bake directory path from the Assetify settings.
    Args:
        context: Blender context object.
    Returns:
        A valid absolute bake directory path.
    Raises:
        RuntimeError: If the path is invalid or inaccessible.
    """
    assetify_settings = context.scene.assetify_bake_settings
    bake_directory = bpy.path.abspath(assetify_settings.export_fbx_path)

    if not bake_directory or not os.path.exists(bake_directory):
        raise RuntimeError(f"Invalid bake directory path: {bake_directory}")

    if not os.path.isdir(bake_directory):
        raise RuntimeError(f"Bake directory is not a folder: {bake_directory}")

    print(f"[DEBUG] Validated bake directory: {bake_directory}")
    return bake_directory

class ANIMATION_OT_apply_bake_to_keyframes(bpy.types.Operator):
    """Apply baked Geometry Node data to keyframes using Shape Keys"""
    bl_idname = "animation.apply_bake_to_keyframes"
    bl_label = "Apply Bake to Keyframes"
    bl_options = {'REGISTER', 'UNDO'}

    bake_directory: bpy.props.StringProperty(
        name="Bake Directory",
        description="Path to the bake files",
        default=""
    )

    def execute(self, context):
        # Validate and resolve the bake directory
        bake_directory = self.bake_directory or validate_bake_directory(context)
        print(f"[DEBUG] Using bake directory: {bake_directory}")

        obj = context.active_object
        bpy.context.view_layer.objects.active = obj  # Explicitly set the active object

        if not obj or obj.type != "MESH":
            self.report({'ERROR'}, "No valid active mesh object found.")
            return {'CANCELLED'}

        # Retrieve the bake_id
        bake_id = get_last_bake_id()
        if not bake_id:
            self.report({'ERROR'}, "No Bake ID found.")
            return {'CANCELLED'}

        meta_directory = os.path.join(bake_directory, str(bake_id), "meta")
        blob_directory = os.path.join(bake_directory, str(bake_id), "blobs")
        print(f"[DEBUG] bake_directory is: {bake_directory}")

        if not os.path.exists(meta_directory) or not os.path.exists(blob_directory):
            self.report({'ERROR'}, "Invalid bake directory paths.")
            return {'CANCELLED'}

        # Collect bake frame data
        bake_files = sorted([f for f in os.listdir(meta_directory) if f.endswith(".json")])
        if not bake_files:
            self.report({'ERROR'}, "No Meta files found in the specified directory.")
            return {'CANCELLED'}

        frame_numbers = [int(f.split('_')[0]) for f in bake_files]
        frame_start, frame_end = min(frame_numbers), max(frame_numbers)

        # Ensure the object has a Basis shape key
        if not obj.data.shape_keys:
            obj.shape_key_add(name="Basis")

        # Create shape keys from baked data
        frame_to_shapekey = {}
        created_shape_keys = []

        for frame in range(frame_start, frame_end + 1):
            json_data, blob_data = parse_json_blob_per_frame(meta_directory, blob_directory, frame)
            if not json_data or not blob_data:
                continue

            position_attribute = next(
                (attr for attr in json_data["items"]["0"]["data"]["mesh"]["attributes"] if attr["name"] == "position"),
                None
            )

            if not position_attribute:
                print(f"[ERROR] 'position' attribute not found in metadata for frame {frame}")
                continue

            blob_file_name = position_attribute["data"]["name"]
            blob_start = position_attribute["data"].get("start", -1)
            blob_size = position_attribute["data"].get("size", -1)

            if blob_start < 0 or blob_size <= 0:
                continue

            frame_blob_path = os.path.join(blob_directory, blob_file_name)
            if not os.path.exists(frame_blob_path):
                continue

            positions = parse_blob_data(frame_blob_path, blob_start, blob_size)
            if not positions:
                continue

            shape_key_name = f"Frame_{frame}"
            if shape_key_name not in obj.data.shape_keys.key_blocks:
                shape_key = obj.shape_key_add(name=shape_key_name, from_mix=False)
                created_shape_keys.append(shape_key)
            else:
                shape_key = obj.data.shape_keys.key_blocks[shape_key_name]

            # Assign geometry to shape key (local space)
            for vertex_index, position in enumerate(positions):
                adjusted_position = (position[0], position[1], position[2])  # Local coordinates
                shape_key.data[vertex_index].co = adjusted_position

            frame_to_shapekey[frame] = shape_key_name
            print(f"[INFO] Created/updated shape key for frame {frame}")

            # Compare positions for the final frame
            if frame == frame_end:
                print(f"[INFO] Comparing blob data with shape key for final frame {frame}")
                compare_blob_with_shape_key(obj, shape_key_name, positions)

        # Keyframe shape keys
        reset_all_shape_keys(obj, frame_start - 1)

        for frame in range(frame_start, frame_end + 1):
            reset_all_shape_keys(obj, frame)
            if frame in frame_to_shapekey:
                shape_key = obj.data.shape_keys.key_blocks[frame_to_shapekey[frame]]
                shape_key.value = 1.0
                shape_key.keyframe_insert(data_path="value", frame=frame)
                print(f"[INFO] Set {frame_to_shapekey[frame]} to 1.0 at frame {frame}")

        reset_all_shape_keys(obj, frame_end + 1)

        # Set keyframe interpolation to CONSTANT
        if obj.data.shape_keys and obj.data.shape_keys.animation_data and obj.data.shape_keys.animation_data.action:
            action = obj.data.shape_keys.animation_data.action
            for fcurve in action.fcurves:
                for kp in fcurve.keyframe_points:
                    kp.interpolation = 'CONSTANT'

        self.report({'INFO'}, f"Keyframes applied to {obj.name} from frame {frame_start} to {frame_end}.")
        return {'FINISHED'}

def compare_blob_with_shape_key(obj, shape_key_name, blob_positions):
    """
    Compare vertex positions from the blob data with the shape key data.

    Args:
        obj: The Blender object containing the shape key.
        shape_key_name: The name of the shape key to compare against.
        blob_positions: The vertex positions from the blob data.
    """
    if not obj.data.shape_keys or shape_key_name not in obj.data.shape_keys.key_blocks:
        print(f"[ERROR] Shape key '{shape_key_name}' not found in object '{obj.name}'.")
        return

    shape_key = obj.data.shape_keys.key_blocks[shape_key_name]

    for i, blob_position in enumerate(blob_positions):
        shape_key_position = shape_key.data[i].co
        if not all(abs(blob_position[j] - shape_key_position[j]) < 1e-6 for j in range(3)):
            print(f"[MISMATCH] Vertex {i} - Blob: {blob_position}, Shape Key: {shape_key_position}")
        else:
            print(f"[MATCH] Vertex {i} - Blob: {blob_position}, Shape Key: {shape_key_position}")

def reset_all_shape_keys(obj, frame):
    for sk in obj.data.shape_keys.key_blocks:
        sk.value = 0.0
        sk.keyframe_insert(data_path="value", frame=frame)

def process_geonode_animation(file_format, obj):
    """
    General function to call both bake_geometry_assets and apply_bake_to_keyframes on a specific object.
    Args:
        file_format (str): Export file format.
        obj (bpy.types.Object): The object to process.
    Returns:
        {'FINISHED'} if successful, {'CANCELLED'} otherwise.
    """
    if not obj:
        print("[ERROR] process_geonode_animation called with no object.")
        return {'CANCELLED'}

    # Debug: Log the object being processed
    print(f"[DEBUG] process_geonode_animation called for object: {obj.name}")

    try:
        # Set the object as the active object in the context
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        print(f"[DEBUG] {obj.name} is now active and selected.")

        # Use the operator, setting the obj_name in context
        bpy.context.window_manager["bake_geometry_obj_name"] = obj.name
        print(f"[DEBUG] Calling bake_geometry_assets for {obj.name}")
        result_bake = bpy.ops.animation.bake_geometry_assets(obj_name=obj.name)
        if result_bake != {'FINISHED'}:
            print(f"[ERROR] bake_geometry_assets operation failed for {obj.name}.")
            return {'CANCELLED'}
        
        # Jump to the first frame in the scene range
        last_frame = bpy.context.scene.frame_start
        bpy.context.scene.frame_set(last_frame)
        
        # Convert the object to a mesh to apply all modifiers
        print(f"[DEBUG] Converting {obj.name} to a mesh to apply modifiers.")
        bpy.ops.object.convert(target='MESH', keep_original=False)
        #print(f"[INFO] {obj.name} successfully converted to a mesh.")
        #return {'FINISHED'}

        # Step 2: Call apply_bake_to_keyframes
        print(f"[DEBUG] Calling apply_bake_to_keyframes for {obj.name}")
        result_keyframes = bpy.ops.animation.apply_bake_to_keyframes()
        if result_keyframes != {'FINISHED'}:
            print(f"[ERROR] apply_bake_to_keyframes operation failed for {obj.name}.")
            return {'CANCELLED'}

        print(f"[INFO] Successfully baked assets and applied keyframes for {obj.name}.")
        return {'FINISHED'}
    except Exception as e:
        print(f"[ERROR] {e}")
        return {'CANCELLED'}
    finally:
        # Deselect the object after processing
        obj.select_set(False)
        print(f"[DEBUG] Deselected {obj.name} after processing.")


# Safe registration
classes = [ANIMATION_OT_bake_geometry_assets, ANIMATION_OT_apply_bake_to_keyframes, ANIMATION_OT_add_frame_dependent_attributes]

# Register the frame change handler
def register_frame_change_handler():
    if update_attributes_on_frame not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(update_attributes_on_frame)
        print("[INFO] Frame change handler registered.")

# Unregister the frame change handler
def unregister_frame_change_handler():
    if update_attributes_on_frame in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(update_attributes_on_frame)
        print("[INFO] Frame change handler unregistered.")

def register():
    for cls in classes:
        if cls.__name__ not in bpy.types.__dict__:
            bpy.utils.register_class(cls)
        else:
            print(f"[DEBUG] {cls.__name__} already registered, skipping.")

def unregister():
    for cls in reversed(classes):
        if cls.__name__ in bpy.types.__dict__:
            bpy.utils.unregister_class(cls)
        else:
            print(f"[DEBUG] {cls.__name__} not registered, skipping.")

if __name__ == "__main__":
    register()
    # Test run
    bpy.ops.animation.bake_geometry_assets()


