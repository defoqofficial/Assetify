import bpy
import mathutils
from mathutils import Vector
from bpy.types import NodeSocket

# Helper: ensure a 3-item tuple for vector inputs (for normals)
def ensure_vector3(val):
    if isinstance(val, (list, tuple)):
        if len(val) >= 3:
            return (val[0], val[1], val[2])
    return (0, 0, 0)

def get_ungroup_override_context(node_tree):
    context = bpy.context
    for window in context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            if area.type == 'NODE_EDITOR':
                override = {
                    'window': window,
                    'screen': screen,
                    'area': area,
                    'region': next((r for r in area.regions if r.type == 'WINDOW'), None),
                    'space_data': area.spaces.active,
                }
                area.spaces.active.tree_type = 'ShaderNodeTree'
                area.spaces.active.node_tree = node_tree
                return override

    for window in context.window_manager.windows:
        screen = window.screen
        for area in screen.areas:
            original_type = area.type
            if original_type in {'TOPBAR', 'STATUSBAR'}:
                continue
            area.type = 'NODE_EDITOR'
            area.spaces.active.tree_type = 'ShaderNodeTree'
            area.spaces.active.node_tree = node_tree
            override = {
                'window': window,
                'screen': screen,
                'area': area,
                'region': next((r for r in area.regions if r.type == 'WINDOW'), None),
                'space_data': area.spaces.active,
                'original_type': original_type,
            }
            return override
    return None

def ungroup_node_preserve_inputs(group_node, node_tree):
    if not group_node or not group_node.node_tree:
        return

    for i, input_socket in enumerate(group_node.inputs):
        if input_socket.is_linked:
            continue 

        socket_type = input_socket.type
        if socket_type == 'SHADER':
            continue

        default_val = input_socket.default_value

        if socket_type == 'VALUE':
            val_node = node_tree.nodes.new("ShaderNodeValue")
            val_node.label = f"GroupValue_{group_node.name}_{i}"
            try:
                val_node.outputs[0].default_value = float(default_val)
            except (TypeError, ValueError):
                val_node.outputs[0].default_value = 1.0
            val_node.location.x = group_node.location.x - 200
            val_node.location.y = group_node.location.y - (i * 40)
            node_tree.links.new(val_node.outputs[0], input_socket)

        elif socket_type == 'RGBA':
            rgb_node = node_tree.nodes.new("ShaderNodeRGB")
            rgb_node.label = f"GroupColor_{group_node.name}_{i}"
            rgb_node.location.x = group_node.location.x - 200
            rgb_node.location.y = group_node.location.y - (i * 40)
            color_4 = (1.0, 1.0, 1.0, 1.0)
            if isinstance(default_val, mathutils.Color):
                color_4 = (default_val.r, default_val.g, default_val.b, 1.0)
            elif hasattr(default_val, '__iter__'):
                temp = tuple(default_val)
                if len(temp) == 3:
                    color_4 = (temp[0], temp[1], temp[2], 1.0)
                elif len(temp) == 4:
                    color_4 = temp
            rgb_node.outputs[0].default_value = color_4
            node_tree.links.new(rgb_node.outputs[0], input_socket)

    for n in node_tree.nodes:
        n.select = False
    group_node.select = True
    node_tree.nodes.active = group_node

    context = bpy.context
    override = get_ungroup_override_context(node_tree)

    if override:
        try:
            with context.temp_override(**override):
                bpy.ops.node.group_ungroup()
        except RuntimeError as e:
            print(f"Failed to ungroup node: {e}")
        finally:
            if 'original_type' in override:
                override['area'].type = override['original_type']
    else:
        bpy.ops.node.group_ungroup('INVOKE_DEFAULT')

def ungroup_all_node_groups(node_tree):
    while True:
        group_nodes = [n for n in node_tree.nodes if n.type == 'GROUP']
        if not group_nodes:
            break
        for g_node in group_nodes:
            ungroup_node_preserve_inputs(g_node, node_tree)

def dissolve_node(node, node_tree):
    if node.type not in ('MIX_SHADER', 'ADD_SHADER'):
        return
    
    if node.type == 'ADD_SHADER':
        input_sock = node.inputs[0]
    else:
        input_sock = node.inputs[1]

    if not input_sock.is_linked:
        return 
    source_socket = input_sock.links[0].from_socket

    links_to_rewire = []
    for out_sock in node.outputs:
        for link in out_sock.links:
            links_to_rewire.append(link)
    for link in links_to_rewire:
        try:
            node_tree.links.new(source_socket, link.to_socket)
        except Exception as e:
            print(f"Error re-wiring link: {e}")

def ensure_color4(val):
    if isinstance(val, (int, float)):
        return (val, val, val, 1.0)
    elif isinstance(val, (list, tuple)):
        if len(val) == 4:
            return tuple(val)
        elif len(val) == 3:
            return (val[0], val[1], val[2], 1.0)
    return (0.0, 0.0, 0.0, 1.0)

def get_output_socket(node, socket_name="Color"):
    if node is None:
        return None
    for sock in node.outputs:
        if sock.name == socket_name:
            return sock
    if len(node.outputs) > 0:
        return node.outputs[0]
    return None

def has_node_input(shader_node, input_name):
    if shader_node:
        for inp in shader_node.inputs:
            if inp.name == input_name:
                return True
    return False

def get_node_input_by_name(shader_node, input_name):
    if shader_node:
        for inp in shader_node.inputs:
            if inp.name == input_name:
                return inp
    return None

