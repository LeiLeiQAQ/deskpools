"""Drawing helpers for the table, the balls, the pockets and the cue.

Every function returns Pillow ``RGBA`` images that are blitted as per-pixel
alpha layered windows by the shell.
"""
import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps


def circular_icon_art(icon, target_size=None):
    """Place a Shell thumbnail inside a complete, antialiased spherical ball sprite."""
    icon = icon.convert('RGBA')
    bounds = icon.getchannel('A').getbbox()
    if not bounds:
        raise ValueError('桌面图标没有可见像素')
    icon = icon.crop(bounds)
    side = max(1, int(target_size if target_size is not None else max(icon.size)))
    icon = ImageOps.fit(icon, (side, side), method=Image.Resampling.LANCZOS, centering=(.5, .5))

    # Supersampling keeps both the ball outline and inset logo smooth at Explorer icon sizes.
    scale = 4
    canvas = side * scale
    center = (side - 1) / 2
    radius = side * .48
    light = (-.48, .56, .68)
    pixels = bytearray(side * side * 4)

    # Per-pixel shading is O(side²); thumbnails are small and generated once before play.
    for y in range(side):
        ny = (center - y) / radius
        for x in range(side):
            nx = (x - center) / radius
            radial = nx * nx + ny * ny
            if radial > 1:
                continue
            nz = math.sqrt(max(0, 1 - radial))
            diffuse = max(0, nx * light[0] + ny * light[1] + nz * light[2])
            illumination = .4 + .6 * diffuse
            specular = diffuse ** 18 * 54
            offset = (y * side + x) * 4
            pixels[offset] = min(255, round(31 + 139 * illumination + specular))
            pixels[offset + 1] = min(255, round(48 + 155 * illumination + specular))
            pixels[offset + 2] = min(255, round(68 + 169 * illumination + specular))
            pixels[offset + 3] = 255

    ball = Image.frombytes('RGBA', (side, side), bytes(pixels))
    outer_mask = Image.new('L', (canvas, canvas))
    ImageDraw.Draw(outer_mask).ellipse((0, 0, canvas - 1, canvas - 1), fill=255)
    ball.putalpha(outer_mask.resize((side, side), Image.Resampling.LANCZOS))

    # The inset roundel preserves each desktop identity while leaving a visible ball rim.
    logo_size = max(1, round(side * .84))
    logo = ImageOps.fit(icon, (logo_size, logo_size), method=Image.Resampling.LANCZOS)
    # Shell thumbnails are commonly rendered from 32/48px icon resources even
    # when the requested canvas is 256px. A restrained unsharp mask restores
    # edge definition after that interpolation without creating halos.
    logo = logo.filter(ImageFilter.UnsharpMask(radius=max(.7, side / 160), percent=145, threshold=2))
    logo_mask = Image.new('L', (logo_size * scale, logo_size * scale))
    ImageDraw.Draw(logo_mask).ellipse((0, 0, logo_size * scale - 1, logo_size * scale - 1), fill=255)
    logo.putalpha(ImageChops.multiply(
        logo.getchannel('A'), logo_mask.resize((logo_size, logo_size), Image.Resampling.LANCZOS)))
    ball.alpha_composite(logo, ((side - logo_size) // 2, (side - logo_size) // 2))

    outline = Image.new('RGBA', (canvas, canvas))
    outline_draw = ImageDraw.Draw(outline)
    outline_draw.ellipse((1, 1, canvas - 2, canvas - 2),
                         outline=(21, 31, 46, 245), width=scale * 2)
    inset = scale * 2
    outline_draw.ellipse((inset, inset, canvas - inset - 1, canvas - inset - 1),
                         outline=(236, 247, 255, 160), width=scale)
    outline = outline.resize((side, side), Image.Resampling.LANCZOS)
    ball.alpha_composite(outline)
    return ball


def billiard_icon_art(size=512):
    """Render the original emerald 8-ball and wooden cue application mark."""
    size = max(16, int(size))
    scale = 4 if size <= 256 else 2
    canvas = size * scale
    cx, cy = canvas * .53, canvas * .455
    radius = canvas * .425
    light = (-.48, .56, .68)
    frame = Image.new('RGBA', (canvas, canvas))

    def box(left, top, right, bottom):
        return tuple(value * canvas for value in (left, top, right, bottom))

    shadow = Image.new('RGBA', frame.size)
    ImageDraw.Draw(shadow).ellipse(box(.20, .78, .91, .91), fill=(2, 30, 24, 65))
    frame.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(canvas * .018)))
    draw = ImageDraw.Draw(frame)
    draw.polygon([(x * canvas, y * canvas) for x, y in ((.055, .924), (.092, .961), (.322, .695), (.302, .675))], fill=(45, 37, 25, 255))
    draw.polygon([(x * canvas, y * canvas) for x, y in ((.064, .925), (.091, .952), (.315, .694), (.301, .682))], fill=(230, 178, 93, 255))
    draw.line((.078 * canvas, .934 * canvas, .306 * canvas, .688 * canvas), fill=(255, 225, 165, 255), width=max(1, round(canvas * .008)))
    draw.line((.076 * canvas, .938 * canvas, .150 * canvas, .854 * canvas), fill=(91, 51, 32, 255), width=max(1, round(canvas * .025)))

    pixels = bytearray(canvas * canvas * 4)
    for y in range(canvas):
        ny = (cy - y) / radius
        for x in range(canvas):
            nx = (x - cx) / radius
            radial = nx * nx + ny * ny
            if radial > 1:
                continue
            nz = math.sqrt(max(0, 1 - radial))
            diffuse = max(0, nx * light[0] + ny * light[1] + nz * light[2])
            specular = diffuse ** 34
            rim = (1 - nz) ** 5
            offset = (y * canvas + x) * 4
            pixels[offset] = min(255, round(5 + diffuse * 18 + specular * 130 + rim * 12))
            pixels[offset + 1] = min(255, round(36 + diffuse * 126 + specular * 80 + rim * 22))
            pixels[offset + 2] = min(255, round(40 + diffuse * 95 + specular * 92 + rim * 20))
            pixels[offset + 3] = 255
    frame.alpha_composite(Image.frombytes('RGBA', (canvas, canvas), bytes(pixels)))
    draw = ImageDraw.Draw(frame)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=(130, 221, 190, 255), width=max(1, round(canvas * .006)))
    badge, ink = (255, 249, 231, 255), (5, 44, 39, 255)
    draw.ellipse(box(.337, .270, .753, .686), fill=(4, 35, 29, 255))
    draw.ellipse(box(.340, .275, .740, .675), fill=badge)
    draw.ellipse(box(.467, .353, .613, .503), fill=ink)
    draw.ellipse(box(.454, .441, .626, .607), fill=ink)
    counter = .004 if size <= 24 else 0
    draw.ellipse(box(.512 - counter, .397 - counter, .568 + counter, .459 + counter), fill=badge)
    draw.ellipse(box(.506 - counter, .488 - counter, .574 + counter, .560 + counter), fill=badge)
    frame = frame.resize((size, size), Image.Resampling.LANCZOS)
    frame.putalpha(frame.getchannel('A').point(lambda alpha: 0 if alpha < 3 else alpha))
    return frame


