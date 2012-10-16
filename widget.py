from urwid.util import is_mouse_press
import logging
from urwid.canvas import SolidCanvas, CanvasCombine
from urwid.widget import Widget, nocache_widget_render_instance, BOX, GIVEN
from urwid.decoration import calculate_top_bottom_filler, normalize_valign
from urwid.signals import connect_signal
from urwid.container import WidgetContainerMixin
import urwid
#from urwid.wimp import SelectableIcon


class TreeBoxError(Exception):
    pass


#ListBox
class ListBox(Widget, WidgetContainerMixin):
    _selectable = True
    _sizing = frozenset([BOX])

    def __init__(self, body):
        """
        body -- a ListWalker-like object that contains
            widgets to be displayed inside the list box
        """
        self.body = body
        try:
            connect_signal(self.body, "modified", self._invalidate)
        except NameError:
            # our list walker has no modified signal so we must not
            # cache our canvases because we don't know when our
            # content has changed
            self.render = nocache_widget_render_instance(self)

        # offset_rows is the number of rows between the top of the view
        # and the top of the focused item
        self.offset_rows = 0
        # inset_fraction is used when the focused widget is off the
        # top of the view.  it is the fraction of the widget cut off
        # at the top.  (numerator, denominator)
        self.inset_fraction = (0, 1)

        # pref_col is the preferred column for the cursor when moving
        # between widgets that use the cursor (edit boxes etc.)
        self.pref_col = 'left'

        # variable for delayed focus change used by set_focus
        self.set_focus_pending = 'first selectable'

        # variable for delayed valign change used by set_focus_valign
        self.set_focus_valign_pending = None

    def calculate_visible(self, size, focus=False):
        """ Return (middle,top,bottom) or None,None,None.

        middle -- ( row offset(when +ve) or inset(when -ve),
            focus widget, focus position, focus rows,
            cursor coords or None )
        top -- ( # lines to trim off top,
            list of (widget, position, rows) tuples above focus
            in order from bottom to top )
        bottom -- ( # lines to trim off bottom,
            list of (widget, position, rows) tuples below focus
            in order from top to bottom )
        """
        (maxcol, maxrow) = size

        # 0. set the focus if a change is pending
        if self.set_focus_pending or self.set_focus_valign_pending:
            self._set_focus_complete((maxcol, maxrow), focus)

        # 1. start with the focus widget
        focus_widget, focus_pos = self.body.get_focus()
        if focus_widget is None:  # list box is empty?
            return None, None, None
        top_pos = focus_pos

        offset_rows, inset_rows = self.get_focus_offset_inset(
            (maxcol, maxrow))
        #    force at least one line of focus to be visible
        if maxrow and offset_rows >= maxrow:
            offset_rows = maxrow - 1

        #    adjust position so cursor remains visible
        cursor = None
        if maxrow and focus_widget.selectable() and focus:
            if hasattr(focus_widget, 'get_cursor_coords'):
                cursor = focus_widget.get_cursor_coords((maxcol,))

        if cursor is not None:
            cx, cy = cursor
            effective_cy = cy + offset_rows - inset_rows

            if effective_cy < 0:  # cursor above top?
                inset_rows = cy
            elif effective_cy >= maxrow:  # cursor below bottom?
                offset_rows = maxrow - cy - 1
                if offset_rows < 0:  # need to trim the top
                    inset_rows, offset_rows = -offset_rows, 0

        #    set trim_top by focus trimmimg
        trim_top = inset_rows
        focus_rows = focus_widget.rows((maxcol,), True)

        # 2. collect the widgets above the focus
        pos = focus_pos
        fill_lines = offset_rows
        fill_above = []
        top_pos = pos
        while fill_lines > 0:
            prev, pos = self.body.get_prev(pos)
            if prev is None:  # run out of widgets above?
                offset_rows -= fill_lines
                break
            top_pos = pos

            p_rows = prev.rows((maxcol,))
            if p_rows:  # filter out 0-height widgets
                fill_above.append((prev, pos, p_rows))
            if p_rows > fill_lines:  # crosses top edge?
                trim_top = p_rows - fill_lines
                break
            fill_lines -= p_rows

        trim_bottom = focus_rows + offset_rows - inset_rows - maxrow
        if trim_bottom < 0:
            trim_bottom = 0

        # 3. collect the widgets below the focus
        pos = focus_pos
        fill_lines = maxrow - focus_rows - offset_rows + inset_rows
        fill_below = []
        while fill_lines > 0:
            next, pos = self.body.get_next(pos)
            if next is None:  # run out of widgets below?
                break

            n_rows = next.rows((maxcol,))
            if n_rows:  # filter out 0-height widgets
                fill_below.append((next, pos, n_rows))
            if n_rows > fill_lines:  # crosses bottom edge?
                trim_bottom = n_rows - fill_lines
                fill_lines -= n_rows
                break
            fill_lines -= n_rows

        # 4. fill from top again if necessary & possible
        fill_lines = max(0, fill_lines)

        if fill_lines > 0 and trim_top > 0:
            if fill_lines <= trim_top:
                trim_top -= fill_lines
                offset_rows += fill_lines
                fill_lines = 0
            else:
                fill_lines -= trim_top
                offset_rows += trim_top
                trim_top = 0
        pos = top_pos
        while fill_lines > 0:
            prev, pos = self.body.get_prev(pos)
            if prev is None:
                break

            p_rows = prev.rows((maxcol,))
            fill_above.append((prev, pos, p_rows))
            if p_rows > fill_lines:  # more than required
                trim_top = p_rows - fill_lines
                offset_rows += fill_lines
                break
            fill_lines -= p_rows
            offset_rows += p_rows

        # 5. return the interesting bits
        return ((offset_rows - inset_rows, focus_widget,
                focus_pos, focus_rows, cursor),
                (trim_top, fill_above), (trim_bottom, fill_below))

    def render(self, size, focus=False):
        """
        Render listbox and return canvas.
        """
        (maxcol, maxrow) = size

        middle, top, bottom = self.calculate_visible(
            (maxcol, maxrow), focus=focus)
        if middle is None:
            return SolidCanvas(" ", maxcol, maxrow)

        _ignore, focus_widget, focus_pos, focus_rows, cursor = middle
        trim_top, fill_above = top
        trim_bottom, fill_below = bottom

        combinelist = []
        rows = 0
        fill_above.reverse()  # fill_above is in bottom-up order
        for widget, w_pos, w_rows in fill_above:
            canvas = widget.render((maxcol,))
            if w_rows != canvas.rows():
                raise TreeBoxError("Widget %r at position %r within listbox calculated %d rows but rendered %d!" % (widget, w_pos, w_rows, canvas.rows()))
            rows += w_rows
            combinelist.append((canvas, w_pos, False))

        focus_canvas = focus_widget.render((maxcol,), focus=focus)

        if focus_canvas.rows() != focus_rows:
            raise TreeBoxError("Focus Widget %r at position %r within listbox calculated %d rows but rendered %d!" % (focus_widget, focus_pos, focus_rows, focus_canvas.rows()))
        c_cursor = focus_canvas.cursor
        if cursor != c_cursor:
            raise TreeBoxError("Focus Widget %r at position %r within listbox calculated cursor coords %r but rendered cursor coords %r!" % (focus_widget, focus_pos, cursor, c_cursor))

        rows += focus_rows
        combinelist.append((focus_canvas, focus_pos, True))

        for widget, w_pos, w_rows in fill_below:
            canvas = widget.render((maxcol,))
            if w_rows != canvas.rows():
                raise TreeBoxError("Widget %r at position %r within listbox calculated %d rows but rendered %d!" % (widget, w_pos, w_rows, canvas.rows()))
            rows += w_rows
            combinelist.append((canvas, w_pos, False))

        final_canvas = CanvasCombine(combinelist)

        if trim_top:
            final_canvas.trim(trim_top)
            rows -= trim_top
        if trim_bottom:
            final_canvas.trim_end(trim_bottom)
            rows -= trim_bottom

        if rows > maxrow:
            raise TreeBoxError("Listbox contents too long!  Probably urwid's fault (please report): %r" % ((top, middle, bottom),))

        if rows < maxrow:
            bottom_pos = focus_pos
            if fill_below:
                bottom_pos = fill_below[-1][1]
            if trim_bottom != 0 or self.body.get_next(bottom_pos) != (None, None):
                raise TreeBoxError("Listbox contents too short!  Probably urwid's fault (please report): %r" % ((top, middle, bottom),))
            final_canvas.pad_trim_top_bottom(0, maxrow - rows)

        return final_canvas

    def get_cursor_coords(self, size):
        (maxcol, maxrow) = size

        middle, top, bottom = self.calculate_visible(
            (maxcol, maxrow), True)
        if middle is None:
            return None

        offset_inset, _ignore1, _ignore2, _ignore3, cursor = middle
        if not cursor:
            return None

        x, y = cursor
        y += offset_inset
        if y < 0 or y >= maxrow:
            return None
        return (x, y)

    def set_focus_valign(self, valign):
        """Set the focus widget's display offset and inset.

        valign -- one of:
            'top', 'middle', 'bottom'
            ('fixed top', rows)
            ('fixed bottom', rows)
            ('relative', percentage 0=top 100=bottom)
        """
        vt, va = normalize_valign(valign, TreeBoxError)
        self.set_focus_valign_pending = vt, va

    def set_focus(self, position, coming_from=None):
        """
        Set the focus position and try to keep the old focus in view.

        position -- a position compatible with self.body.set_focus
        coming_from -- set to 'above' or 'below' if you know that
                       old position is above or below the new position.
        """
        if coming_from not in ('above', 'below', None):
            raise TreeBoxError("coming_from value invalid: %r" %
                               (coming_from,))
        focus_widget, focus_pos = self.body.get_focus()
        if focus_widget is None:
            raise IndexError("Can't set focus, ListBox is empty")

        self.set_focus_pending = coming_from, focus_widget, focus_pos
        self.body.set_focus(position)

    def get_focus(self):
        """
        Return a (focus widget, focus position) tuple, for backwards
        compatibility.  You may also use the new standard container
        properties .focus and .focus_position to read these values.
        """
        return self.body.get_focus()

    def _get_focus(self):
        """
        Return the widget in focus according to our list walker.
        """
        return self.body.get_focus()[0]
    focus = property(_get_focus,
                     doc="the child widget in focus or None when ListBox is empty")

    def _get_focus_position(self):
        """
        Return the list walker position of the widget in focus.  The type
        of value returned depends on the list walker.
        """
        w, pos = self.body.get_focus()
        if w is None:
            raise IndexError("No focus_position, ListBox is empty")
        return pos
    focus_position = property(_get_focus_position, set_focus, doc="""
        the position of child widget in focus.  The valid values for this
        position depend on the list walker in use.  IndexError will be
        raised by reading this property when the ListBox is empty or
        setting this property to an invalid position.
        """)

    def _contents(self):
        class ListBoxContents(object):
            __getitem__ = self._contents__getitem__
        return ListBoxContents()

    def _contents__getitem__(self, key):
        # try list walker protocol v2 first
        getitem = getattr(self.body, '__getitem__', None)
        if getitem:
            try:
                return (getitem(key), None)
            except (IndexError, KeyError):
                raise KeyError("ListBox.contents key not found: %r" % (key,))
        # fall back to v1
        w, old_focus = self.body.get_focus()
        try:
            try:
                self.body.set_focus(key)
                return self.body.get_focus()[0]
            except (IndexError, KeyError):
                raise KeyError("ListBox.contents key not found: %r" % (key,))
        finally:
            self.body.set_focus(old_focus)
    contents = property(lambda self: self._contents, doc="""
        An object that allows reading widgets from the ListBox's list
        walker as a (widget, options) tuple.  None is currently the only
        value for options.

        This object may not be used to set or iterate over contents.  You
        must use the list walker stored as .body to perform manipulation
        and iteration, if supported.
        """)

    def options(self):
        """
        There are currently no options for ListBox contents.

        Return None as a placeholder for future options.
        """
        return None

    def _set_focus_valign_complete(self, size, focus):
        """
        Finish setting the offset and inset now that we have have a
        maxcol & maxrow.
        """
        (maxcol, maxrow) = size
        vt, va = self.set_focus_valign_pending
        self.set_focus_valign_pending = None
        self.set_focus_pending = None

        focus_widget, focus_pos = self.body.get_focus()
        if focus_widget is None:
            return

        rows = focus_widget.rows((maxcol,), focus)
        rtop, rbot = calculate_top_bottom_filler(maxrow,
                                                 vt, va, GIVEN, rows, None, 0, 0)

        self.shift_focus((maxcol, maxrow), rtop)

    def _set_focus_first_selectable(self, size, focus):
        """
        Choose the first visible, selectable widget below the
        current focus as the focus widget.
        """
        (maxcol, maxrow) = size
        self.set_focus_valign_pending = None
        self.set_focus_pending = None
        middle, top, bottom = self.calculate_visible(
            (maxcol, maxrow), focus=focus)
        if middle is None:
            return

        row_offset, focus_widget, focus_pos, focus_rows, cursor = middle
        trim_top, fill_above = top
        trim_bottom, fill_below = bottom

        if focus_widget.selectable():
            return

        if trim_bottom:
            fill_below = fill_below[:-1]
        new_row_offset = row_offset + focus_rows
        for widget, pos, rows in fill_below:
            if widget.selectable():
                self.body.set_focus(pos)
                self.shift_focus((maxcol, maxrow),
                                 new_row_offset)
                return
            new_row_offset += rows

    def _set_focus_complete(self, size, focus):
        """
        Finish setting the position now that we have maxcol & maxrow.
        """
        (maxcol, maxrow) = size
        self._invalidate()
        if self.set_focus_pending == "first selectable":
            return self._set_focus_first_selectable(
                (maxcol, maxrow), focus)
        if self.set_focus_valign_pending is not None:
            return self._set_focus_valign_complete(
                (maxcol, maxrow), focus)
        coming_from, focus_widget, focus_pos = self.set_focus_pending
        self.set_focus_pending = None

        # new position
        new_focus_widget, position = self.body.get_focus()
        if focus_pos == position:
            # do nothing
            return

        # restore old focus temporarily
        self.body.set_focus(focus_pos)

        middle, top, bottom = self.calculate_visible((maxcol, maxrow), focus)
        focus_offset, focus_widget, focus_pos, focus_rows, cursor = middle
        trim_top, fill_above = top
        trim_bottom, fill_below = bottom

        offset = focus_offset
        for widget, pos, rows in fill_above:
            offset -= rows
            if pos == position:
                self.change_focus((maxcol, maxrow), pos,
                                  offset, 'below')
                return

        offset = focus_offset + focus_rows
        for widget, pos, rows in fill_below:
            if pos == position:
                self.change_focus((maxcol, maxrow), pos,
                                  offset, 'above')
                return
            offset += rows

        # failed to find widget among visible widgets
        self.body.set_focus(position)
        widget, position = self.body.get_focus()
        rows = widget.rows((maxcol,), focus)

        if coming_from == 'below':
            offset = 0
        elif coming_from == 'above':
            offset = maxrow - rows
        else:
            offset = (maxrow - rows) // 2
        self.shift_focus((maxcol, maxrow), offset)

    def shift_focus(self, size, offset_inset):
        """Move the location of the current focus relative to the top.

        offset_inset -- either the number of rows between the
          top of the listbox and the start of the focus widget (+ve
          value) or the number of lines of the focus widget hidden off
          the top edge of the listbox (-ve value) or 0 if the top edge
          of the focus widget is aligned with the top edge of the
          listbox
        """
        (maxcol, maxrow) = size

        if offset_inset >= 0:
            if offset_inset >= maxrow:
                raise TreeBoxError("Invalid offset_inset: %r, only %r rows in list box" % (offset_inset, maxrow))
            self.offset_rows = offset_inset
            self.inset_fraction = (0, 1)
        else:
            target, _ignore = self.body.get_focus()
            tgt_rows = target.rows((maxcol,), True)
            if offset_inset + tgt_rows <= 0:
                raise TreeBoxError("Invalid offset_inset: %r, only %r rows in target!" % (offset_inset, tgt_rows))
            self.offset_rows = 0
            self.inset_fraction = (-offset_inset, tgt_rows)
        self._invalidate()

    def update_pref_col_from_focus(self, size):
        """Update self.pref_col from the focus widget."""
        (maxcol, maxrow) = size

        widget, old_pos = self.body.get_focus()
        if widget is None:
            return

        pref_col = None
        if hasattr(widget, 'get_pref_col'):
            pref_col = widget.get_pref_col((maxcol,))
        if pref_col is None and hasattr(widget, 'get_cursor_coords'):
            coords = widget.get_cursor_coords((maxcol,))
            if type(coords) == tuple:
                pref_col, y = coords
        if pref_col is not None:
            self.pref_col = pref_col

    def change_focus(self, size, position,
                     offset_inset=0, coming_from=None,
                     cursor_coords=None, snap_rows=None):
        """Change the current focus widget.

        position -- a position compatible with self.body.set_focus
        offset_inset -- either the number of rows between the
          top of the listbox and the start of the focus widget (+ve
          value) or the number of lines of the focus widget hidden off
          the top edge of the listbox (-ve value) or 0 if the top edge
          of the focus widget is aligned with the top edge of the
          listbox (default if unspecified)
        coming_from -- eiter 'above', 'below' or unspecified (None)
        cursor_coords -- (x, y) tuple indicating the desired
          column and row for the cursor, a (x,) tuple indicating only
          the column for the cursor, or unspecified (None)
        snap_rows -- the maximum number of extra rows to scroll
          when trying to "snap" a selectable focus into the view
        """
        (maxcol, maxrow) = size

        # update pref_col before change
        if cursor_coords:
            self.pref_col = cursor_coords[0]
        else:
            self.update_pref_col_from_focus((maxcol, maxrow))

        self._invalidate()
        self.body.set_focus(position)
        target, _ignore = self.body.get_focus()
        tgt_rows = target.rows((maxcol,), True)
        if snap_rows is None:
            snap_rows = maxrow - 1

        # "snap" to selectable widgets
        align_top = 0
        align_bottom = maxrow - tgt_rows

        if (coming_from == 'above'
                and target.selectable()
                and offset_inset > align_bottom):
            if snap_rows >= offset_inset - align_bottom:
                offset_inset = align_bottom
            elif snap_rows >= offset_inset - align_top:
                offset_inset = align_top
            else:
                offset_inset -= snap_rows

        if (coming_from == 'below'
                and target.selectable()
                and offset_inset < align_top):
            if snap_rows >= align_top - offset_inset:
                offset_inset = align_top
            elif snap_rows >= align_bottom - offset_inset:
                offset_inset = align_bottom
            else:
                offset_inset += snap_rows

        # convert offset_inset to offset_rows or inset_fraction
        if offset_inset >= 0:
            self.offset_rows = offset_inset
            self.inset_fraction = (0, 1)
        else:
            if offset_inset + tgt_rows <= 0:
                raise TreeBoxError("Invalid offset_inset: %s, only %s rows in target!" % (offset_inset, tgt_rows))
            self.offset_rows = 0
            self.inset_fraction = (-offset_inset, tgt_rows)

        if cursor_coords is None:
            if coming_from is None:
                return  # must either know row or coming_from
            cursor_coords = (self.pref_col,)

        if not hasattr(target, 'move_cursor_to_coords'):
            return

        attempt_rows = []

        if len(cursor_coords) == 1:
            # only column (not row) specified
            # start from closest edge and move inwards
            (pref_col,) = cursor_coords
            if coming_from == 'above':
                attempt_rows = range(0, tgt_rows)
            else:
                assert coming_from == 'below', "must specify coming_from ('above' or 'below') if cursor row is not specified"
                attempt_rows = range(tgt_rows, -1, -1)
        else:
            # both column and row specified
            # start from preferred row and move back to closest edge
            (pref_col, pref_row) = cursor_coords
            if pref_row < 0 or pref_row >= tgt_rows:
                raise TreeBoxError("cursor_coords row outside valid range for target. pref_row:%r target_rows:%r" % (pref_row, tgt_rows))

            if coming_from == 'above':
                attempt_rows = range(pref_row, -1, -1)
            elif coming_from == 'below':
                attempt_rows = range(pref_row, tgt_rows)
            else:
                attempt_rows = [pref_row]

        for row in attempt_rows:
            if target.move_cursor_to_coords((maxcol,), pref_col, row):
                break

    def get_focus_offset_inset(self, size):
        """Return (offset rows, inset rows) for focus widget."""
        (maxcol, maxrow) = size
        focus_widget, pos = self.body.get_focus()
        focus_rows = focus_widget.rows((maxcol,), True)
        offset_rows = self.offset_rows
        inset_rows = 0
        if offset_rows == 0:
            inum, iden = self.inset_fraction
            if inum < 0 or iden < 0 or inum >= iden:
                raise TreeBoxError(
                    "Invalid inset_fraction: %r" % (self.inset_fraction,))
            inset_rows = focus_rows * inum // iden
            if inset_rows and inset_rows >= focus_rows:
                raise TreeBoxError(
                    "urwid inset_fraction error (please report)")
        return offset_rows, inset_rows

    def make_cursor_visible(self, size):
        """Shift the focus widget so that its cursor is visible."""
        (maxcol, maxrow) = size

        focus_widget, pos = self.body.get_focus()
        if focus_widget is None:
            return
        if not focus_widget.selectable():
            return
        if not hasattr(focus_widget, 'get_cursor_coords'):
            return
        cursor = focus_widget.get_cursor_coords((maxcol,))
        if cursor is None:
            return
        cx, cy = cursor
        offset_rows, inset_rows = self.get_focus_offset_inset(
            (maxcol, maxrow))

        if cy < inset_rows:
            self.shift_focus((maxcol, maxrow), - (cy))
            return

        if offset_rows - inset_rows + cy >= maxrow:
            self.shift_focus((maxcol, maxrow), maxrow - cy - 1)
            return

    def keypress(self, size, key):
        """Move selection through the list elements scrolling when
        necessary. 'up' and 'down' are first passed to widget in focus
        in case that widget can handle them. 'page up' and 'page down'
        are always handled by the ListBox.

        Keystrokes handled by this widget are:
         'up'        up one line (or widget)
         'down'      down one line (or widget)
         'page up'   move cursor up one listbox length
         'page down' move cursor down one listbox length
        """
        (maxcol, maxrow) = size

        if self.set_focus_pending or self.set_focus_valign_pending:
            self._set_focus_complete((maxcol, maxrow), focus=True)

        focus_widget, pos = self.body.get_focus()
        if focus_widget is None:  # empty listbox, can't do anything
            return key

        if key not in ['page up', 'page down']:
            if focus_widget.selectable():
                key = focus_widget.keypress((maxcol,), key)
            if key is None:
                self.make_cursor_visible((maxcol, maxrow))
                return

        # pass off the heavy lifting
        if self._command_map[key] == 'cursor up':
            return self._keypress_up((maxcol, maxrow))

        if self._command_map[key] == 'cursor down':
            return self._keypress_down((maxcol, maxrow))

        if self._command_map[key] == 'cursor page up':
            return self._keypress_page_up((maxcol, maxrow))

        if self._command_map[key] == 'cursor page down':
            return self._keypress_page_down((maxcol, maxrow))

        return key

    def _keypress_up(self, size):
        (maxcol, maxrow) = size

        middle, top, bottom = self.calculate_visible(
            (maxcol, maxrow), True)
        if middle is None:
            return 'up'

        focus_row_offset, focus_widget, focus_pos, _ignore, cursor = middle
        trim_top, fill_above = top

        row_offset = focus_row_offset

        # look for selectable widget above
        pos = focus_pos
        widget = None
        for widget, pos, rows in fill_above:
            row_offset -= rows
            if rows and widget.selectable():
                # this one will do
                self.change_focus((maxcol, maxrow), pos,
                                  row_offset, 'below')
                return

        # at this point we must scroll
        row_offset += 1
        self._invalidate()

        while row_offset > 0:
            # need to scroll in another candidate widget
            widget, pos = self.body.get_prev(pos)
            if widget is None:
                # cannot scroll any further
                return 'up'  # keypress not handled
            rows = widget.rows((maxcol,), True)
            row_offset -= rows
            if rows and widget.selectable():
                # this one will do
                self.change_focus((maxcol, maxrow), pos,
                                  row_offset, 'below')
                return

        if not focus_widget.selectable() or focus_row_offset + 1 >= maxrow:
            # just take top one if focus is not selectable
            # or if focus has moved out of view
            if widget is None:
                self.shift_focus((maxcol, maxrow), row_offset)
                return
            self.change_focus((maxcol, maxrow), pos,
                              row_offset, 'below')
            return

        # check if cursor will stop scroll from taking effect
        if cursor is not None:
            x, y = cursor
            if y + focus_row_offset + 1 >= maxrow:
                # cursor position is a problem,
                # choose another focus
                if widget is None:
                    # try harder to get prev widget
                    widget, pos = self.body.get_prev(pos)
                    if widget is None:
                        return  # can't do anything
                    rows = widget.rows((maxcol,), True)
                    row_offset -= rows

                if -row_offset >= rows:
                    # must scroll further than 1 line
                    row_offset = - (rows - 1)

                self.change_focus((maxcol, maxrow), pos,
                                  row_offset, 'below')
                return

        # if all else fails, just shift the current focus.
        self.shift_focus((maxcol, maxrow), focus_row_offset + 1)

    def _keypress_down(self, size):
        (maxcol, maxrow) = size

        middle, top, bottom = self.calculate_visible(
            (maxcol, maxrow), True)
        if middle is None:
            return 'down'

        focus_row_offset, focus_widget, focus_pos, focus_rows, cursor = middle
        trim_bottom, fill_below = bottom

        row_offset = focus_row_offset + focus_rows
        rows = focus_rows

        # look for selectable widget below
        pos = focus_pos
        widget = None
        for widget, pos, rows in fill_below:
            if rows and widget.selectable():
                # this one will do
                self.change_focus((maxcol, maxrow), pos,
                                  row_offset, 'above')
                return
            row_offset += rows

        # at this point we must scroll
        row_offset -= 1
        self._invalidate()

        while row_offset < maxrow:
            # need to scroll in another candidate widget
            widget, pos = self.body.get_next(pos)
            if widget is None:
                # cannot scroll any further
                return 'down'  # keypress not handled
            rows = widget.rows((maxcol,))
            if rows and widget.selectable():
                # this one will do
                self.change_focus((maxcol, maxrow), pos,
                                  row_offset, 'above')
                return
            row_offset += rows

        if not focus_widget.selectable() or focus_row_offset + focus_rows - 1 <= 0:
            # just take bottom one if current is not selectable
            # or if focus has moved out of view
            if widget is None:
                self.shift_focus((maxcol, maxrow),
                                 row_offset - rows)
                return
            # FIXME: catch this bug in testcase
            #self.change_focus((maxcol,maxrow), pos,
            #    row_offset+rows, 'above')
            self.change_focus((maxcol, maxrow), pos,
                              row_offset - rows, 'above')
            return

        # check if cursor will stop scroll from taking effect
        if cursor is not None:
            x, y = cursor
            if y + focus_row_offset - 1 < 0:
                # cursor position is a problem,
                # choose another focus
                if widget is None:
                    # try harder to get next widget
                    widget, pos = self.body.get_next(pos)
                    if widget is None:
                        return  # can't do anything
                else:
                    row_offset -= rows

                if row_offset >= maxrow:
                    # must scroll further than 1 line
                    row_offset = maxrow - 1

                self.change_focus((maxcol, maxrow), pos,
                                  row_offset, 'above', )
                return

        # if all else fails, keep the current focus.
        self.shift_focus((maxcol, maxrow), focus_row_offset - 1)

    def _keypress_page_up(self, size):
        (maxcol, maxrow) = size

        middle, top, bottom = self.calculate_visible(
            (maxcol, maxrow), True)
        if middle is None:
            return 'page up'

        row_offset, focus_widget, focus_pos, focus_rows, cursor = middle
        trim_top, fill_above = top

        # topmost_visible is row_offset rows above top row of
        # focus (+ve) or -row_offset rows below top row of focus (-ve)
        topmost_visible = row_offset

        # scroll_from_row is (first match)
        # 1. topmost visible row if focus is not selectable
        # 2. row containing cursor if focus has a cursor
        # 3. top row of focus widget if it is visible
        # 4. topmost visible row otherwise
        if not focus_widget.selectable():
            scroll_from_row = topmost_visible
        elif cursor is not None:
            x, y = cursor
            scroll_from_row = -y
        elif row_offset >= 0:
            scroll_from_row = 0
        else:
            scroll_from_row = topmost_visible

        # snap_rows is maximum extra rows to scroll when
        # snapping to new a focus
        snap_rows = topmost_visible - scroll_from_row

        # move row_offset to the new desired value (1 "page" up)
        row_offset = scroll_from_row + maxrow

        # not used below:
        scroll_from_row = topmost_visible = None

        # gather potential target widgets
        t = []
        # add current focus
        t.append((row_offset, focus_widget, focus_pos, focus_rows))
        pos = focus_pos
        # include widgets from calculate_visible(..)
        for widget, pos, rows in fill_above:
            row_offset -= rows
            t.append((row_offset, widget, pos, rows))
        # add newly visible ones, including within snap_rows
        snap_region_start = len(t)
        while row_offset > -snap_rows:
            widget, pos = self.body.get_prev(pos)
            if widget is None:
                break
            rows = widget.rows((maxcol,))
            row_offset -= rows
            # determine if one below puts current one into snap rgn
            if row_offset > 0:
                snap_region_start += 1
            t.append((row_offset, widget, pos, rows))

        # if we can't fill the top we need to adjust the row offsets
        row_offset, w, p, r = t[-1]
        if row_offset > 0:
            adjust = - row_offset
            t = [(ro + adjust, w, p, r) for (ro, w, p, r) in t]

        # if focus_widget (first in t) is off edge, remove it
        row_offset, w, p, r = t[0]
        if row_offset >= maxrow:
            del t[0]
            snap_region_start -= 1

        # we'll need this soon
        self.update_pref_col_from_focus((maxcol, maxrow))

        # choose the topmost selectable and (newly) visible widget
        # search within snap_rows then visible region
        search_order = (range(snap_region_start, len(t))
                        + range(snap_region_start - 1, -1, -1))
        #assert 0, repr((t, search_order))
        bad_choices = []
        cut_off_selectable_chosen = 0
        for i in search_order:
            row_offset, widget, pos, rows = t[i]
            if not widget.selectable():
                continue

            if not rows:
                continue

            # try selecting this widget
            pref_row = max(0, -row_offset)

            # if completely within snap region, adjust row_offset
            if rows + row_offset <= 0:
                self.change_focus((maxcol, maxrow), pos,
                                  -(rows - 1), 'below',
                                  (self.pref_col, rows - 1),
                                  snap_rows - ((-row_offset) - (rows - 1)))
            else:
                self.change_focus((maxcol, maxrow), pos,
                                  row_offset, 'below',
                                  (self.pref_col, pref_row), snap_rows)

            # if we're as far up as we can scroll, take this one
            if (fill_above and self.body.get_prev(fill_above[-1][1])
                    == (None, None)):
                pass  # return

            # find out where that actually puts us
            middle, top, bottom = self.calculate_visible(
                (maxcol, maxrow), True)
            act_row_offset, _ign1, _ign2, _ign3, _ign4 = middle

            # discard chosen widget if it will reduce scroll amount
            # because of a fixed cursor (absolute last resort)
            if act_row_offset > row_offset + snap_rows:
                bad_choices.append(i)
                continue
            if act_row_offset < row_offset:
                bad_choices.append(i)
                continue

            # also discard if off top edge (second last resort)
            if act_row_offset < 0:
                bad_choices.append(i)
                cut_off_selectable_chosen = 1
                continue

            return

        # anything selectable is better than what follows:
        if cut_off_selectable_chosen:
            return

        if fill_above and focus_widget.selectable():
            # if we're at the top and have a selectable, return
            if self.body.get_prev(fill_above[-1][1]) == (None, None):
                pass  # return

        # if still none found choose the topmost widget
        good_choices = [j for j in search_order if j not in bad_choices]
        for i in good_choices + search_order:
            row_offset, widget, pos, rows = t[i]
            if pos == focus_pos:
                continue

            if not rows:  # never focus a 0-height widget
                continue

            # if completely within snap region, adjust row_offset
            if rows + row_offset <= 0:
                snap_rows -= (-row_offset) - (rows - 1)
                row_offset = -(rows - 1)

            self.change_focus((maxcol, maxrow), pos,
                              row_offset, 'below', None,
                              snap_rows)
            return

        # no choices available, just shift current one
        self.shift_focus((maxcol, maxrow), min(maxrow - 1, row_offset))

        # final check for pathological case where we may fall short
        middle, top, bottom = self.calculate_visible(
            (maxcol, maxrow), True)
        act_row_offset, _ign1, pos, _ign2, _ign3 = middle
        if act_row_offset >= row_offset:
            # no problem
            return

        # fell short, try to select anything else above
        if not t:
            return
        _ign1, _ign2, pos, _ign3 = t[-1]
        widget, pos = self.body.get_prev(pos)
        if widget is None:
            # no dice, we're stuck here
            return
        # bring in only one row if possible
        rows = widget.rows((maxcol,), True)
        self.change_focus((maxcol, maxrow), pos, -(rows - 1),
                          'below', (self.pref_col, rows - 1), 0)

    def _keypress_page_down(self, size):
        (maxcol, maxrow) = size

        middle, top, bottom = self.calculate_visible(
            (maxcol, maxrow), True)
        if middle is None:
            return 'page down'

        row_offset, focus_widget, focus_pos, focus_rows, cursor = middle
        trim_bottom, fill_below = bottom

        # bottom_edge is maxrow-focus_pos rows below top row of focus
        bottom_edge = maxrow - row_offset

        # scroll_from_row is (first match)
        # 1. bottom edge if focus is not selectable
        # 2. row containing cursor + 1 if focus has a cursor
        # 3. bottom edge of focus widget if it is visible
        # 4. bottom edge otherwise
        if not focus_widget.selectable():
            scroll_from_row = bottom_edge
        elif cursor is not None:
            x, y = cursor
            scroll_from_row = y + 1
        elif bottom_edge >= focus_rows:
            scroll_from_row = focus_rows
        else:
            scroll_from_row = bottom_edge

        # snap_rows is maximum extra rows to scroll when
        # snapping to new a focus
        snap_rows = bottom_edge - scroll_from_row

        # move row_offset to the new desired value (1 "page" down)
        row_offset = -scroll_from_row

        # not used below:
        scroll_from_row = bottom_edge = None

        # gather potential target widgets
        t = []
        # add current focus
        t.append((row_offset, focus_widget, focus_pos, focus_rows))
        pos = focus_pos
        row_offset += focus_rows
        # include widgets from calculate_visible(..)
        for widget, pos, rows in fill_below:
            t.append((row_offset, widget, pos, rows))
            row_offset += rows
        # add newly visible ones, including within snap_rows
        snap_region_start = len(t)
        while row_offset < maxrow + snap_rows:
            widget, pos = self.body.get_next(pos)
            if widget is None:
                break
            rows = widget.rows((maxcol,))
            t.append((row_offset, widget, pos, rows))
            row_offset += rows
            # determine if one above puts current one into snap rgn
            if row_offset < maxrow:
                snap_region_start += 1

        # if we can't fill the bottom we need to adjust the row offsets
        row_offset, w, p, rows = t[-1]
        if row_offset + rows < maxrow:
            adjust = maxrow - (row_offset + rows)
            t = [(ro + adjust, w, p, r) for (ro, w, p, r) in t]

        # if focus_widget (first in t) is off edge, remove it
        row_offset, w, p, rows = t[0]
        if row_offset + rows <= 0:
            del t[0]
            snap_region_start -= 1

        # we'll need this soon
        self.update_pref_col_from_focus((maxcol, maxrow))

        # choose the bottommost selectable and (newly) visible widget
        # search within snap_rows then visible region
        search_order = (range(snap_region_start, len(t))
                        + range(snap_region_start - 1, -1, -1))
        #assert 0, repr((t, search_order))
        bad_choices = []
        cut_off_selectable_chosen = 0
        for i in search_order:
            row_offset, widget, pos, rows = t[i]
            if not widget.selectable():
                continue

            if not rows:
                continue

            # try selecting this widget
            pref_row = min(maxrow - row_offset - 1, rows - 1)

            # if completely within snap region, adjust row_offset
            if row_offset >= maxrow:
                self.change_focus((maxcol, maxrow), pos,
                                  maxrow - 1, 'above',
                                  (self.pref_col, 0),
                                  snap_rows + maxrow - row_offset - 1)
            else:
                self.change_focus((maxcol, maxrow), pos,
                                  row_offset, 'above',
                                  (self.pref_col, pref_row), snap_rows)

            # find out where that actually puts us
            middle, top, bottom = self.calculate_visible(
                (maxcol, maxrow), True)
            act_row_offset, _ign1, _ign2, _ign3, _ign4 = middle

            # discard chosen widget if it will reduce scroll amount
            # because of a fixed cursor (absolute last resort)
            if act_row_offset < row_offset - snap_rows:
                bad_choices.append(i)
                continue
            if act_row_offset > row_offset:
                bad_choices.append(i)
                continue

            # also discard if off top edge (second last resort)
            if act_row_offset + rows > maxrow:
                bad_choices.append(i)
                cut_off_selectable_chosen = 1
                continue

            return

        # anything selectable is better than what follows:
        if cut_off_selectable_chosen:
            return

        # if still none found choose the bottommost widget
        good_choices = [j for j in search_order if j not in bad_choices]
        for i in good_choices + search_order:
            row_offset, widget, pos, rows = t[i]
            if pos == focus_pos:
                continue

            if not rows:  # never focus a 0-height widget
                continue

            # if completely within snap region, adjust row_offset
            if row_offset >= maxrow:
                snap_rows -= snap_rows + maxrow - row_offset - 1
                row_offset = maxrow - 1

            self.change_focus((maxcol, maxrow), pos,
                              row_offset, 'above', None,
                              snap_rows)
            return

        # no choices available, just shift current one
        self.shift_focus((maxcol, maxrow), max(1 - focus_rows, row_offset))

        # final check for pathological case where we may fall short
        middle, top, bottom = self.calculate_visible(
            (maxcol, maxrow), True)
        act_row_offset, _ign1, pos, _ign2, _ign3 = middle
        if act_row_offset <= row_offset:
            # no problem
            return

        # fell short, try to select anything else below
        if not t:
            return
        _ign1, _ign2, pos, _ign3 = t[-1]
        widget, pos = self.body.get_next(pos)
        if widget is None:
            # no dice, we're stuck here
            return
        # bring in only one row if possible
        rows = widget.rows((maxcol,), True)
        self.change_focus((maxcol, maxrow), pos, maxrow - 1,
                          'above', (self.pref_col, 0), 0)

    def mouse_event(self, size, event, button, col, row, focus):
        """
        Pass the event to the contained widgets.
        May change focus on button 1 press.
        """
        (maxcol, maxrow) = size
        middle, top, bottom = self.calculate_visible((maxcol, maxrow),
                                                     focus=True)
        if middle is None:
            return False

        _ignore, focus_widget, focus_pos, focus_rows, cursor = middle
        trim_top, fill_above = top
        _ignore, fill_below = bottom

        fill_above.reverse()  # fill_above is in bottom-up order
        w_list = (fill_above +
                  [(focus_widget, focus_pos, focus_rows)] +
                  fill_below)

        wrow = -trim_top
        for w, w_pos, w_rows in w_list:
            if wrow + w_rows > row:
                break
            wrow += w_rows
        else:
            return False

        focus = focus and w == focus_widget
        if is_mouse_press(event) and button == 1:
            if w.selectable():
                self.change_focus((maxcol, maxrow), w_pos, wrow)

        if not hasattr(w, 'mouse_event'):
            return False

        return w.mouse_event((maxcol,), event, button, col, row - wrow,
                             focus)

    def ends_visible(self, size, focus=False):
        """Return a list that may contain 'top' and/or 'bottom'.

        convenience function for checking whether the top and bottom
        of the list are visible
        """
        (maxcol, maxrow) = size
        l = []
        middle, top, bottom = self.calculate_visible((maxcol, maxrow),
                                                     focus=focus)
        if middle is None:  # empty listbox
            return ['top', 'bottom']
        trim_top, above = top
        trim_bottom, below = bottom

        if trim_bottom == 0:
            row_offset, w, pos, rows, c = middle
            row_offset += rows
            for w, pos, rows in below:
                row_offset += rows
            if row_offset < maxrow:
                l.append('bottom')
            elif self.body.get_next(pos) == (None, None):
                l.append('bottom')

        if trim_top == 0:
            row_offset, w, pos, rows, c = middle
            for w, pos, rows in above:
                row_offset -= rows
            if self.body.get_prev(pos) == (None, None):
                l.append('top')

        return l

    def __iter__(self):
        """
        Return an iterator over the positions in this ListBox.

        If self.body does not implement positions() then iterate
        from the focus widget down to the bottom, then from above
        the focus up to the top.  This is the best we can do with
        a minimal list walker implementation.
        """
        positions_fn = getattr(self.body, 'positions', None)
        if positions_fn:
            for pos in positions_fn():
                yield pos
            return

        focus_widget, focus_pos = self.body.get_focus()
        if focus_widget is None:
            return
        pos = focus_pos
        while True:
            yield pos
            w, pos = self.body.get_next(pos)
            if not w:
                break
        pos = focus_pos
        while True:
            w, pos = self.body.get_prev(pos)
            if not w:
                break
            yield pos

    def __reversed__(self):
        """
        Return a reversed iterator over the positions in this ListBox.

        If self.body does not implement positions() then iterate
        from above the focus widget up to the top, then from the focus
        widget down to the bottom.  Note that this is not actually the
        reverse of what __iter__() produces, but this is the best we can
        do with a minimal list walker implementation.
        """
        positions_fn = getattr(self.body, 'positions', None)
        if positions_fn:
            for pos in positions_fn(reverse=True):
                yield pos
            return

        focus_widget, focus_pos = self.body.get_focus()
        if focus_widget is None:
            return
        pos = focus_pos
        while True:
            w, pos = self.body.get_prev(pos)
            if not w:
                break
            yield pos
        pos = focus_pos
        while True:
            yield pos
            w, pos = self.body.get_next(pos)
            if not w:
                break


