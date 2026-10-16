import io
import sys
import os
import threading
import asyncio
import edge_tts
import pygame
from ai_engine import (
    nebius_chat,
    warm_up_nebius,
    DEFAULT_FAST_MODEL,
    DEFAULT_REASONING_MODEL,
    is_nebius_configured
)
import subprocess
import difflib
import sqlite3
import re
from datetime import datetime
from system_ops import SystemDriver
from PyQt6.QtCore import QUrl, pyqtSignal, QObject
from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtWebEngineCore import QWebEnginePage
from PyQt6.QtWebEngineWidgets import QWebEngineView

# Persona Configuration
MASTER_NAME = os.environ.get("HYDRA_MASTER_NAME", "Ahmad Farooq")
ASSISTANT_NAME = os.environ.get("HYDRA_ASSISTANT_NAME", "HYDRA")

# Creates a safe bridge between background threads and the GUI
class UISignaler(QObject):
    js_signal = pyqtSignal(str)

GUI_SIGNALS = UISignaler()

APPS_CACHE = {}
DIALOGUE_STATE = {"mode": "NORMAL", "pending_action": None, "payload": None}
PYGAME_MIXER_READY = False
TTS_ENGINE = None
TTS_LOOP = None
TTS_LOCK = threading.Lock()
LAST_ACTIVE_TIME = 0.0
LEARNING_SESSION_ACTIVE = False
ACTIVE_PAGE = None
SYS_DRIVER = SystemDriver()


def parse_fs_intent(raw_command):
    text = raw_command.lower().strip()
    text = re.sub(r'[!?]', '', text)
    if text.endswith('.'): text = text[:-1].strip()
    text = re.sub(r'^hydra[\s,]+', '', text).strip()
    
    # SAFETY VALVE: Ignore file actions if the user is giving a core system rule
    rule_triggers = ["new rule", "from now on", "behavioral rule", "talk like", "act like"]
    if any(rt in text for rt in rule_triggers):
        return None

    action = None
    if any(w in text for w in ["delete", "remove", "erase", "destroy"]): action = "DELETE"
    elif any(w in text for w in ["move", "transfer", "shift", "put"]): action = "MOVE"
    elif any(w in text for w in ["create", "make", "new", "build"]): action = "CREATE"
    elif any(w in text for w in ["locate", "find", "where is", "search"]): action = "SEARCH"
    elif any(w in text for w in ["open", "launch", "start", "run"]):
        if any(w in text for w in ["file", "folder", "directory", "document"]) or "." in text or re.search(r'\b(in|inside|at)\b', text):
            action = "OPEN"
    if not action: return None

    # Extension logic
    ext_map = {"python": ".py", "py": ".py", "html": ".html", "js": ".js", "css": ".css", "text": ".txt", "txt": ".txt"}
    detected_ext = ""
    for word, ext in ext_map.items():
        if re.search(r'\b' + word + r'\b', text):
            detected_ext = ext
            break

    item_type = "FOLDER" if any(w in text for w in ["folder", "directory"]) else "FILE"

    # Location extraction
    location = None
    prep_split = re.split(r'\b(in|inside|into|at|on)\b', text, maxsplit=1)
    if len(prep_split) == 3:
        text = prep_split[0].strip()
        location = re.sub(r'[^\w\s]', '', prep_split[2]).strip()

    # Clean Name
    clean_working = re.sub(r'[^\w\s.]', ' ', text)
    ban_words = ["please", "hydra", "file", "folder", "directory", "create", "make", "open", "launch"]
    ban_words.extend(list(ext_map.keys()))
    tokens = [t for t in clean_working.split() if t not in ban_words]
    clean_target = " ".join(tokens).strip()

    # SMART GUESSER: If they say "index" and we don't find it, the system will now 
    # try searching for the base name without worrying about the extension.
    # The 'locate_path' function in system_ops.py will now handle partial matching.
    
    return {"action": action, "type": item_type, "name": clean_target, "location": location, "ext": detected_ext}

def ensure_pygame_mixer():
    global PYGAME_MIXER_READY
    if not PYGAME_MIXER_READY:
        pygame.mixer.init(frequency=24000, size=-16, channels=1, buffer=512)
        PYGAME_MIXER_READY = True

def warm_up_ai_models():
    warm_up_nebius()

def ai_chat(model, messages, num_predict=60, num_ctx=256, timeout=30):
    """
    Bridge function routing calls to Nebius Token Factory with NVIDIA models.
    Supports both fast and reasoning models with graceful fallback.
    """
    target_model = (
        DEFAULT_REASONING_MODEL
        if ("1.5b" in str(model) or "70b" in str(model) or "super" in str(model) or "ultra" in str(model))
        else DEFAULT_FAST_MODEL
    )
    return nebius_chat(
        messages=messages,
        model=target_model,
        max_tokens=num_predict or 250,
        timeout=timeout or 30.0
    )

def get_db_connection():
    conn = sqlite3.connect("hydra_memory.db", timeout=15.0)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def get_tts_engine():
    global TTS_ENGINE
    if TTS_ENGINE is None:
        import pyttsx3
        TTS_ENGINE = pyttsx3.init("sapi5")
        voices = TTS_ENGINE.getProperty("voices")
        for voice in voices:
            if "david" in voice.name.lower() or "desktop" in voice.name.lower():
                TTS_ENGINE.setProperty("voice", voice.id)
                break
        TTS_ENGINE.setProperty("rate", 165)
    return TTS_ENGINE


def init_memory_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_date TEXT,
            role TEXT,
            content TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS long_term_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fact TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS custom_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_query TEXT,
            forced_response TEXT,
            context_snapshot TEXT
        )
    """)
    # --- NEW TABLE: Tracks which days the AI has already analyzed ---
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_days (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_date TEXT UNIQUE
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS created_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE
        )
    """)
    conn.commit()
    conn.close()

def log_created_file(filepath):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO created_files (file_path) VALUES (?)", (filepath,))
    conn.commit()
    conn.close()

def get_and_clear_created_files():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT file_path FROM created_files")
    files = [row[0] for row in c.fetchall()]
    c.execute("DELETE FROM created_files")
    conn.commit()
    conn.close()
    return files

def save_chat_message(role, content):
    conn = get_db_connection()
    cursor = conn.cursor()
    current_date = datetime.now().strftime("%d %B")
    cursor.execute("INSERT INTO chats (chat_date, role, content) VALUES (?, ?, ?)", (current_date, role, content))
    conn.commit()
    conn.close()

def load_chat_context():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Dropped from 8 to 2. Saves the CPU ~6 seconds of re-reading old text.
    cursor.execute("SELECT role, content FROM chats ORDER BY id DESC LIMIT 2")
    rows = cursor.fetchall()
    conn.close()
    context = []
    for role, content in reversed(rows):
        context.append({"role": role, "content": content})
    return context

def get_context_snapshot():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM chats ORDER BY id DESC LIMIT 5")
    rows = cursor.fetchall()
    conn.close()
    if len(rows) > 1:
        rows = rows[1:]
    snapshot_elements = [f"{row[0]}: {row[1]}" for row in reversed(rows)]
    return " | ".join(snapshot_elements)

def save_contextual_rule(user_query, forced_response, current_context):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, forced_response, context_snapshot FROM custom_rules WHERE user_query = ?", (user_query,))
    existing_rules = cursor.fetchall()
    updated = False
    for rule_id, old_response, stored_context in existing_rules:
        similarity = difflib.SequenceMatcher(None, current_context.lower(), stored_context.lower()).ratio()
        if similarity > 0.45:
            cursor.execute("UPDATE custom_rules SET forced_response = ?, context_snapshot = ? WHERE id = ?", (forced_response, current_context, rule_id))
            updated = True
            print(f"[LEARNING CORE] Overwriting existing rule match under shared context track (Similarity: {similarity:.2f})")
            break
    if not updated:
        cursor.execute("INSERT INTO custom_rules (user_query, forced_response, context_snapshot) VALUES (?, ?, ?)", (user_query, forced_response, current_context))
        print("[LEARNING CORE] Distinct conversational context mapped. Created a branched rule entry.")
    conn.commit()
    conn.close()

