import sounddevice as sd
import datetime
import subprocess
import numpy as np
import winsound
import os
from vosk import Model, KaldiRecognizer
import json
from queue import Queue
import threading
import time

from windows.text_reader_module import TextReader
from windows.music_player_module import MusicPlayer
from windows.save_contact import extract_number, save_contact_direct
from windows.save_contact import load_contacts, extract_name_from_text


# CONFIG

ESPEAK_PATH = r"C:\Program Files\eSpeak NG\espeak-ng.exe"
SAMPLE_RATE = 16000
MODEL_PATH = "model-hi"

ACTIVE_WINDOW = 20

WAKE_WORDS = [
    "hey assistant", "ok assistant", "hello assistant",
    "सुनो", "अरे सुनो", "दीपू", "ओके दीपू", "हेलो दीपू"
]

#  root folder where your txt files exist (all subfolders included)
SEARCH_ROOT_DIR = r"C:\Users\shash\vosk_setup\readfiles"

MUSIC_FOLDER = r"C:\Users\shash\vosk_setup\music"
music_player = MusicPlayer(MUSIC_FOLDER)


# QUEUES & EVENTS

text_queue = Queue()
tts_queue = Queue()
is_speaking = threading.Event()
current_tts_process = None
tts_lock = threading.Lock()

last_text = ""
last_text_time = 0
STT_DEBOUNCE = 1.0

awaiting_filename = False
awaiting_filename_since = 0

awaiting_suggestion_choice = False
suggested_files = []
suggestion_since = 0

FILENAME_TIMEOUT = 10
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



# TTS THREAD (Interruptible )


