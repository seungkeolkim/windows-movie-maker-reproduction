"""Interactive product mock-up UI.

The package deliberately keeps view widgets separate from the in-memory mock
controller.  Production media and persistence services can replace the mock
controller without redefining the user-facing screen contract.
"""

from movie_maker.ui.main_window import MainWindow
from movie_maker.ui.mock_controller import MockController

__all__ = ["MainWindow", "MockController"]
