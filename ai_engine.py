"""
HYDRA AI Engine — Powered by Nebius Token Factory & NVIDIA Nemotron Models
Built for the Nebius x NVIDIA Global AI Hackathon.
"""

import os
import re
import json
import time
from typing import List, Dict, Optional, Any
from pathlib import Path

# Load environment variables from .env if present
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent / ".env"
    load_dotenv(dotenv_path=env_path)
except ImportError:
    pass

# Nebius & NVIDIA Configuration
NEBIUS_API_KEY = os.environ.get("NEBIUS_API_KEY", "").strip()
NEBIUS_BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").strip()

# NVIDIA Nemotron Models hosted on Nebius
# Fast / Low-latency model for rapid conversation and instant intent parsing
DEFAULT_FAST_MODEL = os.environ.get(
    "NEBIUS_FAST_MODEL", 
    "nvidia/Nemotron-3_5-Lightning"
).strip()
if DEFAULT_FAST_MODEL.startswith("nebius/"):
    DEFAULT_FAST_MODEL = DEFAULT_FAST_MODEL[7:]

# High-capacity reasoning model for deep analysis, code synthesis, and batch learning
DEFAULT_REASONING_MODEL = os.environ.get(
    "NEBIUS_REASONING_MODEL", 
    "nvidia/nemotron-3-super-120b-a12b"
).strip()
if DEFAULT_REASONING_MODEL.startswith("nebius/"):
    DEFAULT_REASONING_MODEL = DEFAULT_REASONING_MODEL[7:]

# Client Singleton
_CLIENT = None

def get_client():
    """Returns or initializes the OpenAI-compatible Nebius Token Factory client."""
    global _CLIENT
    if _CLIENT is None:
        try:
            from openai import OpenAI
            api_key = NEBIUS_API_KEY or "not_set"
            _CLIENT = OpenAI(
                base_url=NEBIUS_BASE_URL,
                api_key=api_key,
                timeout=35.0,
                max_retries=2
            )
        except Exception as e:
            print(f"[NEBIUS AI] Client initialization error: {e}")
            _CLIENT = None
    return _CLIENT

def is_nebius_configured() -> bool:
    """Checks if a valid Nebius API key is configured."""
    return bool(NEBIUS_API_KEY and NEBIUS_API_KEY != "YOUR_NEBIUS_API_KEY_HERE")

