import datetime

def _parse_date(s: str) -> datetime.date:
    d, m, y = s.strip().split('-')
    return datetime.date(int(y), int(m), int(d))

def _format_date(d: datetime.date) -> str:
    return d.strftime('%d-%m-%Y')

def _date_range(start: str, end: str) -> dict:
    cur, stop = _parse_date(start), _parse_date(end)
    out = {}
    while cur <= stop:
        out[_format_date(cur)] = 'none'
        cur += datetime.timedelta(days=1)
    return out

def _find(d: dict, *keys):
    for k in keys:
        if k in d: return d[k]
    lo = {x.lower(): d[x] for x in d}
    for k in keys:
        if k.lower() in lo: return lo[k.lower()]
    return None

def _normalize(payload) -> dict:
    """
    Normalises LLM output into:
    {
      "Exam_dates": { "Subject": "DD-MM-YYYY" },
      "Subjects": {
        "Subject": {
          "TopicName": {
            "status": "none",
            "subtopics": ["Subtopic 1", "Subtopic 2"]
          }
        }
      },
      "study_days": {}
    }
    """
    if not isinstance(payload, dict):
        return {'Exam_dates': {}, 'Subjects': {}, 'study_days': {}}

    raw_s = _find(payload, 'Subjects', 'subjects') or {}
    raw_d = _find(payload, 'Exam_dates', 'exam_dates', 'ExamDates', 'examDates') or {}
    ns, nd = {}, {}

    def _norm_topic(val) -> dict:
        if isinstance(val, dict):
            subs = val.get('subtopics') or []
            if not isinstance(subs, list):
                subs = []
            subs = [str(s).strip() for s in subs if str(s).strip()]
            return {'status': 'none', 'subtopics': subs}
        return {'status': 'none', 'subtopics': []}

    if isinstance(raw_s, list):
        for s in raw_s:
            if not isinstance(s, dict): continue
            name = str(s.get('subject') or s.get('name') or '').strip()
            if not name: continue
            topics_raw = s.get('topics') or {}
            if isinstance(topics_raw, list):
                topics = {str(t).strip(): {'status': 'none', 'subtopics': []}
                          for t in topics_raw if isinstance(t, str) and t.strip()}
            elif isinstance(topics_raw, dict):
                topics = {str(k).strip(): _norm_topic(v)
                          for k, v in topics_raw.items() if str(k).strip()}
            else:
                topics = {}
            if topics: ns[name] = topics
            ed = s.get('exam_date') or s.get('examDate')
            if ed and str(ed).strip() not in ('null', 'None', ''):
                nd[name] = str(ed).strip()
        if isinstance(raw_d, dict):
            for k, v in raw_d.items():
                if v and str(v).strip() not in ('null', 'None', ''):
                    nd[str(k).strip()] = str(v).strip()

    elif isinstance(raw_s, dict):
        for subj, topics_raw in raw_s.items():
            subj = str(subj).strip()
            if not subj: continue
            if isinstance(topics_raw, dict):
                norm_topics = {}
                for tname, tval in topics_raw.items():
                    tname = str(tname).strip()
                    if not tname: continue
                    norm_topics[tname] = _norm_topic(tval)
                if norm_topics: ns[subj] = norm_topics
        if isinstance(raw_d, dict):
            for subj, date in raw_d.items():
                if date and str(date).strip() not in ('null', 'None', ''):
                    nd[str(subj).strip()] = str(date).strip()

    return {'Exam_dates': nd, 'Subjects': ns, 'study_days': {}}

def _filter_dates(payload: dict) -> dict:
    subjects = payload.get('Subjects') or {}
    payload['Exam_dates'] = {s: d for s, d in (payload.get('Exam_dates') or {}).items()
                              if subjects.get(s)}
    return payload

def _ensure_study_days(payload: dict, today_str: str) -> dict:
    valid = []
    for d in (payload.get('Exam_dates') or {}).values():
        try: valid.append(_parse_date(d))
        except: pass
    payload['study_days'] = (
        _date_range(today_str, _format_date(max(valid))) if valid else {}
    )
    return payload
