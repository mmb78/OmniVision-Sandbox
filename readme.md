# OmniVision Sandbox

A powerful, multi-turn Streamlit UI designed for testing, comparing, and pushing the limits of Vision-Language Models (VLMs). Whether you are running local instances (like Gemma, Qwen) or cloud APIs (OpenAI GPT-4o, Google Gemini), this sandbox provides a unified interface to craft complex multi-modal prompts.

## ✨ Features

* **Universal Model Support:** Easily switch between local models (via vLLM/Ollama) and commercial APIs (OpenAI, Gemini) using a centralized config.py.
* **Rich Media Handling:**
  * **Images:** Upload or paste screenshots directly.
  * **Video:** Native video parsing and frame sampling support.
* **Smart EXIF & Location Extraction:** Automatically extracts EXIF data, XMP metadata, and IPTC tags from uploaded images.
* **Reverse Geocoding:** Converts GPS coordinates found in images into human-readable addresses using Nominatim.
* **Dynamic Variable Injection:** Automatically maps extracted locations and timestamps to variables (e.g., {geo_1}, {time_1}) that you can use dynamically in your text prompts.
* **Granular Parameter Control:** Adjust Temperature, Top P, Max Tokens, Seeds, and Video Sampling Frames directly from the sidebar.
* **Transparent Execution:** Inspect exact JSON payloads, token usage, and execution times for every turn.

## 🛠️ Prerequisites

You will need **Python 3.8+**. 

The following dependencies are required to run the application:
* streamlit (UI framework)
* openai (API client for both OpenAI and local OpenAI-compatible endpoints)
* Pillow (Image processing and EXIF extraction)
* geopy (Reverse geocoding)

## 🚀 Installation & Setup

**1. Clone the repository:**
git clone [https://github.com/yourusername/omnivision-sandbox.git](https://github.com/yourusername/omnivision-sandbox.git)
cd omnivision-sandbox

**2. Install dependencies:**
pip install streamlit openai Pillow geopy

**3. Configure your API Keys:**
Set your environment variables for the remote models you wish to use. You can do this in your terminal or via a .env file:

export OPENAI_API_KEY="your-openai-key"
export GEMINI_API_KEY="your-gemini-key"

*(Note: Local models running on localhost:8000 or localhost:11434 do not require API keys).*

**4. Edit the Configuration (Optional):**
Open config.py to modify the LLM_PROFILES list. You can add your own custom local models, change default system prompts, or adjust geocoding limits.

**5. Run the Application:**
streamlit run webapp.py

## 🧩 How to Use the Builder

1. **Select a Model:** Use the sidebar to choose your target LLM.
2. **Add Blocks:** Use the bottom toolbar to add Text, Image, or Video blocks.
3. **Upload Media:** Drag and drop an image or paste from your clipboard. The app will immediately attempt to read its EXIF data.
4. **Use Variables:** If an image has GPS/Time data, the app will display variables like {geo_1} and {time_1}. Write your text prompt like this: *"What is the architectural style of the building in this image? It was taken at {geo_1}."*
5. **Send:** Click **Assemble & Send to LLM** to process the payload.

## ⚠️ Notes on Geocoding Rate Limits
This app uses OpenStreetMap's Nominatim service for reverse geocoding. To comply with their usage policy, the app enforces a strict 1-second pause between requests. Do not upload large batches of GPS-tagged images at once if you are in a rush.