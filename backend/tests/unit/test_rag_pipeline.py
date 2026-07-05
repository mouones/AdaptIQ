import pytest
from rag.agentic import RouterAgent

def test_router_agent_routing_logic():
    router = RouterAgent()
    
    # 1. Hard Geography -> Wikidata boost
    res = router.route(topic="Geography", difficulty=4, user_accuracy=0.8)
    assert res["weights"]["wikidata"] == 40
    assert res["weights"]["wikipedia"] == 40
    
    # 2. Hard History -> Wikipedia boost
    res = router.route(topic="History", difficulty=5, user_accuracy=0.8)
    assert res["weights"]["wikipedia"] == 70
    assert res["weights"]["wikidata"] == 20
    
    # 3. Easy -> HF boost
    res = router.route(topic="Science", difficulty=1, user_accuracy=0.8)
    assert res["weights"]["huggingface"] == 35
    assert res["weights"]["wikipedia"] == 60
    
    # 4. Low accuracy user -> Easier sources
    res = router.route(topic="Science", difficulty=3, user_accuracy=0.3)
    assert res["weights"]["huggingface"] == 35 # 20 + 15
    assert res["weights"]["wikipedia"] == 60 # 70 - 10
    
def test_router_agent_strategy_desc():
    router = RouterAgent()
    assert router._describe_strategy("Math", 1, 0.5) == "easy_recall"
    assert router._describe_strategy("Math", 3, 0.5) == "conceptual_connections"
    assert router._describe_strategy("Math", 5, 0.5) == "multi_hop_inference"
