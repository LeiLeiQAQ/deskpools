"""Nonblocking SDL audio scheduling and volume behavior."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from dib.sound import ImpactSounds

class ImpactSoundTests(unittest.TestCase):
    def setUp(self):
        self.patch=patch('dib.sound.mixer');self.mixer=self.patch.start();self.addCleanup(self.patch.stop)
        self.effects=[Mock() for _ in range(4)];self.mixer.Sound.side_effect=self.effects
        self.sounds=ImpactSounds()
    def test_plays_strongest_effect(self):
        self.assertTrue(self.sounds.play((.02,.20),now=1))
        self.mixer.Channel.return_value.play.assert_called_with(self.effects[2])
    def test_cooldown_coalesces_dense_contacts(self):
        self.assertTrue(self.sounds.play((.20,),now=1))
        self.assertFalse(self.sounds.play((.23,),now=1.02))
        self.assertTrue(self.sounds.play((.02,),now=1+self.sounds.cooldown+.001))
    def test_pocket_preempts_collision_then_finishes(self):
        self.sounds.play((.23,),now=1)
        self.assertTrue(self.sounds.play((),now=1.01,pockets=1))
        self.mixer.Channel.return_value.play.assert_called_with(self.effects[3])
        self.assertFalse(self.sounds.play((.23,),now=1.02))
    def test_volume_updates_all_effects_and_mute_stops(self):
        self.sounds.prepare();self.sounds.set_volume(.2)
        for effect in self.effects:effect.set_volume.assert_called_with(.2)
        self.sounds.set_volume(0)
        self.assertFalse(self.sounds.play((.23,),pockets=1))
        self.mixer.Channel.return_value.stop.assert_called()
    def test_close_stops_and_releases_audio(self):
        self.sounds.prepare();self.sounds.close();self.mixer.quit.assert_called_once()
    def test_missing_assets_report_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(FileNotFoundError):ImpactSounds(Path(folder))
