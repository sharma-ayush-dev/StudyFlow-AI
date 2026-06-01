import logging

logger = logging.getLogger(__name__)

def extract_txt(path: str) -> dict:
    """
    Reads file contents directly and returns text, with fallback encodings.
    """
    logger.info(f"Extracting TXT: {path}")
    text = ""
    encodings = ['utf-8', 'utf-16', 'latin-1']
    
    for encoding in encodings:
        try:
            with open(path, 'r', encoding=encoding) as f:
                text = f.read()
            logger.info(f"Successfully read TXT with encoding: {encoding}")
            break
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logger.error(f"Failed to read TXT: {e}")
            raise e
            
    return {
        "text": text,
        "source_type": "txt",
        "ocr_used": False,
        "metadata": {}
    }
