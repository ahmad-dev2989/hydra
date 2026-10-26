"""
HYDRA Intent & Execution Modality Classifier (Phase 1)
Classifies user commands into distinct execution modalities:
1. BUILTIN_UI: HUD interface toggles (close info, dark/light HUD).
2. CONVERSATIONAL: Knowledge queries, explanations, and Q&A (routed directly to Brain LLM).
3. ACCOUNT_MANAGEMENT: Vault account switching and listing (strictly scoped, no substring false positives).
4. FOREGROUND_DESKTOP: Explicit app launch ("open", "launch") and visual UI manipulation (theme changes, chat navigation).
5. HEADLESS_BACKGROUND: Silent background API execution without launching UI windows (playback, assignments, unread emails, background dispatch).
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Any, Optional, List


class IntentType(str, Enum):
    BUILTIN_UI = "builtin_ui"
    CONVERSATIONAL = "conversational"
    ACCOUNT_MANAGEMENT = "account_management"
    FOREGROUND_DESKTOP = "foreground_desktop"
    HEADLESS_BACKGROUND = "headless_background"


@dataclass
class IntentResult:
    intent_type: IntentType
    target_app: Optional[str] = None
    action: Optional[str] = None
    parameters: Dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""


# Known app identifiers
APP_ALIASES = {
    "spotify": ["spotify", "music", "song", "track", "playlist"],
    "google_classroom": ["google classroom", "classroom", "gclassroom", "classrom", "clasroom"],
    "gmail": ["gmail", "google mail", "g-mail", "email", "emails", "inbox", "mail"],
    "whatsapp": ["whatsapp", "whatsap", "watsapp", "watsp"],
    "github": ["github", "git hub"],
    "youtube": ["youtube", "yt"],
    "canva": ["canva", "canwa"],
    "discord": ["discord"],
    "linkedin": ["linkedin"],
    "chrome": ["chrome", "google chrome"],
    "brave": ["brave", "brave browser"],
    "vscode": ["vscode", "vs code", "visual studio code"],
    "notepad": ["notepad"],
    "figma": ["figma"],
    "instagram": ["instagram", "insta"],
    "calculator": ["calc", "calculator"]
}

TYPO_MAP = {
    "theem": "theme",
    "thems": "themes",
    "step playing": "stop playing",
    "step music": "stop music",
    "step spotify": "stop spotify",
    "step song": "stop song",
    "antigravty": "antigravity",
    "antigraviti": "antigravity",
    "anti gravity": "antigravity",
    "spotfy": "spotify",
    "spotifi": "spotify",
    "sopitfy": "spotify",
    "gmails": "gmail",
    "g-mail": "gmail",
    "google mail": "gmail",
    "clasroom": "classroom",
    "classrom": "classroom",
    "google classroom": "classroom",
    "gclassroom": "classroom",
    "chrme": "chrome",
    "notpad": "notepad",
    "vscod": "vscode",
    "whatsap": "whatsapp",
    "watsapp": "whatsapp",
    "watsp": "whatsapp",
    "canwa": "canva",
    "canv": "canva",
}


def normalize_command_text(text: str) -> str:
    """Normalizes known typos while preserving user casing, emails, and identifiers."""
    s = text
    for typo, repl in TYPO_MAP.items():
        s = re.sub(rf"\b{re.escape(typo)}\b", repl, s, flags=re.IGNORECASE)
    return s


# Explicit Foreground Launch Verbs
FOREGROUND_LAUNCH_VERBS = [
    r"\bopen\b", r"\blaunch\b", r"\bstart\b", r"\bbring up\b",
    r"\bshow\b", r"\bfocus\b", r"\bswitch to\s+(?:\w+\s+)?window\b"
]

# UI Manipulation Patterns (Requires on-screen visual interaction)
UI_MANIPULATION_PATTERNS = [
    r"\bchange\s+(?:the\s+)?theme\b",
    r"\bset\s+(?:the\s+)?theme\b",
    r"\bdark\s+theme\b",
    r"\blight\s+theme\b",
    r"\bdark\s+mode\b",
    r"\blight\s+mode\b",
    r"\bopen\s+chat\s+with\b",
    r"\bopen\s+conversation\s+with\b",
    r"\bprofile\s+(?:picture|pic|photo|image)\b",
    r"\bchange\s+(?:the\s+)?(?:profile|pfp|status|avatar)\b",
    r"\bupdate\s+(?:the\s+)?(?:profile|pfp|status|avatar)\b",
    r"\bset\s+(?:the\s+)?(?:profile|pfp|status|avatar)\b",
    r"\bupload\s+(?:a\s+)?(?:picture|pic|photo|image|file)\b",
    r"\bgo\s+to\s+settings\b",
    r"\bopen\s+settings\b",
    r"\bchange\s+settings\b",
    r"\bclick\b",
    r"\bpress\b"
]


def detect_target_app(text: str) -> Optional[str]:
    """Detects target app from text using whole-word boundary matching, ignoring email address domains."""
    low = text.lower()
    # Strip email addresses so @gmail.com doesn't hijack target app detection
    low_no_email = re.sub(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', '', low)
    for app_id, aliases in APP_ALIASES.items():
        for alias in aliases:
            # Whole word boundary check to avoid substrings like 'ismail' matching 'mail'
            if re.search(rf"\b{re.escape(alias)}\b", low_no_email):
                return app_id

    # Fallback to checking full text if no app matched outside the email
    for app_id, aliases in APP_ALIASES.items():
        for alias in aliases:
            if re.search(rf"\b{re.escape(alias)}\b", low):
                return app_id
    return None


def is_conversational_inquiry(text: str) -> bool:
    """
    Detects if the query is an informational question, explanation, definition,
    or conversational small-talk, rather than an action command.
    Examples:
        - "why spotify is used"
        - "why is spotify used"
        - "what is google classroom"
        - "how does github work"
        - "tell me why whatsapp is popular"
        - "who created python"
        - "tell me a joke"
    """
    low = text.lower().strip()

    # Informational question openings
    qa_starters = [
        r"^why\b",
        r"^what\s+is\s+(?!playing\b|the\s+current\s+song\b|the\s+latest\s+email\b|due\b|my\s+assignment\b)",
        r"^what\s+are\s+(?!my\s+emails\b|my\s+assignments\b|my\s+classes\b|the\s+assignments\b|the\s+classes\b|the\s+announcements\b|announcements\b|my\s+courses\b|the\s+courses\b)",
        r"^what\s+does\b",
        r"^how\s+does\b",
        r"^how\s+do\b",
        r"^who\s+is\b",
        r"^who\s+created\b",
        r"^who\s+made\b",
        r"^tell\s+me\s+about\b",
        r"^tell\s+me\s+why\b",
        r"^tell\s+me\s+how\b",
        r"^explain\b",
        r"^define\b",
        r"^can\s+you\s+explain\b",
        r"^why\s+do\s+we\b",
        r"^is\s+(?:spotify|classroom|whatsapp|github|gmail)\s+(?:safe|free|good|popular|secure|used)\b"
    ]

    for pat in qa_starters:
        if re.search(pat, low):
            # Double check it is not a direct actionable request
            action_exceptions = [
                r"\bplay\s+", r"\bsend\s+", r"\bcheck\s+upcoming\b",
                r"\bcheck\s+assignments\b", r"\bcheck\s+unread\b",
                r"\bcreate\s+repo\b", r"\bswitch\s+account\b",
                r"\bmessages?\b", r"\bemails?\b", r"\bnotifications?\b",
                r"\bassignments?\b", r"\bclasses\b", r"\bcourses?\b",
                r"\bannouncements?\b", r"\bstream\b", r"\bcoursework\b",
                r"\bsay\b", r"\bsays\b", r"\bsaid\b", r"\bsaying\b",
                r"\bsent\b", r"\bwrote\b", r"\bwritten\b",
                r"\bcall\b", r"\bcalls\b", r"\bcalling\b", r"\bring\b", r"\bdial\b"
            ]
            if not any(re.search(act, low) for act in action_exceptions):
                return True

    # General chat small-talk
    chat_patterns = [
        r"^(?:hello|hi|hey|greetings|good\s+morning|good\s+evening|good\s+afternoon)\b",
        r"^(?:who\s+are\s+you|what\s+is\s+your\s+name|how\s+are\s+you)\b",
        r"^(?:tell\s+me\s+a\s+joke|make\s+me\s+laugh)\b",
        r"^(?:thank\s+you|thanks|great\s+job|awesome)\b"
    ]
    for cp in chat_patterns:
        if re.search(cp, low):
            return True

    return False


def is_account_management_command(text: str) -> bool:
    """
    Strictly identifies account listing and switching commands.
    Must NOT trigger on phrases like 'change theme of whatsapp to dark theme'.
    """
    low = text.lower().strip()

    # Listing accounts
    if re.search(r"\b(?:which|what|list|show)\s+(?:all\s+)?(?:connected\s+)?accounts\b", low):
        return True
    if re.search(r"\bconnected\s+accounts\b", low):
        return True

    # Explicit account switching
    if any(w in low for w in ["switch", "change", "toggle", "use", "swap"]) and "account" in low:
        return True

    switch_patterns = [
        r"\b(?:switch|change)\s+(?:to\s+)?(?:account\s+)?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-.]+|\w+)\s+(?:on|for|in)\s+(\w+)\b",
        r"\buse\s+(?:account\s+)?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-.]+|\w+)\s+(?:for|on)\s+(\w+)\b",
        r"\btoggle\s+(?:active\s+)?account\b"
    ]

    for pat in switch_patterns:
        if re.search(pat, low):
            return True

    return False


def is_foreground_command(text: str) -> bool:
    """
    Detects if command explicitly commands opening an app or manipulating visual UI elements.
    Examples:
        - "open whatsapp"
        - "open chat with ismail on whatsapp"
        - "open spotify and play laapata"
        - "change theme of whatsapp to dark theme"
        - "change theme to dark"
        - "open chrome and search AI"
    """
    low = text.lower().strip()
    # Do NOT treat call requests as generic app launches
    if any(re.search(rf"\b{w}\b", low) for w in ["call", "calls", "calling", "videocall"]):
        return False

    # Standalone Media Transport Controls (pure background, zero UI) must NOT be foreground launches
    is_pure_transport = bool(re.search(r"^(?:pause|resume|stop|next|skip|previous|prev|unpause|mute|unmute)(?:\s+(?:the\s+)?(?:song|track|music|audio|playback|spotify))?$", low))
    if is_pure_transport or low in ["pause", "resume", "stop", "next", "skip", "previous", "prev", "unpause", "mute", "unmute"]:
        return False

    # Spotify specific playback, search, or playlist/library goals require foreground visual AI
    if any(re.search(rf"\b{w}\b", low) for w in ["spotify", "playlist", "liked songs", "liked song"]) or "spotify" in low:
        if any(w in low for w in ["play", "search", "liked", "first song", "top song", "find", "listen", "playlist", "stream", "queue"]):
            return True

    # Check UI manipulation patterns
    for pat in UI_MANIPULATION_PATTERNS:
        if re.search(pat, low):
            return True

    # Check explicit launch verbs followed by an app or target
    for verb_pat in FOREGROUND_LAUNCH_VERBS:
        m = re.search(rf"{verb_pat}\s+([a-zA-Z0-9_\s]+)", low)
        if m:
            target_phrase = m.group(1).strip()
            # If the target phrase contains any known desktop app, it is a foreground request
            for app_id, aliases in APP_ALIASES.items():
                for alias in aliases:
                    if re.search(rf"\b{re.escape(alias)}\b", target_phrase):
                        return True
            # Also catch generic words like "browser", "chat", "settings", "tab", "window", "folder"
            if any(w in target_phrase for w in ["browser", "chat", "settings", "tab", "window", "folder", "app"]):
                return True

    return False


def classify_command(raw_text: str) -> IntentResult:
    """
    Master classification function for HYDRA user inputs.
    Returns IntentResult with determined IntentType, target_app, action, and parameters.
    """
    clean_text = normalize_command_text(raw_text.strip())
    low = clean_text.lower()

    if not clean_text:
        return IntentResult(intent_type=IntentType.CONVERSATIONAL, reasoning="Empty input")

    # 1. Built-in HUD System Directives
    if re.search(r"\b(?:close|hide)\s+(?:the\s+)?(?:info|panel|drawer|sidebar)\b", low) or "go back" in low:
        return IntentResult(
            intent_type=IntentType.BUILTIN_UI,
            action="close_panel",
            reasoning="Matched close panel directive"
        )
    if any(w in low for w in ["dark mode", "night mode", "dark theme"]) and not any(app in low for app in ["whatsapp", "chrome", "spotify", "discord", "vscode"]):
        return IntentResult(
            intent_type=IntentType.BUILTIN_UI,
            action="hud_theme_query",
            reasoning="HUD theme query"
        )
    if any(w in low for w in ["light mode", "day mode", "light theme"]) and not any(app in low for app in ["whatsapp", "chrome", "spotify", "discord", "vscode"]):
        return IntentResult(
            intent_type=IntentType.BUILTIN_UI,
            action="hud_theme_query",
            reasoning="HUD theme query"
        )

    # 2. Conversational / Q&A / Knowledge Inquiry
    if is_conversational_inquiry(clean_text):
        target_app = detect_target_app(clean_text)
        return IntentResult(
            intent_type=IntentType.CONVERSATIONAL,
            target_app=target_app,
            action="general_qa",
            reasoning=f"Conversational inquiry detected (question/explanation about {target_app or 'topic'})"
        )

    # 3. Account Management
    if is_account_management_command(clean_text):
        target_app = detect_target_app(clean_text)
        return IntentResult(
            intent_type=IntentType.ACCOUNT_MANAGEMENT,
            target_app=target_app,
            action="account_switch_or_list",
            reasoning="Strict account management pattern matched"
        )

    # 4. Foreground Desktop App & UI Manipulation
    if is_foreground_command(clean_text):
        target_app = detect_target_app(clean_text)
        action = "open_app"
        params = {}

        if any(re.search(pat, low) for pat in [r"\btheme\b", r"\bdark mode\b", r"\blight mode\b"]):
            action = "change_theme"
            params["theme"] = "dark" if any(w in low for w in ["dark", "night"]) else "light"
        elif re.search(r"\bopen\s+chat\s+(?:with\s+)?([a-zA-Z0-9_+]+)", low):
            action = "open_chat"
            m = re.search(r"\bopen\s+chat\s+(?:with\s+)?([a-zA-Z0-9_+]+)", low)
            if m:
                params["contact"] = m.group(1).strip()
            if not target_app:
                target_app = "whatsapp"
        elif any(re.search(pat, low) for pat in [r"\bprofile\b", r"\bpfp\b", r"\bavatar\b"]):
            action = "change_profile"
        elif re.search(r"\bplay\b", low):
            action = "play_media_foreground"

        return IntentResult(
            intent_type=IntentType.FOREGROUND_DESKTOP,
            target_app=target_app,
            action=action,
            parameters=params,
            reasoning="Explicit foreground launch or UI manipulation detected"
        )

    # 5. Headless Background Task Execution
    # Media Standalone Quick Controls & Transport Commands
    transport_match = re.search(r"^(?:pause|resume|stop|next|skip|previous|prev|unpause|mute|unmute|volume\s+(?:up|down)|volume|louder|quieter)(?:\s+(?:the\s+)?(?:song|track|music|audio|playback|spotify))?$", low)
    if transport_match or low in ["pause", "resume", "stop", "next", "skip", "previous", "prev", "unpause", "mute", "unmute", "volume up", "volume down", "louder", "quieter"]:
        cmd_action = "transport"
        if any(w in low for w in ["next", "skip"]):
            cmd_action = "next_track"
        elif any(w in low for w in ["previous", "prev"]):
            cmd_action = "prev_track"
        elif any(w in low for w in ["pause", "stop"]):
            cmd_action = "pause"
        elif any(w in low for w in ["resume", "unpause"]):
            cmd_action = "resume"
        elif any(w in low for w in ["mute", "unmute", "volume", "louder", "quieter"]):
            cmd_action = "volume"
        return IntentResult(
            intent_type=IntentType.HEADLESS_BACKGROUND,
            target_app="spotify",
            action=cmd_action,
            parameters={"command": low},
            reasoning=f"Standalone audio transport command: '{low}'"
        )

    # Detect service from context
    target_app = detect_target_app(clean_text)

    # 5.1 Google Classroom Actions
    classroom_triggers = [
        "assignment", "assignments", "coursework", "classwork", "homework",
        "how many classes", "how many courses", "enrolled classes", "enrolled courses",
        "my classes", "my courses", "classes i am", "classes am i", "enrolled in",
        "announcement", "announcements", "class stream", "classroom stream", "notice", "notices"
    ]
    if target_app == "google_classroom" or any(w in low for w in classroom_triggers):
        action = "check_assignments"
        if any(w in low for w in [
            "how many classes", "how many courses", "enrolled classes", "enrolled courses",
            "my classes", "my courses", "list classes", "classes i am", "classes am i",
            "what classes", "which classes", "list courses", "enrolled in"
        ]):
            action = "enrolled_classes"
        elif any(w in low for w in ["today", "urgent", "tonight"]):
            action = "due_today"
        elif any(w in low for w in ["announcement", "announcements", "stream", "notice"]):
            action = "announcements"

        return IntentResult(
            intent_type=IntentType.HEADLESS_BACKGROUND,
            target_app="google_classroom",
            action=action,
            reasoning=f"Google Classroom background action '{action}'"
        )

    # 5.2 Spotify Playback & Media Actions
    if target_app == "spotify" or any(re.search(rf"\b{w}\b", low) for w in ["song", "music", "track"]):
        if any(re.search(rf"\b{w}\b", low) for w in ["next", "skip"]):
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="spotify",
                action="next_track",
                reasoning="Spotify skip/next track command"
            )
        elif any(re.search(rf"\b{w}\b", low) for w in ["previous", "prev", "back track"]):
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="spotify",
                action="prev_track",
                reasoning="Spotify previous track command"
            )
        elif any(re.search(rf"\b{w}\b", low) for w in ["pause", "stop", "halt"]):
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="spotify",
                action="pause",
                reasoning="Spotify pause command"
            )
        elif any(re.search(rf"\b{w}\b", low) for w in ["resume", "unpause", "continue"]):
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="spotify",
                action="resume",
                reasoning="Spotify resume command"
            )
        elif any(w in low for w in ["what song", "which song", "current song", "now playing", "playing song"]):
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="spotify",
                action="current_track",
                reasoning="Spotify current track query"
            )
        elif any(w in low for w in ["mute", "unmute", "volume"]):
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="spotify",
                action="volume",
                reasoning="Spotify volume command"
            )
        elif any(re.search(rf"\b{w}\b", low) for w in ["play", "listen to", "put on", "stream"]):
            # Specific track or playlist command: route to FOREGROUND_DESKTOP for Dynamic Vision Agent!
            m = re.search(r"\b(?:play|listen to|put on|stream)\s+(?:the\s+(?:song|track|music)\s+)?(.+?)(?:\s+(?:on|in|via)\s+spotify|$)", low)
            track_name = m.group(1).strip() if m else ""
            track_name = re.sub(r'\s+(?:on|via|from|in)\s+spotify\b.*$', '', track_name, flags=re.IGNORECASE).strip()
            if not track_name or track_name in ["music", "song", "songs", "track", "tracks", "spotify"]:
                return IntentResult(
                    intent_type=IntentType.HEADLESS_BACKGROUND,
                    target_app="spotify",
                    action="resume",
                    reasoning="Spotify general resume playback in background"
                )
            return IntentResult(
                intent_type=IntentType.FOREGROUND_DESKTOP,
                target_app="spotify",
                action="play_media_foreground",
                parameters={"query": track_name},
                reasoning=f"Spotify specific track playback '{track_name}' routed to Dynamic Vision Agent"
            )
        elif any(w in low for w in ["volume up", "volume down", "louder", "quieter", "mute", "unmute"]):
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="spotify",
                action="volume",
                reasoning="Spotify audio volume control"
            )

    # 5.3 Gmail Actions
    if target_app == "gmail" or any(re.search(rf"\b{w}\b", low) for w in ["email", "emails", "inbox"]):
        if any(re.search(rf"\b{w}\b", low) for w in ["send", "write", "compose", "dispatch"]):
            m_to = re.search(r"(?:to|unto)\s+([a-zA-Z0-9_.+-]+(?:@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)?|[a-zA-Z]+)", low)
            recipient = m_to.group(1) if m_to else ""
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="gmail",
                action="send_email",
                parameters={"to": recipient},
                reasoning="Gmail background send email"
            )
        elif any(re.search(rf"\b{w}\b", low) for w in ["search", "find", "look for"]):
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="gmail",
                action="search_emails",
                reasoning="Gmail background search"
            )
        else:
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="gmail",
                action="check_unread",
                reasoning="Gmail background unread check"
            )

    # 5.4 WhatsApp Actions
    is_msg_intent = any(re.search(pat, low) for pat in [
        r"\bsend\s+(?:a\s+)?(?:message|msg|text)\s+to\b",
        r"\btext\s+[a-zA-Z0-9_+]+",
        r"\bmessage\s+[a-zA-Z0-9_+]+"
    ])
    is_call_intent = any(re.search(pat, low) for pat in [
        r"\bcall\b", r"\bcalls\b", r"\bcalling\b",
        r"\bvideo\s*call\b", r"\bvoice\s*call\b",
        r"\bring\b", r"\bdial\b"
    ]) and ("whatsapp" in low or target_app == "whatsapp")

    if target_app == "whatsapp" or is_call_intent or (is_msg_intent and not any(w in low for w in ["email", "emails", "mail", "gmail", "inbox"])):
        target_app = "whatsapp"

        # Check if call command (voice call or video call)
        is_video_call = any(re.search(pat, low) for pat in [
            r"\bvideo\s*call\b",
            r"\bvideo\s+calling\b",
            r"\bmake\s+(?:a\s+)?video\s+call\b",
            r"\bstart\s+(?:a\s+)?video\s+call\b"
        ])
        is_voice_call = not is_video_call and any(re.search(pat, low) for pat in [
            r"\bcall\b",
            r"\bcalls\b",
            r"\bcalling\b",
            r"\bvoice\s*call\b",
            r"\bphone\s*call\b",
            r"\bring\b",
            r"\bdial\b",
            r"\bmake\s+(?:a\s+)?call\b"
        ])
        if is_video_call or is_voice_call:
            call_type = "video" if is_video_call else "voice"
            stop_call_tokens = {
                'call', 'calls', 'calling', 'videocall', 'video', 'voice', 'phone', 'ring', 'dial',
                'make', 'start', 'place', 'do', 'whatsapp', 'whatsap', 'watsapp', 'watsp',
                'app', 'to', 'on', 'in', 'via', 'please', 'sir', 'can', 'you', 'could', 'me', 'with'
            }
            contact = ""
            m_call = re.search(r'\b(?:video\s*call|videocall|voice\s*call|call|calling|dial|ring)\s+(?:with\s+|to\s+)?([a-zA-Z0-9_+\s]+?)(?:\s+(?:on|in|via)\s+whatsapp|\s*$)', clean_text, re.IGNORECASE)
            if m_call:
                c_words = [w for w in m_call.group(1).split() if w.lower() not in stop_call_tokens]
                if c_words:
                    contact = " ".join(c_words)

            if not contact:
                clean_q = re.sub(r'[^\w\s]', ' ', clean_text.lower())
                cands = [w for w in clean_q.split() if w not in stop_call_tokens and not w.isdigit()]
                if cands:
                    contact = " ".join(cands)

            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="whatsapp",
                action="start_video_call" if is_video_call else "start_call",
                parameters={"contact": contact, "call_type": call_type},
                reasoning=f"WhatsApp {call_type} call to '{contact}'"
            )

        # Check if inquiry first (if user is asking to check, read, or know what someone said)
        is_inquiry_start = any(re.search(rf"\b{w}\b", low) for w in ["check", "read", "what", "who", "any", "unread", "latest", "recent", "tell me"])

        # Check if explicit send command
        is_explicit_send = not is_inquiry_start and any(re.search(pat, low) for pat in [
            r"\bsend\s+(?:a\s+)?(?:message|msg|text)\b",
            r"\btext\s+[a-zA-Z0-9_+]+",
            r"\bmessage\s+(?!that\b|which\b|from\b|of\b|on\b|to\b)[a-zA-Z0-9_+]+",
            r"\bwrite\s+(?:a\s+)?(?:message|msg|text)\b",
            r"\btell\s+[a-zA-Z0-9_+]+\s+(?:that|to|saying)\b"
        ])
        if is_explicit_send:
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="whatsapp",
                action="send_message",
                reasoning="WhatsApp send message"
            )

        # Check if inquiry about incoming/unread messages
        is_unread_inquiry = is_inquiry_start or any(re.search(pat, low) for pat in [
            r"\btell\s+me\s+about\s+(?:the\s+)?messages\b",
            r"\bcheck\s+(?:my\s+)?(?:unread\s+|new\s+)?messages\b",
            r"\b(?:what|any)\s+(?:new\s+)?messages\b",
            r"\bwhat\s+(?:did\s+|is\s+|was\s+)?[a-zA-Z0-9_+]+(?:\s+|\s+\w+\s+)?(?:say|said|saying|send|sent|sending|write|wrote|text|texted)\b",
            r"\bread\s+(?:my\s+)?messages\b",
            r"\bunread\b",
            r"\bwho\s+(?:sent|messaged)\b",
            r"\b(?:message|messages|chat)\s+(?:from|by|that|which)\s+[a-zA-Z0-9_+]+\b",
            r"\b(?!send\b|write\b|text\b)[a-zA-Z0-9_+]+(?:\x27s|\x27)?\s+(?:message|messages|text|texts|chat)\b"
        ])
        if is_unread_inquiry:
            # Extract sender using comprehensive patterns & token subtraction
            stop_set = {
                'check', 'read', 'tell', 'see', 'show', 'look', 'find', 'get', 'listen', 'hear',
                'say', 'says', 'said', 'saying', 'send', 'sends', 'sent', 'sending', 'write', 'writes', 'wrote', 'writing',
                'text', 'texts', 'texted', 'texting', 'type', 'types', 'typed', 'typing',
                'message', 'messages', 'messaged', 'messaging', 'what', 'whats', 'who', 'whos', 'which', 'how', 'any', 'if',
                'did', 'does', 'do', 'is', 'was', 'are', 'were', 'has', 'have', 'had', 'me', 'my', 'mine', 'i', 'you', 'your',
                'yours', 'he', 'she', 'him', 'her', 'they', 'them', 'their', 'it', 'its', 'we', 'us', 'our', 'the', 'a', 'an',
                'this', 'that', 'these', 'those', 'on', 'in', 'at', 'from', 'to', 'by', 'for', 'about', 'with', 'of', 'up', 'out',
                'whatsapp', 'whatsap', 'watsapp', 'watsp', 'app', 'application', 'msg', 'msgs', 'note', 'notes', 'voice', 'audio',
                'notification', 'notifications', 'latest', 'recent', 'new', 'last', 'unread', 'all', 'there', 'something', 'anything',
                'nothing', 'some', 'please', 'sir'
            }

            sender = ""
            m_from = re.search(r'\b(?:from|by|of)\s+([a-zA-Z0-9_+\s]+?)(?:\s+(?:on|in|via)\s+whatsapp|\s*$)', clean_text, re.IGNORECASE)
            if m_from:
                w_list = [w for w in m_from.group(1).split() if w.lower() not in stop_set]
                if w_list:
                    sender = " ".join(w_list)

            if not sender:
                m_said = re.search(r'\bwhat\s+(?:did\s+|is\s+|was\s+|does\s+)?([a-zA-Z0-9_+\s]+?)\s+(?:say|says|said|saying|is\s+saying|was\s+saying|send|sends|sent|sending|write|writes|wrote|writing|text|texts|texted)\b', clean_text, re.IGNORECASE)
                if m_said:
                    w_list = [w for w in m_said.group(1).split() if w.lower() not in stop_set]
                    if w_list:
                        sender = " ".join(w_list)

            if not sender:
                m_that = re.search(r'\b(?:message|messages|msg|msgs|text|texts)\s+(?:that\s+|which\s+)?([a-zA-Z0-9_+\s]+?)\s+(?:said|sent|wrote|texted|saying|says|sends)\b', clean_text, re.IGNORECASE)
                if m_that:
                    w_list = [w for w in m_that.group(1).split() if w.lower() not in stop_set]
                    if w_list:
                        sender = " ".join(w_list)

            if not sender:
                # Token subtraction fallback - joins all non-common words (e.g. 'ahmad jind')
                clean_q = re.sub(r'[^\w\s]', ' ', clean_text.lower())
                cands = [w for w in clean_q.split() if w not in stop_set and not w.isdigit()]
                if cands:
                    sender = " ".join(cands)

            params = {"sender": sender} if sender else {}
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="whatsapp",
                action="check_unread",
                parameters=params,
                reasoning=f"WhatsApp background unread check (sender: '{sender}')" if sender else "WhatsApp background unread check"
            )

        if any(re.search(rf"\b{w}\b", low) for w in ["send", "write", "text", "message", "msg"]):
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="whatsapp",
                action="send_message",
                reasoning="WhatsApp send message"
            )
        else:
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="whatsapp",
                action="check_unread",
                reasoning="WhatsApp background unread check"
            )

    # 5.5 GitHub Actions
    if target_app == "github":
        if any(w in low for w in ["create repo", "create a repo", "new repo", "create repository"]):
            m_name = re.search(r"(?:named|name|called)\s+([a-zA-Z0-9_.-]+)", low)
            repo_name = m_name.group(1) if m_name else ""
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="github",
                action="create_repo",
                parameters={"repo_name": repo_name},
                reasoning="GitHub background create repo"
            )
        elif any(w in low for w in ["pull request", "pr", "prs", "review"]):
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="github",
                action="check_prs",
                reasoning="GitHub background PR check"
            )
        else:
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="github",
                action="check_notifications",
                reasoning="GitHub background notifications"
            )

    # 5.6 YouTube Actions
    if target_app == "youtube":
        return IntentResult(
            intent_type=IntentType.HEADLESS_BACKGROUND,
            target_app="youtube",
            action="play_or_search",
            reasoning="YouTube background action"
        )

    # 5.7 Canva Actions
    if target_app == "canva":
        return IntentResult(
            intent_type=IntentType.HEADLESS_BACKGROUND,
            target_app="canva",
            action="check_designs",
            reasoning="Canva background action"
        )

    # 5.8 Discord Actions
    if target_app == "discord":
        return IntentResult(
            intent_type=IntentType.HEADLESS_BACKGROUND,
            target_app="discord",
            action="check_mentions",
            reasoning="Discord background action"
        )

    # 5.9 LinkedIn Actions
    if target_app == "linkedin":
        return IntentResult(
            intent_type=IntentType.HEADLESS_BACKGROUND,
            target_app="linkedin",
            action="check_notifications",
            reasoning="LinkedIn background action"
        )

    # If general action verbs exist without explicit app (e.g. "play starboy")
    if re.search(r"\b(?:play|listen to)\s+(.+)", low):
        m = re.search(r"\b(?:play|listen to)\s+(.+)", low)
        track = m.group(1).strip() if m else ""
        if not track or track in ["music", "song", "songs", "track", "tracks"]:
            return IntentResult(
                intent_type=IntentType.HEADLESS_BACKGROUND,
                target_app="spotify",
                action="resume",
                reasoning="General media resume playback in background"
            )
        return IntentResult(
            intent_type=IntentType.FOREGROUND_DESKTOP,
            target_app="spotify",
            action="play_media_foreground",
            parameters={"query": track},
            reasoning=f"Inferred media playback request for '{track}' routed to Dynamic Vision Agent"
        )

    # Default fallback: Send to conversational Brain LLM
    return IntentResult(
        intent_type=IntentType.CONVERSATIONAL,
        target_app=target_app,
        action="general_chat",
        reasoning="Default conversational fallback"
    )
