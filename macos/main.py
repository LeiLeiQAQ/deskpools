"""AppKit desktop frontend for the shared Desktop Icon Billiards engine."""
from __future__ import annotations

import io
import math
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from types import SimpleNamespace

import AppKit as A
import Foundation as F
import objc
import Quartz as Q
from PIL import Image
from PyObjCTools import AppHelper

from dib.art import circular_icon_art, table_art, white_art
from dib.config import PRODUCT_NAME
from dib.modes import CHALLENGE_BALLS, CUE_SKINS, MODES, SPRINT_MS
from dib.sound import ImpactSounds
from dib.table import BilliardsMotion, MIN_SHOT_CHARGE

from hotkey import PauseHotkey

CHARGE_SECONDS = 1.35
SCALE = 2
FPS = 60
PAUSE_LABEL = 'F1 / fn-F1 暂停或恢复'
MODE_LABELS = {name: info[0] for name, info in MODES.items()}
MODE_LABELS['standard'] = '标准 15 球'
CUE_COLORS = {
    'wood': ((.93, .75, .49), (.53, .31, .18)),
    'jade': ((.67, .97, .83), (.11, .47, .34)),
    'midnight': ((.70, .83, 1), (.16, .26, .50)),
}


def color(red, green, blue, alpha=1.):
    return A.NSColor.colorWithSRGBRed_green_blue_alpha_(red, green, blue, alpha)


def native_image(frame, scale=1):
    png = io.BytesIO()
    frame.save(png, format='PNG')
    data = png.getvalue()
    result = A.NSImage.alloc().initWithData_(F.NSData.dataWithBytes_length_(data, len(data)))
    if result is None:
        raise ValueError('图像无法解码')
    result.setSize_((frame.width / scale, frame.height / scale))
    return result


def desktop_icons():
    desktop = Path(F.NSSearchPathForDirectoriesInDomains(F.NSDesktopDirectory, F.NSUserDomainMask, True)[0])
    entries = sorted((path for path in desktop.iterdir() if not path.name.startswith('.')),
                     key=lambda path: (path.name.casefold(), path.name))
    if not entries:
        raise ValueError('桌面上没有可用图标。请先放入文件、文件夹或应用替身。')
    return entries[:45]


def load_icon(path):
    native = A.NSWorkspace.sharedWorkspace().iconForFile_(str(path))
    data = native.TIFFRepresentation()
    if data is None:
        raise ValueError(f'读取桌面图标失败：{path.name}')
    with Image.open(io.BytesIO(bytes(data))) as icon:
        # NSImage's TIFF can carry multiple representations of the same icon.
        best, side = 0, 0
        for index in range(icon.n_frames):
            icon.seek(index)
            size = min(icon.size)
            if size > side:
                best, side = index, size
        icon.seek(best)
        return icon.convert('RGBA')


def draw_image(image, rect, alpha=1.):
    image.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
        F.NSMakeRect(*rect), F.NSZeroRect, A.NSCompositingOperationSourceOver, alpha, True, None)


def circle(x, y, radius):
    return A.NSBezierPath.bezierPathWithOvalInRect_(F.NSMakeRect(x - radius, y - radius, radius * 2, radius * 2))


def line(start, end, stroke, width, dashed=False):
    path = A.NSBezierPath.bezierPath()
    path.moveToPoint_(start)
    path.lineToPoint_(end)
    path.setLineWidth_(width)
    if dashed:
        path.setLineDash_count_phase_((7., 5.), 2, 0.)
    stroke.setStroke()
    path.stroke()


class DesktopWindow(A.NSWindow):
    def canBecomeKeyWindow(self):
        return True


