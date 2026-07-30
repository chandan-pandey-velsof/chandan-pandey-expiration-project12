#!/usr/bin/env python3
"""EaseIP Intelligent PTA Calculator — Flask Backend v3"""
from flask import Flask,render_template,request,redirect,url_for,session,jsonify,send_file
import json,os,hashlib,datetime,io,sys
sys.path.insert(0,os.path.dirname(__file__))
from pta_engine import (fetch_application,fetch_docs_and_transactions,extract_all_events,compute_a_delay,compute_b_delay,
    compute_c_delay,compute_overlap,compute_applicant_delay_forensic,
    compute_maintenance_and_expiration,extract_terminal_disclaimers,resolve_td_expiration,
    fetch_parent_applications,fetch_app_by_patent_number,fetch_expiration_for_patent,
    parse_date,add_months,add_years,resolve_td_chain_full)
import datetime as dt
app=Flask(__name__)
app.secret_key="easeip-pta-secret-2026-xk9"
BASE=os.path.dirname(__file__)
USERS_FILE=os.path.join(BASE,"users.json")
PEND_FILE=os.path.join(BASE,"pending_users.json")
HIST_FILE=os.path.join(BASE,"history.json")
CONFIG_FILE=os.path.join(BASE,"config.json")
def load_config():
    try:
        with open(CONFIG_FILE) as f: return json.load(f)
    except: return {}
def save_config(data):
    with open(CONFIG_FILE,"w") as f: json.dump(data,f,indent=2)

def load_json(path):
    try:
        with open(path) as f:
            data=json.load(f)
            return data if isinstance(data,dict) else {}
    except:return {}
def save_json_file(path,data):
    with open(path,"w") as f:json.dump(data,f,indent=2)
def sha256(s):return hashlib.sha256(s.encode()).hexdigest()
def has_admin():
    users=load_json(USERS_FILE)
    return any(u.get("role")=="admin" for u in users.values())
def login_required(f):
    from functools import wraps
    @wraps(f)
    def d(*a,**k):
        if "user" not in session:return redirect(url_for("login"))
        return f(*a,**k)
    return d
def admin_required(f):
    from functools import wraps
    @wraps(f)
    def d(*a,**k):
        if "user" not in session:return redirect(url_for("login"))
        if session.get("role")!="admin":return redirect(url_for("dashboard"))
        return f(*a,**k)
    return d
@app.route("/")
def index():
    if not has_admin():return redirect(url_for("setup"))
    if "user" not in session:return redirect(url_for("login"))
    return redirect(url_for("dashboard"))
@app.route("/home")
def home():return redirect(url_for("index"))
@app.route("/setup",methods=["GET","POST"])
def setup():
    if has_admin():return redirect(url_for("login"))
    error=None
    if request.method=="POST":
        u=request.form.get("username","").strip()
        e=request.form.get("email","").strip()
        p=request.form.get("password","")
        p2=request.form.get("confirm_password","")
        if not u or not e or not p:error="All fields required."
        elif p!=p2:error="Passwords do not match."
        else:
            save_json_file(USERS_FILE,{u:{"email":e,"password":sha256(p),"role":"admin"}})
            return redirect(url_for("login")+"?setup=done")
    return render_template("setup.html",error=error)
@app.route("/login",methods=["GET","POST"])
def login():
    if not has_admin():return redirect(url_for("setup"))
    setup_done=request.args.get("setup")=="done"
    error=None;req_success=None
    if request.method=="POST":
        action=request.form.get("action","login")
        if action=="login":
            ident=request.form.get("identifier","").strip()
            pw=request.form.get("password","")
            users=load_json(USERS_FILE);matched=None
            for uname,udata in users.items():
                if(uname==ident or udata.get("email")==ident)and udata["password"]==sha256(pw):
                    matched=(uname,udata);break
            if matched:
                session["user"]=matched[0];session["role"]=matched[1]["role"]
                return redirect(url_for("dashboard"))
            else:error="Invalid username/email or password."
        elif action=="request":
            uname=request.form.get("req_username","").strip()
            email=request.form.get("req_email","").strip()
            pw=request.form.get("req_password","")
            pw2=request.form.get("req_confirm","")
            if not uname or not email or not pw:error="All fields required."
            elif pw!=pw2:error="Passwords do not match."
            else:
                users=load_json(USERS_FILE);pending=load_json(PEND_FILE)
                if uname in users or uname in pending:error="Username already taken."
                else:
                    pending[uname]={"email":email,"password":sha256(pw),
                        "requested_at":datetime.datetime.now().strftime("%Y-%m-%d %H:%M")}
                    save_json_file(PEND_FILE,pending)
                    req_success=f"Request submitted for '{uname}'."
    return render_template("login.html",error=error,setup_done=setup_done,req_success=req_success)
