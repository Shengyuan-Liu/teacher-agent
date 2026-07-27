"""Retrieval-augmented generation: everything from raw file to ranked context.

Ingestion   pdf_convert -> extract -> chunking
Retrieval   dense + sparse -> fusion -> rerank, orchestrated by retriever
Evaluation  evaluation/ measures the above and writes reports to logs/

The QA graph that consumes this lives in `app.agents`; keeping the decision
flow out of here stops retrieval and agent concerns from tangling.
"""