def find_contextual_rule(current_command, current_context):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_query, forced_response, context_snapshot FROM custom_rules")
    rows = cursor.fetchall()
    conn.close()
    
    # Remove punctuation so "hi!" and "hi" are treated the same
    clean_cmd = ''.join(e for e in current_command.lower() if e.isalnum() or e.isspace()).strip()
    
    queries = list(set([row[0] for row in rows]))
    clean_queries = [''.join(e for e in q.lower() if e.isalnum() or e.isspace()).strip() for q in queries]
    
    # Lowered cutoff to 0.50 to make the AI more adaptable to slight rephrasing
    matches = difflib.get_close_matches(clean_cmd, clean_queries, n=1, cutoff=0.50)
    
    if matches:
        matched_clean_query = matches[0]
        # Find the original query that matches our cleaned version
        original_query = next(q for q in queries if ''.join(e for e in q.lower() if e.isalnum() or e.isspace()).strip() == matched_clean_query)
        
        best_response = None
        max_similarity = -1
        
        for user_query, forced_response, stored_context in rows:
            if user_query == original_query:
                similarity = difflib.SequenceMatcher(None, current_context.lower(), stored_context.lower()).ratio()
                if similarity > max_similarity:
                    max_similarity = similarity
                    best_response = forced_response
                    
        print(f"[LEARNING CORE] Evaluated Context Score: {max_similarity:.2f}")
        # Lowered context strictness to 0.35 so it actually triggers
        if max_similarity >= 0.35: 
            return best_response
    return None

def get_last_user_command():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM chats WHERE role = 'user' ORDER BY id DESC LIMIT 2")
    rows = cursor.fetchall()
    conn.close()
    if len(rows) >= 2:
        return rows[1][0]
    return None

def save_long_term_memory(fact):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM long_term_memory WHERE fact = ?", (fact,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO long_term_memory (fact) VALUES (?)", (fact,))
        conn.commit()
    conn.close()

def load_long_term_memories():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT fact FROM long_term_memory ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]

def retrieve_relevant_memories(current_command):
    conn = get_db_connection()
    cursor = conn.cursor()
    # Order by DESC to always get newest memories first
    cursor.execute("SELECT fact FROM long_term_memory ORDER BY id DESC")
    rows = cursor.fetchall()
    conn.close()
    
    all_facts = [row[0] for row in rows]
    # Use a beginner-friendly stop-word list instead of char length limits
    stop_words = ["the", "is", "at", "which", "on", "and", "a", "to", "in", "of", "for", "it"]
    command_words = [w for w in current_command.lower().split() if w not in stop_words]
    
    relevant_facts = []
    for fact in all_facts:
        fact_lower = fact.lower()
        if any(word in fact_lower for word in command_words):
            relevant_facts.append(fact)
            
    # Always include the 4 most recent memories so HYDRA understands its current state
    recent_facts = all_facts[:4]
    
    # Combine relevant facts and recent facts, removing duplicates
    combined_facts = list(set(relevant_facts + recent_facts))
    
    # Give it up to 12 facts so it has enough data to analyze
    return combined_facts[:12]

def populate_apps_cache():
    global APPS_CACHE
    paths = [
        os.path.join(os.environ.get('PROGRAMDATA', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs'),
        os.path.join(os.environ.get('APPDATA', ''), 'Microsoft', 'Windows', 'Start Menu', 'Programs')
    ]
    for path in paths:
        if os.path.exists(path):
            for root, _, files in os.walk(path):
                for file in files:
                    if file.endswith('.lnk'):
                        APPS_CACHE[file[:-4].lower()] = os.path.join(root, file)

# --- GLOBAL TRACKER: Remembers what the user just asked about ---
LAST_INFO_QUERY = None

def execute_information_retrieval(query):
    import urllib.request
    import urllib.parse
    import json
    from concurrent.futures import ThreadPoolExecutor
    global LAST_INFO_QUERY

    clean_query = query.lower()
    fillers = [
        "tell me something about", "tell me about", "do you know about", 
        "give me the information on", "give me information on", "information on", 
        "information about", "search for", "what is", "who is", "define", "explain"
    ]
    for filler in fillers:
        clean_query = clean_query.replace(filler, "")
    
    clean_query = clean_query.strip(" ?.,!")
    encoded_query = urllib.parse.quote(clean_query)
    LAST_INFO_QUERY = clean_query  # Lock the target into memory

    def get_best_wiki_data():
        search_url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={encoded_query}&limit=1&namespace=0&format=json"
        req = urllib.request.Request(search_url, headers={'User-Agent': 'HydraAssistant/1.0'})
        best_title = clean_query.title()
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode())
                if len(data) > 1 and len(data[1]) > 0:
                    best_title = data[1][0]
        except Exception:
            pass

        encoded_title = urllib.parse.quote(best_title)
        summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded_title}"
        req2 = urllib.request.Request(summary_url, headers={'User-Agent': 'HydraAssistant/1.0'})
        try:
            with urllib.request.urlopen(req2, timeout=3) as response:
                data = json.loads(response.read().decode())
                if "extract" in data:
                    img_url = data.get("originalimage", {}).get("source", "") or data.get("thumbnail", {}).get("source", "")
                    return {
                        "text": data['extract'], 
                        "title": data.get('title', best_title),
                        "image": img_url,
                        "source": "WIKIPEDIA DATABASE"
                    }
        except Exception:
            pass
        return None

    def get_related_images():
        url = f"https://en.wikipedia.org/w/api.php?action=query&generator=search&gsrsearch={encoded_query}&prop=pageimages&pithumbsize=600&format=json"
        req = urllib.request.Request(url, headers={'User-Agent': 'HydraAssistant/1.0'})
        images = []
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode())
                pages = data.get("query", {}).get("pages", {})
                for page_data in pages.values():
                    if "thumbnail" in page_data:
                        images.append(page_data["thumbnail"]["source"])
        except Exception:
            pass
        return images

    result_payload = get_best_wiki_data()
    related_images = get_related_images()
    
    if result_payload:
        main_img = result_payload.get("image", "")
        
        final_images = []
        if main_img: final_images.append(main_img)
        for img in related_images:
            if img not in final_images:
                final_images.append(img)
                
        img1 = final_images[0] if len(final_images) > 0 else "circle.png"
        img2 = final_images[1] if len(final_images) > 1 else img1
        
        photos_json = json.dumps([
            {"source": result_payload["source"], "desc": result_payload["text"][:110] + "...", "img": img1},
            {"source": "VERIFIED INTEL", "desc": f"Extended digital footprint entry regarding {result_payload['title']}.", "img": img2}
        ])
        
        js_emit = f"populateInfoHUD({json.dumps(result_payload['title'])}, {json.dumps(result_payload['text'])}, {photos_json});"
        GUI_SIGNALS.js_signal.emit(js_emit)
        return f"Summary: {result_payload['text']}"
        
    return "No supplementary database matches found for this query."
    
def network_monitor_worker():
    import time
    import subprocess
    while True:
        try:
            # creationflags=0x08000000 prevents cmd windows from flashing and freezing the app
            result = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"], 
                capture_output=True, 
                text=True, 
                creationflags=0x08000000
            ).stdout
            
            ssid, signal, speed = "DISCONNECTED", "--", "--"
            
            if "State" in result and "connected" in result:
                for line in result.split('\n'):
                    if " SSID" in line and "BSSID" not in line:
                        ssid = line.split(":", 1)[1].strip()
                    elif "Signal" in line:
                        signal = line.split(":", 1)[1].strip()
                    elif "Receive rate" in line:
                        speed = line.split(":", 1)[1].strip()
                js_code = f"updateRealNetwork('{ssid}', '{signal}', '{speed}');"
            else:
                js_code = "updateRealNetwork('DISCONNECTED', '--', '--');"
            
            # Safely send the javascript code to the main thread via our Signal bridge
            GUI_SIGNALS.js_signal.emit(js_code)
        except Exception as e:
            print(f"[NET MONITOR ERR] {e}")
        time.sleep(4)
