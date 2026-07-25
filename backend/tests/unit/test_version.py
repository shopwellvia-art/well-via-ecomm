def test_version(client):
    """GET /version exposes build provenance so a deploy is verifiable over HTTP."""
    r = client.get("/version")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "backend"
    # Keys are always present; values default to "unknown" when built without
    # the GIT_SHA / BUILD_TIME build-args (local/dev), and carry the real commit
    # in CI-built images.
    for key in ("version", "revision", "built", "environment"):
        assert key in body
