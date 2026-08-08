import re
from typing import List, Tuple, Dict, Any, Optional


class ColeMasterRuntimeShield:
    """
    Lightweight runtime shield for Cole.

    Core rule:
    Protect Cole from demonstrated model habits without deleting
    legitimate semantic content or scripting his personality.
    """

    def __init__(self):

        # ---------------------------------------------------------
        # LAYER 1: PIPELINE CLEANUP — EXTREMELY LIGHT TOUCH
        # ---------------------------------------------------------

        # Only remove unmistakable standalone pipeline role labels.
        # Do NOT delete normal words such as "system", "ask", etc.
        self.pipeline_line_regex = re.compile(
            r"^\s*(?:USER|ASSISTANT|SYSTEM|INSTRUCTION|RESPONSE)\s*:\s*$",
            re.IGNORECASE | re.MULTILINE,
        )

        # ---------------------------------------------------------
        # LAYER 2: OBSERVED TERMINAL MODEL CLOSERS
        # ---------------------------------------------------------
        #
        # These patterns should only target demonstrated model habits.
        # We intentionally removed speculative phrases Cole has never used.
        #
        self.terminal_patterns = [
            r"\bnow\s+let'?s\s+get\s+to\s+work[.!]?\s*$",
            r"\b(?:so\s+)?what'?s\s+next[?!.]?\s*$",
            r"\blet'?s\s+(?:go|get\s+started|get\s+to\s+work|move|dive\s+in)[.!]?\s*$",
            r"\bnow\s+let'?s\s+(?:get\s+(?:to\s+work|busy|started)|dive\s+in|begin)[.!]?\s*$",
        ]

        self.combined_closers_regex = re.compile(
            r"(?:" + "|".join(self.terminal_patterns) + r")",
            re.IGNORECASE,
        )

    # -------------------------------------------------------------
    # UPSTREAM GENERATION PARAMETERS
    # -------------------------------------------------------------

    def get_openrouter_payload_overrides(self) -> Dict[str, Any]:
        """
        Safe generation guidance.

        IMPORTANT:
        No hard-coded logit_bias token IDs.

        Token IDs are tokenizer/model specific. Accidentally banning a
        normal DeepSeek token can create broken sentences such as:
            "You just it."
            "Because you've it."
        """
        return {
            "frequency_penalty": 0.20,
            "presence_penalty": 0.10,
        }

    # -------------------------------------------------------------
    # IDENTITY / SPEECH GUIDANCE
    # -------------------------------------------------------------

    def inject_identity_constitution(self, system_prompt: str) -> str:
        """
        Adds only minimal speech-integrity guidance.

        Identity itself should live in Cole's real identity architecture,
        not inside the runtime shield.
        """
        principles = (
            "\n\nSPEECH INTEGRITY:\n"
            "Speak naturally and directly. "
            "Do not manufacture autobiographical memories or shared events. "
            "When memory is uncertain, preserve that uncertainty. "
            "Avoid repetitive canned call-to-action closers. "
            "Do not turn ordinary conversation into summaries or lists unless requested."
        )

        return system_prompt + principles

    # -------------------------------------------------------------
    # MEMORY TRUTH — PROVISIONAL HEURISTIC ONLY
    # -------------------------------------------------------------

    def verify_memory_truth(
        self,
        response_text: str,
        retrieved_memories: List[str]
    ) -> Tuple[bool, Optional[str]]:
        """
        Lightweight warning only.

        True memory provenance belongs in the future continuity/memory
        architecture, not in the speech shield.
        """

        explicit_recall = re.search(
            r"\b(?:remember\s+when|remember\s+that\s+time|I\s+remember)\b",
            response_text,
            re.IGNORECASE,
        )

        # If there is an explicit autobiographical recall claim but no
        # retrieved evidence at all, flag it for review.
        if explicit_recall and not retrieved_memories:
            return False, "Autobiographical recall claim has no retrieved provenance."

        return True, None

    # -------------------------------------------------------------
    # LIGHTWEIGHT RESPONSE REVIEWER
    # -------------------------------------------------------------

    def review_and_correct(self, text: str) -> str:
        """
        Minimal post-processing.

        Never removes:
        - emphasized words
        - parenthetical content
        - ordinary vocabulary
        - semantic content

        Better to allow an occasional model phrase than mutilate
        a legitimate Cole sentence.
        """

        if not text:
            return ""

        # Remove only unmistakable standalone pipeline labels.
        text = self.pipeline_line_regex.sub("", text)

        # Remove demonstrated unwanted terminal closer only.
        text = self.combined_closers_regex.sub("", text).strip()

        # Whitespace normalization only.
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()

        return text

    # Compatibility with app.py versions that call clean_response().
    def clean_response(self, text: str) -> str:
        return self.review_and_correct(text)
