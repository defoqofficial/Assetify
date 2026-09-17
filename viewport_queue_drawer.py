# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
# ##### END GPL LICENSE BLOCK #####

"""
Assetify Viewport GPU Queue Drawer
Provides a collapsable 2D GPU drawer docked on the left edge of the N-Panel in the 3D Viewport.
Features:
- Docked edge handle tab with live asset count badge.
- Slide-out 2nd column with translucent dark theme.
- Interactive asset cards: checkmark toggle, selection focus in 3D scene, status pills (BAKED / PENDING).
- Batch action buttons: All, None, Invert, Process Selected, Clear Queue.
- Smooth mouse wheel scrolling.
- Seamless event pass-through for 3D viewport navigation when mouse is outside the drawer.
"""

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader
from bpy.types import Operator


# Global singleton instance of the drawer operator state
_drawer_state = {
    "is_active": False,
    "is_open": False,
    "handle": None,
    "scroll_offset": 0,
    "hover_id": None,
    "drawer_width": 310,
    "handle_width": 26,
    "handle_height": 90,
    "item_height": 34,
}


def draw_rect_2d(x1, y1, x2, y2, color):
    """Draws a filled 2D rectangle with alpha blending."""
    vertices = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    indices = [(0, 1, 2), (0, 2, 3)]
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'TRIS', {"pos": vertices}, indices=indices)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)


def draw_rect_outline_2d(x1, y1, x2, y2, color, thickness=1.0):
    """Draws a 1px outline for a 2D rectangle."""
    # Bottom, Right, Top, Left
    draw_rect_2d(x1, y1, x2, y1 + thickness, color)
    draw_rect_2d(x2 - thickness, y1, x2, y2, color)
    draw_rect_2d(x1, y2 - thickness, x2, y2, color)
    draw_rect_2d(x1, y1, x1 + thickness, y2, color)


def draw_text_2d(text, x, y, size=12, color=(0.9, 0.9, 0.9, 1.0)):
    """Draws text using blf."""
    font_id = 0
    blf.size(font_id, size)
    blf.color(font_id, color[0], color[1], color[2], color[3])
    blf.position(font_id, x, y, 0)
    blf.draw(font_id, str(text))


def get_text_width(text, size=12):
    """Returns text width in pixels."""
    font_id = 0
    blf.size(font_id, size)
    dims = blf.dimensions(font_id, str(text))
    return dims[0]


def truncate_text(text, max_width, size=12):
    """Truncates text with ellipsis if it exceeds max_width."""
    if get_text_width(text, size) <= max_width:
        return text
    ell = "..."
    for i in range(len(text) - 1, 0, -1):
        sub = text[:i] + ell
        if get_text_width(sub, size) <= max_width:
            return sub
    return text


def get_queue_items(context):
    """Retrieves assets from Assetify settings."""
    settings = getattr(context.scene, "assetify_bake_settings", None)
    if not settings:
        return [], 0, 0
    
    if settings.asset_mode == 'ASSET':
        items = list(settings.baked_assets)
        active_count = sum(1 for a in items if a.include_in_send)
    else:
        items = list(settings.baked_collections)
        active_count = sum(1 for c in items if c.include_in_send)
    
    return items, len(items), active_count


