"""The individual screens of the application."""

from .duplicates import DuplicatesPage
from .library import LibraryPage
from .ranking import RankingPage
from .settings import SettingsPage
from .transfer import ExportPage, ImportPage

__all__ = [
    "LibraryPage",
    "DuplicatesPage",
    "RankingPage",
    "ImportPage",
    "ExportPage",
    "SettingsPage",
]