@app.route("/logout")
def logout():session.clear();return redirect(url_for("login"))
@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html",username=session["user"],role=session.get("role","user"))
@app.route("/analyze",methods=["POST"])
@login_required
def analyze():
    data=request.get_json();app_num=data.get("app_num","").strip()
    if not app_num:return jsonify({"error":"No application number."}),400
    try:
        record=fetch_application(app_num);d=extract_all_events(record)

        # ── Design patent guard: series 29 (29/xxx,xxx) or D-prefixed patent number
        # are NOT eligible for PTA under 35 U.S.C. 154(b). Zero out all delays.
        _pat_num   = d["meta"].get("patentNumber","") or ""
        _clean_app = app_num.replace("/","").replace(",","").replace(" ","")
        _is_design = _clean_app.startswith("29") or _pat_num.upper().startswith("D")

        a_res=compute_a_delay(d);b_res=compute_b_delay(d);c_res=compute_c_delay(d)
        overlap_res=compute_overlap(a_res,b_res);app_delay_forensic=compute_applicant_delay_forensic(d)
        total_app_delay=sum(x["delay_days"] for x in app_delay_forensic)
        non_ov=a_res["total_days"]+b_res["total_days"]+c_res["total_days"]-overlap_res["total_days"]
        total_pta=max(0,non_ov-total_app_delay)

        if _is_design:
            # Design patents have no PTA — override everything to zero
            a_res={"total_days":0,"forensic":[],"windows":[],"merged":[]}
            b_res={"total_days":0,"forensic":[],"windows":[],"merged":[]}
            c_res={"total_days":0,"forensic":[],"windows":[],"merged":[]}
            overlap_res={"total_days":0,"intervals":[]}
            app_delay_forensic=[];total_app_delay=0;non_ov=0;total_pta=0

        # ── Terminal Disclaimer Detection & Auto-Resolution ────
        td_list   = extract_terminal_disclaimers(record)
        parent_apps = []
        td_result   = None
        if td_list:
            # Pass 1: try to resolve from description text
            td_result = resolve_td_expiration(td_list)
            # Pass 2: if still unresolved, fetch parent apps from continuity data
            if not td_result["resolved"]:
                parent_apps = fetch_parent_applications(app_num)
                if parent_apps:
                    td_result = resolve_td_expiration(td_list, parent_apps=parent_apps)
        td_cap = td_result["td_cap_date"] if td_result else None

        maint_exp=compute_maintenance_and_expiration(grant_date=d["grant_date"],
            filing_date=d["filing_date"],total_pta_days=total_pta,
            terminal_disclaimer_date=td_cap)
        pta_raw=d["pta_raw"];commencement=d.get("commencement_date")
        filing=d["filing_date"];grant=d["grant_date"];noa=d["noa_date"]
        _a1=commencement or filing;_b=commencement or filing
        date_table=[]
        def add_dt(date,role,name,note=""):
            if date:date_table.append({"date":str(date),"role":role,"name":name,"note":note})
        add_dt(filing,"Core","Filing Date","Start of all PTA clocks (A, B, C)")
        if commencement:add_dt(commencement,"Core","Commencement Date (PCT/371)","B-clock start")
        add_dt(add_months(_a1,14),"A-Delay","14-Month Deadline (A1)","USPTO must mail 1st OA by this date")
        add_dt(add_years(_b,3),"B-Delay","3-Year Deadline (B)","USPTO must issue patent by this date")
        for i,oa in enumerate(d["mctnf_dates"]):add_dt(oa,"A-Delay",f"Non-Final OA #{i+1} (MCTNF)","Triggers 4-month A2 clock")
        for i,oa in enumerate(d["mctfr_dates"]):add_dt(oa,"A-Delay",f"Final OA #{i+1} (MCTFR)","Triggers 4-month A2 clock")
        for i,r in enumerate(d["resp_nonfinal"]):add_dt(r,"Applicant",f"Response to Non-Final #{i+1}","Starts 4-month A2 clock")
        for i,r in enumerate(d["resp_final"]):add_dt(r,"Applicant",f"Response to Final #{i+1}","Starts 4-month A2 clock")
        for i,x in enumerate(d["xt_granted"]):add_dt(x,"Applicant Delay",f"Extension of Time #{i+1}","37 CFR 1.704(c)(8)")
        for i,ids in enumerate(d["ids_dates"]):
            role="Applicant Delay" if noa and ids>noa else "Applicant"
            add_dt(ids,role,f"IDS Filed #{i+1}","After NOA=applicant delay" if noa and ids>noa else "Before NOA")
        for i,rce in enumerate(d["rce_dates"]):add_dt(rce,"RCE",f"RCE Filed #{i+1}","Resets B-delay clock")
        for i,af in enumerate(d["after_final_amend"]):add_dt(af,"Applicant",f"After-Final Amendment #{i+1}","May cause delay")
        for i,sr in enumerate(d["suppl_replies"]):add_dt(sr,"Applicant",f"Supplemental Reply #{i+1}","May cause delay")
        add_dt(noa,"A-Delay","Notice of Allowance (NOA)","Triggers 4-month A3 clock")
        add_dt(d["issue_fee_date"],"Applicant","Issue Fee Payment","Must be paid within 3 months")
        add_dt(grant,"Core","Grant Date","End of all PTA clocks")
        add_dt(d.get("ptac_date"),"Core","PTA Issue Date (PTAC)","Used in PTA calculation")
        date_table.sort(key=lambda x:x["date"])
        seen=set();unique_dates=[]
        for item in date_table:
            key=(item["date"],item["name"])
            if key not in seen:seen.add(key);unique_dates.append(item)
        maint_json=None
        if maint_exp:
            maint_json={"filing_date":str(maint_exp["filing_date"]),"grant_date":str(maint_exp["grant_date"]),
                "total_pta_days":maint_exp["total_pta_days"],"base_expiration":str(maint_exp["base_expiration"]),
                "expiration_with_pta":str(maint_exp["expiration_with_pta"]),
                "effective_expiration":str(maint_exp["effective_expiration"]),
                "td_applied":maint_exp.get("td_applied",False),
                "td_cap_date":str(maint_exp["td_cap_date"]) if maint_exp.get("td_cap_date") else None,
                "td_days_forfeited":maint_exp.get("td_days_forfeited",0),
                "maintenance_fees":[{"fee_number":mf["fee_number"],"base_years":mf["base_years"],
                    "window_open":str(mf["window_open"]),"window_close":str(mf["window_close"]),
                    "grace_period_end":str(mf["grace_period_end"]),"label":mf["label"],
                    "td_relevance":mf.get("td_relevance","needed"),
                    "td_note":mf.get("td_note","")}
                    for mf in maint_exp["maintenance_fees"]]}

        # ── Serialize TD result ────
        def ser_td(td_r, p_apps=None):
            if not td_r:
                return {"detected":False,"active_count":0,"resolved":False,
                        "resolution_method":None,"parent_apps_tried":[],
                        "td_cap_date":None,"td_ref_patent":None,"td_ref_app":None,
                        "td_chain":[],"note":"No terminal disclaimers detected.","raw_tds":[],
                        "parent_apps":[]}
            raw = []
            for td in (td_r.get("raw_tds") or []):
                raw.append({
                    "date":        str(td.get("date","")) if td.get("date") else None,
                    "doc_code":    td.get("doc_code",""),
                    "description": td.get("description",""),
                    "ref_patent":  td.get("ref_patent"),
                    "ref_app":     td.get("ref_app"),
                    "withdrawn":   td.get("withdrawn",False),
                    "possibly_withdrawn": td.get("possibly_withdrawn",False),
                })
            pa_out = [{"app_num":p.get("app_num",""),"patent_num":p.get("patent_num",""),
                       "relation":p.get("relation",""),"filing_date":p.get("filing_date",""),
                       "status":p.get("status","")} for p in (p_apps or [])]
            return {
                "detected":          True,
                "active_count":      len(td_r.get("raw_tds") or []),
                "resolved":          td_r.get("resolved",False),
                "resolution_method": td_r.get("resolution_method"),
                "parent_apps_tried": td_r.get("parent_apps_tried",[]),
                "td_cap_date":       str(td_r["td_cap_date"]) if td_r.get("td_cap_date") else None,
                "td_ref_patent":     td_r.get("td_ref_patent"),
                "td_ref_app":        td_r.get("td_ref_app"),
                "td_chain":          td_r.get("td_chain",[]),
                "note":              td_r.get("note",""),
                "raw_tds":           raw,
                "parent_apps":       pa_out,
            }
        td_json = ser_td(td_result, parent_apps) if td_list else ser_td(None)
        def ser(items):
            return [{k:str(v) if isinstance(v,(dt.date,dt.datetime)) else v for k,v in item.items()} for item in items]
        pta_hist=[{"seq":h.get("eventSequenceNumber",""),"date":str(h.get("eventDate",""))[:10],
            "uspto_delay":h.get("ipOfficeDayDelayQuantity",0),"app_delay":h.get("applicantDayDelayQuantity",0),
            "desc":h.get("eventDescriptionText","")}
            for h in sorted(d["pta_history"],key=lambda x:x.get("eventSequenceNumber",0))]
        result={"app_num":app_num,"patent_number":d["meta"].get("patentNumber","N/A"),
            "title":d["meta"].get("inventionTitle","N/A"),"applicant":d["meta"].get("firstApplicantName","N/A"),
            "examiner":d["meta"].get("examinerNameText","N/A"),
            "filing_date":str(filing) if filing else None,"grant_date":str(grant) if grant else None,
            "noa_date":str(noa) if noa else None,
            "issue_fee_date":str(d["issue_fee_date"]) if d["issue_fee_date"] else None,
            "commencement_date":str(commencement) if commencement else None,
            "ptac_date":str(d.get("ptac_date")) if d.get("ptac_date") else None,
            "a_delay":{"total_days":a_res["total_days"],"forensic":ser(a_res["forensic"]),
                "merged":[{"start":str(s),"end":str(e),"days":(e-s).days} for s,e in a_res["merged"]]},
            "b_delay":{"total_days":b_res["total_days"],"forensic":ser(b_res["forensic"])},
            "c_delay":{"total_days":c_res["total_days"],"forensic":ser(c_res["forensic"])},
            "overlap":{"total_days":overlap_res["total_days"],
                "intervals":[{"start":str(s),"end":str(e),"days":(e-s).days} for s,e in overlap_res["intervals"]]},
            "applicant_delay":{"total_calculated":total_app_delay,
                "total_uspto":pta_raw.get("applicantDayDelayQuantity",0),"forensic":ser(app_delay_forensic)},
            "totals":{"a_days":a_res["total_days"],"b_days":b_res["total_days"],"c_days":c_res["total_days"],
                "overlap_days":overlap_res["total_days"],"non_overlap":non_ov,"app_delay":total_app_delay,"total_pta":total_pta},
            "uspto_official":{"a_delay":pta_raw.get("aDelayQuantity",0),"b_delay":pta_raw.get("bDelayQuantity",0),
                "c_delay":pta_raw.get("cDelayQuantity",0),"overlap":pta_raw.get("overlappingDayQuantity",0),
                "non_overlap":pta_raw.get("nonOverlappingDayDelayQuantity",0),
                "app_delay":pta_raw.get("applicantDayDelayQuantity",0),"total_pta":pta_raw.get("adjustmentTotalQuantity",0)},
            "pta_history":pta_hist,"maintenance":maint_json,"date_table":unique_dates,
            "raw_events":{"mctnf_count":len(d["mctnf_dates"]),"mctfr_count":len(d["mctfr_dates"]),
                "resp_nonfinal_count":len(d["resp_nonfinal"]),"resp_final_count":len(d["resp_final"]),
                "rce_count":len(d["rce_dates"]),"ids_count":len(d["ids_dates"]),"xt_count":len(d["xt_granted"])},
            "terminal_disclaimer": td_json}
        hist=load_json(HIST_FILE);user=session["user"]
        ulist=[x for x in hist.get(user,[]) if x["app_num"]!=app_num]
        ulist.insert(0,{"app_num":app_num,"date":datetime.date.today().isoformat(),
            "title":result["title"][:60] if result["title"]!="N/A" else ""})
        hist[user]=ulist[:30];save_json_file(HIST_FILE,hist)
        return jsonify(result)
    except Exception as e:return jsonify({"error":str(e)}),500
