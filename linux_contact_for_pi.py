import json
import os
import re

# --- PI CHANGE 1: Absolute Path ---
# This ensures 'contacts.json' is always found in the same folder as this script
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTACT_FILE = os.path.join(BASE_DIR, "contacts.json")

HINDI_DIGITS = {
    "शून्य": "0", "जीरो": "0",
    "एक": "1", "वन": "1",
    "दो": "2", "टू": "2",
    "तीन": "3", "थ्री": "3",
    "चार": "4", "फोर": "4",
    "पाँच": "5", "पांच": "5", "फाइव": "5",
    "छह": "6", "सिक्स": "6",
    "सात": "7", "सेवन": "7",
    "आठ": "8", "एट": "8",
    "नौ": "9", "नाइन": "9"
}


def load_contacts():
    if not os.path.exists(CONTACT_FILE):
        return {}
    try:
        with open(CONTACT_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            return json.loads(content) if content else {}
    except (json.JSONDecodeError, IOError):
        # If file is corrupted (rare on Pi but possible), return empty
        return {}


def save_contacts(data):
    try:
        with open(CONTACT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
            # --- PI CHANGE 2: Force Write to SD Card ---
            # This pushes data from RAM to the physical SD card immediately
            f.flush()
            os.fsync(f.fileno()) 
            
    except IOError as e:
        print(f"Error saving contacts: {e}")


def extract_number(text):
    """Converts Hindi and English spoken digits to a numeric string."""
    digits = ""
    # Check for standard digits (0-9) inside the text string
    # (Note: This simple logic works, but be careful of mixed text)
    temp_text = text.replace("-", " ") # Handle "98-99" style
    
    # 1. Grab raw digits first
    for char in temp_text:
        if char.isdigit():
            digits += char

    # 2. If no digits found, check for number words
    if not digits:
        for w in temp_text.split():
            clean_w = w.strip(".,")
            if clean_w in HINDI_DIGITS:
                digits += HINDI_DIGITS[clean_w]
                
    return digits


def save_contact_direct(name, number):
    # Reload first to ensure we don't overwrite recent changes
    contacts = load_contacts()
    contacts[name] = number
    save_contacts(contacts)
    return True


def format_number_for_tts(text):
    """Adds spaces between digits so TTS reads them individually."""
    # This logic is perfectly fine for Pi
    def repl(match):
        return " ".join(match.group(0))

    return re.sub(r"\d{5,}", repl, text)


def extract_name_from_text(text):
    # Added a few more common Hindi stopwords to ignore
    ignore = [
        "नाम", "है", "का", "की", "के", "नंबर", "बताओ", "जोड़ो", "जोड़ें", 
        "पढो", "read", "file", "save", "contact", "सेव", "करो"
    ]
    
    # Simple logic: returns the first word that isn't in the ignore list
    for w in text.strip().split():
        clean_w = w.strip(".,")
        if clean_w not in ignore and len(clean_w) >= 2:
            return clean_w
    return None