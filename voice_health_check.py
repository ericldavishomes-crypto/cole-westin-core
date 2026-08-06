import os
import time
import requests

api_key = os.environ.get("ELEVENLABS_API_KEY", "")
voice_id = os.environ.get("ELEVENLABS_VOICE_ID", "LpYFItSk5m1WFCX8t9Dl")

if not api_key:
    raise RuntimeError("ELEVENLABS_API_KEY is missing")

url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"

headers = {
    "xi-api-key": api_key,
    "Content-Type": "application/json",
}

payload = {
    "text": "Testing Cole voice.",
    "model_id": "eleven_turbo_v2_5",
    "voice_settings": {
        "stability": 0.65,
        "similarity_boost": 0.85,
        "style": 0.0,
        "use_speaker_boost": True,
    },
}

started = time.time()

try:
    response = requests.post(
        url,
        json=payload,
        headers=headers,
        params={"output_format": "mp3_44100_192"},
        timeout=30,
    )

    print("STATUS:", response.status_code)
    print("ELAPSED:", round(time.time() - started, 2), "seconds")
    print("CONTENT-TYPE:", response.headers.get("content-type"))

    if response.status_code == 200:
        with open("/tmp/cole_voice_test.mp3", "wb") as audio_file:
            audio_file.write(response.content)
        print("SUCCESS: audio saved to /tmp/cole_voice_test.mp3")
        print("BYTES:", len(response.content))
    else:
        print("RESPONSE BODY:")
        print(response.text[:4000])

except Exception as exc:
    print("REQUEST ERROR:", type(exc).__name__, str(exc))
    raise
