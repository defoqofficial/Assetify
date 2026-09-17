import bpy
import os
import shutil
import tempfile

class MockEvent:
    def __init__(self, event_type):
        self.type = event_type

def run_test():
    print("=== STARTING ASSETIFY TEXTURE OUTPUT TEST ===")

    # Ensure addon is enabled
    import addon_utils
    addon_utils.enable("Assetify-master", default_set=True)

    # Clean scene without unregistering addon properties
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)
    for mat in list(bpy.data.materials):
        bpy.data.materials.remove(mat)
    for img in list(bpy.data.images):
        bpy.data.images.remove(img)

    # Create temporary output folder
    temp_dir = tempfile.mkdtemp(prefix="assetify_bake_test_")
    print(f"Temp bake directory: {temp_dir}")

    try:
        scene = bpy.context.scene
        settings = scene.assetify_bake_settings
        settings.bake_folder = temp_dir
        settings.bake_resolution = '512'
        settings.skip_save_check = True
        settings.render_device = 'CPU'
        settings.bake_basecolor = True
        settings.bake_roughness = True
        settings.bake_metallic = True
        settings.bake_normal = False
        settings.bake_ao = False
        settings.bake_emission = False
        settings.bake_transmission = False
        settings.pack_orm = False
        settings.uv_mode = 'UNWRAP'
        settings.texturebake_mode = 'STILL'

        # ----------------------------------------------------
        # TEST 1: COMBINED ATLAS BAKING
        # ----------------------------------------------------
        print("\n--- TEST 1: COMBINED ATLAS BAKING ---")
        settings.texture_output_mode = 'COMBINED'
        settings.asset_mode = 'COLLECTION'

        # Create test collection
        col = bpy.data.collections.new("TestGroup_GameReady")
        bpy.context.scene.collection.children.link(col)

        # Create two mesh objects with different materials and colors
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
        cube1 = bpy.context.active_object
        cube1.name = "Cube1"
        col.objects.link(cube1)

        mat1 = bpy.data.materials.new("Mat_Red")
        mat1.use_nodes = True
        bsdf1 = mat1.node_tree.nodes.get("Principled BSDF")
        bsdf1.inputs['Base Color'].default_value = (1.0, 0.0, 0.0, 1.0)
        bsdf1.inputs['Roughness'].default_value = 0.2
        cube1.data.materials.append(mat1)

        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(2, 0, 0))
        cube2 = bpy.context.active_object
        cube2.name = "Cube2"
        col.objects.link(cube2)

        mat2 = bpy.data.materials.new("Mat_Blue")
        mat2.use_nodes = True
        bsdf2 = mat2.node_tree.nodes.get("Principled BSDF")
        bsdf2.inputs['Base Color'].default_value = (0.0, 0.0, 1.0, 1.0)
        bsdf2.inputs['Roughness'].default_value = 0.8
        cube2.data.materials.append(mat2)

        # Populate baked collections
        settings.baked_collections.clear()
        bc = settings.baked_collections.add()
        bc.name = col.name
        bc.include_in_send = True

        # Setup DummyOp to step through operator execution in headless mode
        class DummyOp:
            def __init__(self):
                self._timer = None
                self._bake_index = 0
                self._step_index = 0
                self._objects_to_bake = []
                self._bake_groups = []
                self._steps_per_object = 8
                self.progress_value = 0.0
                self.total_bake_steps = 0
                self.bake_progress = 0
                self.current_operation = ""
                self.current_sub_operation = ""
                self._active_proxy = None
                self._active_original = None
                self._active_original_name = ""
            def report(self, type_set, msg):
                print(f"REPORT {type_set}: {msg}")

        execute_fn = bpy.types.OBJECT_OT_bake_textures_modal.execute
        modal_fn = bpy.types.OBJECT_OT_bake_textures_modal.modal
        collect_fn = bpy.types.OBJECT_OT_bake_textures_modal.collect_objects_from_collection

        op = DummyOp()
        op.collect_objects_from_collection = lambda col: collect_fn(op, col)
        res = execute_fn(op, bpy.context)
        assert res == {'RUNNING_MODAL'}, f"Execute failed: {res}"

        # Step through modal loop until FINISHED
        max_iters = 100
        iters = 0
        timer_event = MockEvent('TIMER')
        done = False
        while iters < max_iters:
            step_res = modal_fn(op, bpy.context, timer_event)
            iters += 1
            if step_res == {'FINISHED'}:
                done = True
                print(f"Modal completed in {iters} iterations.")
                break
            elif step_res == {'CANCELLED'}:
                raise RuntimeError(f"Modal cancelled unexpectedly at step {op._step_index}")

        assert done, f"Modal did not finish within {max_iters} iterations"

        # Verify output files: should have TestGroup_BaseColor.png, TestGroup_Roughness.png, TestGroup_Metallic.png
        tex_folder = os.path.join(temp_dir, "textures")
        assert os.path.exists(tex_folder), f"Textures folder not found at {tex_folder}"
        files = os.listdir(tex_folder)
        print(f"Generated textures for Combined mode: {files}")

        assert "TestGroup_BaseColor.png" in files, f"Missing TestGroup_BaseColor.png in {files}"
        assert "TestGroup_Roughness.png" in files, f"Missing TestGroup_Roughness.png in {files}"
        assert "TestGroup_Metallic.png" in files, f"Missing TestGroup_Metallic.png in {files}"

        # Assert NO per-object files were created
        assert "Cube1_BaseColor.png" not in files, "Cube1_BaseColor.png should NOT exist in COMBINED mode!"
        assert "Cube2_BaseColor.png" not in files, "Cube2_BaseColor.png should NOT exist in COMBINED mode!"

        # Verify UV maps and materials on both cubes
        for c in [cube1, cube2]:
            assert "UVMap" in c.data.uv_layers, f"{c.name} missing 'UVMap'"
            assert len(c.data.materials) == 1, f"{c.name} has {len(c.data.materials)} materials, expected 1"
            assert c.data.materials[0].name == "TestGroup_Material", f"{c.name} material is {c.data.materials[0].name}, expected TestGroup_Material"

        print(">>> TEST 1 (COMBINED ATLAS) PASSED SUCCESSFULLY! <<<")

        # ----------------------------------------------------
        # TEST 2: INDIVIDUAL TEXTURE BAKING (Backward Compatibility)
        # ----------------------------------------------------
        print("\n--- TEST 2: INDIVIDUAL TEXTURE BAKING ---")
        # Clean output folder
        shutil.rmtree(tex_folder, ignore_errors=True)

        settings.texture_output_mode = 'INDIVIDUAL'
        
        # Reset material names
        cube1.data.materials.clear()
        cube1.data.materials.append(mat1)
        cube2.data.materials.clear()
        cube2.data.materials.append(mat2)

        op2 = DummyOp()
        op2.collect_objects_from_collection = lambda col: collect_fn(op2, col)
        res = execute_fn(op2, bpy.context)
        assert res == {'RUNNING_MODAL'}, f"Execute failed: {res}"

        iters = 0
        done = False
        while iters < max_iters:
            step_res = modal_fn(op2, bpy.context, timer_event)
            iters += 1
            if step_res == {'FINISHED'}:
                done = True
                print(f"Modal completed in {iters} iterations.")
                break
            elif step_res == {'CANCELLED'}:
                raise RuntimeError(f"Modal cancelled unexpectedly at step {op2._step_index}")

        assert done, f"Modal did not finish within {max_iters} iterations"

        files = os.listdir(tex_folder)
        print(f"Generated textures for Individual mode: {files}")

        assert "Cube1_BaseColor.png" in files, f"Missing Cube1_BaseColor.png in {files}"
        assert "Cube2_BaseColor.png" in files, f"Missing Cube2_BaseColor.png in {files}"
        assert "TestGroup_BaseColor.png" not in files, "TestGroup_BaseColor.png should NOT exist in INDIVIDUAL mode!"

        print(">>> TEST 2 (INDIVIDUAL) PASSED SUCCESSFULLY! <<<")
        print("\nALL UNIT TESTS PASSED!")

    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == "__main__":
    run_test()