@app.route("/history")
@login_required
def history():
    return jsonify(load_json(HIST_FILE).get(session["user"],[]))
@app.route("/history/delete/<int:idx>",methods=["POST"])
@login_required
def history_delete(idx):
    hist=load_json(HIST_FILE);user=session["user"];ulist=hist.get(user,[])
    if 0<=idx<len(ulist):ulist.pop(idx);hist[user]=ulist;save_json_file(HIST_FILE,hist)
    return jsonify({"ok":True})
@app.route("/manual_calc")
@login_required
def manual_calc():
    return render_template("calculator.html",
        filing=request.args.get("filing",""),grant=request.args.get("grant",""),
        pta=request.args.get("pta","0"))
@app.route("/download_excel")
@login_required
def download_excel():
    return send_file(os.path.join(BASE,"static","patent_term_calculator.xls"),
        as_attachment=False,download_name="patent_term_calculator.xls")
@app.route("/download_report",methods=["POST"])
@login_required
def download_report():
    result=request.get_json().get("result",{})
    t=result.get("totals",{});u=result.get("uspto_official",{});m=result.get("maintenance")
    W=72;s=f"\n{'═'*W}\n  USPTO PTA FORENSIC REPORT — EaseIP Pvt Ltd\n{'═'*W}\n"
    for l,v in [("Application",result.get("app_num","")),("Patent No.",result.get("patent_number","")),
        ("Title",result.get("title","")),("Filing Date",result.get("filing_date","")),
        ("Grant Date",result.get("grant_date",""))]:s+=f"    {l:<30}: {v}\n"
    s+=f"\n  TOTAL PTA (Calculated): {t.get('total_pta',0)} days\n"
    s+=f"  TOTAL PTA (USPTO):      {u.get('total_pta',0)} days\n"
    diff=t.get("total_pta",0)-u.get("total_pta",0);s+=f"  Difference:             {diff:+d} days\n"
    if m:
        exp_show = m.get('effective_expiration') or m.get('expiration_with_pta','')
        s+=f"\n  EXPIRATION DATE: {exp_show}\n"
        if m.get('td_applied'):
            s+=f"  (Terminal Disclaimer applied — capped at {m.get('td_cap_date','')})\n"
            s+=f"  Uncapped expiration would have been: {m.get('expiration_with_pta','')}\n"
    s+=f"\n{'═'*W}\n  © 2026 EaseIP Pvt Ltd\n{'═'*W}\n"
    buf=io.BytesIO(s.encode("utf-8"));buf.seek(0)
    return send_file(buf,as_attachment=True,
        download_name=f"PTA_{result.get('app_num','').replace('/','_')}.txt",mimetype="text/plain")
