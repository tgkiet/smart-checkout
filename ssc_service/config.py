import os

API_ENDPOINT = os.getenv("API_ENDPOINT", "http://localhost:8800/api/v1")
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6433))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "smart_checkout_objects")

# Session config
SESSION_MAX_IMAGES = int(os.getenv("SESSION_MAX_IMAGES", 50))
# Max concurrent workers for parallel image processing per session
SESSION_MAX_WORKERS = int(os.getenv("SESSION_MAX_WORKERS", 4))
