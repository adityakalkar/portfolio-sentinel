def test_package_imports():
    # Proves the package installs and imports; keeps CI meaningful before real tests exist.
    import pm_agent

    assert pm_agent.__doc__