@app.route("/admin/manage")
@admin_required
def admin_manage():
    return render_template("admin.html",users=load_json(USERS_FILE),
        pending=load_json(PEND_FILE),current_user=session["user"])
@app.route("/admin/approve/<uid>")
@admin_required
def admin_approve(uid):
    pending=load_json(PEND_FILE);users=load_json(USERS_FILE)
    if uid in pending:users[uid]={**pending.pop(uid),"role":"user"};save_json_file(USERS_FILE,users);save_json_file(PEND_FILE,pending)
    return redirect(url_for("admin_manage"))
@app.route("/admin/deny/<uid>")
@admin_required
def admin_deny(uid):
    pending=load_json(PEND_FILE)
    if uid in pending:del pending[uid];save_json_file(PEND_FILE,pending)
    return redirect(url_for("admin_manage"))
@app.route("/admin/delete_user/<uid>")
@admin_required
def admin_delete_user(uid):
    if uid==session["user"]:return redirect(url_for("admin_manage"))
    users=load_json(USERS_FILE)
    if uid in users:del users[uid];save_json_file(USERS_FILE,users)
    return redirect(url_for("admin_manage"))
@app.route("/admin/set_role/<uid>/<role>")
@admin_required
def admin_set_role(uid,role):
    if role not in("admin","user"):return redirect(url_for("admin_manage"))
    users=load_json(USERS_FILE)
    if uid in users:users[uid]["role"]=role;save_json_file(USERS_FILE,users)
    return redirect(url_for("admin_manage"))
@app.route("/admin/create_user",methods=["POST"])
@admin_required
def admin_create_user():
    uname=request.form.get("username","").strip();email=request.form.get("email","").strip()
    pw=request.form.get("password","");pw2=request.form.get("confirm_password","")
    role=request.form.get("role","user");users=load_json(USERS_FILE);error=None
    if not uname or not email or not pw:error="All fields required."
    elif pw!=pw2:error="Passwords do not match."
    elif uname in users:error=f"Username '{uname}' already exists."
    else:users[uname]={"email":email,"password":sha256(pw),"role":role};save_json_file(USERS_FILE,users)
    return render_template("admin.html",users=users,pending=load_json(PEND_FILE),
        current_user=session["user"],create_error=error)
