import sys

from .core.logger import setup_logging


def main() -> int:
    log = setup_logging()
    log.info("Iniciando TechDiag")
    try:
        from PySide6.QtWidgets import QApplication
        from .ui.main_window import MainWindow
    except ImportError as e:
        print(f"Dependências ausentes: {e}")
        return 2
    app = QApplication(sys.argv)
    app.setApplicationName("TechDiag")
    win = MainWindow()
    win.show()
    return app.exec()
