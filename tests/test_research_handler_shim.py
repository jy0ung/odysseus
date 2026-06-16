def test_services_research_handler_is_canonical_shim():
    import services.research.research_handler as service_handler
    import src.research_handler as canonical_handler

    assert service_handler.ResearchHandler is canonical_handler.ResearchHandler
    assert service_handler.RESEARCH_DATA_DIR is canonical_handler.RESEARCH_DATA_DIR