def draw_viewport_drawer(self, context):
    """POST_PIXEL draw callback for SpaceView3D."""
    if not _drawer_state["is_active"]:
        return

    region = context.region
    if not region:
        return

    W = region.width
    H = region.height
    is_open = _drawer_state["is_open"]
    d_w = _drawer_state["drawer_width"]
    h_w = _drawer_state["handle_width"]
    h_h = _drawer_state["handle_height"]
    hover_id = _drawer_state["hover_id"]

    gpu.state.blend_set('ALPHA')

    items, total_count, active_count = get_queue_items(context)

    # -------------------------------------------------------------
    # 1. Calculate Handle Position
    # -------------------------------------------------------------
    # Docked to the right edge of WINDOW region (left edge of N-Panel)
    if is_open:
        # Handle sits on the left edge of the open drawer panel
        h_x1 = W - d_w - h_w
        h_x2 = W - d_w
    else:
        # Handle sits flush against the N-Panel
        h_x1 = W - h_w
        h_x2 = W
    
    h_y1 = int((H / 2) - (h_h / 2))
    h_y2 = h_y1 + h_h

    _drawer_state["handle_rect"] = (h_x1, h_y1, h_x2, h_y2)

    # Draw Handle Background
    is_handle_hovered = (hover_id == "handle")
    h_bg = (0.20, 0.23, 0.28, 0.95) if is_handle_hovered else (0.13, 0.14, 0.17, 0.92)
    h_border = (0.38, 0.52, 0.72, 1.0) if is_handle_hovered else (0.24, 0.26, 0.30, 0.9)
    draw_rect_2d(h_x1, h_y1, h_x2, h_y2, h_bg)
    draw_rect_outline_2d(h_x1, h_y1, h_x2, h_y2, h_border, thickness=1.0)

    # Draw Handle Arrow / Icon / Count
    arrow = "▶" if is_open else "◀"
    draw_text_2d(arrow, h_x1 + 8, h_y2 - 20, size=13, color=(0.95, 0.95, 0.95, 1.0))
    
    # Draw vertical letters "Q U E U E" or count badge
    if not is_open:
        draw_text_2d("Q", h_x1 + 8, h_y2 - 38, size=10, color=(0.8, 0.8, 0.8, 0.9))
        draw_text_2d("U", h_x1 + 8, h_y2 - 50, size=10, color=(0.8, 0.8, 0.8, 0.9))
        draw_text_2d("E", h_x1 + 8, h_y2 - 62, size=10, color=(0.8, 0.8, 0.8, 0.9))
        draw_text_2d("U", h_x1 + 8, h_y2 - 74, size=10, color=(0.8, 0.8, 0.8, 0.9))
        draw_text_2d("E", h_x1 + 8, h_y2 - 86, size=10, color=(0.8, 0.8, 0.8, 0.9))
        # Badge
        badge_text = str(total_count)
        bw = get_text_width(badge_text, 10)
        draw_rect_2d(h_x1 + 3, h_y1 + 4, h_x2 - 3, h_y1 + 18, (0.22, 0.45, 0.75, 0.9))
        draw_text_2d(badge_text, h_x1 + (h_w - bw) / 2, h_y1 + 7, size=10, color=(1, 1, 1, 1))

    # -------------------------------------------------------------
    # 2. Draw Expanded Drawer Panel (if open)
    # -------------------------------------------------------------
    if not is_open:
        _drawer_state["interactive_elements"] = [("handle", (h_x1, h_y1, h_x2, h_y2))]
        return

    d_x1 = W - d_w
    d_x2 = W
    d_y1 = 30
    d_y2 = H - 35

    _drawer_state["drawer_rect"] = (d_x1, d_y1, d_x2, d_y2)

    # Panel Body Shadow & Background
    draw_rect_2d(d_x1 - 4, d_y1 - 2, d_x1, d_y2 + 2, (0.0, 0.0, 0.0, 0.25))  # Left drop shadow
    draw_rect_2d(d_x1, d_y1, d_x2, d_y2, (0.11, 0.12, 0.14, 0.96))
    draw_rect_outline_2d(d_x1, d_y1, d_x2, d_y2, (0.24, 0.26, 0.30, 0.95), thickness=1.0)

    interactive = [("handle", (h_x1, h_y1, h_x2, h_y2))]

    # -------------------------------------------------------------
    # Top Header Bar (Height: 38px)
    # -------------------------------------------------------------
    head_y1 = d_y2 - 38
    head_y2 = d_y2
    draw_rect_2d(d_x1, head_y1, d_x2, head_y2, (0.15, 0.17, 0.20, 1.0))
    draw_rect_outline_2d(d_x1, head_y1, d_x2, head_y2, (0.22, 0.24, 0.28, 1.0), thickness=1.0)

    # Title & Badge
    draw_text_2d("ASSET QUEUE", d_x1 + 12, head_y1 + 12, size=13, color=(0.95, 0.95, 0.95, 1.0))
    badge_str = f"{active_count}/{total_count} Active"
    draw_text_2d(badge_str, d_x1 + 115, head_y1 + 12, size=11, color=(0.4, 0.75, 0.95, 1.0))

    # Close Button [ ✕ ]
    btn_close_x1 = d_x2 - 28
    btn_close_x2 = d_x2 - 8
    btn_close_y1 = head_y1 + 8
    btn_close_y2 = head_y1 + 28
    is_close_hover = (hover_id == "btn_close")
    c_bg = (0.75, 0.22, 0.22, 0.85) if is_close_hover else (0.22, 0.24, 0.28, 0.8)
    draw_rect_2d(btn_close_x1, btn_close_y1, btn_close_x2, btn_close_y2, c_bg)
    draw_text_2d("✕", btn_close_x1 + 5, btn_close_y1 + 4, size=12, color=(1, 1, 1, 1))
    interactive.append(("btn_close", (btn_close_x1, btn_close_y1, btn_close_x2, btn_close_y2)))

    # -------------------------------------------------------------
    # Intake Quick Bar (Height: 32px)
    # -------------------------------------------------------------
    intake_y1 = head_y1 - 34
    intake_y2 = head_y1 - 4
    
    # Process Viewport Selection Button
    b_sel_x1 = d_x1 + 10
    b_sel_x2 = d_x1 + 145
    is_sel_hover = (hover_id == "btn_proc_sel")
    b_sel_bg = (0.26, 0.38, 0.54, 0.9) if is_sel_hover else (0.18, 0.22, 0.28, 0.85)
    draw_rect_2d(b_sel_x1, intake_y1, b_sel_x2, intake_y2, b_sel_bg)
    draw_rect_outline_2d(b_sel_x1, intake_y1, b_sel_x2, intake_y2, (0.35, 0.45, 0.6, 0.8))
    draw_text_2d("+ Proc Selected", b_sel_x1 + 8, intake_y1 + 8, size=11, color=(0.95, 0.95, 0.95, 1.0))
    interactive.append(("btn_proc_sel", (b_sel_x1, intake_y1, b_sel_x2, intake_y2)))

    # Process Active Collection Button
    b_col_x1 = d_x1 + 152
    b_col_x2 = d_x2 - 10
    is_col_hover = (hover_id == "btn_proc_col")
    b_col_bg = (0.26, 0.38, 0.54, 0.9) if is_col_hover else (0.18, 0.22, 0.28, 0.85)
    draw_rect_2d(b_col_x1, intake_y1, b_col_x2, intake_y2, b_col_bg)
    draw_rect_outline_2d(b_col_x1, intake_y1, b_col_x2, intake_y2, (0.35, 0.45, 0.6, 0.8))
    draw_text_2d("+ Proc Collection", b_col_x1 + 8, intake_y1 + 8, size=11, color=(0.95, 0.95, 0.95, 1.0))
    interactive.append(("btn_proc_col", (b_col_x1, intake_y1, b_col_x2, intake_y2)))

    # -------------------------------------------------------------
    # Batch Selection Controls Bar (Height: 26px)
    # -------------------------------------------------------------
    sel_bar_y1 = intake_y1 - 30
    sel_bar_y2 = intake_y1 - 6

    # All / None / Invert / Clear
    btn_w = 46
    gap = 6
    bx = d_x1 + 10
    
    for label, op_name in [("All", "btn_all"), ("None", "btn_none"), ("Invert", "btn_invert"), ("Clear", "btn_clear")]:
        b_x1 = bx
        b_x2 = bx + btn_w
        is_b_hover = (hover_id == op_name)
        bg_col = (0.30, 0.33, 0.38, 0.9) if is_b_hover else (0.16, 0.18, 0.22, 0.85)
        draw_rect_2d(b_x1, sel_bar_y1, b_x2, sel_bar_y2, bg_col)
        draw_rect_outline_2d(b_x1, sel_bar_y1, b_x2, sel_bar_y2, (0.28, 0.30, 0.34, 0.8))
        tw = get_text_width(label, 10)
        draw_text_2d(label, b_x1 + (btn_w - tw) / 2, sel_bar_y1 + 6, size=10, color=(0.9, 0.9, 0.9, 1.0))
        interactive.append((op_name, (b_x1, sel_bar_y1, b_x2, sel_bar_y2)))
        bx += btn_w + gap

    # Separator Line
    sep_y = sel_bar_y1 - 6
    draw_rect_2d(d_x1 + 10, sep_y, d_x2 - 10, sep_y + 1, (0.22, 0.24, 0.28, 0.9))

    # -------------------------------------------------------------
    # Bottom Footer Bar (Height: 38px)
    # -------------------------------------------------------------
    foot_y1 = d_y1
    foot_y2 = d_y1 + 38
    draw_rect_2d(d_x1, foot_y1, d_x2, foot_y2, (0.13, 0.14, 0.17, 1.0))
    draw_rect_outline_2d(d_x1, foot_y1, d_x2, foot_y2, (0.22, 0.24, 0.28, 1.0), thickness=1.0)

    # Proceed to Step 2 Button
    f_btn_x1 = d_x1 + 10
    f_btn_x2 = d_x2 - 10
    f_btn_y1 = foot_y1 + 6
    f_btn_y2 = foot_y2 - 6
    is_f_hover = (hover_id == "btn_go_bake")
    f_bg = (0.22, 0.45, 0.75, 0.95) if is_f_hover else (0.16, 0.35, 0.60, 0.9)
    draw_rect_2d(f_btn_x1, f_btn_y1, f_btn_x2, f_btn_y2, f_bg)
    draw_rect_outline_2d(f_btn_x1, f_btn_y1, f_btn_x2, f_btn_y2, (0.35, 0.58, 0.88, 1.0))
    draw_text_2d("Proceed to Step 2: Bake →", f_btn_x1 + 45, f_btn_y1 + 7, size=11, color=(1, 1, 1, 1))
    interactive.append(("btn_go_bake", (f_btn_x1, f_btn_y1, f_btn_x2, f_btn_y2)))

    # -------------------------------------------------------------
    # Middle Scrollable Asset List Items
    # -------------------------------------------------------------
    list_y1 = foot_y2 + 6
    list_y2 = sep_y - 6
    visible_height = list_y2 - list_y1
    item_h = _drawer_state["item_height"]

    if total_count == 0:
        draw_text_2d("No assets in queue yet.", d_x1 + 60, list_y2 - 50, size=12, color=(0.6, 0.6, 0.6, 1.0))
        draw_text_2d("Click '+ Proc Selected' above to add.", d_x1 + 35, list_y2 - 75, size=11, color=(0.45, 0.45, 0.45, 1.0))
    else:
        # Clamp scroll offset
        max_scroll = max(0, (total_count * item_h) - visible_height)
        _drawer_state["scroll_offset"] = max(0, min(_drawer_state["scroll_offset"], max_scroll))
        scroll_y = _drawer_state["scroll_offset"]

        curr_y = list_y2 - item_h + scroll_y

        active_obj_name = context.active_object.name if context.active_object else ""

        for idx, item in enumerate(items):
            # Only draw items visible in the window bounds
            if curr_y + item_h >= list_y1 and curr_y <= list_y2:
                # Item Rect
                i_x1 = d_x1 + 10
                i_x2 = d_x2 - 10
                i_y1 = max(list_y1, curr_y)
                i_y2 = min(list_y2, curr_y + item_h)

                item_id = f"item_{idx}"
                chk_id = f"chk_{idx}"
                is_item_hover = (hover_id == item_id)
                is_chk_hover = (hover_id == chk_id)

                is_active_obj = (item.name == active_obj_name)

                # Background
                if is_active_obj:
                    row_bg = (0.24, 0.36, 0.52, 0.85)
                elif is_item_hover:
                    row_bg = (0.18, 0.21, 0.26, 0.9)
                else:
                    row_bg = (0.13, 0.14, 0.17, 0.75) if idx % 2 == 0 else (0.11, 0.12, 0.15, 0.75)

                draw_rect_2d(i_x1, i_y1, i_x2, i_y2, row_bg)
                if is_active_obj:
                    draw_rect_outline_2d(i_x1, i_y1, i_x2, i_y2, (0.4, 0.65, 0.95, 0.95), thickness=1.0)
                else:
                    draw_rect_outline_2d(i_x1, i_y1, i_x2, i_y2, (0.20, 0.22, 0.25, 0.6))

                # Checkbox [✓] or [ ]
                chk_box_x1 = i_x1 + 6
                chk_box_x2 = chk_box_x1 + 18
                chk_box_y1 = curr_y + 8
                chk_box_y2 = chk_box_y1 + 18

                chk_bg = (0.22, 0.45, 0.75, 1.0) if item.include_in_send else (0.18, 0.20, 0.24, 0.9)
                draw_rect_2d(chk_box_x1, chk_box_y1, chk_box_x2, chk_box_y2, chk_bg)
                chk_border = (0.5, 0.7, 0.95, 1.0) if is_chk_hover else (0.35, 0.38, 0.44, 1.0)
                draw_rect_outline_2d(chk_box_x1, chk_box_y1, chk_box_x2, chk_box_y2, chk_border)

                if item.include_in_send:
                    draw_text_2d("✓", chk_box_x1 + 3, chk_box_y1 + 3, size=11, color=(1, 1, 1, 1))

                # Status Pill (BAKED vs PEND)
                status_w = 48
                status_x1 = i_x2 - status_w - 6
                status_x2 = i_x2 - 6
                status_y1 = curr_y + 8
                status_y2 = status_y1 + 17

                is_baked = getattr(item, "baked", False)
                if is_baked:
                    st_bg = (0.18, 0.48, 0.25, 0.85)
                    st_txt = "BAKED"
                    st_col = (0.85, 1.0, 0.85, 1.0)
                else:
                    st_bg = (0.25, 0.27, 0.32, 0.7)
                    st_txt = "PEND"
                    st_col = (0.7, 0.7, 0.7, 0.9)

                draw_rect_2d(status_x1, status_y1, status_x2, status_y2, st_bg)
                draw_text_2d(st_txt, status_x1 + 6, status_y1 + 3, size=9, color=st_col)

                # Asset Name
                avail_name_w = status_x1 - chk_box_x2 - 14
                disp_name = truncate_text(item.name, avail_name_w, size=11)
                text_col = (1.0, 1.0, 1.0, 1.0) if item.include_in_send else (0.6, 0.6, 0.6, 0.8)
                draw_text_2d(disp_name, chk_box_x2 + 8, curr_y + 10, size=11, color=text_col)

                interactive.append((chk_id, (chk_box_x1, chk_box_y1, chk_box_x2, chk_box_y2)))
                interactive.append((item_id, (chk_box_x2 + 2, curr_y, status_x2, curr_y + item_h)))

            curr_y -= item_h

    _drawer_state["interactive_elements"] = interactive


