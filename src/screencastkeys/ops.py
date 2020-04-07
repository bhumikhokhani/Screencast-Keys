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
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

# <pep8 compliant>


import math
import collections
import enum
import re
import string
import time

import blf
import bpy
import bpy.props

from .utils.bl_class_registry import BlClassRegistry
from .utils import compatibility as compat

if compat.check_version(2, 80, 0) >= 0:
    from .compat import bglx as bgl
else:
    import bgl


def draw_rounded_box(x, y, w, h, round_radius):

    def circle_verts_num(r):
        """Get number of verticies for circle optimized for drawing."""

        num_verts = 32
        threshold = 2.0  # pixcel
        while True:
            if r * 2 * math.pi / num_verts > threshold:
                return num_verts
            num_verts -= 4
            if num_verts < 1:
                return 1

    num_verts = circle_verts_num(round_radius)
    n = int(num_verts / 4) + 1
    dangle = math.pi * 2 / num_verts

    x_origin = [
        x + round_radius,
        x + w - round_radius,
        x + w - round_radius,
        x + round_radius,
    ]
    y_origin = [
        y + round_radius,
        y + round_radius,
        y + h - round_radius,
        y + h - round_radius,
    ]
    angle_start = [
        math.pi * 1.0,
        math.pi * 1.5,
        math.pi * 0.0,
        math.pi * 0.5,
    ]

    bgl.glBegin(bgl.GL_LINE_LOOP)
    for x0, y0, angle in zip(x_origin, y_origin, angle_start):
        for _ in range(n):
            x = x0 + round_radius * math.cos(angle)
            y = y0 + round_radius * math.sin(angle)
            bgl.glVertex2f(x, y)
            angle += dangle
    bgl.glEnd()


event_type_enum_items = bpy.types.Event.bl_rna.properties['type'].enum_items
EventType = enum.IntEnum(
    'EventType',
    [(e.identifier, e.value) for e in event_type_enum_items]
)
EventType.names = {e.identifier: e.name for e in event_type_enum_items}


def intersect_aabb(min1, max1, min2, max2):
    """Check intersection using AABB method."""

    for i in range(len(min1)):
        if (max1[i] < min2[i]) or (max2[i] < min1[i]):
            return False

    return True


def get_window_region_rect(area):
    """Return 'WINDOW' region rectangle."""

    rect = [99999, 99999, 0, 0]
    for region in area.regions:
        if region.type == 'WINDOW':
            rect[0] = min(rect[0], region.x)
            rect[1] = min(rect[1], region.y)
            rect[2] = max(region.x + region.width - 1, rect[2])
            rect[3] = max(region.y + region.height - 1, rect[3])

    return rect


def get_region_rect_on_v3d(context, area=None, region=None):
    """On VIEW_3D, we need to handle region overlap.
       This function takes into accout this, and return rectangle.
    """

    if not area:
        area = context.area
    if not region:
        region = context.region

    # We don't need to handle non-'WINDOW' region which is not effected by
    # region overlap. So we can return region rectangle as it is.
    if region.type != 'WINDOW':
        return [region.x, region.y,
                region.x + region.width, region.y + region.height]

    # From here, we handle 'WINDOW' region with considering region overlap.
    window = region
    tools = ui = None
    for ar in area.regions:
        # We need to dicard regions whose width is 1.
        if ar.width > 1:
            if ar.type == 'WINDOW':
                if ar == window:
                    window = ar
            elif ar.type == 'TOOLS':
                tools = ar
            elif ar.type == 'UI':
                ui = ar

    xmin, _, xmax, _ = get_window_region_rect(area)
    sys_pref = compat.get_user_preferences(context).system
    if sys_pref.use_region_overlap:
        left_width = right_width = 0

        if tools and ui:
            r1, r2 = sorted([tools, ui], key=lambda ar: ar.x)
            if r1.x == area.x:
                # 'TOOLS' and 'UI' are located on left side.
                if r2.x == r1.x + r1.width:
                    left_width = r1.width + r2.width
                # 'TOOLS' and 'UI' are located on each side.
                else:
                    left_width = r1.width
                    right_width = r2.width
            # 'TOOLS' and 'UI' are located on right side.
            else:
                right_width = r1.width + r2.width

        elif tools:
            # 'TOOLS' is located on left side.
            if tools.x == area.x:
                left_width = tools.width
            # 'TOOLS' is located on right side.
            else:
                right_width = tools.width

        elif ui:
            # 'UI' is located on left side.
            if ui.x == area.x:
                left_width = ui.width
            # 'TOOLS' is located on right side.
            else:
                right_width = ui.width

        # Clip 'UI' and 'TOOLS' region from 'WINDOW' region, which enables us
        # to show only 'WINDOW' region.
        xmin = max(xmin, area.x + left_width)
        xmax = min(xmax, area.x + area.width - right_width - 1)

    ymin = window.y
    ymax = window.y + window.height - 1

    return xmin, ymin, xmax, ymax

