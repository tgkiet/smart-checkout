import io
import base64
import numpy as np
from PIL import Image

def process_detection(pil_img, img_np, det):
    """Crop the bounding box from image, applying mask if available."""
    x1, y1, x2, y2 = [int(v) for v in det["bbox"]]
    mask_base64 = det.get("mask_base64")
    
    if mask_base64:
        try:
            mask_bytes = base64.b64decode(mask_base64)
            mask_pil = Image.open(io.BytesIO(mask_bytes))
            mask = np.array(mask_pil) // 255
            
            h, w = img_np.shape[:2]
            if mask.shape != (h, w):
                mask_pil = Image.fromarray((mask * 255).astype(np.uint8)).resize((w, h), resample=Image.NEAREST)
                mask = np.array(mask_pil) // 255
                
            masked_img = img_np.copy()
            masked_img[mask == 0] = 255
            cropped = Image.fromarray(masked_img[y1:y2, x1:x2])
            return cropped
        except Exception as e:
            print(f"Mask error, falling back to bbox: {e}")
            
    # Fallback to bbox
    return pil_img.crop((x1, y1, x2, y2))
