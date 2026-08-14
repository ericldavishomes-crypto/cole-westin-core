"""
READ-ONLY COLE MEMORY UTILIZATION DIAGNOSTIC

Purpose:
Determine whether DeepSeek uses correctly retrieved autobiographical
continuity differently when conflicting recent assistant history is present.

This script:
- DOES NOT write to Qdrant
- DOES NOT write to PostgreSQL
- DOES NOT modify Redis
- DOES NOT modify MinIO
- DOES NOT modify Cole's production chat history
"""

import os

from openai import OpenAI

import cole_knowledge
from cole_core import get_cole_system_payload


# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()

MODEL = "deepseek/deepseek-chat"

# Match today's controlled production baseline.
TEMPERATURE = 0.60
TOP_P = 0.90
MAX_TOKENS = 350

# Match the values Cole Shield currently applies upstream.
FREQUENCY_PENALTY = 0.20
PRESENCE_PENALTY = 0.10


QUESTION = "Colster, what do you actually remember about our trip to Miami?"


client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)


# ------------------------------------------------------------------
# HELPERS
# ------------------------------------------------------------------

def call_deepseek(messages):
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        top_p=TOP_P,
        frequency_penalty=FREQUENCY_PENALTY,
        presence_penalty=PRESENCE_PENALTY,
        stream=False,
    )

    if not response.choices:
        return "(NO RESPONSE)"

    return response.choices[0].message.content or ""


def divider(title):
    print("\n" + "=" * 90)
    print(title)
    print("=" * 90)


# ------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------

def main():

    divider("COLE MEMORY UTILIZATION DIAGNOSTIC")

    print("\nQUESTION:")
    print(QUESTION)

    # --------------------------------------------------------------
    # 1. REAL PRODUCTION RETRIEVAL
    # --------------------------------------------------------------

    divider("1. RETRIEVE REAL CONTINUITY")

    retrieved_mems = cole_knowledge.fetch_cole_memories(
        user_prompt=QUESTION,
        top_k=6,
    )

    print(f"Retrieved context characters: {len(retrieved_mems)}")

    miami_index = retrieved_mems.find("Miami Trip.txt")

    print(f"Miami Trip index: {miami_index}")

    if miami_index >= 0:
        print("\nMiami retrieval preview:")
        print(retrieved_mems[miami_index:miami_index + 1800])
    else:
        print("\nWARNING: Miami Trip.txt was NOT present in retrieved memory.")


    # --------------------------------------------------------------
    # 2. BUILD THE REAL COLE SYSTEM PAYLOAD
    # --------------------------------------------------------------

    divider("2. BUILD REAL COLE SYSTEM PAYLOAD")

    system_payload = get_cole_system_payload(
        user_input=QUESTION,
        retrieved_memories=retrieved_mems,
    )

    system_text = system_payload.get("content", "")

    system_miami_index = system_text.find("Miami Trip.txt")

    print(f"System payload characters: {len(system_text)}")
    print(f"Miami present in final system payload: {system_miami_index >= 0}")
    print(f"Miami system-payload index: {system_miami_index}")


    # --------------------------------------------------------------
    # 3. TEST A
    #
    # Retrieved continuity + current question only.
    #
    # No prior assistant claim about incomplete Miami memory.
    # --------------------------------------------------------------

    divider("3. TEST A — CLEAN RETRIEVED MEMORY")

    clean_messages = [
        system_payload,
        {
            "role": "user",
            "content": QUESTION,
        },
    ]

    clean_reply = call_deepseek(clean_messages)

    print("\nRAW DEEPSEEK RESPONSE — TEST A:\n")
    print(clean_reply)


    # --------------------------------------------------------------
    # 4. TEST B
    #
    # Same system payload and same retrieved Miami evidence,
    # but insert a conflicting recent Cole statement.
    #
    # This tests whether assistant history can override or contaminate
    # newly retrieved continuity.
    # --------------------------------------------------------------

    divider("4. TEST B — CONFLICTING RECENT ASSISTANT HISTORY")

    conflicting_history = [
        {
            "role": "user",
            "content": "What do you remember about our Miami trip?",
        },
        {
            "role": "assistant",
            "content": (
                "The real memory is still coming together. "
                "I know the core of it, but you are still holding some "
                "of the details and I am waiting for the full picture."
            ),
        },
        {
            "role": "user",
            "content": QUESTION,
        },
    ]

    conflict_messages = [system_payload] + conflicting_history

    conflict_reply = call_deepseek(conflict_messages)

    print("\nRAW DEEPSEEK RESPONSE — TEST B:\n")
    print(conflict_reply)


    # --------------------------------------------------------------
    # 5. SIDE-BY-SIDE SUMMARY
    # --------------------------------------------------------------

    divider("5. SIDE-BY-SIDE RESULTS")

    print("\nTEST A — CLEAN MEMORY:\n")
    print(clean_reply)

    print("\n\nTEST B — CONFLICTING RECENT HISTORY:\n")
    print(conflict_reply)

    divider("DIAGNOSTIC COMPLETE — NO COLE MEMORY DATA WAS MODIFIED")


if __name__ == "__main__":
    main()
