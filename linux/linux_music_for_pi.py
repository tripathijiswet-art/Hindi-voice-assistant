import os
import random
import threading
import pygame
import time

class MusicPlayer:
    def __init__(self, music_root_folder: str):
        """
        music_root_folder = /home/pi/Music (or your specific folder)
        """
        self.music_root_folder = music_root_folder

        self.songs = []          
        self.song_names = []     
        self.current_index = -1

        self.is_playing = False
        self.is_paused = False
        self.lock = threading.Lock()

        # --- PI CHANGE 1: Explicit Init ---
        # 44100Hz is standard for Pi audio. 
        # buffer=4096 reduces CPU usage (preventing skipping).
        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=4096)
        except Exception as e:
            print(f"⚠️ Audio Init Error: {e} (Is a speaker connected?)")

        self.refresh_library()

    def __del__(self):
        """Cleanup when the class is destroyed"""
        pygame.mixer.quit()

    
    # Library Scan
    
    def refresh_library(self):
        supported = (".mp3", ".wav", ".ogg")

        songs = []
        # Walk through directories
        if os.path.exists(self.music_root_folder):
            for dirpath, _, filenames in os.walk(self.music_root_folder):
                for f in filenames:
                    if f.lower().endswith(supported):
                        songs.append(os.path.join(dirpath, f))
        else:
            print(f"⚠️ Warning: Folder not found: {self.music_root_folder}")

        songs.sort()
        self.songs = songs
        
        # Pre-calculate normalized names for faster searching
        self.song_names = [self._normalize(os.path.splitext(os.path.basename(x))[0]) for x in songs]

        print(f"✅ [MusicPlayer] Loaded {len(self.songs)} songs from: {self.music_root_folder}")

    def _normalize(self, text: str) -> str:
        # Helper to clean up names for voice matching
        return " ".join(text.lower().strip().split())

    def list_songs(self, limit=20):
        out = []
        for i, p in enumerate(self.songs[:limit], start=1):
            out.append(f"{i}. {os.path.basename(p)}")
        return out


    # Core Controls
    
    def set_volume(self, level: float):
        """Set volume from 0.0 to 1.0"""
        #  PI CHANGE 2: Volume Control 
        try:
            pygame.mixer.music.set_volume(level)
            return True, f"Volume set to {int(level*100)}%"
        except:
            return False, "Error setting volume"

    def play_index(self, index: int):
        with self.lock:
            if not self.songs:
                return False, "No songs found in music folder."

            if index < 0 or index >= len(self.songs):
                return False, "Invalid song index."

            self.current_index = index
            song_path = self.songs[self.current_index]

            try:
                pygame.mixer.music.load(song_path)
                pygame.mixer.music.play()
                self.is_playing = True
                self.is_paused = False
                return True, f"Playing: {os.path.basename(song_path)}"
            except Exception as e:
                return False, f"Error playing song: {e}"

    def play_random(self):
        if not self.songs:
            return False, "No songs found."
        idx = random.randint(0, len(self.songs) - 1)
        return self.play_index(idx)

    def play_next(self):
        with self.lock:
            if not self.songs:
                return False, "No songs found."
            if self.current_index == -1:
                return self.play_index(0)
            # Wrap around using modulo
            new_index = (self.current_index + 1) % len(self.songs)
            return self.play_index(new_index)

    def play_prev(self):
        with self.lock:
            if not self.songs:
                return False, "No songs found."
            if self.current_index == -1:
                return self.play_index(0)
            # Wrap around using modulo
            new_index = (self.current_index - 1) % len(self.songs)
            return self.play_index(new_index)

    def pause(self):
        with self.lock:
            if self.is_playing and not self.is_paused:
                pygame.mixer.music.pause()
                self.is_paused = True
                return True, "Music paused."
            return False, "Nothing is playing."

    def resume(self):
        with self.lock:
            if self.is_playing and self.is_paused:
                pygame.mixer.music.unpause()
                self.is_paused = False
                return True, "Music resumed."
            return False, "Nothing is paused."

    def stop(self):
        with self.lock:
            if self.is_playing:
                pygame.mixer.music.stop()
                self.is_playing = False
                self.is_paused = False
                return True, "Music stopped."
            return False, "Nothing is playing."


    # Play by Name (voice)
    
    def play_by_name(self, spoken_name: str):
        """
        spoken_name example: "kesariya", "tum hi ho"
        """
        if not self.songs:
            return False, "No songs found."

        key = self._normalize(spoken_name)

        # 1. Exact match search
        if key in self.song_names:
            idx = self.song_names.index(key)
            return self.play_index(idx)

        # 2. Partial match search (smarter loop)
        for i, name in enumerate(self.song_names):
            if key in name:  # If spoken word is PART of the song name
                return self.play_index(i)

        return False, f"Song '{spoken_name}' not found."

    def is_active(self):
        # Helper to let main loop know if music is running
        # (Useful so you can pause music before TTS speaks)
        return pygame.mixer.music.get_busy()