## from here

@BlClassRegistry()
class ScreencastKeysStatus(bpy.types.Operator):
    bl_idname = 'wm.screencast_keys'
    bl_label = 'Screencast Keys'
    bl_description = 'Display keys pressed'
    bl_options = {'REGISTER'}

    # hold modifier keys
    hold_modifier_keys = []
    # Event history.
    # Format: [time, event_type, modifier, repeat]
    event_history = []
    # Operator history.
    # Format: [time, bl_label, idname_py, addr]
    operator_history = []

    MODIFIER_EVENT_TYPES = [
        EventType.LEFT_SHIFT,
        EventType.RIGHT_SHIFT,
        EventType.LEFT_CTRL,
        EventType.RIGHT_CTRL,
        EventType.LEFT_ALT,
        EventType.RIGHT_ALT,
        EventType.OSKEY
    ]

    MOUSE_EVENT_TYPES = {
        EventType.LEFTMOUSE,
        EventType.MIDDLEMOUSE,
        EventType.RIGHTMOUSE,
        EventType.BUTTON4MOUSE,
        EventType.BUTTON5MOUSE,
        EventType.BUTTON6MOUSE,
        EventType.BUTTON7MOUSE,
        EventType.TRACKPADPAN,
        EventType.TRACKPADZOOM,
        EventType.MOUSEROTATE,
        EventType.WHEELUPMOUSE,
        EventType.WHEELDOWNMOUSE,
        EventType.WHEELINMOUSE,
        EventType.WHEELOUTMOUSE,
    }

    SPACE_TYPES = compat.get_all_space_types()

    # Height ratio against font for separator.
    HEIGHT_RATIO_FOR_SEPARATOR = 0.6

    # Interval for 'TIMER' event (redraw).
    TIMER_STEP = 0.1

    # Previous redraw time.
    prev_time = 0.0

    # Timer handlers.
    # Format: {Window.as_pointer(): Timer}
    timers = {}

    # Draw handlers.
    # Format: {(Space, Region.type): handle}
    handlers = {}

    # Regions which are drawing in previous redraw.
    # Format: {Region.as_pointer()}
    draw_regions_prev = set()

    # Draw target.
    origin = {
        "window": "",       # Window.as_pointer()
        "area": "",         # Area.as_pointer()
        "space": "",        # Space.as_pointer()
        "region_type": "",  # Region.type
    }

    # Area - Space mapping.
    # Format: {Area.as_pointer(), [Space.as_pointer(), ...]}
    # TODO: Clear when this model is finished.
    area_spaces = collections.defaultdict(set)

    # Check if this operator is running.
    # TODO: We can check it with the valid of event handler.
    running = False

    @classmethod
    def sorted_modifier_keys(cls, modifiers):
        """Sort and unique modifier keys."""

        def key_fn(event_type):
            if event_type in cls.MODIFIER_EVENT_TYPES:
                return cls.MODIFIER_EVENT_TYPES.index(event_type)
            else:
                return 100

        modifiers = sorted(modifiers, key=key_fn)
        names = []
        for mod in modifiers:
            name = mod.names[mod.name]
            assert mod in cls.MODIFIER_EVENT_TYPES, \
                   "{} must be modifier types".format(name)

            # Remove left and right identifier.
            name = re.sub('(Left |Right )', '', name)

            # Unique.
            if name not in names:
                names.append(name)

        return names

    @classmethod
    def removed_old_event_history(cls):
        """Return event history whose old events are removed."""

        prefs = compat.get_user_preferences(bpy.context).addons["screencastkeys"].preferences
        current_time = time.time()

        event_history = []
        for item in cls.event_history:
            event_time = item[0]
            t = current_time - event_time
            if t <= prefs.display_time:
                event_history.append(item)

        return event_history

    @classmethod
    def removed_old_operator_history(cls):
        """Return operator history whose old operators are removed."""
        # TODO: Control number of history from Preferences.

        return cls.operator_history[-32:]

    @classmethod
    def get_origin(cls, context):
        """Get draw target.
           Retrun value: (Window, Area, Region, x, y)
        """

        prefs = compat.get_user_preferences(bpy.context).addons["screencastkeys"].preferences

        def is_window_match(window):
            return window.as_pointer() == cls.origin['window']

        def is_area_match(area):
            if area.as_pointer() == cls.origin['area']:
                return True     # Area is just same as user specified area.
            elif area.spaces.active.as_pointer() == cls.origin['space']:
                return True     # Area is not same, but active space information is same.
            else:
                area_p = area.as_pointer()
                if area_p in cls.area_spaces:
                    spaces_p = {s.as_pointer() for s in area_p.spaces}
                    if cls.origin['space'] in spaces_p:
                        # Exists in inactive space information.
                        return True
            return False

        def is_region_match(area):
            return region.type == cls.origin['region_type']

        x, y = prefs.offset
        for window in context.window_manager.windows:
            if is_window_match(window):
                break
        else:
            return None, None, None, 0, 0

        if prefs.origin == 'WINDOW':
            return window, None, None, x, y
        elif prefs.origin == 'AREA':
            for area in window.screen.areas:
                if is_area_match(area):
                    return window, area, None, x + area.x, y + area.y
        elif prefs.origin == 'REGION':
            for area in window.screen.areas:
                if not is_area_match(area):
                    continue
                for region in area.regions:
                    if is_region_match(region):
                        if area.type == 'VIEW_3D':
                            rect = get_region_rect_on_v3d(context, area, region)
                            x += rect[0]
                            y += rect[1]
                        else:
                            x += region.x
                            y += region.y
                        return window, area, region, x, y

        return None, None, None, 0, 0

    @classmethod
    def calc_draw_area_rect(cls, context):
        """Return draw area rectangle.

        Draw format:

            Overview:
                ....
                Event history[-3]
                Event history[-2]
                Event history[-1]

                Hold modifier key list
                ----------------
                Operator history

            Event history format:
                With count: {key} x{count}
                With modifier key: {modifier key} + {key}

            Hold modifier key list format:
                 --------------     --------------
                |{modifier key}| + |{modifier key}|
                 --------------     --------------
        """

        # TODO: bpy.context -> context
        prefs = compat.get_user_preferences(bpy.context).addons["screencastkeys"].preferences

        font_size = prefs.font_size
        font_id = 0         # TODO: font_id should be constant.
        dpi = compat.get_user_preferences(context).system.dpi
        blf.size(font_id, font_size, dpi)

        # Get string height in draw area.
        sh = blf.dimensions(font_id, string.printable)[1]

        # Get draw target.
        window, area, region, x, y = cls.get_origin(context)
        if not window:
            return None

        # Calculate width/height of draw area.
        draw_area_width = 0
        draw_area_height = 0

        if prefs.show_last_operator:
            operator_history = cls.removed_old_operator_history()
            if operator_history:
                _, name, idname_py, _ = operator_history[-1]
                text = bpy.app.translations.pgettext(name, 'Operator')
                text += " ('{}')".format(idname_py)

                sw = blf.dimensions(font_id, text)[0]
                draw_area_width = max(draw_area_width, sw)
            draw_area_height += sh + sh * cls.SEPARATOR_HEIGHT

        if cls.hold_modifier_keys:
            mod_names = cls.sorted_modifier_keys(cls.hold_modifier_keys)
            text = ' + '.join(mod_names)

            sw = blf.dimensions(font_id, text)[0]
            draw_area_width = max(draw_area_width, sw)
            draw_area_height += sh

        event_history = cls.removed_old_event_history()

        if cls.hold_modifier_keys or event_history:
            sw = blf.dimensions(font_id, 'Left Mouse')[0]
            draw_area_width = max(draw_area_width, sw)
            draw_area_height += sh * cls.SEPARATOR_HEIGHT

        for _, event_type, modifiers, count in event_history[::-1]:
            text = event_type.names[event_type.name]
            if modifiers:
                mod_names = cls.sorted_modifier_keys(modifiers)
                text = ' + '.join(mod_names) + ' + ' + text
            if count > 1:
                text += ' x' + str(count)

            sw = blf.dimensions(font_id, text)[0]
            draw_area_width = max(draw_area_width, sw)
            draw_area_height += sh

        draw_area_height += sh

        if prefs.origin == 'WINDOW':
            return x, y, x + draw_area_width, y + draw_area_height
        elif prefs.origin == 'AREA':
            xmin = area.x
            ymin = area.y
            xmax = area.x + area.width - 1
            ymax = area.y + area.height - 1
            return (max(x, xmin),
                    max(y, ymin),
                    min(x + draw_area_width, xmax),
                    min(y + draw_area_height, ymax))
        elif prefs.origin == 'REGION':
            xmin = region.x
            ymin = region.y
            xmax = region.x + region.width - 1
            ymax = region.y + region.height - 1
            return (max(x, xmin),
                    max(y, ymin),
                    min(x + draw_area_width, xmax),
                    min(y + draw_area_height, ymax))
        
        assert False, "Value 'prefs.origin' is invalid (value={}).".format(prefs.origin)


    @classmethod
    def find_redraw_regions(cls, context):
        """Find regions to redraw."""

        rect = cls.calc_draw_area_rect(context)
        if not rect:
            return []       # No draw target.

        draw_area_min_x, draw_area_min_y, draw_area_max_x, draw_area_max_y = rect
        width = draw_area_max_x - draw_area_min_x
        height = draw_area_max_y - draw_area_min_y
        if width == height == 0:
            return []       # Zero size region.
        
        draw_area_min = [draw_area_min_x, draw_area_min_y]
        draw_area_max = [draw_area_max_x - 1, draw_area_max_y - 1]

        # Collect regions which overlaps with draw area.
        regions = []
        for area in context.screen.areas:
            for region in area.regions:
                if region.type == '':
                    continue    # Skip region with no type.
                region_min = [region.x, region.y]
                region_max = [region.x + region.width - 1,
                              region.y + region.height - 1]
                if intersect_aabb(region_min, region_max,
                                  draw_area_min, draw_area_max):
                    regions.append((area, region))

        return regions

