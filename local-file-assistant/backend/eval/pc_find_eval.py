"""How well does whole-PC file finding locate the right file from a plain question?

Builds a throwaway "PC" (two drives, ~120 files, look-alike decoys, deep folders), scans it into a private
catalog, then runs each question through the same steps the assistant uses WITHOUT a model (file_agent.understand
falls back to plain keywords), so the numbers are a floor: the model's planning can only add to them.

    python eval/pc_find_eval.py          prints recall@1/3/8 and every miss
Exit code 1 if recall@8 is below MIN_RECALL8 (the pytest wrapper uses the same function)."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# (relative path under the fake PC, ...). The first block are the answers; the rest are decoys and noise.
ANSWERS = {
    "electricity": "C/Users/me/Downloads/Electricity_Bill-Oct2026.pdf",
    "broadband": "C/Users/me/Downloads/BSNL broadband invoice 2026-08.pdf",
    "aadhaar": "C/Users/me/Documents/Identity/Aadhaar_card_scan.jpg",
    "resume": "C/Users/me/Documents/Career/resume_final_v3.docx",
    "cv": "C/Users/me/Documents/Career/Tarun_Verma_CV_2026.pdf",
    "cert": "D/Courses/AIML/IIIT_NR_AIML_Certificate.pdf",
    "marksheet": "C/Users/me/Documents/School/JNV_marksheet_class12.pdf",
    "ticket": "C/Users/me/Downloads/Train ticket Raipur to Delhi.pdf",
    "hostel": "C/Users/me/Documents/College/hostel_fee_receipt_sem1.pdf",
    "syllabus": "D/College/ETE/Sem1/ETE_Syllabus_2026-30.pdf",
    "physics": "D/College/ETE/Sem1/Physics_Assignment_3.docx",
    "timetable": "C/Users/me/Desktop/Class TimeTable.png",
    "gridsentinel": "D/Projects/GridSentinel/GridSentinel_V2_Report.pdf",
    "attendance": "D/Projects/Attendance/attendance_analysis_notes.md",
    "hackathon": "C/Users/me/Documents/Hackathon/LocalAgent_Submission_Draft.docx",
    "bank": "C/Users/me/Documents/Finance/SBI_statement_Sep2026.pdf",
    "scholarship": "C/Users/me/Documents/College/Scholarship_Application_Form.pdf",
    "passport": "C/Users/me/Documents/Identity/passport_photo_35x45.jpg",
    "insurance": "C/Users/me/Documents/Finance/Health_Insurance_Policy_2026.pdf",
    "lecture": "D/College/ETE/Sem1/Lecture 7 - Fourier Series.pptx",
}
DECOYS = [
    "C/Users/me/Downloads/Electricity_Bill-Oct2025.pdf",
    "C/Users/me/Documents/Old/ElectricityBillSept.pdf",
    "C/Users/me/Downloads/Water_bill_Oct2026.pdf",
    "C/Users/me/Downloads/Airtel broadband invoice 2025-08.pdf",
    "C/Users/me/Documents/Career/resume_draft_v1.docx",
    "C/Users/me/Documents/Career/cover_letter_infosys.docx",
    "D/Courses/Python/Coursera_Python_Certificate.pdf",
    "D/Courses/ML/Andrew_Ng_ML_Notes.pdf",
    "C/Users/me/Documents/School/JNV_marksheet_class10.pdf",
    "C/Users/me/Downloads/Bus ticket Raipur to Bilaspur.pdf",
    "C/Users/me/Documents/College/library_fine_receipt.pdf",
    "D/College/ETE/Sem1/Maths_Syllabus.pdf",
    "D/College/ETE/Sem1/Chemistry_Assignment_2.docx",
    "C/Users/me/Desktop/Exam TimeTable.png",
    "D/Projects/TrainTracker/TrainTracker_Report.pdf",
    "D/Projects/Attendance/attendance_dashboard.py",
    "C/Users/me/Documents/Hackathon/Idea_Pitch.pptx",
    "C/Users/me/Documents/Finance/HDFC_statement_Sep2026.pdf",
    "C/Users/me/Documents/College/Scholarship_Results.xlsx",
    "C/Users/me/Documents/Identity/pan_card.jpg",
    "C/Users/me/Documents/Finance/Vehicle_Insurance_Policy_2025.pdf",
    "D/College/ETE/Sem1/Lecture 6 - Laplace Transform.pptx",
]
NOISE = [f"C/Users/me/Documents/Misc/notes_{i:02d}.txt" for i in range(40)] + [f"D/Media/Photos/IMG_{2000+i}.jpg" for i in range(40)]

# (question, key in ANSWERS). Written the way a student would ask, not as file names.
QUESTIONS = [
    ("Where is my electricity bill for October 2026?", "electricity"),
    ("find the broadband invoice from BSNL", "broadband"),
    ("show my aadhaar card scan", "aadhaar"),
    ("open my latest resume", "resume"),
    ("find my CV", "cv"),
    ("where is my AI ML course certificate from IIIT", "cert"),
    ("class 12 marksheet", "marksheet"),
    ("train ticket to Delhi", "ticket"),
    ("hostel fee receipt for first semester", "hostel"),
    ("ETE syllabus pdf", "syllabus"),
    ("my third physics assignment", "physics"),
    ("class timetable picture", "timetable"),
    ("GridSentinel report", "gridsentinel"),
    ("my attendance analysis notes", "attendance"),
    ("hackathon submission draft", "hackathon"),
    ("September bank statement from SBI", "bank"),
    ("scholarship application form", "scholarship"),
    ("passport size photo", "passport"),
    ("health insurance policy", "insurance"),
    ("lecture on Fourier series slides", "lecture"),
    ("the bill I downloaded for electricity last month", "electricity"),
    ("my college certificates and fee receipts for hostel", "hostel"),
]
# Harder: typos, abbreviations, synonyms, Hinglish, words that are not in the file name at all.
HARD = [
    ("elecricity bill october", "electricity"),
    ("bijli ka bill october", "electricity"),
    ("wifi internet bill BSNL aug 2026", "broadband"),
    ("biodata", "resume"),
    ("my curriculum vitae pdf", "cv"),
    ("AIML certfiicate", "cert"),
    ("12th result", "marksheet"),
    ("railway ticket Raipur Delhi", "ticket"),
    ("hostel fees paid sem 1", "hostel"),
    ("syllabus for ETE first sem", "syllabus"),
    ("class time table", "timetable"),
    ("sbi account statement for sept", "bank"),
    ("photo for passport", "passport"),
    ("fourier series ppt", "lecture"),
    ("mediclaim policy", "insurance"),
]
MIN_RECALL8 = 0.80


def build(root: Path) -> None:
    for rel in [*ANSWERS.values(), *DECOYS, *NOISE]:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    import os
    import time

    old = time.time() - 400 * 86400  # decoys are old copies; "latest" and "last month" should prefer the answers
    for rel in [*DECOYS]:
        os.utime(root / rel, (old, old))


def run(verbose: bool = True) -> dict:
    from app.config import settings
    from app.core import pc_access
    from app.core.assistant import file_agent

    work = Path(tempfile.mkdtemp(prefix="lfa-pcfind-"))
    settings.data_dir = work / "data"
    pc = work / "pc"
    build(pc)
    pc_access.set_config(enabled=True, drives=[str(pc / "C"), str(pc / "D")])
    import time

    t0 = time.perf_counter()
    counts = pc_access.scan([str(pc / "C"), str(pc / "D")])
    scan_s = time.perf_counter() - t0

    def no_model(*a, **k):
        raise RuntimeError("eval runs without a model")

    file_agent.llm_json = no_model
    hits = {1: 0, 3: 0, 8: 0}
    misses = []
    for question, key in QUESTIONS + [(q, k) for q, k in HARD]:
        want = (pc / ANSWERS[key]).name
        plan = file_agent.understand(question)
        names = [c["name"] for c in file_agent.candidates(plan)]
        rank = names.index(want) + 1 if want in names else None
        for k in hits:
            hits[k] += bool(rank and rank <= k)
        if rank != 1:
            misses.append((question, want, plan["keywords"], rank, names[:3]))
    n = len(QUESTIONS) + len(HARD)
    result = {"files": counts["added"], "scan_seconds": round(scan_s, 3), "questions": n, **{f"recall@{k}": round(v / n, 2) for k, v in hits.items()}, "misses": misses}
    if verbose:
        print(f"{result['files']} files scanned in {result['scan_seconds']}s, {n} questions")
        print("recall@1 %.2f  recall@3 %.2f  recall@8 %.2f" % (hits[1] / n, hits[3] / n, hits[8] / n))
        for q, want, kw, rank, top in misses:
            print(f"  MISS rank={rank}  {q!r}\n       want {want}\n       words {kw}\n       got  {top}")
    return result


if __name__ == "__main__":
    r = run()
    sys.exit(0 if r["recall@8"] >= MIN_RECALL8 else 1)
