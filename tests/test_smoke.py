"""Most basic smoke test — confirms the scaffold is importable."""


def test_imports() -> None:
    from src.common.config import settings
    from src.api.main import app

    assert settings is not None
    assert app.title == "PERSO Sales Console"
