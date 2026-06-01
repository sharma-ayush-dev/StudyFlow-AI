import logging
from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table

logger = logging.getLogger(__name__)

def iter_block_items(parent):
    """
    Yield each paragraph and table child within parent, in document order.
    Each returned value is an instance of either Paragraph or Table.
    """
    if hasattr(parent, 'element') and hasattr(parent.element, 'body'):
        parent_elm = parent.element.body
    else:
        parent_elm = parent._element
        
    for child in parent_elm.iterchildren():
        if child.tag.endswith('p'):
            yield Paragraph(child, parent)
        elif child.tag.endswith('tbl'):
            yield Table(child, parent)

def format_table(table: Table) -> str:
    """
    Formats a DOCX table into a readable text representation where cell values
    are separated by ' | ' and rows are separated by newlines.
    """
    rows_text = []
    for row in table.rows:
        row_vals = []
        for cell in row.cells:
            # Strip outer whitespace and replace internal newlines to keep cell on one line
            cell_text = cell.text.strip().replace('\n', ' ')
            row_vals.append(cell_text)
        
        # Only add rows that have text content
        if any(row_vals):
            rows_text.append(" | ".join(row_vals))
            
    return "\n".join(rows_text)

def extract_docx(path: str) -> dict:
    """
    Extracts paragraphs and tables from a DOCX file in document reading order.
    """
    logger.info(f"Extracting DOCX: {path}")
    text_blocks = []
    
    try:
        doc = Document(path)
        for item in iter_block_items(doc):
            if isinstance(item, Paragraph):
                p_text = item.text.strip()
                if p_text:
                    text_blocks.append(p_text)
            elif isinstance(item, Table):
                t_text = format_table(item)
                if t_text:
                    text_blocks.append(t_text)
    except Exception as e:
        logger.error(f"Failed to extract DOCX: {e}")
        raise e
        
    extracted_text = "\n\n".join(text_blocks)
    return {
        "text": extracted_text,
        "source_type": "docx",
        "ocr_used": False,
        "metadata": {}
    }
