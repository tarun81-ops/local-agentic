from app.core.assistant import prompts
from app.core.assistant.router import organize_intent


def test_cleanup_requests_are_recognised():
    for m in ["my downloads is mess help", "my downloads folder is a total mess", "find duplicate files",
              "show me duplicates in Pictures", "help me clean up my desktop", "my C drive is almost full",
              "free up space", "organise my files", "tidy my downloads", "sort out my downloads"]:
        assert organize_intent(m), m


def test_file_questions_still_search():
    for m in ["mess fee receipt amount", "what is in my mess receipt", "where is my duplicate marksheet",
              "class 10 roll no", "clean energy notes summary", "find my resume"]:
        assert not organize_intent(m), m


def test_role_is_short_and_points_to_tidy_up():
    assert len(prompts.ROLE.split()) <= 90
    assert "Organize page" in prompts.ROLE and "short" in prompts.ROLE
