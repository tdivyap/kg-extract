## A note on Q2 and Q4 (router-stage limitations)

For Q2, vanilla RAG produced a more complete answer than GraphRAG.
Inspecting the GraphRAG output shows the router resolved only 2 of 4
expected entities. To verify the graph itself contained the relationships,
the question was reformulated as a direct neighborhood query
("What does FDM depend on and what protects it?"). With that phrasing, the
router anchored on `comp.fdm` and the synthesis pulled HOSTD, vCenter,
and the watchdog from FDM's 1-hop neighborhood — all three correctly cited.

Diagnosis: the v1 router is sensitive to question phrasing. The graph
has the data; routing is the next thing to harden. Production upgrade
path documented in the methodology document (Stage 6).