from anybridge.providers import available_providers, default_browser, provider_profiles, select_provider


def test_provider_selection_prefers_explicit_provider():
    assert select_provider(["bdd"], preferred="selenium", browser="chrome", available=("playwright", "selenium")) == "selenium"


def test_provider_selection_uses_capabilities_and_browser():
    assert select_provider(["webmcp"], browser="edge", available=("playwright", "selenium")) == "selenium"
    assert select_provider(["webmcp"], browser="webkit", available=("playwright", "selenium")) == "playwright"


def test_provider_selection_is_dependency_aware():
    assert select_provider(["webmcp"], available=("playwright",)) == "playwright"


def test_provider_profiles_are_explicit():
    assert {p.name for p in provider_profiles()} == {"playwright", "selenium"}


def test_default_browser_is_concrete():
    assert default_browser("playwright") == "chromium"
    assert default_browser("selenium") == "chrome"


def test_available_providers_matches_importable_runtime():
    assert set(available_providers()) >= {"playwright"}
