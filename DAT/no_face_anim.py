"""
NO FACE kaomoji animation (synced to /project1/NO_FACE/anim).

Cycle: mojibake scramble (~1 s) -> per-character decode into the next
kaomoji from the kaomoji_sets table -> hold (~1.2 s) -> scramble again.
Stateless: everything derives from absTime.seconds, so it survives module
reloads and never drifts. Add/edit kaomoji by editing the table rows.

Used as the text expression on the t_kao Text TOP:
    op('anim').module.face()
"""

import hashlib

SCRAMBLE = 1.0    # fallback seconds of garbled churn (par: Scramble)
HOLD = 1.2        # fallback seconds the kaomoji stays (par: Hold)
FLICKER = 12      # scramble refresh rate (glyph changes per second)


def _knob(name, fallback):
    """Read a design knob from the NO_FACE comp's custom page."""
    try:
        return float(getattr(parent().par, name).eval())
    except Exception:
        return fallback


def _timing():
    s = max(_knob('Scramble', SCRAMBLE), 0.05)
    h = max(_knob('Hold', HOLD), 0.05)
    return s, h

# mojibake / glitch alphabets — light marks only (solid blocks like ▓▒█
# render as giant bricks at display size). The face layer churns through
# thin Shift-JIS wreckage; the field layer is dots/dashes like radio noise.
GLITCH = 'ソスｿｽｼﾞ×%#&¿§~=+*:;·'
GLITCH_FIELD = '・.·°×-=+:;‥…―~ソス'
GLITCH_EYES = 'ｏｎａｙ！＠＞＜'   # the eyes glitch through the show's own name
EYE_FLICKER = 8   # eye glyph changes per second — slow enough to read "onay"


def _h(*vals):
    """Deterministic pseudo-random 0..1 from the given values."""
    s = ','.join(str(v) for v in vals).encode()
    return int(hashlib.md5(s).hexdigest()[:8], 16) / 0xffffffff


def _sets():
    t = op('kaomoji_sets')
    faces = []
    if t is not None:
        for r in range(t.numRows):
            v = t[r, 0].val.strip()
            if v:
                faces.append(v)
    return faces or ['(・_・)']