def analyze_specific_day(day):
    print(f"[TARGETED LEARN] Analyzing specific date: {day}")
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT role, content FROM chats WHERE chat_date LIKE ? ORDER BY id ASC", (f"%{day}%",))
    day_chats = cursor.fetchall()
    conn.close()

    if not day_chats:
        if ACTIVE_PAGE: ACTIVE_PAGE.speak_voice(f"I found no archives for {day}, sir.")
        return

    if len(day_chats) >= 2:
        transcript = "\n".join([f"{r}: {c}" for r, c in day_chats])
        prompt = (
            "Analyze this daily chat transcript. Look for patterns where the user gives a command "
            "and the assistant gave a good response.\n"
            "Extract predictive rules ONLY in this exact format, one per line:\n"
            "RULE: user command | ideal assistant response\n"
            "If no clear repeatable rules exist, output strictly 'NONE'.\n"
            f"Transcript:\n{transcript}"
        )
        analysis = ai_chat(DEFAULT_REASONING_MODEL, [{"role": "user", "content": prompt}], num_predict=150, timeout=25)
        
        if analysis and "none" not in analysis.lower():
            for line in analysis.split("\n"):
                if "RULE:" in line:
                    try:
                        clean_line = line.split("RULE:")[1]
                        q, a = clean_line.split("|", 1)
                        save_contextual_rule(q.strip().lower(), a.strip(), f"targeted_learned_{day}")
                        print(f"[TARGETED LEARN] Locked Rule: '{q.strip()}' -> '{a.strip()}'")
                    except Exception:
                        pass
    if ACTIVE_PAGE: ACTIVE_PAGE.speak_voice(f"Targeted learning for {day} is complete, sir.")