# from here.

    @classmethod
    def draw_callback(cls, context):
        prefs = compat.get_user_preferences(context).addons["screencastkeys"].preferences

        if context.window.as_pointer() != cls.origin['window']:
            return      # Not match target window.

        rect = cls.calc_draw_area_rect(context)
        if not rect:
            return      # No draw target.

        # TODO:
        #   x -> origin_x
        #   y -> origin_y
        #   xmin -> draw_area_min_x
        #   rmin -> region_min_x

        draw_area_min_x, draw_area_min_y, draw_area_max_x, draw_area_max_y = rect
        _, _, _, origin_x, origin_y = cls.get_origin(context)
        width = draw_area_max_x - origin_x
        height = draw_area_max_y - origin_y
        if width == height == 0:
            return

        region = context.region
        area = context.area
        if region.type == 'WINDOW':
            region_min_x, region_min_y, region_max_x, region_max_y = get_window_region_rect(area)
        else:
            region_min_x = region.x
            region_min_y = region.y
            region_max_x = region.x + region.width - 1
            region_max_y = region.y + region.height - 1
        if not intersect_aabb(
                [region_min_x, region_min_y], [region_max_x, region_max_y],
                [draw_area_min_x + 1, draw_area_min_y + 1], [draw_area_max_x - 1, draw_area_max_x - 1]):
            # We don't need to draw if draw area is not overlapped with region.
            return

