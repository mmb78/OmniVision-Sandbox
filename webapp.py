import streamlit as st
import base64
import json
import time
import asyncio
import uuid
import re
from io import BytesIO
from PIL import Image, ExifTags, ImageOps, ImageGrab, IptcImagePlugin
from openai import AsyncOpenAI
from geopy.geocoders import Nominatim

import config

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def resize_image(image: Image.Image, max_size: int) -> Image.Image:
    img_copy = image.copy()
    img_copy.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
    return img_copy

def image_to_base64(image: Image.Image) -> str:
    buffered = BytesIO()
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

def get_decimal_from_dms(dms, ref):
    degrees, minutes, seconds = dms[0], dms[1], dms[2]
    decimal = float(degrees) + float(minutes)/60 + float(seconds)/3600
    if ref in ['S', 'W']: decimal = -decimal
    return decimal

def extract_exif_data(image: Image.Image):
    date_time, lat, lon, existing_address = None, None, None, None
    dump_lines = []
    
    # Trackers to prevent Lightroom from printing duplicate tags
    extracted_descriptions = set()
    extracted_titles = set()
    
    # Helper to prevent massive binary data, with exceptions for long text fields
    def safe_str(val, tag_name=""):
        if isinstance(val, bytes):
            if len(val) > 100: return f"<binary data: {len(val)} bytes>"
            try: 
                val = val.decode('utf-8', errors='ignore')
            except: 
                return f"<binary data: {len(val)} bytes>"
                
        s = str(val)
        
        # NEVER truncate descriptions, titles, or keyword subjects. Cap everything else.
        if tag_name not in ["ImageDescription", "Description", "Title", "Subject"] and len(s) > 1000: 
            s = s[:1000] + "...[truncated]"
        
        # Keep printable characters PLUS formatting characters like newlines and tabs
        clean_s = ''.join(c for c in s if c.isprintable() or c in '\n\r\t').strip()
        return clean_s if clean_s else "<unprintable>"

    # 1. Standard EXIF (GPS, Time & Raw Dump)
    try:
        exif = image.getexif()
        if exif:
            for tag_id, value in exif.items():
                tag = ExifTags.TAGS.get(tag_id, tag_id)
                if tag == 'DateTime': date_time = value
                
                clean_val = safe_str(value, tag)
                dump_lines.append(f"{tag}: {clean_val}")
                
                # Remember standard tags so we don't duplicate them later
                if tag == 'ImageDescription':
                    extracted_descriptions.add(clean_val)
                elif tag == 'XPTitle':
                    extracted_titles.add(clean_val)
                
            exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
            if exif_ifd:
                if 36867 in exif_ifd:
                    date_time = exif_ifd[36867]
                elif 36868 in exif_ifd and not date_time:
                    date_time = exif_ifd[36868]
                for tag_id, value in exif_ifd.items():
                    tag = ExifTags.TAGS.get(tag_id, tag_id)
                    dump_lines.append(f"Exif.{tag}: {safe_str(value, tag)}")

            gps_info = exif.get_ifd(ExifTags.IFD.GPSInfo)
            if gps_info:
                for tag_id, value in gps_info.items():
                    tag = ExifTags.TAGS.get(tag_id, tag_id)
                    dump_lines.append(f"GPS.{tag}: {safe_str(value, tag)}")
                    
                gps_lat, gps_lat_ref = gps_info.get(2), gps_info.get(1)
                gps_lon, gps_lon_ref = gps_info.get(4), gps_info.get(3)
                if gps_lat and gps_lat_ref and gps_lon and gps_lon_ref:
                    lat = get_decimal_from_dms(gps_lat, gps_lat_ref)
                    lon = get_decimal_from_dms(gps_lon, gps_lon_ref)
    except: pass
    
    parts = []

    # 2. XMP Metadata (Modern Lightroom Exports)
    try:
        # Check both common XMP dictionary keys
        xmp_raw = image.info.get("XML:com.adobe.xmp") or image.info.get("xmp")
        if xmp_raw:
            if isinstance(xmp_raw, bytes): 
                xmp_raw = xmp_raw.decode('utf-8', errors='ignore')
            else:
                xmp_raw = str(xmp_raw)
            
            # --- Extract Title (dc:title) ---
            title_match = re.search(r'<dc:title>.*?<rdf:li[^>]*>(.*?)</rdf:li>', xmp_raw, re.DOTALL)
            if title_match and title_match.group(1):
                clean_title = safe_str(title_match.group(1), 'Title')
                if clean_title not in extracted_titles:
                    dump_lines.append(f"XMP.Title: {clean_title}")
                    extracted_titles.add(clean_title)
                
            # --- Extract Description (dc:description) ---
            desc_match = re.search(r'<dc:description>.*?<rdf:li[^>]*>(.*?)</rdf:li>', xmp_raw, re.DOTALL)
            if desc_match and desc_match.group(1):
                clean_desc = safe_str(desc_match.group(1), 'Description')
                if clean_desc not in extracted_descriptions:
                    dump_lines.append(f"XMP.Description: {clean_desc}")
                    extracted_descriptions.add(clean_desc)
                
            # --- Extract Keywords/Subjects (dc:subject) ---
            subject_match = re.search(r'<dc:subject>(.*?)</dc:subject>', xmp_raw, re.DOTALL)
            if subject_match:
                # Subjects are usually stored as multiple <rdf:li> tags inside a <rdf:Bag>
                keywords = re.findall(r'<rdf:li[^>]*>(.*?)</rdf:li>', subject_match.group(1))
                if keywords:
                    dump_lines.append(f"XMP.Subject: {safe_str(', '.join(keywords), 'Subject')}")
            
            # Extract Address from XMP
            loc = re.search(r'<Iptc4xmpCore:Location>(.*?)</Iptc4xmpCore:Location>', xmp_raw) or re.search(r'Iptc4xmpCore:Location="(.*?)"', xmp_raw)
            city = re.search(r'<photoshop:City>(.*?)</photoshop:City>', xmp_raw) or re.search(r'photoshop:City="(.*?)"', xmp_raw)
            state = re.search(r'<photoshop:State>(.*?)</photoshop:State>', xmp_raw) or re.search(r'photoshop:State="(.*?)"', xmp_raw)
            country = re.search(r'<photoshop:Country>(.*?)</photoshop:Country>', xmp_raw) or re.search(r'photoshop:Country="(.*?)"', xmp_raw)
            
            if loc and loc.group(1): parts.append(loc.group(1))
            elif city and city.group(1): parts.append(city.group(1))
            if state and state.group(1) and state.group(1) not in parts: parts.append(state.group(1))
            if country and country.group(1) and country.group(1) not in parts: parts.append(country.group(1))
    except: pass

    # 3. Built-in IPTC Plugin (Legacy Lightroom / IrfanView)
    try:
        iptc = IptcImagePlugin.getiptcinfo(image)
        if iptc:
            def decode_val(val):
                if isinstance(val, list) and len(val) > 0: val = val[0]
                if isinstance(val, bytes): return val.decode('utf-8', errors='ignore')
                return str(val)

            # --- Extract Title if XMP/EXIF missed it ---
            t_val = iptc.get((2, 5)) # Tag 2:05 is 'ObjectName' (Document Title)
            if not t_val: t_val = iptc.get((2, 105)) # Fallback to Headline
            if t_val:
                clean_title = safe_str(decode_val(t_val), 'Title')
                if clean_title not in extracted_titles:
                    dump_lines.append(f"IPTC.Title: {clean_title}")
                    extracted_titles.add(clean_title)

            # --- Extract Address if XMP missed it ---
            if not parts:
                loc_val = iptc.get((2, 92))
                city_val = iptc.get((2, 90))
                state_val = iptc.get((2, 95))
                country_val = iptc.get((2, 101))
                
                if loc_val: parts.append(decode_val(loc_val))
                elif city_val: parts.append(decode_val(city_val))
                
                if state_val:
                    s_str = decode_val(state_val)
                    if s_str not in parts: parts.append(s_str)
                    
                if country_val:
                    c_str = decode_val(country_val)
                    if c_str not in parts: parts.append(c_str)
    except Exception: pass

    if parts: 
        existing_address = ", ".join(parts)
        
    exif_dump_str = "\n".join(dump_lines) if dump_lines else ""

    return date_time, lat, lon, existing_address, exif_dump_str