@app.route("/resolve_expiration/<path:ref_num>")
@login_required
def resolve_expiration(ref_num):
    """
    Given an application number OR patent number, fetch the USPTO record,
    compute its expiration date (filing + 20yr + PTA), and return it.
    Used by the manual calculator to auto-fill the TD cap date.
    Optional ?mode=pat  → search by patent number only (default: app number first).
    """
    mode = request.args.get("mode", "app")
    if mode not in ("app", "pat"):
        mode = "app"
    try:
        result = fetch_expiration_for_patent(ref_num.strip(), mode=mode)
        if result is None:
            msg = (f"No issued patent found for '{ref_num}'."
                   if mode == "pat"
                   else (f"No record found for '{ref_num}'. "
                         "Accepted formats: application number (e.g. 15631946 or 17/449,797) "
                         "or patent number (e.g. 10,123,456 or US10123456)."))
            return jsonify({"error": msg}), 404

        return jsonify({
            "app_num":        result.get("app_num",""),
            "patent_num":     result.get("patent_num",""),
            "filing_date":    str(result["filing_date"]) if result.get("filing_date") else None,
            "grant_date":     str(result["grant_date"])  if result.get("grant_date")  else None,
            "total_pta":      result.get("total_pta",0),
            "expiration_date":str(result["expiration_date"]) if result.get("expiration_date") else None,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/search_by_patent/<path:patent_num>")
@login_required
def search_by_patent(patent_num):
    """Resolve a USPTO patent (grant) number to its application number."""
    try:
        info = fetch_app_by_patent_number(patent_num)
        return jsonify(info)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/parent_apps/<path:app_num>")
@login_required
def parent_apps_route(app_num):
    """Return parent/continuity applications for the given app number (for frontend use)."""
    try:
        apps = fetch_parent_applications(app_num)
        return jsonify({"app_num": app_num, "parent_apps": apps, "count": len(apps)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/docs_transactions/<path:app_num>")
@login_required
def docs_transactions(app_num):
    try:
        result = fetch_docs_and_transactions(app_num)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/proxy_doc/<path:app_num>/<doc_id>")
@login_required
def proxy_doc(app_num, doc_id):
    """Proxy USPTO ODP document download — injects API key server-side."""
    import requests as req_lib
    from flask import Response
    from pta_engine import API_KEY

    clean    = app_num.replace("/","").replace(",","").replace(" ","")
    fmt      = request.args.get("fmt", "pdf")   # pdf | xml
    headers  = {"x-api-key": API_KEY}

    if fmt == "xml":
        url = f"https://api.uspto.gov/api/v1/download/applications/{clean}/{doc_id}/xmlarchive"
    elif fmt == "docx":
        url = f"https://api.uspto.gov/api/v1/download/applications/{clean}/{doc_id}.docx"
    else:
        url = f"https://api.uspto.gov/api/v1/download/applications/{clean}/{doc_id}.pdf"

    try:
        upstream = req_lib.get(url, headers=headers, stream=True,
                               allow_redirects=True, timeout=60)
        if upstream.status_code == 404:
            return jsonify({"error": "Document not found on USPTO servers."}), 404
        if upstream.status_code == 403:
            return jsonify({"error": "Access denied by USPTO. API key may be invalid."}), 403
        upstream.raise_for_status()

        # Always force application/pdf so browser renders inline
        # Determine content type and filename by format
        force_dl = request.args.get("dl", "0") == "1"
        if fmt == "xml":
            ct    = "application/xml"
            fname = f"{doc_id}.xml"
            disp  = f'attachment; filename="{fname}"'
        elif fmt == "docx":
            ct    = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            fname = f"{doc_id}.docx"
            disp  = f'attachment; filename="{fname}"'
        else:
            ct    = "application/pdf"
            fname = f"{doc_id}.pdf"
            disp  = f'attachment; filename="{fname}"' if force_dl else f'inline; filename="{fname}"'

        file_bytes = upstream.content
        resp = Response(file_bytes, status=200, content_type=ct)
        resp.headers["Content-Disposition"]    = disp
        resp.headers["Content-Length"]         = str(len(file_bytes))
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ─── EXPIRATION TOOL ─────────────────────────────────────────────────────────

# Internal API key for tool-to-tool calls (set or override via env)
_cfg = load_config()
INTERNAL_EXP_API_KEY = _cfg.get("api_key") or os.environ.get("EASEIP_EXP_API_KEY", "easeip-exp-api-2026")


def _compute_expiration_for_ref(ref, mode="app"):
    """
    Core logic shared by /expiration_analyze (browser) and /api/expiration (API).
    Accepts app number or patent number string.
    mode: "app"  → search application number first (default)
          "pat"  → search patent number only (skip app number search)
    Returns a dict ready to jsonify, or raises ValueError/Exception.
    """
    import requests as req_lib
    from pta_engine import API_KEY as USPTO_KEY, BASE_URL as USPTO_URL

    clean = ref.replace("/","").replace(",","").replace(" ","").upper()
    if clean.startswith("US"):
        clean = clean[2:]

    hdrs = {"x-api-key": USPTO_KEY, "Content-Type": "application/json"}

    def _fetch(q):
        # No "fields" restriction — USPTO omits applicationNumberText from
        # applicationMetaData when a fields filter is applied to patent number
        # searches. Returning all fields ensures app_num is always available.
        r = req_lib.post(USPTO_URL, headers=hdrs, timeout=30, json={
            "q": q, "pagination": {"limit": 1}
        })
        if r.status_code != 200: return None
        bag = r.json().get("patentFileWrapperDataBag", [])
        return bag[0] if bag else None

    if mode == "pat":
        # Patent number mode: search by patent number ONLY — never try app number first
        # (avoids returning wrong application when digits match an app number)
        record = (_fetch(f"applicationMetaData.patentNumber:{clean}") or
                  _fetch(f"applicationMetaData.patentNumber:US{clean}"))
        if not record:
            raise ValueError(
                f"No issued patent found for '{ref}'. "
                "Check the patent number (e.g. US10123456 or 10,123,456). "
                "Note: applications not yet granted have no patent number.")
    else:
        record = (_fetch(f"applicationNumberText:{clean}") or
                  _fetch(f"applicationMetaData.patentNumber:{clean}") or
                  _fetch(f"applicationMetaData.patentNumber:US{clean}"))
        if not record:
            raise ValueError(f"No USPTO record found for '{ref}'.")

    d        = extract_all_events(record)
    meta     = d["meta"]
    pta_raw  = d["pta_raw"]
    filing   = d["filing_date"]
    grant    = d["grant_date"]
    if not filing:
        raise ValueError("No filing date in USPTO record.")

    # Use USPTO OFFICIAL PTA — not recalculated.
    # After the 2-month PTA challenge window passes, this is the authoritative number.
    official_pta = int(pta_raw.get("adjustmentTotalQuantity", 0) or 0)

    # Terminal Disclaimer detection & resolution
    td_list     = extract_terminal_disclaimers(record)
    td_result   = None
    parent_apps = []
    if td_list:
        td_result = resolve_td_expiration(td_list)
        if not td_result["resolved"]:
            parent_apps = fetch_parent_applications(meta.get("applicationNumberText") or record.get("applicationNumberText") or clean)
            if parent_apps:
                td_result = resolve_td_expiration(td_list, parent_apps=parent_apps)
    td_cap = td_result["td_cap_date"] if td_result else None

    maint_exp = compute_maintenance_and_expiration(
        grant_date=grant, filing_date=filing,
        total_pta_days=official_pta, terminal_disclaimer_date=td_cap
    )

    # Serialize terminal disclaimer
    def _ser_td(td_r, p_apps=None):
        if not td_r:
            return {"detected": False, "active_count": 0, "resolved": False,
                    "resolution_method": None, "td_cap_date": None,
                    "td_ref_patent": None, "td_ref_app": None, "td_chain": [],
                    "note": "No terminal disclaimers detected.", "raw_tds": [], "parent_apps": []}
        raw = [{"date":    str(td.get("date","")) if td.get("date") else None,
                "doc_code":    td.get("doc_code",""),
                "description": td.get("description",""),
                "ref_patent":  td.get("ref_patent"),
                "ref_app":     td.get("ref_app"),
                "withdrawn":   td.get("withdrawn", False),
                "possibly_withdrawn": td.get("possibly_withdrawn", False)}
               for td in (td_r.get("raw_tds") or [])]
        pa_out = [{"app_num": p.get("app_num",""), "patent_num": p.get("patent_num",""),
                   "relation": p.get("relation",""), "filing_date": p.get("filing_date","")}
                  for p in (p_apps or [])]
        return {"detected": True, "active_count": len(raw),
                "resolved": td_r.get("resolved", False),
                "resolution_method": td_r.get("resolution_method"),
                "td_cap_date": str(td_r["td_cap_date"]) if td_r.get("td_cap_date") else None,
                "td_ref_patent": td_r.get("td_ref_patent"),
                "td_ref_app": td_r.get("td_ref_app"),
                "td_chain": td_r.get("td_chain", []),
                "note": td_r.get("note",""),
                "raw_tds": raw, "parent_apps": pa_out}

    td_json   = _ser_td(td_result, parent_apps) if td_list else _ser_td(None)
    maint_json = None
    if maint_exp:
        maint_json = {
            "filing_date":         str(maint_exp["filing_date"]),
            "grant_date":          str(maint_exp["grant_date"]),
            "total_pta_days":      maint_exp["total_pta_days"],
            "base_expiration":     str(maint_exp["base_expiration"]),
            "expiration_with_pta": str(maint_exp["expiration_with_pta"]),
            "effective_expiration":str(maint_exp["effective_expiration"]),
            "td_applied":          maint_exp.get("td_applied", False),
            "td_cap_date":         str(maint_exp["td_cap_date"]) if maint_exp.get("td_cap_date") else None,
            "td_days_forfeited":   maint_exp.get("td_days_forfeited", 0),
            "maintenance_fees": [{
                "fee_number":       mf["fee_number"],
                "base_years":       mf["base_years"],
                "window_open":      str(mf["window_open"]),
                "window_close":     str(mf["window_close"]),
                "grace_period_end": str(mf["grace_period_end"]),
                "label":            mf["label"],
                "td_relevance":     mf.get("td_relevance","needed"),
                "td_note":          mf.get("td_note","")
            } for mf in maint_exp["maintenance_fees"]]
        }

    return {
        "source":       "EaseIP Expiration API v1",
        "query":        ref,
        "app_num":      meta.get("applicationNumberText") or record.get("applicationNumberText") or (clean if mode == "app" else ""),
        "patent_num":   meta.get("patentNumber",""),
        "title":        meta.get("inventionTitle",""),
        "applicant":    meta.get("firstApplicantName",""),
        "examiner":     meta.get("examinerNameText",""),
        "status":       meta.get("applicationStatusDescriptionText",""),
        "filing_date":  str(filing) if filing else None,
        "grant_date":   str(grant)  if grant  else None,
        "official_pta_days": official_pta,
        "maintenance":  maint_json,
        "terminal_disclaimer": td_json,
    }


@app.route("/expiration")
@login_required
def expiration_tool():
    """Simple Patent Expiration Tool — clean dashboard, default landing for non-PTA users."""
    return render_template("expiration.html",
                           username=session["user"], role=session.get("role","user"))


@app.route("/expiration_analyze", methods=["POST"])
@login_required
def expiration_analyze():
    """Browser-facing endpoint for the simple expiration dashboard."""
    data = request.get_json()
    ref  = (data.get("ref") or data.get("app_num") or "").strip()
    mode = data.get("mode", "app")
    if mode not in ("app", "pat"):
        mode = "app"
    if not ref:
        return jsonify({"error": "No application/patent number provided."}), 400
    try:
        result = _compute_expiration_for_ref(ref, mode=mode)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/settings")
@admin_required
def api_settings_page():
    return render_template("api_settings.html",
        username=session["user"], role=session.get("role","admin"),
        api_key=INTERNAL_EXP_API_KEY,
        server_host=request.host)

@app.route("/api/settings/regenerate", methods=["POST"])
@admin_required
def api_regenerate_key():
    global INTERNAL_EXP_API_KEY
    import secrets
    new_key = "easeip-" + secrets.token_hex(12)
    cfg = load_config()
    cfg["api_key"] = new_key
    save_config(cfg)
    INTERNAL_EXP_API_KEY = new_key
    return jsonify({"key": new_key, "ok": True})


@app.route("/api/expiration/<path:ref_num>", methods=["GET"])
def api_expiration(ref_num):
    """
    ╔══════════════════════════════════════════════════════╗
    ║  EaseIP Patent Expiration API  (v1)                  ║
    ║  GET /api/expiration/<app_or_patent_number>          ║
    ║                                                      ║
    ║  Authentication (either):                            ║
    ║    • Session cookie  (browser users)                 ║
    ║    • X-EaseIP-Api-Key: <key>  (tool-to-tool calls)   ║
    ║                                                      ║
    ║  Response JSON fields:                               ║
    ║    app_num, patent_num, filing_date, grant_date,     ║
    ║    official_pta_days, base_expiration,               ║
    ║    expiration_with_pta, td_applied, td_cap_date,     ║
    ║    effective_expiration, terminal_disclaimer{},      ║
    ║    maintenance_fees[]                                ║
    ╚══════════════════════════════════════════════════════╝
    """
    # Auth: session OR API key header
    api_key = request.headers.get("X-EaseIP-Api-Key", "")
    if "user" not in session and api_key != INTERNAL_EXP_API_KEY:
        return jsonify({
            "error": "Unauthorized.",
            "hint":  "Provide session cookie (browser) or X-EaseIP-Api-Key header (API calls)."
        }), 401
    try:
        result = _compute_expiration_for_ref(ref_num.strip())
        # Flatten maintenance fees to top level for API consumers
        maint = result.get("maintenance") or {}
        result["base_expiration"]      = maint.get("base_expiration")
        result["expiration_with_pta"]  = maint.get("expiration_with_pta")
        result["td_days_forfeited"]    = maint.get("td_days_forfeited", 0)
        result["effective_expiration"] = maint.get("effective_expiration")
        result["td_applied"]           = maint.get("td_applied", False)
        result["td_cap_date"]          = maint.get("td_cap_date")
        result["maintenance_fees"]     = maint.get("maintenance_fees", [])
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/proxy_bulk_download/<path:app_num>", methods=["POST"])
@login_required
def proxy_bulk_download(app_num):
    """Merge multiple PDFs into one and return as a single download."""
    import requests as req_lib, io
    from flask import Response
    from pta_engine import API_KEY
    try:
        import pypdf
        def make_writer(): return pypdf.PdfWriter()
        def get_reader(b): return pypdf.PdfReader(io.BytesIO(b))
    except ImportError:
        try:
            import PyPDF2
            def make_writer(): return PyPDF2.PdfWriter()
            def get_reader(b): return PyPDF2.PdfReader(io.BytesIO(b))
        except ImportError:
            return jsonify({"error": "pypdf not installed on server."}), 500

    clean   = app_num.replace("/","").replace(",","").replace(" ","")
    headers = {"x-api-key": API_KEY}
    doc_ids = request.json.get("doc_ids", [])
    if not doc_ids:
        return jsonify({"error": "No documents selected."}), 400

    writer = make_writer()
    errors = []
    for doc_id in doc_ids:
        url = f"https://api.uspto.gov/api/v1/download/applications/{clean}/{doc_id}.pdf"
        try:
            r = req_lib.get(url, headers=headers, timeout=60)
            if r.status_code == 200:
                reader = get_reader(r.content)
                for page in reader.pages:
                    writer.add_page(page)
            else:
                errors.append(doc_id)
        except Exception as e:
            errors.append(doc_id)

    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    pdf_bytes = out.read()

    resp = Response(pdf_bytes, status=200, content_type="application/pdf")
    resp.headers["Content-Disposition"] = f'attachment; filename="bulk_{clean}.pdf"'
    resp.headers["Content-Length"]      = str(len(pdf_bytes))
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp

def _generate_self_signed_cert(cert_path, key_path):
    """Auto-generate a self-signed TLS certificate valid for 10 years."""
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import ipaddress as _ip
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, u"localhost"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, u"EaseIP"),
        ])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject).issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.utcnow())
            .not_valid_after(dt.datetime.utcnow() + dt.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName(u"localhost"),
                x509.IPAddress(_ip.IPv4Address("127.0.0.1")),
            ]), critical=False)
            .sign(key, hashes.SHA256())
        )
        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        with open(key_path, "wb") as f:
            f.write(key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption()
            ))
        return True
    except ImportError:
        print("  [HTTPS] 'cryptography' package not installed.")
        print("  [HTTPS] Run:  pip install cryptography")
        return False
    except Exception as e:
        print(f"  [HTTPS] Certificate generation failed: {e}")
        return False

