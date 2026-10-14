"""Windows Qt desktop entry point; no admin elevation or system mutation on import."""
import os
import sys


def main():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from app.core.settings import Settings
    from app.i18n import set_language
    from app.ui.theme import apply_theme
    from app.ui.main_window import MainWindow
    app = QApplication(sys.argv)
    app.setApplicationName('EDIUS Booster')
    app.setOrganizationName('Mohammad Irani')
    settings = Settings()
    set_language(settings.get('language','en'))
    apply_theme(app)
    window = MainWindow(settings)
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
