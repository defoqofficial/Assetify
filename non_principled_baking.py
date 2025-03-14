import bpy
from mathutils import Vector
from bpy.types import NodeSocket

# Helper: ensure a 3-item tuple for vector inputs (for normals)
def ensure_vector3(val):
    if isinstance(val, (list, tuple)):
        if len(val) >= 3:
            return (val[0], val[1], val[2])
    return (0, 0, 0)

# Helper: ensure a 4-item tuple for color/float values
def ensure_color4(val):
    if isinstance(val, (int, float)):
        return (val, val, val, 1.0)
    elif isinstance(val, (list, tuple)):
        if len(val) == 4:
            return tuple(val)
        elif len(val) == 3:
            return (val[0], val[1], val[2], 1.0)
    return (0.0, 0.0, 0.0, 1.0)

# Helper: get a socket output by name (explicit loop)
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

# Emission Helpers
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

# Normal Helper
def get_normal_value(shader_node):
    if shader_node:
        sock = shader_node.inputs.get("Normal")
        if sock:
            return sock.links[0].from_socket if sock.is_linked else ensure_vector3(sock.default_value)
    return (0, 0, 0)

# Color Helpers
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

# Roughness Helper
def get_roughness(shader_node):
    if shader_node and "Roughness" in shader_node.inputs:
        return shader_node.inputs["Roughness"].default_value
    return 1.0

# Metallic Helpers
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