def reverse_geocode(lat, lon):
    pause_limit = getattr(config, 'GEO_RATE_LIMIT_PAUSE', 1.05)
    if 'last_geocode_time' not in st.session_state: st.session_state.last_geocode_time = 0.0
    elapsed = time.time() - st.session_state.last_geocode_time
    if elapsed < pause_limit: time.sleep(pause_limit - elapsed)

    try:
        geolocator = Nominatim(user_agent=getattr(config, 'GEO_USER_AGENT', 'vlm_sandbox'))
        location = geolocator.reverse(f"{lat}, {lon}")
        st.session_state.last_geocode_time = time.time()
        return location.address if location else ""
    except: return ""

# ==========================================
# CORE LLM FUNCTION
# ==========================================
async def send_to_llm(profile, full_messages_array, override_params):
    client = AsyncOpenAI(
        base_url=profile.get("base_url"), 
        api_key=profile["api_key"], 
        timeout=180.0
    )

    api_args = profile["api_params"].copy()
    api_args["model"] = profile["model"]
    api_args["messages"] = full_messages_array
    
    for key, value in override_params.items():
        if key == "extra_body" and isinstance(value, dict) and "extra_body" in api_args:
            api_args["extra_body"].update(value)
        else:
            api_args[key] = value
    
    start_time = time.time()
    try:
        response = await client.chat.completions.create(**api_args)
        elapsed_time = time.time() - start_time
        
        message = response.choices[0].message
        
        # 1. Check for standard OpenAI reasoning field
        reasoning = getattr(message, "reasoning_content", None)
        
        # 2. Check for alternative reasoning attribute
        if not reasoning:
            reasoning = getattr(message, "reasoning", None)
            
        # 3. Check if the openai library stashed it in 'model_extra' (this is what is happening to you)
        if not reasoning and hasattr(message, "model_extra") and message.model_extra:
            reasoning = message.model_extra.get("reasoning", None)
        
        return {
            "success": True,
            "text": response.choices[0].message.content,
            "reasoning": reasoning,
            "stats": {
                "Prompt Tokens": response.usage.prompt_tokens if hasattr(response, 'usage') else 0,
                "Completion Tokens": response.usage.completion_tokens if hasattr(response, 'usage') else 0,
                "Time (s)": round(elapsed_time, 2),
                "Model": profile["model"]
            },
            "raw": response.model_dump_json(indent=4),
            "payload": full_messages_array
        }
    except Exception as e:
        return {"success": False, "error": str(e), "payload": full_messages_array}