#Thread buffer
#class ThreadBuffer(Buffer):
#    """displays a thread as a tree of messages"""
#
#    modename = 'thread'
#
#    def __init__(self, ui, thread):
#        self.message_count = thread.get_total_messages()
#        self.thread = thread
#        self.rebuild()
#        Buffer.__init__(self, ui, self.body)
#
#    def __str__(self):
#        return '[thread] %s (%d message%s)' % (self.thread.get_subject(),
#                                               self.message_count,
#                                               's' * (self.message_count > 1))
#
#    def get_info(self):
#        info = {}
#        info['subject'] = self.thread.get_subject()
#        info['authors'] = self.thread.get_authors_string()
#        info['tid'] = self.thread.get_thread_id()
#        info['message_count'] = self.message_count
#        return info
#
#    def get_selected_thread(self):
#        """returns the displayed :class:`~alot.db.Thread`"""
#        return self.thread
#
#    def _build_pile(self, acc, msg, parent, depth):
#        acc.append((parent, depth, msg))
#        for reply in self.thread.get_replies_to(msg):
#            self._build_pile(acc, reply, msg, depth + 1)
#
#    def rebuild(self):
#        try:
#            self.thread.refresh()
#        except NonexistantObjectError:
#            self.body = urwid.SolidFill()
#            self.message_count = 0
#            return
#        # depth-first traversing the thread-tree, thereby
#        # 1) build a list of tuples (parentmsg, depth, message) in DF order
#        # 2) create a dict that counts no. of direct replies per message
#        messages = list()  # accumulator for 1,
#        childcount = {None: 0}  # accumulator for 2)
#        for msg, replies in self.thread.get_messages().items():
#            childcount[msg] = len(replies)
#        # start with all toplevel msgs, then recursively call _build_pile
#        for msg in self.thread.get_toplevel_messages():
#            self._build_pile(messages, msg, None, 0)
#            childcount[None] += 1
#
#        # go through list from 1) and pile up message widgets for all msgs.
#        # each one will be given its depth, if siblings follow and where to
#        # draw bars (siblings follow at lower depths)
#        msglines = list()
#        bars = []
#        for (num, (p, depth, m)) in enumerate(messages):
#            bars = bars[:depth]
#            childcount[p] -= 1
#
#            bars.append(childcount[p] > 0)
#            mwidget = MessageWidget(m, even=(num % 2 == 0),
#                                    depth=depth,
#                                    bars_at=bars)
#            msglines.append(mwidget)
#
#        self.body = urwid.ListBox(urwid.SimpleListWalker(msglines))
#        self.message_count = self.thread.get_total_messages()
#
#    def get_selection(self):
#        """returns focussed :class:`~alot.widgets.MessageWidget`"""
#        (messagewidget, size) = self.body.get_focus()
#        return messagewidget
#
#    def get_messagewidgets(self):
#        """returns all message widgets contained in this list"""
#        return self.body.body.contents
#
#    def get_selected_message(self):
#        """returns focussed :class:`~alot.db.message.Message`"""
#        messagewidget = self.get_selection()
#        return messagewidget.get_message()
#
#    def get_message_widgets(self):
#        """
#        returns all :class:`MessageWidgets <alot.widgets.MessageWidget>`
#        displayed in this thread-tree.
#        """
#        return self.body.body.contents
#
#    def get_focus(self):
#        return self.body.get_focus()
#
#    def unfold_matching(self, querystring, focus_first=True):
#        """
#        unfolds messages that match a given querystring.
#
#        :param querystring: query to match
#        :type querystring: str
#        :param focus_first: set the focus to the first matching message
#        :type focus_first: bool
#        """
#        i = 0
#        for mw in self.get_message_widgets():
#            msg = mw.get_message()
#            if msg.matches(querystring):
#                if focus_first:
#                    # let urwid.ListBox focus this widget:
#                    # The first parameter is a "size" tuple: that needs only to
#                    # be iterable an is *never* used. i is the integer index
#                    # to focus. offset_inset is may be used to shift the visible area
#                    # so that the focus lies at given offset
#                    self.body.change_focus((0, 0), i,
#                                           offset_inset=0,
#                                           coming_from='above')
#                    focus_first = False
#                mw.folded = False
#                if 'unread' in msg.get_tags():
#                    msg.remove_tags(['unread'])
#                    self.ui.apply_command(commands.globals.FlushCommand())
#                mw.rebuild()
#            i = i + 1


