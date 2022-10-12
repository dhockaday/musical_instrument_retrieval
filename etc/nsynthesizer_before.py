import argparse
import os
import sys
from pathlib import Path

import numpy as np

import pretty_midi
import librosa
from scipy.io.wavfile import write as write_wav
from scipy.io.wavfile import read as read_wav
# import shutup; shutup.please()


class NSynthesizer():
    def __init__(self, dataset_path, sr=16000, transpose=0, attack_in_ms=5, release_in_ms=100, leg_stac=.9, velocities=np.arange(0,128), preset=0,
                    duration_in_seconds=3.0, min_notes=1):

        self.duration_in_seconds = duration_in_seconds
        self.min_notes = min_notes
        
        self.dataset_path = Path(dataset_path)
        self.sr = sr
        self.transpose = transpose
        self.release_in_ms = release_in_ms
        self.release_in_sample = int(self.release_in_ms * 0.001 * self.sr)
        self.attack_in_ms = attack_in_ms
        self.attack_in_sample = int(self.attack_in_ms * 0.001 * self.sr)
        self.leg_stac = leg_stac
        self.velocities = velocities
        self.preset = preset
        self.notes = {}

    def _get_note_name(self, note, velocity, instrument, source_type, preset=None):
        preset = preset if(preset is not None) else self.preset
        return "%s_%s_%s-%s-%s.wav" % (instrument, source_type, str(preset).zfill(3), str(note).zfill(3), str(velocity).zfill(3))    

    def _quantize(self, value, quantized_values):
        diff = np.array([np.abs(q - value) for q in quantized_values])
        return quantized_values[diff.argmin()]

    def preload_notes(self, instrument, source_type, preset=None):
        inst_path = self.dataset_path / instrument / source_type / str(preset)
        preset = preset if(preset is not None) else self.preset
        for n in range(22, 108):
            for v in self.velocities:
                note_name = self._get_note_name(n, v, instrument, source_type, preset)
                try:
                    _, audio_int = read_wav(inst_path / note_name)
                    audio = audio_int / 32768.0
                except Exception as e:
                    audio = None
                self.notes[note_name] = audio

    def _read_midi(self, filename):
        midi_data = pretty_midi.PrettyMIDI(filename)
        end_time = midi_data.get_end_time()
        
        sequence = []
        for instrument in midi_data.instruments:
            for note in instrument.notes:
                if note.start < end_time:
                    note.velocity = self._quantize(note.velocity, self.velocities)
                    sequence.append((note.pitch, note.velocity, note.start/end_time, note.end/end_time))
        return sequence, end_time

    def _render_note(self, note_filename, duration, velocity):
        try:
            note = self.notes[note_filename]

            attack_env = np.arange(self.attack_in_sample) / self.attack_in_sample
            note[:self.attack_in_sample] *= attack_env

            decay_ind = int(self.leg_stac*duration)
            decay_env = np.exp(-np.arange(len(note)-decay_ind)/3000.)
            note[decay_ind:] *= decay_env

            release_env = (self.release_in_sample-np.arange(self.release_in_sample)) / self.release_in_sample
            note[duration:duration+self.release_in_sample] *= release_env
        except Exception as e:
            # print('Note not fonund', note_filename)
            note = np.zeros(duration+self.release_in_sample)
        return note[:duration+self.release_in_sample]

    def partially_render_sequence(self, sequence, instrument='guitar', source_type='acoustic', 
                                        preset=None, playback_speed=1, duration_scale=1, transpose=0, eps=1e-9, start_position=0, 
                                        duration_in_seconds=3.0, min_notes=1):

        duration_in_seconds = self.duration_in_seconds
        min_notes = self.min_notes

        preset = preset if(preset is not None) else self.preset
        transpose = transpose if(transpose is not None) else self.transposer

        seq, end_time = self._read_midi(sequence)
        total_length = int(end_time * self.sr / playback_speed)
        slice_length = int(duration_in_seconds * self.sr / playback_speed)
        data = np.zeros(slice_length)

        # Midi Slicing
        end_position = start_position + duration_in_seconds / end_time
        seq = [item for item in seq if (item[2] > start_position and item[3] < end_position)]

        # There should be at least min_notes of notes in the midi slice.
        num_valid_notes = 0
        for note, velocity, _, _ in seq:
            note_filename = self._get_note_name(
                                                    note=note+transpose, 
                                                    velocity=velocity, 
                                                    instrument=instrument, 
                                                    source_type=source_type,
                                                    preset=preset
                                                )
            if note_filename in self.notes.keys():
                if self.notes[note_filename] is not None:
                    num_valid_notes += 1
        if  num_valid_notes < min_notes:
            return None

        for note, velocity, note_start, note_end in seq:
            start_sample = int((note_start-start_position) * total_length)
            end_sample = int((note_end-start_position) * total_length)
            duration = end_sample - start_sample

            if(duration_scale != 1):
                duration = int(duration * duration_scale)
                end_sample = start_sample + duration
            
            end_sample += self.release_in_sample

            note_filename = self._get_note_name(
                                                    note=note+transpose,
                                                    velocity=velocity,
                                                    instrument=instrument,
                                                    source_type=source_type,
                                                    preset=preset
                                                )
            note = self._render_note(note_filename, duration, velocity)
            if(end_sample <= len(data)):
                data[start_sample:start_sample+len(note)] += note

        data /= np.max(np.abs(data)) + eps

        mel_spec = librosa.feature.melspectrogram(y=data, sr=self.sr, win_length=1024, hop_length=512, n_mels=128)
        log_spec = librosa.power_to_db(mel_spec)

        return log_spec