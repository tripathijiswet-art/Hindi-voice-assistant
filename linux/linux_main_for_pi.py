import sounddevice as sd
import datetime
import subprocess
import os
os.environ["OMP_NUM_THREADS"] = "2"
from vosk import Model, KaldiRecognizer
import json
import sounddevice as sd
import datetime
import subprocess
import os
import numpy as np
from queue import Queue, Empty
import threading
import time
import re

from linux.linux_text_for_pi import TextReader
from linux.linux_music_for_pi import MusicPlayer
from linux.linux_contact_for_pi import extract_number, save_contact_direct
from linux.linux_contact_for_pi import load_contacts, extract_name_from_text


# CONFIG


def find_usb_mic():
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        name = d['name'].lower()
        if 'usb' in name and d['max_input_channels'] > 0:
            return i, d['max_input_channels']
    raise RuntimeError("No USB microphone found")


def find_output_device():
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d['max_output_channels'] > 0:
            name = d['name'].lower()
            # prefer USB speaker if present
            if 'usb' in name:
                return i
    # fallback: first output device
    for i, d in enumerate(devices):
        if d['max_output_channels'] > 0:
            return i
    raise RuntimeError("No output device found")


SPEAKER_DEVICE = find_output_device()
print(f"🔊 Using speaker device {SPEAKER_DEVICE}")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SAMPLE_RATE = 44100
VOSK_MODEL_PATH = "model"
MODEL_PATH = "model"

model = Model(VOSK_MODEL_PATH)
recognizer = KaldiRecognizer(model, SAMPLE_RATE)


ACTIVE_WINDOW = 20

WAKE_WORDS = [
    "hey assistant", "ok assistant", "hello assistant",
    "सुनो", "अरे सुनो", "दीपू", "ओके दीपू", "हेलो दीपू","सुनो",
    "सुनिए", "अरे सुनो ना", "सुनो यार", "सुणो","सुणो रे",
    "ओए सुनो", "सुन्नो", "सुन्नो हो", "ए सुनो"
]

# Root folders for your media
SEARCH_ROOT_DIR = os.path.join(BASE_DIR, "readfiles")
MUSIC_FOLDER = os.path.join(BASE_DIR, "music")

# Initialize Helpers
# (Ensure folders exist to prevent crashes)
os.makedirs(SEARCH_ROOT_DIR, exist_ok=True)
os.makedirs(MUSIC_FOLDER, exist_ok=True)

music_player = MusicPlayer(MUSIC_FOLDER)


# QUEUES & EVENTS

text_queue = Queue()
tts_queue = Queue()
is_speaking = threading.Event()
stop_tts_event = threading.Event()
current_tts_process = None
tts_lock = threading.Lock()

last_text = ""
last_text_time = 0
STT_DEBOUNCE = 1.0

awaiting_filename = False
awaiting_filename_since = 0
FILENAME_TIMEOUT = 10

awaiting_suggestion_choice = False
suggested_files = []
suggestion_since = 0
SUGGESTION_TIMEOUT = 7


# Contact Flow

contact_flow = {
    "active": False,
    "name": None,
    "digits": "",
    "started_at": 0
}

get_contact_flow = {
    "active": False,
    "name": None
}


def smooth_text(text):
    text = text.replace("।", ". ")
    text = text.replace(",", ", ")
    text = text.replace("?", "? ")
    text = text.replace("!", "! ")
    return text


# HELPER: Map Digits to Hindi Words (Forces Digit-by-Digit)

def number_to_hindi_words(num_str):
    digit_map = {
        '0': 'शून्य', '1': 'एक', '2': 'दो', '3': 'तीन', '4': 'चार',
        '5': 'पाँच', '6': 'छह', '7': 'सात', '8': 'आठ', '9': 'नौ'
    }
    # Converts "98" to "नौ आठ"
    return " ".join([digit_map.get(d, d) for d in num_str if d.isdigit()])



# FORCE STOP TTS (Instant)