def face():
    faces = _sets()
    t = absTime.seconds
    scramble, hold = _timing()
    cycle = scramble + hold
    n = int(t // cycle)
    target = faces[n % len(faces)]
    phase = t % cycle

    if phase >= scramble:
        return target

    # scramble phase: each character locks in at its own moment, so the
    # features surface one by one out of the noise
    tick = int(t * FLICKER)
    out = []
    for i, ch in enumerate(target):
        lock = scramble * (0.25 + 0.7 * _h('lock', n, i))
        if phase >= lock or ch == ' ':
            out.append(ch)
        else:
            out.append(GLITCH[int(_h('g', tick, n, i) * len(GLITCH))])
    return ''.join(out)


def label(text):
    """Cycle-synced scramble for the static N-O / F-A-C-E labels: they are
    the EYES and MOUTH of the big face, so they churn and resolve on the
    same clock as the kaomoji nose. Spaces pass through untouched."""
    t = absTime.seconds
    scramble, hold = _timing()
    cycle = scramble + hold
    n = int(t // cycle)
    phase = t % cycle
    if phase >= scramble:
        return text
    # all unlocked characters run through GLITCH_EYES IN ORDER, in sync —
    # the eyes literally spell out o-n-a-y-!-@->-< while searching
    tick = int(t * EYE_FLICKER)
    glyph = GLITCH_EYES[tick % len(GLITCH_EYES)]
    out = []
    for i, ch in enumerate(text):
        lock = scramble * (0.25 + 0.7 * _h('lbl', text, n, i))
        if phase >= lock or ch in (' ', '　'):
            out.append(ch)
        else:
            out.append(glyph)
    return ''.join(out)


FLIP = 0.08       # base seconds per kaomoji in the mouth row (faster than the
                  # eyes' glyphs — small inner-character changes read slower)
ASSEMBLE = 0.2   # seconds for the F A C E letters to pop in one by one


def _mouth_flip(n):
    """Per-cycle mouth tempo (0.8x..1.3x of FLIP), like the eyes have."""
    return FLIP * (0.8 + 0.5 * _h('mtempo', n))


def is_face_now():
    """True while the mouth row reads F A C E (used to switch its font:
    rounded latin for the word, MS Gothic for the kaomoji)."""
    t = absTime.seconds
    scramble, hold = _timing()
    cycle = scramble + hold
    n = int(t // cycle)
    phase = t % cycle
    flip = _mouth_flip(n)
    steps_total = max(1, int(scramble / flip))
    return phase >= scramble or int(phase / flip) >= steps_total


def face_label():
    """The mouth row: kaomoji run through one after another (FLIP seconds
    each, order shifted every cycle) for Scramble seconds, then the row
    reads F A C E and holds. No glitch characters here — faces only."""
    faces = _sets()
    t = absTime.seconds
    scramble, hold = _timing()
    cycle = scramble + hold
    n = int(t // cycle)
    phase = t % cycle
    # quantize to whole beats: the last kaomoji gets a full beat and
    # F A C E takes over ON the beat — no rushed final flip
    flip = _mouth_flip(n)
    steps_total = max(1, int(scramble / flip))
    step = int(phase / flip)
    if phase >= scramble or step >= steps_total:
        # letters assemble one by one (order reshuffles every cycle)
        base = 'F A C E'
        start_t = min(scramble, steps_total * flip)
        p = phase - start_t
        if p >= ASSEMBLE:
            return base
        out = []
        for i, ch in enumerate(base):
            if ch == ' ':
                out.append(ch)
                continue
            lock = ASSEMBLE * (0.1 + 0.85 * _h('fassem', n, i))
            out.append(ch if p >= lock else ' ')
        return ''.join(out)
    start = int(_h('start', n) * len(faces))
    return faces[(start + step) % len(faces)]


IMG_COUNT = 7     # numbered glyph images in AssetPath (1.png .. 7.png)
IMG_FLIP = 0.14   # seconds each drawn glyph stays while the eyes cycle


# glyph inventory (0-based SWITCH indices; img2/img3 are wired swapped):
#   0 = n (ink blob)   1 = o (3.png)   2 = n geometric (2.png)
#   3 = a   4 = y   5 = p   6 = bar
N_IMGS = [0, 2]   # what the LEFT eye may land on
O_IMGS = [1]      # what the RIGHT eye lands on -> together they spell N O


def _eye_run(side, final_pool):
    """Carousel that ARRIVES on the final glyph: the sequence is laid out
    backwards from the landing image, so every flip — including the last
    one onto the n/o — is exactly IMG_FLIP long. No speed-up before the
    stop. The stride changes every cycle (all of 1..6 are coprime with 7)
    so the path through the glyphs still varies and the eyes never sync."""
    t = absTime.seconds
    scramble, hold = _timing()
    cycle = scramble + hold
    n = int(t // cycle)
    phase = t % cycle
    final = final_pool[int(_h('fin', side, n) * len(final_pool))]
    # each eye draws its own tempo every cycle (0.6x..1.6x of IMG_FLIP),
    # so the two eyes run at different speeds — the arrival still lands
    # exactly on that eye's own beat
    flip = IMG_FLIP * (0.6 + 1.0 * _h('tempo', side, n))
    steps_total = max(1, int(scramble / flip))
    step = int(phase / flip)
    if phase >= scramble or step >= steps_total:
        return final
    stride = 1 + int(_h('stride', side, n) * 6)
    return (final - (steps_total - step) * stride) % IMG_COUNT


def eye_index_left():
    return _eye_run('L', N_IMGS)


def eye_index_right():
    return _eye_run('R', O_IMGS)


# ---- scattered glitch field (the "particle cloud" layer behind the face)

ROWS = 14         # character grid of the noise field
COLS = 22
DENSITY = 0.085   # fraction of cells holding a glyph while scrambling


def noise_field():
    """Sparse multi-line field of mojibake — cells flicker on/off so the
    cloud churns. An elliptical mask keeps it loosely gathered around the
    centre (like marks orbiting where the face will land)."""
    t = absTime.seconds
    tick = int(t * FLICKER)
    lines = []
    for r in range(ROWS):
        row = []
        for c in range(COLS):
            # distance from centre 0..1 (ellipse) — sparser toward the edge
            dx = (c / (COLS - 1)) * 2 - 1
            dy = (r / (ROWS - 1)) * 2 - 1
            d = (dx * dx + dy * dy) ** 0.5
            p = _knob('Noisedensity', DENSITY) * (1.25 - d)
            if _h('cell', tick // 2, r, c) < max(p, 0):
                row.append(GLITCH_FIELD[int(_h('cg', tick, r, c) * len(GLITCH_FIELD))])
            else:
                row.append('　')          # fullwidth space keeps the grid square
        lines.append(''.join(row))
    return '\n'.join(lines)


def noise_alpha():
    """Field opacity over the cycle: full while scrambling, quick fade as
    the face locks in, out during the hold, quick fade back in."""
    t = absTime.seconds
    scramble, hold = _timing()
    cycle = scramble + hold
    phase = t % cycle
    fade = min(0.25, scramble * 0.25)
    if phase < scramble - fade:
        return 1.0
    if phase < scramble:
        return (scramble - phase) / fade          # fading out
    if phase > cycle - fade:
        return 1.0 - (cycle - phase) / fade       # fading back in
    return 0.0