def has_emission(shader_node):
    if shader_node:
        if shader_node.bl_idname == "ShaderNodeEmission":
            return shader_node.inputs.get("Color") is not None
        elif shader_node.bl_idname == "ShaderNodeBsdfPrincipled":
            return shader_node.inputs.get("Emission") is not None
    return False

def has_emission_strength(shader_node):
    if shader_node:
        if shader_node.bl_idname == "ShaderNodeEmission":
            return shader_node.inputs.get("Strength") is not None
        elif shader_node.bl_idname == "ShaderNodeBsdfPrincipled":
            return shader_node.inputs.get("Emission Strength") is not None
    return False

def get_emission_color_default(shader_node):
    if shader_node:
        if shader_node.bl_idname == "ShaderNodeEmission":
            sock = shader_node.inputs.get("Color")
            if sock:
                return sock.links[0].from_socket if sock.is_linked else sock.default_value
        elif shader_node.bl_idname == "ShaderNodeBsdfPrincipled":
            sock = shader_node.inputs.get("Emission")
            if sock:
                return sock.links[0].from_socket if sock.is_linked else sock.default_value
    return (0, 0, 0, 1)

def get_emission_strength_default(shader_node):
    if shader_node:
        if shader_node.bl_idname == "ShaderNodeEmission":
            sock = shader_node.inputs.get("Strength")
            if sock:
                return sock.links[0].from_socket if sock.is_linked else sock.default_value
        elif shader_node.bl_idname == "ShaderNodeBsdfPrincipled":
            sock = shader_node.inputs.get("Emission Strength")
            if sock:
                return sock.links[0].from_socket if sock.is_linked else sock.default_value
    return 0.0

def get_normal_value(shader_node):
    if shader_node:
        sock = shader_node.inputs.get("Normal")
        if sock:
            return sock.links[0].from_socket if sock.is_linked else ensure_vector3(sock.default_value)
    return (0, 0, 0)

def get_color_input_socket(shader_node):
    if not shader_node:
        return None
    if shader_node.bl_idname == 'ShaderNodeBsdfPrincipled':
        return shader_node.inputs.get("Base Color")
    elif shader_node.bl_idname == 'ShaderNodeBsdfMetallic':
        if "Color" in shader_node.inputs:
            return shader_node.inputs["Color"]
        elif len(shader_node.inputs) > 0:
            return shader_node.inputs[0]
        return None
    else:
        return shader_node.inputs.get("Color")

def get_color_default(shader_node):
    sock = get_color_input_socket(shader_node)
    if sock:
        val = sock.default_value
        if len(val) == 3:
            return (val[0], val[1], val[2], 1.0)
        return val
    return (1.0, 1.0, 1.0, 1.0)

def get_color_link(shader_node):
    sock = get_color_input_socket(shader_node)
    if sock and sock.is_linked:
        return sock.links[0].from_socket
    return None

def is_color_linked(shader_node):
    return (get_color_link(shader_node) is not None)

def get_roughness(shader_node):
    if shader_node and "Roughness" in shader_node.inputs:
        return shader_node.inputs["Roughness"].default_value
    return 1.0

def get_metallic_input_socket(shader_node):
    if not shader_node:
        return None
    if shader_node.bl_idname == 'ShaderNodeBsdfPrincipled':
        return shader_node.inputs.get("Metallic")
    elif shader_node.bl_idname == 'ShaderNodeBsdfMetallic':
        if len(shader_node.inputs) > 1:
            return shader_node.inputs[1]
        return None
    return None

def get_metallic_default(shader_node):
    sock = get_metallic_input_socket(shader_node)
    if sock:
        val = sock.default_value
        if isinstance(val, float):
            return (val, val, val, 1.0)
        if len(val) == 3:
            return (val[0], val[1], val[2], 1.0)
        return val
    if shader_node and shader_node.bl_idname == 'ShaderNodeBsdfMetallic':
        return (1.0, 1.0, 1.0, 1.0)
    return (0.0, 0.0, 0.0, 1.0)

def get_metallic_link(shader_node):
    sock = get_metallic_input_socket(shader_node)
    if sock and sock.is_linked:
        return sock.links[0].from_socket
    return None

def is_metallic_linked(shader_node):
    return (get_metallic_link(shader_node) is not None)

def get_alpha_input_socket(shader_node):
    if shader_node and shader_node.bl_idname == 'ShaderNodeBsdfPrincipled':
        return shader_node.inputs.get("Alpha")
    return None

def get_alpha_link(shader_node):
    sock = get_alpha_input_socket(shader_node)
    if sock and sock.is_linked:
        return sock.links[0].from_socket
    return None

def get_alpha_default(shader_node):
    sock = get_alpha_input_socket(shader_node)
    if sock:
        val = sock.default_value
        if isinstance(val, float):
            return (val, val, val, val)
        if len(val) == 1:
            return (val[0], val[0], val[0], val[0])
        if len(val) == 3:
            return (val[0], val[1], val[2], 1.0)
        return val
    return (1, 1, 1, 1)

def is_alpha_linked(shader_node):
    return (get_alpha_link(shader_node) is not None)