@app.route("/api/auto_resolve_td/<path:app_num>", methods=["GET"])
def api_auto_resolve_td(app_num):
    """
    ╔══════════════════════════════════════════════════════╗
    ║  EaseIP TD Auto-Resolver API  (v1)                   ║
    ║  GET /api/auto_resolve_td/<app_number>               ║
    ║                                                      ║
    ║  Authentication (either):                            ║
    ║    • Session cookie  (browser users)                 ║
    ║    • X-EaseIP-Api-Key: <key>  (tool-to-tool calls)   ║
    ║                                                      ║
    ║  Response JSON fields:                               ║
    ║    chain[], final_cap_date, resolved, note           ║
    ╚══════════════════════════════════════════════════════╝
    """
    api_key = request.headers.get("X-EaseIP-Api-Key", "")
    if "user" not in session and api_key != INTERNAL_EXP_API_KEY:
        return jsonify({
            "error": "Unauthorized.",
            "hint":  "Provide session cookie (browser) or X-EaseIP-Api-Key header (API calls)."
        }), 401
    try:
        max_depth = int(request.args.get("max_depth", 6))
        result = resolve_td_chain_full(app_num.strip(), max_depth=max_depth)
        # Serialize dates to strings for JSON
        for node in result.get("chain", []):
            for k in ("filing_date", "expiration_date"):
                if node.get(k) and not isinstance(node[k], str):
                    node[k] = str(node[k])
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/debug_td_pdf/<path:app_num>", methods=["GET"])
def api_debug_td_pdf(app_num):
    """Debug endpoint — returns raw PDF text and regex hits for TD docs."""
    from pta_engine import (fetch_docs_and_transactions, _download_td_pdf_bytes,
                            _extract_text_from_pdf_bytes, _parse_refs_from_td_text,
                            _TD_PDF_CODES)
    import importlib, pta_engine as _pte
    importlib.reload(_pte)   # force reload so we always run latest code
    api_key = request.headers.get("X-EaseIP-Api-Key", "")
    if "user" not in session and api_key != INTERNAL_EXP_API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    try:
        clean = app_num.replace("/","").replace(",","").replace(" ","")
        docs_result = fetch_docs_and_transactions(clean)
        docs = docs_result.get("documents", []) if isinstance(docs_result, dict) else []
        td_docs = [d for d in docs
                   if (d.get("code") or "").strip().upper() in _TD_PDF_CODES
                   and d.get("identifier")]
        if not td_docs:
            return jsonify({
                "error": "No TD docs found",
                "total_docs": len(docs),
                "all_doc_codes": [d.get("code") for d in docs[:40]],
            })
        results = []
        for td_doc in td_docs[:3]:
            doc_id = td_doc["identifier"]
            # --- download with full diagnostics ---
            import requests as _req
            from pta_engine import API_KEY
            dl_url = f"https://api.uspto.gov/api/v1/download/applications/{clean}/{doc_id}.pdf"
            try:
                r = _req.get(dl_url, headers={"x-api-key": API_KEY}, timeout=60, allow_redirects=True)
                http_status   = r.status_code
                content_type  = r.headers.get("Content-Type","")
                pdf_bytes     = r.content if r.status_code == 200 else None
                first_bytes   = r.content[:20].hex() if r.content else ""
                is_pdf_magic  = r.content[:4] == b'%PDF' if r.content else False
            except Exception as dl_err:
                results.append({"doc_id": doc_id, "error": f"Download exception: {dl_err}"})
                continue

            if not pdf_bytes:
                results.append({"doc_id": doc_id, "http_status": http_status, "error": "Non-200 response"})
                continue

            # --- per-layer diagnostic + final extraction ---
            import io as _io, traceback as tb

            layers = {}

            # pypdf content
            try:
                import pypdf
                rdr  = pypdf.PdfReader(_io.BytesIO(pdf_bytes))
                ct   = "".join(p.extract_text() or "" for p in rdr.pages)
                fields = rdr.get_fields() or {}
                fparts = []
                for nm, fld in fields.items():
                    v = fld.get("/V") or fld.get("value") or ""
                    if v and str(v).strip():
                        fparts.append(f"{nm}={v}")
                # Also page annotations
                for page in rdr.pages:
                    for ann in (page.get("/Annots") or []):
                        try:
                            obj = ann.get_object() if hasattr(ann,"get_object") else ann
                            v = obj.get("/V") or obj.get("/DV") or ""
                            if v and str(v).strip() not in ("/Off",""):
                                fparts.append(str(v))
                        except Exception:
                            pass
                layers["pypdf_content"] = {"len": len(ct), "preview": ct[:300]}
                layers["pypdf_fields"]  = {"count": len(fields), "values": fparts[:30]}
            except Exception as e:
                layers["pypdf"] = {"error": str(e)}

            # pdfplumber
            try:
                import pdfplumber
                with pdfplumber.open(_io.BytesIO(pdf_bytes)) as pdf:
                    plt = "".join(p.extract_text() or "" for p in pdf.pages)
                layers["pdfplumber"] = {"len": len(plt), "preview": plt[:300]}
            except Exception as e:
                layers["pdfplumber"] = {"error": str(e)}

            # OCR via extract function
            try:
                text, used_ocr = _pte._extract_text_from_pdf_bytes(pdf_bytes)
                layers["extract_result"] = {"text_len": len(text), "used_ocr": used_ocr,
                                            "preview": text[:500]}
            except Exception as e:
                layers["extract_result"] = {"error": str(e), "tb": tb.format_exc()[-600:]}

            ref_apps, ref_pats = _pte._parse_refs_from_td_text(
                layers.get("extract_result",{}).get("preview","")
            )
            results.append({
                "doc_id":         doc_id,
                "doc_code":       td_doc.get("code"),
                "http_status":    http_status,
                "is_pdf_magic":   is_pdf_magic,
                "pdf_size_bytes": len(pdf_bytes),
                "layers":         layers,
                "ref_apps":       ref_apps,
                "ref_pats":       ref_pats,
            })
        return jsonify({"app_num": clean, "td_docs_found": len(td_docs), "results": results})
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