def stop_tts_immediately():
    global current_tts_process

    # kill current
    with tts_lock:
        if current_tts_process and current_tts_process.poll() is None:
            try:
                current_tts_process.terminate()
            except:
                pass
        current_tts_process = None

    # clear pending queue
    try:
        while not tts_queue.empty():
            tts_queue.get_nowait()
            tts_queue.task_done()
    except:
        pass



# ESPEAK-NG TTS ENGINE


class ESpeakTTS:
    def speak(self, text):
        global current_tts_process

        if not text:
            return

        with tts_lock:
            try:
                # Stop previous speech if running
                if current_tts_process and current_tts_process.poll() is None:
                    current_tts_process.terminate()

                # eSpeak-NG command
                current_tts_process = subprocess.Popen(
                    [
                        "espeak-ng",
                        "-v", "hi+f3",          # change to 'en' for English
                        "-s", "150",         # speed (150-180 natural)
                        "-p", "43",          # pitch (0-99)
                        "-a", "180",
                        text
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )

                current_tts_process.wait()

            except Exception as e:
                print("eSpeak error:", e)


# TTS CALLBACK


def tts_callback(text):
    if not text:
        return
    tts_queue.put(text)

def callback(indata, frames, time_info, status):
    if status:
        print(status)
        
    if not is_listening.is_set() or music_playing:
        return

    try:
        data = bytes(indata)

        if recognizer.AcceptWaveform(data):
            result = recognizer.Result()
            text = json.loads(result).get("text", "").strip()

            if text:
                text_queue.put(text)

    except Exception as e:
        print("Callback error:", e)

# -----------------------------
# TTS WORKER THREAD
# -----------------------------

def tts_worker():
    while True:
        try:
            text = tts_queue.get()
            is_speaking.set()
            tts_engine.speak(text)
            is_speaking.clear()
        except Exception as e:
            print("TTS Worker error:", e)


# -----------------------------
# INITIALIZE ENGINE
# -----------------------------

tts_engine = ESpeakTTS()
threading.Thread(target=tts_worker, daemon=True).start()
# Warm it
time.sleep(0.2)


def speak(text):
    print("🤖 Assistant:", text)
    tts_queue.put(text)


def speak_and_wait(text):
    print("🤖 Assistant:", text)
    tts_queue.put(text)
    tts_queue.join()


# -----------------------------
# STT Setup
# -----------------------------
audio_queue = Queue()

music_playing = False
is_listening = threading.Event()
is_listening.set()

model_path_full = os.path.join(BASE_DIR, VOSK_MODEL_PATH)
if not os.path.exists(model_path_full):
    print(f" Error: Vosk model not found at {model_path_full}")
    print("Please download it from alphacephei.com and unpack it.")
    exit(1)

model = Model(MODEL_PATH)
rec = KaldiRecognizer(model, SAMPLE_RATE)

print("Offline Voice Assistant started!")
print(" Sleeping mode ON (Say wake word like: 'दीपू' / 'सुनो' / 'hey assistant')")
print(f" TXT search root: {SEARCH_ROOT_DIR}")

running = True
awake_until = 0

audio_buffer = bytearray()

SMALL_BUFFER = 4000  # normal listening
BIG_BUFFER = 8000




# commands file execution


def load_commands(file_path):
    full_path = os.path.join(BASE_DIR, file_path)
    with open(full_path, "r", encoding="utf-8") as f:
        return [line.strip().lower() for line in f if line.strip()]


# CHANGE TO FULL PATH IF ERROR
EXIT_COMMANDS = load_commands(r"commands/exit")
DATE_COMMANDS = load_commands(r"commands/date")
GREETING_COMMANDS = load_commands(r"commands/greeting")
TIME_COMMANDS = load_commands(r"commands/time")
THANKS_COMMANDS = load_commands(r"commands/thanks")

PLAY_MUSIC_COMMANDS = load_commands(r"commands/music/play_music")
PAUSE_MUSIC_COMMANDS = load_commands(r"commands/music/pause_music")
RESUME_MUSIC_COMMANDS = load_commands(r"commands/music/resume_music")
NEXT_MUSIC_COMMANDS = load_commands(r"commands/music/next_music")
PREV_MUSIC_COMMANDS = load_commands(r"commands/music/prev_music")
STOP_MUSIC_COMMANDS = load_commands(r"commands/music/stop_music")

TEXT_READER_COMMANDS = load_commands(r"commands/text_reader/ask_filename")
TEXT_PAUSE_COMMANDS = load_commands(r"commands/text_reader/pause")
TEXT_RESUME_COMMANDS = load_commands(r"commands/text_reader/resume")
TEXT_STOP_COMMANDS = load_commands(r"commands/text_reader/stop")

print("EXIT:", EXIT_COMMANDS[:5])
print("GREETING:", GREETING_COMMANDS[:5])
print("TIME:", TIME_COMMANDS[:5])


# INIT TextReader (Module)

text_reader = TextReader(
    speak=speak,
    speak_and_wait=speak_and_wait,
    stop_tts_immediately=stop_tts_immediately,
    search_root_dir=SEARCH_ROOT_DIR  # file_path=TEXT_FILE #single file
)



# Intent Recognition


def normalize_text(t):
    t = t.lower().strip()
    t = re.sub(r"[^\w\s\u0900-\u097F]", "", t)  # keep Hindi + English
    t = re.sub(r"\s+", " ", t)
    return t


def recognize_intent(text: str):
    t = normalize_text(text)

    # ✅ EXIT
    if any(x in t for x in EXIT_COMMANDS):
        return "exit", {}

    # ✅ TIME
    if any(x in t for x in TIME_COMMANDS):
        return "time", {}

    # ✅ DATE
    if any(x in t for x in DATE_COMMANDS):
        return "date", {}

    # ✅ GREETING
    if any(x in t for x in GREETING_COMMANDS):
        return "greet", {}

    # ✅ THANKS
    if any(x in t for x in THANKS_COMMANDS):
        return "thanks", {}

    # TXT controls
    # Start reading fixed file

    if any(x in t for x in TEXT_READER_COMMANDS):
        return "ask_filename", {}

    if any(x in t for x in TEXT_PAUSE_COMMANDS):
        return "pause_txt", {}

    if any(x in t for x in TEXT_RESUME_COMMANDS):
        return "resume_txt", {}

    if any(x in t for x in TEXT_STOP_COMMANDS):
        return "stop_txt", {}

    # read by name patterns (simple)
    # Example: "os notes पढ़ो", "read file os notes", "open file os notes"
    if "read file" in t:
        name = t.replace("read file", "").strip()
        if name:
            return "read_named_txt", {"name": name}

    if "open file" in t:
        name = t.replace("open file", "").strip()
        if name:
            return "read_named_txt", {"name": name}

    # Hindi: "___ पढ़ो"
    if "पढ़ो" in t or "पढ़ो" in t:
        name = t.replace("पढ़ो", "").replace("पढ़ो", "").strip()
        if name:
            return "read_named_txt", {"name": name}

    # music player
    if any(x in t for x in PLAY_MUSIC_COMMANDS):
        return "music_play_random", {}

    if any(x in t for x in PAUSE_MUSIC_COMMANDS):
        return "music_pause", {}

    if any(x in t for x in RESUME_MUSIC_COMMANDS):
        return "music_resume", {}

    if any(x in t for x in STOP_MUSIC_COMMANDS):
        return "music_stop", {}

    if any(x in t for x in NEXT_MUSIC_COMMANDS):
        return "music_next", {}

    if any(x in t for x in PREV_MUSIC_COMMANDS):
        return "music_prev", {}

    # ✅ Play by song name
    if t.startswith("play "):
        name = t.replace("play ", "").strip()
        if name:
            return "music_play_name", {"name": name}

        # 1. Check for Contact Intents
    if any(x in t for x in ["फोन नंबर जोड़ो", "नंबर सेव करो", "कॉन्टैक्ट जोड़ो", 
                            "नया नंबर जोड़ो", "फोन में नंबर डालो", "नंबर स्टोर करो",
                            "नंबर ऐड करो", "सेव कर दो नंबर", "नंबर भर दो",
                            "कॉन्टैक्ट बना दो","नया कॉन्टैक्ट", "नया नंबर", 
                            "नंबर जोड़ो", "सेव नंबर", "नम्बर बताओ"]):
        return "start_contact", {}

    if "नंबर" in t and "बताओ" in t:
        return "get_contact", {}

    return "unknown", {"text": text}



# Handle Intent

def handle_intent(intent, slots):
    global awaiting_filename, awaiting_suggestion_choice
    global suggested_files, suggestion_since
    global contact_flow, get_contact_flow
    now = time.time()
    raw_text = slots.get("text", "").strip()

    
    # PRIORITY 1: ACTIVE CONTACT FLOW
    # (Check this FIRST so we don't get "Unknown Command")
    
    if contact_flow["active"]:
        # Allow user to cancel
        if intent == "exit":
            contact_flow["active"] = False
            speak("कॉन्टैक्ट जोड़ना रद्द कर दिया।")
            return True

        # Timeout Check
        if now - contact_flow["started_at"] > 30:
            speak("समय समाप्त। कॉन्टैक्ट नहीं जोड़ा गया।")
            contact_flow["active"] = False
            return True

        raw_text = slots.get("text", "")

        # STEP 2: CAPTURE NAME
        if contact_flow["name"] is None:
            name = extract_name_from_text(raw_text)
            if name:
                contact_flow["name"] = name
                contact_flow["started_at"] = now
                speak_and_wait(f"ठीक है। {name} का नंबर बताइए।")
            else:
                speak_and_wait("नाम समझ नहीं आया, फिर से बोलिए।")
            return True

        # STEP 3: CAPTURE NUMBER
        else:
            digits = extract_number(raw_text)
            if digits:
                contact_flow["digits"] += digits
                contact_flow["started_at"] = now

                print(f"DEBUG DIGITS: {contact_flow['digits']}")

                if len(contact_flow["digits"]) >= 10:
                    save_contact_direct(contact_flow["name"], contact_flow["digits"])
                    speak_and_wait(f"{contact_flow['name']} का नंबर सेव कर दिया गया है।")
                    contact_flow["active"] = False
                else:
                    speak_and_wait("और नंबर बोलिए...")
            else:
                speak_and_wait("नंबर समझ नहीं आया।")
            return True

    
    #  PRIORITY 2: GET CONTACT FLOW (Asking "Whose number?")
    
    if get_contact_flow["active"]:
        name = extract_name_from_text(slots.get("text", ""))
        contacts = load_contacts()

        if name and name in contacts:
            raw_number = contacts[name]
            # Convert to words so eSpeak DOES NOT say "Crore/Lakh"
            spoken_number = number_to_hindi_words(raw_number)

            speak(f"{name} का नंबर है {spoken_number}")
        else:
            speak("यह नंबर सेव नहीं है।")

        get_contact_flow["active"] = False
        return True

    
    # PRIORITY 3: STANDARD COMMANDS
    

    if intent == "start_contact":
        contact_flow["active"] = True
        contact_flow["name"] = None
        contact_flow["digits"] = ""
        contact_flow["started_at"] = now
        speak_and_wait("ठीक है। पहले नाम बताइए।")
        return True

    if intent == "get_contact":
        name = extract_name_from_text(slots.get("text", ""))
        contacts = load_contacts()

        if name:
            contacts = load_contacts()
            if name in contacts:
                raw_number = contacts[name]
                # Convert to words
                spoken_number = number_to_hindi_words(raw_number)

                speak(f"{name} का नंबर है {spoken_number}")
            else:
                speak(f"{name} का नंबर सेव नहीं है।")
        else:
            get_contact_flow["active"] = True
            speak_and_wait("किसका नंबर बताऊँ?")
        return True

    if intent == "greet":
        speak("Hello Shashank ! Main ready hoon.")
        return True

    if intent == "thanks":
        speak("बहुत धन्यवा'द")
        return True

    if intent == "time":
        now = datetime.datetime.now()
        speak(f"टाइम है {now.strftime('%I:%M %p')}")
        return True

    if intent == "date":
        today = datetime.date.today()
        speak(f"आज की तारीख है {today.strftime('%d %B %Y')}")
        return True

    if intent == "unknown":
        speak("कृपया दो'बारा बोलि'ए")
        return True

    if intent == "exit":
        # stop reading + stop tts and quit
        text_reader.stop_silent()
        stop_tts_immediately()
        speak_and_wait("अच्छा, ठीक है फिर मिलते हैं")
        return False

    # STEP 1: User said "read file"
    if intent == "ask_filename":
        global awaiting_filename, awaiting_filename_since
        awaiting_filename = True
        awaiting_filename_since = time.time()
        speak_and_wait("किस बारे मे जानकारी चाहिए ")
        return True

    # STEP 2: User now says filename
    if awaiting_filename:
        awaiting_filename = False
        filename = slots.get("text") or slots.get("filename") or slots.get("raw") or ""
        filename = filename.strip()

    if intent == "filename_provided":
        awaiting_filename = False
        spoken_name = slots.get("text", "").strip()

        if not spoken_name:
            speak_and_wait("नाम समझ नहीं आया।")
            return True

        # Try direct read
        success = text_reader.start_read_by_name(spoken_name)
        if success:
            return True

        #  Auto-suggest
        suggestions = text_reader.suggest_files(spoken_name)

        if not suggestions:
            speak_and_wait("इस बारे मे जानकारी नहीं ")
            return True

        # Ask user to choose
        suggested_files = suggestions
        awaiting_suggestion_choice = True
        suggestion_since = time.time()

        msg = "मुझे ये जानकारी मिली है  "
        for i, name in enumerate(suggestions, 1):
            msg += f"{i}. {name}, "

        msg += "किस बारे मे बताऊ"
        speak_and_wait(msg)
        return True

    # suggesting text file
    if intent == "suggestion_choice":
        awaiting_suggestion_choice = False

        choice = slots.get("text", "").lower().strip()

        # Number choice
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(suggested_files):
                text_reader.start_read_by_name(suggested_files[idx])
                return True

        # Name choice
        for name in suggested_files:
            if choice in name:
                text_reader.start_read_by_name(name)
                return True

        speak_and_wait("समझ नहीं आया   कृपया नाम या नंबर बोलिए")
        awaiting_suggestion_choice = True
        return True

    if intent == "pause_txt":
        if not text_reader.pause():
            speak_and_wait("यह सिर्फ जानकारी देते समय ही हो सकता है")
        return True

    if intent == "resume_txt":
        if not text_reader.resume():
            speak_and_wait("यह सिर्फ जानकारी देते समय ही हो सकता है")
        return True

    if intent == "stop_txt":
        if not text_reader.stop():
            speak_and_wait("यह सिर्फ जानकारी देते समय ही हो सकता है")
        return True

    if intent == "music_play_random":
        ok, msg = music_player.play_random()
        speak_and_wait(msg)
        return True

    if intent == "music_pause":
        ok, msg = music_player.pause()
        speak_and_wait(msg)
        return True

    if intent == "music_resume":
        ok, msg = music_player.resume()
        speak_and_wait(msg)
        return True

    if intent == "music_stop":
        ok, msg = music_player.stop()
        speak_and_wait(msg)
        return True

    if intent == "music_next":
        ok, msg = music_player.play_next()
        speak_and_wait(msg)
        return True

    if intent == "music_prev":
        ok, msg = music_player.play_prev()
        speak_and_wait(msg)
        return True

    if intent == "music_play_name":
        song_name = slots.get("name", "")
        ok, msg = music_player.play_by_name(song_name)
        speak_and_wait(msg)
        return True

    return True



# WAKE WORD CHECK

def is_wake_word(text: str) -> bool:
    t = normalize_text(text)
    return any(w in t for w in WAKE_WORDS)



# eSpeak-NG SPEAK FUNCTIONS


current_tts_process = None

def speak(text):
    global current_tts_process

    if not text:
        return

    try:
        # Kill previous speech if still running
        if current_tts_process and current_tts_process.poll() is None:
            current_tts_process.terminate()

        current_tts_process = subprocess.Popen(
            [
                "espeak-ng",
                "-v", "hi",      # Hindi (use "en" for English)
                "-s", "165",     # Speed (150–170 natural)
                "-p", "45",      # Pitch
                "-a", "200",
                text
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

    except Exception as e:
        print("eSpeak error:", e)


def speak_and_wait(text):
    global current_tts_process

    if not text:
        return

    try:
        if current_tts_process and current_tts_process.poll() is None:
            current_tts_process.terminate()

        current_tts_process = subprocess.Popen(
            [
                "espeak-ng",
                "-v", "hi",
                "-s", "165",
                "-p", "45",
                "-a", "200",
                text
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        current_tts_process.wait()

    except Exception as e:
        print("eSpeak wait error:", e)


# MAIN LOOP
try:
    MIC_DEVICE, MIC_CHANNELS = find_usb_mic()  # USB PnP Sound Device
    MIC_RATE = 44100  # Standard Sample Rate

    print(f"\n🎤 Connecting to Mic (Device {MIC_DEVICE}) at {MIC_RATE}Hz...")

    with sd.RawInputStream(
            samplerate=MIC_RATE,
            blocksize=8000,
            dtype="int16",
            channels=MIC_CHANNELS,
            device=MIC_DEVICE,  
            callback=callback):
        print("\n✅ System Online...")
        speak("सिस्टम शुरू हो गया , मैं तैयार हूं")

        running = True
        awake_until = 0

        while running:
            if not text_queue.empty():
                text = text_queue.get()
                now = time.time()

                if len(text.strip()) < 2:
                    continue

                now_ts = time.time()

                print("\n🗣️ You said:", text)

                intent, slots = recognize_intent(text)

                handled = False  

                
                #  TEXT READER ACTIVE → allow text controls ONLY
                
                if text_reader.is_active():
                    if intent in ("pause_txt", "resume_txt", "stop_txt", "exit"):
                        running = handle_intent(intent, slots)
                        handled = True
                    else:
                        print("📖 Text reading active — ignoring non-text command")

                
                # MUSIC ACTIVE → allow music controls ONLY
            
                elif music_player.is_active():
                    if intent.startswith("music_"):
                        running = handle_intent(intent, slots)
                        handled = True
                    else:
                        print("🎧 Music playing — ignoring non-music command")

                
                #  IDLE MODE → normal assistant behavior
                
                else:
                    # filename timeout
                    if awaiting_filename:
                        if time.time() - awaiting_filename_since > FILENAME_TIMEOUT:
                            awaiting_filename = False
                            speak_and_wait("ठीक है, बाद में बताइए।")

                    # suggestion timeout
                    if awaiting_suggestion_choice:
                        if time.time() - suggestion_since > SUGGESTION_TIMEOUT:
                            awaiting_suggestion_choice = False
                            speak_and_wait("ठीक है, बाद में बताइए।")

                    # wake-word gating 
                    if now_ts > awake_until:
                        if not is_wake_word(text):
                            continue
                        awake_until = now_ts + ACTIVE_WINDOW
                        speak("हाँ बोलिए!")
                        continue
                    else:
                        if awaiting_suggestion_choice:
                            running = handle_intent("suggestion_choice", {"text": text})
                            handled = True

                        elif intent == "unknown" and awaiting_filename:
                            running = handle_intent("filename_provided", {"text": text})
                            handled = True

                        else:
                            running = handle_intent(intent, slots)
                            handled = True

                        awake_until = time.time() + ACTIVE_WINDOW

            time.sleep(0.1)

except KeyboardInterrupt:
    print("\n Stopped by user.")
    text_reader.stop_silent()
    music_player.stop()
    speak_and_wait("ठीक है, बंद कर रहा हूँ।")

    if current_tts_process and current_tts_process.poll() is None:
        current_tts_process.terminate()

except Exception as e:
    print(f"\n Critical Error: {e}")