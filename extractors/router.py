import os
from extractors.pdf_extractor import extract_pdf
from extractors.docx_extractor import extract_docx
from extractors.image_extractor import extract_image
from extractors.txt_extractor import extract_txt
from extractors.pptx_extractor import extract_pptx, extract_ppt
from extractors.xlsx_extractor import extract_xlsx

# Map file extensions to their corresponding extractor functions
EXTRACTOR_MAP = {
    '.pdf': extract_pdf,
    '.docx': extract_docx,
    '.png': extract_image,
    '.jpg': extract_image,
    '.jpeg': extract_image,
    '.webp': extract_image,
    '.txt': extract_txt,
    '.pptx': extract_pptx,
    '.ppt': extract_ppt,
    '.xlsx': extract_xlsx
}

def route_file(path: str, force_ocr: bool = False) -> dict:
    """
    Routes a file to the correct extractor based on its extension.
    Returns:
        dict: {
            "text": str,
            "source_type": str,
            "ocr_used": bool,
            "metadata": dict
        }
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    ext = os.path.splitext(path)[1].lower()
    extractor_func = EXTRACTOR_MAP.get(ext)
    
    if not extractor_func:
        raise ValueError(f"No extractor registered for file extension: '{ext}'")

    # Check if the extractor supports force_ocr parameter (like extract_pdf)
    import inspect
    sig = inspect.signature(extractor_func)
    if 'force_ocr' in sig.parameters:
        result = extractor_func(path, force_ocr=force_ocr)
    else:
        result = extractor_func(path)
    
    # Ensure consistent structure is returned
    required_keys = {"text", "source_type", "ocr_used", "metadata"}
    if not isinstance(result, dict) or not required_keys.issubset(result.keys()):
        raise ValueError(
            f"Extractor for extension '{ext}' returned invalid structure: {result}"
        )
        
    return result

def get_allowed_extensions() -> set:
    """Returns the set of all supported file extensions."""
    return set(EXTRACTOR_MAP.keys())
