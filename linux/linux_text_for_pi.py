import os
import re
import time
import threading

# ============================================================
# TXT Reader Module (Optimized for Raspberry Pi)
# ============================================================

def read_text_file(filepath: str):
    try:
        if not filepath or not os.path.exists(filepath):
            return None, " जानकारी नहीं मिली"

        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read().strip()

        if not content:
            return None, "इस बारे मे कोई जानकारी नहीं मिली"

        return content, None

    except Exception as e:
        return None, f"फ़ाइल पढ़ने में error आया: {e}"


def split_into_chunks(text: str, max_len=140):
    text = text.replace("\n", " ").strip()
    if not text:
        return []

    parts = []
    buf = ""

    for ch in text:
        buf += ch
        if ch in ".!?।":
            parts.append(buf.strip())
            buf = ""

    if buf.strip():
        parts.append(buf.strip())

    chunks = []
    for p in parts:
        if len(p) <= max_len:
            chunks.append(p)
        else:
            for i in range(0, len(p), max_len):
                chunks.append(p[i:i + max_len].strip())

    return [c for c in chunks if c]


class TextReader:
    def __init__(self, speak, speak_and_wait, stop_tts_immediately, search_root_dir):
        self.speak = speak
        self.speak_and_wait = speak_and_wait
        self.stop_tts_immediately = stop_tts_immediately
        self.search_root_dir = search_root_dir

        self.txt_index = {}
        self.index_lock = threading.Lock()

        self.reading_active = threading.Event()
        self.pause_event = threading.Event()
        self.stop_event = threading.Event()

        self.refresh_index()

    # -----------------------------
    # FILE INDEXING
    # -----------------------------
    def normalize_name(self, name: str) -> str:
        name = name.lower().strip()
        name = name.replace(".txt", "").strip()
        name = re.sub(r"\s+", " ", name)
        return name

    def similarity(self, a: str, b: str) -> float:
        a = a.lower().strip()
        b = b.lower().strip()
        if not a or not b: return 0.0
        if a in b or b in a: return 1.0
        
        aw = set(a.split())
        bw = set(b.split())
        common = aw & bw
        if not common: return 0.0
        return len(common) / max(len(aw), len(bw))

    def suggest_files(self, spoken_name: str, max_suggestions=3):
        key = self.normalize_name(spoken_name)
        scored = []
        with self.index_lock:
            for fname in self.txt_index.keys():
                score = self.similarity(key, fname)
                if score > 0:
                    scored.append((score, fname))
        scored.sort(reverse=True)
        return [name for _, name in scored[:max_suggestions]]

    def build_index(self):
        index = {}
        if not os.path.exists(self.search_root_dir):
            return index

        for dirpath, _, filenames in os.walk(self.search_root_dir):
            for fn in filenames:
                if fn.lower().endswith(".txt"):
                    fullpath = os.path.join(dirpath, fn)
                    key = self.normalize_name(os.path.splitext(fn)[0])
                    if key not in index:
                        index[key] = []
                    index[key].append(fullpath)
        return index

    def refresh_index(self):
        new_index = self.build_index()
        with self.index_lock:
            self.txt_index = new_index
        total = sum(len(v) for v in new_index.values())
        print(f"✅ [TextReader] Indexed {total} .txt files")

    def find_file_by_name(self, spoken_name: str):
        key = self.normalize_name(spoken_name)
        with self.index_lock:
            if key in self.txt_index:
                return self.txt_index[key][0], self.txt_index[key]
            for k, paths in self.txt_index.items():
                if key and key in k:
                    return paths[0], paths
        return None, None

    # -----------------------------
    # CONTROLS
    # -----------------------------
    def is_active(self):
        return self.reading_active.is_set()

    def pause(self):
        if self.reading_active.is_set():
            self.pause_event.set()
            self.speak("रोक दिया।")
            return True
        return False

    def resume(self):
        if self.reading_active.is_set():
            self.pause_event.clear()
            self.speak("फिर से शुरू कर रही हूँ।")
            return True
        return False

    def stop(self):
        if self.reading_active.is_set():
            self.stop_event.set()
            self.pause_event.clear()
            self.stop_tts_immediately()
            return True
        return False

    def stop_silent(self):
        if self.reading_active.is_set():
            self.stop_event.set()
            self.pause_event.clear()
            self.stop_tts_immediately()
            return True
        return False

    # -----------------------------
    # READ WORKER (Pi Optimized)
    # -----------------------------
    def start_read_by_name(self, spoken_name: str):
        if self.reading_active.is_set():
            self.speak_and_wait("पहले रुकिए।")
            return False

        path, all_paths = self.find_file_by_name(spoken_name)

        if not path:
            return False

        if all_paths and len(all_paths) > 1:
            self.speak("एक से ज़्यादा फाइल मिली हैं। पहली वाली पढ़ रही हूँ।")

        t = threading.Thread(
            target=self._read_worker,
            args=(path, spoken_name),
            daemon=True
        )
        t.start()
        return True

    def _read_worker(self, filepath: str, display_name: str):
        self.reading_active.set()
        self.stop_event.clear()
        self.pause_event.clear()

        try:
            content, err = read_text_file(filepath)
            if err:
                self.speak(err)
                return

            chunks = split_into_chunks(content, max_len=140)
            if not chunks:
                self.speak("फाइल खाली है")
                return

            self.speak(f"{display_name} में लिखा है")

            for chunk in chunks:
                # 1. Check Stop
                if self.stop_event.is_set():
                    break

                # 2. Check Pause
                while self.pause_event.is_set():
                    time.sleep(0.1)
                    if self.stop_event.is_set():
                        break
                
                if self.stop_event.is_set():
                    break

                # 3. Speak (Blocking)
                self.speak(chunk)
                
                # 4. Small natural pause
                time.sleep(0.2) 

        except Exception as e:
            print(f"Reader Error: {e}")
        finally:
            self.reading_active.clear()
            print("📖 Reading finished.")