import os
import logging

logger = logging.getLogger(__name__)

# Default OCR engine configuration
OCR_ENGINE = os.environ.get("OCR_ENGINE", "paddleocr")

class BaseOCREngine:
    def ocr_image(self, image_path: str) -> str:
        raise NotImplementedError()

class PaddleOCREngine(BaseOCREngine):
    def __init__(self):
        # Lazy import to avoid loading weights/dependencies if not used or failing
        try:
            from paddleocr import PaddleOCR
            # Set environment variable to bypass connectivity checks
            os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"
            # Initialize PaddleOCR
            self.ocr = PaddleOCR(use_angle_cls=True, lang='en')
        except Exception as e:
            logger.error(f"Failed to initialize PaddleOCR: {e}")
            self.ocr = None
            raise e

    def ocr_image(self, image_path: str) -> str:
        if not self.ocr:
            raise RuntimeError("PaddleOCR is not initialized.")
        try:
            result = self.ocr.ocr(image_path, cls=True)
            if not result:
                return ""
            lines = []
            for page in result:
                if page:
                    for line in page:
                        if line and len(line) > 1 and isinstance(line[1], tuple):
                            text, confidence = line[1]
                            if text:
                                lines.append(text)
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error during PaddleOCR execution: {e}")
            raise e

class TesseractOCREngine(BaseOCREngine):
    def ocr_image(self, image_path: str) -> str:
        try:
            import pytesseract
            from PIL import Image
            img = Image.open(image_path)
            return pytesseract.image_to_string(img)
        except Exception as e:
            logger.error(f"Failed to run Tesseract OCR: {e}")
            raise e

class SuryaOCREngine(BaseOCREngine):
    def ocr_image(self, image_path: str) -> str:
        logger.warning("Surya OCR Engine is not fully implemented. Falling back to empty text.")
        return ""

class RapidOCREngine(BaseOCREngine):
    def __init__(self):
        try:
            from rapidocr_onnxruntime import RapidOCR
            self.ocr = RapidOCR()
        except Exception as e:
            logger.error(f"Failed to initialize RapidOCR: {e}")
            self.ocr = None
            raise e

    def ocr_image(self, image_path: str) -> str:
        if not self.ocr:
            raise RuntimeError("RapidOCR is not initialized.")
        try:
            result, elapse = self.ocr(image_path)
            if not result:
                return ""
            lines = []
            for line in result:
                if line and len(line) > 1:
                    text = line[1]
                    if text:
                        lines.append(text)
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Error during RapidOCR execution: {e}")
            raise e

class MockOCREngine(BaseOCREngine):
    def ocr_image(self, image_path: str) -> str:
        logger.warning(f"Mock OCR Engine used for {image_path}")
        return f"[OCR Text from {os.path.basename(image_path)}]"

_engine_instance = None

def get_ocr_engine():
    global _engine_instance
    if _engine_instance is not None:
        return _engine_instance

    engine_name = OCR_ENGINE.lower()
    if engine_name == "paddleocr":
        try:
            _engine_instance = PaddleOCREngine()
        except Exception as e:
            logger.warning(f"PaddleOCR initialization failed: {e}. Falling back to RapidOCREngine.")
            try:
                _engine_instance = RapidOCREngine()
            except Exception as ex:
                logger.warning(f"RapidOCR initialization failed: {ex}. Falling back to MockOCREngine.")
                _engine_instance = MockOCREngine()
    elif engine_name == "rapidocr":
        try:
            _engine_instance = RapidOCREngine()
        except Exception as e:
            logger.warning(f"RapidOCR initialization failed: {e}. Falling back to MockOCREngine.")
            _engine_instance = MockOCREngine()
    elif engine_name == "tesseract":
        _engine_instance = TesseractOCREngine()
    elif engine_name == "surya":
        _engine_instance = SuryaOCREngine()
    else:
        _engine_instance = MockOCREngine()
    return _engine_instance

def ocr_image(image_path: str) -> str:
    try:
        engine = get_ocr_engine()
        return engine.ocr_image(image_path)
    except Exception as e:
        logger.error(f"OCR failed for {image_path}: {e}")
        return ""
