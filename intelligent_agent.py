"""
HYDRA Super-Intelligence & Conversational Intent Reasoning Layer
Enables HYDRA to act like a true sentient AI partner:
1. Proactive clarifying questions: Inquires before executing indirect or ambiguous desires
   (e.g., "I want to design something" -> "Would you like me to open Canva for designing, sir?")
2. Contact disambiguation: Resolves duplicate or similar names interactively
3. Real-time screen awareness integration (Phase 9)
4. Charismatic, alive, natural persona with zero robotic stiffness
"""

import re
import difflib
from typing import Tuple, Optional, Dict, Any, List
from screen_watcher import SCREEN_WATCHER

# Common intent mappings for high-speed proactive suggestions
INTENT_APP_MAP = [
    {
        "patterns": [r"design\s+(?:something|a\s+poster|a\s+flyer|graphics|a\s+card|thumbnails?)", r"make\s+a\s+poster", r"create\s+a\s+graphic"],
        "app": "canva",
        "question": "Would you like me to open Canva for designing, sir?"
    },
    {
        "patterns": [r"listen\s+to\s+(?:music|songs?|tracks?)", r"play\s+some\s+music", r"put\s+on\s+some\s+songs?", r"vibe\s+to\s+music"],
        "app": "spotify",
        "question": "Should I launch Spotify for your music, sir?"
    },
    {
        "patterns": [r"write\s+(?:some\s+)?code", r"do\s+some\s+coding", r"program\s+something", r"edit\s+(?:some\s+)?scripts?", r"write\s+python"],
        "app": "code",
        "question": "Shall I bring up Visual Studio Code for your programming, sir?"
    },
    {
        "patterns": [r"take\s+(?:some\s+)?notes?", r"write\s+(?:some\s+)?notes?", r"jot\s+(?:down\s+)?something", r"draft\s+a\s+note"],
        "app": "notepad",
        "question": "Would you like me to open Notepad for your notes, sir?"
    },
    {
        "patterns": [r"watch\s+(?:some\s+)?(?:videos?|youtube|tutorials?)", r"watch\s+something", r"put\s+on\s+a\s+video"],
        "action_type": "OPEN_URL",
        "payload": "https://www.youtube.com",
        "question": "Should I open YouTube in your browser, sir?"
    },
    {
        "patterns": [r"check\s+(?:my\s+)?messages?", r"see\s+who\s+texted", r"chat\s+with\s+someone"],
        "app": "whatsapp",
        "question": "Would you like me to bring up WhatsApp, sir?"
    }
]

def check_screen_context_intent(cmd: str) -> Optional[Tuple[bool, str]]:
    """
    Handles Phase 9 queries regarding what is happening on screen or previous activity.
    Always analyzes the window UNDERNEATH the HYDRA interface.
    """
    c = cmd.lower().replace(".", "").replace("?", "").replace("!", "").strip()
    
    # 1. WHAT AM I DOING RIGHT NOW? / WHAT IS ON MY SCREEN?
    if any(c == p for p in [
        "what am i doing right now", "what am i doing", "what am i doing rn",
        "what is on my screen", "whats on my screen", "what's on my screen",
        "describe my screen", "what am i looking at", "tell me what is on my screen",
        "what is currently on my screen", "what's on screen", "what is on screen",
        "where am i right now", "current screen", "screen status"
    ]) or any(c.startswith(p) for p in ["what am i doing", "what's on my screen", "what is on my screen", "describe what is on"]):
        reply = SCREEN_WATCHER.describe_current_activity()
        return True, reply

    # 2. WHAT WAS I DOING BEFORE THIS?
    if any(c == p for p in [
        "what was i doing before this", "what was i doing before",
        "what was the last thing i worked on", "what was my last app",
        "what app was i using before", "previous activity"
    ]) or any(c.startswith(p) for p in ["what was i doing", "what was the last thing"]):
        reply = SCREEN_WATCHER.describe_previous_activity()
        return True, reply

    # 3. EXPLAIN THIS ERROR / SCREEN CONTENT
    if any(k in c for k in ["explain this error", "explain the error", "what is this error", "diagnose screen", "troubleshoot this error"]):
        reply = SCREEN_WATCHER.explain_screen_content_or_error()
        return True, reply

    return None