class GameView(A.NSView):
    def isFlipped(self):
        return True

    def acceptsFirstResponder(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    @objc.python_method
    def configure(self, owner, entries, mode):
        self.owner = owner
        self.entries = entries
        self.mode = mode
        self.charging_at = None
        self.previous_tick = monotonic()
        self.visible_table = True
        self.game = None
        self.rebuild()

    @objc.python_method
    def rebuild(self):
        size = self.bounds().size
        count = len(self.entries) if self.mode == 'practice' else CHALLENGE_BALLS
        game = BilliardsMotion((0, 0, int(size.width), int(size.height)), [(1, 1)] * count, mode=self.mode)
        ball_side = max(1, round(game.tile * SCALE))
        art = {}
        sprites = []
        for index in range(count):
            path = self.entries[index % len(self.entries)]
            if path not in art:
                art[path] = native_image(circular_icon_art(load_icon(path), ball_side), SCALE)
            sprites.append(art[path])
        felt, left, top = table_art(game)
        self.background = native_image(felt), (left, top, *felt.size)
        self.white = native_image(white_art(game.white_radius * SCALE, shaded=True), SCALE)
        self.game, self.sprites = game, sprites
        self.previous_tick = monotonic()
        self.cancel_charge()

    @objc.python_method
    def cancel_charge(self):
        self.charging_at = None
        if self.game is not None:
            self.game.charge = 0.
        self.setNeedsDisplay_(True)

    @objc.python_method
    def advance(self, now):
        game = self.game
        state, seconds = game.state, game.round_seconds
        moving = state in ('stroke', 'rolling') or bool(game.exits)
        if self.charging_at is not None:
            game.charge = min(1., (now - self.charging_at) / CHARGE_SECONDS)
        game.step(now - self.previous_tick, now)
        self.previous_tick = now
        self.owner.sounds.play(game.consume_sound_events(), now, pockets=game.consume_pocket_events())
        if game.state == 'finished' and self.charging_at is not None:
            self.cancel_charge()
        elif moving or self.charging_at is not None or state != game.state or seconds != game.round_seconds:
            self.setNeedsDisplay_(True)

    def drawRect_(self, dirty):
        # Clear previous positions on the transparent surface before redrawing.
        A.NSColor.clearColor().set()
        A.NSRectFillUsingOperation(dirty, A.NSCompositingOperationCopy)
        game = getattr(self, 'game', None)
        if game is None:
            return

        if self.visible_table:
            draw_image(*self.background)
        else:
            for x, y in game.pockets:
                color(.03, .09, .08, .74).setFill()
                circle(x, y, game.pocket_radius).fill()

        if game.state == 'ready' and (self.visible_table or A.NSEvent.modifierFlags() & A.NSEventModifierFlagShift):
            self.draw_guide()
        if self.visible_table or A.NSEvent.modifierFlags() & A.NSEventModifierFlagShift:
            self.draw_cue()

        for index, (body, sprite) in enumerate(zip(game.icons, self.sprites), 1):
            if index not in game.sunk:
                side = game.tile
                draw_image(sprite, (body.x - side / 2, body.y - side / 2, side, side))
            elif exit_state := game.exit_visual(index):
                x, y, fade, scale, _ = exit_state
                side = game.tile * scale
                draw_image(sprite, (x - side / 2, y - side / 2, side, side), 1 - fade)
        if not game.white_sunk:
            size = self.white.size()
            draw_image(self.white, (game.white.x - size.width / 2, game.white.y - size.height / 2,
                                    size.width, size.height))
        self.draw_status()

    @objc.python_method
    def draw_guide(self):
        colors = {'incoming': color(.92, 1., .94, .9), 'white': color(.97, .98, .99, .65),
                  'target': color(.42, 1., .73, .85)}
        lines, circles = self.game.aim_preview()
        for role, start, end in lines:
            line(start, end, color(.04, .10, .09, .84), 3.5, True)
            line(start, end, colors[role], 1.5, True)
        for role, (x, y), radius in circles:
            path = circle(x, y, radius)
            colors['white' if role == 'ghost' else 'target'].setStroke()
            path.setLineWidth_(1.4)
            path.stroke()

    @objc.python_method
    def draw_cue(self):
        cue = self.game.cue()
        if cue is None:
            return
        tip, tail = cue
        grip = tuple(a + (b - a) * .37 for a, b in zip(tail, tip))
        shaft, handle = CUE_COLORS[self.owner.cue_skin]
        line(tail, tip, color(.12, .09, .08, .92), 7)
        line(tail, tip, color(*shaft), 4)
        line(tail, grip, color(*handle), 5)
        color(.49, .81, .89).setFill()
        circle(*tip, 2).fill()

    @objc.python_method
    def draw_status(self):
        game = self.game
        total = len(game.icons)
        remaining = total - game.pocketed_icons
        clock = game.round_seconds
        if game.mode == 'sprint':
            clock = math.ceil(max(0, SPRINT_MS - game.play_ms) / 1000)
        status = f'{MODE_LABELS[game.mode]}   剩余 {remaining}/{total}   {clock // 60:02}:{clock % 60:02}   {game.shots} 杆'
        if game.state == 'finished':
            result = '挑战结束' if game.finish_reason == 'timeout' else '清台完成'
            status = f'{result}   {status}   R 重开'
        shortcut = f'{PAUSE_LABEL if self.owner.hotkey else "菜单栏暂停或恢复"}  ·  Esc 关闭球台'
        attributes = {
            A.NSFontAttributeName: A.NSFont.systemFontOfSize_weight_(12, A.NSFontWeightMedium),
            A.NSForegroundColorAttributeName: color(.95, 1., .98),
        }
        title = F.NSAttributedString.alloc().initWithString_attributes_(status, attributes)
        help_text = F.NSAttributedString.alloc().initWithString_attributes_(shortcut, attributes)
        width = max(text.size().width for text in (title, help_text)) + 24
        y = 8
        bar = A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(F.NSMakeRect(12, y, width, 44), 8, 8)
        color(.04, .11, .09, .95).setFill()
        bar.fill()
        title.drawAtPoint_((24, y + 5))
        help_text.drawAtPoint_((24, y + 22))
        if self.charging_at is not None and game.charge > 0:
            color(.32, .93, .67).setFill()
            A.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                F.NSMakeRect(12, y + 44, width * game.charge, 3), 1.5, 1.5).fill()

    @objc.python_method
    def aim(self, event):
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        self.game.aim_at(point.x, point.y)
        return point

    def mouseMoved_(self, event):
        if self.game.state == 'ready':
            self.aim(event)
            self.setNeedsDisplay_(True)

    def mouseDragged_(self, event):
        self.mouseMoved_(event)

    def flagsChanged_(self, event):
        if not self.visible_table:
            self.setNeedsDisplay_(True)

    def mouseDown_(self, event):
        point = self.aim(event)
        left, top, right, bottom = self.game.bounds
        if not (left <= point.x <= right and top <= point.y <= bottom):
            return
        if self.game.state == 'ball_in_hand':
            self.game.place_white(point.x, point.y)
            self.setNeedsDisplay_(True)
        elif self.game.state == 'ready':
            self.charging_at = monotonic()

    def mouseUp_(self, event):
        if self.charging_at is None:
            return
        now = monotonic()
        self.aim(event)
        power = min(1., (now - self.charging_at) / CHARGE_SECONDS)
        self.charging_at = None
        self.game.shoot(max(MIN_SHOT_CHARGE, power), now)

    def rightMouseDown_(self, event):
        self.cancel_charge()

    def keyDown_(self, event):
        if event.isARepeat():
            return
        if event.keyCode() == 53:
            self.owner.closeTable_(None)
        elif event.keyCode() == 122 and self.owner.hotkey is None:
            self.owner.togglePause_(None)
        elif (event.charactersIgnoringModifiers() or '').lower() == 'r':
            self.cancel_charge()
            self.game.reset()
        else:
            objc.super(GameView, self).keyDown_(event)