# Alpha Helpers (for Principled)
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

    # Deselect all nodes
    for node in nodes:
        node.select = False

    # 1) Gather all Mix Shader nodes and connected shader nodes.
    mix_shader_info = []
    for node in nodes:
        if node.type == 'MIX_SHADER':
            fac_input = node.inputs[0]
            shader1_input = node.inputs[1]
            shader2_input = node.inputs[2]
            top_shader = shader1_input.links[0].from_node if shader1_input.is_linked else None
            bot_shader = shader2_input.links[0].from_node if shader2_input.is_linked else None
            info = {
                'node': node,
                'factor': fac_input.default_value if not fac_input.is_linked else None,
                'shader1': top_shader,
                'shader2': bot_shader,
            }
            mix_shader_info.append(info)

    # 2) Build chain mapping (which mix feeds into which).
    chain_map = {}
    for info in mix_shader_info:
        current_node = info['node']
        for link in current_node.outputs[0].links:
            if link.to_node.type == 'MIX_SHADER' and link.to_socket == link.to_node.inputs[1]:
                chain_map[current_node] = link.to_node
                break

    # 3) Find starting node (one that is not an input to another mix).
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

    # Track previous mix nodes.
    prev_color_mix = None
    prev_roughness_mix = None
    prev_transmission_mix = None
    prev_alpha_mix = None
    prev_metallic_mix = None
    prev_normal_mix = None
    prev_emission_color_mix = None
    prev_emission_strength_mix = None
    max_emission_strength = 0.0

    # 5) Loop over each Mix Shader in the chain.
    for idx, mix_node in enumerate(chain_order):
        # If the mix node's first output is not connected, skip it.
        if len(mix_node.outputs[0].links) == 0:
            continue

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

        # Set the mix factor from the mix shader node.
        if mix_node.inputs[0].is_linked:
            fac_src = mix_node.inputs[0].links[0].from_socket
            node_tree.links.new(fac_src, color_mix.inputs["Fac"])
        else:
            color_mix.inputs["Fac"].default_value = mix_node.inputs[0].default_value

        if prev_color_mix:
            node_tree.links.new(get_output_socket(prev_color_mix, "Color"), color_mix.inputs["Color1"])
        else:
            if top_shader and is_color_linked(top_shader):
                node_tree.links.new(get_color_link(top_shader), color_mix.inputs["Color1"])
            else:
                color_mix.inputs["Color1"].default_value = get_color_default(top_shader)

        # Set Color2 from the bottom shader.
        if bot_shader and is_color_linked(bot_shader):
            node_tree.links.new(get_color_link(bot_shader), color_mix.inputs["Color2"])
        else:
            color_mix.inputs["Color2"].default_value = get_color_default(bot_shader)
            
        force_transparent_black = bpy.context.scene.assetify_bake_settings.force_transparent_black

        # If the force checkbox is enabled, force any Transparent BSDF color to black.
        if force_transparent_black:
            # If the top shader is Transparent, force its color to black.
            if top_shader and top_shader.bl_idname == 'ShaderNodeBsdfTransparent':
                while color_mix.inputs["Color1"].links:
                    node_tree.links.remove(color_mix.inputs["Color1"].links[0])
                color_mix.inputs["Color1"].default_value = (0.0, 0.0, 0.0, 1.0)
            # If the bottom shader is Transparent, force its color to black.
            if bot_shader and bot_shader.bl_idname == 'ShaderNodeBsdfTransparent':
                while color_mix.inputs["Color2"].links:
                    node_tree.links.remove(color_mix.inputs["Color2"].links[0])
                color_mix.inputs["Color2"].default_value = (0.0, 0.0, 0.0, 1.0)

        prev_color_mix = color_mix

        # -----------------------------------------------------
        # ROUGHNESS Mix with Diffuse override
        roughness_mix = nodes.new(type='ShaderNodeMixRGB')
        roughness_mix.label = f"RoughnessMix_for_{mix_node.name}"
        roughness_mix.blend_type = 'MIX'
        roughness_mix.location = (col_x, row_y['roughness'])

        if mix_node.inputs[0].is_linked:
            fac_src = mix_node.inputs[0].links[0].from_socket
            node_tree.links.new(fac_src, roughness_mix.inputs["Fac"])
        else:
            roughness_mix.inputs["Fac"].default_value = mix_node.inputs[0].default_value

        if prev_roughness_mix:
            node_tree.links.new(get_output_socket(prev_roughness_mix, "Color"), roughness_mix.inputs["Color1"])
        else:
            if top_shader:
                # If the top shader is Diffuse, force roughness to 1.
                if top_shader.bl_idname == 'ShaderNodeBsdfDiffuse':
                    roughness_mix.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1)
                elif "Roughness" in top_shader.inputs and top_shader.inputs["Roughness"].is_linked:
                    node_tree.links.new(top_shader.inputs["Roughness"].links[0].from_socket, roughness_mix.inputs["Color1"])
                else:
                    top_rough = get_roughness(top_shader)
                    roughness_mix.inputs["Color1"].default_value = (top_rough, top_rough, top_rough, 1)
            else:
                roughness_mix.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1)

        if bot_shader and "Roughness" in bot_shader.inputs and bot_shader.inputs["Roughness"].is_linked:
            node_tree.links.new(bot_shader.inputs["Roughness"].links[0].from_socket, roughness_mix.inputs["Color2"])
        else:
            # If bot shader is Diffuse, force roughness to 1.
            if bot_shader and bot_shader.bl_idname == 'ShaderNodeBsdfDiffuse':
                roughness_mix.inputs["Color2"].default_value = (1.0, 1.0, 1.0, 1)
            else:
                bot_rough = get_roughness(bot_shader)
                roughness_mix.inputs["Color2"].default_value = (bot_rough, bot_rough, bot_rough, 1)

        prev_roughness_mix = roughness_mix

        # -----------------------------------------------------
        # TRANSMISSION Mix (optional)
        top_has_trans = top_shader and ((top_shader.bl_idname == 'ShaderNodeBsdfGlass') or
                                         (top_shader.bl_idname == 'ShaderNodeBsdfPrincipled' and has_node_input(top_shader, "Transmission Weight")))
        bot_has_trans = bot_shader and ((bot_shader.bl_idname == 'ShaderNodeBsdfGlass') or
                                         (bot_shader.bl_idname == 'ShaderNodeBsdfPrincipled' and has_node_input(bot_shader, "Transmission Weight")))
        if top_has_trans or bot_has_trans:
            transmission_mix = nodes.new(type='ShaderNodeMixRGB')
            transmission_mix.label = f"TransmissionMix_for_{mix_node.name}"
            transmission_mix.blend_type = 'MIX'
            transmission_mix.location = (col_x, row_y['transmission'])

            if mix_node.inputs[0].is_linked:
                fac_src = mix_node.inputs[0].links[0].from_socket
                node_tree.links.new(fac_src, transmission_mix.inputs["Fac"])
            else:
                transmission_mix.inputs["Fac"].default_value = mix_node.inputs[0].default_value

            if prev_transmission_mix:
                node_tree.links.new(get_output_socket(prev_transmission_mix, "Color"),
                                    transmission_mix.inputs["Color1"])
            else:
                transmission_mix.inputs["Color1"].default_value = (0, 0, 0, 1)

            if (top_shader and top_shader.bl_idname == 'ShaderNodeBsdfGlass') or (bot_shader and bot_shader.bl_idname == 'ShaderNodeBsdfGlass'):
                transmission_mix.inputs["Color2"].default_value = (1, 1, 1, 1)
            else:
                if top_shader and top_shader.bl_idname == 'ShaderNodeBsdfPrincipled' and has_node_input(top_shader, "Transmission Weight"):
                    sock = get_node_input_by_name(top_shader, "Transmission Weight")
                    if sock and sock.is_linked:
                        node_tree.links.new(sock.links[0].from_socket, transmission_mix.inputs["Color2"])
                    elif sock:
                        transmission_mix.inputs["Color2"].default_value = ensure_color4(sock.default_value)
                elif bot_shader and bot_shader.bl_idname == 'ShaderNodeBsdfPrincipled' and has_node_input(bot_shader, "Transmission Weight"):
                    sock = get_node_input_by_name(bot_shader, "Transmission Weight")
                    if sock and sock.is_linked:
                        node_tree.links.new(sock.links[0].from_socket, transmission_mix.inputs["Color2"])
                    elif sock:
                        transmission_mix.inputs["Color2"].default_value = ensure_color4(sock.default_value)
            prev_transmission_mix = transmission_mix

        # -----------------------------------------------------
        # ALWAYS create an Emission Color Mix node for every mix shader.
        emission_color_mix = nodes.new(type='ShaderNodeMixRGB')
        emission_color_mix.label = f"EmissionColorMix_for_{mix_node.name}"
        emission_color_mix.blend_type = 'MIX'
        emission_color_mix.location = (col_x, row_y['emission_color'])

        # Set the mix factor from the mix shader node.
        if mix_node.inputs[0].is_linked:
            fac_src = mix_node.inputs[0].links[0].from_socket
            node_tree.links.new(fac_src, emission_color_mix.inputs["Fac"])
        else:
            emission_color_mix.inputs["Fac"].default_value = mix_node.inputs[0].default_value

        # Input Color1: Use previous chain if available; otherwise, try the top shader or default to black.
        if prev_emission_color_mix:
            node_tree.links.new(get_output_socket(prev_emission_color_mix, "Color"),
                                emission_color_mix.inputs["Color1"])
        else:
            if top_shader and has_emission(top_shader):
                em_color = get_emission_color_default(top_shader)
            else:
                em_color = (0, 0, 0, 1)  # Default to no emission (black)
            if hasattr(em_color, "is_linked") and em_color.is_linked:
                node_tree.links.new(em_color, emission_color_mix.inputs["Color1"])
            else:
                emission_color_mix.inputs["Color1"].default_value = em_color

        # Input Color2: Use the bottom shader's emission if available; otherwise, default to black.
        if bot_shader and has_emission(bot_shader):
            em_color = get_emission_color_default(bot_shader)
            if hasattr(em_color, "is_linked") and em_color.is_linked:
                node_tree.links.new(em_color, emission_color_mix.inputs["Color2"])
            else:
                emission_color_mix.inputs["Color2"].default_value = em_color
        else:
            emission_color_mix.inputs["Color2"].default_value = (0, 0, 0, 1)

        prev_emission_color_mix = emission_color_mix

        # -----------------------------------------------------
        # ALWAYS create an Emission Strength Mix node for every mix shader.
        emission_strength_mix = nodes.new(type='ShaderNodeMix')
        emission_strength_mix.label = f"EmissionStrengthMix_for_{mix_node.name}"
        emission_strength_mix.data_type = 'FLOAT'
        emission_strength_mix.location = (col_x, row_y['emission_strength'])

        # Set the mix factor from the mix shader node.
        if mix_node.inputs[0].is_linked:
            fac_src = mix_node.inputs[0].links[0].from_socket
            node_tree.links.new(fac_src, emission_strength_mix.inputs["Factor"])
        else:
            emission_strength_mix.inputs["Factor"].default_value = mix_node.inputs[0].default_value

        # Input A: Use the previous emission chain if available; otherwise, try the top shader.
        if prev_emission_strength_mix:
            node_tree.links.new(get_output_socket(prev_emission_strength_mix, "Result"),
                                emission_strength_mix.inputs["A"])
        else:
            if top_shader and has_emission_strength(top_shader):
                es_val = get_emission_strength_default(top_shader)
                if hasattr(es_val, "is_linked") and es_val.is_linked:
                    node_tree.links.new(es_val, emission_strength_mix.inputs["A"])
                else:
                    es_val = ensure_color4(es_val)
                    emission_strength_mix.inputs["A"].default_value = es_val[0]
            else:
                # If top shader does not provide emission strength, default to 0.
                emission_strength_mix.inputs["A"].default_value = 0.0

        # Input B: Use bottom shader emission strength if available; otherwise, default to 0.
        if bot_shader and has_emission_strength(bot_shader):
            es_val = get_emission_strength_default(bot_shader)
            if hasattr(es_val, "is_linked") and es_val.is_linked:
                node_tree.links.new(es_val, emission_strength_mix.inputs["B"])
            else:
                es_val = ensure_color4(es_val)
                emission_strength_mix.inputs["B"].default_value = es_val[0]
        else:
            emission_strength_mix.inputs["B"].default_value = 0.0

        prev_emission_strength_mix = emission_strength_mix

        # Extract a candidate maximum from the current emission strength mix node.
        local_A = extract_max_from_socket(emission_strength_mix.inputs["A"])
        local_B = extract_max_from_socket(emission_strength_mix.inputs["B"])
        local_max = max(local_A, local_B)
        max_emission_strength = max(max_emission_strength, local_max)

        # -----------------------------------------------------
        # ALPHA Mix (Principled)
        is_top_principled = top_shader and top_shader.bl_idname == 'ShaderNodeBsdfPrincipled'
        is_bot_principled = bot_shader and bot_shader.bl_idname == 'ShaderNodeBsdfPrincipled'
        if is_top_principled or is_bot_principled:
            alpha_mix = nodes.new(type='ShaderNodeMixRGB')
            alpha_mix.label = f"AlphaMix_Principled_for_{mix_node.name}"
            alpha_mix.blend_type = 'MIX'
            alpha_mix.location = (col_x, row_y['principled_alpha'])

            if mix_node.inputs[0].is_linked:
                fac_src = mix_node.inputs[0].links[0].from_socket
                node_tree.links.new(fac_src, alpha_mix.inputs["Fac"])
            else:
                alpha_mix.inputs["Fac"].default_value = mix_node.inputs[0].default_value

            if prev_alpha_mix:
                node_tree.links.new(get_output_socket(prev_alpha_mix, "Color"), alpha_mix.inputs["Color1"])
            else:
                alpha_mix.inputs["Color1"].default_value = (1, 1, 1, 1)

            if is_top_principled:
                alpha_source = get_alpha_link(top_shader)
                if alpha_source:
                    node_tree.links.new(alpha_source, alpha_mix.inputs["Color2"])
                else:
                    alpha_mix.inputs["Color2"].default_value = get_alpha_default(top_shader)
            else:
                alpha_source = get_alpha_link(bot_shader)
                if alpha_source:
                    node_tree.links.new(alpha_source, alpha_mix.inputs["Color2"])
                else:
                    alpha_mix.inputs["Color2"].default_value = get_alpha_default(bot_shader)

            prev_alpha_mix = alpha_mix

        # -----------------------------------------------------
        # -----------------------------------------------------
        # METALLIC Mix with Glossy BSDF Handling
        is_top_glossy = top_shader and (top_shader.bl_idname in {'ShaderNodeBsdfGlossy', 'ShaderNodeBsdfAnisotropic'})
        is_bot_glossy = bot_shader and (bot_shader.bl_idname in {'ShaderNodeBsdfGlossy', 'ShaderNodeBsdfAnisotropic'} )
        # Consider a shader metallic if it is Principled, Metallic, or Glossy
        is_top_metal = top_shader and (top_shader.bl_idname in {'ShaderNodeBsdfMetallic', 'ShaderNodeBsdfPrincipled'} or is_top_glossy)
        is_bot_metal = bot_shader and (bot_shader.bl_idname in {'ShaderNodeBsdfMetallic', 'ShaderNodeBsdfPrincipled'} or is_bot_glossy)

        if is_top_metal or is_bot_metal:
            metallic_mix = nodes.new(type='ShaderNodeMixRGB')
            metallic_mix.label = f"MetallicMix_for_{mix_node.name}"
            metallic_mix.blend_type = 'MIX'
            metallic_mix.location = (col_x, row_y['metallic'])

            # Factor from the Mix Shader
            if mix_node.inputs[0].is_linked:
                fac_src = mix_node.inputs[0].links[0].from_socket
                node_tree.links.new(fac_src, metallic_mix.inputs["Fac"])
            else:
                metallic_mix.inputs["Fac"].default_value = mix_node.inputs[0].default_value

            # -------------------------------
            # COLOR1 comes from the PREVIOUS Metallic Mix (if it exists)
            # Otherwise, fall back to the "top" shader's metallic or 0/1.

            if prev_metallic_mix:
                # Chain from the previous metallic mix node
                node_tree.links.new(
                    prev_metallic_mix.outputs["Color"],  # or "Result" if using ShaderNodeMix, etc.
                    metallic_mix.inputs["Color1"]
                )
            else:
                # This is the first Metallic Mix in the chain:
                # Decide if top_shader is glossy => 1, metallic => link, or else => 0
                if top_shader:
                    if is_top_glossy:
                        metallic_mix.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
                    elif top_shader.bl_idname in {'ShaderNodeBsdfMetallic','ShaderNodeBsdfPrincipled'}:
                        if is_metallic_linked(top_shader):
                            node_tree.links.new(get_metallic_link(top_shader),
                                                metallic_mix.inputs["Color1"])
                        else:
                            metallic_mix.inputs["Color1"].default_value = get_metallic_default(top_shader)
                    else:
                        metallic_mix.inputs["Color1"].default_value = (0.0, 0.0, 0.0, 1.0)
                else:
                    # No top shader => 0
                    metallic_mix.inputs["Color1"].default_value = (0.0, 0.0, 0.0, 1.0)

            # -------------------------------
            # COLOR2 always comes from the "bottom" shader’s metallic or 0/1
            # (or you can invert this logic if you prefer the bottom to chain.)

            if bot_shader:
                if is_bot_glossy:
                    metallic_mix.inputs["Color2"].default_value = (1.0, 1.0, 1.0, 1.0)
                elif bot_shader.bl_idname in {'ShaderNodeBsdfMetallic','ShaderNodeBsdfPrincipled'}:
                    if is_metallic_linked(bot_shader):
                        node_tree.links.new(get_metallic_link(bot_shader),
                                            metallic_mix.inputs["Color2"])
                    else:
                        metallic_mix.inputs["Color2"].default_value = get_metallic_default(bot_shader)
                else:
                    metallic_mix.inputs["Color2"].default_value = (0.0, 0.0, 0.0, 1.0)
            else:
                metallic_mix.inputs["Color2"].default_value = (0.0, 0.0, 0.0, 1.0)

            # --------------------------------
            # Update the chain reference:
            prev_metallic_mix = metallic_mix
            
            # -----------------------------------------------------
        # NORMAL Mix (using ShaderNodeMix in VECTOR mode)
        if top_shader and bot_shader:
            top_has_normal = ("Normal" in top_shader.inputs)
            bot_has_normal = ("Normal" in bot_shader.inputs)
            if top_has_normal or bot_has_normal:
                normal_mix = nodes.new(type='ShaderNodeMix')
                normal_mix.label = f"NormalMix_for_{mix_node.name}"
                normal_mix.data_type = 'VECTOR'
                normal_mix.location = (col_x, row_y['normal'])
                if mix_node.inputs[0].is_linked:
                    fac_src = mix_node.inputs[0].links[0].from_socket
                    node_tree.links.new(fac_src, normal_mix.inputs["Factor"])
                else:
                    normal_mix.inputs["Factor"].default_value = mix_node.inputs[0].default_value
                if prev_normal_mix:
                    node_tree.links.new(prev_normal_mix.outputs["Result"], normal_mix.inputs["A"])
                else:
                    top_sock = top_shader.inputs.get("Normal")
                    if top_sock:
                        if top_sock.is_linked:
                            node_tree.links.new(top_sock.links[0].from_socket, normal_mix.inputs["A"])
                        else:
                            normal_mix.inputs["A"].default_value = ensure_vector3(top_sock.default_value)
                    else:
                        normal_mix.inputs["A"].default_value = (0.0, 0.0, 0.0)
                bot_sock = bot_shader.inputs.get("Normal")
                if bot_sock:
                    if bot_sock.is_linked:
                        node_tree.links.new(bot_sock.links[0].from_socket, normal_mix.inputs["B"])
                    else:
                        normal_mix.inputs["B"].default_value = ensure_vector3(bot_sock.default_value)
                else:
                    normal_mix.inputs["B"].default_value = (0.0, 0.0, 0.0)
                prev_normal_mix = normal_mix

        prev_color_mix = color_mix
        prev_roughness_mix = roughness_mix

        # ALPHA Mix for Transparent BSDF Handling
        is_top_transparent = top_shader and (top_shader.bl_idname == 'ShaderNodeBsdfTransparent')
        is_bot_transparent = bot_shader and (bot_shader.bl_idname == 'ShaderNodeBsdfTransparent')
        if is_top_transparent or is_bot_transparent:
            alpha_mix = nodes.new(type='ShaderNodeMixRGB')
            alpha_mix.label = f"AlphaMix_Transparent_for_{mix_node.name}"
            alpha_mix.blend_type = 'MIX'
            alpha_mix.location = (col_x, row_y['principled_alpha'])
            
            # Set the mix factor from the mix shader node
            if mix_node.inputs[0].is_linked:
                fac_src = mix_node.inputs[0].links[0].from_socket
                node_tree.links.new(fac_src, alpha_mix.inputs["Fac"])
            else:
                alpha_mix.inputs["Fac"].default_value = mix_node.inputs[0].default_value
            
            # For the transparent branch, always force black (0,0,0,0)
            # For the non-transparent branch, chain the previous alpha mix if available, else default to white (1,1,1,1)
            if is_top_transparent:
                # For the top shader input, force black
                alpha_mix.inputs["Color1"].default_value = (0.0, 0.0, 0.0, 0.0)
                # For the other branch (bottom), use previous alpha chain or white
                if prev_alpha_mix:
                    node_tree.links.new(get_output_socket(prev_alpha_mix, "Color"), alpha_mix.inputs["Color2"])
                else:
                    alpha_mix.inputs["Color2"].default_value = (1.0, 1.0, 1.0, 1.0)
            elif is_bot_transparent:
                # For the bottom shader input, force black
                alpha_mix.inputs["Color2"].default_value = (0.0, 0.0, 0.0, 0.0)
                # For the other branch (top), use previous alpha chain or white
                if prev_alpha_mix:
                    node_tree.links.new(get_output_socket(prev_alpha_mix, "Color"), alpha_mix.inputs["Color1"])
                else:
                    alpha_mix.inputs["Color1"].default_value = (1.0, 1.0, 1.0, 1.0)
            
            prev_alpha_mix = alpha_mix

    # ---------------------------------------------------------
    # 6) Create Principled BSDF and final connections.
    final_col_x = start_x + len(chain_order) * x_spacing + 400
    principled_bsdf = nodes.new(type='ShaderNodeBsdfPrincipled')
    principled_bsdf.label = "Principled BSDF"
    principled_bsdf.location = (final_col_x, -150)

    print("Principled BSDF inputs:", [socket.name for socket in principled_bsdf.inputs])

    # Base Color
    if prev_color_mix:
        node_tree.links.new(get_output_socket(prev_color_mix), principled_bsdf.inputs["Base Color"])
    # Roughness
    if prev_roughness_mix:
        node_tree.links.new(get_output_socket(prev_roughness_mix), principled_bsdf.inputs["Roughness"])
    # Transmission Weight (if exists)
    if prev_transmission_mix and "Transmission Weight" in principled_bsdf.inputs:
        node_tree.links.new(get_output_socket(prev_transmission_mix), principled_bsdf.inputs["Transmission Weight"])
    # Alpha
    if prev_alpha_mix:
        node_tree.links.new(get_output_socket(prev_alpha_mix), principled_bsdf.inputs["Alpha"])
    # Metallic
    if prev_metallic_mix:
        node_tree.links.new(get_output_socket(prev_metallic_mix), principled_bsdf.inputs["Metallic"])
    # Normal
    if prev_normal_mix:
        node_tree.links.new(prev_normal_mix.outputs["Result"], principled_bsdf.inputs["Normal"])
    # Emission Color (if exists)
    if prev_emission_color_mix and "Emission Color" in principled_bsdf.inputs:
        node_tree.links.new(get_output_socket(prev_emission_color_mix), principled_bsdf.inputs["Emission Color"])
    # Emission Strength (if exists)
    if prev_emission_strength_mix and "Emission Strength" in principled_bsdf.inputs:
        # Create Map Range node to remap the emission strength from 0-max_emission_strength to 0-1.
        map_range = nodes.new(type='ShaderNodeMapRange')
        map_range.label = "EmissionStrength_MapRange"
        map_range.location = (final_col_x - 200, row_y['emission_strength'])  # adjust as needed
        
        # Connect final emission mix to Map Range node.
        node_tree.links.new(get_output_socket(prev_emission_strength_mix), map_range.inputs["Value"])
        
        # Set the map range parameters:
        map_range.inputs["From Min"].default_value = 0.0
        map_range.inputs["From Max"].default_value = max_emission_strength if max_emission_strength > 0.0 else 1.0
        map_range.inputs["To Min"].default_value = 0.0
        map_range.inputs["To Max"].default_value = 1.0
        
        # Finally, connect the Map Range output to the Principled BSDF’s Emission Strength.
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
    """Recursively traverse a socket’s input chain and return a candidate maximum value.
    If a multiply node is found, it extracts the constant values if present.
    """
    if visited is None:
        visited = set()
    if socket is None or socket in visited:
        return 0.0
    visited.add(socket)
    
    # If not linked, try to return the socket’s default (if it’s a number).
    try:
        if not socket.is_linked:
            return float(socket.default_value)
    except Exception:
        return 0.0
    
    # Otherwise, follow the link.
    link = socket.links[0]
    from_socket = link.from_socket
    from_node = link.from_node
    
    # If this is a multiply node, check its inputs.
    if from_node.bl_idname == 'ShaderNodeMath' and from_node.operation == 'MULTIPLY':
        candidate_A = 0.0
        candidate_B = 0.0
        # For each input, if it isn’t linked, use its default value,
        # otherwise recurse.
        if not from_node.inputs[0].is_linked:
            candidate_A = float(from_node.inputs[0].default_value)
        else:
            candidate_A = extract_max_from_socket(from_node.inputs[0], visited)
        if not from_node.inputs[1].is_linked:
            candidate_B = float(from_node.inputs[1].default_value)
        else:
            candidate_B = extract_max_from_socket(from_node.inputs[1], visited)
        candidate = max(candidate_A, candidate_B)
        # Also check further upstream from the multiply node’s output.
        upstream_candidate = extract_max_from_socket(from_socket, visited)
        return max(candidate, upstream_candidate)
    else:
        # For any other node, just continue traversing.
        return extract_max_from_socket(from_socket, visited)

