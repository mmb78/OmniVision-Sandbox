import os

# --- NETWORK & API SETTINGS ---
# Select which profile to use (0 = Local, 1 = OpenAI, etc.)
ACTIVE_LLM_PROFILE = 0

# --- LLM PARAMETERS ---
SEED_DEFAULT = 1
LOG_RAW_RESPONSES = 1 # "1" to log raw JSON response

LLM_PROFILES = [
    # [0] Primary "Local" Server
    {
        "name": "Gemma4 26B",
        "base_url": "http://localhost:8000/v1",
        "api_key": "local-llm-key", # Required by OpenAI library, but ignored by local servers
        "model": "google/gemma-4-26B-A4B-it",
        "api_params": {
            "temperature": 1,
            "top_p": 0.95,
            "max_tokens": 5000,
            "seed": None  # <--- Placeholder: Tells the worker this model accepts seeds!
        }
    },
    # [1] Secondary "Local" Server
    {
        "name": "Qwen3.6 35B",
        "base_url": "http://localhost:11434/v1",
        "api_key": "local-llm-key", # Required by OpenAI library, but ignored by local servers
        "model": "Qwen/Qwen3.6-35B-A3B-FP8",
        "api_params": {
            "temperature": 0.7,
            "top_p": 0.8,
            "presence_penalty": 1.5,
            "max_tokens": 5000,
            "extra_body": {
                "top_k": 20,
                "min_p": 0.0,
                "repetition_penalty": 1.0,
                "mm_processor_kwargs": {"fps": 1, "max_frames": 1200, "do_sample_frames": True},
                "chat_template_kwargs": {"enable_thinking": False}
                },
            "seed": None  # <--- Placeholder: Tells the worker this model accepts seeds!
        }
    },
    # [2] Secondary Remote Server - thinking model!
    {
        "name": "Qwen 3.5 397B",
        "base_url": os.getenv("LITELLM_API_BASE", "localhost"), # Your alternative port/IP from OS env
        "api_key": os.getenv("LITELLM_API_KEY", ""),
        "model": "qwen35-397b-a17b-fp8",
        "api_params": {
            "temperature": 0.7,
            "top_p": 0.9,
            "frequency_penalty": 0.5,
            "reasoning_effort": "low", # Can be "low", "medium", or "high"
            "max_tokens": 50000,
            "presence_penalty": 0.5, # Encourages broader vocabulary
            "frequency_penalty": 0.3, # Stops repetitive keyword looping
            "timeout": 45.0, # If the server doesn't reply in 45 seconds, kill it and retry!
            "seed": None  # <--- Placeholder: Tells the worker this model accepts seeds!
        }
    },
    # [3] OpenAI Cloud
    {
        "name": "OpenAI GPT-5.4",
        "base_url": None, # Leaving this None tells the client to use the official OpenAI URL
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "model": "gpt-5.4-nano", # Or whatever OpenAI model you prefer
        "api_params": {
            "temperature": 1.0,
            "reasoning_effort": "low", # Can be "low", "medium", or "high"
            "max_completion_tokens": 5000,
            "seed": None  # <--- Placeholder: Tells the worker this model accepts seeds!
        }
    },
    # [4] Google Gemini API - no SEED parameter
    {
        "name": "Gemini 3.1 Flash Lite",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key": os.getenv("GEMINI_API_KEY", ""),
        "model": "gemini-3.1-flash-lite-preview",
        "api_params": {
            "temperature": 1,
            "top_p": 0.95,
            "max_tokens": 5000
        }
    }
]


# --- SYSTEM SETTINGS ---
SYSTEM_PROMPT = "You are an expert AI assistant tasked with carefully analyzing images and text provided by the user. Follow all instructions precisely."
EXIF_PROMPT_ADDRESS = "The image was taken on this address:\n"
EXIF_PROMPT_TIME = "The image was taken at this time:\n"
EXIF_PROMPT_DUMP = "Here is a full EXIF dump:\n"

# --- GEOCODING SETTINGS ---
GEO_USER_AGENT = "vlm_sandbox_tester"
GEO_RATE_LIMIT_PAUSE = 1.05  # Strict Nominatim 1s+ rule