class ASSETIFY_OT_toggle_viewport_drawer(Operator):
    """Toggle the Assetify Viewport GPU Queue Drawer on the left of the N-Panel"""
    bl_idname = "assetify.toggle_viewport_drawer"
    bl_label = "Toggle Asset Queue Drawer"
    bl_options = {"REGISTER"}

    _timer = None

    def invoke(self, context, event):
        global _drawer_state

        if _drawer_state["is_active"]:
            # If already active, toggle open/close or stop
            if _drawer_state["is_open"]:
                _drawer_state["is_open"] = False
                context.region.tag_redraw()
                return {"FINISHED"}
            else:
                _drawer_state["is_open"] = True
                context.region.tag_redraw()
                return {"FINISHED"}

        # Activate drawer and register draw callback
        _drawer_state["is_active"] = True
        _drawer_state["is_open"] = True
        _drawer_state["handle"] = bpy.types.SpaceView3D.draw_handler_add(
            draw_viewport_drawer, (self, context), "WINDOW", "POST_PIXEL"
        )

        self._timer = context.window_manager.event_timer_add(0.1, window=context.window)
        context.window_manager.modal_handler_add(self)
        context.region.tag_redraw()
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        global _drawer_state

        if not _drawer_state["is_active"]:
            self.clean_exit(context)
            return {"FINISHED"}

        region = context.region
        if not region:
            return {"PASS_THROUGH"}

        mx = event.mouse_region_x
        my = event.mouse_region_y

        is_open = _drawer_state["is_open"]
        handle_rect = _drawer_state.get("handle_rect")
        drawer_rect = _drawer_state.get("drawer_rect")

        in_handle = False
        if handle_rect:
            hx1, hy1, hx2, hy2 = handle_rect
            in_handle = (hx1 <= mx <= hx2 and hy1 <= my <= hy2)

        in_drawer = False
        if is_open and drawer_rect:
            dx1, dy1, dx2, dy2 = drawer_rect
            in_drawer = (dx1 <= mx <= dx2 and dy1 <= my <= dy2)

        # ---------------------------------------------------------
        # If outside drawer & handle -> PASS THROUGH so viewport works!
        # ---------------------------------------------------------
        if not (in_handle or in_drawer):
            if _drawer_state["hover_id"] is not None:
                _drawer_state["hover_id"] = None
                context.region.tag_redraw()
            return {"PASS_THROUGH"}

        # ---------------------------------------------------------
        # Mouse Move: Hit test interactive elements
        # ---------------------------------------------------------
        if event.type == 'MOUSEMOVE':
            hit_id = None
            for elem_id, rect in _drawer_state.get("interactive_elements", []):
                rx1, ry1, rx2, ry2 = rect
                if rx1 <= mx <= rx2 and ry1 <= my <= ry2:
                    hit_id = elem_id
                    break

            if hit_id != _drawer_state["hover_id"]:
                _drawer_state["hover_id"] = hit_id
                context.region.tag_redraw()
            return {"RUNNING_MODAL"}

        # ---------------------------------------------------------
        # Mouse Wheel Scrolling
        # ---------------------------------------------------------
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} and in_drawer:
            step = 30
            if event.type == 'WHEELUPMOUSE':
                _drawer_state["scroll_offset"] = max(0, _drawer_state["scroll_offset"] - step)
            else:
                _drawer_state["scroll_offset"] += step
            context.region.tag_redraw()
            return {"RUNNING_MODAL"}

        # ---------------------------------------------------------
        # Left Mouse Click
        # ---------------------------------------------------------
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            hit_id = _drawer_state["hover_id"]

            if hit_id == "handle":
                _drawer_state["is_open"] = not _drawer_state["is_open"]
                context.region.tag_redraw()
                return {"RUNNING_MODAL"}

            if hit_id == "btn_close":
                _drawer_state["is_open"] = False
                context.region.tag_redraw()
                return {"RUNNING_MODAL"}

            settings = getattr(context.scene, "assetify_bake_settings", None)
            items, total_count, _ = get_queue_items(context)

            if hit_id == "btn_proc_sel":
                try:
                    bpy.ops.object.convert_to_game_ready(source='SELECTED')
                except Exception as e:
                    print(f"[Assetify] Process Selected error: {e}")
                context.region.tag_redraw()
                return {"RUNNING_MODAL"}

            if hit_id == "btn_proc_col":
                try:
                    bpy.ops.object.convert_to_game_ready(source='DROPDOWN')
                except Exception as e:
                    print(f"[Assetify] Process Collection error: {e}")
                context.region.tag_redraw()
                return {"RUNNING_MODAL"}

            if hit_id == "btn_all":
                if settings:
                    bpy.ops.assetify.toggle_select_assets(action='SELECT')
                context.region.tag_redraw()
                return {"RUNNING_MODAL"}

            if hit_id == "btn_none":
                if settings:
                    bpy.ops.assetify.toggle_select_assets(action='DESELECT')
                context.region.tag_redraw()
                return {"RUNNING_MODAL"}

            if hit_id == "btn_invert":
                if settings:
                    bpy.ops.assetify.toggle_select_assets(action='INVERT')
                context.region.tag_redraw()
                return {"RUNNING_MODAL"}

            if hit_id == "btn_clear":
                if settings:
                    if settings.asset_mode == 'ASSET':
                        settings.baked_assets.clear()
                    else:
                        settings.baked_collections.clear()
                context.region.tag_redraw()
                return {"RUNNING_MODAL"}

            if hit_id == "btn_go_bake":
                if settings:
                    settings.current_pipeline_step = '2'
                context.region.tag_redraw()
                return {"RUNNING_MODAL"}

            # Checkbox click: toggle include_in_send
            if hit_id and hit_id.startswith("chk_"):
                idx = int(hit_id.split("_")[1])
                if 0 <= idx < len(items):
                    items[idx].include_in_send = not items[idx].include_in_send
                    context.region.tag_redraw()
                return {"RUNNING_MODAL"}

            # Item row click: focus asset in 3D viewport
            if hit_id and hit_id.startswith("item_"):
                idx = int(hit_id.split("_")[1])
                if 0 <= idx < len(items):
                    asset_name = items[idx].name
                    obj = bpy.data.objects.get(asset_name)
                    if obj:
                        bpy.ops.object.select_all(action='DESELECT')
                        obj.select_set(True)
                        context.view_layer.objects.active = obj
                    if settings:
                        settings.active_baked_asset_index = idx
                    context.region.tag_redraw()
                return {"RUNNING_MODAL"}

            return {"RUNNING_MODAL"}

        # ESC closes drawer
        if event.type == 'ESC' and is_open:
            _drawer_state["is_open"] = False
            context.region.tag_redraw()
            return {"RUNNING_MODAL"}

        return {"PASS_THROUGH"}

    def clean_exit(self, context):
        global _drawer_state
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        if _drawer_state["handle"]:
            bpy.types.SpaceView3D.draw_handler_remove(_drawer_state["handle"], "WINDOW")
            _drawer_state["handle"] = None
        _drawer_state["is_active"] = False
        _drawer_state["is_open"] = False
        if context.region:
            context.region.tag_redraw()


def close_viewport_drawer():
    """Safely shuts down the viewport drawer upon addon unregister."""
    global _drawer_state
    if _drawer_state["handle"]:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(_drawer_state["handle"], "WINDOW")
        except Exception:
            pass
        _drawer_state["handle"] = None
    _drawer_state["is_active"] = False
    _drawer_state["is_open"] = False


classes = (
    ASSETIFY_OT_toggle_viewport_drawer,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    close_viewport_drawer()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