def create_mix_chains_and_principled():
    mat = bpy.context.active_object.active_material
    node_tree = mat.node_tree
    
    nodes = node_tree.nodes
    
    ungroup_all_node_groups(node_tree)

    # Deselect all nodes
    for node in nodes:
        node.select = False

    # 1) Gather all Mix and Add Shader nodes
    mix_shader_info = []
    for node in nodes:
        if node.type in ('MIX_SHADER', 'ADD_SHADER'):
            if node.mute:
                dissolve_node(node, node_tree)
                continue
            
            if node.type == 'ADD_SHADER':
                if len(node.inputs) < 2: continue
                fac_default = 0.0
                fac_linked = False
                shader1_input = node.inputs[0]
                shader2_input = node.inputs[1]
            else: 
                if len(node.inputs) < 3: continue
                fac_input = node.inputs[0]
                fac_default = fac_input.default_value
                fac_linked = fac_input.is_linked
                shader1_input = node.inputs[1]
                shader2_input = node.inputs[2]

            top_shader = shader1_input.links[0].from_node if shader1_input.is_linked else None
            bot_shader = shader2_input.links[0].from_node if shader2_input.is_linked else None
            
            info = {
                'node': node,
                'factor': fac_default if not fac_linked else None, 
                'shader1': top_shader,
                'shader2': bot_shader,
            }
            mix_shader_info.append(info)
            
    if not mix_shader_info:
        print("No active Mix Shader nodes found. Skipping non-principled BSDF baking setup.")
        return

    # 2) Build chain mapping
    chain_map = {}
    for info in mix_shader_info:
        current_node = info['node']
        out_links = current_node.outputs[0].links
        
        for link in out_links:
            if link.to_node.type in ('MIX_SHADER', 'ADD_SHADER'):
                target_node = link.to_node
                is_input_slot = False
                if target_node.type == 'MIX_SHADER':
                    if link.to_socket == target_node.inputs[1] or link.to_socket == target_node.inputs[2]:
                        is_input_slot = True
                elif target_node.type == 'ADD_SHADER':
                    if link.to_socket == target_node.inputs[0] or link.to_socket == target_node.inputs[1]:
                        is_input_slot = True     
                if is_input_slot:
                    chain_map[current_node] = target_node
                    break

    # 3) Find starting node
    starting_node = None
    for info in mix_shader_info:
        if info['node'] not in chain_map.values():
            starting_node = info['node']
            break

    chain_order = []
    if starting_node:
        chain_order.append(starting_node)
        while starting_node in chain_map:
            starting_node = chain_map[starting_node]
            chain_order.append(starting_node)
    else:
        chain_order = [info['node'] for info in mix_shader_info]

    info_dict = {info['node']: info for info in mix_shader_info}

    # 4) Layout parameters.
    row_y = {
        'color': 0,
        'roughness': -200,
        'transmission': -400,
        'principled_alpha': -800,
        'metallic': -600,
        'normal': -1000,
        'emission_color': -1200,
        'emission_strength': -1400,
    }
    start_x = -1000
    x_spacing = 300

    # Dictionaries to track the created mix nodes for each shader node
    # Key: shader_node (Mix/Add), Value: generated_color_mix_node
    mix_node_lookup_color = {}
    mix_node_lookup_roughness = {}
    mix_node_lookup_transmission = {}
    mix_node_lookup_alpha = {}
    mix_node_lookup_metallic = {}
    mix_node_lookup_normal = {}
    mix_node_lookup_emission_col = {}
    mix_node_lookup_emission_str = {}
    
    max_emission_strength = 0.0

    # 5) Loop over each Mix Shader in the chain.
    for idx, mix_node in enumerate(chain_order):
        if len(mix_node.outputs[0].links) == 0:
            pass # We process it anyway, but normally it should be linked

        info = info_dict[mix_node]
        top_shader = info['shader1']
        bot_shader = info['shader2']
        col_x = start_x + idx * x_spacing

        # -----------------------------------------------------
        # COLOR Mix (MixRGB)
        color_mix = nodes.new(type='ShaderNodeMixRGB')
        color_mix.label = f"ColorMix_for_{mix_node.name}"
        color_mix.blend_type = 'MIX'
        color_mix.location = (col_x, row_y['color'])

        # --- Set Factor ---
        if mix_node.type == 'ADD_SHADER':
            color_mix.blend_type = 'MIX'
            color_mix.inputs["Fac"].default_value = 0.0 
        elif mix_node.inputs[0].is_linked:
            fac_src = mix_node.inputs[0].links[0].from_socket
            node_tree.links.new(fac_src, color_mix.inputs["Fac"])
        else:
            color_mix.inputs["Fac"].default_value = mix_node.inputs[0].default_value

        # --- Detect Transparent BSDFs ---
        top_is_transparent = top_shader and top_shader.bl_idname == 'ShaderNodeBsdfTransparent'
        bot_is_transparent = bot_shader and bot_shader.bl_idname == 'ShaderNodeBsdfTransparent'

        print(f"[DEBUG] Mix Node: {mix_node.name} | Top Trans: {top_is_transparent} | Bot Trans: {bot_is_transparent}")

        # --- Standard Assignment using Lookup Table ---
        # Input 1 (Top)
        if top_shader in mix_node_lookup_color:
            # If top is a previously processed Mix Node
            node_tree.links.new(get_output_socket(mix_node_lookup_color[top_shader], "Color"), color_mix.inputs["Color1"])
        else:
            # If top is a standard shader node
            if top_shader and is_color_linked(top_shader):
                node_tree.links.new(get_color_link(top_shader), color_mix.inputs["Color1"])
            else:
                color_mix.inputs["Color1"].default_value = get_color_default(top_shader)

        # Input 2 (Bottom)
        if bot_shader in mix_node_lookup_color:
            # If bottom is a previously processed Mix Node
            node_tree.links.new(get_output_socket(mix_node_lookup_color[bot_shader], "Color"), color_mix.inputs["Color2"])
        else:
            # If bottom is a standard shader node
            if bot_shader and is_color_linked(bot_shader):
                node_tree.links.new(get_color_link(bot_shader), color_mix.inputs["Color2"])
            else:
                color_mix.inputs["Color2"].default_value = get_color_default(bot_shader)

        # --- Transparency Override Logic (The Fix) ---
        # If TOP is transparent, make Color1 match Color2
        if top_is_transparent:
            print(f"[DEBUG] >> Overriding TOP input with BOTTOM source for {mix_node.name}")
            if color_mix.inputs["Color2"].is_linked:
                source_link = color_mix.inputs["Color2"].links[0]
                node_tree.links.new(source_link.from_socket, color_mix.inputs["Color1"])
                print(f"[DEBUG]    Linked TOP to {source_link.from_socket.node.name}")
            else:
                color_mix.inputs["Color1"].default_value = color_mix.inputs["Color2"].default_value
                print(f"[DEBUG]    Copied value: {color_mix.inputs['Color2'].default_value}")

        # If BOTTOM is transparent, make Color2 match Color1
        if bot_is_transparent:
            print(f"[DEBUG] >> Overriding BOTTOM input with TOP source for {mix_node.name}")
            if color_mix.inputs["Color1"].is_linked:
                source_link = color_mix.inputs["Color1"].links[0]
                node_tree.links.new(source_link.from_socket, color_mix.inputs["Color2"])
                print(f"[DEBUG]    Linked BOTTOM to {source_link.from_socket.node.name}")
            else:
                color_mix.inputs["Color2"].default_value = color_mix.inputs["Color1"].default_value
                print(f"[DEBUG]    Copied value: {color_mix.inputs['Color1'].default_value}")
                
        # --- Force Black (Optional User Override) ---
        force_transparent_black = bpy.context.scene.assetify_bake_settings.force_transparent_black
        if force_transparent_black:
            if top_is_transparent:
                if color_mix.inputs["Color1"].links: node_tree.links.remove(color_mix.inputs["Color1"].links[0])
                color_mix.inputs["Color1"].default_value = (0.0, 0.0, 0.0, 1.0)
            if bot_is_transparent:
                if color_mix.inputs["Color2"].links: node_tree.links.remove(color_mix.inputs["Color2"].links[0])
                color_mix.inputs["Color2"].default_value = (0.0, 0.0, 0.0, 1.0)

        mix_node_lookup_color[mix_node] = color_mix

        # -----------------------------------------------------
        # ROUGHNESS Mix 
        roughness_mix = nodes.new(type='ShaderNodeMixRGB')
        roughness_mix.label = f"RoughnessMix_for_{mix_node.name}"
        roughness_mix.blend_type = 'MIX'
        roughness_mix.location = (col_x, row_y['roughness'])

        if mix_node.type == 'ADD_SHADER':
            roughness_mix.inputs["Fac"].default_value = 0.0
        elif mix_node.inputs[0].is_linked:
            fac_src = mix_node.inputs[0].links[0].from_socket
            node_tree.links.new(fac_src, roughness_mix.inputs["Fac"])
        else:
            roughness_mix.inputs["Fac"].default_value = mix_node.inputs[0].default_value

        # Top Input
        if top_shader in mix_node_lookup_roughness:
            node_tree.links.new(get_output_socket(mix_node_lookup_roughness[top_shader], "Color"), roughness_mix.inputs["Color1"])
        else:
            if top_shader:
                if top_shader.bl_idname == 'ShaderNodeBsdfDiffuse':
                    roughness_mix.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1)
                elif "Roughness" in top_shader.inputs and top_shader.inputs["Roughness"].is_linked:
                    node_tree.links.new(top_shader.inputs["Roughness"].links[0].from_socket, roughness_mix.inputs["Color1"])
                else:
                    top_rough = get_roughness(top_shader)
                    roughness_mix.inputs["Color1"].default_value = (top_rough, top_rough, top_rough, 1)
            else:
                roughness_mix.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1)

        # Bot Input
        if bot_shader in mix_node_lookup_roughness:
            node_tree.links.new(get_output_socket(mix_node_lookup_roughness[bot_shader], "Color"), roughness_mix.inputs["Color2"])
        else:
            if bot_shader and "Roughness" in bot_shader.inputs and bot_shader.inputs["Roughness"].is_linked:
                node_tree.links.new(bot_shader.inputs["Roughness"].links[0].from_socket, roughness_mix.inputs["Color2"])
            else:
                if bot_shader and bot_shader.bl_idname == 'ShaderNodeBsdfDiffuse':
                    roughness_mix.inputs["Color2"].default_value = (1.0, 1.0, 1.0, 1)
                else:
                    bot_rough = get_roughness(bot_shader)
                    roughness_mix.inputs["Color2"].default_value = (bot_rough, bot_rough, bot_rough, 1)

        mix_node_lookup_roughness[mix_node] = roughness_mix

        # -----------------------------------------------------
        # TRANSMISSION Mix
        top_has_trans = top_shader and ((top_shader.bl_idname == 'ShaderNodeBsdfGlass') or
                                        (top_shader.bl_idname == 'ShaderNodeBsdfPrincipled' and has_node_input(top_shader, "Transmission Weight")))
        bot_has_trans = bot_shader and ((bot_shader.bl_idname == 'ShaderNodeBsdfGlass') or
                                        (bot_shader.bl_idname == 'ShaderNodeBsdfPrincipled' and has_node_input(bot_shader, "Transmission Weight")))
        
        # Check if previous mixes exist (even if current shader nodes don't have trans, previous mix might)
        top_is_mix = top_shader in mix_node_lookup_transmission
        bot_is_mix = bot_shader in mix_node_lookup_transmission

        if top_has_trans or bot_has_trans or top_is_mix or bot_is_mix:
            transmission_mix = nodes.new(type='ShaderNodeMixRGB')
            transmission_mix.label = f"TransmissionMix_for_{mix_node.name}"
            transmission_mix.blend_type = 'MIX'
            transmission_mix.location = (col_x, row_y['transmission'])

            if mix_node.type == 'ADD_SHADER':
                transmission_mix.inputs["Fac"].default_value = 0.0
            elif mix_node.inputs[0].is_linked:
                fac_src = mix_node.inputs[0].links[0].from_socket
                node_tree.links.new(fac_src, transmission_mix.inputs["Fac"])
            else:
                transmission_mix.inputs["Fac"].default_value = mix_node.inputs[0].default_value

            # Top
            if top_is_mix:
                node_tree.links.new(get_output_socket(mix_node_lookup_transmission[top_shader], "Color"), transmission_mix.inputs["Color1"])
            else:
                transmission_mix.inputs["Color1"].default_value = (0, 0, 0, 1)
                if (top_shader and top_shader.bl_idname == 'ShaderNodeBsdfGlass'):
                    transmission_mix.inputs["Color1"].default_value = (1, 1, 1, 1)
                elif top_shader and top_shader.bl_idname == 'ShaderNodeBsdfPrincipled' and has_node_input(top_shader, "Transmission Weight"):
                    sock = get_node_input_by_name(top_shader, "Transmission Weight")
                    if sock and sock.is_linked:
                        node_tree.links.new(sock.links[0].from_socket, transmission_mix.inputs["Color1"])
                    elif sock:
                        transmission_mix.inputs["Color1"].default_value = ensure_color4(sock.default_value)

            # Bot
            if bot_is_mix:
                node_tree.links.new(get_output_socket(mix_node_lookup_transmission[bot_shader], "Color"), transmission_mix.inputs["Color2"])
            else:
                transmission_mix.inputs["Color2"].default_value = (0, 0, 0, 1) # Default to 0
                if (bot_shader and bot_shader.bl_idname == 'ShaderNodeBsdfGlass'):
                    transmission_mix.inputs["Color2"].default_value = (1, 1, 1, 1)
                elif bot_shader and bot_shader.bl_idname == 'ShaderNodeBsdfPrincipled' and has_node_input(bot_shader, "Transmission Weight"):
                    sock = get_node_input_by_name(bot_shader, "Transmission Weight")
                    if sock and sock.is_linked:
                        node_tree.links.new(sock.links[0].from_socket, transmission_mix.inputs["Color2"])
                    elif sock:
                        transmission_mix.inputs["Color2"].default_value = ensure_color4(sock.default_value)
            
            mix_node_lookup_transmission[mix_node] = transmission_mix

        # -----------------------------------------------------
        # EMISSION COLOR Mix
        emission_color_mix = nodes.new(type='ShaderNodeMixRGB')
        emission_color_mix.label = f"EmissionColorMix_for_{mix_node.name}"
        emission_color_mix.blend_type = 'MIX'
        emission_color_mix.location = (col_x, row_y['emission_color'])

        if mix_node.type == 'ADD_SHADER':
            emission_color_mix.blend_type = 'ADD'
            emission_color_mix.inputs["Fac"].default_value = 1.0
        elif mix_node.inputs[0].is_linked:
            fac_src = mix_node.inputs[0].links[0].from_socket
            node_tree.links.new(fac_src, emission_color_mix.inputs["Fac"])
        else:
            emission_color_mix.inputs["Fac"].default_value = mix_node.inputs[0].default_value

        # Top
        if top_shader in mix_node_lookup_emission_col:
            node_tree.links.new(get_output_socket(mix_node_lookup_emission_col[top_shader], "Color"), emission_color_mix.inputs["Color1"])
        else:
            if top_shader and has_emission(top_shader):
                em_color = get_emission_color_default(top_shader)
            else:
                em_color = (0, 0, 0, 1)
            if hasattr(em_color, "is_linked") and em_color.is_linked:
                node_tree.links.new(em_color, emission_color_mix.inputs["Color1"])
            else:
                emission_color_mix.inputs["Color1"].default_value = em_color

        # Bot
        if bot_shader in mix_node_lookup_emission_col:
             node_tree.links.new(get_output_socket(mix_node_lookup_emission_col[bot_shader], "Color"), emission_color_mix.inputs["Color2"])
        else:
            if bot_shader and has_emission(bot_shader):
                em_color = get_emission_color_default(bot_shader)
            else:
                em_color = (0, 0, 0, 1) 
            if hasattr(em_color, "is_linked") and em_color.is_linked:
                node_tree.links.new(em_color, emission_color_mix.inputs["Color2"])
            else:
                emission_color_mix.inputs["Color2"].default_value = em_color
        
        mix_node_lookup_emission_col[mix_node] = emission_color_mix

        # -----------------------------------------------------
        # EMISSION STRENGTH Mix
        emission_strength_mix = nodes.new(type='ShaderNodeMix')
        emission_strength_mix.label = f"EmissionStrengthMix_for_{mix_node.name}"
        emission_strength_mix.data_type = 'FLOAT'
        emission_strength_mix.location = (col_x, row_y['emission_strength'])

        if mix_node.type == 'ADD_SHADER':
            emission_strength_mix.blend_type = 'ADD'
            emission_strength_mix.inputs["Factor"].default_value = 1.0
        elif mix_node.inputs[0].is_linked:
            fac_src = mix_node.inputs[0].links[0].from_socket
            node_tree.links.new(fac_src, emission_strength_mix.inputs["Factor"])
        else:
            emission_strength_mix.inputs["Factor"].default_value = mix_node.inputs[0].default_value

        # Top
        if top_shader in mix_node_lookup_emission_str:
            node_tree.links.new(get_output_socket(mix_node_lookup_emission_str[top_shader], "Result"), emission_strength_mix.inputs["A"])
        else:
            if top_shader and has_emission_strength(top_shader):
                es_val = get_emission_strength_default(top_shader)
                if hasattr(es_val, "is_linked") and es_val.is_linked:
                    node_tree.links.new(es_val, emission_strength_mix.inputs["A"])
                else:
                    es_val = ensure_color4(es_val)
                    emission_strength_mix.inputs["A"].default_value = es_val[0]
            else:
                emission_strength_mix.inputs["A"].default_value = 0.0

        # Bot
        if bot_shader in mix_node_lookup_emission_str:
             node_tree.links.new(get_output_socket(mix_node_lookup_emission_str[bot_shader], "Result"), emission_strength_mix.inputs["B"])
        else:
            if bot_shader and has_emission_strength(bot_shader):
                es_val = get_emission_strength_default(bot_shader)
                if hasattr(es_val, "is_linked") and es_val.is_linked:
                    node_tree.links.new(es_val, emission_strength_mix.inputs["B"])
                else:
                    es_val = ensure_color4(es_val)
                    emission_strength_mix.inputs["B"].default_value = es_val[0]
            else:
                emission_strength_mix.inputs["B"].default_value = 0.0
        
        mix_node_lookup_emission_str[mix_node] = emission_strength_mix

        local_A = extract_max_from_socket(emission_strength_mix.inputs["A"])
        local_B = extract_max_from_socket(emission_strength_mix.inputs["B"])
        local_max = max(local_A, local_B)
        max_emission_strength = max(max_emission_strength, local_max)

        # -----------------------------------------------------
        # ALPHA Mix
        is_top_principled = top_shader and top_shader.bl_idname == 'ShaderNodeBsdfPrincipled'
        is_bot_principled = bot_shader and bot_shader.bl_idname == 'ShaderNodeBsdfPrincipled'
        top_is_mix_alpha = top_shader in mix_node_lookup_alpha
        bot_is_mix_alpha = bot_shader in mix_node_lookup_alpha

        if is_top_principled or is_bot_principled or top_is_mix_alpha or bot_is_mix_alpha or is_top_transparent or is_bot_transparent:
            alpha_mix = nodes.new(type='ShaderNodeMixRGB')
            alpha_mix.label = f"AlphaMix_Principled_for_{mix_node.name}"
            alpha_mix.blend_type = 'MIX'
            alpha_mix.location = (col_x, row_y['principled_alpha'])
            
            if mix_node.type == 'ADD_SHADER':
                alpha_mix.blend_type = 'MIX'
                alpha_mix.inputs["Fac"].default_value = 0.0 
            elif mix_node.inputs[0].is_linked:
                fac_src = mix_node.inputs[0].links[0].from_socket
                node_tree.links.new(fac_src, alpha_mix.inputs["Fac"])
            else:
                alpha_mix.inputs["Fac"].default_value = mix_node.inputs[0].default_value
            
            # Top
            if top_is_mix_alpha:
                 node_tree.links.new(get_output_socket(mix_node_lookup_alpha[top_shader], "Color"), alpha_mix.inputs["Color1"])
            elif top_is_transparent:  # <--- CORRECTED NAME
                alpha_mix.inputs["Color1"].default_value = (0.0, 0.0, 0.0, 0.0)
            elif is_top_principled:
                alpha_source = get_alpha_link(top_shader)
                if alpha_source:
                    node_tree.links.new(alpha_source, alpha_mix.inputs["Color1"])
                else:
                    alpha_mix.inputs["Color1"].default_value = get_alpha_default(top_shader)
            else:
                 alpha_mix.inputs["Color1"].default_value = (1, 1, 1, 1)

            # Bot
            if bot_is_mix_alpha:
                 node_tree.links.new(get_output_socket(mix_node_lookup_alpha[bot_shader], "Color"), alpha_mix.inputs["Color2"])
            elif bot_is_transparent:  # <--- CORRECTED NAME (was is_bot_transparent)
                 alpha_mix.inputs["Color2"].default_value = (0.0, 0.0, 0.0, 0.0)
            elif is_bot_principled:
                alpha_source = get_alpha_link(bot_shader)
                if alpha_source:
                    node_tree.links.new(alpha_source, alpha_mix.inputs["Color2"])
                else:
                    alpha_mix.inputs["Color2"].default_value = get_alpha_default(bot_shader)
            else:
                 alpha_mix.inputs["Color2"].default_value = (1, 1, 1, 1)
            
            mix_node_lookup_alpha[mix_node] = alpha_mix

        # -----------------------------------------------------
        # METALLIC Mix
        is_top_glossy = top_shader and (top_shader.bl_idname in {'ShaderNodeBsdfGlossy', 'ShaderNodeBsdfAnisotropic'})
        is_bot_glossy = bot_shader and (bot_shader.bl_idname in {'ShaderNodeBsdfGlossy', 'ShaderNodeBsdfAnisotropic'} )
        is_top_metal = top_shader and (top_shader.bl_idname in {'ShaderNodeBsdfMetallic', 'ShaderNodeBsdfPrincipled'} or is_top_glossy)
        is_bot_metal = bot_shader and (bot_shader.bl_idname in {'ShaderNodeBsdfMetallic', 'ShaderNodeBsdfPrincipled'} or is_bot_glossy)
        
        top_is_mix_metal = top_shader in mix_node_lookup_metallic
        bot_is_mix_metal = bot_shader in mix_node_lookup_metallic

        if is_top_metal or is_bot_metal or top_is_mix_metal or bot_is_mix_metal:
            metallic_mix = nodes.new(type='ShaderNodeMixRGB')
            metallic_mix.label = f"MetallicMix_for_{mix_node.name}"
            metallic_mix.blend_type = 'MIX'
            metallic_mix.location = (col_x, row_y['metallic'])

            if mix_node.type == 'ADD_SHADER':
                metallic_mix.inputs["Fac"].default_value = 0.0 
            elif mix_node.inputs[0].is_linked:
                fac_src = mix_node.inputs[0].links[0].from_socket
                node_tree.links.new(fac_src, metallic_mix.inputs["Fac"])
            else:
                metallic_mix.inputs["Fac"].default_value = mix_node.inputs[0].default_value

            # Top
            if top_is_mix_metal:
                node_tree.links.new(mix_node_lookup_metallic[top_shader].outputs["Color"], metallic_mix.inputs["Color1"])
            else:
                if top_shader:
                    if is_top_glossy:
                        metallic_mix.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
                    elif top_shader.bl_idname in {'ShaderNodeBsdfMetallic','ShaderNodeBsdfPrincipled'}:
                        if is_metallic_linked(top_shader):
                            node_tree.links.new(get_metallic_link(top_shader), metallic_mix.inputs["Color1"])
                        else:
                            metallic_mix.inputs["Color1"].default_value = get_metallic_default(top_shader)
                    else:
                        metallic_mix.inputs["Color1"].default_value = (0.0, 0.0, 0.0, 1.0)
                else:
                    metallic_mix.inputs["Color1"].default_value = (0.0, 0.0, 0.0, 1.0)

            # Bot
            if bot_is_mix_metal:
                node_tree.links.new(mix_node_lookup_metallic[bot_shader].outputs["Color"], metallic_mix.inputs["Color2"])
            else:
                if bot_shader:
                    if is_bot_glossy:
                        metallic_mix.inputs["Color2"].default_value = (1.0, 1.0, 1.0, 1.0)
                    elif bot_shader.bl_idname in {'ShaderNodeBsdfMetallic','ShaderNodeBsdfPrincipled'}:
                        if is_metallic_linked(bot_shader):
                            node_tree.links.new(get_metallic_link(bot_shader), metallic_mix.inputs["Color2"])
                        else:
                            metallic_mix.inputs["Color2"].default_value = get_metallic_default(bot_shader)
                    else:
                        metallic_mix.inputs["Color2"].default_value = (0.0, 0.0, 0.0, 1.0)
                else:
                    metallic_mix.inputs["Color2"].default_value = (0.0, 0.0, 0.0, 1.0)

            mix_node_lookup_metallic[mix_node] = metallic_mix
            
        # -----------------------------------------------------
        # NORMAL Mix
        top_has_normal = top_shader and ("Normal" in top_shader.inputs)
        bot_has_normal = bot_shader and ("Normal" in bot_shader.inputs)
        top_is_mix_normal = top_shader in mix_node_lookup_normal
        bot_is_mix_normal = bot_shader in mix_node_lookup_normal

        if top_has_normal or bot_has_normal or top_is_mix_normal or bot_is_mix_normal:
            normal_mix = nodes.new(type='ShaderNodeMix')
            normal_mix.label = f"NormalMix_for_{mix_node.name}"
            normal_mix.data_type = 'VECTOR'
            normal_mix.location = (col_x, row_y['normal'])
            
            if mix_node.type == 'ADD_SHADER':
                normal_mix.inputs["Factor"].default_value = 0.0 
            elif mix_node.inputs[0].is_linked:
                fac_src = mix_node.inputs[0].links[0].from_socket
                node_tree.links.new(fac_src, normal_mix.inputs["Factor"])
            else:
                normal_mix.inputs["Factor"].default_value = mix_node.inputs[0].default_value
            
            # Top
            if top_is_mix_normal:
                 node_tree.links.new(mix_node_lookup_normal[top_shader].outputs["Result"], normal_mix.inputs["A"])
            else:
                top_sock = top_shader.inputs.get("Normal") if top_shader else None
                if top_sock:
                    if top_sock.is_linked:
                        node_tree.links.new(top_sock.links[0].from_socket, normal_mix.inputs["A"])
                    else:
                        normal_mix.inputs["A"].default_value = ensure_vector3(top_sock.default_value)
                else:
                    normal_mix.inputs["A"].default_value = (0.0, 0.0, 0.0)
            
            # Bot
            if bot_is_mix_normal:
                 node_tree.links.new(mix_node_lookup_normal[bot_shader].outputs["Result"], normal_mix.inputs["B"])
            else:
                bot_sock = bot_shader.inputs.get("Normal") if bot_shader else None
                if bot_sock:
                    if bot_sock.is_linked:
                        node_tree.links.new(bot_sock.links[0].from_socket, normal_mix.inputs["B"])
                    else:
                        normal_mix.inputs["B"].default_value = ensure_vector3(bot_sock.default_value)
                else:
                    normal_mix.inputs["B"].default_value = (0.0, 0.0, 0.0)
            
            mix_node_lookup_normal[mix_node] = normal_mix

    # ---------------------------------------------------------
    # 6) Create Principled BSDF and final connections.
    final_col_x = start_x + len(chain_order) * x_spacing + 400
    principled_bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
    principled_bsdf.label = "Principled BSDF"
    principled_bsdf.location = (final_col_x, -150)

    # Use the last processed mix node (or starting node if it was a Mix) to connect to Principled
    last_mix_node = chain_order[-1] if chain_order else None

    # Base Color
    if last_mix_node in mix_node_lookup_color:
        node_tree.links.new(get_output_socket(mix_node_lookup_color[last_mix_node]), principled_bsdf.inputs["Base Color"])
    
    # Roughness
    if last_mix_node in mix_node_lookup_roughness:
        node_tree.links.new(get_output_socket(mix_node_lookup_roughness[last_mix_node]), principled_bsdf.inputs["Roughness"])
    
    # Transmission
    if last_mix_node in mix_node_lookup_transmission and "Transmission Weight" in principled_bsdf.inputs:
        node_tree.links.new(get_output_socket(mix_node_lookup_transmission[last_mix_node]), principled_bsdf.inputs["Transmission Weight"])
    
    # Alpha
    if last_mix_node in mix_node_lookup_alpha:
        node_tree.links.new(get_output_socket(mix_node_lookup_alpha[last_mix_node]), principled_bsdf.inputs["Alpha"])
    
    # Metallic
    if last_mix_node in mix_node_lookup_metallic:
        node_tree.links.new(get_output_socket(mix_node_lookup_metallic[last_mix_node]), principled_bsdf.inputs["Metallic"])
    
    # Normal
    if last_mix_node in mix_node_lookup_normal:
        node_tree.links.new(mix_node_lookup_normal[last_mix_node].outputs["Result"], principled_bsdf.inputs["Normal"])
    
    # Emission Color
    if last_mix_node in mix_node_lookup_emission_col and "Emission Color" in principled_bsdf.inputs:
        node_tree.links.new(get_output_socket(mix_node_lookup_emission_col[last_mix_node]), principled_bsdf.inputs["Emission Color"])
    
    # Emission Strength
    if last_mix_node in mix_node_lookup_emission_str and "Emission Strength" in principled_bsdf.inputs:
        map_range = nodes.new(type='ShaderNodeMapRange')
        map_range.label = "EmissionStrength_MapRange"
        map_range.location = (final_col_x - 200, row_y['emission_strength'])
        
        node_tree.links.new(get_output_socket(mix_node_lookup_emission_str[last_mix_node]), map_range.inputs["Value"])
        
        map_range.inputs["From Min"].default_value = 0.0
        map_range.inputs["From Max"].default_value = max_emission_strength if max_emission_strength > 0.0 else 1.0
        map_range.inputs["To Min"].default_value = 0.0
        map_range.inputs["To Max"].default_value = 1.0
        
        node_tree.links.new(get_output_socket(map_range), principled_bsdf.inputs["Emission Strength"])
        
    # ---------------------------------------------------------
    # MATERIAL OUTPUT CONNECTION
    material_output = None
    for node in nodes:
        if node.type == 'OUTPUT_MATERIAL':
            material_output = node
            break
    if not material_output:
        material_output = nodes.new(type='ShaderNodeOutputMaterial')
        material_output.location = (final_col_x + 250, -150)
    node_tree.links.new(principled_bsdf.outputs["BSDF"], material_output.inputs["Surface"])