def historical_learning_worker():
    global LEARNING_SESSION_ACTIVE, ACTIVE_PAGE
    import time

    while True:
        if not LEARNING_SESSION_ACTIVE:
            time.sleep(1)
            continue

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT chat_date FROM chats 
            WHERE chat_date NOT IN (SELECT chat_date FROM processed_days)
        """)
        unprocessed_days = [row[0] for row in cursor.fetchall()]
        conn.close()

        if not unprocessed_days:
            if ACTIVE_PAGE:
                ACTIVE_PAGE.speak_voice("I have finished reading all archives to the end, sir. Resetting memory pointer to read again.")
            
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("DELETE FROM processed_days")
            conn.commit()
            conn.close()
            
            time.sleep(3)
            continue

        for day in unprocessed_days:
            if not LEARNING_SESSION_ACTIVE:
                break

            print(f"[BATCH LEARN] Digesting full archive for: {day}...")
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT role, content FROM chats WHERE chat_date = ? ORDER BY id ASC", (day,))
            day_chats = cursor.fetchall()
            conn.close()

            if len(day_chats) >= 2:
                transcript = "\n".join([f"{r}: {c}" for r, c in day_chats])
                prompt = (
                    "Analyze this daily chat transcript. Look for patterns where the user gives a command "
                    "and the assistant gave a good response.\n"
                    "Extract predictive rules ONLY in this exact format, one per line:\n"
                    "RULE: user command | ideal assistant response\n"
                    "If no clear repeatable rules exist, output strictly 'NONE'.\n"
                    f"Transcript:\n{transcript}"
                )

                analysis = ai_chat(DEFAULT_REASONING_MODEL, [{"role": "user", "content": prompt}], num_predict=150, timeout=25)

                if analysis and "none" not in analysis.lower():
                    for line in analysis.split("\n"):
                        if "RULE:" in line:
                            try:
                                clean_line = line.split("RULE:")[1]
                                q, a = clean_line.split("|", 1)
                                save_contextual_rule(q.strip().lower(), a.strip(), f"batch_learned_{day}")
                                print(f"[BATCH LEARN] Locked Rule: '{q.strip()}' -> '{a.strip()}'")
                            except Exception:
                                pass

            # Mark day as read in DB so we never scan it again
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO processed_days (chat_date) VALUES (?)", (day,))
            conn.commit()
            conn.close()

            if ACTIVE_PAGE:
                ACTIVE_PAGE.speak_voice(f"I have read the {day} conversation, sir.")

            # 20-second leash: checks every half-second if you spoke to interrupt it
            interrupted = False
            start_wait = time.time()
            while (time.time() - start_wait) < 20:
                if not LEARNING_SESSION_ACTIVE:
                    interrupted = True
                    break
                time.sleep(0.5)

            if interrupted:
                if ACTIVE_PAGE:
                    ACTIVE_PAGE.speak_voice("Learning cycle paused, sir.")
                break

        LEARNING_SESSION_ACTIVE = False

def is_simple_conversation(text):
    """Fallback function to determine if a prompt is just casual chat."""
    words = text.lower().split()
    # If the user is just saying a few words with no action verbs, bypass deep reasoning
    action_words = ["open", "launch", "play", "search", "find", "delete", "create", "what", "how", "why", "who", "tell", "about", "know", "information"]
    if len(words) <= 6 and not any(w in text for w in action_words):
        return True
    return False

class HydraPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, line, source):
        if message.startswith("HYDRA_CMD:"):
            command = message[10:].lower().strip()
            self.process_hydra_command(command)

    def process_hydra_command(self, command):
        import time
        import re
        global LAST_ACTIVE_TIME, LEARNING_SESSION_ACTIVE, ACTIVE_PAGE
        LAST_ACTIVE_TIME = time.time()
        ACTIVE_PAGE = self 

        cmd_lower = command.lower()
        
        # ---> ALWAYS CLOSE INFO PANEL ON NEW COMMAND <---
        GUI_SIGNALS.js_signal.emit("resetScreenSwipe();")
        
        # ---> ALLOW EXPLICIT CLOSING OF THE PANEL <---
        if any(w in cmd_lower for w in ["close info", "close panel", "hide info", "go back"]):
            reply = "Closing information panel, sir."
            self.speak_voice(reply)
            save_chat_message("user", command)
            save_chat_message("assistant", reply)
            return

        # ---> NEW: DIVE DEEP & DETAILED ANALYSIS COMMAND <---
        if any(w in cmd_lower for w in ["dive deep", "more detail", "more details", "expand on", "tell me more about that"]):
            global LAST_INFO_QUERY
            if LAST_INFO_QUERY:
                reply = f"I have collected the info on {LAST_INFO_QUERY}, sir. Pulling it up now."
                self.speak_voice(reply)
                save_chat_message("user", command)
                save_chat_message("assistant", reply)
                
                def deep_dive_worker():
                    import urllib.request, urllib.parse, json
                    # 1. Fetch many images from Wikipedia silently
                    encoded = urllib.parse.quote(LAST_INFO_QUERY)
                    img_url = f"https://en.wikipedia.org/w/api.php?action=query&generator=search&gsrsearch={encoded}&prop=pageimages&pithumbsize=800&gsrlimit=8&format=json"
                    req = urllib.request.Request(img_url, headers={'User-Agent': 'HydraAssistant/1.0'})
                    images = []
                    try:
                        with urllib.request.urlopen(req, timeout=4) as response:
                            data = json.loads(response.read().decode())
                            pages = data.get("query", {}).get("pages", {})
                            for page_data in pages.values():
                                if "thumbnail" in page_data: images.append(page_data["thumbnail"]["source"])
                    except Exception as e:
                        print(f"[DEEP DIVE] Image fetch error: {e}")

                    # 2. Instruct Nebius AI to structure data into clean HTML sections
                    prompt = (
                        f"Provide a highly detailed overview of {LAST_INFO_QUERY}. "
                        "Format it STRICTLY using HTML tags. "
                        "You MUST include exactly 3 sections using <h3> tags: "
                        "<h3>About</h3>, <h3>History & Dates</h3>, and <h3>Key Facts</h3>. "
                        "Put all body text inside <p> tags. "
                        "Do NOT use markdown. Do NOT write ```html. Just raw HTML text."
                    )
                    
                    structured_html = ai_chat(DEFAULT_REASONING_MODEL, [{"role": "user", "content": prompt}], num_predict=600, timeout=45)
                    
                    if not structured_html: 
                        structured_html = "<p>Failed to compile deep dive analysis.</p>"
                    else:
                        # Clean up formatting if Nebius AI disobeys and wraps it in code blocks
                        structured_html = structured_html.replace("```html", "").replace("```", "").strip()

                    # 3. Fire the Javascript function to trigger the mega-swipe and load data
                    js_emit = f"openDeepDiveHUD({json.dumps(LAST_INFO_QUERY.upper())}, {json.dumps(structured_html)}, {json.dumps(images)});"
                    GUI_SIGNALS.js_signal.emit(js_emit)

                threading.Thread(target=deep_dive_worker, daemon=True).start()
                return
            else:
                reply = "I don't have a recent subject in memory to dive into, sir."
                self.speak_voice(reply)
                return

        # 1. SPECIFIC DATE LEARNING MUST BE CHECKED FIRST
        targeted_match = re.search(r'(?:read|learn from|start learning from)\s+(.*?)\s+(?:conversation|logs|archive|chat)', cmd_lower)
        if targeted_match:
            target_date = targeted_match.group(1).strip()
            self.speak_voice(f"Initiating targeted analysis for {target_date}, sir.")
            threading.Thread(target=analyze_specific_day, args=(target_date,), daemon=True).start()
            return

        # 2. GENERAL BACKGROUND LEARNING (checked second)
        if "start learning" in cmd_lower:
            LEARNING_SESSION_ACTIVE = True
            self.speak_voice("Background learning sequence started, sir.")
            return

        if LEARNING_SESSION_ACTIVE:
            LEARNING_SESSION_ACTIVE = False # Intercepts background loop!
            print("[BATCH LEARN] User spoke; pausing deep learning.")

        if "close" in command or "exit" in command:
            reply = "Shutting down systems. Goodbye."
            self.speak_voice(reply)
            QApplication.quit()
            return

        if "clear database" in command or "wipe memory" in command or "reset database" in command:
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM chats")
                cursor.execute("DELETE FROM long_term_memory")
                cursor.execute("DELETE FROM custom_rules")
                conn.commit()
                conn.close()
                reply = "Systems flushed, sir. Core memory tables have been completely initialized."
            except Exception as e:
                reply = f"Database flush execution failure: {e}"
            self.speak_voice(reply)
            return

        save_chat_message("user", command)

        def background_processor():
            import random
            import time
            global DIALOGUE_STATE
            pipeline_start = time.perf_counter()
            
            print(f"\n========================================")
            print(f"[USER COMMAND]: '{command}'")
            print(f"========================================")
            
            clean_cmd = command.strip().lower().replace(".", "").replace("!", "").replace("?", "").strip()

            # ---> MASS DELETE ALL CREATED FILES <---
            if "delete all" in clean_cmd and any(w in clean_cmd for w in ["created", "made", "generated", "wrote"]):
                import shutil
                files = get_and_clear_created_files()
                count = 0
                for f in files:
                    if os.path.exists(f):
                        try:
                            if os.path.isdir(f): shutil.rmtree(f)
                            else: os.remove(f)
                            count += 1
                        except: pass
                reply = f"I have purged {count} files that I previously created sir. "
                self.speak_voice(reply)
                save_chat_message("assistant", reply)
                return

            # ---> SMART FILE EXTENSION CHANGER <---
            ext_match = re.search(r'change (?:the )?(?:type|extension) of (.*?) to (.*?)$', clean_cmd)
            if not ext_match: ext_match = re.search(r'change (.*?)(?:\'s)? (?:type|extension) to (.*?)$', clean_cmd)
            
            implicit_match = re.search(r'change (?:the )?(?:file )?(?:type|extension) to (.*?)$', clean_cmd)
            from_to_match = re.search(r'change (?:the )?(?:file )?(?:type|extension) from (.*?) to (.*?)$', clean_cmd)

            target_file = None
            new_ext = None
            is_implicit = False

            if from_to_match:
                new_ext = from_to_match.group(2).strip()
                is_implicit = True
            elif implicit_match:
                new_ext = implicit_match.group(1).strip()
                is_implicit = True
            elif ext_match:
                target_file = ext_match.group(1).strip()
                new_ext = ext_match.group(2).strip()
                if target_file in ["the file", "file", "it", "this"]:
                    is_implicit = True
                    target_file = None
                    
            if new_ext:
                new_ext = new_ext.replace("dot", "").strip(". ")
                if is_implicit:
                    last_file = globals().get("LAST_INTERACTED_FILE")
                    if last_file and os.path.exists(last_file):
                        success, reply = SYS_DRIVER.change_extension(last_file, new_ext)
                        if success:
                            new_path = os.path.splitext(last_file)[0] + "." + new_ext
                            globals()["LAST_INTERACTED_FILE"] = new_path
                            reply += " Sir, do you want me to open this updated file?"
                            DIALOGUE_STATE = {"mode": "AWAITING_OPEN_FILE", "pending_action": "OPEN_FILE", "payload": new_path}
                        self.speak_voice(reply)
                        save_chat_message("assistant", reply)
                        return
                    else:
                        reply = "Sir, can you please provide me the name of the file you want to change?"
                        DIALOGUE_STATE = {"mode": "AWAITING_FILE_NAME", "pending_action": "CHANGE_EXTENSION", "payload": new_ext}
                        self.speak_voice(reply)
                        save_chat_message("assistant", reply)
                        return
                else:
                    matches = SYS_DRIVER.locate_all_paths(target_file, target_type="FILE")
                    if len(matches) == 0:
                        reply = f"I could not find a file named '{target_file}', sir."
                        self.speak_voice(reply)
                        save_chat_message("assistant", reply)
                        return
                    elif len(matches) == 1:
                        success, reply = SYS_DRIVER.change_extension(matches[0], new_ext)
                        if success:
                            new_path = os.path.splitext(matches[0])[0] + "." + new_ext
                            globals()["LAST_INTERACTED_FILE"] = new_path
                            reply += " Sir, do you want me to open the changed file?"
                            DIALOGUE_STATE = {"mode": "AWAITING_OPEN_FILE", "pending_action": "OPEN_FILE", "payload": new_path}
                        self.speak_voice(reply)
                        save_chat_message("assistant", reply)
                        return
                    else:
                        locations = [os.path.basename(os.path.dirname(m)) for m in matches]
                        loc_str = ", ".join(f"one in {loc}" for loc in locations)
                        reply = f"I found multiple files named {target_file}, sir. {loc_str}. Which one should I target?"
                        DIALOGUE_STATE = {"mode": "AWAITING_FILE_LOCATION", "pending_action": "CHANGE_EXTENSION", "payload": {"matches": matches, "ext": new_ext}}
                        self.speak_voice(reply)
                        save_chat_message("assistant", reply)
                        return

            # --- SMART FILESYSTEM INTERCEPTOR ---
            fs_intent = parse_fs_intent(command)
            if fs_intent:
                act = fs_intent["action"]
                target_folder = None

                if act == "CREATE":
                    success, reply = SYS_DRIVER.smart_create(fs_intent["type"], fs_intent["name"], fs_intent["location"])
                    if success:
                        target_folder = SYS_DRIVER.resolve_directory(fs_intent["location"])
                        # Track it perfectly
                        found, path, _ = SYS_DRIVER.locate_path(fs_intent["name"], fs_intent["location"])
                        if found:
                            log_created_file(path)
                            globals()["LAST_INTERACTED_FILE"] = path

                elif act == "DELETE":
                    success, reply = SYS_DRIVER.smart_delete(fs_intent["name"])

                elif act == "MOVE":
                    success, reply = SYS_DRIVER.smart_move(fs_intent["name"], fs_intent["location"])

                elif act == "SEARCH":
                    found, path, is_dir = SYS_DRIVER.locate_path(fs_intent["name"], fs_intent["location"])
                    if found:
                        reply = f"Located at: {path}"
                        target_folder = path if is_dir else os.path.dirname(path)
                    else:
                        reply = f"Could not find '{fs_intent['name']}'."
                
                # ---> NEW ACTION: SMART OPEN <---
                elif act == "OPEN":
                    found, path, is_dir = SYS_DRIVER.locate_path(fs_intent["name"], fs_intent["location"])
                    if found:
                        os.startfile(path)
                        reply = f"Pulling up {fs_intent['name']} on your screen, sir."
                    else:
                        reply = f"Could not find '{fs_intent['name']}' to open."
                
                if target_folder:
                    reply += ", sir do you want to see?"
                    DIALOGUE_STATE = {"mode": "AWAITING_EXPLORER", "payload": target_folder}

                self.speak_voice(reply)
                save_chat_message("assistant", reply)
                return

            # ---> SMART NOTEPAD BRAIN (MOVED ABOVE BASIC LAUNCHER) <---
            # Also added 'draft' to triggers
            if "notepad" in clean_cmd and any(w in clean_cmd for w in ["write", "note", "type", "draft"]):
                content = clean_cmd
                for filler in ["open notepad and write", "write in notepad", "take a note saying", "take a note", "notepad write", "write", "type", "notepad", "saying", "that", "open", "in", "about", "for"]:
                    content = content.replace(filler, "").strip()
                
                if not content or content in ["something", "anything"]:
                    reply = "Sir, I have opened Notepad and I am ready to note. What should I write?"
                    subprocess.Popen(["notepad.exe"])
                    self.speak_voice(reply)
                    save_chat_message("assistant", reply)
                    return
                
                # BULLETPROOF NAMING
                safe_name = "".join(c for c in content if c.isalnum() or c.isspace()).strip().replace(' ', '_')
                if len(safe_name) > 20:
                    safe_name = safe_name[:20].rstrip('_')
                if not safe_name:
                    safe_name = "hydra_note"
                
                file_name = f"{safe_name}.txt"
                dest_dir = SYS_DRIVER.resolve_directory("desktop")
                full_path = os.path.join(dest_dir, file_name)
                
                friendly_name = safe_name.replace('_', ' ')
                self.speak_voice(f"Creating a new file named {friendly_name} and drafting the content, sir.")
                
                raw_rules = load_long_term_memories()[:8]
                formatted_rules = "\n".join([f"- {r}" for r in raw_rules]) if raw_rules else "None."
                
                sys_prompt = (
                    f"Your name is {ASSISTANT_NAME}. The user is {MASTER_NAME}. "
                    f"Core Memories & Rules:\n{formatted_rules}\n\n"
                    "Write ONLY the raw text that belongs inside the requested document. "
                    "Do not include markdown formatting, backticks, greetings, or conversational filler. "
                    f"If asked to write your name, write {ASSISTANT_NAME}. If asked to write the user's name, write {MASTER_NAME}. "
                    "Command: " + command
                )
                
                generated_content = ai_chat(DEFAULT_REASONING_MODEL, [{"role": "user", "content": sys_prompt}], num_predict=500, timeout=35)
                final_content = generated_content.strip() if generated_content else content
                
                try:
                    # strictly create the file first
                    with open(full_path, "w", encoding="utf-8") as f:
                        f.write(final_content)
                        
                    # Track this file globally so the correction system knows what to overwrite
                    globals()["LAST_INTERACTED_FILE"] = full_path
                    log_created_file(full_path)
                    subprocess.Popen(["notepad.exe", full_path])
                    reply = "New document created and pulled up on your screen, sir."
                except Exception as e:
                    reply = "I encountered a system error while trying to save the file, sir."
                    print(f"[FILE ERROR]: {e}")
                    
                self.speak_voice(reply)
                save_chat_message("assistant", reply)
                return

            commands = {
                "WIFI_ON": ["turn on wifi", "enable wifi"],
                "WIFI_OFF": ["turn off wifi", "disable wifi"],
                "WIFI_CONNECT": ["connect to"],
                "OPEN_APP": ["open ", "launch ", "start "],
                "CLOSE_APP": ["close ", "kill ", "stop app "],
                "LIST_APPS": ["list apps", "running apps", "open apps"],
                "SEARCH_FILE": ["find file ", "locate file ", "where is "],
                "CREATE_FOLDER": ["create folder ", "make folder ", "new folder "],
                "DELETE_FILE": ["delete file ", "remove file "],
                "SHUTDOWN": ["shut down computer", "turn off pc"],
                "RESTART": ["restart computer", "reboot pc"],
                "LOCK": ["lock pc", "lock the computer"]
            }

            executed = False
            for action, triggers in commands.items():
                matched_trigger = next((t for t in triggers if t in clean_cmd), None)
                if matched_trigger:
                    raw_low = command.strip().lower()
                    payload = raw_low.replace(matched_trigger, "").replace("wifi", "").strip()
                    payload = payload.rstrip(".?!") 
                    if not payload: payload = None

                    success, state = SYS_DRIVER.execute_command(action, payload)

                    if action == "WIFI_ON":
                        reply = "Wi-Fi adapter enabled, sir." if success else "Failed to enable Wi-Fi."
                    elif action == "WIFI_OFF":
                        reply = "Wi-Fi adapter disabled, sir." if success else "Failed to disable Wi-Fi."
                    elif action == "WIFI_CONNECT":
                        reply = f"Connected to {state}, sir." if success else f"Connection to {payload} failed."
                    elif action == "OPEN_APP":
                        reply = f"Opening {payload}, sir." if success else f"Failed to launch {payload}."
                    elif action == "CLOSE_APP":
                        reply = f"Closed {payload}, sir." if success else f"Could not find or close {payload}."
                    elif action == "LIST_APPS":
                        reply = f"Active processes: {state}" if success else "Could not retrieve process list."
                    elif action == "SEARCH_FILE":
                        reply = f"File located at: {state}" if success else state
                    elif action == "CREATE_FOLDER":
                        reply = state if success else f"Failed to create folder: {state}"
                    elif action == "DELETE_FILE":
                        reply = f"Item purged, sir." if success else state
                    elif action in ["SHUTDOWN", "RESTART", "LOCK"]:
                        reply = "Executing system command, sir."
                        self.speak_voice(reply)
                        return

                    self.speak_voice(reply)
                    save_chat_message("assistant", reply)
                    executed = True
                    break
            
            if executed: return

            if "setting" in clean_cmd:
                sub_settings = {
                    "bluetooth": ("ms-settings:bluetooth", "bluetooth"),
                    "display": ("ms-settings:display", "display"),
                    "screen": ("ms-settings:display", "display"),
                    "sound": ("ms-settings:sound", "audio"),
                    "audio": ("ms-settings:sound", "audio"),
                    "volume": ("ms-settings:sound", "audio"),
                    "apps": ("ms-settings:appsfeatures", "installed applications"),
                    "uninstall": ("ms-settings:appsfeatures", "installed applications"),
                    "update": ("ms-settings:windowsupdate", "windows update"),
                    "privacy": ("ms-settings:privacy", "privacy"),
                    "power": ("ms-settings:powersleep", "power and sleep"),
                    "sleep": ("ms-settings:powersleep", "power and sleep"),
                    "battery": ("ms-settings:batterysaver", "battery"),
                    "storage": ("ms-settings:storagesense", "storage")
                }
                for key, (uri, name) in sub_settings.items():
                    if key in clean_cmd:
                        subprocess.Popen(f"cmd /c start {uri}", shell=True)
                        reply = f"navigating directly to {name} settings, sir."
                        self.speak_voice(reply)
                        save_chat_message("assistant", reply)
                        return

            explorer_triggers = ["open", "go to", "show", "explorer", "folder", "drive", "check"]
            if any(t in clean_cmd for t in explorer_triggers):
                sub_folders = {
                    "downloads": ("shell:Downloads", "downloads"),
                    "download": ("shell:Downloads", "downloads"),
                    "documents": ("shell:Personal", "documents"),
                    "document": ("shell:Personal", "documents"),
                    "pictures": ("shell:My Pictures", "pictures"),
                    "picture": ("shell:My Pictures", "pictures"),
                    "photos": ("shell:My Pictures", "pictures"),
                    "music": ("shell:My Music", "music"),
                    "videos": ("shell:My Video", "videos"),
                    "desktop": ("shell:Desktop", "desktop"),
                    "recycle bin": ("shell:RecycleBinFolder", "recycle bin"),
                    "trash": ("shell:RecycleBinFolder", "recycle bin"),
                    "c drive": ("C:\\", "local disk C"),
                    "d drive": ("D:\\", "local disk D"),
                    "e drive": ("E:\\", "local disk E")
                }
                for key, (path, name) in sub_folders.items():
                    if key in clean_cmd:
                        subprocess.Popen(f"explorer {path}", shell=True)
                        reply = f"pulling up your {name} directory, sir."
                        self.speak_voice(reply)
                        save_chat_message("assistant", reply)
                        return

            launch_verbs = ["open ", "launch ", "start ", "run ", "pull up "]
            matched_verb = next((v for v in launch_verbs if clean_cmd.startswith(v)), None)

            if matched_verb:
                raw_target = clean_cmd.split(matched_verb, 1)[1].strip()
                for filler in ["please", "for me", "quickly", "the ", "an ", "app ", "application "]:
                    if raw_target.startswith(filler): raw_target = raw_target[len(filler):].strip()
                    if raw_target.endswith(filler.strip()): raw_target = raw_target[:-len(filler.strip())].strip()

                if raw_target:
                    sys_maps = {
                        "settings": "ms-settings:", "control panel": "control",
                        "command prompt": "cmd", "file explorer": "explorer",
                        "task manager": "taskmgr", "calculator": "calc",
                        "notepad": "notepad", "chrome": "chrome", "spotify": "spotify"
                    }
                    all_known = list(sys_maps.keys()) + list(APPS_CACHE.keys())
                    best_match = difflib.get_close_matches(raw_target, all_known, n=1, cutoff=0.35)
                    app_name = best_match[0] if best_match else raw_target

                    self.speak_voice(f"sir, opening {app_name}")

                    try:
                        if app_name in sys_maps:
                            subprocess.Popen(f"cmd /c start {sys_maps[app_name]}", shell=True)
                        elif app_name in APPS_CACHE:
                            os.startfile(APPS_CACHE[app_name])
                        else:
                            subprocess.Popen(f"cmd /c start {app_name}", shell=True)
                    except Exception as e:
                        print(f"[FAST LAUNCH ERROR]: {e}")

                    low = app_name.lower()
                    if any(x in low for x in ["notepad", "word", "docs", "writer"]):
                        followup = f"sir, {app_name} ready, do you want to write something on it?"
                    elif any(x in low for x in ["spotify", "music", "youtube", "vlc"]):
                        followup = "app ready sir, what do you like to listen to?"
                    elif any(x in low for x in ["chrome", "edge", "browser", "google"]):
                        followup = f"sir, {app_name} ready, what are we searching today?"
                    elif any(x in low for x in ["canva", "photoshop", "paint", "figma"]):
                        followup = f"sir, {app_name} ready, what are we designing today?"
                    else:
                        followup = f"sir, {app_name} ready, what do you want to do here?"

                    time.sleep(3.2)
                    self.speak_voice(followup)
                    threading.Thread(target=lambda: save_chat_message("assistant", followup), daemon=True).start()
                    return

            intent_type = "CHAT"
            app_target = None
            app_opened_successfully = False
            is_state_handled = False
            is_correction = False
            reply = ""
            
            instant_responses = {
                "hey": ["Yes, sir.", "At your service, sir.", "hir sir, what's up", "hi sir, i am Ready."],
                "hello": ["si, sir. how are you.", "hey, what we are going to do sir..", "hi sir, what we are going to do."],
                "hi": ["Hello, sir.","hi, 'hydra' here, what we are going to do." , "hi sir, At your service."],
                "hydra": ["Listening, sir.","Listening, sir.", "Yes, I am here."],
                "status": ["All systems operating within normal parameters, sir.", "Core status nominal. Fully active."],
                "what is your name": [f"My name is {ASSISTANT_NAME}.", f"I am {ASSISTANT_NAME}, your system automation assistant."],
                "whats your name": [f"My name is '{ASSISTANT_NAME}'.", f"You can call me '{ASSISTANT_NAME}'."],
                "who are you": [f"I am '{ASSISTANT_NAME}', your assistant."],
                "what is my name": [f"sir, your name is '{MASTER_NAME}'."],
                "how are you": ["I'm operating perfectly, sir. How are you?", "All systems optimal, sir. Yourself?"],
                "how are you doing": ["Running smoothly, sir. How about you?", "I'm doing great, sir. And you?"],
                "how is it going": ["Going well, sir. Ready when you are.", "All good on my end, sir."],
                "how's it going": ["Going well, sir. Ready when you are.", "All good on my end, sir."],
                "whats up": ["Not much, sir. Ready for your command.", "All quiet here, sir. What do you need?"],
                "what's up": ["Not much, sir. Ready for your command.", "All quiet here, sir. What do you need?"],
                "good morning": ["Good morning, sir.", "Morning, sir. Ready to assist."],
                "good night": ["Good night, sir. Rest well.", "Sleep well, sir."],
                "thank you": ["You're welcome, sir.", "Anytime, sir."],
                "thanks": ["You're welcome, sir.", "Happy to help, sir."],
            }
            
            if DIALOGUE_STATE["mode"] == "AWAITING_CONFIRMATION":
                if "yes" in clean_cmd or "sure" in clean_cmd or "do it" in clean_cmd or "go ahead" in clean_cmd:
                    app_target = DIALOGUE_STATE["payload"]
                    DIALOGUE_STATE = {"mode": "NORMAL", "pending_action": None, "payload": None}
                    print(f"[DIALOGUE MANAGER] Confirmation approved for target: {app_target}")
                    is_state_handled = True
                elif "no" in clean_cmd or "cancel" in clean_cmd or "stop" in clean_cmd:
                    reply = "Understood, sir. Operation cancelled."
                    DIALOGUE_STATE = {"mode": "NORMAL", "pending_action": None, "payload": None}
                    print("[DIALOGUE MANAGER] Confirmation rejected by user.")
                    is_state_handled = True
                else:
                    reply = "I am waiting for your confirmation, sir. Should I proceed?"
                    is_state_handled = True

            # ---> NEW: EXPLORER POP-UP LISTENER <---
            elif DIALOGUE_STATE["mode"] == "AWAITING_EXPLORER":
                if any(w in clean_cmd for w in ["yes", "sure", "yeah", "yup", "ok", "show", "open", "please"]):
                    folder_to_open = DIALOGUE_STATE["payload"]
                    subprocess.Popen(f'explorer "{folder_to_open}"', shell=True)
                    reply = "Opening location on your screen, sir."
                    DIALOGUE_STATE = {"mode": "NORMAL", "pending_action": None, "payload": None}
                    is_state_handled = True
                elif any(w in clean_cmd for w in ["no", "nope", "nah", "cancel", "nevermind", "leave"]):
                    reply = "As you wish, sir."
                    DIALOGUE_STATE = {"mode": "NORMAL", "pending_action": None, "payload": None}
                    is_state_handled = True
                else:
                    reply = "Sir, do you want me to pull up that folder? Yes or no?"
                    is_state_handled = True

            elif DIALOGUE_STATE["mode"] == "AWAITING_OPEN_FILE":
                if any(w in clean_cmd for w in ["yes", "sure", "yeah", "yup", "ok", "open", "please"]):
                    target_file = DIALOGUE_STATE["payload"]
                    if target_file and os.path.exists(target_file):
                        os.startfile(target_file)
                        reply = "Opening file on your screen, sir."
                    else:
                        reply = "File could not be found to open, sir."
                    DIALOGUE_STATE = {"mode": "NORMAL", "pending_action": None, "payload": None}
                    is_state_handled = True
                elif any(w in clean_cmd for w in ["no", "nope", "nah", "cancel", "nevermind", "leave"]):
                    reply = "Understood, sir."
                    DIALOGUE_STATE = {"mode": "NORMAL", "pending_action": None, "payload": None}
                    is_state_handled = True
                else:
                    reply = "Sir, do you want me to open the updated file? Yes or no?"
                    is_state_handled = True

            # ---> NEW: SMART FILE EDITING & CORRECTION LISTENER <---
            edit_triggers = ["should have wrote", "should have written", "instead write", "write this instead", "replace the text", "remove that and write"]
            if any(nt in clean_cmd for nt in edit_triggers):
                new_text = command
                for nt in edit_triggers:
                    if nt in clean_cmd:
                        idx = command.lower().find(nt) + len(nt)
                        # Removes filler words like 'with' or 'to' just after the trigger
                        new_text = command[idx:].strip(' \'".,:')
                        if new_text.lower().startswith("with "): new_text = new_text[5:].strip(' \'".,:')
                        if new_text.lower().startswith("to "): new_text = new_text[3:].strip(' \'".,:')
                        break
                
                # Try to extract a specific filename if mentioned: "in my_file replace the text..."
                file_target = None
                file_match = re.search(r'(?:in|on|inside) (.*?) (?:replace|remove|write)', clean_cmd)
                if file_match:
                    found, path, is_dir = SYS_DRIVER.locate_path(file_match.group(1).strip(), target_type="FILE")
                    if found: file_target = path
                
                # Fallback to the last file we interacted with or created
                if not file_target:
                    file_target = globals().get("LAST_INTERACTED_FILE")
                
                if file_target and os.path.exists(file_target):
                    try:
                        with open(file_target, "w", encoding="utf-8") as f:
                            f.write(new_text)
                        
                        last_cmd = get_last_user_command()
                        if last_cmd:
                            memory_string = f"CRITICAL RULE: When told '{last_cmd.strip()}', the EXACT document text must be: {new_text}"
                            save_long_term_memory(memory_string)
                        
                        globals()["LAST_INTERACTED_FILE"] = file_target
                        subprocess.Popen(["notepad.exe", file_target])
                        reply = "I have updated the file and logged this correction into my core memory, sir."
                        is_correction = True
                    except Exception as e:
                        reply = f"Failed to rewrite file, sir: {e}" 
                        is_correction = True
                else:
                    reply = "I couldn't determine which file you meant, sir. Please specify the name."
                    is_correction = True

            global_rule_triggers = ["new rule", "from now on", "behavioral rule", "talk like", "act like"]
            
            if any(gt in clean_cmd for gt in global_rule_triggers):
                clean_fact = command
                for gt in global_rule_triggers:
                    if gt in clean_cmd:
                        idx = command.lower().find(gt) + len(gt)
                        clean_fact = command[idx:].replace('"', '').replace("'", "").strip(",:! \n")
                        break
                
                save_long_term_memory(f"CRITICAL RULE: {clean_fact}")
                reply = "Understood, sir i will be carefull next time."
                is_correction = True
            else:
                correction_triggers = ["answer like this", "say this instead", "reply like this", "should reply", "should have replied", "replied", "when i ask"]
                if any(trigger in clean_cmd for trigger in correction_triggers):
                    quotes = re.findall(r'"([^"]+)"', command)
                    if not quotes:
                        quotes = re.findall(r"'([^']+)'", command)
                        
                    user_query = ""
                    forced_response = ""
                    
                    if len(quotes) >= 2:
                        user_query = quotes[0].strip().lower()
                        forced_response = quotes[1].strip()
                    elif len(quotes) == 1:
                        forced_response = quotes[0].strip()
                        last_user_cmd = get_last_user_command()
                        if last_user_cmd:
                            user_query = last_user_cmd.strip().lower()
                    else:
                        text_lower = command.lower()
                        if "when i ask" in text_lower:
                            try:
                                parts = text_lower.split("when i ask", 1)[1]
                                for splitter in ["you should reply", "you should say", "you should", "reply", "just"]:
                                    if splitter in parts:
                                        sub_parts = parts.split(splitter, 1)
                                        user_query = sub_parts[0].strip()
                                        forced_response = sub_parts[1].strip()
                                        if user_query.startswith("you "): 
                                            user_query = user_query[4:].strip()
                                        break
                            except Exception:
                                pass

                    if user_query and forced_response:
                        user_query = user_query.replace(".", "").replace("!", "").replace("?", "").strip()
                        context_track = get_context_snapshot()
                        save_contextual_rule(user_query, forced_response, context_track)
                        reply = "Understood, sir. Mapped those exact phrase parameters to this conversation track."
                        is_correction = True

            if is_correction or is_state_handled:
                pass
            else:
                context_track = get_context_snapshot()
                matched_rule = find_contextual_rule(clean_cmd, context_track)
                if matched_rule:
                    print(f"[LEARNING CORE] Context Matching Rule Triggered: '{matched_rule}'")
                    reply = matched_rule
                elif clean_cmd in instant_responses:
                    reply = random.choice(instant_responses[clean_cmd])
                elif is_simple_conversation(clean_cmd):
                    print("[ROUTER] Fast conversational path")
                    quick_reply = ai_chat(
                        DEFAULT_FAST_MODEL,
                        [
                            {
                                "role": "system",
                                "content": (
                                    f"You are {ASSISTANT_NAME}, {MASTER_NAME}'s loyal desktop assistant. "
                                    "Address him as sir. Reply in one short spoken sentence, max 12 words."
                                ),
                            },
                            {"role": "user", "content": command},
                        ],
                        num_predict=25,
                        num_ctx=256,
                        timeout=5,
                    )
                    reply = quick_reply or "I'm here and ready, sir."
                else:
                    try:
                        history_context = load_chat_context()
                        learned_facts = retrieve_relevant_memories(clean_cmd)
                        facts_context = ", ".join(learned_facts[:6]) if learned_facts else "None."
                        
                        intent_type = "CHAT"
                        extracted_target = None
                        launch_phrases = [
                            "open up", "launch", "open", "start", "run", "execute", 
                            "go to", "fire up", "pull up", "bring up", "turn on", "boot up"
                        ]
                        info_phrases = [
                            "what is", "who is", "tell me about", "tell me something about", 
                            "search for", "define", "explain", "do you know about", 
                            "information on", "information about"
                        ]
                        
                        for phrase in launch_phrases:
                            if clean_cmd.startswith(phrase + " "):
                                intent_type = "LAUNCH"
                                extracted_target = clean_cmd.split(phrase, 1)[1].strip()
                                break
                            elif " " + phrase + " " in " " + clean_cmd + " ":
                                preceding_text = clean_cmd.split(" " + phrase + " ", 1)[0]
                                conversational_markers = ["like", "about", "how", "show", "tell", "explain", "example"]
                                if not any(marker in preceding_text for marker in conversational_markers):
                                    intent_type = "LAUNCH"
                                    extracted_target = clean_cmd.split(" " + phrase + " ", 1)[1].strip()
                                    break

                        if intent_type == "CHAT":
                            # Check if the phrase is anywhere in the command, not just at the start
                            if any(p in clean_cmd for p in info_phrases) or "meaning of" in clean_cmd:
                                intent_type = "INFO"

                        if intent_type == "LAUNCH" and extracted_target:
                            for filler in ["please", "for me", "quickly", "the", "an", "app", "application"]:
                                if extracted_target.startswith(filler + " "):
                                    extracted_target = extracted_target.replace(filler + " ", "", 1)
                                if extracted_target.endswith(" " + filler):
                                    extracted_target = extracted_target[:-len(" " + filler)].strip()
                            app_target = extracted_target
                            print(f"[INTENT AGENT] Classified: LAUNCH | Target Entity: '{app_target}'")
                        else:
                            print(f"[INTENT AGENT] Classified: {intent_type}")

                        if intent_type == "LAUNCH":
                            selected_model = DEFAULT_FAST_MODEL
                            system_prompt = (
                                f"Your name is {ASSISTANT_NAME}. Address master as sir. "
                                f"Return line 1 EXACTLY as: 'LAUNCH: {app_target}'. "
                                "Line 2: Friendly 5-word confirmation."
                            )
                            model_options = {"num_predict": 30, "num_ctx": 256}
                        elif intent_type == "INFO":
                            selected_model = DEFAULT_FAST_MODEL
                            retrieved_context = execute_information_retrieval(clean_cmd)
                            print(f"[IR AGENT] Fetched Live Data Payload: {retrieved_context}")
                            system_prompt = (
                                f"You are {ASSISTANT_NAME}. Summarize this data for sir in 15 words max:\n[{retrieved_context}]"
                            )
                            model_options = {"num_predict": 45, "num_ctx": 256}
                        else:
                            selected_model = DEFAULT_FAST_MODEL
                            raw_rules = load_long_term_memories()[:5]
                            formatted_rules = "\n".join([f"- {r}" for r in raw_rules]) if raw_rules else "None."
                            system_prompt = (
                                f"You are {ASSISTANT_NAME}, {MASTER_NAME}'s desktop AI. Address him as sir. Keep replies conversational and under 25 words.\n"
                                f"Rules:\n{formatted_rules}\nFacts:\n{facts_context}"
                            )
                            model_options = {"num_predict": 50, "num_ctx": 256}
                        
                        print(f"[ROUTER] Active Intelligence Core: {selected_model}")
                        system_instruction = {"role": "system", "content": system_prompt}
                        conversation_payload = [system_instruction] + history_context
                        
                        llm_reply = ai_chat(
                            selected_model,
                            conversation_payload,
                            num_predict=model_options.get("num_predict", 50),
                            num_ctx=model_options.get("num_ctx", 256),
                            timeout=22,
                        )
                        if llm_reply:
                            reply = llm_reply
                        else:
                            reply = "I'm here, sir. Could you repeat that?"
                    except Exception as e:
                        print(f"\n[DEBUG] Core Communication Error: {e}")
                        self.speak_voice("Failed to communicate with local intelligence core.")
                        return

            if not is_state_handled:
                print(f"[DEBUG] Raw AI Response:\n{reply}\n")
                cleaned_reply = reply.replace("*", "").replace("`", "").strip()

                if not is_correction:
                    if "memorize:" in cleaned_reply.lower():
                        try:
                            for line in cleaned_reply.split("\n"):
                                if "memorize:" in line.lower():
                                    idx = line.lower().find("memorize:") + len("memorize:")
                                    extracted_fact = line[idx:].strip()
                                    if extracted_fact:
                                        save_long_term_memory(extracted_fact)
                                        print(f"[DEBUG] Permanent Memory Locked In: '{extracted_fact}'")
                        except Exception:
                            pass
                    
                    if "launch:" in cleaned_reply.lower():
                        try:
                            lines = cleaned_reply.split("\n")
                            launch_line = [l for l in lines if "launch:" in l.lower()][0]
                            idx = launch_line.lower().find("launch:") + len("launch:")
                            app_target = launch_line[idx:].strip().lower()
                            if app_target.startswith("open "):
                                app_target = app_target.replace("open ", "", 1)
                        except Exception:
                            pass
                    else:
                        if intent_type == "LAUNCH":
                            conversational_triggers = ["opening", "openin'", "launching", "launchin'", "start", "starting", "open", "launch"]
                            for trigger in conversational_triggers:
                                match = re.search(r'\b' + re.escape(trigger) + r'\b', cleaned_reply.lower())
                                if match:
                                    idx = match.end()
                                    remainder = cleaned_reply[idx:].strip()
                                    if remainder:
                                        potential = remainder.split()[0].replace(".", "").replace("!", "").replace(",", "").replace("...", "").strip().lower()
                                        if potential and len(potential) > 1:
                                            app_target = potential
                                            print(f"[DEBUG] Conversational Intercept Triggered! Target: '{app_target}'")
                                            break

                    if app_target:
                        word_count = len(app_target.split())
                        has_punctuation = any(char in app_target for char in ['.', '!', ',', '?', '...'])
                        if word_count > 3 or has_punctuation:
                            print(f"[DEBUG] Target '{app_target}' rejected by Safety Valve.")
                            app_target = None

            if app_target:
                try:
                    system_mappings = {
                        "settings": "ms-settings:",
                        "control panel": "control",
                        "controlpanel": "control",
                        "command prompt": "cmd",
                        "file explorer": "explorer",
                        "task manager": "taskmgr",
                        "calculator": "calc",
                        "notepad": "notepad",
                        "chrome": "chrome"
                    }
                    all_targets = list(system_mappings.keys()) + list(APPS_CACHE.keys())
                    matches = difflib.get_close_matches(app_target, all_targets, n=1, cutoff=0.4)
                    if matches:
                        matched_key = matches[0]
                        if app_target != matched_key and app_target not in system_mappings and not is_state_handled:
                            DIALOGUE_STATE = {
                                "mode": "AWAITING_CONFIRMATION",
                                "pending_action": "LAUNCH",
                                "payload": matched_key
                            }
                            reply = f"Did you mean to open {matched_key}, sir?"
                            print(f"[DIALOGUE MANAGER] Entering verification mode for: {matched_key}")
                        else:
                            print(f"[DEBUG] Executing Target Match: '{matched_key}'")
                            self.speak_voice(f"opening {matched_key}")
                            
                            if matched_key in system_mappings:
                                subprocess.Popen(f"cmd /c start {system_mappings[matched_key]}", shell=True)
                            elif matched_key in APPS_CACHE:
                                os.startfile(APPS_CACHE[matched_key])
                                
                            app_target = matched_key
                            app_opened_successfully = True
                            time.sleep(3.5)
                    else:
                        print(f"[DEBUG] Direct system execution fallback for: '{app_target}'")
                        self.speak_voice(f"opening {app_target}")
                        subprocess.Popen(f"cmd /c start {app_target}", shell=True)
                        app_opened_successfully = True
                        time.sleep(3.5)
                except Exception as e:
                    print(f"[DEBUG] System Automation Error: {e}")

            if not is_state_handled:
                clean_lines = []
                for line in cleaned_reply.split("\n"):
                    if "launch:" not in line.lower() and "memorize:" not in line.lower():
                        clean_lines.append(line)
                reply = "\n".join(clean_lines).strip()
                if app_opened_successfully:
                    reply = f"sir, {app_target} ready, what do you want here."
                elif not reply:
                    reply = "Done, sir."

            self.speak_voice(reply)
            print(f"[TIMING] Reply ready in {time.perf_counter() - pipeline_start:.1f}s")
            threading.Thread(target=lambda: save_chat_message("assistant", reply), daemon=True).start()

        threading.Thread(target=background_processor, daemon=True).start()

    def speak_voice(self, text):
        # ---> THIS PRINTS HYDRA'S REPLY TO YOUR TERMINAL <---
        print(f"\n[HYDRA SAYS]: {text}\n")
        
        def voice_worker():
            import time
            t0 = time.perf_counter()
            cleaned_text = text.replace("h.y.d.r.a", "Hydra").replace("H.Y.D.R.A", "Hydra").replace(".", " ")
            spoken = False

            def speak_offline():
                nonlocal spoken
                with TTS_LOCK:  # Forces threads to stand in line to use the Windows Voice
                    engine = get_tts_engine()
                    engine.say(cleaned_text)
                    engine.runAndWait()
                    spoken = True
                    print(f"[VOICE] Offline speech successful ({time.perf_counter() - t0:.1f}s).")

            try:
                print("[VOICE] Attempting cloud synthesis...")
                async def generate_audio():
                    communicate = edge_tts.Communicate(
                        cleaned_text,
                        "en-GB-RyanNeural",
                        pitch="-25Hz",
                        rate="-8%",
                    )
                    audio_data = bytearray()
                    async for chunk in communicate.stream():
                        if chunk["type"] == "audio":
                            audio_data.extend(chunk["data"])
                    return bytes(audio_data)

                async def generate_with_timeout():
                    # Dropped to 1.2s so offline fallback happens almost instantly
                    return await asyncio.wait_for(generate_audio(), timeout=7)

                global TTS_LOOP
                if TTS_LOOP is None or TTS_LOOP.is_closed():
                    TTS_LOOP = asyncio.new_event_loop()
                raw_audio = TTS_LOOP.run_until_complete(generate_with_timeout())
                audio_stream = io.BytesIO(raw_audio)
                ensure_pygame_mixer()
                pygame.mixer.music.load(audio_stream, "mp3")
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    pygame.time.Clock().tick(10)
                pygame.mixer.music.unload()
                spoken = True
                print(f"[VOICE] Cloud speech successful ({time.perf_counter() - t0:.1f}s).")
            except Exception as online_err:
                print(f"[VOICE] Cloud speech dropped ({time.perf_counter() - t0:.1f}s): {online_err}. Using offline voice...")
                try:
                    try:
                        if PYGAME_MIXER_READY:
                            pygame.mixer.music.stop()
                            pygame.mixer.music.unload()
                    except Exception:
                        pass
                    speak_offline()
                except Exception as offline_err:
                    print(f"[VOICE] Offline speech failed: {offline_err}")

            if not spoken:
                try:
                    speak_offline()
                except Exception as offline_err:
                    print(f"[VOICE] Both voice engines failed: {offline_err}")

        threading.Thread(target=voice_worker, daemon=True).start()

if __name__ == "__main__":
    init_memory_db()
    populate_apps_cache()
    ensure_pygame_mixer()
    warm_up_ai_models()
    threading.Thread(target=get_tts_engine, daemon=True).start()
    threading.Thread(target=historical_learning_worker, daemon=True).start() # <--- Starts the Deep Sleep learner
    threading.Thread(target=network_monitor_worker, daemon=True).start() # <--- Starts the Real Network HUD Tracker
    
    app = QApplication(sys.argv)
    
    # Safe executor function that runs on the main GUI thread
    def _run_js_safe(code):
        if ACTIVE_PAGE:
            ACTIVE_PAGE.runJavaScript(code)
            
    # Connect our bridge signal to the safe executor
    GUI_SIGNALS.js_signal.connect(_run_js_safe)
    
    window = QMainWindow()
    window.setWindowTitle("HYDRA System Interface")
    window.resize(1000, 700)
    view = QWebEngineView()
    
    # --- FIX: ENSURE REMOTE IMAGES LOAD PROPERLY ---
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    settings = view.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    # -----------------------------------------------
    
    page = HydraPage(view)
    ACTIVE_PAGE = page
    view.setPage(page)
    view.setUrl(QUrl.fromLocalFile(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")))
    window.setCentralWidget(view)
    window.show()
    sys.exit(app.exec())