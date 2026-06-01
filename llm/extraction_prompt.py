"""
llm/extraction_prompt.py — Contains prompt templates for LLM extraction.
"""

_PROMPT = """Extract study planner data from the SYLLABUS and DATESHEET provided.
You MUST return valid JSON. No explanations, no markdown, no trailing commas, no comments.

{
  "Exam_dates": {"SubjectName": "DD-MM-YYYY"},
  "Subjects": {
    "SubjectName": {
      "TopicName": {
        "subtopics": ["Subtopic 1", "Subtopic 2", "Subtopic 3"]
      }
    }
  }
}

Rules:
- Only include subjects present in SYLLABUS
- 3-7 topics per subject (unit/chapter titles preferred)
- 2-5 subtopics per topic (key concepts within that unit)
- If no subtopics can be identified, use an empty array []
- Exam dates in DD-MM-YYYY format; omit subject from Exam_dates if not found"""