# from here

        current_time = time.time()
        draw_any = False

        font_size = prefs.font_size
        font_id = 0
        dpi = compat.get_user_preferences(context).system.dpi
        blf.size(font_id, font_size, dpi)

        def draw_text(text):
            col = prefs.color_shadow
            compat.set_blf_font_color(font_id, *col[:3], col[3] * 20)
            compat.set_blf_blur(font_id, 5)
            blf.draw(font_id, text)
            compat.set_blf_blur(font_id, 0)

            compat.set_blf_font_color(font_id, *prefs.color, 1.0)
            blf.draw(font_id, text)

        def draw_line(p1, p2):
            bgl.glEnable(bgl.GL_BLEND)
            bgl.glEnable(bgl.GL_LINE_SMOOTH)

            bgl.glLineWidth(3.0)
            bgl.glColor4f(*prefs.color_shadow)
            bgl.glBegin(bgl.GL_LINES)
            bgl.glVertex2f(*p1)
            bgl.glVertex2f(*p2)
            bgl.glEnd()

            bgl.glLineWidth(1.0 if prefs.color_shadow[-1] == 0.0 else 1.5)
            bgl.glColor3f(*prefs.color)
            bgl.glBegin(bgl.GL_LINES)
            bgl.glVertex2f(*p1)
            bgl.glVertex2f(*p2)
            bgl.glEnd()

            bgl.glLineWidth(1.0)
            bgl.glDisable(bgl.GL_LINE_SMOOTH)

        # user_preferences.system.use_region_overlapが真の場合に、
        # 二重に描画されるのを防ぐ
        glscissorbox = bgl.Buffer(bgl.GL_INT, 4)
        bgl.glGetIntegerv(bgl.GL_SCISSOR_BOX, glscissorbox)
        if context.area.type == 'VIEW_3D' and region.type == 'WINDOW':
            xmin, ymin, xmax, ymax = get_region_rect_on_v3d(context)
            bgl.glScissor(xmin, ymin, xmax - xmin + 1, ymax - ymin + 1)

        th = blf.dimensions(0, string.printable)[1]
        px = x - region.x
        py = y - region.y

        operator_history = cls.removed_old_operator_history()
        if prefs.show_last_operator and operator_history:
            t, name, idname_py, addr = operator_history[-1]
            if current_time - t <= prefs.display_time:
                color = prefs.color
                compat.set_blf_font_color(font_id, *color, 1.0)

                text = bpy.app.translations.pgettext_iface(name, 'Operator')
                text += " ('{}')".format(idname_py)

                blf.position(font_id, px, py, 0)
                draw_text(text)
                py += th + th * cls.SEPARATOR_HEIGHT * 0.2
                tw = blf.dimensions(font_id, 'Left Mouse')[0]  # 適当
                draw_line((px, py), (px + tw, py))
                py += th * cls.SEPARATOR_HEIGHT * 0.8

                draw_any = True

            else:
                py += th + th * cls.SEPARATOR_HEIGHT

        compat.set_blf_font_color(font_id, *prefs.color, 1.0)
        margin = th * 0.2
        if cls.hold_modifier_keys or False:   # is_rendering
            col = prefs.color_shadow[:3] + (prefs.color_shadow[3] * 2,)
            mod_names = cls.sorted_modifier_keys(cls.hold_modifier_keys)
            if False:    # is_rendering
                if 0:
                    text = '- - -'
                else:
                    text = ''
            else:
                text = ' + '.join(mod_names)

            ofsy = -th * 0.0
            box_h = th + margin * 2
            blf.position(font_id, px, py + margin, 0)
            draw_text(text)
            w, h = blf.dimensions(font_id, text)
            draw_rounded_box(px - margin, py - margin + ofsy,
                             w + margin * 2, box_h, box_h * 0.2)
            draw_any = True
        py += th + margin * 2

        event_history = cls.removed_old_event_history()

        py += th * cls.SEPARATOR_HEIGHT

        for event_time, event_type, modifiers, count in event_history[::-1]:
            color = prefs.color
            compat.set_blf_font_color(font_id, *color, 1.0)

            text = event_type.names[event_type.name]
            if modifiers:
                mod_names = cls.sorted_modifier_keys(modifiers)
                text = ' + '.join(mod_names) + ' + ' + text
            if count > 1:
                text += ' x' + str(count)
            blf.position(font_id, px, py, 0)
            draw_text(text)

            py += th
            draw_any = True

        bgl.glDisable(bgl.GL_BLEND)
        bgl.glScissor(*glscissorbox)
        bgl.glLineWidth(1.0)

        if draw_any:
            cls.draw_regions_prev.add(region.as_pointer())

    def update_hold_modifier_keys(self, event):

        self.hold_modifier_keys.clear()

        mod_keys = []
        if event.shift:
            mod_keys.append(EventType.LEFT_SHIFT)
        if event.oskey:
            mod_keys.append(EventType.OSKEY)
        if event.alt:
            mod_keys.append(EventType.LEFT_ALT)
        if event.ctrl:
            mod_keys.append(EventType.LEFT_CTRL)

        if EventType[event.type] == EventType.WINDOW_DEACTIVATE:
            mod_keys = []

        self.hold_modifier_keys.extend(mod_keys)

    def is_ignore_event(self, event, prefs=None):
        event_type = EventType[event.type]
        if event_type in {EventType.NONE, EventType.MOUSEMOVE,
                          EventType.INBETWEEN_MOUSEMOVE,
                          EventType.WINDOW_DEACTIVATE, EventType.TEXTINPUT}:
            return True
        elif (prefs is not None
              and not prefs.show_mouse_events
              and event_type in self.MOUSE_EVENT_TYPES):
            return True
        elif event_type.name.startswith('EVT_TWEAK'):
            return True
        elif event_type.name.startswith('TIMER'):
            return True

    def is_modifier_event(self, event):
        event_type = EventType[event.type]
        return event_type in self.MODIFIER_EVENT_TYPES

    def modal(self, context, event):
        prefs = compat.get_user_preferences(bpy.context).addons["screencastkeys"].preferences

        if not self.__class__.running:
            return {'FINISHED'}

        if event.type == '':
            # Many events that should (?) be identified as 'NONE' instead are
            # identified as '' and raise KeyErrors in EventType
            # (i.e. caps lock and the spin tool in edit mode)
            return {'PASS_THROUGH'}
        event_type = EventType[event.type]
        current_time = time.time()

        # update cls.area_spaces
        for area in context.screen.areas:
            for space in area.spaces:
                self.area_spaces[area.as_pointer()].add(space.as_pointer())

        # update hold modifiers keys
        self.update_hold_modifier_keys(event)
        current_mod = self.hold_modifier_keys.copy()
        if event_type in current_mod:
            current_mod.remove(event_type)

        # event_history
        if (not self.is_ignore_event(event, prefs=prefs) and
                not self.is_modifier_event(event) and event.value == 'PRESS'):
            last = self.event_history[-1] if self.event_history else None
            current = [current_time, event_type, current_mod, 1]
            if (last and last[1:-1] == current[1:-1] and
                    current_time - last[0] < prefs.display_time):
                last[0] = current_time
                last[-1] += 1
            else:
                self.event_history.append(current)
        self.event_history[:] = self.removed_old_event_history()

        # operator_history
        operators = list(context.window_manager.operators)

        if operators:
            if self.operator_history:
                addr = self.operator_history[-1][-1]
            else:
                addr = None
            j = 0
            for i, op in enumerate(operators[::-1]):
                if op.as_pointer() == addr:
                    j = len(operators) - i
                    break

            for op in operators[j:]:
                m, f = op.bl_idname.split('_OT_')
                idname_py = m.lower() + '.' + f
                self.operator_history.append(
                    [current_time, op.bl_label, idname_py, op.as_pointer()])
        self.operator_history[:] = self.removed_old_operator_history()

        # redraw
        prev_time = self.prev_time
        if (not self.is_ignore_event(event, prefs=prefs) or
                prev_time and current_time - prev_time >= self.TIMER_STEP):
            regions = self.find_redraw_regions(context)

            # 前回描画した箇所でregionsに含まれないものは再描画
            for area in context.screen.areas:
                for region in area.regions:
                    if region.as_pointer() in self.draw_regions_prev:
                        # TODO: region.id is not available in Blender 2.8
                        region.tag_redraw()
                        self.draw_regions_prev.remove(region.as_pointer())


            # 再描画
            for area, region in regions:
                space_type = self.SPACE_TYPES[area.type]
                h_key = (space_type, region.type)
                if h_key not in self.handlers:
                    self.handlers[h_key] = space_type.draw_handler_add(
                        self.draw_callback, (context,), region.type,
                        'POST_PIXEL')
                region.tag_redraw()
                self.draw_regions_prev.add(region.as_pointer())

            self.__class__.prev_time = current_time

        return {'PASS_THROUGH'}

    @classmethod
    def draw_handler_remove(cls):
        for (space_type, region_type), handle in cls.handlers.items():
            space_type.draw_handler_remove(handle, region_type)
        cls.handlers.clear()

    @classmethod
    def event_timer_add(cls, context):
        wm = context.window_manager
        for win in wm.windows:
            key = win.as_pointer()
            if key not in cls.timers:
                cls.timers[key] = wm.event_timer_add(cls.TIMER_STEP, window=win)

    @classmethod
    def event_timer_remove(cls, context):
        wm = context.window_manager
        for win in wm.windows:
            key = win.as_pointer()
            if key in cls.timers:
                wm.event_timer_remove(cls.timers[key])
        cls.timers.clear()

    def invoke(self, context, event):
        cls = self.__class__
        if cls.running:
            self.event_timer_remove(context)
            self.draw_handler_remove()
            self.hold_modifier_keys.clear()
            self.event_history.clear()
            self.operator_history.clear()
            self.draw_regions_prev.clear()
            context.area.tag_redraw()
            cls.running = False
            return {'CANCELLED'}
        else:
            self.update_hold_modifier_keys(event)
            self.event_timer_add(context)
            context.window_manager.modal_handler_add(self)
            self.origin['window'] = context.window.as_pointer()
            self.origin['area'] = context.area.as_pointer()
            self.origin['space'] = context.space_data.as_pointer()
            self.origin['region_type'] = context.region.type
            context.area.tag_redraw()
            cls.running = True
            return {'RUNNING_MODAL'}


