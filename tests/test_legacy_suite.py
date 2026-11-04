"""Include migrated analysis smoke tests in the default offline unittest command."""
def load_tests(loader, tests, pattern):
    for name in ("analysis.test.test_analysis_service", "analysis.test.test_db_source", "analysis.test.test_tools"):
        tests.addTests(loader.loadTestsFromName(name))
    return tests
