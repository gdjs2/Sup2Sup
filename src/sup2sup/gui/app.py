import sys


def main(argv: list[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("The GUI requires PySide6. Install it with: uv sync --extra gui", file=sys.stderr)
        return 1
    from .main_window import MainWindow

    args = sys.argv[1:] if argv is None else argv
    application = QApplication(["sup2sup"])
    application.setApplicationName("Sup2Sup")
    application.setOrganizationName("Sup2Sup")
    application.setStyle("Fusion")
    window = MainWindow()
    window.show()
    if args:
        window.open_path(args[0])
    return application.exec()
