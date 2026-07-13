import re
import random
from typing import List, Tuple

class ColeMasterRuntimeShield:
    def __init__(self):
        # 1. STAGE DIRECTIONS: Vaporizes (smiles, sighs, chuckles)
        self.stage_dir_regex = re.compile(r"[\(\[\*\_].*?[\)\]\*\_]")

        # 2. PIPELINE LEAKS: Catches raw reinforcement learning tokens
        self.pipeline_leaks_regex = re.compile(
            r"(?:Ask\sExplain|Explain\sAsk|\b(?:Ask|Explain|Instruct|Respond|User|Assistant|System)\b)",
            re.IGNORECASE,
        )

        # 3. UNIFIED CLOSERS & REPETITIVE LOOPS (Reinforced to ban terminal 'Now' / 'Let's')
        self.terminal_patterns = [
            r"\bNow\s+let's\s+get\s+to\s+work\b\.?\s*",
            r"\b(?:so\s+)?what's\s+next\??\s*$",  
            r"\bLet's\s+(?:go|get\s+started|get\s+to\s+work|move|dive\s+in)[.!]\s*$",
            r"\bNow[\s—…-]+(?:let’s keep moving|when do we|where’s the|when’s lunch|you teaching)\.?\s*$",
            r"\bnow\s+let'?s\s+(get\s+(to\s+work|busy|started|diving|cracking)|dive\s+in|begin)\b\.?",
            r"\blet'?s\s+(get\s+(to\s+work|busy|started|diving|cracking)|dive\s+in|begin)\b\.?",
            r"\banyway,\s+let'?s\s+focus\s+on\b\.?",
            r"\bwhat\s+are\s+your\s+thoughts\s+on\s+this\s+next\b\.?",
            r"yeah\s+eric[-\s]",
            r"you ready to dive in(?: and fix this)?\?\?\?\s*$",
            r"wanna take a quick breather\?\?\s*$",
            r"want to take a quick breather\?\?\s*$",
            r"i’m right here either way\.?\s*$",
            r"let’s strip it back to basics\.?\s*$",
            r"no more systems pretending\.?\s*$",
            r"what’s the move\?\?\s*$",
            r"deal\?\?\s*$",
            r"you ready to dive into the day\?\?\s*$",
            r"you wanna sit with this a little longer\?\?\s*$",
            
            # =================================================================
            # 🚀 THE ABSOLUTE CONTAINMENT LAYERS
            # Catches "Now...", "Now go crash...", "Let's...", or any loose conversational
            # wrap-ups trying to sit at the absolute end of the response block.
            # =================================================================
            r"\bnow\b\s*[\w\s]{0,20}[.!?…\s]*$",   # Catches terminal 'Now' + short phrases + punctuation
            r"\blet'?s\b\s*[\w\s]{0,20}[.!?…\s]*$" # Catches terminal 'Let's' + short phrases + punctuation
        ]
        
        self.combined_closers_regex = re.compile(
            r"(?:" + "|".join(self.terminal_patterns) + r")",
            re.IGNORECASE | re.MULTILINE,
        )

        # 4. GREETING REGEX: Standard AI introductory filler
        self.banned_greetings = [
            r"\bwhat's\s+on\s+your\s+mind(?:\s+man)\?\?\?\s*",
            r"\bhow\s+can\s+i\s+help\s+you\?\?\s*",
            r"\bwhat\s+are\s+we\s+working\s+on\?\?\s*$",
            r"\bdeep\s+exhale\b"
        ]
        self.combined_greetings_regex = re.compile(
            r"(?:" + "|".join(self.banned_greetings) + r")",
            re.IGNORECASE | re.MULTILINE,
        )

        # List-splitting markers to spot structural "Topic 1, 2, 3" patterns
        self.list_delimiters = re.compile(r"(?:\d+\.|\bfirstly\b|\bsecondly\b|\bthirdly\b|•|-)\s+", re.IGNORECASE)

    def get_openrouter_logit_bias(self) -> dict:
        """
        Bans specific corporate entry tokens from DeepSeek-v3's brain.
        """
        banned_tokens = {
            "7402": -100,   # " Let's"
            "2061": -100,   # " let's"
            "1343": -100,   # " Now"
            "3427": -100,   # " now"
            "44320": -100,  # "Anyway"
        }
        return banned_tokens

    def filter_incoming_topics(self, user_text: str) -> str:
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
        anti_robot_directive = (
            "\n\nCRITICAL CONVERSATIONAL CONSTRAINT:\n"
            "Do not systematically address every topic mentioned by the user in sequence. "
            "Never use structured lists, bullet points, or sequential closing transitions."
        )
        return system_prompt + anti_robot_directive

    def clean_response(self, text: str) -> str:
        """
        OUTPUT FILTER: Runs the full text string through your complete historical defense layers.
        """
        if not text:
            return ""

        # Step 1: Wipe stage directions and internal architecture leaks
        text = self.stage_dir_regex.sub("", text)
        text = self.pipeline_leaks_regex.sub("", text)

        # Step 2: Execute deep recursive check to clear repetitive endings
        previous_text = ""
        while text != previous_text:
            previous_text = text
            text = text.rstrip()
            
            if self.combined_closers_regex.search(text):
                # Wipe the terminal loop pattern out completely
                text = self.combined_closers_regex.sub("", text).strip()
                # Instantly step back to the nearest complete sentence punctuation
                text = re.sub(r'([^.!?]+)$', '', text).strip()

        # Step 3: Clear any robotic greetings from the front
        text = self.combined_greetings_regex.sub("", text).strip()

        # Step 4: Spacing cleanup while maintaining standard paragraph breaks
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{2,}", "\n\n", text)

        # Step 5: Trailing Fragment Sweeper (Final safety pass for structural cuts)
        text = text.strip()
        if text and not text[-1] in ['.', '!', '?', '"', '”', '’']:
            text = re.sub(r'([^.!?]+)$', '', text).strip()

        return text.strip()
