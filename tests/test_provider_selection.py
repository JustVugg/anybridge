from anybridge.server import BridgeRuntime

def test_runtime_accepts_selenium_provider():
    runtime = BridgeRuntime(provider="selenium")
    assert runtime.bridge.__class__.__name__ == "SeleniumDriver"
