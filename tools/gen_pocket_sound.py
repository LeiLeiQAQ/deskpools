"""Generate the original short hollow pocket-drop sample as 44.1 kHz PCM."""
from array import array
import math
from pathlib import Path
import random
import sys
import wave


def generate(destination):
    rate = 44100
    random_source = random.Random(7021)
    frames = array('h')
    filtered = 0.
    for index in range(round(rate * .29)):
        time = index / rate
        filtered = .72 * filtered + .28 * random_source.uniform(-1, 1)
        value = 0.
        # The ball first hits the pocket lining, then makes a quieter second
        # contact below it. Low resonances keep it distinct from cue clicks.
        for start, strength in ((0., 1.), (.079, .37), (.158, .12)):
            elapsed = time - start
            if elapsed >= 0:
                attack = min(1., elapsed / .0015)
                value += strength * attack * (
                    .40 * math.sin(2 * math.pi * 185 * elapsed) * math.exp(-elapsed * 38)
                    + .19 * math.sin(2 * math.pi * 327 * elapsed) * math.exp(-elapsed * 49)
                    + .38 * filtered * math.exp(-elapsed * 65))
        value *= min(1., (.29 - time) / .012)
        frames.append(round(max(-1., min(1., value * .92)) * 32767))
    if sys.byteorder != 'little':
        frames.byteswap()
    with wave.open(str(destination), 'wb') as sample:
        sample.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        sample.writeframes(frames.tobytes())


if __name__ == '__main__':
    path = Path(__file__).resolve().parents[1] / 'assets/pocket.wav'
    generate(path)
    print(path)
