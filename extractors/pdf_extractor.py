import os
import re
import logging
import fitz  # PyMuPDF
from extractors.ocr import ocr_image

logger = logging.getLogger(__name__)

# Minimum characters extracted normally before we assume it is a scanned PDF
MIN_NORMAL_TEXT_LEN = 100

def check_text_quality(text: str) -> float:
    """
    Evaluates the quality of extracted text. Returns a score from 0.0 to 1.0.
    Identifies layout jumbling / merging signature patterns such as:
    - Embedded digits inside alphabetic words (e.g. 'their8classification')
    - High ratio of single-letter words (indicates layout splitting characters)
    - Character noise and spacing anomalies
    """
    stripped = text.strip()
    if not stripped:
        return 0.0
    
    words = stripped.split()
    if not words:
        return 0.0
        
    # 1. Check for embedded digits inside alphabetic words (e.g. "their8", "its8")
    # This is a major signature of column merging in PDF tables.
    embedded_digit_words = [w for w in words if re.search(r'[a-zA-Z]+\d+[a-zA-Z]*|\d+[a-zA-Z]+', w)]
    embedded_digit_ratio = len(embedded_digit_words) / len(words)
    
    # 2. Check for excessive single-letter tokens (excluding common letters/marks)
    # Jumbled layouts often scatter individual characters.
    standard_single_chars = {'a', 'i', 'o', 'A', 'I', '&', '-', '|', '•', '*'}
    single_char_words = [w for w in words if len(w) == 1 and w not in standard_single_chars]
    single_char_ratio = len(single_char_words) / len(words)
    
    # 3. Check for standard alphanumeric word density
    alnum_words = [w for w in words if any(c.isalnum() for c in w)]
    alnum_ratio = len(alnum_words) / len(words)
    
    # 4. Check for character noise (ratio of alphanumeric/space chars to total chars)
    chars = [c for c in stripped if c.isalnum() or c.isspace()]
    readable_char_ratio = len(chars) / len(stripped)
    
    # Calculate score starting at 1.0
    score = 1.0
    
    # Penalize if we see column numbers merged into words
    if embedded_digit_ratio > 0.02:
        score -= 0.3
    if embedded_digit_ratio > 0.05:
        score -= 0.2
        
    # Penalize if there's an excessive amount of single characters scattered around
    if single_char_ratio > 0.10:
        score -= 0.2
    if single_char_ratio > 0.20:
        score -= 0.2
        
    # Penalize if word density or characters contain too much noise
    if alnum_ratio < 0.75:
        score -= 0.2
    if readable_char_ratio < 0.80:
        score -= 0.2
        
    return max(0.0, score)

def extract_pdf(path: str, force_ocr: bool = False) -> dict:
    """
    Extracts text from a PDF file using PyMuPDF.
    If the extracted text is empty, very short, poor quality, or force_ocr is True,
    falls back to page rendering and OCR.
    """
    logger.info(f"Extracting PDF with PyMuPDF: {path} (force_ocr={force_ocr})")
    ocr_used = False
    metadata = {}
    
    # 1. Attempt normal PDF text extraction using PyMuPDF (fitz)
    text_parts = []
    try:
        doc = fitz.open(path)
        for page in doc:
            page_text = page.get_text()
            if page_text:
                text_parts.append(page_text)
        metadata["pages_parsed"] = len(doc)
    except Exception as e:
        logger.warning(f"PyMuPDF extraction failed for {path}: {e}")

    extracted_text = "\n".join(text_parts)
    
    # Analyze quality
    quality_score = check_text_quality(extracted_text)
    metadata["normal_extraction_quality"] = round(quality_score, 2)
    
    # 2. Check if normal text extraction is sufficient and of acceptable quality
    if not force_ocr and len(extracted_text.strip()) >= MIN_NORMAL_TEXT_LEN and quality_score >= 0.6:
        logger.info(f"Normal PDF text extraction sufficient: {len(extracted_text)} chars, quality={quality_score:.2f}")
    else:
        if force_ocr:
            logger.info("Forced OCR requested. Ignoring text layer.")
        elif quality_score < 0.6:
            logger.info(f"Normal PDF text extraction has poor quality ({quality_score:.2f}). Falling back to OCR.")
        else:
            logger.info(f"Normal PDF text extraction insufficient ({len(extracted_text)} chars). Falling back to OCR.")
        
        # 3. Scanned PDF: convert pages into images and run OCR
        ocr_text_parts = []
        try:
            pages = []
            try:
                from pdf2image import convert_from_path
                pages = convert_from_path(path)
                logger.info("Successfully converted PDF to images using pdf2image.")
            except Exception as e_pdf2image:
                logger.warning(f"pdf2image conversion failed: {e_pdf2image}. Trying pypdfium2.")
                try:
                    import pypdfium2 as pdfium
                    pdf = pdfium.PdfDocument(path)
                    pages = [page.render(scale=3).to_pil() for page in pdf]
                    logger.info(f"Successfully converted PDF to images using pypdfium2: {len(pages)} pages.")
                except Exception as e_pdfium:
                    logger.error(f"pypdfium2 conversion also failed: {e_pdfium}")
                    raise RuntimeError("Both pdf2image (poppler) and pypdfium2 failed to render PDF pages.")
            
            # Temporary directory inside the workspace
            import uuid
            workspace_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            temp_dir = os.path.join(workspace_dir, f"temp_pdf_pages_{uuid.uuid4().hex}")
            os.makedirs(temp_dir, exist_ok=True)
            
            for i, page in enumerate(pages):
                page_img_path = os.path.join(temp_dir, f"page_{i}.png")
                page.save(page_img_path, "PNG")
                
                # OCR the page image
                page_text = ocr_image(page_img_path)
                if page_text:
                    ocr_text_parts.append(page_text)
                
                # Delete page image immediately
                try:
                    os.remove(page_img_path)
                except Exception as ex:
                    logger.warning(f"Failed to remove temp file {page_img_path}: {ex}")
            
            # Clean up temp directory
            try:
                os.rmdir(temp_dir)
            except Exception:
                pass
                
            extracted_text = "\n".join(ocr_text_parts)
            ocr_used = True
            metadata["ocr_pages_processed"] = len(pages)
            logger.info(f"OCR PDF extraction complete: {len(extracted_text)} chars from {len(pages)} pages")
            
        except Exception as e:
            logger.error(f"OCR PDF extraction failed: {e}. Returning normal extracted text.")
            metadata["ocr_error"] = str(e)
            # Retain what we had from normal extraction (could be empty or short)
            
    return {
        "text": extracted_text,
        "source_type": "pdf",
        "ocr_used": ocr_used,
        "metadata": metadata
    }
