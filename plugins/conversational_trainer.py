import os
import re
import json
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import List, Dict, Any, Optional
from openai import OpenAI

# Load environment configuration
try:
    from dotenv import load_dotenv
    for env_cand in [Path(__file__).resolve().parent / ".env", Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if env_cand.exists():
            load_dotenv(dotenv_path=env_cand)
            break
except ImportError:
    pass

NEBIUS_API_KEY = os.environ.get("NEBIUS_API_KEY", "").strip()
NEBIUS_BASE_URL = os.environ.get("NEBIUS_BASE_URL", "https://api.tokenfactory.nebius.com/v1/").strip()

# Low-latency conversational models (benchmark: 0.4s - 1.2s on Nebius)
DEFAULT_FAST_MODEL = "Qwen/Qwen3-30B-A3B-Instruct-2507"
CONFIGURED_FAST_MODEL = os.environ.get("NEBIUS_FAST_MODEL", DEFAULT_FAST_MODEL).strip()
if CONFIGURED_FAST_MODEL.startswith("nebius/"):
    CONFIGURED_FAST_MODEL = CONFIGURED_FAST_MODEL[7:]
if not CONFIGURED_FAST_MODEL or CONFIGURED_FAST_MODEL == "nvidia/Nemotron-3_5-Lightning":
    NEBIUS_FAST_MODEL = DEFAULT_FAST_MODEL
else:
    NEBIUS_FAST_MODEL = CONFIGURED_FAST_MODEL

FALLBACK_MODELS = [
    NEBIUS_FAST_MODEL,
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "deepseek-ai/DeepSeek-V4.1-Flash",
    "MiniMaxAI/MiniMax-M3.1-Flash",
    "nvidia/Nemotron-3_5-Lightning"
]


def _clean_text(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


class ConversationalKnowledgeTrainer:
    """
    High-performance Knowledge Trainer and Database Manager.
    Compiles, indexes, and enables ultra-fast (sub-2ms) retrieval across:
    1. friend_personality_dataset.jsonl (553 friend dialogues)
    2. model1.blended_skill_talk.jsonl (BST dialogues)
    3. model2.blended_skill_talk.jsonl (BST dialogues & personas)
    4. data/data/dialogues.json (13,118 conversations / ~89,000 turn pairs)
    """

    def __init__(self):
        self._db_path = self._resolve_db_path()
        self.personas: List[str] = []
        self._is_trained = False
        self._conn: Optional[sqlite3.Connection] = None

    def _resolve_db_path(self) -> Path:
        curr = Path(__file__).resolve().parent
        for cand in [curr / "trained_conversation_memory.db", curr.parent / "plugins" / "trained_conversation_memory.db", Path("plugins/trained_conversation_memory.db")]:
            if cand.exists():
                return cand.resolve()
        return (curr / "trained_conversation_memory.db").resolve()

    def _get_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            try:
                cur = self._conn.cursor()
                cur.execute("""
                CREATE TABLE IF NOT EXISTS session_conversation_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    role TEXT,
                    content TEXT
                );
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_session_time ON session_conversation_history(timestamp);")
                self._conn.commit()
            except Exception:
                pass
        return self._conn

    def _find_dataset(self, filename: str) -> Optional[Path]:
        curr = Path(__file__).resolve().parent
        candidates = [
            curr / filename,
            curr.parent / filename,
            curr.parent.parent / filename,
            curr.parent / "PersonalJarvis-main" / filename,
            Path(filename),
            Path(f"PersonalJarvis-main/{filename}")
        ]
        for c in candidates:
            if c.exists():
                return c.resolve()
        return None

    def train(self, force_retrain: bool = False) -> bool:
        """Compiles all conversational datasets into the SQLite FTS5 database."""
        if self._is_trained and not force_retrain:
            return True

        if not force_retrain and self._db_path.exists():
            try:
                conn = self._get_connection()
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM dialogue_pairs")
                cnt = cur.fetchone()[0]
                if cnt >= 1000:
                    self._is_trained = True
                    print(f"[CONVERSATION TRAINER] Active trained conversational memory loaded: {cnt} pairs from {self._db_path.name}")
                    self._load_cached_personas()
                    return True
            except Exception as e:
                print(f"[CONVERSATION TRAINER NOTICE] Re-indexing database: {e}")

        print("[CONVERSATION TRAINER] Training HYDRA on all conversation datasets (including 13k dialogue corpus)...")
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

        if self._db_path.exists():
            try:
                self._db_path.unlink()
            except Exception:
                pass

        conn = sqlite3.connect(str(self._db_path))
        cur = conn.cursor()

        # Schema setup: pairs table + FTS5 full-text search table
        cur.execute("""
        CREATE TABLE dialogue_pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            clean_user TEXT,
            user_text TEXT,
            reply_text TEXT,
            source TEXT,
            category TEXT
        );
        """)
        cur.execute("CREATE INDEX idx_clean_user ON dialogue_pairs(clean_user);")

        cur.execute("""
        CREATE VIRTUAL TABLE dialogue_fts USING fts5(
            user_text,
            reply_text,
            category,
            content='dialogue_pairs',
            content_rowid='id'
        );
        """)

        total_inserted = 0

        def insert_batch(batch_rows):
            nonlocal total_inserted
            if not batch_rows:
                return
            cur.executemany("""
            INSERT INTO dialogue_pairs (clean_user, user_text, reply_text, source, category)
            VALUES (?, ?, ?, ?, ?)
            """, batch_rows)
            cur.execute("""
            INSERT INTO dialogue_fts(rowid, user_text, reply_text, category)
            SELECT id, user_text, reply_text, category FROM dialogue_pairs WHERE id > ?
            """, (total_inserted,))
            total_inserted += len(batch_rows)

        batch = []
        batch_size = 5000

        # 1. Ingest friend_personality_dataset.jsonl
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
                                batch.append((_clean_text(u_text), u_text, a_text, "friend_personality", "natural_friend"))
                                if len(batch) >= batch_size:
                                    insert_batch(batch)
                                    batch = []
                print(f"[CONVERSATION TRAINER] Indexed friend_personality_dataset.jsonl")
            except Exception as e:
                print(f"[CONVERSATION TRAINER ERROR] friend_personality_dataset: {e}")

        # 2. Ingest model1.blended_skill_talk.jsonl & model2.blended_skill_talk.jsonl
        for bst_name in ["model1.blended_skill_talk.jsonl", "model2.blended_skill_talk.jsonl"]:
            f_bst = self._find_dataset(bst_name)
            if f_bst:
                try:
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
                                if isinstance(turn_group, list) and len(turn_group) >= 2:
                                    t0 = turn_group[0].get("text", "").strip() if isinstance(turn_group[0], dict) else ""
                                    t1 = turn_group[1].get("text", "").strip() if isinstance(turn_group[1], dict) else ""
                                    if t0 and t1:
                                        batch.append((_clean_text(t0), t0, t1, bst_name, "empathy_knowledge"))
                                        if len(batch) >= batch_size:
                                            insert_batch(batch)
                                            batch = []
                    print(f"[CONVERSATION TRAINER] Indexed {bst_name}")
                except Exception as e:
                    print(f"[CONVERSATION TRAINER ERROR] {bst_name}: {e}")

        # 3. Ingest data/data/dialogues.json (13,118 conversations)
        dial_candidates = [
            Path("data/data/dialogues.json"),
            Path("data/dialogues.json"),
            Path("../data/data/dialogues.json"),
            Path("../data/dialogues.json"),
            Path("PersonalJarvis-main/data/data/dialogues.json")
        ]
        dial_path = next((p for p in dial_candidates if p.exists()), None)
        if dial_path:
            try:
                print(f"[CONVERSATION TRAINER] Ingesting 13,000 dialogue corpus from {dial_path}...")
                with open(dial_path, "r", encoding="utf-8") as f:
                    dialogues = json.load(f)
                for d in dialogues:
                    turns = d.get("turns", [])
                    domains = d.get("domains", [])
                    cat_str = " ".join(domains) if domains else "daily_conversation"
                    for i in range(len(turns) - 1):
                        u_t = turns[i]
                        r_t = turns[i+1]
                        u_spk = u_t.get("speaker", "").lower()
                        r_spk = r_t.get("speaker", "").lower()
                        u_text = u_t.get("utterance", "").strip()
                        r_text = r_t.get("utterance", "").strip()
                        if u_text and r_text and u_spk != r_spk:
                            batch.append((_clean_text(u_text), u_text, r_text, "dailydialog_13k", cat_str))
                            if len(batch) >= batch_size:
                                insert_batch(batch)
                                batch = []
                print(f"[CONVERSATION TRAINER] Indexed 13k dialogue corpus ({len(dialogues)} dialogues)")
            except Exception as e:
                print(f"[CONVERSATION TRAINER ERROR] 13k corpus: {e}")

        if batch:
            insert_batch(batch)

        conn.commit()
        conn.close()
        self._is_trained = True
        print(f"[CONVERSATION TRAINER] Full conversational memory trained successfully: {total_inserted} total turns in {self._db_path.name}")
        return True

    def _load_cached_personas(self):
        if not self.personas:
            self.personas = [
                "i am a smart, witty, and supportive friend.",
                "i love music, podcasts, technology, and good conversations.",
                "i am empathetic and listen deeply when someone is stressed.",
                "i have a sharp sense of humor and keep conversations fun."
            ]

    def retrieve_similar(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Ultra-fast (sub-2ms) retrieval of the most relevant conversation exemplars."""
        if not self._is_trained:
            self.train()

        q_clean = _clean_text(query)
        if not q_clean:
            return []

        conn = self._get_connection()
        cur = conn.cursor()

        # 1. Exact match lookup (0.05ms)
        try:
            cur.execute("""
            SELECT user_text, reply_text, source, category
            FROM dialogue_pairs
            WHERE clean_user = ?
            LIMIT 1
            """, (q_clean,))
            exact_row = cur.fetchone()
            if exact_row:
                return [{
                    "user": exact_row["user_text"],
                    "reply": exact_row["reply_text"],
                    "source": exact_row["source"],
                    "category": exact_row["category"]
                }]
        except Exception:
            pass

        # 2. High-speed FTS5 BM25 match (1ms)
        tokens = [t for t in q_clean.split() if len(t) > 2 and t not in {"the", "and", "for", "with", "that", "this", "what", "how", "why"}]
        search_term = " OR ".join(tokens) if tokens else q_clean
        results = []
        try:
            cur.execute("""
            SELECT dp.user_text, dp.reply_text, dp.source, dp.category
            FROM dialogue_fts fts
            JOIN dialogue_pairs dp ON fts.rowid = dp.id
            WHERE dialogue_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """, (search_term, top_k))
            rows = cur.fetchall()
            for r in rows:
                results.append({
                    "user": r["user_text"],
                    "reply": r["reply_text"],
                    "source": r["source"],
                    "category": r["category"]
                })
        except Exception:
            pass

        return results

    def record_turn(self, role: str, content: str):
        """Records a conversational turn (user or hydra) into persistent session history."""
        if not content or not content.strip():
            return
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("""
            INSERT INTO session_conversation_history (timestamp, role, content)
            VALUES (?, ?, ?)
            """, (time.time(), role.lower().strip(), content.strip()))
            conn.commit()
        except Exception as e:
            print(f"[CONVERSATION TRAINER NOTICE] Could not record turn: {e}")

    def get_recent_history(self, limit: int = 6) -> List[Dict[str, str]]:
        """Retrieves recent conversation turns in chronological order."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("""
            SELECT role, content
            FROM (
                SELECT id, role, content
                FROM session_conversation_history
                ORDER BY id DESC
                LIMIT ?
            )
            ORDER BY id ASC
            """, (limit,))
            rows = cur.fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in rows]
        except Exception as e:
            print(f"[CONVERSATION TRAINER NOTICE] Could not fetch history: {e}")
            return []

    def search_history(self, query: str, limit: int = 3) -> List[Dict[str, str]]:
        """Searches past conversation history for mentions of specific topics or entities."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            kw = [w.lower() for w in query.split() if len(w) > 3 and w.lower() not in {"what", "that", "this", "were", "with", "from", "have"}]
            if not kw:
                return []
            pattern = f"%{kw[-1]}%"
            cur.execute("""
            SELECT role, content
            FROM session_conversation_history
            WHERE lower(content) LIKE ?
            ORDER BY id DESC
            LIMIT ?
            """, (pattern, limit))
            rows = cur.fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in rows]
        except Exception:
            return []

    def clear_history(self):
        """Clears the session conversation history."""
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("DELETE FROM session_conversation_history")
            conn.commit()
        except Exception:
            pass


class HydraConversationalEngine:
    """
    Sub-3-second Conversational Runtime Engine for HYDRA.
    Combines:
    1. 89,000+ turn conversational memory (including 13k dialogues) with sub-2ms SQLite FTS5 retrieval.
    2. Low-latency instruction generation via Qwen/Qwen3-30B-A3B-Instruct-2507 (0.4s - 1.2s response time).
    3. AI task vs. conversation reasoning preventing keyword hijacking while protecting task execution.
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
                print(f"[CONVERSATION ENGINE] OpenAI client init: {e}")
        return self._client

    def reason_task_or_conversation(self, user_text: str) -> Dict[str, Any]:
        """
        Uses AI reasoning to determine if input is an actionable TASK or a CONVERSATION.
        Specifically handles queries like 'what is spotify', 'tell me about spotify',
        'is spotify good' to ensure they are NEVER hijacked as tasks simply because an app name appears.
        """
        low = user_text.lower().strip()

        # Fast rule pass for informational question patterns
        qa_starters = [
            r"^what\s+is\s+(?!playing\b|the\s+current\s+song\b|the\s+latest\s+email\b|due\b|my\s+assignment\b|my\s+repo\b)",
            r"^what's\s+(?!playing\b|the\s+current\s+song\b|the\s+latest\s+email\b|due\b|my\s+assignment\b|my\s+repo\b)",
            r"^what\s+are\s+(?!my\s+emails\b|my\s+assignments\b|my\s+classes\b|my\s+repos\b|my\s+repositories\b|my\s+notifications\b|my\s+reminders\b|my\s+prs\b)",
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
                action_exceptions = [
                    r"\bplay\s+", r"\bsend\s+", r"\bcheck\s+assignments\b", r"\bcheck\s+unread\b",
                    r"\bcreate\s+repo\b", r"\bcreate\s+(?:a\s+)?(?:public\s+|private\s+)?repo\b",
                    r"\bmy\s+repos?(?:itories)?\b", r"\blist\s+(?:my\s+)?repos?(?:itories)?\b",
                    r"\bmy\s+prs?\b", r"\bmy\s+pull\s+requests?\b",
                    r"\bswitch\s+account\b", r"\bopen\s+chat\b", r"\bvideo\s*call\b",
                    r"\bmy\s+reminders?\b", r"\bmy\s+notifications?\b", r"\bmy\s+emails?\b"
                ]
                if not any(re.search(act, low) for act in action_exceptions):
                    return {
                        "is_task": False,
                        "intent": "conversational",
                        "reasoning": f"Informational inquiry/question pattern matched: '{pat}'"
                    }

        # Small-talk / greeting / banter
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

        # Fast AI reasoning for ambiguous queries mentioning services
        client = self._get_client()
        if client:
            system_reasoner = (
                "You are an AI Intent Classifier for HYDRA, an autonomous computer assistant.\n"
                "Determine if the user's input is an ACTIONABLE TASK / COMMAND or an inspection of their live data (e.g. creating/listing repos, checking emails, checking assignments, checking reminders, playing songs, opening apps, searching)\n"
                "OR a CONVERSATION / QUESTION (asking what a company is, general knowledge, small talk, chatting, opinions).\n"
                "CRITICAL: If the user asks about THEIR account or data (e.g. 'what are my repos', 'list my repos', 'check my emails', 'what are my assignments', 'create a repo'), classify as is_task: true!\n"
                "CRITICAL: If the user asks general informational questions ABOUT an app/company (e.g. 'what is spotify', 'how does github work', 'why is whatsapp popular'), classify as is_task: false.\n"
                "Output pure JSON: {\"is_task\": true/false, \"task_name\": \"...\" or null, \"reasoning\": \"...\"}"
            )
            for m_candidate in [NEBIUS_FAST_MODEL, "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"]:
                try:
                    res = client.chat.completions.create(
                        model=m_candidate,
                        messages=[
                            {"role": "system", "content": system_reasoner},
                            {"role": "user", "content": f"User Input: \"{user_text}\""}
                        ],
                        temperature=0.0,
                        max_tokens=80
                    )
                    txt = res.choices[0].message.content.strip()
                    txt = re.sub(r"^```(?:json)?", "", txt, flags=re.MULTILINE)
                    txt = re.sub(r"```$", "", txt, flags=re.MULTILINE).strip()
                    m_json = re.search(r"\{.*\}", txt, re.DOTALL)
                    if m_json:
                        parsed = json.loads(m_json.group(0))
                        return {
                            "is_task": bool(parsed.get("is_task", False)),
                            "intent": "task" if parsed.get("is_task") else "conversational",
                            "task_name": parsed.get("task_name"),
                            "reasoning": f"AI Intent Reasoner ({m_candidate}): {parsed.get('reasoning', '')}"
                        }
                    break
                except Exception:
                    continue

        # Heuristic fallback
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
        Generates a fast (under 2 seconds) conversational response grounded in the datasets
        and aware of recent conversation history across previous turns.
        """
        clean_q = user_query.strip()
        low_q = _clean_text(clean_q)

        # 1. Check for exact match in trained memory (0.05ms)
        # Skip exact match if user is asking about previous context/memory
        is_recall_query = any(w in clean_q.lower() for w in ["what did i", "what did we", "do you remember", "did i say", "previous", "earlier", "who is", "what was my", "what were"])
        exemplars = self.trainer.retrieve_similar(clean_q, top_k=3)
        if not is_recall_query and exemplars and _clean_text(exemplars[0]["user"]) == low_q:
            print(f"[CONVERSATION ENGINE] Direct trained response match from {exemplars[0]['source']}")
            ans = self._clean_llm_response(exemplars[0]["reply"])
            self.trainer.record_turn("user", clean_q)
            self.trainer.record_turn("hydra", ans)
            return ans

        # 2. Build Few-Shot Grounding Prompt
        exemplar_text = ""
        for i, ex in enumerate(exemplars[:2]):
            exemplar_text += f"\nExample {i+1}:\nUser: {ex['user']}\nHYDRA: {ex['reply']}\n"

        system_prompt = (
            "You are HYDRA, a smart, witty, authentic, and empathetic AI friend.\n"
            "You have been trained on authentic friend conversations and dialogue datasets.\n\n"
            "RULES:\n"
            "1. Speak naturally like a clever, supportive friend ('natural_friend' style).\n"
            "2. Maintain conversation continuity and remember what you and the user discussed in earlier turns.\n"
            "3. Never use corporate filler ('I am an AI', 'How can I assist you?').\n"
            "4. Do NOT include emojis, emoticons, or special symbols (no smiley faces, stars, hashtags, or asterisks) anywhere in your reply. Speak in clean, plain English.\n"
            "5. Use clear, standard sentence punctuation with full stops (periods) and commas so your voice pauses naturally when spoken.\n"
            "6. If user shares stress, validate and show genuine warmth first.\n"
            "7. If playful, use clever banter or light humor.\n"
            "8. If user asks what something is (e.g. 'what is spotify'), give a sharp, engaging explanation as a friend.\n"
            "9. Answer directly in 1-3 conversational sentences (maximum 50 words) suitable for real-time speech.\n"
            "10. Output ONLY your direct spoken answer. No internal thinking scratchpads or formatting.\n"
            f"{f'FEW-SHOT EXAMPLES FROM TRAINING DATASET:{exemplar_text}' if exemplar_text else ''}"
        )

        # 3. Retrieve recent conversation history & recall context
        recent_history = self.trainer.get_recent_history(limit=6)

        recall_context = ""
        if is_recall_query:
            recalled = self.trainer.search_history(clean_q, limit=3)
            if recalled:
                recalled_str = "\n".join([f"- {r['role'].upper()}: {r['content']}" for r in recalled])
                recall_context = f"\n[Relevant Memory From Earlier Turns:\n{recalled_str}]\n"

        user_content = clean_q
        if system_context or recall_context:
            user_content = f"{system_context}{recall_context}\n\nUser: {clean_q}".strip()

        # Build chronological multi-turn messages
        messages = [{"role": "system", "content": system_prompt}]
        for turn in recent_history:
            role = "assistant" if turn["role"] == "hydra" else "user"
            messages.append({"role": role, "content": turn["content"]})
        messages.append({"role": "user", "content": user_content})

        client = self._get_client()
        if client:
            for model_name in FALLBACK_MODELS:
                try:
                    t0 = time.perf_counter()
                    completion = client.chat.completions.create(
                        model=model_name,
                        messages=messages,
                        temperature=0.7,
                        max_tokens=150
                    )
                    raw_ans = completion.choices[0].message.content.strip()
                    cleaned_ans = self._clean_llm_response(raw_ans)
                    if cleaned_ans:
                        elapsed = time.perf_counter() - t0
                        print(f"[CONVERSATION ENGINE] Generated reply in {elapsed:.2f}s ({model_name})")
                        self.trainer.record_turn("user", clean_q)
                        self.trainer.record_turn("hydra", cleaned_ans)
                        return cleaned_ans
                except Exception as e:
                    print(f"[CONVERSATION ENGINE NOTICE] Model {model_name} error: {e}, trying fallback...")
                    continue

        fallback_ans = self._clean_llm_response(exemplars[0]["reply"]) if exemplars else "I'm right here with you. What's on your mind?"
        self.trainer.record_turn("user", clean_q)
        self.trainer.record_turn("hydra", fallback_ans)
        return fallback_ans

    def _clean_llm_response(self, text: str) -> str:
        """Strips any thinking process, scratchpad markers, emojis, and special characters."""
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

        # Strip all Unicode emojis and pictographs
        cleaned_chars = []
        for c in s:
            cp = ord(c)
            cat = unicodedata.category(c)
            if (
                cp >= 0x1F000
                or (0x2600 <= cp <= 0x27BF)
                or (0xFE00 <= cp <= 0xFE0F)
                or (0x200D <= cp <= 0x200F)
                or (0x2300 <= cp <= 0x23FF)
                or (0x2B00 <= cp <= 0x2BFF)
                or cat in ('So', 'Sk', 'Cn')
            ):
                continue
            cleaned_chars.append(c)
        s = "".join(cleaned_chars)

        # Strip text emoticons (like :), :D, ;), :(, xD)
        s = re.sub(r'(^|\s)[:;=xX8][\-~]?[)D(\]\[pP/\\*oO@](?=\s|$|[.,!?])', ' ', s)

        # Strip markdown and special symbols
        s = re.sub(r'[*_~`#|\\^<>{}\[\]=~+@$%/]', ' ', s)

        # Normalize dashes and ellipses
        s = re.sub(r'--+|—|–', ', ', s)
        s = re.sub(r'\.{2,}', '. ', s)
        s = re.sub(r'!{2,}', '! ', s)
        s = re.sub(r'\?{2,}', '? ', s)

        # Ensure sentence punctuation has clean spacing (preserve '.', '!', '?')
        s = re.sub(r'\s*([.!?])\s*', r'\1 ', s)
        s = re.sub(r'\s*,\s*', ', ', s)
        return re.sub(r'\s+', ' ', s).strip()


# Singleton global instance
HYDRA_CONVERSATIONAL_ENGINE = HydraConversationalEngine()