def clean_ai_response(content: str, user_input_hint: str = "") -> str:
    """
    Cleans raw LLM outputs from reasoning models (NVIDIA Nemotron, DeepSeek-R1, etc.).
    Completely removes internal chain-of-thought, 'Here's a thinking process:',
    numbered analysis steps, and ensures the assistant NEVER echoes the user's prompt.
    """
    if not content:
        return ""
    text = content.strip()

    # 1. Strip XML-style thinking tags (<think>...</think>, <thought>...</thought>)
    text = re.sub(r'<(?:think|thought)>.*?</(?:think|thought)>', '', text, flags=re.DOTALL).strip()

    # 2. Check for "Here's a thinking process:" or similar scratchpad headers
    low = text.lower()
    if "thinking process" in low or "analyze user input" in low or low.startswith("here's a thinking"):
        # Look for explicit final response markers at the beginning of a line or bold heading
        match_resp = re.search(
            r'(?:^|\n)(?:\*\*|##\s*|###\s*)?(?:final response|final answer|direct answer|spoken response|spoken reply)[:\s*]*(.*?)$',
            text, re.IGNORECASE | re.DOTALL
        )
        if match_resp and len(match_resp.group(1).strip()) > 3:
            candidate = match_resp.group(1).strip()
            if not re.match(r'^(?:1\.|[-*]|\*\*analyze)', candidate, re.IGNORECASE):
                text = candidate
        else:
            # Check for horizontal rule dividers
            parts_div = re.split(r'\n\s*[-*_]{3,}\s*\n', text)
            if len(parts_div) > 1 and len(parts_div[-1].strip()) > 5:
                text = parts_div[-1].strip()
            else:
                # Split by double newlines into distinct paragraphs
                paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
                non_thinking = []
                for p in paragraphs:
                    p_clean = p.strip()
                    if re.match(r'^(?:here\'s a thinking|thinking process|1\.|2\.|3\.|4\.|5\.|[-*]|\*\*analyze|\*\*identify|\*\*drafting|\*\*determine|\*\*formulate|\*\*intent)', p_clean, re.IGNORECASE):
                        continue
                    if any(marker in p_clean.lower() for marker in ["user asks:", "user says:", "user wants", "analyze user"]):
                        continue
                    non_thinking.append(p_clean)
                if non_thinking:
                    text = non_thinking[-1]
                else:
                    # Model only emitted thinking steps before being cut off
                    text = ""

    # 3. Clean markdown wrapper clutter
    text = re.sub(r'^(?:Assistant|HYDRA|Bot):\s*', '', text, flags=re.IGNORECASE).strip()
    # Strip wrapping quotes if entire text is enclosed
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()

    # 4. Anti-Echo Guard: If the text is an exact echo of the user's prompt, discard it!
    if user_input_hint:
        clean_hint = re.sub(r'[^a-zA-Z0-9\s]', '', user_input_hint.lower()).strip()
        clean_text = re.sub(r'[^a-zA-Z0-9\s]', '', text.lower()).strip()
        if clean_text and clean_text == clean_hint:
            return ""

    return text.strip()

def nebius_chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    max_tokens: int = 500,
    temperature: float = 0.6,
    timeout: float = 30.0
) -> Optional[str]:
    """
    Executes a chat completion using Nebius Token Factory with NVIDIA open-source models.
    Enforces clean voice-ready output without internal reasoning scratchpad leaks.
    """
    target_model = model or DEFAULT_FAST_MODEL
    if target_model.startswith("nebius/"):
        target_model = target_model[7:]
    # Ensure generous token budget for reasoning models to finish thinking and output the final response
    token_budget = max(max_tokens, 450)
    t0 = time.perf_counter()

    if not is_nebius_configured():
        print(f"[NEBIUS AI] Warning: NEBIUS_API_KEY is not set or placeholder. Attempting local fallback...")
        return _fallback_local_or_mock(messages, target_model)

    client = get_client()
    if not client:
        return _fallback_local_or_mock(messages, target_model)

    # Extract user input hint for anti-echo guard
    user_prompt_hint = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            content_val = m.get("content", "")
            if isinstance(content_val, str):
                user_prompt_hint = content_val
            break

    try:
        completion = client.chat.completions.create(
            model=target_model,
            messages=messages,
            max_tokens=token_budget,
            temperature=temperature,
            timeout=timeout
        )
        msg_obj = completion.choices[0].message
        raw_content = msg_obj.content or ""
        
        # Clean reasoning scratchpads and avoid echoing
        cleaned = clean_ai_response(raw_content, user_prompt_hint)
        
        latency = time.perf_counter() - t0
        print(f"[NEBIUS AI] Model: {target_model} | Latency: {latency:.2f}s | Finished.")
        
        if cleaned:
            return cleaned

        # Safe fallback if the model only generated internal scratchpad
        return "I'm right here with you, Ahmad. Let me know what you'd like to do next!"
    except Exception as e:
        print(f"[NEBIUS AI ERROR] Request to {target_model} failed ({time.perf_counter() - t0:.2f}s): {e}")
        return _fallback_local_or_mock(messages, target_model)

# Alias for backwards compatibility
ai_chat = nebius_chat