# ==========================================
# UI INITIALIZATION & STATE
# ==========================================
st.set_page_config(page_title="Multi-Turn VLM Sandbox", layout="wide")

if "blocks" not in st.session_state:
    st.session_state.blocks = [{"id": str(uuid.uuid4()), "type": "text"}]

if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

if "confirm_clear" not in st.session_state:
    st.session_state.confirm_clear = False

if "total_images_processed" not in st.session_state:
    st.session_state.total_images_processed = 0

if "global_vars" not in st.session_state:
    st.session_state.global_vars = {}

def add_text_block():
    st.session_state.blocks.append({"id": str(uuid.uuid4()), "type": "text"})

def add_image_block():
    st.session_state.blocks.append({"id": str(uuid.uuid4()), "type": "image"})

def add_video_block():
    st.session_state.blocks.append({"id": str(uuid.uuid4()), "type": "video"})

def remove_block(block_id):
    st.session_state.blocks = [b for b in st.session_state.blocks if b["id"] != block_id]

# ==========================================
# SIDEBAR
# ==========================================
st.sidebar.header("1. Model Selection")
profile_names = [f"{p['name']} ({p['model']})" for p in config.LLM_PROFILES]
selected_idx = st.sidebar.selectbox("Active Model", range(len(profile_names)), format_func=lambda x: profile_names[x], index=config.ACTIVE_LLM_PROFILE)
active_profile = config.LLM_PROFILES[selected_idx]


st.sidebar.header("2. Model Parameters")

# 1. Determine the correct token key for the active model
token_key = "max_completion_tokens" if "max_completion_tokens" in active_profile["api_params"] else "max_tokens"

