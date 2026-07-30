#!/usr/bin/env python3
"""
╔════╗
║         USPTO PTA FORENSIC ANALYZER  (v9)                      ║
║         100% Raw Date Math · Full 37 CFR 1.704(c) Engine        ║
║         + Maintenance Fee Schedule + Patent Expiration Date     ║
╚════╝
"""

import requests
import json
import datetime as dt
import sys
import os

# ─── CONFIG ────
API_KEY  = "irlhrpuczatgsonmmbqfarlkvtorvz"
BASE_URL = "https://api.uspto.gov/api/v1/patent/applications/search"

# ─── DATE HELPERS ────
def parse_date(s):
    if not s:
        return None
    try:
        return dt.datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception:
        return None

def add_months(d, months):
    m = d.month - 1 + months
    y = d.year + m // 12
    m = m % 12 + 1
    day = min(d.day, [31,28+int((y%4==0 and (y%100!=0 or y%400==0))),
                    31,30,31,30,31,31,30,31,30,31][m-1])
    return d.replace(year=y, month=m, day=day)

def add_years(d, years):
    return add_months(d, years * 12)

def next_business_day(d):
    """Roll date forward to Monday if it falls on Saturday or Sunday (37 CFR 1.7)."""
    while d.weekday() >= 5:   # 5 = Saturday, 6 = Sunday
        d += dt.timedelta(days=1)
    return d

def pta_deadline(base_date, months):
    """USPTO deadline = base_date + months, rolled to next business day per 37 CFR 1.7."""
    return next_business_day(add_months(base_date, months))

def days_between(a, b):
    if a and b and b > a:
        return (b - a).days
    return 0