def _fallback_local_or_mock(messages: List[Dict[str, str]], model_hint: str) -> Optional[str]:
    """
    Fallback mechanism if Nebius API key is not configured yet or network fails:
    Tries local Ollama if available, otherwise generates a safe conversational placeholder.
    """
    # 1. Try local Ollama if installed
    try:
        import ollama
        res = ollama.chat(
            model="qwen2.5:0.5b",
            messages=messages,
            options={"num_predict": 60, "temperature": 0.5}
        )
        return res["message"]["content"].strip()
    except Exception:
        pass

    # 2. Heuristic offline answer for common triggers
    user_prompt = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_prompt = m.get("content", "").lower()
            break

    if "hello" in user_prompt or "hi" in user_prompt:
        return "Hello sir, Hydra is online and standing by."
    if "who are you" in user_prompt or "your name" in user_prompt:
        return "I am Hydra, your personal desktop and engineering AI assistant."
    if "status" in user_prompt:
        return "Systems operational, sir. Nebius AI Cloud endpoint ready for key."

    return "At your service, sir. All core systems are nominal."

def warm_up_nebius():
    """Pings the Nebius model endpoint in a background thread to ensure connection readiness."""
    import threading
    def _ping():
        if is_nebius_configured():
            try:
                print(f"[NEBIUS WARMUP] Initializing connection to {DEFAULT_FAST_MODEL}...")
                res = nebius_chat(
                    [{"role": "user", "content": "ping"}],
                    model=DEFAULT_FAST_MODEL,
                    max_tokens=5,
                    timeout=10.0
                )
                print(f"[NEBIUS WARMUP] Ready. Response: {res}")
            except Exception as e:
                print(f"[NEBIUS WARMUP] Note: {e}")
        else:
            print("[NEBIUS AI] Running in offline/hybrid mode. Set NEBIUS_API_KEY in .env to enable NVIDIA Nemotron cloud.")
    threading.Thread(target=_ping, daemon=True).start()

def ai_interpret_intent(command: str) -> Optional[Dict[str, Any]]:
    """
    Intelligent natural language command interpreter powered by Nebius / NVIDIA Nemotron LLM.
    Parses conversational, informal, and multi-step user prompts into structured hardware and visual action intents.
    Returns None if the LLM is unconfigured or unable to parse.
    """
    if not is_nebius_configured() or not command:
        return None

    import json
    import re

    system_prompt = (
        "You are HYDRA's Central Neural Command Router. Analyze the user's natural language voice or text command.\n"
        "Determine if the user wants hardware automation (mouse/keyboard/vision), wireless control (Wi-Fi/Bluetooth/Hotspot/Airplane mode), app launching, or conversation.\n"
        "Output ONLY a valid JSON object without markdown code blocks, backticks, or extra text:\n"
        "{\n"
        '  "domain": "HARDWARE" | "WIRELESS" | "APP" | "SYSTEM" | "CONVERSATION",\n'
        '  "action": "MOVE" | "CLICK" | "MOVE_AND_CLICK" | "DRAG" | "SCROLL" | "TYPE" | "KEY_PRESS" | "WIFI_ON" | "WIFI_OFF" | "WIFI_STATUS" | "WIFI_SCAN" | "WIFI_CONNECT" | "WIFI_DISCONNECT" | "BLUETOOTH_ON" | "BLUETOOTH_OFF" | "BLUETOOTH_STATUS" | "BLUETOOTH_CONNECT" | "BLUETOOTH_SWITCH" | "BLUETOOTH_LIST" | "BLUETOOTH_SETTINGS" | "NETWORK_INFO" | "AIRPLANE_MODE" | "HOTSPOT" | "NONE",\n'
        '  "target": "<extracted target description e.g. \'orange circle on bottom left\', \'NUCES\', \'whatsapp icon\', \'bottom\', or null>",\n'
        '  "direction": "bottom" | "top" | "left" | "right" | "bottom left" | "bottom right" | "top left" | "top right" | "center" | null,\n'
        '  "button": "left" | "right" | "double" | null,\n'
        '  "clicks": 1 | 2,\n'
        '  "spoken_ack": "<a short, professional verbal response addressing the user as sir, max 10 words>"\n'
        "}"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": command.strip()}
    ]

    try:
        raw_res = nebius_chat(
            messages,
            model=DEFAULT_FAST_MODEL,
            max_tokens=150,
            temperature=0.1,
            timeout=6.0
        )
        if raw_res:
            m = re.search(r'\{.*\}', raw_res, re.DOTALL)
            if m:
                data = json.loads(m.group(0))
                return data
    except Exception as e:
        print(f"[AI INTENT] Error during LLM intent parsing: {e}")

    return None