@BlClassRegistry()
class ScreencastKeysStatusSetOrigin(bpy.types.Operator):
    bl_idname = 'wm.screencast_keys_set_origin'
    bl_label = 'Screencast Keys Set Origin'
    bl_description = ''
    bl_options = {'REGISTER'}

    color = (1.0, 0.0, 0.0, 0.3)
    handles = {}  # {(space_type, region_type): handle, ...}

    def draw_callback(self, context):
        region = context.region
        if region and region == self.region:
            bgl.glEnable(bgl.GL_BLEND)
            bgl.glColor4f(*self.color)
            bgl.glRecti(0, 0, region.width, region.height)
            bgl.glDisable(bgl.GL_BLEND)
            bgl.glColor4f(1.0, 1.0, 1.0, 1.0)  # 初期値ってこれだっけ？

    def draw_handler_add(self, context):
        for area in context.screen.areas:
            space_type = ScreencastKeysStatus.SPACE_TYPES[area.type]
            for region in area.regions:
                # TODO: region.id is not available in Blender 2.8
                if region.type != "":
                    key = (space_type, region.type)
                    if key not in self.handles:
                        handle = space_type.draw_handler_add(
                            self.draw_callback, (context,), region.type,
                            'POST_PIXEL')
                        self.handles[key] = handle

    def draw_handler_remove(self):
        for (space_type, region_type), handle in self.handles.items():
            space_type.draw_handler_remove(handle, region_type)
        self.handles.clear()

    def current_region(self, context, event):
        x, y = event.mouse_x, event.mouse_y
        for area in context.screen.areas:
            for region in area.regions:
                # TODO: region.id is not available in Blender 2.8
                if region.x <= x < region.x + region.width:
                    if region.y <= y < region.y + region.height:
                        return area, region
        return None, None

    def modal(self, context, event):
        area, region = self.current_region(context, event)
        if self.area_prev:
            self.area_prev.tag_redraw()
        if area:
            area.tag_redraw()
        self.region = region
        if event.type in {'LEFTMOUSE', 'SPACE', 'RET', 'NUMPAD_ENTER'}:
            if event.value == 'PRESS':
                origin = ScreencastKeysStatus.origin
                origin['window'] = context.window.as_pointer()
                origin['area'] = area.as_pointer()
                origin['space'] = area.spaces.active.as_pointer()
                origin['region_type'] = region.type
                self.draw_handler_remove()
                return {'FINISHED'}
        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            self.draw_handler_remove()
            return {'CANCELLED'}
        self.area_prev = area
        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        self.area_prev = None
        self.region = None
        self.draw_handler_add(context)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}