# 2. Extract defaults based on what the profile ACTUALLY supports
default_temp = float(active_profile["api_params"].get("temperature", 0.7))
default_top_p = float(active_profile["api_params"].get("top_p", 0.9))
default_max_tokens = int(active_profile["api_params"].get(token_key, 5000))
default_seed = int(getattr(config, 'SEED_DEFAULT', 1))

# 3. Render UI
ui_temperature = st.sidebar.number_input("Temperature", min_value=0.0, max_value=2.0, value=default_temp, step=0.05, format="%.2f")
ui_top_p = st.sidebar.number_input("Top P", min_value=0.0, max_value=1.0, value=default_top_p, step=0.05, format="%.2f")
ui_max_tokens = st.sidebar.number_input("Max Tokens", min_value=1, max_value=128000, value=default_max_tokens)
ui_seed = st.sidebar.number_input("Seed", min_value=0, value=default_seed)

# --- ADVANCED MODEL SETTINGS ---
supports_chat_template = (
    "extra_body" in active_profile.get("api_params", {}) 
    and "chat_template_kwargs" in active_profile["api_params"]["extra_body"]
    and "enable_thinking" in active_profile["api_params"]["extra_body"]["chat_template_kwargs"]
)

if supports_chat_template:
    default_thinking = active_profile["api_params"]["extra_body"]["chat_template_kwargs"].get("enable_thinking", False)
    ui_enable_thinking = st.sidebar.checkbox(
        "🧠 Enable Thinking", 
        value=default_thinking, 
        help="Instructs the model to \"think\" before it outputs the final answer."
    )


# --- VIDEO SETTINGS SECTION ---
st.sidebar.header("3. Video Parameters (vLLM)")

# Safely check if the active profile actually supports mm_processor_kwargs
supports_mm_kwargs = (
    "extra_body" in active_profile.get("api_params", {}) 
    and "mm_processor_kwargs" in active_profile["api_params"]["extra_body"]
)

default_fps = 2.0
default_max_frames = 64

if supports_mm_kwargs:
    kwargs = active_profile["api_params"]["extra_body"]["mm_processor_kwargs"]
    default_fps = float(kwargs.get("fps", 2.0))
    default_max_frames = int(kwargs.get("max_frames", 64))

ui_fps = st.sidebar.number_input(
    "Sampling FPS", min_value=0.01, max_value=120.0, value=default_fps, 
    disabled=not supports_mm_kwargs, 
    help="Higher FPS captures more detail but consumes massive amounts of tokens."
)
ui_max_frames = st.sidebar.number_input(
    "Max Frames Cap", min_value=1, max_value=10000, value=default_max_frames, 
    disabled=not supports_mm_kwargs, 
    help="Safety limit. Prevents long videos from crashing the context window."
)

# 4. Build override params dynamically (STRICT INJECTION)
override_params = {}

# Only inject keys if they are explicitly listed in the config's api_params
if "temperature" in active_profile["api_params"]:
    override_params["temperature"] = ui_temperature

if "top_p" in active_profile["api_params"]:
    override_params["top_p"] = ui_top_p

if "seed" in active_profile["api_params"]:
    override_params["seed"] = ui_seed

if token_key in active_profile["api_params"]:
    override_params[token_key] = ui_max_tokens

# 5. Build extra_body dynamically (Video + Thinking Overrides)
has_extra_body_overrides = False
active_extra_body = active_profile["api_params"].get("extra_body", {}).copy()

if supports_mm_kwargs:
    active_extra_body["mm_processor_kwargs"] = {
        "fps": ui_fps,
        "max_frames": ui_max_frames,
        "do_sample_frames": True
    }
    has_extra_body_overrides = True
    
if supports_chat_template:
    # Copy the nested dictionary so we don't accidentally alter the global config
    active_extra_body["chat_template_kwargs"] = active_extra_body.get("chat_template_kwargs", {}).copy()
    active_extra_body["chat_template_kwargs"]["enable_thinking"] = ui_enable_thinking
    has_extra_body_overrides = True
    
if has_extra_body_overrides:
    override_params["extra_body"] = active_extra_body

# --- SYSTEM & EXIF PROMPTS SECTION ---
st.sidebar.header("4. System & Auto-Prompts")
sys_prompt = st.sidebar.text_area("System Context", value=config.SYSTEM_PROMPT, height=150)