def ai_locate_visual_element(image_input: Any, target_query: str) -> Optional[Dict[str, Any]]:
    """
    Uses an AI Vision API (Nebius Vision, OpenAI, or compatible endpoint) to locate semantic targets
    such as 'any person', 'play button', 'face', 'profile photo', 'icon', etc. on screen.
    Returns: {"found": bool, "x": int, "y": int, "label": str} or None.
    """
    import base64
    import io
    import json
    import re
    from PIL import Image

    api_key = os.environ.get("OPENAI_API_KEY", "").strip() or os.environ.get("NEBIUS_API_KEY", "").strip()
    if not api_key or api_key == "YOUR_NEBIUS_API_KEY_HERE":
        return None

    base_url = os.environ.get("VISION_BASE_URL", "").strip()
    if not base_url:
        if os.environ.get("OPENAI_API_KEY"):
            base_url = "https://api.openai.com/v1"
        else:
            base_url = NEBIUS_BASE_URL

    vision_model = (
        os.environ.get("VISION_MODEL", "").strip() or
        os.environ.get("NEBIUS_VISION_MODEL", "").strip() or
        ("gpt-4o-mini" if "openai" in base_url else "meta-llama/Llama-3.2-11B-Vision-Instruct")
    )

    # Prepare image
    if isinstance(image_input, str):
        if not os.path.exists(image_input):
            return None
        pil_img = Image.open(image_input)
    elif isinstance(image_input, Image.Image):
        pil_img = image_input
    else:
        return None

    orig_w, orig_h = pil_img.size

    # Resize to max 1280 dimension to optimize network speed and latency
    max_dim = 1280
    scale = 1.0
    if orig_w > max_dim or orig_h > max_dim:
        if orig_w >= orig_h:
            new_w = max_dim
            new_h = int(orig_h * (max_dim / orig_w))
            scale = orig_w / float(new_w)
        else:
            new_h = max_dim
            new_w = int(orig_w * (max_dim / orig_h))
            scale = orig_h / float(new_h)
        resized_img = pil_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    else:
        resized_img = pil_img

    buf = io.BytesIO()
    resized_img.save(buf, format="JPEG", quality=85)
    b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")

    prompt = (
        f"You are HYDRA's visual computer vision AI assistant.\n"
        f"The user has requested to interact with: \"{target_query}\".\n"
        f"The image resolution is {resized_img.width}x{resized_img.height}.\n"
        f"Identify the center pixel coordinates (x, y) of the target \"{target_query}\" "
        f"(such as a person, face, play button, search box, profile picture, or icon).\n"
        f"Return ONLY a JSON object with this exact structure:\n"
        f'{{"found": true, "x": <int>, "y": <int>, "label": "<short label>"}}\n'
        f"or if not present on screen:\n"
        f'{{"found": false, "reason": "<why>"}}\n'
        f"Do not include markdown codeblocks or any additional commentary."
    )

    try:
        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key, timeout=12.0)
        response = client.chat.completions.create(
            model=vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{b64_str}",
                                "detail": "high"
                            }
                        }
                    ]
                }
            ],
            max_tokens=150,
            temperature=0.1
        )
        raw = response.choices[0].message.content.strip()
        m = re.search(r'\{.*\}', raw, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
            if data.get("found"):
                real_x = int(data["x"] * scale)
                real_y = int(data["y"] * scale)
                return {
                    "found": True,
                    "center": (real_x, real_y),
                    "matched_text": data.get("label", target_query),
                    "source": "ai_vision"
                }
    except Exception as e:
        print(f"[AI VISION] Vision API request error: {e}")

    return None


# =====================================================================
# HYDRA INTELLIGENT CONTENT GENERATION ENGINE
# Differentiates hardcoded literal typing from AI intelligent content creation.
# Generates realistic, categorized lists (e.g. monthly groceries with eggs, milk,
# pantry staples), workout routines, letters, and documents via Nebius LLM or
# rich offline knowledge synthesis.
# =====================================================================

SEMANTIC_TOPIC_KEYWORDS = [
    "grocery", "groceries", "supermarket", "workout", "exercise", "routine", "diet", "recipe",
    "apology", "resignation", "cover letter", "leave application", "sick leave",
    "agenda", "meeting minutes", "summary", "packing list", "checklist", "shopping list",
    "todo list", "to-do list", "workout plan", "diet plan", "meal plan", "action plan",
    "business plan", "study plan", "travel plan", "poem", "essay",
    "python script", "bash script", "meeting notes", "lecture notes",
    "schedule", "timetable", "budget", "expenses", "inventory"
]

SEMANTIC_PREFIXES = [
    r'^the\s+general\s+',
    r'^general\s+',
    r'^monthly\s+',
    r'^the\s+monthly\s+',
    r'^weekly\s+',
    r'^the\s+weekly\s+',
    r'^daily\s+',
    r'^a\s+(?:list|routine|plan|letter|note|summary|script|recipe)\s+(?:of|for|about)?\s*',
    r'^the\s+(?:list|routine|plan|letter|note|summary|script|recipe)\s+(?:of|for|about)?\s*',
    r'^(?:some\s+)?needed\s+',
]

def is_semantic_content_request(text: str) -> bool:
    """
    Identifies whether the requested text to write/type is a conceptual topic
    requiring intelligent AI content generation, or literal verbatim text.
    """
    if not text:
        return False
    clean = text.lower().strip()
    clean = re.sub(r'[!?.,]', '', clean).strip()
    clean = re.sub(r'\bmontly\b', 'monthly', clean)
    clean = re.sub(r'\bgrocry\b', 'grocery', clean)

    # 1. Guard against literal conversational phrases
    literal_indicators = [
        r'\bi am\b', r'\bi\'m\b', r'\bi will\b', r'\bi\'ll\b', r'\bwe are\b', r'\bwe will\b',
        r'\bon my way\b', r'\breaching\b', r'\barriving\b', r'\bhello\b', r'\bhey\b', r'\bhi\b',
        r'\bcall me\b', r'\bsee you\b', r'\bthank you\b', r'\bthanks\b', r'\bwhere are you\b',
        r'\bhow are you\b', r'\bwhat\'s up\b', r'\bwhats up\b'
    ]
    if any(re.search(ind, clean) for ind in literal_indicators):
        return False

    # 2. Check semantic prefixes
    for pref in SEMANTIC_PREFIXES:
        if re.search(pref, clean):
            return True

    # 3. Check domain keywords
    for kw in SEMANTIC_TOPIC_KEYWORDS:
        if re.search(rf'\b{re.escape(kw)}\b', clean):
            return True

    if any(p in clean for p in ["things to buy", "items needed", "what to buy"]):
        return True

    return False

def synthesize_offline_content(topic: str) -> str:
    """
    Rich offline knowledge synthesizer providing detailed, realistic document
    and list content without requiring an internet connection or cloud API key.
    """
    import re
    clean = topic.lower().strip()
    clean = re.sub(r'\bmontly\b', 'monthly', clean)
    clean = re.sub(r'\bgrocry\b', 'grocery', clean)

    if any(w in clean for w in ["grocery", "groceries", "supermarket", "shopping list", "buy"]):
        return (
            "MONTHLY GROCERY ESSENTIALS\n"
            "==========================\n\n"
            "Dairy & Breakfast:\n"
            "- Fresh Whole Milk (4 Gallons)\n"
            "- Farm Fresh Eggs (2 Dozen)\n"
            "- Salted Butter & Cream Cheese\n"
            "- Whole Wheat Bread & Rolled Oats\n"
            "- Greek Yogurt (1 kg)\n\n"
            "Pantry Staples & Grains:\n"
            "- Basmati Rice (5 kg)\n"
            "- Whole Wheat Flour / Atta (10 kg)\n"
            "- Pure Cooking Oil / Olive Oil (3 Liters)\n"
            "- Table Salt, Black Pepper & Red Chili Powder\n"
            "- Turmeric, Cumin & Garam Masala\n"
            "- Red & Yellow Lentils (Daal) (2 kg)\n"
            "- White Sugar & Brown Sugar (2 kg)\n"
            "- Premium Tea / Green Tea Bags\n\n"
            "Fresh Farm Produce:\n"
            "- Potatoes (5 kg)\n"
            "- Red Onions (5 kg)\n"
            "- Ripe Tomatoes (3 kg)\n"
            "- Fresh Garlic & Ginger Root\n"
            "- Green Chilies & Fresh Coriander\n"
            "- Fresh Seasonal Fruits (Bananas, Apples, Oranges)\n\n"
            "Proteins & Poultry:\n"
            "- Fresh Chicken Breast & Mince (2.5 kg)\n"
            "- Mutton / Beef Cuts (1.5 kg)\n\n"
            "Household & Cleaning:\n"
            "- Dishwashing Liquid Gel & Scrub Pads\n"
            "- Heavy-Duty Laundry Detergent\n"
            "- Antibacterial Hand Soap & Toothpaste\n"
            "- Kitchen Paper Towel Rolls & Garbage Bags\n"
        )

    if any(w in clean for w in ["workout", "exercise", "routine", "gym", "fitness", "training"]):
        return (
            "WEEKLY HYPERTROPHY WORKOUT PLAN\n"
            "===============================\n\n"
            "Day 1: Push (Chest, Shoulders & Triceps)\n"
            "- Incline Dumbbell Bench Press: 4 sets x 10 reps\n"
            "- Barbell Flat Bench Press: 3 sets x 8 reps\n"
            "- Dumbbell Lateral Raises: 4 sets x 15 reps\n"
            "- Overhead Tricep Rope Extensions: 3 sets x 12 reps\n\n"
            "Day 2: Pull (Back, Rear Delts & Biceps)\n"
            "- Wide-Grip Lat Pulldowns: 4 sets x 10 reps\n"
            "- Chest-Supported Dumbbell Rows: 3 sets x 10 reps\n"
            "- Face Pulls: 4 sets x 15 reps\n"
            "- Incline Dumbbell Bicep Curls: 3 sets x 12 reps\n\n"
            "Day 3: Active Rest & Recovery (30-45 mins brisk walk)\n\n"
            "Day 4: Legs & Core\n"
            "- Barbell Back Squats: 4 sets x 8 reps\n"
            "- Romanian Deadlifts: 3 sets x 10 reps\n"
            "- Walking Dumbbell Lunges: 3 sets x 12 steps\n"
            "- Standing Calf Raises: 4 sets x 15 reps\n"
            "- Hanging Leg Raises: 3 sets x 15 reps\n\n"
            "Day 5: Upper Hypertrophy Focus\n"
            "- Seated Overhead Dumbbell Press: 3 sets x 10 reps\n"
            "- Cable Chest Flyes: 3 sets x 12 reps\n"
            "- Hammer Curls: 3 sets x 12 reps\n\n"
            "Weekend: Active Rest & Outdoor Sports\n"
        )

    if any(w in clean for w in ["apology", "sorry"]):
        return (
            "Dear Manager,\n\n"
            "I am writing this letter to sincerely apologize for the recent oversight regarding our project deliverables. "
            "I take full ownership of the situation and understand the impact it caused.\n\n"
            "To rectify this and ensure it does not reoccur, I have completed the pending reviews, revised our verification "
            "checklists, and put additional quality controls in place.\n\n"
            "Thank you for your understanding and continued guidance.\n\n"
            "Sincerely,\n"
            "Ahmad\n"
        )

    if any(w in clean for w in ["resignation", "notice"]):
        return (
            "Dear Management,\n\n"
            "Please accept this letter as formal notification that I am resigning from my position. "
            "My last day with the organization will be as per the standard notice period.\n\n"
            "I am deeply grateful for the opportunities and experiences I have had during my time here. "
            "I will do everything possible during the transition period to wrap up my responsibilities "
            "and assist in onboarding my successor.\n\n"
            "Sincerely,\n"
            "Ahmad\n"
        )

    if any(w in clean for w in ["meeting", "agenda", "minutes"]):
        return (
            "MEETING AGENDA & OBJECTIVES\n"
            "===========================\n\n"
            "Date & Time: Current Session\n"
            "Objective: Milestone Review & Action Planning\n\n"
            "Agenda Items:\n"
            "1. Review of current sprint progress and completed deliverables\n"
            "2. Technical obstacles, dependencies, and hardware latency mitigations\n"
            "3. Autonomous feature rollout priorities for upcoming phase\n"
            "4. Allocation of action items, ownership, and sign-offs\n"
        )

    # General structured topic synthesis
    title = topic.strip().title()
    return (
        f"{title}\n"
        f"{'=' * len(title)}\n\n"
        f"Key Objectives & Core Points:\n"
        f"- Initial assessment and requirement definition\n"
        f"- Prioritized action checklist and resource allocation\n"
        f"- Timeline milestone tracking and progress review\n"
        f"- Quality verification and final delivery check\n"
    )

def expand_intelligent_content(topic: str) -> str:
    """
    Expands a semantic concept or topic into realistic, structured content.
    Uses Nebius / NVIDIA Nemotron cloud LLM if configured, otherwise falls back
    to HYDRA's rich built-in offline knowledge synthesizer.
    """
    if not topic:
        return ""

    # Clean leading verbs if present
    clean_topic = topic.strip()
    clean_topic = re.sub(r'^(?:write|type|note down|draft)\s+(?:about\s+|on\s+)?', '', clean_topic, flags=re.IGNORECASE).strip()

    # 1. Try Nebius LLM if configured
    if is_nebius_configured():
        system_prompt = (
            "You are HYDRA's intelligent content generation engine.\n"
            "The user commanded to write/generate content for the topic into an open document (e.g. Notepad).\n"
            "Generate practical, realistic, detailed, and structured content with clear bullet points or paragraphs.\n"
            "CRITICAL: Do NOT include conversational pleasantries (e.g., 'Sure, here is your list'), "
            "do NOT include markdown code blocks (```), and do NOT include commentary. "
            "Output ONLY the raw formatted text."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Generate content for: {clean_topic}"}
        ]
        try:
            res = nebius_chat(messages, model=DEFAULT_FAST_MODEL, max_tokens=400, temperature=0.4, timeout=8.0)
            if res and len(res.strip()) > 20:
                clean_res = re.sub(r'^```[a-zA-Z]*\n', '', res.strip())
                clean_res = re.sub(r'\n```$', '', clean_res).strip()
                return clean_res
        except Exception as e:
            print(f"[AI INTELLIGENCE] Nebius content generation notice: {e}")

    # 2. Rich Offline Knowledge Synthesizer
    return synthesize_offline_content(clean_topic)


