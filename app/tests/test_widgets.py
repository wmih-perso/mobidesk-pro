import os

import pytest


@pytest.fixture()
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_clear_button_appears_with_text_and_clears_search(qt_app):
    from PySide6.QtWidgets import QLineEdit

    from app.ui.widgets import add_clear_button

    field = QLineEdit()
    add_clear_button(field)
    [action] = field.actions()
    assert not action.isVisible()

    field.setText("A12")
    assert action.isVisible()

    action.trigger()
    assert field.text() == ""
    assert not action.isVisible()
