"""Deterministic Phase 7 fixture with all six Phase 6 claim statuses."""

import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

from app.db.models import CrawledPage, CrawlJob, ResearchJob, ResearchSubquestion
from app.evidence.models import (
    ClaimCitation,
    ClaimEvidenceRelation,
    ClaimOrigin,
    EvidenceJob,
    SourceGroup,
    VerifiedClaim,
)
from app.rag.models import KnowledgeChunk, KnowledgeChunkSource, KnowledgeDocument


def uid(label: str) -> str:
    return str(uuid5(NAMESPACE_URL, "spidermind-phase7-fixture:" + label))


async def seed_graph_fixture(sessions) -> dict:
    async with sessions() as session:
        research = ResearchJob(
            id=uid("research-a"), question="How reliable is GraphRAG evidence?",
            status="completed", request_json="{}", planner_model="fixture",
            max_subquestions=2, max_total_pages=10,
        )
        other = ResearchJob(
            id=uid("research-b"), question="How reliable is unrelated evidence?",
            status="completed", request_json="{}", planner_model="fixture",
            max_subquestions=1, max_total_pages=1,
        )
        crawl = CrawlJob(
            id=uid("crawl-a"), start_url="https://a.example/study", max_depth=0,
            max_pages=10, timeout=10, max_content_size=100000,
        )
        other_crawl = CrawlJob(
            id=uid("crawl-b"), start_url="https://other.example/study", max_depth=0,
            max_pages=1, timeout=10, max_content_size=100000,
        )
        session.add_all([research, other, crawl, other_crawl])
        await session.flush()
        subs = [
            ResearchSubquestion(
                research_job_id=research.id, plan_id=f"facet-{index}",
                question=f"What does facet {index} show?", rationale="Fixture",
                priority="high", expected_evidence="[]", preferred_source_types="[]",
                order=index,
            ) for index in (1, 2)
        ]
        other_sub = ResearchSubquestion(
            research_job_id=other.id, plan_id="other", question="Other question?",
            rationale="Fixture", priority="low", expected_evidence="[]",
            preferred_source_types="[]", order=0,
        )
        session.add_all([*subs, other_sub])
        await session.flush()
        texts = [
            "GraphRAG improves recall on Dataset X.",
            "GraphRAG does not improve recall on Dataset X.",
            "GraphRAG may improve recall in some cases.",
        ]
        documents = []
        chunks = []
        for index, text in enumerate(texts, 1):
            url = f"https://source{index}.example/study"
            page = CrawledPage(
                crawl_job_id=crawl.id, url=url, normalized_url=url,
                depth=0, crawl_status="COMPLETED", text_content=text,
            )
            session.add(page)
            await session.flush()
            document = KnowledgeDocument(
                research_job_id=research.id, crawl_job_id=crawl.id,
                crawled_page_id=page.id, source_url=url, title=f"Study {index}",
                canonical_url="https://source2.example/study" if index == 3 else None,
                source_domain=f"source{index}.example",
                content_hash=hashlib.sha256(text.encode()).hexdigest(),
                index_status="COMPLETED",
            )
            session.add(document)
            await session.flush()
            documents.append(document)
        # A canonical passage has two document occurrences, both in one Phase 6 group.
        for index, text in enumerate(texts):
            document = documents[index]
            chunk = KnowledgeChunk(
                research_job_id=research.id, document_id=document.id,
                crawl_job_id=crawl.id, crawled_page_id=document.crawled_page_id,
                source_url=document.source_url, source_title=document.title,
                source_domain=document.source_domain, chunk_index=0, text=text,
                text_hash=hashlib.sha256(text.encode()).hexdigest(),
                token_count=len(text.split()), vector_id=uid(f"vector-{index}"),
            )
            session.add(chunk)
            await session.flush()
            chunks.append(chunk)
            session.add(KnowledgeChunkSource(
                chunk_id=chunk.id, document_id=document.id,
                research_job_id=research.id, chunk_index=0,
            ))
        mirror_url = "https://mirror.example/study"
        mirror_page = CrawledPage(
            crawl_job_id=crawl.id, url=mirror_url, normalized_url=mirror_url,
            depth=0, crawl_status="COMPLETED", text_content=texts[0],
        )
        session.add(mirror_page)
        await session.flush()
        mirror_doc = KnowledgeDocument(
            research_job_id=research.id, crawl_job_id=crawl.id,
            crawled_page_id=mirror_page.id, source_url=mirror_url, title="Mirror",
            source_domain="mirror.example", content_hash=documents[0].content_hash,
            index_status="COMPLETED",
        )
        session.add(mirror_doc)
        await session.flush()
        session.add(KnowledgeChunkSource(
            chunk_id=chunks[0].id, document_id=mirror_doc.id,
            research_job_id=research.id, chunk_index=0,
        ))
        evidence = EvidenceJob(
            id=uid("evidence-a"), research_job_id=research.id,
            input_fingerprint="a" * 64, request_json="{}", status="COMPLETED",
        )
        other_evidence = EvidenceJob(
            id=uid("evidence-b"), research_job_id=other.id,
            input_fingerprint="b" * 64, request_json="{}", status="COMPLETED",
        )
        groups = [
            SourceGroup(
                id=uid("group-mirror"), evidence_job_id=evidence.id,
                group_key="1" * 64,
                document_ids_json=json.dumps([documents[0].id, mirror_doc.id]),
            ),
            SourceGroup(
                id=uid("group-independent"), evidence_job_id=evidence.id,
                group_key="2" * 64,
                document_ids_json=json.dumps([documents[1].id, documents[2].id]),
            ),
        ]
        session.add_all([evidence, other_evidence, *groups])
        await session.flush()
        status_names = [
            "CORROBORATED", "SUPPORTED", "PARTIALLY_SUPPORTED",
            "CONTESTED", "CONTRADICTED", "INSUFFICIENT_EVIDENCE",
        ]
        confidence = ["HIGH", "MEDIUM", "LOW", "UNRESOLVED", "UNRESOLVED", "UNRESOLVED"]
        claims = {}
        for index, (status, tier) in enumerate(zip(status_names, confidence, strict=True)):
            claim = VerifiedClaim(
                id=uid(status), stable_key=hashlib.sha256(status.encode()).hexdigest(),
                evidence_job_id=evidence.id, research_job_id=research.id,
                subquestion_id=subs[index % 2].id,
                claim_text=f"Fixture claim {status} on Dataset X.",
                normalized_claim=f"fixture claim {status.lower()} on dataset x.",
                claim_type="DESCRIPTIVE", status=status, confidence_tier=tier,
                needs_more_verification=status in {"CONTESTED", "INSUFFICIENT_EVIDENCE"},
            )
            session.add(claim)
            claims[status] = claim
        await session.flush()
        for claim in claims.values():
            session.add(ClaimOrigin(
                claim_id=claim.id, chunk_id=chunks[0].id,
                document_id=documents[0].id, source_url=documents[0].source_url,
            ))

        relations = [
            ("CORROBORATED", 0, "DIRECT_SUPPORT", 0, True),
            ("CORROBORATED", 1, "DIRECT_SUPPORT", 1, True),
            ("SUPPORTED", 0, "DIRECT_SUPPORT", 0, True),
            ("PARTIALLY_SUPPORTED", 2, "PARTIAL_SUPPORT", 1, False),
            ("CONTESTED", 0, "DIRECT_SUPPORT", 0, True),
            ("CONTESTED", 1, "CONTRADICTION", 1, True),
            ("CONTRADICTED", 1, "CONTRADICTION", 1, True),
            ("INSUFFICIENT_EVIDENCE", 2, "DIRECT_SUPPORT", 1, False),
        ]
        for status, chunk_index, decision, group_index, cited in relations:
            claim = claims[status]
            chunk = chunks[chunk_index]
            document = documents[chunk_index]
            row = ClaimEvidenceRelation(
                claim_id=claim.id, chunk_id=chunk.id, document_id=document.id,
                source_group_id=groups[group_index].id,
                source_url=document.source_url, source_domain=document.source_domain,
                retrieval_rank=chunk_index + 1, retrieval_score=0.5,
                nli_relation="ENTAILMENT" if decision != "CONTRADICTION" else "CONTRADICTION",
                entailment_score=0.9 if decision != "CONTRADICTION" else 0.05,
                neutral_score=0.05, contradiction_score=0.9 if decision == "CONTRADICTION" else 0.05,
                nli_model_name="fixture-nli", adjudicated_relation=decision,
                is_citation_candidate=cited,
            )
            session.add(row)
            await session.flush()
            if cited:
                session.add(ClaimCitation(
                    claim_id=claim.id, relation_id=row.id, chunk_id=chunk.id,
                    document_id=document.id, source_url=document.source_url,
                    start_offset=0, end_offset=len(chunk.text), exact_text=chunk.text,
                    relation="CONTRADICTING" if decision == "CONTRADICTION" else "SUPPORTING",
                ))
        other_claim = VerifiedClaim(
            id=uid("other-claim"), stable_key="c" * 64,
            evidence_job_id=other_evidence.id, research_job_id=other.id,
            subquestion_id=other_sub.id, claim_text="Other job secret claim.",
            normalized_claim="other job secret claim.", claim_type="DESCRIPTIVE",
            status="INSUFFICIENT_EVIDENCE", confidence_tier="UNRESOLVED",
        )
        session.add(other_claim)
        await session.commit()
        return {
            "research_job_id": research.id, "evidence_job_id": evidence.id,
            "other_research_job_id": other.id, "other_evidence_job_id": other_evidence.id,
            "claims": {status: row.id for status, row in claims.items()},
            "chunks": [row.id for row in chunks],
            "documents": [row.id for row in documents] + [mirror_doc.id],
            "groups": [row.id for row in groups],
        }
