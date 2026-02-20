import json
import os
import re

CONTACT_FILE = "contacts.json"

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
        return {}


def save_contacts(data):
    with open(CONTACT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def extract_number(text):
    """Converts Hindi and English spoken digits to a numeric string."""
    digits = ""
    # Check for standard digits (0-9)
    for char in text:
        if char.isdigit():
            digits += char

    # Check for spoken words
    for w in text.split():
        if w in HINDI_DIGITS:
            digits += HINDI_DIGITS[w]
    return digits


def save_contact_direct(name, number):
    contacts = load_contacts()
    contacts[name] = number
    save_contacts(contacts)


def format_number_for_tts(text):
    """Adds spaces between digits so TTS reads them individually (e.g., '9 8 7' instead of '987')."""

    def repl(match):
        return " ".join(match.group(0))

    return re.sub(r"\d{5,}", repl, text)


def extract_name_from_text(text):
    ignore = ["नाम", "है", "का", "की", "के", "नंबर", "बताओ", "जोड़ो", "जोड़ें", "पढो", "read", "file"]
    for w in text.strip().split():
        if w not in ignore and len(w) >= 2:
            return w
    return None