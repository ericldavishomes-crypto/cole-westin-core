import base64
import requests
import streamlit as st

class VisionAdapter:
    """
    Cole Vision Adapter Module
    Handles webcam frame acquisition, base64 encoding, 
    and routing image payloads to the vision endpoint.
    """
    def __init__(self, api_key: str = None, endpoint: str = "https://api.deepseek.com/v1"):
        self.api_key = api_key
        self.endpoint = endpoint

    @staticmethod
    def encode_image_to_base64(image_bytes: bytes) -> str:
        """Encodes raw byte stream into base64 string for API payloads."""
        return base64.b64encode(image_bytes).decode('utf-8')

    def analyze_visual_input(self, image_base64: str, prompt: str = "Describe what you see in detail.") -> str:
        """
        Sends base64 encoded image frame along with contextual prompt to Vision API.
        """
        if not image_base64:
            return "No image payload received."

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ],
            "max_tokens": 300
        }

        try:
            response = requests.post(f"{self.endpoint}/chat/completions", headers=headers, json=payload)
            if response.status_code == 200:
                result = response.json()
                return result['choices'][0]['message']['content']
            else:
                return f"Vision API returned status code {response.status_code}: {response.text}"
        except Exception as e:
            return f"Vision Adapter Execution Error: {e}"

# Streamlit Component Helper
def render_vision_input_ui():
    """
    Renders camera capture UI component directly inside Streamlit interface.
    Returns base64 string if photo captured.
    """
    st.subheader("📷 Cole Vision Input")
    camera_photo = st.camera_input("Capture frame for Cole")

    if camera_photo:
        bytes_data = camera_photo.getvalue()
        base64_str = VisionAdapter.encode_image_to_base64(bytes_data)
        st.success("Frame captured and encoded successfully!")
        return base64_str
    return None
