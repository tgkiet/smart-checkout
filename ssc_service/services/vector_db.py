from qdrant_client import QdrantClient
from config import QDRANT_HOST, QDRANT_PORT, QDRANT_COLLECTION

class VectorDBService:
    def __init__(self):
        try:
            self.client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        except Exception as e:
            print(f"Warning: Could not connect to Qdrant at startup: {e}")
            self.client = None

    def search(self, embedding, limit=1, threshold=0.6):
        if not self.client:
            raise Exception("Qdrant client not initialized")
            
        try:
            results = self.client.search(
                collection_name=QDRANT_COLLECTION,
                query_vector=embedding,
                limit=limit
            )
            
            valid_results = []
            for match in results:
                if match.score > threshold:
                    valid_results.append({
                        "name": match.payload.get("name", "Unknown Product"),
                        "price": float(match.payload.get("price", 0.0)),
                        "score": match.score,
                        "sku": match.payload.get("sku", ""),
                        "platform": match.payload.get("platform", "")
                    })
            return valid_results
        except Exception as e:
            print(f"Qdrant search error: {e}")
            return []

vector_db = VectorDBService()