def extract_max_from_socket(socket, visited=None):
    if visited is None:
        visited = set()
    if socket is None or socket in visited:
        return 0.0
    visited.add(socket)
    
    try:
        if not socket.is_linked:
            return float(socket.default_value)
    except Exception:
        return 0.0
    
    link = socket.links[0]
    from_socket = link.from_socket
    from_node = link.from_node
    
    if from_node.bl_idname == 'ShaderNodeMath' and from_node.operation == 'MULTIPLY':
        candidate_A = 0.0
        candidate_B = 0.0
        if not from_node.inputs[0].is_linked:
            candidate_A = float(from_node.inputs[0].default_value)
        else:
            candidate_A = extract_max_from_socket(from_node.inputs[0], visited)
        if not from_node.inputs[1].is_linked:
            candidate_B = float(from_node.inputs[1].default_value)
        else:
            candidate_B = extract_max_from_socket(from_node.inputs[1], visited)
        candidate = max(candidate_A, candidate_B)
        upstream_candidate = extract_max_from_socket(from_socket, visited)
        return max(candidate, upstream_candidate)
    else:
        return extract_max_from_socket(from_socket, visited)

def create_node_group(custom_name="Custom_NodeGroup"):
    context = bpy.context
    obj = context.object
    
    if not obj or not obj.active_material or not obj.active_material.node_tree:
        print("No active material with a node tree found!")
        return

    node_tree = obj.active_material.node_tree
    nodes = node_tree.nodes

    material_output = None
    for node in nodes:
        if node.type == 'OUTPUT_MATERIAL':
            material_output = node
            break

    override = None
    for area in context.screen.areas:
        if area.type == 'NODE_EDITOR':
            for space in area.spaces:
                if space.type == 'NODE_EDITOR':
                    override = {'window': context.window, 'screen': context.screen, 'area': area, 'region': area.regions[-1]}
                    break
            if override:
                break

    if not override:
        print("Please open a Node Editor before running this script.")
        return

    with context.temp_override(**override):
        bpy.ops.node.group_make()

    new_group_node = node_tree.nodes.active
    if new_group_node and new_group_node.type == 'GROUP':
        new_group_node.node_tree.name = custom_name
        new_group_node.label = custom_name

        if material_output:
            new_group_node.location = (
                material_output.location.x,
                material_output.location.y - 200
            )

    print(f"Node group created: {custom_name}")

    with context.temp_override(**override):
        if bpy.ops.node.tree_path_parent.poll():
            bpy.ops.node.tree_path_parent()

    print("Exited node group.")
