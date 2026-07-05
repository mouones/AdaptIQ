"""Regression tests for test database constraints behavior."""

import pytest
from database.models import QuestionBank, User
from database.governance_models import GovernanceBlockRule, QuestionAudit
from sqlalchemy import String

def test_question_bank_topic_truncation_risk():
    # Verify the topic column is a String with length
    topic_col = QuestionBank.__table__.columns.get('topic')
    assert isinstance(topic_col.type, String)
    assert topic_col.type.length == 20

def test_governance_block_rule_uniqueness():
    # Verify there is a UniqueConstraint or unique index on kind, pattern
    table = GovernanceBlockRule.__table__
    unique_constraints = [c for c in table.constraints if type(c).__name__ == "UniqueConstraint"]
    unique_indexes = [i for i in table.indexes if i.unique]
    
    found = False
    for c in unique_constraints:
        if set(c.columns.keys()) == {'kind', 'pattern'}:
            found = True
            
    for i in unique_indexes:
        if set([c.name for c in i.columns]) == {'kind', 'pattern'}:
            found = True
            
    assert found is True

def test_question_audit_indexes():
    # Verify question_id has an index
    table = QuestionAudit.__table__
    indexes = [i for i in table.indexes]
    
    indexed_columns = set()
    for i in indexes:
        for c in i.columns:
            indexed_columns.add(c.name)
            
    assert 'question_id' in indexed_columns
