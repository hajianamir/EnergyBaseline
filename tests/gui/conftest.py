"""Enforce real Qt widget destruction while the session QApplication is alive.

Legacy qapp-only tests close windows but do not register them with qtbot. Closing
hides an ordinary QWidget; it does not delete its C++ object. Letting those graphs
survive until Python/Qt global teardown can crash after every test has passed.
This fixture strengthens isolation without suppressing errors or changing tests.
"""

import gc

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid


@pytest.fixture(autouse=True)
def destroy_test_windows_before_qapplication_shutdown():
    yield
    app = QApplication.instance()
    if app is None:
        return
    # Child widgets are destroyed by their owning root, not deleted twice.
    roots = [widget for widget in app.topLevelWidgets() if widget.parent() is None]
    for widget in roots:
        if isValid(widget):
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
    gc.collect()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert all(not isValid(widget) for widget in roots), "Test-owned Qt window leaked beyond teardown"
