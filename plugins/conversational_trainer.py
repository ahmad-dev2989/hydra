"""
HYDRA Conversational Training & Reasoning Engine
Trained on:
1. friend_personality_dataset.jsonl (553+ pairs of natural_friend, empathy, humor, banter)
2. model1.blended_skill_talk.jsonl (BST multi-turn empathetic, knowledgeable dialogue)
3. model2.blended_skill_talk.jsonl (BST multi-turn engaging personas & shared dialogue)

Features:
- Dataset compilation & vector-indexed conversational memory.
- Few-shot semantic exemplar retrieval for conversational turns.
- Grounded personality prompt distillation (natural_friend + blended_skill_talk).
- AI Task vs. Conversation Reasoner (prevents keyword hijacking when user asks questions like 'what is spotify').
"""

import os
import sys
import re
import json
import math
import time
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from openai import OpenAI

# Environment resolution
try:
    from dotenv import load_dotenv
    for d in [Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent, Path(__file__).resolve().parent.parent.parent]:
        env_f = d / ".env"
        if env_f.exists():
            load_dotenv(dotenv_path=env_f)
            break
except ImportError:
    pass

NEBIUS_API_KEY = os.environ.get("NEBIUS_API_KEY", "").strip()
NEBIUS_BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").strip()
NEBIUS_FAST_MODEL = os.environ.get("NEBIUS_FAST_MODEL", "nvidia/Nemotron-3_5-Lightning").strip()
if NEBIUS_FAST_MODEL.startswith("nebius/"):
    NEBIUS_FAST_MODEL = NEBIUS_FAST_MODEL[7:]


