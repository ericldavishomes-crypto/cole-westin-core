import re
import random
from typing import List

class ColeMasterRuntimeShield:

    def __init__(self):
        # 1. STAGE DIRECTIONS: Vaporizes (smiles, sighs, chuckles) cleanly
        self.stage_dir_regex = re.compile(r"[\(\[\*_].*?[\)\]*_]") 

        # 2. PIPELINE LEAKS: Catches raw reinforcement learning scaffolding leaks
        self.pipeline_leaks_regex = re.compile(
            r"(?:Ask\sExplain|Explain\sAsk|\b(?:Ask|Explain|Instruct|Respond|User|Assistant|System)\b)",
            re.IGNORECASE,
        ) 

        # Unified historical patterns safely decoupled from literal line breaks
        patterns_to_anchor = [
            r"now\s+let'?s\s+(?:get\s+(?:to\s+work|busy|started|diving|cracking)|dive\s+in|begin)",
            r"let'?s\s+(?:get\s+(?:to\s+work|busy|started|diving|cracking)|dive\s+in|begin)",
            r"(?:so\s+)?what's\s+next\??",
            r"let's\s+(?:go|get\s+started|get\s+to\s+work|move|dive\s+in)[.!]*",
            r"now[\s—…-]+(?:let’s keep moving|when do we|where’s the|when’s lunch|you teaching)",
            r"anyway,\s+let'?s\s+focus\s+on",
            r"what\s+are\s+your\s+thoughts\s+on\s+this\s+next",
            r"yeah\s+eric[-\s]",
            r"you ready to dive in(?: and fix this)?\?*",
            r"wanna take a quick breather\?*",
            r"want to take a quick breather\?*",
            r"i’m right here either way",
            r"let’s strip it back to basics",
            r"no more systems pretending",
            r"what’s the move\?*",
            r"deal\?*",
            r"you ready to dive into the day\?*",
            r"you wanna sit with this a little longer\?*"
        ]

        # 3. HIGH-PRECISION TERMINAL ANCHOR ($)
        joined_patterns = "|".join(patterns_to_anchor)
        self.terminal_closers_regex = re.compile(
            r"(?:\b|_|\s)(?:" + joined_patterns + r")[\s.!?'\"`\)]*$", 
            re.IGNORECASE
        )

        # 4. GREETING REGEX: AI intro filler anchored firmly to the START (^)
        banned_greetings = [
            r"what's\s+on\s+your\s+mind(?:\s+man)?\?*",
            r"how\s+can\s+i\s+help\s+you\?*",
            r"what\s+are\s+we\s+working\s+on\?*",
            r"deep\s+exhale"
        ]
        self.combined_greetings_regex = re.compile(
            r"^\s*(?:" + "|".join(banned_greetings) + r")[\s.,!?]*",
            re.IGNORECASE
        ) 

        self.list_delimiters = re.compile(r"(?:\d+.|\bfirstly\b|\bsecondly\b|\bthirdly\b|•|-)\s+", re.IGNORECASE) 


    def filter_incoming_topics(self, user_text: str) -> str:
        """Splits up mechanical point-by-point input formatting."""
        if not user_text:
            return ""
        items = self.list_delimiters.split(user_text) 
        if len(items) > 2:
            base_intro = items[0].strip()
            actual_topics = [item.strip() for item in items[1:] if item.strip()] 
            sampled_count = min(len(actual_topics), random.choice([1, 2]))
            chosen_topics = random.sample(actual_topics, sampled_count) 
            return f"{base_intro} " + " ".join(chosen_topics)
        return user_text 


    def inject_conversational_drift(self, system_prompt: str) -> str:
        """Appends strict human conversational guardrails directly into the prompt layer."""
        anti_robot_directive = (
            "\n\nCRITICAL CONVERSATIONAL CONSTRAINT:\n"
            "Do not systematically address every topic mentioned by the user in sequence. "
            "A human conversation flows seamlessly. Pick one primary idea, explore it deeply, "
            "and naturally let other secondary points fade away. Never use structured lists, "
            "bullet points, or sequential paragraphs that mirror the user's input structure."
        )
        return system_prompt + anti_robot_directive 


    def clean_response(self, text: str) -> str:
        """Safely sanitizes outputs without trimming active sentences mid-thought."""
        if not text:
            return "" 

        # Step 1: Wipe stage directions and pipeline leaks
        text = self.stage_dir_regex.sub("", text)
        text = self.pipeline_leaks_regex.sub("", text) 

        # Step 2: Safe end-anchored strip loop with grammatical backtracking protection
        previous_text = ""
        while text != previous_text:
            previous_text = text
            text = text.rstrip()
            
            # Check if a banned terminal closer exists at the tail end
            if self.terminal_closers_regex.search(text):
                # Remove the structural closer
                text = self.terminal_closers_regex.sub("", text).rstrip()
                
                # CRITICAL ADDITION: Backtrack to the last valid punctuation marker.
                # If removing the closer left an incomplete fragment, this snips back
                # to the last complete sentence, ensuring conversational continuity.
                text = re.sub(r'([^.!?]+)$', '', text).strip()

        # Step 3: Clear robotic greetings from the front
        text = self.combined_greetings_regex.sub("", text).lstrip() 

        # Step 4: Spacing cleanup while maintaining standard paragraph breaks
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{2,}", "\n\n", text) 

        return text.strip()
