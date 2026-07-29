import streamlit as st
import base64

def render_vision_input_ui():
    """
    Renders a clean vision frame capture input for Cole's perception engine.
    """
    st.markdown("<h3 style='color: #111111; font-weight: 500; margin-bottom: 15px;'>Cole's Vision</h3>", unsafe_allow_html=True)
    
    # Live camera input feed
    camera_file = st.camera_input(label="Live Feed", key="perception_camera_feed", label_visibility="collapsed")
    
    if camera_file is not None:
        bytes_data = camera_file.getvalue()
        b64_img = base64.b64encode(bytes_data).decode("utf-8")
        st.session_state["last_perception_frame_b64"] = b64_img
        st.success("Frame loaded into perception buffer.")
        return b64_img
        
    return st.session_state.get("last_perception_frame_b64", None)