def create_node_group(custom_name="Custom_NodeGroup"):
    context = bpy.context
    obj = context.object
    
    if not obj or not obj.active_material or not obj.active_material.node_tree:
        print("No active material with a node tree found!")
        return

    node_tree = obj.active_material.node_tree
    nodes = node_tree.nodes

    # Find the Material Output node
    material_output = None
    for node in nodes:
        if node.type == 'OUTPUT_MATERIAL':
            material_output = node
            break

    # Find an open Node Editor area
    override = None
    for area in context.screen.areas:
        if area.type == 'NODE_EDITOR':
            for space in area.spaces:
                if space.type == 'NODE_EDITOR':
                    override = {'window': context.window, 'screen': context.screen, 'area': area, 'region': area.regions[-1]}
                    break
            if override:
                break

    # If no Node Editor is open, print a warning
    if not override:
        print("Please open a Node Editor before running this script.")
        return

    # Execute the node grouping operation in the correct context
    with context.temp_override(**override):
        bpy.ops.node.group_make()

    # Get the active node (which should now be the new node group)
    new_group_node = node_tree.nodes.active
    if new_group_node and new_group_node.type == 'GROUP':
        new_group_node.node_tree.name = custom_name  # Rename the internal node group
        new_group_node.label = custom_name  # Change label for clarity

        # ---- MOVE NODE GROUP UNDER MATERIAL OUTPUT ----
        if material_output:
            new_group_node.location = (
                material_output.location.x,  # Same X position
                material_output.location.y - 200  # Move it below (adjust the Y offset as needed)
            )

    print(f"Node group created: {custom_name}")

    # ---- EXIT NODE GROUP ----
    with context.temp_override(**override):
        if bpy.ops.node.tree_path_parent.poll():  # Check if we are inside a node group
            bpy.ops.node.tree_path_parent()  # Exit the node group

    print("Exited node group.")

# Run the function to create a node group with a custom name
#create_mix_chains_and_principled()
#create_node_group("PrincipledBSDF Setup")