class ListWalkerAdapter(urwid.ListWalker):
    def __init__(self, walker, indent=2,
                 arrow_hbar=u'\u2500',
                 arrow_vbar=u'\u2502',
                 arrow_tip=u'\u25b6',
                 arrow_connector_t=u'\u251c',
                 arrow_connector_l=u'\u2514'):
        self._walker = walker
        self._indent = indent
        self._arrow_hbar = arrow_hbar
        self._arrow_vbar = arrow_vbar
        self._arrow_connector_l = arrow_connector_l
        self._arrow_connector_t = arrow_connector_t
        self._arrow_tip = arrow_tip
        self._cache = {}

    def get_focus(self):
        widget, position = self._walker.get_focus()
        return self[position], position

    def set_focus(self, pos):
        return self._walker.set_focus(pos)

    def next_position(self, pos):
        return self._walker.next_position(pos)

    def prev_position(self, pos):
        return self._walker.prev_position(pos)

    def __getitem__(self, pos):
        candidate = None
        if pos in self._cache:
            candidate = self._cache[pos]
        else:
            candidate = self._construct_line(pos)
            self._cache[pos] = candidate
        return candidate

    def _construct_spacer(self, pos, acc):
        parent = self._walker.parent_position(pos)
        if parent is not None:
            grandparent = self._walker.parent_position(parent)
            if self._indent > 0 and grandparent is not None:
                parent_sib = self._walker.next_sibbling_position(parent)
                bar_width = self._indent - 1 * (parent_sib is not None)
                if bar_width > 0:
                    void = urwid.SolidFill(' ')
                    acc.insert(0, ((bar_width, void)))
                if parent_sib is not None:
                    bar = urwid.SolidFill(self._arrow_vbar)
                    acc.insert(0, ((1, bar)))
            return self._construct_spacer(parent, acc)
        else:
            return acc

    def _construct_line(self, pos):
        line = None
        if pos is not None:
            original_widget = self._walker[pos]
            cols = self._construct_spacer(pos, [])
            parent = self._walker.parent_position(pos)
            if self._indent > 0 and parent is not None:
                void = urwid.Text(' ')
                if self._walker.next_sibbling_position(pos) is not None:
                    # add t-connector
                    connector = urwid.SolidFill(self._arrow_connector_t)
                    bar = urwid.SolidFill('Y')
                    hb_spacer = urwid.Pile([(1, connector), bar])
                    cols.append((1, hb_spacer))
                else:
                    connector = urwid.SolidFill(self._arrow_connector_l)
                    hb_spacer = urwid.Pile([(1, connector), void])
                    cols.append((1, hb_spacer))
                # build bar spacer
                if self._indent > 1:
                    void = urwid.Text('Y')
                    bar = urwid.SolidFill(self._arrow_hbar)
                    hb_spacer = urwid.Pile([(1, bar), void])
                    cols.append((self._indent - 1, hb_spacer))
                #arrow tip
                arrow_tip = urwid.SolidFill(self._arrow_tip)
                hb_spacer = urwid.Pile([(1, arrow_tip), void])
                cols.append((1, hb_spacer))

            cols.append(original_widget)
            line = urwid.Columns(cols, box_columns=range(len(cols))[:-1])
        return line


class TreeBox(urwid.WidgetWrap):
    """A widget representing something in a nested tree display."""
    _selectable = True
    #unexpanded_icon = SelectableIcon('+', 0)
    #expanded_icon = SelectableIcon('-', 0)

    def __init__(self, walker, **kwargs):
        self._walker = walker
        self._adapter = ListWalkerAdapter(walker, **kwargs)
        self._outer_list = urwid.ListBox(self._adapter)
        self.__super.__init__(self._outer_list)

    def get_focus(self):
        return self._outer_list.get_focus()