if __name__=="__main__":
    import ssl,socket,argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--host",default="0.0.0.0")
    parser.add_argument("--port",type=int,default=9092)
    args=parser.parse_args()
    cert=os.path.join(BASE,"cert.pem");key=os.path.join(BASE,"key.pem")
    local_ip=socket.gethostbyname(socket.gethostname())

    # Auto-generate certificate if not present
    if not (os.path.exists(cert) and os.path.exists(key)):
        print("  [HTTPS] No certificate found — generating self-signed certificate...")
        ok = _generate_self_signed_cert(cert, key)
        if ok:
            print("  [HTTPS] Certificate generated (cert.pem + key.pem).")
        else:
            print("  [HTTPS] Falling back to HTTP.")

    if os.path.exists(cert) and os.path.exists(key):
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert,key)
        print(f" =============================================")
        print(f"  EaseIP Intelligent PTA Calculator")
        print(f"  Starting server...")
        print(f" =============================================")
        print(f" Server running at:")
        print(f"   Local:   https://localhost:{args.port}")
        print(f"   Network: https://{local_ip}:{args.port}")
        print(f" NOTE: Your browser may show a security warning.")
        print(f" Click 'Advanced' then 'Proceed to localhost' to continue.")
        print(f" =============================================")
        app.run(debug=False,host=args.host,port=args.port,ssl_context=ctx)
    else:
        print(f" =============================================")
        print(f"  EaseIP Intelligent PTA Calculator (HTTP mode)")
        print(f"  Install 'cryptography' for HTTPS: pip install cryptography")
        print(f" Server running at:")
        print(f"   Local:   http://localhost:{args.port}")
        print(f"   Network: http://{local_ip}:{args.port}")
        print(f" =============================================")
        app.run(debug=True,host=args.host,port=args.port)