def check_indirect_intent(cmd: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    Detects if the user expresses an indirect desire ("I want to design something")
    rather than a rigid command ("open canva"), formulating a clarifying question.
    """
    c = cmd.lower().replace(".", "").replace("?", "").replace("!", "").strip()
    clean = re.sub(r'^(?:hey\s+|hi\s+|ok\s+)?hydra[\s,:]+', '', c).strip()
    clean = re.sub(r'^(?:i\s+want\s+to|i\s+wanna|i\s+need\s+to|i\s+would\s+like\s+to|let\'s|lets|how\s+about\s+we)\s+', '', clean).strip()

    for item in INTENT_APP_MAP:
        for pat in item["patterns"]:
            if re.search(pat, clean):
                action_type = item.get("action_type", "OPEN_APP")
                payload = item.get("app") or item.get("payload")
                state = {
                    "mode": "AWAITING_INTENT_CONFIRMATION",
                    "pending_action": action_type,
                    "payload": payload
                }
                return item["question"], state

    return None

def handle_intent_confirmation(user_response: str, dialogue_state: Dict[str, Any], sys_driver: Any) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Executes or cancels the pending action when user responds to a clarifying question.
    """
    ans = user_response.lower().replace(".", "").replace("!", "").strip()
    positive = ["yes", "yeah", "yep", "sure", "please", "do it", "go ahead", "open it", "confirm", "of course", "why not", "ok", "okay"]
    negative = ["no", "nope", "cancel", "don't", "dont", "never mind", "nevermind", "stop", "abort"]

    if any(ans == p or ans.startswith(p + " ") for p in positive):
        action_type = dialogue_state.get("pending_action")
        payload = dialogue_state.get("payload")
        
        if action_type == "OPEN_APP" and payload:
            sys_driver.open_application(payload)
            reset_state = {"mode": "NORMAL", "pending_action": None, "payload": None}
            return True, f"Opening {payload.capitalize()} for you now, sir.", reset_state
            
        elif action_type == "OPEN_URL" and payload:
            import webbrowser
            webbrowser.open(payload)
            reset_state = {"mode": "NORMAL", "pending_action": None, "payload": None}
            return True, "Opening YouTube for you now, sir.", reset_state

    elif any(ans == n or ans.startswith(n + " ") for n in negative):
        reset_state = {"mode": "NORMAL", "pending_action": None, "payload": None}
        return True, "Understood, sir. Standing by.", reset_state

    # If the user answered with something else, reset and treat as normal command
    reset_state = {"mode": "NORMAL", "pending_action": None, "payload": None}
    return False, "", reset_state

def handle_contact_disambiguation(user_response: str, dialogue_state: Dict[str, Any], sys_driver: Any) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Resolves which contact was chosen when multiple contacts matched in WhatsApp.
    """
    candidates = dialogue_state.get("payload", {}).get("candidates", [])
    original_msg = dialogue_state.get("payload", {}).get("message")
    ans = user_response.lower().strip()
    
    # 1. Match ordinal references: "first one", "second one", "the 1st", "1"
    chosen_contact = None
    if any(ans == w or w in ans for w in ["first", "1st", "1", "the first one", "first one"]):
        if len(candidates) >= 1:
            chosen_contact = candidates[0]
    elif any(ans == w or w in ans for w in ["second", "2nd", "2", "the second one", "second one"]):
        if len(candidates) >= 2:
            chosen_contact = candidates[1]
    elif any(ans == w or w in ans for w in ["third", "3rd", "3", "the third one", "third one"]):
        if len(candidates) >= 3:
            chosen_contact = candidates[2]

    # 2. Fuzzy text match against candidate names
    if not chosen_contact:
        best_ratio = 0.0
        for cand in candidates:
            r = difflib.SequenceMatcher(None, ans, cand.lower()).ratio()
            if ans in cand.lower():
                r += 0.3
            if r > best_ratio:
                best_ratio = r
                chosen_contact = cand

    if chosen_contact:
        from dodaios_operator import DODAIOS_OPERATOR
        report = DODAIOS_OPERATOR.execute_goal(f"send message to {chosen_contact} saying {original_msg}", sys_driver=sys_driver)
        reply = report.spoken_reply if (report and report.spoken_reply) else f"Sir, I have sent your message to {chosen_contact}."
        return True, reply, {"mode": "NORMAL", "pending_action": None, "payload": None}

    reset_state = {"mode": "NORMAL", "pending_action": None, "payload": None}
    return True, f"I couldn't determine which contact you meant, sir. Please specify the full name.", reset_state


def is_semantic_message_request(msg: str) -> bool:
    """
    Checks if a requested message is a high-level conceptual topic
    (e.g. 'saying about spotify', 'about dinner', 'telling him about the cricket match', 'spotify')
    rather than a literal message ('i am on my way').
    """
    if not msg:
        return False
    low = msg.strip().lower()
    triggers = [
        "about", "regarding", "concerning", "saying about", "saying to check out",
        "telling him about", "telling her about", "tell him about", "tell her about",
        "say about", "talk about", "info on"
    ]
    if any(low == t or low.startswith(t + " ") or f" {t} " in f" {low} " for t in triggers):
        return True
        
    common_topics = ["spotify", "cricket", "meeting", "lunch", "dinner", "weather", "music", "movies", "coding", "python"]
    if low in common_topics:
        return True
    return False


def expand_semantic_message(raw_msg: str, recipient: str = "") -> str:
    """
    Intelligently expands conceptual topics into friendly, conversational 1-2 sentence
    WhatsApp messages (e.g. 'about spotify' -> 'Hey Tanzeel, have you checked out Spotify?...').
    If the message is literal (e.g. 'i am reaching in five minutes'), leaves it unmodified.
    """
    if not is_semantic_message_request(raw_msg):
        # Literal message body
        return raw_msg.strip()

    clean = raw_msg.strip()
    prefixes = [
        "saying about ", "telling him about ", "telling her about ",
        "tell him about ", "tell her about ", "saying to check out ",
        "say about ", "talk about ", "about ", "regarding ", "concerning ", "info on "
    ]
    for p in prefixes:
        idx = clean.lower().find(p)
        if idx != -1:
            clean = clean[idx + len(p):].strip()
            break

    topic = clean
    recip_name = recipient.title() if recipient else "my friend"

    # 1. Try Nebius AI Cloud / LLM if available
    try:
        from ai_engine import is_nebius_configured, nebius_chat, DEFAULT_FAST_MODEL
        if is_nebius_configured():
            prompt = [
                {
                    "role": "system",
                    "content": (
                        "You are drafting a friendly, natural WhatsApp text message from the user to their friend. "
                        "The user wants to tell them about a specific topic. "
                        "Draft a casual, engaging 1-to-2 sentence message telling them about this topic. "
                        "CRITICAL: Return ONLY the exact message text to send. No quotes, no preamble, no markdown."
                    )
                },
                {
                    "role": "user",
                    "content": f"Contact: {recip_name}\nTopic to tell them about: {topic}"
                }
            ]
            res = nebius_chat(prompt, model=DEFAULT_FAST_MODEL, max_tokens=100, temperature=0.6, timeout=2.0)
            if res and len(res.strip()) > 5:
                out = res.strip()
                if (out.startswith('"') and out.endswith('"')) or (out.startswith("'") and out.endswith("'")):
                    out = out[1:-1].strip()
                return out
    except Exception as e:
        print(f"[SEMANTIC MESSAGE] LLM notice: {e}")

    # 2. Rich Knowledge Synthesizer (Instant 0ms local fallback)
    t_low = topic.lower()
    if "spotify" in t_low:
        return f"Hey {recip_name}! Have you checked out Spotify lately? It's awesome for streaming music and listening to podcasts on your PC and phone."
    elif "cricket" in t_low or "match" in t_low:
        return f"Hey {recip_name}! Are you going to watch the cricket match tonight? Let me know if you want to catch it together."
    elif "meeting" in t_low or "schedule" in t_low:
        return f"Hey {recip_name}, just checking in regarding our meeting. Let me know what time works best for you today."
    elif "dinner" in t_low or "lunch" in t_low or "food" in t_low:
        return f"Hey {recip_name}! Are you free to grab some food today? Let me know if you want to meet up."
    elif "python" in t_low or "code" in t_low or "coding" in t_low:
        return f"Hey {recip_name}, was working on some Python automation and thought of you. How is your project going?"
    elif "youtube" in t_low or "video" in t_low:
        return f"Hey {recip_name}! Found a really cool video on YouTube you might like. Let me know when you have time to check it out."
    else:
        return f"Hey {recip_name}! Wanted to check in and tell you about {topic}. Let me know when you're free to catch up."