@BlClassRegistry()
class ScreencastKeysPanel(bpy.types.Panel):
    bl_idname = 'WM_PT_screencast_keys'
    bl_label = 'Screencast Keys'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Screencast Keys"

    def draw_header(self, context):
        layout = self.layout
        layout.prop(context.window_manager, 'enable_screencast_keys',
                    text='')

    def draw(self, context):
        layout = self.layout
        prefs = compat.get_user_preferences(bpy.context).addons["screencastkeys"].preferences

        column = layout.column()

        column.prop(prefs, 'color')
        column.prop(prefs, 'color_shadow')
        column.prop(prefs, 'font_size')
        column.prop(prefs, 'display_time')

        column.separator()

        column.prop(prefs, 'origin')
        row = column.row()
        row.prop(prefs, 'offset')
        column.operator('wm.screencast_keys_set_origin',
                        text='Set Origin')
        column.prop(prefs, 'show_mouse_events')
        column.prop(prefs, 'show_last_operator')

    @classmethod
    def register(cls):
        def get_func(self):
            return ScreencastKeysStatus.running

        def set_func(self, value):
            pass

        def update_func(self, context):
            bpy.ops.wm.screencast_keys('INVOKE_REGION_WIN')

        bpy.types.WindowManager.enable_screencast_keys = \
            bpy.props.BoolProperty(
                name='Screencast Keys',
                get=get_func,
                set=set_func,
                update=update_func,
            )

    @classmethod
    def unregister(cls):
        del bpy.types.WindowManager.enable_screencast_keys
