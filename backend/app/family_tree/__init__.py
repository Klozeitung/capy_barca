"""
The family tree view: the tree drawn from the family set of a database
(``app.blocks.kinship``) and the sibling mapper.

graph        – the family links of a database, read once; the scope of a tree
generations  – which row each person is drawn in
conflicts    – contradictions in the family data, plausibility warnings
siblings     – siblings derived from shared parents (the mapper)
render       – what the view draws, for every point in time
router       – the endpoints
"""