st.sidebar.markdown("**Auto-EXIF Injection**")
ui_auto_exif = st.sidebar.checkbox("Inject Address & Time after images", value=True, help="Automatically append the text prompts below directly after each image if EXIF data was found.")
ui_exif_address = st.sidebar.text_area("Address Prompt", value=getattr(config, 'EXIF_PROMPT_ADDRESS', "The image was taken on this address:"), height=68)
ui_exif_time = st.sidebar.text_area("Time Prompt", value=getattr(config, 'EXIF_PROMPT_TIME', "The image was taken at this time:"), height=68)

st.sidebar.markdown("**Full EXIF Dump**")
ui_inject_exif_dump = st.sidebar.checkbox("Inject Full EXIF Dump", value=True, help="Append the raw, formatted dictionary of EXIF tags at the very end of the image.")
ui_exif_dump = st.sidebar.text_area("Dump Prompt", value=getattr(config, 'EXIF_PROMPT_DUMP', "Here is full EXIF dump:"), height=68)

st.sidebar.divider()
st.sidebar.info(
    "💡 **Media & Variables Guide**\n\n"
    "Upload images to an **Image Block** to generate dynamic variables (e.g., `{geo_1}`, `{time_1}`).\n\n"
    "Upload video files to a **Video Block** for native video analysis.\n\n"
    "Variables are kept for the **entire conversation**."
)

# ==========================================
# MAIN AREA: CHAT HISTORY
# ==========================================
if len(st.session_state.chat_history) > 0:
    st.markdown("### 💬 Conversation History")
    for i, turn in enumerate(st.session_state.chat_history):
        
        with st.chat_message("user"):
            for item in turn["user_payload"]:
                if item["type"] == "text":
#                    st.write(item["text"]) # this removes new lines!
                    st.text(item["text"])
                elif item["type"] == "image_url":
                    st.image(item["image_url"]["url"], width=250)
                elif item["type"] == "video_url":
                    st.video(item["video_url"]["url"])
                    
        with st.chat_message("assistant"):
            if turn.get("reasoning"):
                with st.expander("🧠 View Model Thinking"):
                    st.write(turn["reasoning"])
                    
            st.info(turn["assistant_text"])
            
            col_a, col_b = st.columns(2)
            with col_a:
                with st.expander("📊 Token & Time Statistics"):
                    st.json(turn["stats"])
            with col_b:
                if turn.get("variables"):
                    with st.expander("📍 Variables Extracted (This Turn)"):
                        st.json(turn["variables"])
            
            with st.expander("✉️ View Assembled Prompt Payload"):
                safe_messages = json.loads(json.dumps(turn["full_messages_sent"]))
                for message in safe_messages:
                    if isinstance(message.get("content"), list):
                        for item in message["content"]:
                            if item.get("type") == "image_url":
                                item["image_url"]["url"] = "[BASE64_IMAGE_DATA_REMOVED]"
                            elif item.get("type") == "video_url":
                                item["video_url"]["url"] = "[BASE64_VIDEO_DATA_REMOVED]"
                st.json(safe_messages)
                
            if getattr(config, 'LOG_RAW_RESPONSES', 0) == 1:
                with st.expander("🛠️ Raw API JSON Response"):
                    st.code(turn["raw"], language="json")
    
    st.divider()

# ==========================================
# MAIN AREA: NEW PROMPT BUILDER
# ==========================================
st.markdown("### 🧩 Build Next Message")

ui_global_img_counter = st.session_state.total_images_processed

