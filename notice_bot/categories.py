"""Guess a notice's category from its title, to label messages."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    label: str
    emoji: str


GENERAL = Category("General", "📢")

# Checked top to bottom, first match wins. Order matters: "Admission Test
# Written Examination" is an admission notice, not an exam notice, and
# "Merit Based Scholarship Examination" is about scholarships.
_RULES: tuple[tuple[Category, tuple[str, ...]], ...] = (
    (Category("Holiday", "🏖️"), ("holiday", "university open", "university closed", "vacation")),
    (Category("Admission", "🎓"), ("admission",)),
    (Category("Scholarship & Aid", "💰"), ("scholarship", "financial aid", "grant", "waiver")),
    (Category("Payments", "💳"), ("payment", "installment", "tuition", "fee ")),
    (
        Category("Exams", "📝"),
        ("exam", "seat plan", "mid-term", "midterm", "mid term", "final term", "set b", "viva"),
    ),
    (
        Category("Registration", "🗂️"),
        ("registration", "adding", "dropping", "orientation", "probation", "advising"),
    ),
    (
        Category("Internship & Thesis", "💼"),
        ("internship", "thesis", "capstone", "cv ", "career"),
    ),
    (
        Category("Events", "🏆"),
        ("competition", "contest", "program", "uthshab", "workshop", "seminar", "survey", "fest"),
    ),
    (Category("System", "🛠️"), ("maintenance", "server", "portal", "downtime")),
)


def categorize(title: str) -> Category:
    # Padded so keywords with a trailing space ("cv ", "fee ") also match at the end.
    haystack = f"{title.casefold()} "
    for category, keywords in _RULES:
        if any(keyword in haystack for keyword in keywords):
            return category
    return GENERAL
