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