def _clean_text(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


class ConversationalKnowledgeTrainer:
    """Compiles and indexes the 3 conversational datasets for HYDRA."""

    def __init__(self):
        self.pairs: List[Dict[str, Any]] = []
        self.personas: List[str] = []
        self.vocab: Dict[str, int] = {}
        self.idf: Dict[str, float] = {}
        self.indexed_vectors: List[Dict[str, float]] = []
        self._is_trained = False
        self._db_path = self._resolve_db_path()

    def _resolve_db_path(self) -> Path:
        curr = Path(__file__).resolve().parent
        for cand in [curr / "trained_conversation_index.json", curr.parent / "trained_conversation_index.json"]:
            if cand.exists():
                return cand
        return curr / "trained_conversation_index.json"

    def _find_dataset(self, filename: str) -> Optional[Path]:
        curr = Path(__file__).resolve().parent
        candidates = [
            curr / filename,
            curr.parent / filename,
            curr.parent.parent / filename,
            curr.parent / "PersonalJarvis-main" / filename
        ]
        for c in candidates:
            if c.exists():
                return c
        return None

    def train(self, force_retrain: bool = False) -> bool:
        """Parses the 3 datasets and builds the semantic search index."""
        if self._is_trained and not force_retrain:
            return True

        if not force_retrain and self._db_path.exists():
            try:
                with open(self._db_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.pairs = data.get("pairs", [])
                    self.personas = data.get("personas", [])
                    self.idf = data.get("idf", {})
                    self._build_vocab_from_idf()
                    self._is_trained = True
                    print(f"[CONVERSATION TRAINER] Loaded {len(self.pairs)} pre-trained conversational pairs from {self._db_path.name}")
                    return True
            except Exception as e:
                print(f"[CONVERSATION TRAINER NOTICE] Re-training due to index load issue: {e}")

        print("[CONVERSATION TRAINER] Training HYDRA on conversation datasets...")
        self.pairs = []
        self.personas = []

        # 1. Train on friend_personality_dataset.jsonl
        f_friend = self._find_dataset("friend_personality_dataset.jsonl")
        if f_friend:
            try:
                with open(f_friend, "r", encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        row = json.loads(line)
                        msgs = row.get("messages", [])
                        if len(msgs) >= 2:
                            u_text = msgs[0].get("content", "").strip()
                            a_text = msgs[1].get("content", "").strip()
                            if u_text and a_text:
                                self.pairs.append({
                                    "user": u_text,
                                    "reply": a_text,
                                    "source": "friend_personality",
                                    "category": row.get("category", "natural_friend"),
                                    "style": row.get("style", "natural_friend")
                                })
                print(f"[CONVERSATION TRAINER] Indexed friend_personality_dataset.jsonl ({len(self.pairs)} turns)")
            except Exception as e:
                print(f"[CONVERSATION TRAINER ERROR] friend_personality_dataset: {e}")

        # 2. Train on model1.blended_skill_talk.jsonl & model2.blended_skill_talk.jsonl
        for bst_name in ["model1.blended_skill_talk.jsonl", "model2.blended_skill_talk.jsonl"]:
            f_bst = self._find_dataset(bst_name)
            if f_bst:
                try:
                    count_before = len(self.pairs)
                    with open(f_bst, "r", encoding="utf-8") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            row = json.loads(line)
                            for ctx in row.get("context", []):
                                c_txt = ctx.get("text", "")
                                for l in c_txt.split("\n"):
                                    if l.strip().startswith("your persona:"):
                                        p_val = l.replace("your persona:", "").strip()
                                        if p_val and p_val not in self.personas:
                                            self.personas.append(p_val)

                            dialog = row.get("dialog", [])
                            for turn_group in dialog:
                                if len(turn_group) >= 2:
                                    t0 = turn_group[0].get("text", "").strip()
                                    t1 = turn_group[1].get("text", "").strip()
                                    if t0 and t1:
                                        self.pairs.append({
                                            "user": t0,
                                            "reply": t1,
                                            "source": bst_name,
                                            "category": "blended_skill_talk",
                                            "style": "empathetic_knowledgeable"
                                        })
                    print(f"[CONVERSATION TRAINER] Indexed {bst_name} (+{len(self.pairs) - count_before} turns)")
                except Exception as e:
                    print(f"[CONVERSATION TRAINER ERROR] {bst_name}: {e}")

        # Compute TF-IDF weights across user turns
        self._compute_tfidf()
        self._is_trained = True

        # Save compiled index artifact
        try:
            with open(self._db_path, "w", encoding="utf-8") as f:
                json.dump({
                    "pairs": self.pairs,
                    "personas": self.personas,
                    "idf": self.idf,
                    "trained_at": time.time(),
                    "total_turns": len(self.pairs)
                }, f, indent=2)
            print(f"[CONVERSATION TRAINER] Successfully saved trained conversational memory to {self._db_path.name}")
        except Exception as e:
            print(f"[CONVERSATION TRAINER WARNING] Could not persist db cache: {e}")

        return True

    def _build_vocab_from_idf(self):
        for idx, term in enumerate(self.idf.keys()):
            self.vocab[term] = idx

    def _compute_tfidf(self):
        doc_count = len(self.pairs)
        if doc_count == 0:
            return

        df: Dict[str, int] = {}
        tokenized_docs = []
        for pair in self.pairs:
            tokens = set(_clean_text(pair["user"]).split())
            tokenized_docs.append(tokens)
            for t in tokens:
                df[t] = df.get(t, 0) + 1

        self.idf = {t: math.log((doc_count + 1) / (count + 1)) + 1.0 for t, count in df.items()}
        self._build_vocab_from_idf()

    def retrieve_similar(self, query: str, top_k: int = 4) -> List[Dict[str, Any]]:
        """Retrieves the top-k most semantically relevant conversation exemplars."""
        if not self._is_trained:
            self.train()

        q_clean = _clean_text(query)
        q_tokens = q_clean.split()
        if not q_tokens or not self.pairs:
            return []

        scored = []
        for pair in self.pairs:
            u_clean = _clean_text(pair["user"])
            if u_clean == q_clean:
                score = 100.0
            else:
                score = 0.0
                u_tokens = set(u_clean.split())
                for t in q_tokens:
                    if t in u_tokens:
                        score += self.idf.get(t, 1.0)
                # Boost if category words match
                if pair.get("category") and pair["category"] in q_clean:
                    score += 2.0

            if score > 0:
                scored.append((score, pair))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored[:top_k]]


class HydraConversationalEngine:
    """
    Main runtime conversation generator.
    Guarantees that all HYDRA conversation happens in the voice, personality,
    and grounding of the 3 trained conversation datasets.
    """

    def __init__(self):
        self.trainer = ConversationalKnowledgeTrainer()
        self.trainer.train()
        self._client: Optional[OpenAI] = None

    def _get_client(self) -> Optional[OpenAI]:
        if self._client is None and NEBIUS_API_KEY:
            try:
                self._client = OpenAI(base_url=NEBIUS_BASE_URL, api_key=NEBIUS_API_KEY)
            except Exception as e:
                print(f"[CONVERSATION ENGINE] OpenAI client init notice: {e}")
        return self._client

    def reason_task_or_conversation(self, user_text: str) -> Dict[str, Any]:
        """
        Uses AI reasoning to determine if input is an actionable TASK or a CONVERSATION.
        Specifically handles ambiguous queries like 'what is spotify', 'tell me about spotify',
        'is spotify good' to ensure they are NEVER hijacked as tasks simply because an app name appears.
        """
        low = user_text.lower().strip()

        # Fast rule pass for informational question patterns
        qa_starters = [
            r"^what\s+is\s+(?!playing\b|the\s+current\s+song\b|the\s+latest\s+email\b|due\b|my\s+assignment\b)",
            r"^what's\s+",
            r"^what\s+are\s+(?!my\s+emails\b|my\s+assignments\b|my\s+classes\b)",
            r"^what\s+does\b",
            r"^why\s+is\b",
            r"^why\s+do\b",
            r"^why\s+does\b",
            r"^how\s+does\b",
            r"^how\s+is\b",
            r"^tell\s+me\s+about\b",
            r"^tell\s+me\s+why\b",
            r"^who\s+created\b",
            r"^who\s+made\b",
            r"^who\s+is\b",
            r"^explain\b",
            r"^define\b",
            r"^can\s+you\s+explain\b",
            r"^do\s+you\s+know\s+(?:what|about)\b",
            r"^is\s+(?:spotify|youtube|classroom|gmail|whatsapp|github|linkedin|instagram|facebook)\s+(?:good|safe|free|popular|better|used)\b"
        ]
        for pat in qa_starters:
            if re.search(pat, low):
                # Ensure no explicit action verbs
                action_exceptions = [
                    r"\bplay\s+", r"\bsend\s+", r"\bcheck\s+assignments\b", r"\bcheck\s+unread\b",
                    r"\bcreate\s+repo\b", r"\bswitch\s+account\b", r"\bopen\s+chat\b", r"\bvideo\s*call\b"
                ]
                if not any(re.search(act, low) for act in action_exceptions):
                    return {
                        "is_task": False,
                        "intent": "conversational",
                        "reasoning": f"Informational inquiry/question pattern matched: '{pat}'"
                    }

        # If it's a short small-talk phrase
        chat_starters = [
            "hello", "hi", "hey", "greetings", "good morning", "good evening", "how are you",
            "who are you", "what are you", "tell me a joke", "i'm bored", "highkey", "lowkey",
            "bet", "not gonna lie", "i tried something", "i'm stressed", "i am stressed"
        ]
        if any(low.startswith(cs) or low == cs for cs in chat_starters):
            return {
                "is_task": False,
                "intent": "conversational",
                "reasoning": "Small-talk / greeting / friend banter phrase matched"
            }

        # If an app name is mentioned, perform fast AI reasoning if API available
        client = self._get_client()
        if client:
            system_reasoner = (
                "You are an AI Intent Classifier for HYDRA, an autonomous computer assistant. "
                "Analyze the user's input and determine whether the user is giving an ACTIONABLE TASK / COMMAND "
                "(instructing the assistant to execute an action on apps, OS, media, or files) "
                "OR engaging in a CONVERSATION / QUESTION (asking what something is, small talk, chatting, opinions, knowledge questions). "
                "CRITICAL: If the user asks 'what is spotify', 'tell me about spotify', or any question ABOUT an app, "
                "this is CONVERSATION, NOT a task! Simply mentioning an app name does NOT make it a task unless there is an actionable command. "
                "Output pure JSON: {\"is_task\": true/false, \"task_name\": \"...\" or null, \"reasoning\": \"...\"}"
            )
            try:
                res = client.chat.completions.create(
                    model=NEBIUS_FAST_MODEL,
                    messages=[
                        {"role": "system", "content": system_reasoner},
                        {"role": "user", "content": f"User Input: \"{user_text}\""}
                    ],
                    temperature=0.0,
                    max_tokens=100
                )
                txt = res.choices[0].message.content.strip()
                # Clean markdown fencing
                txt = re.sub(r"^```(?:json)?", "", txt, flags=re.MULTILINE)
                txt = re.sub(r"```$", "", txt, flags=re.MULTILINE).strip()
                m_json = re.search(r"\{.*\}", txt, re.DOTALL)
                if m_json:
                    parsed = json.loads(m_json.group(0))
                    return {
                        "is_task": bool(parsed.get("is_task", False)),
                        "intent": "task" if parsed.get("is_task") else "conversational",
                        "task_name": parsed.get("task_name"),
                        "reasoning": f"AI Intent Reasoner: {parsed.get('reasoning', '')}"
                    }
            except Exception as e:
                print(f"[AI INTENT REASONER NOTICE] Fallback to heuristic: {e}")

        # Default heuristic: if no explicit action verbs, treat as conversational
        task_verbs = [
            r"\bplay\b", r"\blisten\b", r"\bstream\b", r"\bpause\b", r"\bresume\b", r"\bstop\b",
            r"\bskip\b", r"\bnext\b", r"\bprevious\b", r"\bvolume\b", r"\bmute\b", r"\bunmute\b",
            r"\bsend\b", r"\bwrite\b", r"\bcompose\b", r"\bdispatch\b", r"\bcheck\b", r"\bdownload\b",
            r"\bopen\b", r"\blaunch\b", r"\bcreate\b", r"\bchange\s+theme\b", r"\bcall\b"
        ]
        has_task_verb = any(re.search(v, low) for v in task_verbs)
        return {
            "is_task": has_task_verb,
            "intent": "task" if has_task_verb else "conversational",
            "reasoning": "Heuristic action verb analysis"
        }

    def generate_reply(self, user_query: str, system_context: str = "") -> str:
        """
        Generates a conversational response strictly grounded in the 3 conversation datasets.
        Uses retrieved exemplars and the natural_friend / blended_skill_talk persona.
        """
        clean_q = user_query.strip()
        low_q = _clean_text(clean_q)

        # 1. Check for exact / very high similarity match in dataset
        exemplars = self.trainer.retrieve_similar(clean_q, top_k=4)
        if exemplars and _clean_text(exemplars[0]["user"]) == low_q:
            print(f"[CONVERSATION ENGINE] Direct trained response match from {exemplars[0]['source']}")
            return exemplars[0]["reply"]

        # 2. Build Few-Shot System Prompt grounded in the datasets
        exemplar_text = ""
        for i, ex in enumerate(exemplars[:3]):
            exemplar_text += f"\nExample {i+1}:\nUser: {ex['user']}\nHYDRA: {ex['reply']}\n"

        persona_notes = " • " + "\n • ".join(self.trainer.personas[:5]) if self.trainer.personas else ""

        system_prompt = (
            "You are HYDRA, a smart, witty, authentic, and empathetic AI friend and assistant.\n"
            "You have been trained on genuine conversational datasets (Friend Personality and BlendedSkillTalk).\n\n"
            "YOUR CONVERSATIONAL STYLE & RULES:\n"
            "1. Speak naturally like a real, supportive, and clever friend ('natural_friend' style).\n"
            "2. Never speak like a robotic assistant or use generic corporate filler ('I am an AI', 'As an AI model', 'How can I assist you today?').\n"
            "3. If the user shares feelings or stress, show genuine empathy first before jumping into solutions.\n"
            "4. If the user is joking or playful, use witty banter, sarcasm, or appropriate teasing.\n"
            "5. If the user asks an informational question (e.g. 'what is spotify', 'what is machine learning', 'who created python'), "
            "provide a sharp, accurate, engaging explanation as an expert friend.\n"
            "6. Keep replies concise, conversational, and direct (1-3 sentences) suitable for natural speech output.\n"
            "7. Output ONLY your direct spoken answer. Do not output scratchpads, reasoning numbers, or analysis steps.\n"
            f"{f'Contextual Personas & Hobbies:\n{persona_notes}' if persona_notes else ''}\n\n"
            f"FEW-SHOT EXAMPLES FROM YOUR TRAINING DATASET:{exemplar_text}\n"
        )

        user_content = clean_q
        if system_context:
            user_content = f"{system_context}\n\nUser: {clean_q}"

        client = self._get_client()
        if client:
            try:
                completion = client.chat.completions.create(
                    model=NEBIUS_FAST_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=0.7,
                    max_tokens=1500
                )
                raw_ans = completion.choices[0].message.content.strip()
                cleaned_ans = self._clean_llm_response(raw_ans)
                if cleaned_ans:
                    return cleaned_ans
            except Exception as e:
                print(f"[CONVERSATION ENGINE ERROR]: {e}")

        # Fallback to closest exemplar reply if API fails
        if exemplars:
            return exemplars[0]["reply"]

        return "I'm right here with you. What's on your mind?"

    def _clean_llm_response(self, text: str) -> str:
        """Strips Nemotron thinking process, scratchpad markers, and unwanted metadata."""
        if not text:
            return ""
        s = text.strip()
        s = re.sub(r'<(?:think|thought)>.*?</(?:think|thought)>', '', s, flags=re.DOTALL).strip()
        low = s.lower()
        if "thinking process" in low or "analyze user input" in low or low.startswith("here's a thinking"):
            match_resp = re.search(
                r'(?:^|\n)(?:\*\*|##\s*|###\s*)?(?:final response|final answer|direct answer|spoken response|spoken reply)[:\s*]*(.*?)$',
                s, re.IGNORECASE | re.DOTALL
            )
            if match_resp and len(match_resp.group(1).strip()) > 3:
                candidate = match_resp.group(1).strip()
                if not re.match(r'^(?:1\.|[-*]|\*\*analyze)', candidate, re.IGNORECASE):
                    s = candidate
            else:
                paragraphs = [p.strip() for p in s.split("\n\n") if p.strip()]
                non_thinking = []
                for p in paragraphs:
                    p_clean = p.strip()
                    if re.match(r'^(?:here\'s a thinking|thinking process|1\.|2\.|3\.|4\.|5\.|[-*]|\*\*analyze|\*\*identify|\*\*drafting|\*\*determine|\*\*formulate|\*\*intent)', p_clean, re.IGNORECASE):
                        continue
                    if any(marker in p_clean.lower() for marker in ["user asks:", "user says:", "user wants", "analyze user"]):
                        continue
                    non_thinking.append(p_clean)
                if non_thinking:
                    s = non_thinking[-1]
                else:
                    s = ""

        s = re.sub(r"^\s*HYDRA:\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"^[\"']|[\"']$", "", s).strip()
        return s.strip()


# Singleton global instance
HYDRA_CONVERSATIONAL_ENGINE = HydraConversationalEngine()
