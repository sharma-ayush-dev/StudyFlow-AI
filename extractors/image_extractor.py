import logging
from extractors.ocr import ocr_image

logger = logging.getLogger(__name__)

def extract_image(path: str) -> dict:
    """
    Extracts text from an image file using OCR.
    Implements architecture for fallback to vision model on low confidence.
    """
    logger.info(f"Extracting Image: {path}")
    
    # Run OCR on the image
    text = ocr_image(path)
    
    # Implement architecture for vision model fallback if OCR results are weak
    suggest_fallback = False
    if not text or not text.strip():
        suggest_fallback = True
        logger.warning(f"OCR extracted no text from image: {path}. Suggesting fallback to vision model.")
        
    return {
        "text": text,
        "source_type": "image",
        "ocr_used": True,
        "metadata": {
            "fallback_to_vision_suggested": suggest_fallback,
            "estimated_confidence": 0.0 if suggest_fallback else 1.0
        }
    }