class GameController(F.NSObject):
    def applicationDidFinishLaunching_(self, notification):
        self.window = self.view = self.timer = self.hotkey = None
        self.paused = True
        self.cue_skin = 'wood'
        self.visible_table = True
        self.sounds = ImpactSounds()
        self.install_menu()
        try:
            self.hotkey = PauseHotkey(lambda: self.togglePause_(None))
        except OSError as error:
            F.NSLog('DeskPools: %@', str(error))
            self.status.button().setToolTip_('F1 注册失败。请使用菜单栏控制暂停和恢复。')
        self.open_mode('practice')

    @objc.python_method
    def install_menu(self):
        self.status = A.NSStatusBar.systemStatusBar().statusItemWithLength_(A.NSVariableStatusItemLength)
        self.status.button().setTitle_('🎱')
        self.status.button().setToolTip_(PRODUCT_NAME)
        self.menu = A.NSMenu.alloc().init()
        self.pause_item = self.add_menu('显示球台', 'togglePause:')
        self.menu.addItem_(A.NSMenuItem.separatorItem())
        for mode, label in MODE_LABELS.items():
            self.add_menu(label, 'selectMode:', mode)
        self.menu.addItem_(A.NSMenuItem.separatorItem())
        for skin, label in CUE_SKINS.items():
            self.add_menu(f'球杆 · {label}', 'selectCue:', skin)
        self.felt_item = self.add_menu('显示球台背景', 'toggleFelt:')
        self.felt_item.setState_(A.NSControlStateValueOn)
        self.sound_item = self.add_menu('播放音效', 'toggleSound:')
        self.sound_item.setState_(A.NSControlStateValueOn)
        self.menu.addItem_(A.NSMenuItem.separatorItem())
        self.add_menu('关闭球台', 'closeTable:')
        self.add_menu('退出桌面图标台球', 'quit:', shortcut='q')
        self.status.setMenu_(self.menu)

    @objc.python_method
    def add_menu(self, title, action, value=None, shortcut=''):
        item = A.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, shortcut)
        item.setTarget_(self)
        item.setRepresentedObject_(value)
        self.menu.addItem_(item)
        return item

    @objc.python_method
    def open_mode(self, mode):
        try:
            screens = A.NSScreen.screens()
            if not screens:
                raise RuntimeError('未检测到显示器')
            frame = screens[0].visibleFrame()
            window = DesktopWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                frame, A.NSWindowStyleMaskBorderless, A.NSBackingStoreBuffered, False)
            window.setReleasedWhenClosed_(False)
            window.setOpaque_(False)
            window.setBackgroundColor_(A.NSColor.clearColor())
            window.setHasShadow_(False)
            window.setLevel_(Q.CGWindowLevelForKey(Q.kCGDesktopIconWindowLevelKey) + 1)
            window.setCollectionBehavior_(A.NSWindowCollectionBehaviorMoveToActiveSpace |
                                          A.NSWindowCollectionBehaviorFullScreenNone)
            window.setAcceptsMouseMovedEvents_(True)
            view = GameView.alloc().initWithFrame_(((0, 0), frame.size))
            view.configure(self, desktop_icons(), mode)
            view.visible_table = self.visible_table
            window.setContentView_(view)
            self.sounds.prepare()
        except (OSError, RuntimeError, ValueError) as error:
            self.pause_game()
            alert = A.NSAlert.alloc().init()
            alert.setMessageText_('球台启动失败')
            alert.setInformativeText_(str(error))
            A.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            alert.runModal()
            return
        self.closeTable_(None)
        self.window, self.view = window, view
        self.show_game()

    @objc.python_method
    def show_game(self):
        now = monotonic()
        self.view.previous_tick = now
        self.view.game.resume(now)
        self.paused = False
        self.pause_item.setTitle_('暂停并隐藏球台')
        A.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.window.makeKeyAndOrderFront_(None)
        self.window.makeFirstResponder_(self.view)
        self.timer = F.NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1 / FPS, self, 'tick:', None, True)
        F.NSRunLoop.mainRunLoop().addTimer_forMode_(self.timer, F.NSRunLoopCommonModes)

    @objc.python_method
    def pause_game(self):
        if self.paused:
            return
        self.paused = True
        self.timer.invalidate()
        self.timer = None
        self.view.cancel_charge()
        self.view.game.pause(monotonic())
        self.sounds.stop()
        self.window.orderOut_(None)
        self.pause_item.setTitle_('恢复球台')

    def tick_(self, timer):
        if not self.paused:
            self.view.advance(monotonic())

    def applicationDidResignActive_(self, notification):
        self.pause_game()

    def selectMode_(self, sender):
        self.open_mode(sender.representedObject())

    def selectCue_(self, sender):
        self.cue_skin = sender.representedObject()
        if self.view is not None:
            self.view.setNeedsDisplay_(True)

    def toggleFelt_(self, sender):
        self.visible_table = not self.visible_table
        sender.setState_(A.NSControlStateValueOn if self.visible_table else A.NSControlStateValueOff)
        if self.view is not None:
            self.view.visible_table = self.visible_table
            self.view.setNeedsDisplay_(True)

    def toggleSound_(self, sender):
        enabled = self.sounds.volume == 0
        try:
            self.sounds.set_volume(.45 if enabled else 0.)
        except RuntimeError as error:
            self.sounds.set_volume(0.)
            F.NSLog('DeskPools: %@', str(error))
            self.status.button().setToolTip_('声音设备初始化失败')
            return
        sender.setState_(A.NSControlStateValueOn if enabled else A.NSControlStateValueOff)

    def togglePause_(self, sender):
        if self.window is None:
            self.open_mode('practice')
        elif self.paused:
            self.show_game()
        else:
            self.pause_game()

    def closeTable_(self, sender):
        self.pause_game()
        if self.window is not None:
            self.window.close()
            self.window = self.view = None
        self.pause_item.setTitle_('显示球台')

    def quit_(self, sender):
        A.NSApplication.sharedApplication().terminate_(None)

    def applicationWillTerminate_(self, notification):
        self.closeTable_(None)
        self.sounds.close()
        if self.hotkey is not None:
            self.hotkey.close()