def tts_worker():
    global current_tts_process

    while True:
        text0 = tts_queue.get()

        if text0 is None:
            tts_queue.task_done()
            break

        try:
            is_speaking.set()

            wav_path = "temp.wav"

            with tts_lock:
                current_tts_process = subprocess.Popen(
                    [
                        ESPEAK_PATH,
                        "-v", "hi",
                        "-s", "150",
                        "-w", wav_path,
                        text0
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )

            current_tts_process.wait()

            if os.path.exists(wav_path):
                winsound.PlaySound(wav_path, winsound.SND_FILENAME)

        except Exception as e:
            print("TTS Error:", e)


        finally:
            is_speaking.clear()
            with tts_lock:
                current_tts_process = None
            try:
                tts_queue.task_done()
            except:
                pass


tts_thread = threading.Thread(target=tts_worker, daemon=True)
tts_thread.start()


def speak(text):
    subprocess.run([
        "espeak-ng",
        "-v", "hi",
        "-s", "150",
        "-w", "out.wav",
        text
    ])


def speak_and_wait(text):
    print("🤖 Assistant:", text)
    tts_queue.put(text)
    tts_queue.join()


# STT Setup

audio_queue = Queue()

is_listening = threading.Event()
is_listening.set()

model = Model(MODEL_PATH)
rec = KaldiRecognizer(model, SAMPLE_RATE)


print("Offline Voice Assistant started!")
print("Sleeping mode ON (Say wake word like: 'दीपू' / 'सुनो' / 'hey assistant')")
print(f"TXT search root: {SEARCH_ROOT_DIR}")

running = True
awake_until = 0

# -----------------------------
# MIC CALLBACK (No TTS here)
# -----------------------------
audio_buffer = bytearray()

SMALL_BUFFER = 4000   # normal listening
BIG_BUFFER = 8000

def callback(indata, frames, time_info, status):

    if is_speaking.is_set() and not music_player.is_active() and not text_reader.is_active():
        return

    if status:
        print("Mic Status:", status)

    data = bytes(indata)

    if rec.AcceptWaveform(data):
        result = json.loads(rec.Result())
        text = result.get("text", "").strip()
        if text:
            print("VOSK FINAL:", text)
            text_queue.put(text)
    else:
        # Optional: debug partial results
        partial = json.loads(rec.PartialResult()).get("partial", "")


# commands file execution


def load_commands(file_path):
    with open(file_path, "r", encoding="utf-8") as f:
        return [line.strip().lower() for line in f if line.strip()]

# CHANGE TO FULL PATH IF ERROR
EXIT_COMMANDS = load_commands(r"commands\exit")
DATE_COMMANDS = load_commands(r"commands\date")
GREETING_COMMANDS = load_commands(r"commands\greeting")
TIME_COMMANDS = load_commands(r"commands\time")
THANKS_COMMANDS = load_commands(r"commands\thanks")

PLAY_MUSIC_COMMANDS = load_commands(r"commands\music\play_music")
PAUSE_MUSIC_COMMANDS = load_commands(r"commands\music\pause_music")
RESUME_MUSIC_COMMANDS = load_commands(r"commands\music\resume_music")
NEXT_MUSIC_COMMANDS = load_commands(r"commands\music\next_music")
PREV_MUSIC_COMMANDS = load_commands(r"commands\music\prev_music")
STOP_MUSIC_COMMANDS = load_commands(r"commands\music\stop_music")

TEXT_READER_COMMANDS = load_commands(r"commands\text_reader\ask_filename")
TEXT_PAUSE_COMMANDS = load_commands(r"commands\text_reader\pause")
TEXT_RESUME_COMMANDS = load_commands(r"commands\text_reader\resume")
TEXT_STOP_COMMANDS = load_commands(r"commands\text_reader\stop")


# INIT TextReader (Module)

text_reader = TextReader(
    speak=speak,
    speak_and_wait=speak_and_wait,
    stop_tts_immediately=stop_tts_immediately,
    search_root_dir=SEARCH_ROOT_DIR #file_path=TEXT_FILE #single file
)


# Intent Recognition

def recognize_intent(text: str):
    t = text.lower().strip()

    # EXIT
    if any(x in t for x in EXIT_COMMANDS):
        return "exit", {}

    # TIME
    if any(x in t for x in TIME_COMMANDS):
        return "time", {}

    # DATE
    if any(x in t for x in DATE_COMMANDS):
        return "date", {}

    # GREETING
    if any(x in t for x in GREETING_COMMANDS):
        return "greet", {}

    # THANKS
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

    #music player
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

    #  Play by song name
    if t.startswith("play "):
        name = t.replace("play ", "").strip()
        if name:
            return "music_play_name", {"name": name}

        # 1. Check for Contact Intents
    if any(x in t for x in ["नया कॉन्टैक्ट", "नया नंबर", "नंबर जोड़ो", "सेव नंबर", "नम्बर बताओ"]):
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

    
    #  PRIORITY 1: ACTIVE CONTACT FLOW
    # (Check this FIRST so we don't get "Unknown Command")
    # 
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

    
    # 🚨 PRIORITY 2: GET CONTACT FLOW (Asking "Whose number?")
    
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

    
    #  PRIORITY 3: STANDARD COMMANDS
    

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
        speak_and_wait("अच्छा, चलती हूँ! दुआ'ओं में याद रखना ")
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

        # 🔍 Auto-suggest
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

    #suggesting text file
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

def is_wake_word(text):
    text = text.strip().lower()

    for w in WAKE_WORDS:
        if w.strip().lower() in text:
            return True

    return False


# MAIN LOOP

try:
    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=4000, dtype="int16", channels=1, callback=callback):
        print("\nSystem Online...")
        speak("सिस्टम ऑनलाइन. मैं रेडी हु.")

        running = True
        awake_until = 0

        while running:
            if not text_queue.empty():
                text = text_queue.get()
                text_queue.task_done()
                now = time.time()

                if len(text.strip()) < 2:
                    continue

                now_ts = time.time()

                print("\nYou said:", text)

                intent, slots = recognize_intent(text)

                handled = False  # key flag

                
                #  TEXT READER ACTIVE → allow text controls ONLY
                
                if text_reader.is_active():
                    if intent in ("pause_txt", "resume_txt", "stop_txt", "exit"):
                        running = handle_intent(intent, slots)
                        handled = True
                    else:
                        print("Text reading active — ignoring non-text command")

                
                # MUSIC ACTIVE → allow music controls ONLY
            
                elif music_player.is_active():
                    if intent.startswith("music_"):
                        running = handle_intent(intent, slots)
                        handled = True
                    else:
                        print(" Music playing — ignoring non-music command")

                
                # IDLE MODE → normal assistant behavior
                
                else:
                    #  filename timeout 
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
                        speak_and_wait("हाँ बोलिए!")
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

            sd.sleep(50)

except KeyboardInterrupt:
    print("\n Stopped by user.")
    text_reader.stop_silent()
    stop_tts_immediately()
    speak_and_wait("ठीक है भाई, बंद कर रहा हूँ ")

# shutdown tts thread
tts_queue.put(None)
tts_queue.join()
time.sleep(0.2)