import pytest

from notice_bot.categories import GENERAL, categorize


@pytest.mark.parametrize(
    ("title", "label"),
    [
        ("Admission Test Written Examination for LL.B. & B. Pharm", "Admission"),
        ("Seat Plan of Final Term Exams of Summer 2025-26", "Exams"),
        ("FBA Mid Term Set B for Summer 2025-26", "Exams"),
        ("Releasing of Exam Permit for the Midterm Exam", "Exams"),
        ("Notice for Result of Merit Based Scholarship Examination", "Scholarship & Aid"),
        ("FINANCIAL AID - Fall 2026-27", "Scholarship & Aid"),
        ("Payment Schedule for Spring 2025-2026 Final 3rd Installment", "Payments"),
        ("Adding Dropping for Fall 2026-27", "Registration"),
        ("Freshman Student Orientation of Fall 2026-27", "Registration"),
        ("Notice for University open on Saturday and Durga Puja holiday", "Holiday"),
        ("CV Submission Notice for Summer 2025-26", "Internship & Thesis"),
        ("Thesis Day Summer 2025-26 for CS", "Internship & Thesis"),
        ("Cultural competitions for Nazrul Borsho 2026", "Events"),
        ("Scheduled Server Maintenance 02-04 July", "System"),
    ],
)
def test_categorize(title, label):
    assert categorize(title).label == label


def test_unrecognised_title_is_general():
    assert categorize("Important Notice on Official Information Sharing") is GENERAL


def test_matching_ignores_case():
    assert categorize("ADMISSION TEST FINAL RESULT").label == "Admission"