for i, block in enumerate(st.session_state.blocks):
    with st.container():
        col1, col2 = st.columns([11, 1])
        
        if block["type"] == "text":
            with col1:
                st.session_state[f"val_{block['id']}"] = st.text_area(f"📝 Text Block", value=st.session_state.get(f"val_{block['id']}", ""), key=f"ui_{block['id']}", placeholder="Write prompt here... You can use {geo_1}, {time_1}, etc.")
            with col2:
                st.write("") 
                st.write("")
                st.button("❌", key=f"del_{block['id']}", on_click=remove_block, args=(block["id"],), help="Remove this text block")

        elif block["type"] == "image":
            with col1:
                uploaded_files = st.session_state.get(f"file_{block['id']}", [])
                pasted_images = st.session_state.get(f"pasted_{block['id']}", [])
                total_imgs = len(uploaded_files) + len(pasted_images)
                
                if total_imgs > 0:
                    var_range = [f"`{{geo_{ui_global_img_counter + j + 1}}}`/`{{time_{ui_global_img_counter + j + 1}}}`" for j in range(total_imgs)]
                    st.markdown(f"**🖼️ Image Block** *(Vars available for these {total_imgs} images: {', '.join(var_range)})*")
                else:
                    st.markdown("**🖼️ Image Block** *(Upload or paste images to generate variable names)*")

                up_col1, up_col2 = st.columns([4, 1])
                with up_col1:
                    uploaded_files = st.file_uploader("Upload Image", type=["jpg", "jpeg", "png"], accept_multiple_files=True, key=f"file_{block['id']}", label_visibility="collapsed")
                with up_col2:
                    st.write("") 
                    if st.button("📋 Paste", key=f"paste_btn_{block['id']}", help="Paste screenshot from clipboard"):
                        try:
                            clip_img = ImageGrab.grabclipboard()
                            if isinstance(clip_img, Image.Image):
                                if f"pasted_{block['id']}" not in st.session_state:
                                    st.session_state[f"pasted_{block['id']}"] = []
                                st.session_state[f"pasted_{block['id']}"].append(clip_img)
                                st.rerun()
                            else:
                                st.toast("No image found in clipboard!")
                        except Exception as e:
                            st.toast(f"Clipboard error: {e}")
                    
                    if pasted_images:
                        if st.button("🗑️ Clear Pasted", key=f"clear_paste_{block['id']}"):
                            st.session_state[f"pasted_{block['id']}"] = []
                            st.rerun()
                
                scol1, scol2, scol3, scol4, scol5 = st.columns([2, 2, 2, 2, 2])
                use_native = scol1.checkbox("Keep Native Res", value=True, key=f"ui_native_{block['id']}")
                st.session_state[f"res_{block['id']}"] = scol2.number_input(f"Resize to Max Size (px)", min_value=100, max_value=8000, value=1500, key=f"ui_res_{block['id']}", disabled=use_native)
                st.session_state[f"time_{block['id']}"] = scol3.checkbox("Extract Time", value=True, key=f"ui_time_{block['id']}")
                st.session_state[f"address_{block['id']}"] = scol4.checkbox("Use Address", value=True, key=f"ui_address_{block['id']}")
                st.session_state[f"revgeo_{block['id']}"] = scol5.checkbox("Enforce RevGeo", value=False, key=f"ui_revgeo_{block['id']}")
                
                if total_imgs > 0:
                    with st.expander(f"Preview {total_imgs} Selected Images"):
                        preview_cols = st.columns(3)
                        current_idx = 0
                        
                        if uploaded_files:
                            for uf in uploaded_files:
                                preview_img = Image.open(uf)
                                preview_img = ImageOps.exif_transpose(preview_img)
                                preview_cols[current_idx % 3].image(preview_img, caption=f"Img #{ui_global_img_counter + current_idx + 1}: {uf.name}", width="stretch")
                                current_idx += 1
                                
                        if pasted_images:
                            for p_img in pasted_images:
                                preview_img = ImageOps.exif_transpose(p_img.copy())
                                preview_cols[current_idx % 3].image(preview_img, caption=f"Img #{ui_global_img_counter + current_idx + 1}: Pasted Screenshot", width="stretch")
                                current_idx += 1
                                
                    ui_global_img_counter += total_imgs

            with col2:
                st.write("")
                st.button("❌", key=f"del_{block['id']}", on_click=remove_block, args=(block["id"],), help="Remove this image block")

        elif block["type"] == "video":
            with col1:
                uploaded_files = st.session_state.get(f"file_{block['id']}", [])
                total_vids = len(uploaded_files)
                
                if total_vids > 0:
                    st.markdown(f"**🎥 Video Block** *({total_vids} video selected)*")
                else:
                    st.markdown("**🎥 Video Block** *(Upload video files for native processing)*")

                uploaded_files = st.file_uploader("Upload Video", type=["mp4", "mov"], accept_multiple_files=True, key=f"file_{block['id']}", label_visibility="collapsed")
                
                if uploaded_files:
                    with st.expander(f"Preview {total_vids} Selected Video(s)"):
                        preview_cols = st.columns(3)
                        for idx, uf in enumerate(uploaded_files):
                            preview_cols[idx % 3].video(uf)
            with col2:
                st.write("")
                st.button("❌", key=f"del_{block['id']}", on_click=remove_block, args=(block["id"],), help="Remove this video block")
        
        st.markdown("<hr style='margin: 10px 0; border-color: #333;'>", unsafe_allow_html=True)

