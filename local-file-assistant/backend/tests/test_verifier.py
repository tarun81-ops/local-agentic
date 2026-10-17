from app.core.llm.verifier import verify

CHUNKS = [
    {"path": r"C:\docs\invoice_notes.pdf", "loc_kind": "page", "loc_no": 1, "text": "The invoice total for Acme Corp is $4,250 due March 3rd."},
    {"path": r"C:\docs\invoice_notes.pdf", "loc_kind": "page", "loc_no": 2, "text": "Payment terms: net 30 days from the invoice date."},
]


def test_correct_citation_verifies():
    r = verify("The invoice total for Acme Corp is $4,250 (invoice_notes.pdf, page 1).", CHUNKS)
    assert r["all_verified"], r
    assert r["citations"][0]["path"] == CHUNKS[0]["path"] and r["citations"][0]["n"] == 1


def test_citation_to_unretrieved_file_fails():
    r = verify("The invoice total is $4,250 (fake_receipt.pdf, page 9).", CHUNKS)
    assert not r["all_verified"] and r["citations"][0]["reason"] == "not in retrieved files"


def test_misattributed_claim_fails():
    r = verify("Payment is due within 14 days of delivery (invoice_notes.pdf, page 1).", CHUNKS)
    assert not r["all_verified"], r


def test_full_path_in_citation_still_matches():
    r = verify(r"Acme Corp owes $4,250 (C:\docs\invoice_notes.pdf, page 1).", CHUNKS)
    assert r["all_verified"], r


def test_correct_claim_against_a_long_chunk_verifies():
    """The old verifier scored the whole chunk's facts against the answer, so any realistic
    500-word chunk failed. Checking the claim against the chunk scales with chunk size."""
    filler = " ".join(f"Item{i} costs {i * 10} dollars in Region{i}." for i in range(60))
    chunks = [{"path": "q3.docx", "loc_kind": "part", "loc_no": 2, "text": filler + " The Everest budget is $120,000."}]
    r = verify("The Everest budget was approved at $120,000 (q3.docx, part 2).", chunks)
    assert r["all_verified"], r


def test_same_source_gets_same_number_and_spans_point_at_citations():
    text = "Acme Corp owes $4,250 (invoice_notes.pdf, page 1). Due March 3rd (invoice_notes.pdf, page 1)."
    r = verify(text, CHUNKS)
    assert [c["n"] for c in r["citations"]] == [1, 1]
    s, e = r["citations"][0]["span"]
    assert text[s:e] == "(invoice_notes.pdf, page 1)"


def test_uncited_claim_is_reported():
    text = "The invoice total for Acme Corp is $4,250 (invoice_notes.pdf, page 1). The vendor also owes a $9,999 fee to Globex."
    r = verify(text, CHUNKS)
    assert r["all_verified"]  # the one citation is fine...
    [(s, e)] = r["uncited"]  # ...but the made-up sentence after it is flagged
    assert text[s:e] == "The vendor also owes a $9,999 fee to Globex."


def test_refusal_and_cited_sentences_are_not_uncited():
    text = "Acme Corp owes $4,250 (invoice_notes.pdf, page 1). The excerpts don't say when it was paid."
    assert verify(text, CHUNKS)["uncited"] == []


def test_claim_with_nothing_to_check_is_unchecked_not_verified():
    r = verify("the invoice has been paid (invoice_notes.pdf, page 1).", CHUNKS)
    assert r["citations"][0]["status"] == "unchecked" and not r["all_verified"]


def test_negated_claim_fails():
    r = verify("The invoice total is not $4,250 (invoice_notes.pdf, page 1).", CHUNKS)
    assert r["citations"][0]["status"] == "failed" and r["citations"][0]["reason"] == "claim negates the source"


MARKSHEET = [{"path": r"C:\docs\class 10.pdf", "loc_kind": "page", "loc_no": 1, "text": "CENTRAL BOARD OF SECONDARY EDUCATION Roll No. 12127198 Name of Candidate TARUN VERMA"}]


def test_abbreviation_no_does_not_split_the_claim():
    """Regression: "roll no." ended a sentence, so "Your class 10 roll no." was flagged as uncited."""
    r = verify("Your class 10 roll no. is 12127198. (class 10.pdf, page 1)", MARKSHEET)
    assert r["uncited"] == [], r["uncited"]
    assert r["citations"][0]["status"] == "checked", r


def test_no_as_an_abbreviation_is_not_a_negation():
    chunks = [{"path": "bill.pdf", "loc_kind": "page", "loc_no": 1, "text": "Bill No. 4471 amount Rs. 1,250"}]
    r = verify("Bill no. 4471 is for Rs. 1,250 (bill.pdf, page 1).", chunks)
    assert r["all_verified"] and r["uncited"] == [], r


def test_titles_and_initials_do_not_split_sentences():
    chunks = [{"path": "letter.pdf", "loc_kind": "page", "loc_no": 1, "text": "Signed by Dr. A. K. Sharma on 12 May"}]
    r = verify("It was signed by Dr. A. K. Sharma on 12 May (letter.pdf, page 1).", chunks)
    assert r["all_verified"] and r["uncited"] == [], r


def test_real_negation_still_fails():
    r = verify("Acme Corp does not owe $4,250 (invoice_notes.pdf, page 1).", CHUNKS)
    assert r["citations"][0]["reason"] == "claim negates the source", r


ROLL_PAGE = ("2101729 0230729 Regn.No. B123/19016/0069 CENTRAL BOARD OF SECONDARY EDUCATION MARKS STATEMENT CUM CERTIFICATE "
             "2023 SECONDARY SCHOOL EXAMINATION, 2023 This is to certify that TARUN VERMA Roll No. 12127198 Mother's Name "
             "SUNITA VERMA has achieved scholastic achievements as under. SUBJECT CODE SUBJECT THEORY PRACTICAL TOTAL")


def test_excerpt_is_centred_on_the_cited_number():
    """Regression: the source line showed "2[10]1729…" (a search word inside another number) and not the roll number."""
    from app.core.llm.verifier import excerpt

    out = excerpt(ROLL_PAGE, "Your class 10 roll no. is 12127198.")
    assert "Roll No. 12127198" in out and len(out) <= 230
    assert excerpt("a 2101729 b", "class 10") == ""  # "10" is not found inside 2101729
    assert excerpt("Total due Rs. 1,240 for Sept", "The bill is Rs. 1240") == "Total due Rs. 1,240 for Sept"
    assert excerpt(ROLL_PAGE, "nothing to see here") == ""


def test_citations_carry_their_claim():
    r = verify("Acme Corp owes $4,250 (invoice_notes.pdf, page 1).", CHUNKS)
    assert r["citations"][0]["claim"] == "Acme Corp owes $4,250"
