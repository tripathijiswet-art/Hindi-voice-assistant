import os
import random
import threading
import pygame


class MusicPlayer:
    def __init__(self, music_root_folder: str):
        """
        music_root_folder = folder that contains music files (can have subfolders)
        """
        self.music_root_folder = music_root_folder

        self.songs = []          # list of full paths
        self.song_names = []     # clean names for voice matching
        self.current_index = -1

        self.is_playing = False
        self.is_paused = False
        self.lock = threading.Lock()

        pygame.mixer.init()

        self.refresh_library()

    # -----------------------------
    # Library Scan
    # -----------------------------
    def refresh_library(self):
        supported = (".mp3", ".wav", ".ogg")

        songs = []
        for dirpath, _, filenames in os.walk(self.music_root_folder):
            for f in filenames:
                if f.lower().endswith(supported):
                    songs.append(os.path.join(dirpath, f))

        songs.sort()
        self.songs = songs
        self.song_names = [self._normalize(os.path.splitext(os.path.basename(x))[0]) for x in songs]

        print(f"✅ [MusicPlayer] Loaded {len(self.songs)} songs from: {self.music_root_folder}")

    def _normalize(self, text: str) -> str:
        return " ".join(text.lower().strip().split())

    def list_songs(self, limit=20):
        out = []
        for i, p in enumerate(self.songs[:limit], start=1):
            out.append(f"{i}. {os.path.basename(p)}")
        return out

    # -----------------------------
    # Core Controls
    # -----------------------------
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
            return self.play_index((self.current_index + 1) % len(self.songs))

    def play_prev(self):
        with self.lock:
            if not self.songs:
                return False, "No songs found."
            if self.current_index == -1:
                return self.play_index(0)
            return self.play_index((self.current_index - 1) % len(self.songs))

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

    # -----------------------------
    # Play by Name (voice)
    # -----------------------------
    def play_by_name(self, spoken_name: str):
        """
        spoken_name example: "kesariya", "tum hi ho"
        """
        if not self.songs:
            return False, "No songs found."

        key = self._normalize(spoken_name)

        # Exact match
        if key in self.song_names:
            idx = self.song_names.index(key)
            return self.play_index(idx)

        # Partial match
        for i, name in enumerate(self.song_names):
            if key and key in name:
                return self.play_index(i)

        return False, f"Song '{spoken_name}' not found."

    def is_active(self):
        return self.is_playing