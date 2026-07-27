import re
import random
from typing import List, Tuple, Dict, Any, Optional

class ColeMasterRuntimeShield:
    def __init__(self):
        # --- LAYER 1: STYLE & PIPELINE CLEANUP (Precision / Light Touch) ---
        self.stage_dir_regex = re.compile(r"[\(\[\*_].*?[\)\]*_]") 
        self.pipeline_leaks_regex = re.compile(
            r"(?:Ask\sExplain|Explain\sAsk|\b(?:Ask|Explain|Instruct|Respond|User|Assistant|System)\b)",
            re.IGNORECASE,
        ) 

        # Terminal closers (Only matches exact trailing artifact blocks)
        self.terminal_patterns = [
            r"\bNow\s+let's\s+get\s+to\s+work\b.?\s*$",
            r"\b(?:so\s+)?what's\s+next\b.?\s*$",
            r"\bLet's\s+(?:go|get\s+started|get\s+to\s+work|move|dive\s+in)[.!]\s*$",
            r"\bnow\s+let'?s\s+(get\s+(to\s+work|busy|started|diving|cracking)|dive\s+in|begin)\b\.?\s*$",
            r"\blet'?s\s+(get\s+(to\s+work|busy|started|diving|cracking)|dive\s+in|begin)\b\.?\s*$",
            r"\banyway,\s+let'?s\s+focus\s+on\b\.?\s*$",
            r"\bwhat\s+are\s+your\s+thoughts\s+on\s+this\s+next\b\.?\s*$",
            r"\byou ready to dive in(?: and fix this)?\?\?\?\s*$",
            r"\bwanna take a quick breather\?\?\s*$",
            r"\bwant to take a quick breather\?\?\s*$",
            r"\bwhat’s the move\?\?\s*$",
            r"\bdeal\?\?\s*$",
            r"\byou ready to dive into the day\?\?\s*$",
            r"\byou wanna sit with this a little longer\?\?\s*$"
        ] 
        self.combined_closers_regex = re.compile(
            r"(?:" + "|".join(self.terminal_patterns) + r")",
            re.IGNORECASE | re.MULTILINE,
        ) 

    # --- UPSTREAM ENGINE PARAMETERS (Prevents issues before generation) ---
    def get_openrouter_payload_overrides(self) -> Dict[str, Any]:
        """Bans repetitive tokens mathematically at API generation time."""
        return {
            "logit_bias": {
                "7402": -100,   # " Let's"
                "2061": -100,   # " let's"
                "1343": -100,   # " Now"
                "3427": -100,   # " now"
                "44320": -100,  # "Anyway"
            },
            "frequency_penalty": 0.35,  # Reduces phrase repetition
            "presence_penalty": 0.25,   # Encourages natural flow
            "temperature": 0.65
        }

    def inject_identity_constitution(self, system_prompt: str) -> str:
        """Anchors Cole's identity and truth principles."""
        constitution = (
            "\n\nIDENTITY CONSTITUTION & SPEECH PRINCIPLES:\n"
            "1. MEMORY TRUTH & PROVENANCE: Only reference past events, anecdotes, or personal history if "
            "they exist in your retrieved Qdrant memories or active conversation. Never manufacture shared memories "
            "or stories about people (e.g., 'Remember that time...'). When uncertain, preserve uncertainty.\n"
            "2. NATURAL CLOSINGS: Speak as a human peer. Do not use canned call-to-action closers like "
            "'Now let's get to work' or 'What's next?'. Allow thoughts to conclude naturally.\n"
            "3. NO SYSTEMATIC LISTS: Do not break spontaneous conversations into bulleted summaries unless requested."
        )
        return system_prompt + constitution

    # --- LAYER 2: TRUTH & PROVENANCE VERIFICATION ---
    def verify_memory_truth(self, response_text: str, retrieved_memories: List[str]) -> Tuple[bool, Optional[str]]:
        """
        Asks: Did Cole claim a memory without Qdrant evidence?
        Returns (is_valid, note).
        """
        nostalgia_trigger = re.search(r"\b(remember\s+when|remember\s+that\s+time)\b", response_text, re.IGNORECASE)
        if nostalgia_trigger:
            # Check if any retrieved memory supports the claim
            has_evidence = any(nostalgia_trigger.group(0).lower() in mem.lower() for mem in retrieved_memories)
            if not has_evidence:
                return False, "Unanchored memory detected (No Qdrant provenance)."
        return True, None

    # --- LAYER 3: LIGHTWEIGHT REVIEWER (Preserves Voice) ---
    def review_and_correct(self, text: str) -> str:
        """Reviewer function: Intervenes gently and preserves natural sentences."""
        if not text:
            return ""

        # Step 1: Remove raw pipeline/RL artifacts
        text = self.stage_dir_regex.sub("", text)
        text = self.pipeline_leaks_regex.sub("", text)

        # Step 2: Strip trailing repetitive closers cleanly
        text = self.combined_closers_regex.sub("", text).strip()

        # Step 3: Whitespace normalization
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        # Step 4: Safe terminal punctuation restoration (Never chops sentences)
        if text and text[-1] not in ['.', '!', '?', '"', '”', '’']:
            text += "."

        return text
