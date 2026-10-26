
from mcad.db import session_scope
from mcad.models import Candidate

with session_scope() as session:
    candidate = Candidate(
        title="TEST 4- Second cyberattack",
        summary=(
            "This is a fictional maritime cybersecurity "
            "incident created to test the approval system."
        ),    
        source_url="https://example.com/mcad-test-incident-4",  
        matched_keywords=["maritime", "cyberattack", "port"],
        status="pending"
    )

    session.add(candidate)

print("Test incident successfully added!")

