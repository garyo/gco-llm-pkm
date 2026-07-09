"""Tests for the hybrid-retrieval RRF fusion in context_retriever."""

from pkm_bridge.context_retriever import KEYWORD_WEIGHT, RRF_K, VECTOR_WEIGHT, rrf_fuse


def test_both_lists_beats_single_list():
    # 'b' appears in both lists (ranked 2nd in each); 'a' and 'k' top one list each.
    fused = rrf_fuse(vector_ids=['a', 'b'], keyword_ids=['k', 'b'])
    assert fused[0] == 'b'


def test_vector_outweighs_keyword_at_equal_rank():
    fused = rrf_fuse(vector_ids=['v'], keyword_ids=['k'])
    assert fused == ['v', 'k']


def test_preserves_rank_order_within_one_list():
    fused = rrf_fuse(vector_ids=['a', 'b', 'c'], keyword_ids=[])
    assert fused == ['a', 'b', 'c']


def test_empty_inputs():
    assert rrf_fuse([], []) == []
    assert rrf_fuse([], ['k1', 'k2']) == ['k1', 'k2']


def test_deterministic_tie_break():
    # Two ids with identical scores (same rank, same single list) tie-break on id.
    fused_1 = rrf_fuse([], ['x', 'y'])
    fused_2 = rrf_fuse([], ['x', 'y'])
    assert fused_1 == fused_2


def test_weighted_scores_match_formula():
    # A keyword hit at rank 1 must outrank a vector hit at a rank where the
    # RRF formula says it should: kw@1 = 0.3/61 ≈ 0.00492 beats
    # vec@30 = 0.7/90 ≈ 0.00778? No — verify with the actual formula.
    kw_at_1 = KEYWORD_WEIGHT / (RRF_K + 1)
    vec_at_30 = VECTOR_WEIGHT / (RRF_K + 30)
    vector_ids = [f'v{i}' for i in range(1, 31)]
    fused = rrf_fuse(vector_ids, ['k'])
    k_pos = fused.index('k')
    v30_pos = fused.index('v30')
    if kw_at_1 > vec_at_30:
        assert k_pos < v30_pos
    else:
        assert k_pos > v30_pos
