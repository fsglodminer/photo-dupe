"""Interface tests.

These run against Qt's offscreen platform, so they need no display. They check
that every page builds, that the theme applies cleanly, that the delegate can
paint, and that the review actions actually reach the database.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from photodupe.records import MARK_DELETE, MARK_KEEP, MARK_NONE  # noqa: E402
from photodupe.ui.icons import app_icon, icon  # noqa: E402
from photodupe.ui.models import PHOTO_ROLE, PIXMAP_ROLE, PhotoDelegate, PhotoListModel  # noqa: E402
from photodupe.ui.theme import DARK, LIGHT, palette_for, score_color, stylesheet  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application


@pytest.fixture
def window(qt_app, settings, library, photo_set):
    from photodupe.ui.app import MainWindow

    root, _ = photo_set
    library.scan([root])
    settings.watched_folders = [str(root)]
    main = MainWindow(settings, library)
    yield main
    main.thumbnail_loader.shutdown()
    main.deleteLater()


# ---------------------------------------------------------------------------
# theme and icons
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("palette", [DARK, LIGHT])
def test_stylesheet_has_no_unresolved_placeholders(palette):
    """Every ``{p.colour}`` field must have been substituted, not emitted raw."""
    import re

    sheet = stylesheet(palette)
    leftovers = re.findall(r"\{[A-Za-z_][A-Za-z0-9_.]*\}", sheet)
    assert not leftovers, f"unsubstituted placeholders: {leftovers}"
    assert palette.accent in sheet
    assert palette.window in sheet
    assert len(sheet) > 2000


@pytest.mark.parametrize("palette", [DARK, LIGHT])
def test_stylesheet_includes_generated_arrow_assets(qt_app, palette):
    from photodupe.ui.icons import stylesheet_assets

    assets = stylesheet_assets(palette)
    sheet = stylesheet(palette, assets)
    for path in assets.values():
        assert os.path.exists(path)
        assert path in sheet


def test_palette_lookup():
    assert palette_for("light") is LIGHT
    assert palette_for("dark") is DARK
    assert palette_for("nonsense") is DARK


def test_score_colours_move_from_bad_to_good():
    assert score_color(DARK, 90).name() == DARK.good
    assert score_color(DARK, 60).name() == DARK.warn
    assert score_color(DARK, 20).name() == DARK.bad


@pytest.mark.parametrize(
    "name",
    ["library", "duplicates", "ranking", "import", "export", "settings",
     "folder", "search", "trash", "check", "cross", "refresh", "star", "info"],
)
def test_every_icon_renders(qt_app, name):
    rendered = icon(name, "#4c8dff", 24)
    assert not rendered.isNull()
    pixmap = rendered.pixmap(24, 24)
    assert not pixmap.isNull()
    # A blank icon would mean the drawing code silently did nothing.
    image = pixmap.toImage()
    assert any(
        image.pixelColor(x, y).alpha() > 0
        for x in range(image.width())
        for y in range(image.height())
    ), f"{name} rendered blank"


def test_icons_are_cached(qt_app):
    assert icon("library", "#ffffff", 20) is icon("library", "#ffffff", 20)


def test_app_icon_offers_several_sizes(qt_app):
    assert len(app_icon().availableSizes()) >= 5


# ---------------------------------------------------------------------------
# the grid
# ---------------------------------------------------------------------------

def test_model_exposes_photos(qt_app, library, photo_set):
    from photodupe.ui.workers import ThumbnailLoader

    root, _ = photo_set
    library.scan([root])
    photos = library.ranked()

    loader = ThumbnailLoader(1)
    model = PhotoListModel(loader, 128)
    model.set_photos(photos, best_ids={photos[0].id})

    assert model.rowCount() == len(photos)
    index = model.index(0, 0)
    assert model.data(index, PHOTO_ROLE) is photos[0]
    assert photos[0].name in model.data(index, QtCore.Qt.ItemDataRole.DisplayRole)
    assert "Score" in model.data(index, QtCore.Qt.ItemDataRole.ToolTipRole)
    assert model.is_best(photos[0].id)
    assert model.index_of(photos[0].id).row() == 0
    assert not model.index_of(-1).isValid()
    loader.shutdown()


def test_model_updates_and_removals(qt_app, library, photo_set):
    from photodupe.ui.workers import ThumbnailLoader

    root, _ = photo_set
    library.scan([root])
    photos = library.ranked()
    loader = ThumbnailLoader(1)
    model = PhotoListModel(loader, 128)
    model.set_photos(photos)

    model.update_marks({photos[0].id: MARK_DELETE})
    assert model.photo_at(0).mark == MARK_DELETE

    model.remove_ids([photos[0].id])
    assert model.rowCount() == len(photos) - 1
    assert not model.index_of(photos[0].id).isValid()
    loader.shutdown()


def test_delegate_paints_without_error(qt_app, library, photo_set):
    from photodupe.ui.workers import ThumbnailLoader

    root, _ = photo_set
    library.scan([root])
    photos = library.ranked()
    photos[0].mark = MARK_DELETE
    photos[1].mark = MARK_KEEP
    photos[2].rating = 3

    loader = ThumbnailLoader(1)
    model = PhotoListModel(loader, 128)
    model.set_photos(photos, best_ids={photos[1].id})
    delegate = PhotoDelegate(DARK, 160)

    image = QtGui.QImage(240, 240, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(image)
    option = QtWidgets.QStyleOptionViewItem()
    option.rect = QtCore.QRect(0, 0, 220, 220)
    for row in range(min(4, model.rowCount())):
        delegate.paint(painter, option, model.index(row, 0))
    painter.end()
    loader.shutdown()

    assert delegate.sizeHint(option, model.index(0, 0)).width() > 0


# ---------------------------------------------------------------------------
# the window
# ---------------------------------------------------------------------------

def test_every_page_builds_and_refreshes(window):
    assert set(window.pages) == {
        "library", "duplicates", "ranking", "import", "export", "settings"
    }
    for name in window.pages:
        window.go_to(name)
        assert window.stack.currentWidget() is window.pages[name]
        assert window.nav_buttons[name].isChecked()


def test_switching_theme_reaches_every_page(window):
    for theme in ("light", "dark", "light"):
        window.settings.theme = theme
        window.apply_theme()
        assert window.palette_() is palette_for(theme)
        for name in window.pages:
            window.go_to(name)


def test_library_page_shows_the_index(window):
    page = window.pages["library"]
    window.go_to("library")
    assert page.stat_photos.value_label.text() == "10"
    assert page.folders.folders() == window.settings.watched_folders


def test_ranking_page_lists_and_filters(window):
    page = window.pages["ranking"]
    window.go_to("ranking")
    assert page.model.rowCount() == 10

    page.search.setText("unique")
    page.reload()
    assert page.model.rowCount() == 3
    assert "of 10" in page.count_label.text()

    page.search.setText("")
    page.min_score.setValue(100)
    page.reload()
    assert page.model.rowCount() == 0

    page.min_score.setValue(0)
    page.reload()
    assert page.model.rowCount() == 10


def test_ranking_marks_reach_the_database(window):
    page = window.pages["ranking"]
    window.go_to("ranking")
    page.grid.selectAll()
    page._mark_selected(MARK_DELETE)

    marked = window.library.db.photos(mark=MARK_DELETE)
    assert len(marked) == 10

    page._mark_selected(MARK_NONE)
    assert window.library.db.photos(mark=MARK_DELETE) == []


def test_ranking_rating_is_saved(window):
    page = window.pages["ranking"]
    window.go_to("ranking")
    photo = page.model.photo_at(0)
    page._on_rating(photo.id, 4)
    assert window.library.db.get(photo.id).rating == 4


def test_duplicates_page_finds_and_marks(window):
    page = window.pages["duplicates"]
    window.go_to("duplicates")
    page._on_groups(window.library.duplicate_groups())

    assert page.group_list.count() == len(page._groups) == 3
    assert page.stat_groups.value_label.text() == "3"

    page.group_list.setCurrentRow(0)
    assert page._current is not None
    assert "Group 1" in page.group_title.text()

    marked = page._mark_all_duplicates()
    stored = window.library.db.photos(mark=MARK_DELETE)
    assert len(stored) == sum(len(group.photos) - 1 for group in page._groups)
    # never the keeper
    keepers = {group.best_id for group in page._groups}
    assert not keepers.intersection({photo.id for photo in stored})

    page._clear_marks()
    assert window.library.db.photos(mark=MARK_DELETE) == []


def test_duplicates_toggle_one_photo(window):
    page = window.pages["duplicates"]
    window.go_to("duplicates")
    page._on_groups(window.library.duplicate_groups())
    page.group_list.setCurrentRow(0)

    victim = page._current.others[0]
    page._toggle_mark(victim)
    assert window.library.db.get(victim.id).mark == MARK_DELETE
    page._toggle_mark(victim)
    assert window.library.db.get(victim.id).mark == MARK_NONE


def test_export_page_resolves_each_selection(window):
    page = window.pages["export"]
    window.go_to("export")

    page.selection.setCurrentIndex(page.selection.findData("all"))
    assert len(page._resolve()) == 10

    page.selection.setCurrentIndex(page.selection.findData("top"))
    page.top_count.setValue(3)
    assert len(page._resolve()) == 3

    page.selection.setCurrentIndex(page.selection.findData("selection"))
    assert page._resolve() == []
    page.set_explicit_selection(window.library.ranked()[:2])
    assert len(page._resolve()) == 2


def test_export_waits_for_grouping_rather_than_blocking(window):
    """Grouping runs in the background, so the page must cope with 'not yet'."""
    page = window.pages["export"]
    window.go_to("export")
    assert window.cached_groups() is None

    page.selection.setCurrentIndex(page.selection.findData("keepers"))
    assert page._resolve() == []                       # nothing to offer yet
    assert "duplicates" in page.preview_label.text().lower()
    assert not page.run_button.isEnabled()

    # once the groups arrive, the same selection resolves properly
    window.set_group_cache(window.library.duplicate_groups())
    page._update_preview()
    assert len(page._resolve()) == 6                   # 10 photos, 4 surplus
    assert page.run_button.isEnabled()


def test_the_duplicates_page_shares_its_result_with_export(window):
    duplicates = window.pages["duplicates"]
    window.go_to("duplicates")
    duplicates._on_groups(window.library.duplicate_groups())

    assert window.cached_groups() is not None
    window.go_to("export")
    export = window.pages["export"]
    export.selection.setCurrentIndex(export.selection.findData("keepers"))
    assert len(export._resolve()) == 6                 # no recomputation needed


def test_changing_the_library_invalidates_the_group_cache(window):
    window.set_group_cache(window.library.duplicate_groups())
    assert window.cached_groups() is not None
    window.library_changed()
    assert window.cached_groups() is None


def test_export_selection_hands_over_from_the_ranking_page(window):
    window.go_to("ranking")
    ranking = window.pages["ranking"]
    ranking.grid.setCurrentIndex(ranking.model.index(0, 0))
    ranking._export_selection()

    assert window.stack.currentWidget() is window.pages["export"]
    assert len(window.pages["export"]._resolve()) == 1


def test_settings_page_writes_through(window):
    page = window.pages["settings"]
    window.go_to("settings")

    page.similarity.setValue(4)
    page._save()
    assert window.settings.similarity_threshold == 4

    page.weight_sliders["sharpness"].setValue(50)
    page._apply_weights()
    assert window.settings.weights.sharpness == 5.0
    assert all(0 <= photo.score <= 100 for photo in window.library.ranked())


def test_settings_can_empty_the_index(window, monkeypatch):
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question",
        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes),
    )
    window.pages["settings"]._reset_database()
    assert window.library.db.count() == 0


def test_only_one_job_of_a_kind_runs_at_a_time(window, monkeypatch):
    seen = []
    monkeypatch.setattr(window, "notify", lambda *args, **kwargs: seen.append(args))

    import time

    first = window.run_worker("scan", lambda progress: time.sleep(0.4))
    second = window.run_worker("scan", lambda progress: None)
    assert first is not None
    assert second is None
    assert seen and "Already running" in seen[0][0]
    first.wait(4000)
