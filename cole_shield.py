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

        # 3. UNIFIED CLOSERS & REPETITIVE LOOPS (All original historical patterns strictly preserved)
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
            r"you wanna sit with this a little longer\?\?\s*$"
        ]
        
        # Compiled exactly with your original multi-line strategy flags
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
        Safety pipeline bridge preventing app.py from crashing if called 
        before logit mapping files are loaded.
        """
        return {}

    def filter_incoming_topics(self, user_text: str) -> str:
        """
        INPUT FILTER: Breaks up sequential list patterns in your input.
        If you raise 3+ distinct points or use lists, it purposefully drops
        some or signals Cole to ignore the rigid sequential ordering.
        """
        if not user_text:
            return ""

        items = self.list_delimiters.split(user_text)
       
        # If the input contains a structured list format
        if len(items) > 2:
            base_intro = items[0].strip()
            actual_topics = [item.strip() for item in items[1:] if item.strip()]
           
            # Human element simulation: pick 1-2 random topics, drop the others
            sampled_count = min(len(actual_topics), random.choice([1, 2]))
            chosen_topics = random.sample(actual_topics, sampled_count)
           
            # Reconstruct an organic sentence layout for the prompt
            reconstructed = f"{base_intro} " + " ".join(chosen_topics)
            return reconstructed.strip()
           
        return user_text

    def inject_conversational_drift(self, system_prompt: str) -> str:
        """
        SYSTEM PROMPT AUGMENTATION: Injects a negative constraint directly into
        the brain's context layer to discourage point-by-point summaries.
        """
        anti_robot_directive = (
            "\n\nCRITICAL CONVERSATIONAL CONSTRAINT:\n"
            "Do not systematically address every topic mentioned by the user in sequence. "
            "A human conversation flows seamlessly. Pick one primary idea, explore it deeply, "
            "and naturally let other secondary points fade away. Never use structured lists, "
            "bullet points, or sequential paragraphs that mirror the user's input structure."
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
            
            # Check if an immersion closer matches at the terminal boundary
            if self.combined_closers_regex.search(text):
                # Execute your original regex subtraction
                text = self.combined_closers_regex.sub("", text).strip()
                
                # INTEGRATION LAYER: Backtrack to protect Cole's sentence structures.
                # If your original pattern wiped out a word at the very end of an 
                # active sentence, this ensures the sentence doesn't sit broken.
                text = re.sub(r'([^.!?]+)$', '', text).strip()

        # Step 3: Clear any robotic greetings
        text = self.combined_greetings_regex.sub("", text).strip()

        # Step 4: Format Spacer Reset (Keeps UI paragraphs perfectly clean)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{2,}", "\n\n", text) # Upgraded to \n\n to preserve paragraph gaps in Streamlit Markdown

        return text.strip()