# --- CONTROLS ROW ---
b_col1, b_col2, b_col3, b_spacer, b_col4 = st.columns([2, 2, 2, 4, 2])
b_col1.button("📝 Add Text", on_click=add_text_block, use_container_width=True)
b_col2.button("🖼️ Add Image", on_click=add_image_block, use_container_width=True)
b_col3.button("🎥 Add Video", on_click=add_video_block, use_container_width=True)

with b_col4:
    if st.button("🗑️ Clear All", type="primary", use_container_width=True):
        st.session_state.confirm_clear = True

if st.session_state.confirm_clear:
    st.warning("⚠️ Are you sure you want to delete the entire chat history and reset the builder?")
    c1, c2, c3 = st.columns([2, 2, 6])
    
    if c2.button("No, Keep It"):
        st.session_state.confirm_clear = False
        st.rerun()

    if c1.button("Yes, Clear All", type="primary"):
        st.session_state.blocks = [{"id": str(uuid.uuid4()), "type": "text"}]
        st.session_state.chat_history = []
        st.session_state.total_images_processed = 0
        st.session_state.global_vars = {}
        st.session_state.confirm_clear = False
        st.rerun()

# ==========================================
# EXECUTION PHASE
# ==========================================
st.divider()

if len(st.session_state.blocks) > 0 and not st.session_state.confirm_clear:
    if st.button("🚀 ASSEMBLE & SEND TO LLM", type="primary", use_container_width=True):
        
        progress_text = st.empty()
        
        current_turn_variables = {}
        raw_payload = []
        execution_img_counter = st.session_state.total_images_processed
        
        progress_text.text("Processing media and extracting metadata...")
        
        for block in st.session_state.blocks:
            if block["type"] == "text":
                text_content = st.session_state.get(f"val_{block['id']}", "")
                raw_payload.append({"role_type": "text", "data": text_content})
                
            elif block["type"] == "image":
                uploaded_files = st.session_state.get(f"file_{block['id']}", [])
                pasted_images = st.session_state.get(f"pasted_{block['id']}", [])
                
                all_imgs_to_process = []
                for uf in uploaded_files:
                    all_imgs_to_process.append({"img": Image.open(uf), "name": uf.name})
                for idx, p_img in enumerate(pasted_images):
                    all_imgs_to_process.append({"img": p_img.copy(), "name": f"Pasted_Screenshot_{idx+1}.jpg"})
                
                if all_imgs_to_process:
                    for item in all_imgs_to_process:
                        execution_img_counter += 1
                        
                        original_img = item["img"]
                        
                        use_time = st.session_state.get(f"time_{block['id']}", True)
                        use_address = st.session_state.get(f"address_{block['id']}", True)
                        enforce_revgeo = st.session_state.get(f"revgeo_{block['id']}", False)
                        
                        date_time, lat, lon, existing_addr, ex_dump = "Unknown Time", None, None, None, ""
                        
                        if use_time or use_address or ui_inject_exif_dump:
                            dt, l_lat, l_lon, ex_addr, extracted_dump = extract_exif_data(original_img)
                            if use_time and dt: date_time = dt
                            lat, lon, existing_addr = l_lat, l_lon, ex_addr
                            ex_dump = extracted_dump
                        
                        original_img = ImageOps.exif_transpose(original_img)
                        
                        # --- ADDRESS RESOLUTION LOGIC ---
                        geo_str = "Unknown Location"
                        if use_address:
                            if existing_addr and not enforce_revgeo:
                                geo_str = existing_addr
                            elif lat and lon:
                                progress_text.text(f"Reverse geocoding Image #{execution_img_counter}...")
                                addr = reverse_geocode(lat, lon)
                                geo_str = addr if addr else (existing_addr or "Unknown Location")
                            elif existing_addr:
                                geo_str = existing_addr
                            
                        current_turn_variables[f"{{geo_{execution_img_counter}}}"] = geo_str
                        current_turn_variables[f"{{time_{execution_img_counter}}}"] = date_time
                        
                        is_native = st.session_state.get(f"ui_native_{block['id']}", False)
                        if is_native:
                            processed_img = original_img
                        else:
                            max_res = st.session_state.get(f"res_{block['id']}", 1500)
                            processed_img = resize_image(original_img, max_res)
                            
                        b64 = image_to_base64(processed_img)
                        raw_payload.append({"role_type": "image", "data": b64, "name": item["name"], "img_idx": execution_img_counter, "exif_dump": ex_dump})
                else:
                    pass
                    
            elif block["type"] == "video":
                uploaded_files = st.session_state.get(f"file_{block['id']}", [])
                if uploaded_files:
                    for uf in uploaded_files:
                        video_bytes = uf.read()
                        b64_video = base64.b64encode(video_bytes).decode('utf-8')
                        mime_type = "video/mp4" if uf.name.lower().endswith('.mp4') else "video/quicktime"
                        raw_payload.append({"role_type": "video", "data": f"data:{mime_type};base64,{b64_video}"})
                else:
                    pass

        progress_text.text("Assembling prompt payload...")
        
        all_variables = {**st.session_state.global_vars, **current_turn_variables}
        final_user_content = []
        
        for item in raw_payload:
            if item["role_type"] == "text":
                text_string = item["data"]
                for var_key, var_val in all_variables.items():
                    text_string = text_string.replace(var_key, str(var_val))
                
                if text_string.strip(): 
                    final_user_content.append({"type": "text", "text": text_string})
                    
            elif item["role_type"] == "image":
                final_user_content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{item['data']}"}})
                
                # --- AUTO EXIF & DUMP INJECTION ---
                if ui_auto_exif or ui_inject_exif_dump:
                    idx = item.get("img_idx")
                    if idx:
                        geo_val = current_turn_variables.get(f"{{geo_{idx}}}")
                        time_val = current_turn_variables.get(f"{{time_{idx}}}")
                        
                        exif_texts = []
                        if ui_auto_exif:
                            if geo_val and geo_val != "Unknown Location":
                                exif_texts.append(f"{ui_exif_address} {geo_val}")
                            if time_val and time_val != "Unknown Time":
                                exif_texts.append(f"{ui_exif_time} {time_val}")
                            
                        if ui_inject_exif_dump and item.get("exif_dump"):
                            exif_texts.append(f"{ui_exif_dump}\n{item['exif_dump']}")
                            
                        if exif_texts:
                            combined_exif_text = "\n".join(exif_texts)
                            final_user_content.append({"type": "text", "text": combined_exif_text})

            elif item["role_type"] == "video":
                final_user_content.append({"type": "video_url", "video_url": {"url": item["data"]}})

        full_messages = [{"role": "system", "content": sys_prompt}]
        
        for turn in st.session_state.chat_history:
            full_messages.append({"role": "user", "content": turn["user_payload"]})
            full_messages.append({"role": "assistant", "content": turn["assistant_text"]})
            
        full_messages.append({"role": "user", "content": final_user_content})

        progress_text.text(f"Awaiting response from {active_profile['model']}...")
        result = asyncio.run(send_to_llm(active_profile, full_messages, override_params))
        progress_text.empty() 
        
        if result["success"]:
            st.session_state.total_images_processed = execution_img_counter
            st.session_state.global_vars.update(current_turn_variables)
            
            st.session_state.chat_history.append({
                "user_payload": final_user_content,
                "assistant_text": result["text"],
                "reasoning": result.get("reasoning"),
                "stats": result["stats"],
                "raw": result["raw"],
                "full_messages_sent": result["payload"],
                "variables": current_turn_variables
            })
            
            st.session_state.blocks = [{"id": str(uuid.uuid4()), "type": "text"}]
            st.rerun() 
            
        else:
            st.error(f"Error communicating with LLM: {result.get('error', 'Unknown Error')}")