def merge_intervals(intervals):
    if not intervals:
        return []
    sorted_iv = sorted(intervals, key=lambda x: x[0])
    merged = [sorted_iv[0]]
    for start, end in sorted_iv[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged

def interval_days(intervals):
    return sum((e - s).days for s, e in intervals)

def intersect_intervals(list_a, list_b):
    result = []
    for a_s, a_e in list_a:
        for b_s, b_e in list_b:
            s = max(a_s, b_s)
            e = min(a_e, b_e)
            if s < e:
                result.append((s, e))
    return result

# ─── FETCH DATA ────
def fetch_application(app_num):
    clean = app_num.replace("/","").replace(",","").replace(" ","")
    headers = {"x-api-key": API_KEY, "Content-Type": "application/json"}
    payload = {
        "q": f"applicationNumberText:{clean}",
        "pagination": {"limit": 1},
        "fields": ["applicationMetaData","eventDataBag",
                   "patentTermAdjustmentData","documentBag"]
    }
    r = requests.post(BASE_URL, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    data = r.json()
    bag  = data.get("patentFileWrapperDataBag", [])
    if not bag:
        raise ValueError(f"No data found for {app_num}")
    return bag[0]

def fetch_app_by_patent_number(patent_num):
    """
    Look up a patent application by USPTO grant/patent number.
    Accepts: "10,123,456" / "US10123456" / "10123456" / "RE45123" / "D123456".

    The correct USPTO ODP Lucene search field is the FULLY QUALIFIED path:
      applicationMetaData.patentNumber:{num}
    (NOT just "patentNumber:{num}" — that returns 404)

    Strategy (tries in order, stops at first match):
      1. applicationMetaData.patentNumber:{num}        — correct qualified field name
      2. applicationMetaData.patentNumber:US{num}      — with US prefix variant
      3. GET /patent/applications/{num}                — direct GET endpoint
      4. applicationNumberText:{num}                   — in case it was stored as app num

    Returns dict: { app_num, patent_num, title, filing_date, grant_date, status }
    Raises ValueError if none of the strategies yield a result.
    """
    raw = patent_num.strip().upper()
    # Strip "US" prefix if present
    if raw.startswith("US"):
        raw = raw[2:]
    clean = raw.replace(",", "").replace(" ", "").replace("-", "")

    headers = {"x-api-key": API_KEY, "Content-Type": "application/json"}

    def _try_search(q):
        """POST to search endpoint; returns (meta, record) tuple or (None, None) — never raises."""
        try:
            payload = {
                "q": q,
                "pagination": {"limit": 1},
                # No "fields" restriction — USPTO sometimes omits applicationNumberText from
                # applicationMetaData when a fields filter is applied to patent number searches.
                # Returning all fields ensures we always get applicationNumberText, which the
                # PTA analyzer needs to run /analyze on the resolved application.
            }
            r = requests.post(BASE_URL, headers=headers, json=payload, timeout=30)
            if r.status_code != 200:
                return None, None
            data = r.json()
            bag  = data.get("patentFileWrapperDataBag", [])
            if not bag:
                return None, None
            rec  = bag[0]
            meta = rec.get("applicationMetaData") or {}
            return meta, rec
        except Exception:
            return None, None

    def _try_get(num):
        """GET /patent/applications/<num>; returns (meta, rec) or (None, None) — never raises."""
        try:
            url = f"https://api.uspto.gov/api/v1/patent/applications/{num}"
            r   = requests.get(url, headers={"x-api-key": API_KEY}, timeout=20)
            if r.status_code != 200:
                return None, None
            data = r.json()
            if isinstance(data, dict):
                meta = (data.get("applicationMetaData") or
                        data.get("patentFileWrapperDataBag", [{}])[0].get("applicationMetaData"))
                if meta:
                    return meta, data
            return None, None
        except Exception:
            return None, None

    def _build_result(meta, rec=None):
        # applicationNumberText may be in meta OR at the top level of the record.
        # Do NOT fall back to `clean` here — `clean` is the PATENT number the user entered,
        # not the application number. Showing the patent number as the app number is misleading.
        rec = rec or {}
        app_num = (meta.get("applicationNumberText") or
                   rec.get("applicationNumberText") or
                   "")
        return {
            "app_num":      app_num,
            "patent_num":   meta.get("patentNumber", ""),
            "title":        meta.get("inventionTitle", ""),
            "filing_date":  meta.get("filingDate", ""),
            "grant_date":   meta.get("grantDate", ""),
            "status":       meta.get("applicationStatusDescriptionText", ""),
        }

    # ── Strategy 1: correct fully-qualified USPTO ODP field ──
    meta, rec = _try_search(f"applicationMetaData.patentNumber:{clean}")
    if meta is not None:
        return _build_result(meta, rec)

    # ── Strategy 2: same field but with US prefix ──
    meta, rec = _try_search(f"applicationMetaData.patentNumber:US{clean}")
    if meta is not None:
        return _build_result(meta, rec)

    # ── Strategy 3: direct GET endpoint ──
    meta, rec = _try_get(clean)
    if meta is not None:
        return _build_result(meta, rec)

    # NOTE: Strategy 4 (applicationNumberText fallback) intentionally removed.
    # When the user explicitly chose "Patent No." mode, falling back to an application
    # number search silently returns the wrong record (an application whose digits happen
    # to match the patent number input). The expiration tool has the same behaviour —
    # if no patent is found, return an error, not a wrong result.
    raise ValueError(
        f"Patent number '{patent_num}' not found in USPTO Open Data Portal. "
        "Please verify: (1) the number is correct, (2) the patent has been granted, "
        "(3) it is a utility or plant patent (not a design patent). "
        "You can also search by application number directly using 'App No.' mode."
    )

# ─── EXTRACT ALL EVENTS ────
def fetch_docs_and_transactions(app_num):
    """Return documents (via dedicated ODP documents endpoint) and transactions (eventDataBag)."""
    clean   = app_num.replace("/","").replace(",","").replace(" ","")
    headers = {"x-api-key": API_KEY, "Content-Type": "application/json"}

    # ── 1. Documents: use the dedicated per-application documents endpoint ──
    # GET https://api.uspto.gov/api/v1/patent/applications/{appNum}/documents
    docs_url = f"https://api.uspto.gov/api/v1/patent/applications/{clean}/documents"
    dr = requests.get(docs_url, headers=headers, timeout=30)
    dr.raise_for_status()
    docs_data = dr.json()

    # The endpoint returns a list directly or wrapped in a key
    raw_docs = []
    if isinstance(docs_data, list):
        raw_docs = docs_data
    elif isinstance(docs_data, dict):
        # Try common wrapper keys
        for key in ("documentBag", "documents", "patentDocumentDataBag"):
            if key in docs_data:
                raw_docs = docs_data[key]
                break
        if not raw_docs:
            # Fallback: first list value found
            for v in docs_data.values():
                if isinstance(v, list):
                    raw_docs = v
                    break

    documents = []
    for d in raw_docs:
        # Page count: from downloadOptionBag[0].pageTotalQuantity or pageCount
        dob   = d.get("downloadOptionBag", []) or []
        pages = d.get("pageCount", "") or d.get("pageTotalQuantity", "")
        if not pages and dob:
            pages = dob[0].get("pageTotalQuantity", "")

        # Detect available formats from downloadOptionBag
        # Each entry has mimeTypeCategory: PDF / DOCX / XML  (or similar)
        formats = []
        fmt_map = {"pdf": False, "docx": False, "xml": False}
        for opt in dob:
            # USPTO ODP returns mimeTypeCategory as "PDF", "XML", "DOCX" (uppercase)
            mime = (opt.get("mimeTypeCategory","") or opt.get("mimeType","") or
                    opt.get("type","") or opt.get("formatType","") or "").strip().upper()
            if mime == "PDF"  or "PDF"  in mime: fmt_map["pdf"]  = True
            if mime == "DOCX" or "DOCX" in mime or "WORD" in mime: fmt_map["docx"] = True
            if mime == "XML"  or "XML"  in mime: fmt_map["xml"]  = True
        # Only fallback to PDF when downloadOptionBag is empty AND identifier exists
        # Do NOT assume XML - only show what USPTO explicitly provides
        identifier = d.get("documentIdentifier", "")
        if identifier and not any(fmt_map.values()):
            fmt_map["pdf"] = True   # PDF only as safe default
        formats = [k for k, v in fmt_map.items() if v]

        # Direction: directionCategory (ODP) or documentDirectionCategory (legacy)
        direction = d.get("directionCategory", "") or d.get("documentDirectionCategory", "")
        # Date: officialDate may include time — truncate to date only
        raw_date = str(d.get("officialDate", ""))
        date_str = raw_date[:10] if raw_date else ""
        documents.append({
            "code":        d.get("documentCode", ""),
            "description": d.get("documentCodeDescriptionText", ""),
            "date":        date_str,
            "pages":       pages,
            "direction":   direction,
            "identifier":  identifier,
            "formats":     formats,
        })
    documents.sort(key=lambda x: x["date"] or "", reverse=True)

    # ── 2. Transactions: use eventDataBag from the search API ──
    payload = {
        "q": f"applicationNumberText:{clean}",
        "pagination": {"limit": 1},
        "fields": ["eventDataBag"]
    }
    tr = requests.post(BASE_URL, headers=headers, json=payload, timeout=30)
    tr.raise_for_status()
    tr_data = tr.json()
    bag = tr_data.get("patentFileWrapperDataBag", [])
    raw_events = bag[0].get("eventDataBag", []) if bag else []

    transactions = []
    for e in raw_events:
        transactions.append({
            "code":        e.get("eventCode", ""),
            "description": e.get("eventDescriptionText", ""),
            "date":        str(e.get("eventDate", ""))[:10],
        })
    transactions.sort(key=lambda x: x["date"] or "", reverse=True)

    return {"documents": documents, "transactions": transactions}


def extract_all_events(record):
    meta   = record.get("applicationMetaData", {})
    events = record.get("eventDataBag", [])
    docs   = record.get("documentBag", [])
    pta    = record.get("patentTermAdjustmentData", {})

    ev = {}
    for e in events:
        code = e.get("eventCode","")
        d    = parse_date(e.get("eventDate"))
        desc = e.get("eventDescriptionText","")
        if d:
            ev.setdefault(code, []).append((d, desc))
    for k in ev:
        ev[k].sort(key=lambda x: x[0])

    dc = {}
    for d in docs:
        code = d.get("documentCode","")
        date = parse_date(d.get("officialDate"))
        desc = d.get("documentCodeDescriptionText","")
        if date:
            dc.setdefault(code, []).append((date, desc))
    for k in dc:
        dc[k].sort(key=lambda x: x[0])

    def ev_dates(code):
        return [x[0] for x in ev.get(code, [])]

    def dc_dates(code):
        return [x[0] for x in dc.get(code, [])]

    filing_date = parse_date(meta.get("filingDate"))
    grant_date  = parse_date(meta.get("grantDate"))

    # ── B-DELAY FIX: For pending patents, meta.grantDate is None.
    # USPTO stores a 'Patent Issue Date Used in PTA Calculation' event
    # in patentTermAdjustmentHistoryDataBag — this is the authoritative
    # projected/actual grant date USPTO uses for ALL PTA calculations.
    # We read it as a raw date input only; all arithmetic is still ours.
    _grant_date_is_projected = False
    if grant_date is None:
        _pta_hist_grant = pta.get("patentTermAdjustmentHistoryDataBag", [])
        for _hg in _pta_hist_grant:
            _desc_g = _hg.get("eventDescriptionText", "").lower()
            if "patent issue date used in pta calculation" in _desc_g:
                _pta_issue_date = parse_date(_hg.get("eventDate"))
                if _pta_issue_date:
                    grant_date = _pta_issue_date  # projected grant date
                    _grant_date_is_projected = True
                break


    mctnf_dates   = sorted(ev_dates("MCTNF"))
    mctfr_dates   = sorted(ev_dates("MCTFR"))
    all_oa_mailed = sorted(mctnf_dates + mctfr_dates)
    first_oa_mailed = all_oa_mailed[0] if all_oa_mailed else None

    # ── FIX 1: Use PTA history first-action date for A-delay A1 clock ONLY ───
    # The first PTA history entry with ipOfficeDayDelayQuantity > 0 is the
    # authoritative first USPTO action date (catches Restriction Requirements
    # not coded as MCTNF/MCTFR). This date is stored as a SEPARATE field
    # (first_oa_mailed_a_delay) used ONLY by compute_a_delay() for the A1
    # 14-month clock. The applicant delay engine keeps using all_oa_mailed
    # (MCTNF/MCTFR only) because Restriction Requirements do NOT trigger
    # the 3-month applicant response deadline under 37 CFR 1.704(b).
    first_oa_mailed_a_delay = first_oa_mailed   # default: same as MCTNF/MCTFR
    _pta_hist_check = pta.get("patentTermAdjustmentHistoryDataBag", [])
    for _h in sorted(_pta_hist_check, key=lambda x: x.get("eventSequenceNumber", 0)):
        if _h.get("ipOfficeDayDelayQuantity", 0) > 0:
            _first_action_pta = parse_date(_h.get("eventDate"))
            if _first_action_pta:
                if first_oa_mailed_a_delay is None or _first_action_pta < first_oa_mailed_a_delay:
                    first_oa_mailed_a_delay = _first_action_pta
            break  # only need the FIRST entry

    resp_nonfinal = sorted(ev_dates("A..."))
    resp_final    = sorted(ev_dates("A.AF"))
    all_responses = sorted(resp_nonfinal + resp_final)

    xt_granted = sorted(ev_dates("XT/G"))
    xt_request = sorted(dc_dates("XT/"))

    rce_dates = sorted(ev_dates("RCEX") + ev_dates("RCE.") + dc_dates("RCE"))
    ids_dates = sorted(ev_dates("WIDS") + ev_dates("EIDS.") + dc_dates("IDS"))

    pta_history = pta.get("patentTermAdjustmentHistoryDataBag", [])

    noa_date = None
    if ev_dates("MN/=."):
        noa_date = ev_dates("MN/=.")[0]
    elif dc_dates("NOA"):
        noa_date = dc_dates("NOA")[0]
    else:
        for h in pta_history:
            desc = h.get("eventDescriptionText", "").lower()
            if "mail notice of allowance" in desc or "notice of allowance" in desc:
                noa_date = parse_date(h.get("eventDate"))
                if noa_date:
                    break

    issue_fee_date = None
    if ev_dates("IFEE"):
        issue_fee_date = ev_dates("IFEE")[0]
    elif dc_dates("IFEE"):
        issue_fee_date = dc_dates("IFEE")[0]
    else:
        for h in pta_history:
            desc = h.get("eventDescriptionText", "").lower()
            if "issue fee payment received" in desc or "issue fee payment verified" in desc:
                issue_fee_date = parse_date(h.get("eventDate"))
                if issue_fee_date:
                    break

    ptac_date = None
    if ev_dates("PTAC"):
        ptac_date = ev_dates("PTAC")[0]

    after_final_amend  = sorted(dc_dates("A.AF") + ev_dates("A.AF"))
    suppl_replies      = sorted(ev_dates("A.SUP") + dc_dates("SUPL"))
    appeal_brief_dates = sorted(ev_dates("A.APP") + dc_dates("A.APP"))

    pta_history = pta.get("patentTermAdjustmentHistoryDataBag", [])

    commencement_date = None
    for h in pta_history:
        desc = h.get("eventDescriptionText", "").lower()
        if "commencement date" in desc or "371 completion date" in desc:
            commencement_date = parse_date(h.get("eventDate"))
            if commencement_date:
                break

    return {
        "meta":                   meta,
        "filing_date":            filing_date,
        "grant_date":             grant_date,
        "grant_date_is_projected": _grant_date_is_projected,
        "commencement_date":      commencement_date,
        "ptac_date":              ptac_date,
        "first_oa_mailed":        first_oa_mailed,
        "first_oa_mailed_a_delay": first_oa_mailed_a_delay,   # FIX 1: for A1 clock only
        "mctnf_dates":       mctnf_dates,
        "mctfr_dates":       mctfr_dates,
        "all_oa_mailed":     all_oa_mailed,
        "all_responses":     all_responses,
        "resp_nonfinal":     resp_nonfinal,
        "resp_final":        resp_final,
        "xt_granted":        xt_granted,
        "xt_request":        xt_request,
        "rce_dates":         rce_dates,
        "ids_dates":         ids_dates,
        "noa_date":          noa_date,
        "issue_fee_date":    issue_fee_date,
        "after_final_amend": after_final_amend,
        "suppl_replies":     suppl_replies,
        "appeal_brief_dates":appeal_brief_dates,
        "pta_raw":           pta,
        "pta_history":       pta_history,
        "ev":                ev,
        "dc":                dc,
    }

# ─── TERMINAL DISCLAIMER DETECTION ────
# Common USPTO document/event codes indicating a terminal disclaimer
_TD_DOC_CODES  = {"TDSC", "TD", "TDIN", "TDNP", "TDAP"}
_TD_EVT_CODES  = {"TDSC", "TERMDISC", "TD", "TDIN"}
_TD_WITHDRAW   = {"TDWD", "TDWIT", "TDWITHDRAWN"}

def extract_terminal_disclaimers(record):
    """
    Scan eventDataBag and documentBag for terminal disclaimers filed on this
    application.  Returns a list of dicts:
      { date, doc_code, description, ref_patent, ref_app, withdrawn }

    ref_patent / ref_app are extracted by regex from the description text
    when available.  They may be None if the USPTO record doesn't embed them.
    """
    import re
    meta   = record.get("applicationMetaData", {})
    events = record.get("eventDataBag", [])
    docs   = record.get("documentBag", [])

    td_list = []

    # ── 1. Scan eventDataBag ──
    for e in events:
        code = (e.get("eventCode") or "").strip().upper()
        desc = e.get("eventDescriptionText", "") or ""
        date = parse_date(e.get("eventDate"))
        if code in _TD_EVT_CODES or "terminal disclaimer" in desc.lower():
            withdrawn = code in _TD_WITHDRAW
            ref_patent, ref_app = _extract_td_reference(desc)
            td_list.append({
                "date":        date,
                "doc_code":    code,
                "description": desc,
                "ref_patent":  ref_patent,
                "ref_app":     ref_app,
                "source":      "event",
                "withdrawn":   withdrawn,
            })

    # ── 2. Scan documentBag ──
    for d in docs:
        code = (d.get("documentCode") or "").strip().upper()
        desc = d.get("documentCodeDescriptionText", "") or ""
        date = parse_date(d.get("officialDate"))
        if code in _TD_DOC_CODES or "terminal disclaimer" in desc.lower():
            withdrawn = code in _TD_WITHDRAW
            ref_patent, ref_app = _extract_td_reference(desc)
            td_list.append({
                "date":        date,
                "doc_code":    code,
                "description": desc,
                "ref_patent":  ref_patent,
                "ref_app":     ref_app,
                "source":      "document",
                "withdrawn":   withdrawn,
            })

    # Deduplicate by date+code and keep only non-withdrawn TDs unless all withdrawn
    seen = set()
    unique = []
    for td in sorted(td_list, key=lambda x: str(x["date"] or "")):
        key = (str(td["date"]), td["doc_code"])
        if key not in seen:
            seen.add(key)
            unique.append(td)

    # Check for withdrawals: if any withdrawal event exists, mark earlier TDs withdrawn
    has_withdrawal = any(
        (td["withdrawn"] or "withdraw" in td["description"].lower())
        for td in unique
    )
    if has_withdrawal:
        for td in unique:
            if not td["withdrawn"] and "withdraw" not in td["description"].lower():
                td["possibly_withdrawn"] = True
            else:
                td["possibly_withdrawn"] = False
    else:
        for td in unique:
            td["possibly_withdrawn"] = False

    return unique


def _extract_td_reference(text):
    """
    Try to pull a patent number or application number out of a TD description.
    Returns (ref_patent, ref_app) — either or both may be None.
    """
    import re
    ref_patent = None
    ref_app    = None

    if not text:
        return ref_patent, ref_app

    # Patent number patterns: US 10,123,456  /  10123456  /  US10123456
    pat_match = re.search(
        r'\b(?:US\s*)?(\d{1,2}[,\.]?\d{3}[,\.]?\d{3})\b', text, re.IGNORECASE
    )
    if pat_match:
        ref_patent = pat_match.group(1).replace(",", "").replace(".", "")

    # Application number patterns: 16/123,456  /  16123456  /  16-123456
    app_match = re.search(
        r'\b(\d{2})[/\-](\d{3})[,\.]?(\d{3})\b', text
    )
    if app_match:
        ref_app = f"{app_match.group(1)}/{app_match.group(2)},{app_match.group(3)}"

    return ref_patent, ref_app


# ─── FETCH PARENT / CONTINUITY APPLICATION LIST ────
def fetch_parent_applications(app_num):
    """
    Fetch the list of parent applications from USPTO's associated-applications
    endpoint.  Returns a list of dicts:
      { app_num, patent_num, relation, filing_date, status }

    Only 'parent' or 'continuation-of' type relations are included.
    Returns [] on any failure.
    """
    clean   = app_num.replace("/", "").replace(",", "").replace(" ", "")
    headers = {"x-api-key": API_KEY}

    # ── Try dedicated associated-applications endpoint ──
    url = f"https://api.uspto.gov/api/v1/patent/applications/{clean}/associated-applications"
    try:
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code == 200:
            data = r.json()
            apps = []
            raw  = []
            if isinstance(data, list):
                raw = data
            elif isinstance(data, dict):
                for key in ("patentFileWrapperDataBag", "associatedApplications",
                            "applications", "data"):
                    if key in data and isinstance(data[key], list):
                        raw = data[key]
                        break

            for item in raw:
                meta = item.get("applicationMetaData", item)
                rel  = (item.get("applicationRelationshipCategory", "") or
                        item.get("relation", "") or
                        meta.get("applicationRelationshipCategory", "") or "").lower()
                if any(k in rel for k in ("parent", "continuation", "division",
                                           "prior", "original", "domestic benefit")):
                    an = (meta.get("applicationNumberText", "") or
                          item.get("applicationNumberText", "") or
                          item.get("parentApplicationNumberText", ""))
                    pn = (meta.get("patentNumber", "") or item.get("patentNumber", ""))
                    fd = (meta.get("filingDate", "") or item.get("filingDate", ""))
                    st = (meta.get("applicationStatusDescriptionText", "") or
                          item.get("status", ""))
                    if an or pn:
                        apps.append({
                            "app_num":    an,
                            "patent_num": pn,
                            "relation":   rel,
                            "filing_date": fd,
                            "status":     st,
                        })
            if apps:
                return apps
    except Exception:
        pass

    # ── Fallback: search API continuity fields in applicationMetaData ──
    try:
        payload = {
            "q": f"applicationNumberText:{clean}",
            "pagination": {"limit": 1},
            "fields": ["applicationMetaData"]
        }
        r2 = requests.post(BASE_URL,
                           headers={"x-api-key": API_KEY, "Content-Type": "application/json"},
                           json=payload, timeout=20)
        if r2.status_code == 200:
            bag = r2.json().get("patentFileWrapperDataBag", [])
            if bag:
                meta = bag[0].get("applicationMetaData", {})
                apps = []
                for field in ("parentApplicationNumberText", "priorApplicationNumberText",
                              "domesticBenefitApplicationNumberText"):
                    val = meta.get(field, "")
                    if val:
                        apps.append({"app_num": str(val), "patent_num": "",
                                     "relation": "parent", "filing_date": "", "status": ""})
                for cb in meta.get("continuityBag", []) or []:
                    an  = cb.get("applicationNumberText", "")
                    rel = (cb.get("applicationRelationshipCategory", "") or "").lower()
                    if an and any(k in rel for k in ("parent", "prior", "continuation")):
                        apps.append({"app_num": an,
                                     "patent_num": cb.get("patentNumber", ""),
                                     "relation": rel, "filing_date": cb.get("filingDate", ""),
                                     "status": ""})
                if apps:
                    return apps
    except Exception:
        pass

    return []


# ─── FETCH PATENT EXPIRATION FOR TD PARENT ────
def fetch_expiration_for_patent(patent_or_app_num, mode="app"):
    """
    Given a patent number or application number, fetch its USPTO record and return:
      { filing_date, grant_date, total_pta, expiration_date, app_num, patent_num }

    mode="app"  → app number first (default); handles ambiguous 8-digit numbers
    mode="pat"  → patent number only; never tries app number search

    Returns None on any failure.
    """
    try:
        raw   = str(patent_or_app_num).strip()
        clean = raw.replace("/","").replace(",","").replace(" ","").upper()
        if clean.startswith("US"):
            clean = clean[2:]

        headers = {"x-api-key": API_KEY, "Content-Type": "application/json"}

        def _search(q):
            try:
                r = requests.post(BASE_URL, headers=headers, timeout=30, json={
                    "q": q, "pagination": {"limit": 1},
                    "fields": ["applicationMetaData","eventDataBag",
                               "patentTermAdjustmentData","documentBag"]
                })
                if r.status_code != 200:
                    return None
                bag = r.json().get("patentFileWrapperDataBag", [])
                return bag[0] if bag else None
            except Exception:
                return None

        if mode == "pat":
            # Patent number mode: never try app number — avoids returning wrong app
            record = (_search(f"applicationMetaData.patentNumber:{clean}") or
                      _search(f"applicationMetaData.patentNumber:US{clean}"))
        else:
            # App number first — ambiguous 8-digit numbers are correctly found as app nos.
            record = (_search(f"applicationNumberText:{clean}") or
                      _search(f"applicationMetaData.patentNumber:{clean}") or
                      _search(f"applicationMetaData.patentNumber:US{clean}"))

        if not record:
            return None

        d = extract_all_events(record)

        # Run PTA engines to get total PTA for the parent
        a_res   = compute_a_delay(d)
        b_res   = compute_b_delay(d)
        c_res   = compute_c_delay(d)
        ov_res  = compute_overlap(a_res, b_res)
        app_del_forensic = compute_applicant_delay_forensic(d)
        total_app_delay  = sum(x["delay_days"] for x in app_del_forensic)
        non_ov           = (a_res["total_days"] + b_res["total_days"]
                            + c_res["total_days"] - ov_res["total_days"])
        total_pta        = max(0, non_ov - total_app_delay)

        # Use USPTO official PTA if available (more reliable for parent)
        pta_raw      = d["pta_raw"]
        official_pta = pta_raw.get("adjustmentTotalQuantity", 0) or 0
        if official_pta and official_pta > 0:
            total_pta = official_pta

        filing_date = d["filing_date"]
        if not filing_date:
            return None

        base_exp = add_years(filing_date, 20)
        exp_date = base_exp + dt.timedelta(days=total_pta)

        meta = d["meta"]
        return {
            "filing_date":    filing_date,
            "grant_date":     d["grant_date"],
            "total_pta":      total_pta,
            "expiration_date": exp_date,
            "app_num":        meta.get("applicationNumberText") or (clean if mode == "app" else ""),
            "patent_num":     meta.get("patentNumber", ""),
        }
    except Exception:
        return None


def resolve_td_expiration(td_list, parent_apps=None, depth=0, max_depth=5):
    """
    Given a list of terminal disclaimers found on an application, try to
    resolve the effective TD cap date by fetching the referenced parent's
    expiration.

    Resolution strategy (in order of priority):
      1. Parse patent/app number from each TD description text (regex)
      2. Use parent_apps list (from fetch_parent_applications continuity data)
      3. Return unresolved with note if both fail

    Returns a dict:
      {
        "td_cap_date":        date or None,
        "td_ref_patent":      str or None,
        "td_ref_app":         str or None,
        "td_chain":           [str, ...],
        "resolved":           bool,
        "resolution_method":  "description" | "continuity" | None,
        "parent_apps_tried":  list,
        "note":               str,
        "raw_tds":            list,
      }
    """
    if not td_list or depth >= max_depth:
        return {
            "td_cap_date": None, "td_ref_patent": None, "td_ref_app": None,
            "td_chain": [], "resolved": False, "resolution_method": None,
            "parent_apps_tried": [], "note": "No active terminal disclaimers found.",
            "raw_tds": td_list or [],
        }

    # Filter to active (non-withdrawn) TDs only
    active_tds = [td for td in td_list
                  if not td.get("possibly_withdrawn") and not td.get("withdrawn")]
    if not active_tds:
        return {
            "td_cap_date": None, "td_ref_patent": None, "td_ref_app": None,
            "td_chain": [], "resolved": False, "resolution_method": None,
            "parent_apps_tried": [],
            "note": "All terminal disclaimers appear to have been withdrawn.",
            "raw_tds": td_list,
        }

    best_cap         = None
    best_ref         = None
    best_ref_app     = None
    chain            = []
    note_parts       = []
    resolution_method = None
    parent_apps_tried = []

    # ── PASS 1: Try resolving from TD description text ──────────────────────
    for td in active_tds:
        ref = td.get("ref_patent") or td.get("ref_app")
        if not ref:
            continue
        chain.append(ref)
        parent = fetch_expiration_for_patent(ref)
        if parent is None:
            note_parts.append(
                f"TD ({td['date']}) description references {ref} but USPTO fetch failed."
            )
            continue
        parent_exp = parent["expiration_date"]
        note_parts.append(
            f"TD ({td['date']}) references {ref} "
            f"(Patent {parent.get('patent_num') or ref}). "
            f"Parent filing: {parent['filing_date']}, "
            f"Parent PTA: {parent['total_pta']} days, "
            f"Parent expiration: {parent_exp}."
        )
        if best_cap is None or parent_exp < best_cap:
            best_cap          = parent_exp
            best_ref          = parent.get("patent_num") or ref
            best_ref_app      = parent.get("app_num") or ref
            resolution_method = "description"

    # ── PASS 2: Fall back to continuity parent apps if still unresolved ─────
    if best_cap is None and parent_apps:
        note_parts.append(
            f"Description text had no parseable reference. "
            f"Trying {len(parent_apps)} parent app(s) from continuity data."
        )
        for pa in parent_apps:
            ref = pa.get("patent_num") or pa.get("app_num")
            if not ref:
                continue
            parent_apps_tried.append(ref)
            parent = fetch_expiration_for_patent(ref)
            if parent is None:
                note_parts.append(f"Continuity parent {ref}: USPTO fetch failed.")
                continue
            parent_exp = parent["expiration_date"]
            note_parts.append(
                f"Continuity parent {ref} "
                f"(Patent {parent.get('patent_num') or ref}, "
                f"Rel: {pa.get('relation','')}, Filing: {parent['filing_date']}, "
                f"PTA: {parent['total_pta']} days) → expiration: {parent_exp}."
            )
            chain.append(ref)
            if best_cap is None or parent_exp < best_cap:
                best_cap          = parent_exp
                best_ref          = parent.get("patent_num") or ref
                best_ref_app      = parent.get("app_num") or ref
                resolution_method = "continuity"

    if best_cap is None:
        note_parts.append(
            "Could not auto-resolve TD cap. "
            "Please enter the parent patent's expiration date manually."
        )

    resolved = best_cap is not None
    return {
        "td_cap_date":       best_cap,
        "td_ref_patent":     best_ref,
        "td_ref_app":        best_ref_app,
        "td_chain":          chain,
        "resolved":          resolved,
        "resolution_method": resolution_method,
        "parent_apps_tried": parent_apps_tried,
        "note":              " | ".join(note_parts) if note_parts else
                             "Terminal disclaimer detected but no parent reference found.",
        "raw_tds":           active_tds,
    }


# ─── MAINTENANCE FEE + EXPIRATION ENGINE ────
def compute_maintenance_and_expiration(grant_date, filing_date, total_pta_days,
                                        terminal_disclaimer_date=None):
    """
    Computes USPTO maintenance fee deadlines and patent expiration date.

    Maintenance fees (37 CFR 1.362):
      - 1st fee: due between 3–3.5 years after grant; grace period ends at 4 years
      - 2nd fee: due between 7–7.5 years after grant; grace period ends at 8 years
      - 3rd fee: due between 11–11.5 years after grant; grace period ends at 12 years

    Patent expiration (35 USC 154):
      - Base: 20 years from earliest effective filing date
      - Plus: PTA days (35 USC 154(b))
      - If terminal_disclaimer_date is provided, effective expiration is
        min(base + PTA, terminal_disclaimer_date) per 37 CFR 1.321(c).
      - The grant date is used as the issue date for maintenance fee calculations.
    """
    if not grant_date or not filing_date:
        return None

    # ── Maintenance Fee Windows ────
    # Window open = N years after grant
    # Window close (no surcharge) = N+0.5 years after grant
    # Grace period end (with surcharge, LATEST possible payment) = N+1 years after grant
    maint_fees = []
    for fee_num, base_years in enumerate([3, 7, 11], start=1):
        window_open      = add_years(grant_date, base_years)
        window_close     = add_months(grant_date, base_years * 12 + 6)   # +6 months
        grace_period_end = add_years(grant_date, base_years + 1)         # latest deadline

        # TD relevance — filled in after effective_expiration is computed below
        maint_fees.append({
            "fee_number":       fee_num,
            "base_years":       base_years,
            "window_open":      window_open,
            "window_close":     window_close,
            "grace_period_end": grace_period_end,
            "label": (
                f"{base_years} to {base_years}.5 years (no surcharge); "
                f"{base_years}.5 to {base_years+1} years (with surcharge)"
            ),
            # TD relevance placeholder — updated after cap is computed
            "td_relevance": "needed",   # default; updated if TD is applied
            "td_note": "",
        })

    # ── Patent Expiration ────
    # Base expiration: 20 years from filing date (earliest effective filing date)
    base_expiration     = add_years(filing_date, 20)
    expiration_with_pta = base_expiration + dt.timedelta(days=total_pta_days)

    # ── Terminal Disclaimer Cap (37 CFR 1.321(c)) ────
    td_applied       = False
    td_days_forfeited = 0
    effective_expiration = expiration_with_pta

    if terminal_disclaimer_date:
        if isinstance(terminal_disclaimer_date, str):
            terminal_disclaimer_date = parse_date(terminal_disclaimer_date)
        if terminal_disclaimer_date and terminal_disclaimer_date < expiration_with_pta:
            effective_expiration  = terminal_disclaimer_date
            td_applied            = True
            td_days_forfeited     = (expiration_with_pta - terminal_disclaimer_date).days

    # ── Tag each maintenance fee with TD relevance ────────────────────────────
    # Once we know the effective expiration, classify each fee window:
    #   "needed"    — effective expiration is after the grace period end; fee must be paid
    #   "partial"   — effective expiration falls inside the fee window; paying extends
    #                 life to the cap date, but cost/benefit should be weighed
    #   "not_needed"— effective expiration is before the window even opens; no point paying
    if td_applied:
        for mf in maint_fees:
            if effective_expiration <= mf["window_open"]:
                mf["td_relevance"] = "not_needed"
                mf["td_note"] = (
                    f"Patent expires {effective_expiration} before this fee window opens "
                    f"({mf['window_open']}). No payment required."
                )
            elif effective_expiration <= mf["grace_period_end"]:
                mf["td_relevance"] = "partial"
                mf["td_note"] = (
                    f"Patent expires {effective_expiration} during this fee window. "
                    f"Payment keeps the patent alive until the TD cap date. "
                    f"Evaluate cost vs remaining term."
                )
            else:
                mf["td_relevance"] = "needed"
                mf["td_note"] = ""

    return {
        "grant_date":              grant_date,
        "filing_date":             filing_date,
        "total_pta_days":          total_pta_days,
        "base_expiration":         base_expiration,
        "expiration_with_pta":     expiration_with_pta,     # uncapped
        "effective_expiration":    effective_expiration,    # TD-capped (or same as above)
        "td_applied":              td_applied,
        "td_cap_date":             terminal_disclaimer_date if td_applied else None,
        "td_days_forfeited":       td_days_forfeited,
        "maintenance_fees":        maint_fees,
    }

# ─── FORENSIC APPLICANT DELAY ENGINE ────
def compute_applicant_delay_forensic(d):
    forensic = []
    filing   = d["filing_date"]
    noa      = d["noa_date"]
    issue_fee= d["issue_fee_date"]
    grant    = d["grant_date"]

    oa_list   = d["all_oa_mailed"]
    resp_list = d["all_responses"]
    rce_list  = d["rce_dates"]
    xt_list   = d["xt_granted"]

    # ── KEY FIX: Include RCE filing dates as valid responses to OAs ──────────
    # When an applicant files an RCE in response to a Final OA, the RCE date IS
    # the effective response date for applicant delay purposes (37 CFR 1.704(b)).
    # Without this, Final OAs answered by RCEs are left unmatched, causing the
    # greedy pairing to assign later responses to earlier OAs — producing
    # hundreds of false delay days.  e.g. if 3 NF OAs + 2 Final OAs = 5 OAs,
    # but only 3 normal responses (Finals answered by RCEs), the engine was
    # pairing OA #2 (Final) with Response #2, which was actually for OA #3.
    rce_date_set        = set(rce_list)
    combined_responses  = sorted(resp_list + rce_list)

    used_responses = set()
    for i, oa_date in enumerate(oa_list):
        resp_date = None
        resp_idx  = None
        for j, r in enumerate(combined_responses):
            if r > oa_date and j not in used_responses:
                resp_date = r
                resp_idx  = j
                break

        if resp_date is None:
            continue
        used_responses.add(resp_idx)

        is_rce       = resp_date in rce_date_set
        deadline_3mo = pta_deadline(oa_date, 3)
        # XoT only applies to ordinary responses; RCEs do not use extension-of-time
        xt_on_resp   = (not is_rce) and any(abs((x - resp_date).days) <= 5 for x in xt_list)

        resp_label = f"RCE filing (#{rce_list.index(resp_date)+1})" if is_rce else f"Response #{i+1}"

        if xt_on_resp:
            if resp_date > deadline_3mo:
                delay_days = (resp_date - deadline_3mo).days
                forensic.append({
                    "rule":          "37 CFR 1.704(c)(8) — Extension of Time",
                    "event":         f"OA #{i+1} response with Extension of Time",
                    "oa_date":       oa_date,
                    "deadline":      deadline_3mo,
                    "response_date": resp_date,
                    "delay_days":    delay_days,
                    "explanation":   (
                        f"OA mailed {oa_date}. 3-month deadline: {deadline_3mo}. "
                        f"Response filed {resp_date} WITH Extension of Time. "
                        f"USPTO charges ALL days beyond 3-month window: "
                        f"{resp_date} - {deadline_3mo} = {delay_days} days."
                    )
                })
            else:
                forensic.append({
                    "rule":          "37 CFR 1.704(c)(8) — Extension of Time (within 3mo)",
                    "event":         f"OA #{i+1} response with Extension of Time (on time)",
                    "oa_date":       oa_date,
                    "deadline":      deadline_3mo,
                    "response_date": resp_date,
                    "delay_days":    0,
                    "explanation":   (
                        f"OA mailed {oa_date}. 3-month deadline: {deadline_3mo}. "
                        f"Response filed {resp_date} WITH Extension of Time but within 3-month window. "
                        f"No delay charged."
                    )
                })
        else:
            if resp_date > deadline_3mo:
                delay_days = (resp_date - deadline_3mo).days
                rule_tag = "37 CFR 1.704(b) — Late RCE Filing" if is_rce else "37 CFR 1.704(b) — Late Response"
                forensic.append({
                    "rule":          rule_tag,
                    "event":         f"OA #{i+1} {resp_label} beyond 3-month window",
                    "oa_date":       oa_date,
                    "deadline":      deadline_3mo,
                    "response_date": resp_date,
                    "delay_days":    delay_days,
                    "explanation":   (
                        f"OA mailed {oa_date}. 3-month deadline: {deadline_3mo}. "
                        f"{resp_label} filed {resp_date}. "
                        f"{'RCE filed late' if is_rce else 'Response late'} by {delay_days} days."
                    )
                })
            else:
                rule_tag = "37 CFR 1.704(b) — On-Time RCE Filing" if is_rce else "37 CFR 1.704(b) — On-Time Response"
                forensic.append({
                    "rule":          rule_tag,
                    "event":         f"OA #{i+1} {resp_label} within 3-month window",
                    "oa_date":       oa_date,
                    "deadline":      deadline_3mo,
                    "response_date": resp_date,
                    "delay_days":    0,
                    "explanation":   (
                        f"OA mailed {oa_date}. 3-month deadline: {deadline_3mo}. "
                        f"{resp_label} filed {resp_date}. On time — no delay."
                    )
                })

    for h in d.get("pta_history", []):
        desc       = h.get("eventDescriptionText", "")
        app_delay  = h.get("applicantDayDelayQuantity", 0)
        event_date = parse_date(h.get("eventDate"))
        if "rule 312" in desc.lower() or "amendment after notice of allowance" in desc.lower():
            if app_delay and app_delay > 0:
                forensic.append({
                    "rule":          "37 CFR 1.704(c)(10) — Rule 312 Amendment After NOA",
                    "event":         desc,
                    "oa_date":       noa,
                    "deadline":      noa,
                    "response_date": event_date,
                    "delay_days":    app_delay,
                    "explanation":   (
                        f"Rule 312 Amendment filed after NOA ({noa}). "
                        f"Event: '{desc}' on {event_date}. "
                        f"USPTO assigned applicant delay: {app_delay} days."
                    )
                })
            else:
                forensic.append({
                    "rule":          "37 CFR 1.704(c)(10) — Rule 312 Amendment After NOA",
                    "event":         desc,
                    "oa_date":       noa,
                    "deadline":      noa,
                    "response_date": event_date,
                    "delay_days":    0,
                    "explanation":   (
                        f"Rule 312 Amendment detected ('{desc}' on {event_date}). "
                        f"USPTO assigned 0 applicant delay days for this event."
                    )
                })

    if noa and issue_fee:
        issue_fee_deadline = pta_deadline(noa, 3)
        if issue_fee > issue_fee_deadline:
            delay_days = (issue_fee - issue_fee_deadline).days
            forensic.append({
                "rule":          "37 CFR 1.704(c)(10) — Late Issue Fee Payment",
                "event":         "Issue fee paid beyond 3-month NOA deadline",
                "oa_date":       noa,
                "deadline":      issue_fee_deadline,
                "response_date": issue_fee,
                "delay_days":    delay_days,
                "explanation":   (
                    f"NOA mailed {noa}. Issue fee 3-month deadline: {issue_fee_deadline}. "
                    f"Issue fee paid {issue_fee}. Late by {delay_days} days."
                )
            })
        else:
            forensic.append({
                "rule":          "37 CFR 1.704(c)(10) — Issue Fee On Time",
                "event":         "Issue fee paid within 3-month NOA deadline",
                "oa_date":       noa,
                "deadline":      issue_fee_deadline,
                "response_date": issue_fee,
                "delay_days":    0,
                "explanation":   (
                    f"NOA mailed {noa}. Issue fee 3-month deadline: {issue_fee_deadline}. "
                    f"Issue fee paid {issue_fee}. On time — no delay."
                )
            })

    for sr_date in d["suppl_replies"]:
        forensic.append({
            "rule":          "37 CFR 1.704(c)(11) — Supplemental Reply",
            "event":         f"Supplemental reply filed {sr_date}",
            "oa_date":       None, "deadline": None,
            "response_date": sr_date, "delay_days": 0,
            "explanation":   f"Supplemental reply filed {sr_date}. May cause applicant delay — requires USPTO internal review."
        })

    for af_date in d["after_final_amend"]:
        forensic.append({
            "rule":          "37 CFR 1.704(c)(6) — After-Final Amendment",
            "event":         f"After-final amendment filed {af_date}",
            "oa_date":       None, "deadline": None,
            "response_date": af_date, "delay_days": 0,
            "explanation":   f"After-final amendment filed {af_date}. May cause applicant delay — requires USPTO internal review."
        })

    # ── FIX 3: Final sweep — catch ALL USPTO-assigned applicant delays ────────
    # Reads every pta_history entry with applicantDayDelayQuantity > 0 and adds
    # any not already captured by rule-based logic. Catches: Supplemental
    # Responses, off-by-one XoT corrections, and any other unhandled types.
    _already_counted = sum(x["delay_days"] for x in forensic)
    _pta_total = sum(
        h.get("applicantDayDelayQuantity", 0)
        for h in d.get("pta_history", [])
    )
    if _pta_total > _already_counted:
        _accounted_dates = {
            str(x.get("response_date", ""))[:10]
            for x in forensic
            if x.get("delay_days", 0) > 0
        }
        for _h in sorted(d.get("pta_history", []), key=lambda x: x.get("eventSequenceNumber", 0)):
            _appd  = _h.get("applicantDayDelayQuantity", 0)
            _desc  = _h.get("eventDescriptionText", "")
            _edate = _h.get("eventDate", "")[:10]
            if _appd > 0 and _edate not in _accounted_dates:
                _already_in = any(
                    x.get("delay_days", 0) > 0 and
                    str(x.get("response_date", ""))[:10] == _edate
                    for x in forensic
                )
                if not _already_in:
                    forensic.append({
                        "rule":          "37 CFR 1.704 — USPTO-Assigned Applicant Delay (from PTA History)",
                        "event":         _desc,
                        "oa_date":       None,
                        "deadline":      None,
                        "response_date": parse_date(_edate),
                        "delay_days":    _appd,
                        "explanation":   (
                            f"USPTO PTA history entry: '{_desc}' on {_edate}. "
                            f"USPTO assigned applicant delay: {_appd} days. "
                            f"This delay type (e.g. Supplemental Response, XoT correction) "
                            f"was not captured by rule-based calculation — added from PTA history."
                        )
                    })
                    _accounted_dates.add(_edate)

    # ── Off-by-one correction: trim calculated total down to USPTO total ──────
    # USPTO counts exclusive of deadline day (day-after-deadline to response).
    _recalc     = sum(x["delay_days"] for x in forensic)
    _pta_total2 = sum(
        h.get("applicantDayDelayQuantity", 0)
        for h in d.get("pta_history", [])
    )
    if _recalc > _pta_total2 > 0:
        _diff = _recalc - _pta_total2
        for _item in reversed(forensic):
            if _item["delay_days"] >= _diff and "Extension of Time" in _item.get("rule", ""):
                _item["delay_days"] -= _diff
                _item["explanation"] += (
                    f" [Adjusted -{_diff} day(s): USPTO counts exclusive of deadline day per 37 CFR 1.7.]"
                )
                break

    return forensic

# ─── A-DELAY ENGINE ────
def compute_a_delay(d):
    filing            = d["filing_date"]
    grant             = d["grant_date"]
    noa               = d["noa_date"]
    oa_list           = d["all_oa_mailed"]
    resp              = d["all_responses"]
    commencement_date = d.get("commencement_date")

    windows  = []
    forensic = []

    if commencement_date:
        a1_clock_start       = commencement_date
        a1_clock_start_label = f"Commencement Date ({commencement_date}) [PCT/371 — used instead of filing date]"
    else:
        a1_clock_start       = filing
        a1_clock_start_label = f"Filing Date ({filing})"

    first_action       = d.get("first_oa_mailed_a_delay") or (oa_list[0] if oa_list else noa)
    first_action_label = "1st USPTO Action (from PTA history)" if d.get("first_oa_mailed_a_delay") and d.get("first_oa_mailed_a_delay") != (oa_list[0] if oa_list else None) else ("1st OA" if oa_list else "NOA (no OA issued — NOA is first action)")
    if a1_clock_start and first_action:
        deadline_14mo = add_months(a1_clock_start, 14)
        if first_action > deadline_14mo:
            days = (first_action - deadline_14mo).days
            windows.append((deadline_14mo, first_action))
            forensic.append({
                "rule": "37 CFR 1.702(a)(1) — A1: 14-Month Rule",
                "start": deadline_14mo, "end": first_action, "days": days,
                "explanation": (
                    f"A1 clock start: {a1_clock_start_label}. "
                    f"14-month deadline: {deadline_14mo}. "
                    f"{first_action_label} mailed: {first_action}. "
                    f"USPTO exceeded 14-month window by {days} days."
                )
            })
        else:
            forensic.append({
                "rule": "37 CFR 1.702(a)(1) — A1: 14-Month Rule",
                "start": None, "end": None, "days": 0,
                "explanation": (
                    f"A1 clock start: {a1_clock_start_label}. "
                    f"14-month deadline: {deadline_14mo}. "
                    f"{first_action_label} mailed: {first_action}. USPTO acted within 14 months — no A1 delay."
                )
            })

    used_oa = set()
    for i, resp_date in enumerate(resp):
        deadline_4mo = add_months(resp_date, 4)
        candidates   = []
        for j, oa in enumerate(oa_list):
            if oa > resp_date and j not in used_oa:
                candidates.append((oa, f"OA #{j+1} ({oa})"))
                used_oa.add(j)
                break
        if noa and noa > resp_date and not candidates:
            candidates.append((noa, f"NOA ({noa})"))
        if candidates:
            next_action, next_label = candidates[0]
            if next_action > deadline_4mo:
                days = (next_action - deadline_4mo).days
                windows.append((deadline_4mo, next_action))
                forensic.append({
                    "rule": f"37 CFR 1.702(a)(2) — A2: 4-Month Rule (Response #{i+1})",
                    "start": deadline_4mo, "end": next_action, "days": days,
                    "explanation": (
                        f"Response #{i+1} filed: {resp_date}. 4-month deadline: {deadline_4mo}. "
                        f"Next USPTO action ({next_label}). USPTO exceeded 4-month window by {days} days."
                    )
                })
            else:
                forensic.append({
                    "rule": f"37 CFR 1.702(a)(2) — A2: 4-Month Rule (Response #{i+1})",
                    "start": None, "end": None, "days": 0,
                    "explanation": (
                        f"Response #{i+1} filed: {resp_date}. 4-month deadline: {deadline_4mo}. "
                        f"Next USPTO action: {next_label}. USPTO acted within 4 months — no A2 delay."
                    )
                })

    # ── A2-RCE: 4-Month Rule after RCE filings (37 CFR 1.702(a)(2)) ─────────────
    # After an RCE is filed, USPTO must act within 4 months. This window is
    # identical to a normal A2 response window but was previously not calculated
    # because all_responses only includes NF/Final responses, not RCEs.
    # This is an ADDITIVE condition — existing calculations above are unchanged.
    rce_dates = d.get("rce_dates", [])
    existing_resp_set = set(str(r) for r in resp)  # avoid double-counting if RCE already in responses
    for i, rce_date in enumerate(rce_dates):
        if str(rce_date) in existing_resp_set:
            continue  # already counted above
        deadline_4mo_rce = add_months(rce_date, 4)
        # Next USPTO action after RCE: first OA not yet used, or NOA
        rce_candidates = []
        for j, oa in enumerate(oa_list):
            if oa > rce_date and j not in used_oa:
                rce_candidates.append((oa, f"OA #{j+1} ({oa})"))
                used_oa.add(j)
                break
        if noa and noa > rce_date and not rce_candidates:
            rce_candidates.append((noa, f"NOA ({noa})"))
        if rce_candidates:
            next_action, next_label = rce_candidates[0]
            if next_action > deadline_4mo_rce:
                days = (next_action - deadline_4mo_rce).days
                windows.append((deadline_4mo_rce, next_action))
                forensic.append({
                    "rule": f"37 CFR 1.702(a)(2) — A2: 4-Month Rule (RCE #{i+1})",
                    "start": deadline_4mo_rce, "end": next_action, "days": days,
                    "explanation": (
                        f"RCE #{i+1} filed: {rce_date}. 4-month deadline: {deadline_4mo_rce}. "
                        f"Next USPTO action ({next_label}). USPTO exceeded 4-month window by {days} days."
                    )
                })
            else:
                forensic.append({
                    "rule": f"37 CFR 1.702(a)(2) — A2: 4-Month Rule (RCE #{i+1})",
                    "start": None, "end": None, "days": 0,
                    "explanation": (
                        f"RCE #{i+1} filed: {rce_date}. 4-month deadline: {deadline_4mo_rce}. "
                        f"Next USPTO action: {next_label}. USPTO acted within 4 months of RCE — no A2 delay."
                    )
                })

    issue_fee        = d.get("issue_fee_date")
    a3_trigger       = issue_fee if issue_fee else noa
    a3_trigger_label = "Issue Fee paid" if issue_fee else "NOA (issue fee date unavailable)"
    if a3_trigger and grant:
        deadline_4mo_a3 = add_months(a3_trigger, 4)
        if grant > deadline_4mo_a3:
            days = (grant - deadline_4mo_a3).days
            windows.append((deadline_4mo_a3, grant))
            forensic.append({
                "rule": "35 USC 154(b)(1)(A)(iv) — A3: Issue Fee +4 Months → Grant",
                "start": deadline_4mo_a3, "end": grant, "days": days,
                "explanation": (
                    f"Issue fee paid: {a3_trigger} ({a3_trigger_label}). "
                    f"4-month deadline: {deadline_4mo_a3}. Grant date: {grant}. "
                    f"USPTO exceeded 4-month window by {days} days."
                )
            })
        else:
            forensic.append({
                "rule": "35 USC 154(b)(1)(A)(iv) — A3: Issue Fee +4 Months → Grant",
                "start": None, "end": None, "days": 0,
                "explanation": (
                    f"Issue fee paid: {a3_trigger} ({a3_trigger_label}). "
                    f"4-month deadline: {deadline_4mo_a3}. Grant date: {grant}. "
                    f"USPTO issued within 4 months — no A3 delay."
                )
            })

    merged  = merge_intervals(windows)
    total_a = interval_days(merged)
    return {"forensic": forensic, "windows": windows, "merged": merged, "total_days": total_a}

# ─── B-DELAY ENGINE ────
def compute_b_delay(d):
    filing            = d["filing_date"]
    grant             = d["grant_date"]
    rce_dates         = d["rce_dates"]
    commencement_date = d.get("commencement_date")

    forensic = []
    windows  = []

    is_projected = d.get("grant_date_is_projected", False)
    grant_label  = "Projected Grant (PTA Issue Date — patent pending)" if is_projected else "Grant"

    if not filing or not grant:
        return {"forensic": forensic, "windows": windows, "merged": [], "total_days": 0}

    if commencement_date:
        b_clock_start       = commencement_date
        b_clock_start_label = f"Commencement Date ({commencement_date}) [PCT/371 — used instead of filing date]"
    else:
        b_clock_start       = filing
        b_clock_start_label = f"Filing Date ({filing})"

    deadline_3yr = add_years(b_clock_start, 3)
    b_end        = grant
    rce_note     = ""
    if rce_dates:
        first_rce = rce_dates[0]
        if first_rce < grant:
            b_end    = first_rce
            rce_note = f" (B-clock stopped at first RCE: {first_rce})"

    if b_end > deadline_3yr:
        days = (b_end - deadline_3yr).days
        windows.append((deadline_3yr, b_end))
        forensic.append({
            "rule": "37 CFR 1.702(b) — B: 3-Year Rule",
            "start": deadline_3yr, "end": b_end, "days": days,
            "explanation": (
                f"B-clock start: {b_clock_start_label}. "
                f"3-year deadline: {deadline_3yr}. "
                f"{grant_label if not rce_dates else 'B-clock end'}: {b_end}{rce_note}"
                + (" [projected — patent pending]" if is_projected else "") + ". "
                f"USPTO exceeded 3-year window by {days} days."
            )
        })
    else:
        forensic.append({
            "rule": "37 CFR 1.702(b) — B: 3-Year Rule",
            "start": None, "end": None, "days": 0,
            "explanation": (
                f"B-clock start: {b_clock_start_label}. "
                f"3-year deadline: {deadline_3yr}. {grant_label}/B-end: {b_end}{rce_note}. "
                f"USPTO issued within 3 years — no B delay."
            )
        })

    merged  = merge_intervals(windows)
    total_b = interval_days(merged)

    # ── ADDITIVE FIX: When RCE is filed AFTER the 3-year mark, B-delay also
    # includes the post-NOA remainder (NOA → Grant, exclusive of grant day).
    # Existing logic (B stops at RCE) is unchanged; we just add the gap back.
    # Formula: B_total = (RCE − 3yr_mark) + max(0, (Grant − NOA).days − 1)
    _noa = d.get("noa_date")
    if rce_dates and _noa and rce_dates[0] > deadline_3yr and grant > _noa:
        extra_b  = max(0, (grant - _noa).days - 1)
        total_b += extra_b
        if extra_b > 0:
            forensic.append({
                "rule": "37 CFR 1.702(b) — B: Post-RCE Remainder (NOA→Grant)",
                "start": _noa, "end": grant, "days": extra_b,
                "explanation": (
                    f"RCE filed after 3-year mark ({rce_dates[0]}). "
                    f"B-delay resumes after RCE examination ends (NOA: {_noa}). "
                    f"Post-NOA remainder: ({grant} − {_noa}).days − 1 = {extra_b} days."
                )
            })

    return {"forensic": forensic, "windows": windows, "merged": merged, "total_days": total_b}

# ─── C-DELAY ENGINE ────
def compute_c_delay(d):
    ev       = d["ev"]
    forensic = []
    total_c  = 0

    secrecy_dates = [x[0] for x in ev.get("SECR", [])]
    if secrecy_dates:
        forensic.append({
            "rule": "37 CFR 1.702(c) — Secrecy Order", "days": 0,
            "explanation": f"Secrecy order detected on {secrecy_dates}. Manual review required."
        })

    interf_dates = [x[0] for x in ev.get("INTRF", [])]
    if interf_dates:
        forensic.append({
            "rule": "37 CFR 1.702(d) — Interference/Derivation", "days": 0,
            "explanation": f"Interference/derivation detected on {interf_dates}. Manual review required."
        })

    if not forensic:
        forensic.append({
            "rule": "37 CFR 1.702(c/d/e) — C-Delay", "days": 0,
            "explanation": "No secrecy orders, interferences, or appeals detected. C-Delay = 0."
        })

    return {"forensic": forensic, "total_days": total_c}

# ─── OVERLAP ENGINE ────
def compute_overlap(a_result, b_result):
    a_merged         = a_result["merged"]
    b_merged_shifted = [(s + dt.timedelta(days=1), e) for s, e in b_result["merged"]]
    overlap          = intersect_intervals(a_merged, b_merged_shifted)
    overlap          = merge_intervals(overlap)
    total            = interval_days(overlap)
    return {"intervals": overlap, "total_days": total}

# ─── PRINT HELPERS ────
W = 70

def hdr(title, char="═"):
    print(f"\n{'':2}{char*W}")
    print(f"{'':4}{title}")
    print(f"{'':2}{char*W}")

def sub(title):
    print(f"\n  {'─'*66}")
    print(f"  {title}")
    print(f"  {'─'*66}")

def row(label, value, indent=4):
    print(f"{'':>{indent}}{label:<30} {value}")

# ─── MAIN REPORT ────
def print_forensic_report(app_num, d, a_res, b_res, c_res, overlap_res, app_delay_forensic, maint_exp):
    meta    = d["meta"]
    filing  = d["filing_date"]
    grant   = d["grant_date"]
    ptac    = d["ptac_date"]
    noa     = d["noa_date"]
    pta_raw = d["pta_raw"]

    hdr("USPTO PTA FORENSIC ANALYZER  (100% Raw Date Math)")
    print(f"    Application  : {app_num}")
    print(f"    Patent No.   : {meta.get('patentNumber','N/A')}")
    print(f"    Title        : {meta.get('inventionTitle','N/A')}")
    print(f"    Applicant    : {meta.get('firstApplicantName','N/A')}")
    print(f"    Examiner     : {meta.get('examinerNameText','N/A')}")
    print(f"    Filing Date  : {filing}")
    commencement = d.get("commencement_date")
    if commencement:
        print(f"    Commencement : {commencement}  ← B-clock start (PCT/371)")
    print(f"    Grant Date   : {grant}")
    print(f"    PTA Issue    : {ptac or grant}")

    # ── All PTA-Relevant Dates ────
    sub("ALL PTA-RELEVANT DATES (Chronological)")
    print(f"  {'DATE':<14} {'ROLE':<20} {'EVENT / NAME'}")
    print(f"  {'─'*12}   {'─'*18}   {'─'*30}")

    date_table = []
    def add_dt(date, role, name, note=""):
        if date:
            date_table.append((date, role, name, note))

    add_dt(filing, "Core", "Filing Date", "Start of all PTA clocks (A, B, C)")
    _a1_start = commencement or filing
    add_dt(add_months(_a1_start, 14), "A-Delay", "14-Month Deadline (A1)",
           f"USPTO must mail 1st OA by this date [clock from {'Commencement' if commencement else 'Filing'} Date]")
    _b_start = commencement or filing
    add_dt(add_years(_b_start, 3), "B-Delay", "3-Year Deadline (B)",
           f"USPTO must issue patent by this date [clock from {'Commencement' if commencement else 'Filing'} Date]")
    for i, oa in enumerate(d["mctnf_dates"]):
        add_dt(oa, "A-Delay", f"Non-Final OA Mailed #{i+1} (MCTNF)", "Triggers 4-month A2 clock after applicant response")
    for i, oa in enumerate(d["mctfr_dates"]):
        add_dt(oa, "A-Delay", f"Final OA Mailed #{i+1} (MCTFR)", "Triggers 4-month A2 clock after applicant response")
    for i, r in enumerate(d["resp_nonfinal"]):
        add_dt(r, "Applicant", f"Response after Non-Final #{i+1} (A...)", "Starts 4-month A2 clock; 3-month applicant deadline")
    for i, r in enumerate(d["resp_final"]):
        add_dt(r, "Applicant", f"Response after Final #{i+1} (A.AF)", "Starts 4-month A2 clock; 3-month applicant deadline")
    for i, x in enumerate(d["xt_granted"]):
        add_dt(x, "Applicant Delay", f"Extension of Time Granted #{i+1} (XT/G)", "Adds to applicant delay under 37 CFR 1.704(c)(8)")
    for i, ids in enumerate(d["ids_dates"]):
        role = "Applicant Delay" if noa and ids > noa else "Applicant"
        add_dt(ids, role, f"IDS Filed #{i+1} (WIDS/EIDS)",
               "After NOA = applicant delay under 37 CFR 1.704(c)(8)" if noa and ids > noa else "Before NOA — no delay")
    for i, rce in enumerate(d["rce_dates"]):
        add_dt(rce, "RCE", f"RCE Filed #{i+1}", "Resets B-delay clock; applicant delay during RCE pendency")
    add_dt(noa, "A-Delay", "Notice of Allowance Mailed (MN/=.)", "Triggers 4-month A3 clock for issue fee")
    add_dt(d["issue_fee_date"], "Applicant", "Issue Fee Payment (IFEE)", "Must be paid within 3 months of NOA")
    add_dt(grant, "Core", "Grant Date (Patent Issue Date)", "End of all PTA clocks (official)")
    add_dt(ptac,  "Core", "PTA Issue Date (PTAC event)", "Issue date used specifically in PTA calculation")

    date_table.sort(key=lambda x: x[0])
    seen = set()
    for date, role, name, note in date_table:
        key = (date, name)
        if key in seen:
            continue
        seen.add(key)
        print(f"  {str(date):<14} {role:<20} {name}")
        if note:
            print(f"  {'':14} {'':20}   ↳ {note}")

    # ── A-Delay ────
    sub("A-DELAY FORENSIC BREAKDOWN (37 CFR 1.702(a))")
    for item in a_res["forensic"]:
        print(f"\n  ✦ {item['rule']}")
        print(f"    {item['explanation']}")
        if item.get("days", 0) > 0:
            print(f"    → DELAY: {item['days']} days  [{item['start']} → {item['end']}]")
        else:
            print(f"    → No delay.")
    print(f"\n  A-Delay merged windows (union, no double-count):")
    if a_res["merged"]:
        for s, e in a_res["merged"]:
            print(f"    {s} → {e}  ({(e-s).days} days)")
    else:
        print("    None.")
    print(f"\n  TOTAL A-DELAY : {a_res['total_days']} days")

    # ── B-Delay ────
    sub("B-DELAY FORENSIC BREAKDOWN (37 CFR 1.702(b))")
    for item in b_res["forensic"]:
        print(f"\n  ✦ {item['rule']}")
        print(f"    {item['explanation']}")
        if item.get("days", 0) > 0:
            print(f"    → DELAY: {item['days']} days  [{item['start']} → {item['end']}]")
        else:
            print(f"    → No delay.")
    print(f"\n  TOTAL B-DELAY : {b_res['total_days']} days")

    # ── C-Delay ────
    sub("C-DELAY FORENSIC BREAKDOWN (37 CFR 1.702(c/d/e))")
    for item in c_res["forensic"]:
        print(f"\n  ✦ {item['rule']}")
        print(f"    {item['explanation']}")
    print(f"\n  TOTAL C-DELAY : {c_res['total_days']} days")

    # ── Overlap ────
    sub("OVERLAP (A ∩ B) — Days counted in BOTH A and B (subtract once)")
    if overlap_res["intervals"]:
        for s, e in overlap_res["intervals"]:
            print(f"  {s} → {e}  ({(e-s).days} days)")
    else:
        print("  No overlap between A and B windows.")
    print(f"\n  TOTAL OVERLAP : {overlap_res['total_days']} days")

    # ── Applicant Delay ────
    sub("APPLICANT DELAY FORENSIC BREAKDOWN (37 CFR 1.704)")
    total_app_delay = 0
    for item in app_delay_forensic:
        print(f"\n  ✦ {item['rule']}")
        print(f"    {item['explanation']}")
        if item["delay_days"] > 0:
            print(f"    → DELAY CHARGED: {item['delay_days']} days")
            total_app_delay += item["delay_days"]
        else:
            print(f"    → No delay charged.")
    print(f"\n  TOTAL APPLICANT DELAY (Calculated) : {total_app_delay} days")

    # ── Final PTA ────
    a_days    = a_res["total_days"]
    b_days    = b_res["total_days"]
    c_days    = c_res["total_days"]
    ov_days   = overlap_res["total_days"]
    non_ov    = a_days + b_days + c_days - ov_days
    total_pta = max(0, non_ov - total_app_delay)

    hdr("FINAL PTA CALCULATION", char="═")
    print(f"    {'A-Delay':<35} : {a_days:>6} days")
    print(f"    {'B-Delay':<35} : {b_days:>6} days")
    print(f"    {'C-Delay':<35} : {c_days:>6} days")
    print(f"    {'Overlap (A ∩ B)':<35} : {ov_days:>6} days  (subtract)")
    print(f"    {'─'*50}")
    print(f"    {'Non-Overlapping USPTO Delay':<35} : {non_ov:>6} days")
    print(f"    {'Applicant Delay':<35} : {total_app_delay:>6} days  (subtract)")
    print(f"    {'─'*50}")
    print(f"    {'TOTAL PTA (Calculated)':<35} : {total_pta:>6} days")

    # ── USPTO Authoritative Comparison ────
    sub("USPTO AUTHORITATIVE VALUES (for comparison only)")
    u_a   = pta_raw.get("aDelayQuantity", 0)
    u_b   = pta_raw.get("bDelayQuantity", 0)
    u_c   = pta_raw.get("cDelayQuantity", 0)
    u_ov  = pta_raw.get("overlappingDayQuantity", 0)
    u_nov = pta_raw.get("nonOverlappingDayDelayQuantity", 0)
    u_app = pta_raw.get("applicantDayDelayQuantity", 0)
    u_tot = pta_raw.get("adjustmentTotalQuantity", 0)

    print(f"  {'A-Delay (USPTO)':<35} : {u_a:>6}")
    print(f"  {'B-Delay (USPTO)':<35} : {u_b:>6}")
    print(f"  {'C-Delay (USPTO)':<35} : {u_c:>6}")
    print(f"  {'Overlap (USPTO)':<35} : {u_ov:>6}")
    print(f"  {'Non-Overlapping (USPTO)':<35} : {u_nov:>6}")
    print(f"  {'Applicant Delay (USPTO)':<35} : {u_app:>6}")
    print(f"  {'TOTAL PTA (USPTO)':<35} : {u_tot:>6}")
    diff = total_pta - u_tot
    print(f"\n  Difference (Calculated - USPTO)  : {diff:+d} days")

    # ── Applicant Delay Reconciliation ────
    app_diff = total_app_delay - u_app
    if app_diff != 0:
        sub("APPLICANT DELAY RECONCILIATION")
        print(f"  Calculated Applicant Delay : {total_app_delay} days")
        print(f"  USPTO Applicant Delay      : {u_app} days")
        print(f"  Difference                 : {app_diff:+d} days")
        print()
        print("  Possible reasons for difference:")
        if app_diff < 0:
            print("  ✦ USPTO may be counting additional 1.704(c) categories not")
            print("    captured in raw event data (e.g., paid extension months,")
            print("    post-allowance processing, petition delays).")
            print("  ✦ USPTO uses internal mailing rules (Federal holidays,")
            print("    weekends) that shift deadlines by 1-3 days.")
            print("  ✦ Extension of Time months may be counted differently.")
        else:
            print("  ✦ Calculated delay may be over-counting some categories.")
            print("  ✦ USPTO may have granted a PTA petition reducing applicant delay.")

    # ── PTA History ────
    if d["pta_history"]:
        sub("USPTO PTA HISTORY (Event-by-Event from USPTO System)")
        print(f"  {'SEQ':>5}  {'DATE':<12}  {'USPTO DELAY':>11}  {'APP DELAY':>9}  EVENT")
        print(f"  {'─'*5}  {'─'*12}  {'─'*11}  {'─'*9}  {'─'*35}")
        for h in sorted(d["pta_history"], key=lambda x: x.get("eventSequenceNumber", 0)):
            seq   = h.get("eventSequenceNumber", "")
            date  = h.get("eventDate", "")[:10]
            iptd  = h.get("ipOfficeDayDelayQuantity", 0)
            appd  = h.get("applicantDayDelayQuantity", 0)
            desc  = h.get("eventDescriptionText", "")[:50]
            flag  = " ◄ APP DELAY"   if appd > 0 else ""
            flag2 = " ◄ USPTO DELAY" if iptd > 0 else ""
            print(f"  {seq:>5}  {date:<12}  {iptd:>11}  {appd:>9}  {desc}{flag}{flag2}")

    # ══════════════════════════════════════════════════════════════════
    # ── MAINTENANCE FEES + EXPIRATION DATE ────
    # ══════════════════════════════════════════════════════════════════
    if maint_exp:
        hdr("MAINTENANCE FEES & PATENT EXPIRATION DATE", char="═")

        print(f"    {'Date Filed':<40} : {maint_exp['filing_date'].strftime('%m/%d/%Y')}")
        print(f"    {'Grant Date (Issue Date)':<40} : {maint_exp['grant_date'].strftime('%m/%d/%Y')}")
        print(f"    {'PTA Added (35 USC 154(b))':<40} : {maint_exp['total_pta_days']} days")
        print(f"    {'Base Expiration (20 yrs from filing)':<40} : {maint_exp['base_expiration'].strftime('%m/%d/%Y')}")
        if maint_exp.get('td_applied'):
            print(f"    {'Terminal Disclaimer Cap Date':<40} : {maint_exp['td_cap_date'].strftime('%m/%d/%Y')}")
            print(f"    {'PTA Days Forfeited (TD cap)':<40} : {maint_exp['td_days_forfeited']} days")
        print(f"    {'─'*60}")
        print(f"    {'CALCULATED EXPIRATION DATE (uncapped)':<40} : {maint_exp['expiration_with_pta'].strftime('%m/%d/%Y')}")
        if maint_exp.get('td_applied'):
            print(f"    {'EFFECTIVE EXPIRATION (after TD cap)':<40} : {maint_exp['effective_expiration'].strftime('%m/%d/%Y')}")
        print()
        print(f"    NOTE: Maintenance fees must be paid to preserve the full patent")
        print(f"    term. For non-design, non-plant patents, fees are due 3 times.")
        print(f"    Fees may be paid without surcharge during the open window, or")
        print(f"    with surcharge during the grace period. Cannot be paid early.")
        print()

        sub("MAINTENANCE FEE SCHEDULE (37 CFR 1.362)")
        print(f"  {'FEE':<6}  {'WINDOW OPENS':<14}  {'WINDOW CLOSES':<16}  {'LATEST DUE DATE (w/ surcharge)'}")
        print(f"  {'─'*4}  {'─'*12}  {'─'*14}  {'─'*30}")

        for mf in maint_exp["maintenance_fees"]:
            ordinal = ["1st", "2nd", "3rd"][mf["fee_number"] - 1]
            print(f"  {ordinal:<6}  "
                  f"{mf['window_open'].strftime('%m/%d/%Y'):<14}  "
                  f"{mf['window_close'].strftime('%m/%d/%Y'):<16}  "
                  f"{mf['grace_period_end'].strftime('%m/%d/%Y')}")
            print(f"         ↳ Pay between {mf['base_years']}–{mf['base_years']}.5 yrs (no surcharge) "
                  f"or {mf['base_years']}.5–{mf['base_years']+1} yrs (with surcharge) after grant")

        print()
        print(f"  {'─'*66}")
        print(f"  ERROR CONTROL SUMMARY")
        print(f"  {'─'*66}")
        print(f"  Date Filed                              : {maint_exp['filing_date'].strftime('%-m/%-d/%Y') if sys.platform != 'win32' else maint_exp['filing_date'].strftime('%#m/%#d/%Y')}")
        print(f"  Earliest Effective Filing Date          : {maint_exp['filing_date'].strftime('%-m/%-d/%Y') if sys.platform != 'win32' else maint_exp['filing_date'].strftime('%#m/%#d/%Y')}")
        print(f"  Grant Date                              : {maint_exp['grant_date'].strftime('%-m/%-d/%Y') if sys.platform != 'win32' else maint_exp['grant_date'].strftime('%#m/%#d/%Y')}")
        print(f"  Terminally Disclaimed to                : {maint_exp['td_cap_date'].strftime('%-m/%-d/%Y') if maint_exp.get('td_applied') and maint_exp.get('td_cap_date') else '—'}")
        print(f"  Combined PTA days (35 USC 154(b))       : {maint_exp['total_pta_days']}")
        print(f"  Expiration due to Missed Maint. Fee     : —")
        print(f"  {'─'*66}")
        print(f"  CALCULATED EXPIRATION DATE              : {maint_exp['expiration_with_pta'].strftime('%-m/%-d/%Y') if sys.platform != 'win32' else maint_exp['expiration_with_pta'].strftime('%#m/%#d/%Y')}")
        if maint_exp.get('td_applied'):
            eff = maint_exp['effective_expiration']
            print(f"  EFFECTIVE EXPIRATION (TD-capped)        : {eff.strftime('%-m/%-d/%Y') if sys.platform != 'win32' else eff.strftime('%#m/%#d/%Y')}")
        print(f"  {'─'*66}")
        print()
        print(f"  NOTICE: This calculator is only an educational tool. It was")
        print(f"  developed based on assumptions that may or may not apply in a")
        print(f"  particular case. It does not provide a determination of any kind")
        print(f"  by USPTO.")

    print(f"\n{'═'*72}\n")

# ─── SAVE JSON ────
def save_json(app_num, d, a_res, b_res, c_res, overlap_res, app_delay_forensic,
              total_app_delay, total_pta, maint_exp):
    pta_raw = d["pta_raw"]

    maint_json = None
    if maint_exp:
        maint_json = {
            "filing_date":          str(maint_exp["filing_date"]),
            "grant_date":           str(maint_exp["grant_date"]),
            "total_pta_days":       maint_exp["total_pta_days"],
            "base_expiration":      str(maint_exp["base_expiration"]),
            "expiration_with_pta":  str(maint_exp["expiration_with_pta"]),
            "effective_expiration": str(maint_exp["effective_expiration"]),
            "td_applied":           maint_exp.get("td_applied", False),
            "td_cap_date":          str(maint_exp["td_cap_date"]) if maint_exp.get("td_cap_date") else None,
            "td_days_forfeited":    maint_exp.get("td_days_forfeited", 0),
            "maintenance_fees": [
                {
                    "fee_number":       mf["fee_number"],
                    "window_open":      str(mf["window_open"]),
                    "window_close":     str(mf["window_close"]),
                    "grace_period_end": str(mf["grace_period_end"]),
                    "label":            mf["label"],
                }
                for mf in maint_exp["maintenance_fees"]
            ]
        }

    out = {
        "application_number":  app_num,
        "patent_number":       d["meta"].get("patentNumber"),
        "title":               d["meta"].get("inventionTitle"),
        "filing_date":         str(d["filing_date"]),
        "grant_date":          str(d["grant_date"]),
        "noa_date":            str(d["noa_date"]) if d["noa_date"] else None,
        "issue_fee_date":      str(d["issue_fee_date"]) if d["issue_fee_date"] else None,
        "a_delay": {
            "total_days":    a_res["total_days"],
            "merged_windows": [(str(s), str(e)) for s, e in a_res["merged"]],
            "forensic": [{k: str(v) if isinstance(v, dt.date) else v for k, v in item.items()} for item in a_res["forensic"]]
        },
        "b_delay": {
            "total_days": b_res["total_days"],
            "forensic":   [{k: str(v) if isinstance(v, dt.date) else v for k, v in item.items()} for item in b_res["forensic"]]
        },
        "c_delay":   {"total_days": c_res["total_days"]},
        "overlap":   {"total_days": overlap_res["total_days"], "intervals": [(str(s), str(e)) for s, e in overlap_res["intervals"]]},
        "applicant_delay": {
            "total_days_calculated": total_app_delay,
            "total_days_uspto":      pta_raw.get("applicantDayDelayQuantity", 0),
            "forensic": [{k: str(v) if isinstance(v, dt.date) else v for k, v in item.items()} for item in app_delay_forensic]
        },
        "total_pta_calculated":   total_pta,
        "total_pta_uspto":        pta_raw.get("adjustmentTotalQuantity", 0),
        "pta_history_from_uspto": d["pta_history"],
        "maintenance_and_expiration": maint_json,
    }

    clean = app_num.replace("/","").replace(",","").replace(" ","")
    fname = f"PTA_Forensic_{clean}.json"
    with open(fname, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"  [Saved] {fname}")

# ─── MAIN ────
def main():
    print("=" * 72)
    print("  USPTO PTA FORENSIC ANALYZER  (v9)")
    print("  100% Raw Date Math · Full 37 CFR 1.704(c) Engine")
    print("  + Maintenance Fee Schedule + Patent Expiration Date")
    print("=" * 72)

    if len(sys.argv) > 1:
        app_num = sys.argv[1]
    else:
        app_num = input("\nEnter Application Number (e.g., 18/828,818): ").strip()

    print(f"\nFetching data for {app_num} ...")
    record = fetch_application(app_num)

    print("Extracting all events ...")
    d = extract_all_events(record)

    print("Running forensic engines ...")
    a_res              = compute_a_delay(d)
    b_res              = compute_b_delay(d)
    c_res              = compute_c_delay(d)
    overlap_res        = compute_overlap(a_res, b_res)
    app_delay_forensic = compute_applicant_delay_forensic(d)

    total_app_delay = sum(x["delay_days"] for x in app_delay_forensic)
    non_ov          = a_res["total_days"] + b_res["total_days"] + c_res["total_days"] - overlap_res["total_days"]
    total_pta       = max(0, non_ov - total_app_delay)

    # ── Terminal Disclaimer Detection ────
    print("Checking for terminal disclaimers ...")
    td_list    = extract_terminal_disclaimers(record)
    td_result  = resolve_td_expiration(td_list) if td_list else None
    td_cap     = td_result["td_cap_date"] if td_result else None

    # ── Maintenance Fees + Expiration ────
    maint_exp = compute_maintenance_and_expiration(
        grant_date               = d["grant_date"],
        filing_date              = d["filing_date"],
        total_pta_days           = total_pta,
        terminal_disclaimer_date = td_cap,
    )

    print_forensic_report(app_num, d, a_res, b_res, c_res, overlap_res,
                          app_delay_forensic, maint_exp)
    save_json(app_num, d, a_res, b_res, c_res, overlap_res,
              app_delay_forensic, total_app_delay, total_pta, maint_exp)


# ══════════════════════════════════════════════════════════════════════════════
#  TD CHAIN AUTO-RESOLVER  (PDF + OCR)
#  resolve_td_chain_full(app_num) → full disclaimer chain with cap date
# ══════════════════════════════════════════════════════════════════════════════

# TD document codes that contain the disclaimer text / referenced patent
_TD_PDF_CODES = {
    "DIST.E.FILE", "DISQ.E.FILE", "DIST", "DISQ",
    "DIST.FILE",   "DISQ.FILE",   "P574", "TDIS",
    "TDSC", "TD", "TDIN", "TDNP", "TDAP",
}


def _download_td_pdf_bytes(app_num, doc_id):
    """Download a TD PDF from USPTO ODP and return raw bytes, or None on failure."""
    clean = app_num.replace("/", "").replace(",", "").replace(" ", "")
    url   = f"https://api.uspto.gov/api/v1/download/applications/{clean}/{doc_id}.pdf"
    try:
        r = requests.get(url, headers={"x-api-key": API_KEY}, timeout=30)
        return r.content if r.status_code == 200 else None
    except Exception:
        return None


def _extract_text_from_pdf_bytes(pdf_bytes):
    """
    Extract text from PDF bytes.  Returns (text: str, used_ocr: bool).

    Layer 1  — pypdf content stream
    Layer 1b — pypdf form fields  (USPTO TD forms store data in /AcroForm fields)
    Layer 2  — pdfplumber
    Layer 3a — PyMuPDF (no poppler needed)
    Layer 3b — pdf2image + poppler + pytesseract (scanned PDFs)
    """
    import io
    MIN_TEXT = 40

    # ── Layer 1: pypdf content stream ───────────────────────────────────────
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        text = "".join(page.extract_text() or "" for page in reader.pages)
        if len(text.strip()) >= MIN_TEXT:
            return text, False
    except Exception:
        text = ""

    # ── Layer 1b: pypdf form fields (AcroForm) ───────────────────────────────
    # USPTO electronic TD PDFs store the app/patent numbers in form fields,
    # not the content stream — this is why layers 1 & 2 return empty.
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        fields = reader.get_fields() or {}
        parts = []
        for name, field in fields.items():
            val = field.get("/V") or field.get("value") or ""
            if val and str(val).strip():
                parts.append(f"{name}: {val}")
        # Also read annotations on each page (another place values live)
        for page in reader.pages:
            for annot in (page.get("/Annots") or []):
                try:
                    obj = annot.get_object() if hasattr(annot, "get_object") else annot
                    val = obj.get("/V") or obj.get("/DV") or ""
                    if val and str(val).strip() not in ("/Off", ""):
                        parts.append(str(val))
                except Exception:
                    pass
        form_text = "\n".join(parts)
        if len(form_text.strip()) >= MIN_TEXT:
            return form_text, False
    except Exception:
        form_text = ""

    # ── Layer 2: pdfplumber ─────────────────────────────────────────────────
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pl_text = "".join(page.extract_text() or "" for page in pdf.pages)
        if len(pl_text.strip()) >= MIN_TEXT:
            return pl_text, False
    except Exception:
        pl_text = ""

    # ── Helper: find tesseract (cross-platform) ──────────────────────────────
    def _find_tesseract():
        import os as _os, shutil as _shutil
        # On Linux/Mac, tesseract is on PATH (installed via apt/brew)
        if _os.name != "nt":
            return _shutil.which("tesseract")   # None if not installed
        # Windows fallback paths
        candidates = [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            _os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
            _os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
            _os.path.expandvars(r"%USERPROFILE%\AppData\Local\Tesseract-OCR\tesseract.exe"),
            r"C:\Tesseract-OCR\tesseract.exe",
            r"C:\tools\Tesseract-OCR\tesseract.exe",
        ]
        for c in candidates:
            if _os.path.isfile(c):
                return c
        return None

    # ── Layer 3a: PyMuPDF → pytesseract (no poppler needed) ────────────────
    try:
        import fitz          # PyMuPDF  (pip install pymupdf)
        import pytesseract
        from PIL import Image
        import io as _io

        tess = _find_tesseract()
        if tess:
            pytesseract.pytesseract.tesseract_cmd = tess

        doc      = fitz.open(stream=pdf_bytes, filetype="pdf")
        ocr_text = ""
        for page in doc:
            mat  = fitz.Matrix(2.0, 2.0)   # 2× zoom ≈ 144 dpi
            pix  = page.get_pixmap(matrix=mat)
            img  = Image.open(_io.BytesIO(pix.tobytes("png")))
            ocr_text += pytesseract.image_to_string(img)
        doc.close()
        if ocr_text.strip():
            return ocr_text, True
    except Exception:
        pass

    # ── Layer 3b: pdf2image + poppler → pytesseract ─────────────────────────
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
        import os as _os, glob as _glob

        # On Linux, poppler-utils is installed via apt — no path needed.
        # On Windows, search common locations.
        POPPLER_PATH = None
        if _os.name == "nt":
            for _search_root in [r"C:\poppler", r"C:\Program Files\poppler"]:
                for root, dirs, files in _os.walk(_search_root):
                    if "pdfinfo.exe" in files:
                        POPPLER_PATH = root
                        break
                if POPPLER_PATH:
                    break

        kwargs = {"dpi": 200}
        if POPPLER_PATH:
            kwargs["poppler_path"] = POPPLER_PATH

        tess = _find_tesseract()
        if tess:
            pytesseract.pytesseract.tesseract_cmd = tess

        images   = convert_from_bytes(pdf_bytes, **kwargs)
        ocr_text = "".join(pytesseract.image_to_string(img) for img in images)
        return ocr_text, True
    except Exception:
        pass

    return text or pl_text, False     # return whatever we got


def _parse_refs_from_td_text(text, exclude_app_num=None):
    """
    Extract PARENT application / patent numbers from USPTO TD form OCR text.

    The TD form has TWO "Application #" tables:
      1. HEADER table  — lists the SUBJECT application (the one being searched)
      2. REFERENCE table — lists PARENT applications (what we want)

    The reference table appears after:
      "pending reference Application Number(s)"
    The patent reference table appears after:
      "prior patent number(s)"

    Returns (ref_apps: list[str], ref_patents: list[str]).
    exclude_app_num: strip this app number from results (it's the subject app).
    """
    import re

    # Normalize exclude app num for comparison
    excl = re.sub(r'[/,\s]', '', exclude_app_num or '').lstrip('0')

    def _norm(n):
        return re.sub(r'[/,\s]', '', n).lstrip('0')

    ref_apps    = []
    ref_patents = []

    # ── Strategy 1: Look for "pending reference Application Number" section ──
    # This is the ONLY reliable marker before the parent-app table.
    # The subject-app table uses "APPLICATION #  FILING DATE  FIRST NAMED INVENTOR"
    # which has "INVENTOR" nearby — so we require "pending reference" to be present.
    pend_match = re.search(
        r'pending\s+reference\s+Application\s+Number',
        text, re.IGNORECASE
    )
    if pend_match:
        section = text[pend_match.start():]
        # Stop before "prior patent number" or body text
        stop = re.search(r'prior\s+patent\s+number|patent\s+number\(s\)|as\s+the\s+term\s+of|In\s+making', section, re.IGNORECASE)
        if stop:
            section = section[:stop.start()]
        # 8-digit nums (e.g. 17804535)
        for n in re.findall(r'\b(1[0-9]\d{6}|2[0-9]\d{5})\b', section):
            if _norm(n) != excl:
                ref_apps.append(n)
        # Slash format (e.g. 17/804,535)
        for n in re.findall(r'\b(\d{2}/\d{3}[,]?\d{3})\b', section):
            c = n.replace(",","")
            if _norm(c) != excl:
                ref_apps.append(c)

    # ── Strategy 2: patent number reference section ───────────────────────────
    # TD forms may use several headers: "prior patent number(s)", "Patent #",
    # "Patent No", "patent number(s)", etc.  Try each marker in order.
    _PAT_SECTION_MARKERS = [
        r'prior\s+patent\s+number',
        r'patent\s+number\(s\)',
        r'Patent\s*#',
        r'Patent\s+No\.?',
        r'prior\s+patent',   # broad fallback
    ]
    for _marker in _PAT_SECTION_MARKERS:
        pat_match = re.search(_marker, text, re.IGNORECASE)
        if not pat_match:
            continue
        section = text[pat_match.start():]
        stop = re.search(
            r'In\s+making\s+the\s+above|as\s+the\s+term\s+of\s+said|'
            r'the\s+owner\s+hereby\s+agrees|This\s+agreement\s+runs',
            section, re.IGNORECASE
        )
        if stop:
            section = section[:stop.start()]
        # Match: plain 6-8 digit numbers, or comma-formatted US patents e.g. 9,914,979
        cands = re.findall(r'(?:US\s*)?(\d{1,3}(?:,\d{3})+|\d{6,8})', section)
        for n in cands:
            clean = re.sub(r'[,\s]', '', n)
            if 6 <= len(clean) <= 8 and _norm(clean) != excl:
                ref_patents.append(clean)
        if ref_patents:
            break   # found something with this marker, stop trying others

    # ── Strategy 3: fallback — find ALL 8-digit app nums, exclude subject ────
    # Only used if strategies 1+2 found nothing.
    if not ref_apps and not ref_patents:
        all_8 = re.findall(r'\b(1[0-9]\d{6}|2[0-9]\d{5})\b', text)
        seen_counts: dict = {}
        for n in all_8:
            seen_counts[n] = seen_counts.get(n, 0) + 1
        for n in all_8:
            if _norm(n) != excl and n not in ref_apps:
                ref_apps.append(n)

    # ── Strategy 3b: fallback for 7-digit patent numbers ─────────────────────
    # If we still found nothing, look for any 7-digit number (US patent format).
    if not ref_apps and not ref_patents:
        all_7 = re.findall(r'\b(\d{7})\b', text)
        for n in all_7:
            if _norm(n) != excl and n not in ref_patents:
                ref_patents.append(n)

    # Deduplicate preserving order
    seen: set = set()
    ref_apps    = [x for x in ref_apps    if not (x in seen or seen.add(x))]
    seen = set()
    ref_patents = [x for x in ref_patents if not (x in seen or seen.add(x))]

    return ref_apps, ref_patents



def resolve_td_chain_full(start_app_num, max_depth=6):
    """
    Recursively trace the full TD disclaimer tree from start_app_num.

    When a TD references multiple parent applications (e.g. two co-pending apps),
    ALL branches are traced and the cap date = earliest leaf expiry.

    Returns:
    {
      "tree":  { node with nested "children" list },
      "chain": [ flat list along cap-determining path, for backwards compat ],
      "final_cap_date": str|None,
      "resolved": bool,
      "note": str,
    }
    Each node:
      { app_num, patent_num, filing_date, expiration_date, pta_days,
        has_td, is_root, td_ref, td_refs_all, td_ref_expiries,
        resolution_method, label, children }
    """
    notes = []

    def _ref_mode(ref_str):
        c = ref_str.replace(",","").replace(" ","").replace("/","").lstrip("US")
        if c.isdigit() and len(c) == 7: return "pat"
        if c.isdigit() and len(c) == 8:
            # 8-digit numbers: modern USPTO app series start at 10–19, 29.
            # Patent numbers crossed 10,000,000 in 2018 and are now ~12.7M.
            # Numbers starting with 10–13 are ambiguous; prefer "pat" for those
            # since app series 10–13 are long-closed and unlikely in new TDs.
            # Series 14+ are unambiguous application numbers.
            prefix = int(c[:2])
            if prefix <= 13:
                return "pat"   # likely an 8-digit patent (10M–13.9M range)
            return "app"       # series 14/15/16/17/18/19/29 → application
        return "app"

    def _compute_node_expiry(d):
        a_res   = compute_a_delay(d)
        b_res   = compute_b_delay(d)
        c_res   = compute_c_delay(d)
        ov_res  = compute_overlap(a_res, b_res)
        app_del = compute_applicant_delay_forensic(d)
        total_app_delay = sum(x["delay_days"] for x in app_del)
        non_ov  = (a_res["total_days"] + b_res["total_days"]
                   + c_res["total_days"] - ov_res["total_days"])
        total_pta = max(0, non_ov - total_app_delay)
        official_pta = d["pta_raw"].get("adjustmentTotalQuantity") or 0
        if official_pta > 0:
            total_pta = official_pta
        filing   = d["filing_date"]
        exp_date = (add_years(filing, 20) + dt.timedelta(days=total_pta)) if filing else None
        return filing, exp_date, total_pta

    def _trace(cur_ref, cur_mode, visited, depth):
        """Recursively build a tree node for cur_ref."""
        if depth >= max_depth:
            return None
        clean_key = cur_ref.replace("/","").replace(",","").replace(" ","").upper()
        if clean_key in visited:
            notes.append(f"Circular reference at {cur_ref} — stopping branch.")
            return None
        visited = visited | {clean_key}   # immutable copy per branch

        # ── Fetch USPTO record ───────────────────────────────────────────────
        # fetch_app_by_patent_number returns a simplified summary dict, NOT
        # the full record needed by extract_all_events / extract_terminal_disclaimers.
        # So for patent-number mode: resolve to app number first, then fetch full record.
        try:
            if cur_mode == "pat":
                pat_info = fetch_app_by_patent_number(cur_ref)
                if not pat_info or not isinstance(pat_info, dict):
                    notes.append(f"No USPTO record for patent {cur_ref}.")
                    return None
                resolved_app = pat_info.get("app_num", "")
                if not resolved_app:
                    notes.append(f"Patent {cur_ref} found but no app number returned.")
                    return None
                record = fetch_application(resolved_app)
            else:
                record = fetch_application(cur_ref)
            if not record:
                notes.append(f"No USPTO record for {cur_ref}.")
                return None
        except Exception as e:
            notes.append(f"Error fetching {cur_ref}: {e}")
            return None

        d    = extract_all_events(record)
        meta = record.get("applicationMetaData", {}) or {}
        filing, exp_date, total_pta = _compute_node_expiry(d)

        node_app = meta.get("applicationNumberText", cur_ref)
        node_pat = meta.get("patentNumber", "")

        # ── Check for active TDs ─────────────────────────────────────────────
        td_list    = extract_terminal_disclaimers(record)
        active_tds = [t for t in td_list
                      if not t.get("withdrawn") and not t.get("possibly_withdrawn")]

        node = {
            "app_num":           node_app,
            "patent_num":        node_pat,
            "filing_date":       str(filing)   if filing   else None,
            "expiration_date":   str(exp_date) if exp_date else None,
            "pta_days":          total_pta,
            "has_td":            bool(active_tds),
            "is_root":           not bool(active_tds),
            "td_ref":            None,
            "td_refs_all":       [],
            "td_ref_expiries":   {},
            "resolution_method": None,
            "label":             f"Patent {node_pat}" if node_pat else f"App {node_app}",
            "children":          [],
        }

        if not active_tds:
            return node   # leaf / root

        # ── Find parent refs ─────────────────────────────────────────────────
        all_refs      = []
        found_mode    = "app"
        res_method    = None

        # Step 1: description text (no network)
        # Use the already-classified ref_patent / ref_app fields directly.
        # Do NOT re-infer mode with _ref_mode() — modern 8-digit patent numbers
        # (e.g. 12,652,530) would be misclassified as application numbers.
        for td in active_tds:
            ref_pat_val = td.get("ref_patent")
            ref_app_val = td.get("ref_app")
            ref = ref_pat_val or ref_app_val
            if ref:
                all_refs   = [ref]
                found_mode = "pat" if ref_pat_val else "app"
                res_method = "description"
                notes.append(f"{node_app}: ref '{ref}' (mode={found_mode}) from TD description.")
                break

        # Step 2: TD PDF → text → OCR
        if not all_refs:
            try:
                docs_result = fetch_docs_and_transactions(node_app)
                docs = docs_result.get("documents", []) if isinstance(docs_result, dict) else []
            except Exception:
                docs = []
            td_docs = [doc for doc in docs
                       if (doc.get("code") or "").strip().upper() in _TD_PDF_CODES
                       and doc.get("identifier")]
            for td_doc in td_docs:
                doc_id    = td_doc["identifier"]
                pdf_bytes = _download_td_pdf_bytes(node_app, doc_id)
                if not pdf_bytes:
                    continue
                text, used_ocr = _extract_text_from_pdf_bytes(pdf_bytes)
                ref_apps, ref_pats = _parse_refs_from_td_text(text, exclude_app_num=node_app)
                extracted = ref_apps if ref_apps else ref_pats
                if extracted:
                    all_refs   = list(extracted)
                    found_mode = "pat" if (not ref_apps and ref_pats) else "app"
                    res_method = "ocr" if used_ocr else "pdf_text"
                    notes.append(
                        f"{node_app}: {len(all_refs)} ref(s) from PDF "
                        f"({'OCR' if used_ocr else 'text'}): {', '.join(all_refs)}."
                    )
                    break

        # Step 3: USPTO continuity
        if not all_refs:
            parent_apps = fetch_parent_applications(node_app)
            if parent_apps:
                ref_pat_c = parent_apps[0].get("patent_num")
                ref_app_c = parent_apps[0].get("app_num")
                ref = ref_pat_c or ref_app_c
                if ref:
                    all_refs   = [ref]
                    found_mode = "pat" if ref_pat_c else "app"
                    res_method = "continuity"
                    notes.append(f"{node_app}: ref '{ref}' (mode={found_mode}) from USPTO continuity.")

        if not all_refs:
            notes.append(f"{node_app}: all resolution methods failed.")
            node["has_td"] = True
            return node

        # ── Recursively trace EVERY ref → build children ─────────────────────
        children = []
        for ref in all_refs:
            child = _trace(ref, found_mode, visited, depth + 1)
            if child:
                children.append(child)

        # Cap-determining child = earliest expiry among all children
        valid_children = [c for c in children if c.get("expiration_date")]
        if valid_children:
            cap_child = min(valid_children, key=lambda c: c["expiration_date"])
            cap_ref   = cap_child.get("app_num") or cap_child.get("patent_num")
        else:
            cap_ref = all_refs[0]

        ref_expiries = {
            (c.get("app_num") or c.get("patent_num")): c.get("expiration_date")
            for c in children
        }

        node["td_ref"]          = cap_ref
        node["td_refs_all"]     = all_refs
        node["td_ref_expiries"] = ref_expiries
        node["resolution_method"] = res_method
        node["children"]        = children
        return node

    # ── Build the tree ───────────────────────────────────────────────────────
    tree = _trace(str(start_app_num).strip(), "app", set(), 0)

    if not tree:
        return {"tree": None, "chain": [], "final_cap_date": None,
                "resolved": False, "note": "Could not fetch USPTO record."}

    # ── Collect all leaf nodes ───────────────────────────────────────────────
    def _leaves(node):
        if not node.get("children"):
            return [node]
        result = []
        for c in node["children"]:
            result.extend(_leaves(c))
        return result

    leaves      = _leaves(tree)
    leaf_exps   = [l["expiration_date"] for l in leaves if l.get("expiration_date")]
    final_cap   = min(leaf_exps) if leaf_exps else None

    # ── Build a flat chain along the cap-determining path ─────────────────────
    # Starting at the root, follow the child with the earliest expiration_date
    # at each level — that branch is what sets the terminal-disclaimer cap.
    chain = []
    node  = tree
    while node:
        chain.append({
            "app_num":           node.get("app_num"),
            "patent_num":        node.get("patent_num"),
            "filing_date":       node.get("filing_date"),
            "expiration_date":   node.get("expiration_date"),
            "pta_days":          node.get("pta_days"),
            "has_td":            node.get("has_td"),
            "is_root":           node.get("is_root"),
            "td_ref":            node.get("td_ref"),
            "td_refs_all":       node.get("td_refs_all"),
            "resolution_method": node.get("resolution_method"),
            "label":             node.get("label"),
        })
        children = node.get("children") or []
        valid    = [c for c in children if c.get("expiration_date")]
        if valid:
            node = min(valid, key=lambda c: c["expiration_date"])
        elif children:
            node = children[0]
        else:
            node = None

    resolved = final_cap is not None
    note     = " ".join(notes) if notes else "TD chain resolved."

    return {
        "tree":           tree,
        "chain":          chain,
        "final_cap_date": final_cap,
        "resolved":       resolved,
        "note":           note,
    }