def main():
    application = A.NSApplication.sharedApplication()
    application.setActivationPolicy_(A.NSApplicationActivationPolicyAccessory)
    controller = GameController.alloc().init()
    application.setDelegate_(controller)
    AppHelper.runEventLoop()


def check_runtime():
    """Exercise the packaged AppKit view without accessing the runner's desktop files."""
    A.NSApplication.sharedApplication()
    assert Q.CGWindowLevelForKey(Q.kCGDesktopIconWindowLevelKey) < A.NSNormalWindowLevel
    sounds = ImpactSounds()
    frame = F.NSMakeRect(0, 0, 960, 600)
    window = DesktopWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        frame, A.NSWindowStyleMaskBorderless, A.NSBackingStoreBuffered, False)
    window.setReleasedWhenClosed_(False)
    try:
        sounds.prepare()
        with TemporaryDirectory() as folder:
            icon_path = Path(folder) / 'DeskPools.txt'
            icon_path.touch()
            owner = SimpleNamespace(cue_skin='wood', hotkey=None, sounds=sounds)
            view = GameView.alloc().initWithFrame_(frame)
            view.configure(owner, [icon_path], 'standard')
            window.setContentView_(view)
            game = view.game
            assert len(game.icons) == CHALLENGE_BALLS and view.sprites[0].isValid()

            def render():
                image = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
                view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), image)
                with Image.open(io.BytesIO(bytes(image.TIFFRepresentation()))) as capture:
                    return capture.convert('RGBA')

            before = render()
            colors = before.getcolors(maxcolors=64)
            assert colors is None, 'AppKit rendered a blank or incomplete board'

            def click(kind):
                point = view.convertPoint_toView_((game.icons[0].x, game.icons[0].y), None)
                return A.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                    kind, point, 0, monotonic(), window.windowNumber(), None, 1, 1, 1.)

            view.mouseDown_(click(A.NSEventTypeLeftMouseDown))
            assert view.charging_at is not None, 'Left mouse down failed to start charging'
            view.charging_at -= CHARGE_SECONDS
            view.mouseUp_(click(A.NSEventTypeLeftMouseUp))
            assert game.shots == 1 and game.state == 'stroke', 'Left mouse up failed to strike'

            opening_x = game.white.x
            start = view.previous_tick = monotonic()
            for step in range(1, 21):
                view.advance(start + step / FPS)
            assert game.white.x > opening_x, 'The cue ball did not advance'
            after = render()
            assert before.tobytes() != after.tobytes(), 'The rendered board did not update'

            sounds.stop()
            assert sounds.play((), pockets=1), 'Pocket audio did not start'
    finally:
        window.close()
        sounds.close()
    print('AppKit icon, table rendering, cue strike, motion and pocket audio checks passed.')


if __name__ == '__main__':
    if sys.argv[1:] == ['--check-runtime']:
        check_runtime()
    else:
        main()
