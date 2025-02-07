import bpy

def bake_cloth_modifier(obj, cloth_mod):
    """
    Bakes only the Cloth modifier for the given object using the correct context.

    Args:
        obj (bpy.types.Object): The object with the Cloth modifier.
        cloth_mod (bpy.types.ClothModifier): The Cloth modifier to bake.
    """
    # Ensure the object is in Object Mode
    if obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    # Ensure the object is selected and active
    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    # Access the point cache of the Cloth modifier
    point_cache = cloth_mod.point_cache

    # Step 1: Clear existing bake with correct context
    print(f"[INFO] Clearing previous bake for {obj.name}...")
    with bpy.context.temp_override(
        scene=bpy.context.scene,
        object=obj,
        active_object=obj,
        selected_objects=[obj],
        point_cache=point_cache
    ):
        bpy.ops.ptcache.free_bake()

    # Step 2: Bake the Cloth modifier with correct context
    print(f"[INFO] Baking Cloth simulation for {obj.name}...")
    with bpy.context.temp_override(
        scene=bpy.context.scene,
        object=obj,
        active_object=obj,
        selected_objects=[obj],
        point_cache=point_cache
    ):
        bpy.ops.ptcache.bake(bake=True)

def process_cloth_animation(obj, start_frame, end_frame):
    """
    Bakes the Cloth simulation and converts it to shape keys for the given object.

    Args:
        obj (bpy.types.Object): The object with the Cloth modifier.
        start_frame (int): The starting frame for baking.
        end_frame (int): The ending frame for baking.
    """
    # Step 1: Find the Cloth modifier
    cloth_mod = next((mod for mod in obj.modifiers if mod.type == 'CLOTH'), None)
    if not cloth_mod:
        print(f"[ERROR] No Cloth modifier found on {obj.name}")
        return

    # Step 2: Bake the Cloth simulation for this modifier only
    bake_cloth_modifier(obj, cloth_mod)

    # Step 3: Ensure Basis Shape Key exists
    if not obj.data.shape_keys:
        obj.shape_key_add(name="Basis")

    shape_key_names = []

    # Step 4: Convert each frame to a shape key
    for frame in range(start_frame, end_frame + 1):
        bpy.context.scene.frame_set(frame)

        # Apply the cloth simulation as a shape key, keeping the modifier
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply_as_shapekey(keep_modifier=True, modifier=cloth_mod.name)

        # Rename the new shape key
        shape_key_name = f"Frame_{frame}"
        obj.data.shape_keys.key_blocks[-1].name = shape_key_name
        shape_key_names.append(shape_key_name)

    # Step 5: Animate the shape keys
    for frame in range(start_frame, end_frame + 1):
        for shape_key_name in shape_key_names:
            key = obj.data.shape_keys.key_blocks[shape_key_name]
            key.value = 1.0 if shape_key_name == f"Frame_{frame}" else 0.0
            key.keyframe_insert(data_path="value", frame=frame)

    # Step 6: Remove the Cloth modifier after baking
    obj.modifiers.remove(cloth_mod)

    print(f"[INFO] Cloth simulation baked, shape keys created, and Cloth modifier removed from {obj.name}")
