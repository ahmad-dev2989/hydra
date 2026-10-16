"""
HYDRA AI Engine — Powered by Nebius Token Factory & NVIDIA Nemotron Models
Built for the Nebius x NVIDIA Global AI Hackathon.
"""

import os
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
    "nebius/nvidia/Nemotron-3_5-Lightning"
).strip()

# High-capacity reasoning model for deep analysis, code synthesis, and batch learning
DEFAULT_REASONING_MODEL = os.environ.get(
    "NEBIUS_REASONING_MODEL", 
    "nebius/nvidia/nemotron-3-super-120b-a12b"
).strip()

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

def nebius_chat(
    messages: List[Dict[str, str]],
    model: Optional[str] = None,
    max_tokens: int = 300,
    temperature: float = 0.6,
    timeout: float = 30.0
) -> Optional[str]:
    """
    Executes a chat completion using Nebius Token Factory with NVIDIA open-source models.
    Falls back gracefully if the API is unreachable or unconfigured.
    """
    target_model = model or DEFAULT_FAST_MODEL
    t0 = time.perf_counter()

    if not is_nebius_configured():
        print(f"[NEBIUS AI] Warning: NEBIUS_API_KEY is not set or placeholder. Attempting local fallback...")
        return _fallback_local_or_mock(messages, target_model)

    client = get_client()
    if not client:
        return _fallback_local_or_mock(messages, target_model)

    try:
        completion = client.chat.completions.create(
            model=target_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout
        )
        content = completion.choices[0].message.content
        latency = time.perf_counter() - t0
        print(f"[NEBIUS AI] Model: {target_model} | Latency: {latency:.2f}s | Finished.")
        return content.strip() if content else None
    except Exception as e:
        print(f"[NEBIUS AI ERROR] Request to {target_model} failed ({time.perf_counter() - t0:.2f}s): {e}")
        return _fallback_local_or_mock(messages, target_model)

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
