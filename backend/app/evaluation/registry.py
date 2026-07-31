"""Suite registry: adding a new agent eval does not change the runner."""

from app.evaluation.base import EvaluationSuite, SuiteInfo

_suites: dict[str, EvaluationSuite] = {}
_loaded = False


def register(suite: EvaluationSuite) -> EvaluationSuite:
    name = suite.info.name
    if name in _suites:
        raise ValueError(f"Evaluation suite already registered: {name}")
    _suites[name] = suite
    return suite


def _load_builtins() -> None:
    global _loaded
    if _loaded:
        return
    from app.evaluation import suites  # noqa: F401

    _loaded = True


def get_suite(name: str) -> EvaluationSuite:
    _load_builtins()
    try:
        return _suites[name]
    except KeyError as exc:
        raise KeyError(f"Unknown evaluation suite: {name}") from exc


def list_suites() -> list[SuiteInfo]:
    _load_builtins()
    return sorted((suite.info for suite in _suites.values()), key=lambda item: item.name)
