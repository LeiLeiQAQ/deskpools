"""Non-blocking Windows sound effects for the desktop billiards loop."""
from __future__ import annotations

from pathlib import Path
import time
import wave

import os
os.environ.setdefault('PYGAME_HIDE_SUPPORT_PROMPT', '1')
from pygame import mixer

from . import config


class ImpactSounds:
    """Play short asynchronous impacts, coalescing dense collision chains."""

    def __init__(self, assets: Path | None = None, *, volume=.45):
        root = assets or config.ASSETS
        self.samples = tuple(root / f'impact-{index}.wav' for index in (1, 2, 3))
        self.pocket_sample = root / 'pocket.wav'
        missing = [str(path) for path in (*self.samples, self.pocket_sample) if not path.is_file()]
        if missing:
            raise FileNotFoundError(f'Missing billiards sound assets: {", ".join(missing)}')
        durations = []
        for path in self.samples:
            with wave.open(str(path), 'rb') as sample:
                durations.append(sample.getnframes() / sample.getframerate())
        self.cooldown = max(durations) + .005
        with wave.open(str(self.pocket_sample), 'rb') as sample:
            self.pocket_duration = sample.getnframes() / sample.getframerate()
        self.pocket_until = float('-inf')
        self.last_played = float('-inf')
        self.volume = max(0., min(1., float(volume)))
        self.loaded = None

    def prepare(self):
        if self.loaded is None and self.volume > 0:
            if not mixer.get_init():
                mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
            mixer.set_num_channels(4)
            mixer.set_reserved(2)
            self.loaded = tuple(mixer.Sound(str(p)) for p in (*self.samples, self.pocket_sample))
            for sound in self.loaded:
                sound.set_volume(self.volume)

    def set_volume(self, volume):
        value = max(0., min(1., float(volume)))
        if value == self.volume:
            return
        self.volume = value
        self.prepare()
        if self.loaded:
            for sound in self.loaded:
                sound.set_volume(value)
        if not value:
            self.stop()

    def play(self, intensities, now=None, *, pockets=0):
        """Play the strongest event in this frame; ``SND_ASYNC`` keeps the game responsive."""
        now = time.monotonic() if now is None else now
        if self.volume <= 0:
            return False
        self.prepare()
        if now < self.pocket_until:
            return False
        if pockets:
            # A pocket overrides a preceding rail/cue hit. Subsequent collisions
            # wait for its short hollow drop rather than cutting it off.
            mixer.Channel(0).stop()
            mixer.Channel(1).play(self.loaded[3])
            self.last_played = now
            self.pocket_until = now + self.pocket_duration
            return True
        strongest = max(intensities, default=0.0)
        if strongest <= 0:
            return False
        # Let each short WAV finish: a break can produce dozens of contacts per frame.
        if now - self.last_played < self.cooldown:
            return False
        index = min(2, max(0, int(strongest / .23 * 3)))
        mixer.Channel(0).play(self.loaded[index])
        self.last_played = now
        return True

    def stop(self):
        if self.loaded is not None:
            mixer.Channel(0).stop()
            mixer.Channel(1).stop()
        self.pocket_until = self.last_played = float('-inf')

    def close(self):
        self.stop()
        if self.loaded is not None:
            self.loaded = None
            mixer.quit()