def _monitor_icon_art(size=512):
    """Render a slim glass monitor with a polished eight-ball in its display."""
    size = max(16, int(size))
    scale = 4 if size <= 256 else 2
    canvas = size * scale
    frame = Image.new('RGBA', (canvas, canvas))

    def box(left, top, right, bottom):
        return tuple(value * canvas for value in (left, top, right, bottom))

    def rounded_gradient(target, bounds, radius, top, bottom):
        left, upper, right, lower = (round(value * canvas) for value in bounds)
        dimensions = (right - left + 1, lower - upper + 1)
        ramp = Image.linear_gradient('L').resize(dimensions)
        gradient = ImageOps.colorize(ramp, top, bottom).convert('RGBA')
        mask = Image.new('L', dimensions)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, dimensions[0] - 1, dimensions[1] - 1),
                                               radius=round(radius * canvas), fill=255)
        target.paste(gradient, (left, upper), mask)

    def metal_polygon(points, light, dark):
        vertices = [(round(x * canvas), round(y * canvas)) for x, y in points]
        left, top = min(p[0] for p in vertices), min(p[1] for p in vertices)
        width = max(p[0] for p in vertices) - left + 1
        height = max(p[1] for p in vertices) - top + 1
        mask = Image.new('L', (width, height))
        ImageDraw.Draw(mask).polygon([(x - left, y - top) for x, y in vertices], fill=255)
        ramp = Image.linear_gradient('L').rotate(90).resize(mask.size)
        metal = ImageOps.colorize(ramp, light, dark).convert('RGBA')
        frame.paste(metal, (left, top), mask)

    # A shallow aluminium foot replaces the bulky pedestal. The contact shadow
    # stays local so the icon also has a clean silhouette on dark taskbars.
    shadow = Image.new('RGBA', frame.size)
    ImageDraw.Draw(shadow).ellipse(box(.268, .904, .744, .950), fill=(4, 18, 31, 65))
    frame.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(canvas * .012)))
    metal_polygon(((.451, .716), (.539, .711), (.572, .895), (.425, .898)),
                  (204, 229, 237), (63, 102, 128))
    metal_polygon(((.469, .743), (.491, .741), (.479, .887), (.447, .888)),
                  (220, 242, 245), (119, 166, 185))
    rounded_gradient(frame, (.302, .888, .706, .933), .022, (109, 153, 176), (32, 66, 91))
    rounded_gradient(frame, (.307, .877, .701, .916), .020, (223, 243, 247), (115, 163, 185))

    # Draw the front plane at full supersampled resolution, then give the whole
    # display a restrained tilt. A single transform keeps every bezel parallel.
    face = Image.new('RGBA', (canvas, round(canvas * .68)))
    rounded_gradient(face, (0, 0, 1, .68), .052, (210, 233, 242), (61, 101, 128))
    rounded_gradient(face, (.006, .006, .994, .674), .048, (42, 65, 85), (17, 35, 52))
    screen_bounds = (.025, .027, .975, .622)
    rounded_gradient(face, screen_bounds, .035, (8, 124, 112), (3, 45, 62))
    screen_mask = Image.new('L', face.size)
    ImageDraw.Draw(screen_mask).rounded_rectangle(box(*screen_bounds), radius=round(canvas * .035), fill=255)

    def screen_glow(bounds, color, blur):
        glow = Image.new('RGBA', face.size)
        ImageDraw.Draw(glow).ellipse(box(*bounds), fill=color)
        glow = glow.filter(ImageFilter.GaussianBlur(canvas * blur))
        glow.putalpha(ImageChops.multiply(glow.getchannel('A'), screen_mask))
        face.alpha_composite(glow)

    # Broad, soft lighting gives the glass depth without a hard reflection slash.
    screen_glow((-.16, -.25, .85, .49), (20, 243, 172, 145), .125)
    screen_glow((.65, .27, 1.22, .84), (16, 116, 191, 105), .115)
    shadow = Image.new('RGBA', face.size)
    ImageDraw.Draw(shadow).ellipse(box(.273, .553, .827, .611), fill=(0, 12, 23, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(canvas * .015))
    shadow.putalpha(ImageChops.multiply(shadow.getchannel('A'), screen_mask))
    face.alpha_composite(shadow)

    # The screen is a tiny desktop pool table: corner pockets, a cue stick and
    # several glossy balls that stand in for the user's desktop app icons.
    draw = ImageDraw.Draw(face)
    pocket_radius = canvas * (.032 if size <= 32 else .027)
    for px, py in ((.060, .070), (.940, .070), (.060, .570), (.940, .570)):
        x, y = canvas * px, canvas * py
        draw.ellipse((x - pocket_radius, y - pocket_radius, x + pocket_radius, y + pocket_radius),
                     fill=(2, 19, 30, 235), outline=(93, 225, 191, 170),
                     width=max(1, round(canvas * .004)))

    def add_ball(px, py, radius, base, marker=None):
        """Composite a shaded desktop-icon ball and its tiny identifying mark."""
        cx, cy = canvas * px, canvas * py
        left, top = math.floor(cx - radius) - 1, math.floor(cy - radius) - 1
        side = math.ceil(radius * 2) + 4
        pixels = bytearray(side * side * 4)
        light = (-.48, .60, .64)
        for sy in range(side):
            ny = (cy - sy - top) / radius
            for sx in range(side):
                nx = (sx + left - cx) / radius
                radial = nx * nx + ny * ny
                if radial > 1:
                    continue
                nz = math.sqrt(max(0, 1 - radial))
                diffuse = max(0, nx * light[0] + ny * light[1] + nz * light[2])
                specular = diffuse ** 42
                softbox = math.exp(-(((nx + .34) / .27) ** 4 + ((ny - .51) / .135) ** 4))
                rim = (1 - nz) ** 3 * max(0, nx * .62 - ny * .50 + .08)
                offset = (sy * side + sx) * 4
                for channel, value in enumerate(base):
                    pixels[offset + channel] = min(255, round(value * (.48 + .52 * diffuse) + specular * (72 + channel * 9) + softbox * (70 + channel * 7) + rim * (8 + channel * 12)))
                pixels[offset + 3] = 255
        sphere = Image.frombytes('RGBA', (side, side), bytes(pixels))
        face.alpha_composite(sphere, (left, top))
        if marker:
            marker(draw, cx, cy, radius)

    def app_tile_marker(draw, cx, cy, radius):
        # Four tiny tiles make this read as a Windows desktop app icon, not a
        # numbered pool-ball set.
        r = radius * .34
        gap = radius * .08
        for dx, dy, color in ((-1, -1, (92, 210, 255, 255)), (1, -1, (244, 220, 91, 255)),
                              (-1, 1, (114, 235, 161, 255)), (1, 1, (255, 135, 121, 255))):
            x, y = cx + dx * (r + gap), cy + dy * (r + gap)
            draw.rounded_rectangle((x - r, y - r, x + r, y + r), radius=max(1, round(radius * .07)), fill=color)

    def folder_marker(draw, cx, cy, radius):
        r = radius * .44
        draw.rounded_rectangle((cx - r, cy - r * .42, cx + r, cy + r * .54), radius=max(1, round(radius * .08)), fill=(255, 225, 104, 255))
        draw.rectangle((cx - r * .72, cy - r * .62, cx - r * .02, cy - r * .30), fill=(255, 236, 134, 255))

    def eight_marker(draw, cx, cy, radius):
        badge_r = radius * .46
        draw.ellipse((cx - badge_r, cy - badge_r, cx + badge_r, cy + badge_r), fill=(255, 249, 233, 255))
        font = ImageFont.load_default(size=max(8, round(radius * .94)))
        glyph = font.getbbox('8')
        draw.text((cx - (glyph[2] - glyph[0]) / 2 - glyph[0], cy - (glyph[3] - glyph[1]) / 2 - glyph[1]),
                  '8', font=font, fill=(12, 31, 42, 255))

    ball_radius = canvas * (.082 if size <= 32 else .078)
    # The white cue ball, coloured app balls and one unmistakable 8-ball form a
    # compact rack that remains legible when Explorer requests a 16px icon.
    add_ball(.255, .390, ball_radius, (205, 220, 224), lambda d, x, y, r: d.ellipse((x - r * .28, y - r * .28, x + r * .28, y + r * .28), fill=(245, 252, 247, 255)))
    add_ball(.445, .285, ball_radius, (20, 104, 181), app_tile_marker)
    add_ball(.640, .300, ball_radius, (188, 53, 69), folder_marker)
    add_ball(.520, .465, ball_radius, (225, 93, 39), app_tile_marker)
    add_ball(.725, .455, ball_radius, (8, 19, 29), eight_marker)

    # Gold cue aimed at the white desktop-icon ball makes the interaction clear
    # even before the user sees the game running.
    cue_start = (canvas * .115, canvas * .535)
    cue_end = (canvas * .225, canvas * .420)
    draw.line((*cue_start, *cue_end), fill=(48, 31, 22, 235), width=max(2, round(canvas * .018)))
    draw.line((*cue_start, *cue_end), fill=(236, 181, 83, 255), width=max(1, round(canvas * .010)))
    draw.ellipse((cue_end[0] - canvas * .010, cue_end[1] - canvas * .010,
                  cue_end[0] + canvas * .010, cue_end[1] + canvas * .010), fill=(155, 216, 224, 255))

    a, b, d, e = .905, .028, -.052, .905
    tx, ty = canvas * .034, canvas * .159
    determinant = a * e - b * d
    inverse = (e / determinant, -b / determinant, (b * ty - e * tx) / determinant,
               -d / determinant, a / determinant, (d * tx - a * ty) / determinant)
    display = face.transform(frame.size, Image.Transform.AFFINE, inverse, Image.Resampling.BICUBIC)
    # A narrow rear shell provides depth along the lower/right edge, rather than
    # outlining the entire icon with a heavy cartoon border.
    rear = Image.new('RGBA', frame.size, (12, 33, 51, 0))
    rear.putalpha(display.getchannel('A'))
    dx, dy = max(1, round(canvas * .012)), max(1, round(canvas * .010))
    frame.alpha_composite(rear.crop((0, 0, canvas - dx, canvas - dy)), (dx, dy))
    frame.alpha_composite(display)
    frame = frame.resize((size, size), Image.Resampling.LANCZOS)
    frame.putalpha(frame.getchannel('A').point(lambda alpha: 0 if alpha < 3 else alpha))
    return frame


def table_art(table, wallpaper=None, pockets=True):
    """Draw the table frame and pockets; returns ``(frame, origin_x, origin_y)``."""
    left, top, right, bottom = map(round, table.bounds)
    width = right - left
    height = bottom - top
    pad = math.ceil(table.pocket_radius) + 5
    origin_x, origin_y = left - pad, top - pad
    minimal = table.desktop and table.manual
    if table.desktop:
        origin_x, origin_y, outer_right, outer_bottom = table.work_area
        frame = Image.new('RGBA', (outer_right - origin_x, outer_bottom - origin_y),
                          (8, 26, 25, 40 if minimal else 57))
    else:
        frame = Image.new('RGBA', (width + 2 * pad + 1, height + 2 * pad + 1))
    draw = ImageDraw.Draw(frame)
    rail = max(9, min(22, pad // 2))
    x0 = left - origin_x
    y0 = top - origin_y
    if not minimal:
        draw.rounded_rectangle((x0 - rail, y0 - rail, x0 + width + rail, y0 + height + rail), radius=rail * 2,
                               fill=(75, 51, 38, 178 if table.desktop else 238),
                               outline=(202, 149, 91, 200 if table.desktop else 244), width=3)
    felt = (0, 0, 0, 0) if table.desktop else (14, 82, 63, 156)
    if wallpaper is not None:
        blur = wallpaper.filter(ImageFilter.GaussianBlur(radius=max(10, height / 55)))
        frame.paste(blur.crop((x0, y0, x0 + width + 1, y0 + height + 1)), (x0, y0))
        felt = None
    draw.rectangle((x0, y0, x0 + width, y0 + height), fill=felt,
                   outline=(105, 221, 179, 184) if minimal else (61, 155, 120, 168 if table.desktop else 238),
                   width=3 if minimal else 5)
    # Clear the six openings first so a hidden-felt mode can place native
    # Recycle Bin artwork at the same centres without a rail painted over it.
    r = round(table.pocket_radius)
    for px, py in table.pockets:
        x = px - origin_x
        y = py - origin_y
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(0, 0, 0, 0))
    if pockets:
        for px, py in table.pockets:
            x = px - origin_x
            y = py - origin_y
        # Minimal tables keep the pockets light: a soft ring and a translucent hole, not black discs.
            draw.ellipse((x - r, y - r, x + r, y + r), fill=(11, 22, 21, 100 if minimal else 248),
                         outline=(105, 221, 179, 105) if minimal else (153, 116, 77, 255), width=2 if minimal else 3)
            inset = r * .23
            draw.ellipse((x - r + inset, y - r + inset, x + r - inset, y + r - inset),
                         fill=(4, 10, 11, 120 if minimal else 255))
    return frame, origin_x, origin_y


def pocket_art(table, source):
    """Place one native Shell icon at every pocket centre for hidden-felt mode."""
    pad = max(math.ceil(table.pocket_radius), math.ceil(max(source.size) / 2)) + 5
    left, top, right, bottom = map(round, table.bounds)
    origin_x, origin_y = left - pad, top - pad
    frame = Image.new('RGBA', (right - left + 2 * pad + 1, bottom - top + 2 * pad + 1))
    bounds = source.getchannel('A').getbbox()
    if not bounds:
        raise RuntimeError('Windows 返回了空的回收站图标')
    for px, py in table.pockets:
        frame.alpha_composite(source, (round(px - origin_x - source.width / 2),
                                        round(py - origin_y - source.height / 2)))
    return frame, origin_x, origin_y


def charge_art(percent, font):
    """Draw the charge label and its filled progress bar."""
    pad = max(4, font.size // 2)
    width = font.size * 8
    height = font.size * 2 + pad
    frame = Image.new('RGBA', (width, height))
    draw = ImageDraw.Draw(frame)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=pad,
                           fill=(13, 25, 23, 248), outline=(105, 221, 179, 240))
    draw.text((pad, height // 2), '力度', font=font, anchor='lm', fill=(235, 250, 243))

    left = pad * 2 + font.size * 2
    top = (height - pad) // 2
    bar_width = width - pad - left
    draw.rounded_rectangle((left, top, left + bar_width - 1, top + pad - 1),
                           radius=pad // 2, fill=(49, 76, 70))
    fill_width = round(bar_width * percent / 100)
    if fill_width:
        draw.rounded_rectangle((left, top, left + fill_width - 1, top + pad - 1),
                               radius=min(pad, fill_width) // 2, fill=(111, 239, 185))
    return frame


# Bright strokes with a dark casing on dark felt, deep strokes with a white casing on bright
# wallpapers. The casing keeps the dashes readable whatever the background does.
GUIDE_DARK = {'incoming': (240, 252, 247, 245), 'white': (150, 205, 255, 250), 'target': (108, 245, 183, 250),
              'casing': (4, 14, 13, 190)}
GUIDE_LIGHT = {'incoming': (26, 42, 48, 245), 'white': (10, 70, 168, 250), 'target': (0, 118, 68, 250),
               'casing': (255, 255, 255, 215)}
BRIGHT_BACKGROUND = 150
GUIDE_SCALE = 2
DASH, GAP = 12, 8


def guide_palette(luma, start, end):
    """Pick the light or dark palette from the wallpaper brightness along one guide line."""
    if luma is None:
        return GUIDE_DARK
    samples = [luma(start[0] + (end[0] - start[0]) * t / 4, start[1] + (end[1] - start[1]) * t / 4)
               for t in range(5)]
    samples = [value for value in samples if value is not None]
    if samples and sum(samples) / len(samples) > BRIGHT_BACKGROUND:
        return GUIDE_LIGHT
    return GUIDE_DARK


def guide_art(lines, circles=(), luma=None):
    """Draw the aiming preview; returns ``(frame, origin_x, origin_y)``.

    ``lines`` are ``(role, start, end)`` dashed paths. ``circles`` are ``(role, centre, radius)``
    collision circles, drawn faintly: ``ghost`` is the cue ball at the moment of contact and
    ``target`` the ball it will hit. ``luma(x, y)`` returns the wallpaper brightness (0-255)
    under a point, or ``None``, and selects a palette that contrasts with it.
    """
    xs = [point[0] for _, start, end in lines for point in (start, end)]
    ys = [point[1] for _, start, end in lines for point in (start, end)]
    for _, (cx, cy), radius in circles:
        xs += [cx - radius, cx + radius]
        ys += [cy - radius, cy + radius]
    pad = 9
    left, top = min(xs) - pad, min(ys) - pad
    width = math.ceil((max(xs) - left + pad) / 64) * 64
    height = math.ceil((max(ys) - top + pad) / 64) * 64
    s = GUIDE_SCALE  # draw enlarged and shrink back: Pillow lines have no anti-aliasing
    frame = Image.new('RGBA', (width * s, height * s))
    draw = ImageDraw.Draw(frame)
    for role, (cx, cy), radius in circles:
        palette = guide_palette(luma, (cx, cy), (cx, cy))
        box = ((cx - left - radius) * s, (cy - top - radius) * s, (cx - left + radius) * s, (cy - top + radius) * s)
        core = palette['white' if role == 'ghost' else 'target']
        if role == 'target':
            draw.ellipse(box, fill=core[:3] + (26,))
        draw.ellipse(box, outline=palette['casing'][:3] + (110,), width=round(4.5 * s))
        draw.ellipse(box, outline=core[:3] + (150 if role == 'ghost' else 125,), width=round(1.8 * s))
    strokes = []
    for role, start, end in lines:
        length = math.dist(start, end)
        if length < 1:
            continue
        palette = guide_palette(luma, start, end)
        x, y = (start[0] - left) * s, (start[1] - top) * s
        dx, dy = (end[0] - start[0]) / length, (end[1] - start[1]) / length
        usable = max(0., length - 5)
        dashes = []
        offset = 0.
        while offset < max(1., usable):
            stop = min(usable, offset + DASH)
            dashes.append(((x + dx * offset * s, y + dy * offset * s), (x + dx * stop * s, y + dy * stop * s)))
            offset += DASH + GAP
        strokes.append((palette, palette[role], dashes, ((end[0] - left) * s, (end[1] - top) * s)))
    # Casings first, then the strokes, so crossing lines never cover each other's outline.
    for palette, _, dashes, _ in strokes:
        for a, b in dashes:
            draw.line((a, b), fill=palette['casing'], width=round(5.5 * s))
    for palette, color, dashes, tip in strokes:
        for a, b in dashes:
            draw.line((a, b), fill=color, width=round(2.6 * s))
        r = 5 * s
        draw.ellipse((tip[0] - r - s, tip[1] - r - s, tip[0] + r + s, tip[1] + r + s),
                     outline=palette['casing'], width=round(2 * s))
        draw.ellipse((tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r), outline=color, width=round(2.2 * s))
    return frame.reduce(s), left, top


def white_art(radius, shaded=False):
    """Draw the cue ball, optionally shaded as a lit sphere."""
    side = max(3, math.ceil(radius * 2) + 4)
    frame = Image.new('RGBA', (side * 3, side * 3))
    if shaded:
        center = (side * 3 - 1) / 2
        r = max(1, radius * 3)
        pixels = []
        for py in range(side * 3):
            y = (py - center) / r
            for px in range(side * 3):
                x = (px - center) / r
                z2 = 1 - x * x - y * y
                if z2 < 0:
                    pixels.append((0, 0, 0, 0))
                    continue
                z = math.sqrt(z2)
                light = max(0, -.46 * x - .49 * y + .74 * z)
                shine = math.exp(-((x + .34) ** 2 + (y + .40) ** 2) / .038)
                value = min(255, round(157 + 96 * light + 32 * shine))
                pixels.append((value, min(255, value + 4), min(255, value + 9), 255))
        frame.putdata(pixels)
        return frame.resize((side, side), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(frame)
    draw.ellipse((5, 5, side * 3 - 6, side * 3 - 6), fill=(246, 249, 250, 255), outline=(134, 155, 169, 255), width=3)
    draw.ellipse((side * .65, side * .55, side * 1.15, side * 1.05), fill=(255, 255, 255, 255))
    return frame.resize((side, side), Image.Resampling.LANCZOS)


def pocket_particles(frame, index, fade):
    """Draw the ripple and the few sparks that accompany an icon sinking into a pocket."""
    frame = frame.copy()
    draw = ImageDraw.Draw(frame)
    center = frame.width / 2
    glow = math.sin(math.pi * fade) ** .65  # 0 at both ends, strongest mid-way
    radius = 10 + fade * 32
    draw.ellipse((center - radius, center - radius, center + radius, center + radius),
                 outline=(173, 255, 221, round(150 * glow)), width=2)
    for i in range(6):
        angle = i * math.tau / 6 + index * .61
        distance = fade * (12 + i % 3 * 7)
        x = center + math.cos(angle) * distance
        y = center + math.sin(angle) * distance
        size = max(.7, (1 - fade) * (2 + i % 2))
        draw.ellipse((x - size, y - size, x + size, y + size), fill=(173, 255, 221, round(210 * glow)))
    return frame


def cue_art(cue, thickness=1, skin='wood'):
    """Draw a cue from ``(tip, tail)``; returns ``(frame, offset_x, offset_y)``."""
    tip, tail = cue
    side = math.ceil(math.dist(tip, tail)) + 18
    x = round((tip[0] + tail[0] - side) / 2)
    y = round((tip[1] + tail[1] - side) / 2)
    start = (tail[0] - x, tail[1] - y)
    end = (tip[0] - x, tip[1] - y)
    grip = (start[0] + (end[0] - start[0]) * .37, start[1] + (end[1] - start[1]) * .37)
    frame = Image.new('RGBA', (side, side))
    draw = ImageDraw.Draw(frame)
    draw.line((start, end), fill=(60, 37, 22, 220), width=round(7 * thickness))
    shaft, handle = {'wood': ((239,193,126,255), (127,79,48,255)),
                     'jade': ((168,248,213,255), (27,119,87,255)),
                     'midnight': ((178,211,255,255), (40,67,128,255))}[skin]
    draw.line((start, end), fill=shaft, width=round(4 * thickness))
    draw.line((start, grip), fill=handle, width=round(5 * thickness))
    r = 2 * thickness
    draw.ellipse((end[0] - r, end[1] - r, end[0] + r, end[1] + r), fill=(126, 207, 228, 255))
    return frame, x, y
