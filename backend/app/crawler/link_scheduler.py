import json
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.crawler.frontier import Frontier, PriorityFrontier
from app.crawler.models import (
    CrawlMode,
    FrontierItem,
    RejectionReason,
    ScoringStatus,
)
from app.crawler.normalizer import hostname, is_private_address
from app.crawler.scoring import LinkCandidate, ScoringFailure, SemanticScorer
from app.db.models import CrawledPage, CrawlJob, DiscoveredLink

logger = logging.getLogger("spidermind.scheduler")


class LinkScheduler:
    """Turns persisted links into eligible, scored frontier items."""

    def __init__(
        self,
        job: CrawlJob,
        frontier: Frontier | PriorityFrontier,
        scorer: SemanticScorer | None,
    ) -> None:
        self.job = job
        self.frontier = frontier
        self.scorer = scorer
        self.root_domain = hostname(job.start_url)
        self.allowed_domains = set(json.loads(job.allowed_domains))
        self.seen = {job.start_url}
        self.discovery_order = 0

    def in_scope(self, url: str) -> bool:
        domain = hostname(url)
        return (domain == self.root_domain or self.job.allow_external_domains) and (
            not self.allowed_domains or domain in self.allowed_domains
        )

    async def schedule(
        self,
        session: AsyncSession,
        source_page: CrawledPage,
        links: list[DiscoveredLink],
    ) -> None:
        eligible: list[DiscoveredLink] = []
        for row in links:
            target = row.normalized_target_url
            if target in self.seen:
                row.rejection_reason = RejectionReason.DUPLICATE_URL
                continue
            self.seen.add(target)
            self.discovery_order += 1
            row.discovery_order = self.discovery_order
            self.job.pages_discovered += 1
            logger.info("URL discovered: %s", target, extra={"job_id": self.job.id})
            if row.target_depth > self.job.max_depth:
                row.rejection_reason = RejectionReason.DEPTH_LIMIT
            elif is_private_address(hostname(target)):
                row.rejection_reason = RejectionReason.SSRF_BLOCKED
            elif hostname(target) != self.root_domain and not self.job.allow_external_domains:
                row.rejection_reason = RejectionReason.EXTERNAL_DOMAIN_DISABLED
            elif self.allowed_domains and hostname(target) not in self.allowed_domains:
                row.rejection_reason = RejectionReason.DOMAIN_NOT_ALLOWED
            if row.rejection_reason:
                self.job.pages_skipped += 1
                logger.info("URL skipped: %s", target, extra={"job_id": self.job.id})
            else:
                eligible.append(row)

        if self.job.crawl_mode == CrawlMode.INTELLIGENT and eligible:
            if self.scorer is None:
                raise RuntimeError("Intelligent mode requires a scorer")
            logger.info("link scoring started", extra={"job_id": self.job.id})
            candidates = [
                LinkCandidate(
                    normalized_url=row.normalized_target_url,
                    anchor_text=row.anchor_text,
                    source_page_title=row.source_page_title,
                    link_context=row.link_context or "",
                    depth=row.target_depth,
                )
                for row in eligible
            ]
            try:
                scores = await self.scorer.score_batch(candidates)
            except Exception as exc:
                raise ScoringFailure("Candidate embedding or scoring failed") from exc
            for row, score in zip(eligible, scores, strict=True):
                row.relevance_score = score.relevance_score
                row.priority_score = score.priority_score
                row.depth_penalty = score.depth_penalty
                row.scoring_status = ScoringStatus.SCORED
                row.scoring_reason = "cosine_similarity(query, candidate); depth penalty applied"
                self.job.links_scored += 1
                self.job.relevance_score_sum += score.relevance_score
                if (
                    self.job.highest_relevance_score is None
                    or score.relevance_score > self.job.highest_relevance_score
                ):
                    self.job.highest_relevance_score = score.relevance_score
                if (
                    self.job.min_relevance_score is not None
                    and score.relevance_score < self.job.min_relevance_score
                ):
                    row.rejection_reason = RejectionReason.LOW_RELEVANCE
                    self.job.links_below_threshold += 1
                    self.job.pages_skipped += 1
                    logger.info(
                        "low relevance URL rejected: %s", row.normalized_target_url,
                        extra={"job_id": self.job.id},
                    )
            logger.info(
                "link scoring completed: %s candidates", len(eligible),
                extra={"job_id": self.job.id},
            )

        for row in eligible:
            if row.rejection_reason:
                continue
            row.selected_for_crawl = True
            item = FrontierItem(
                url=row.target_url,
                normalized_url=row.normalized_target_url,
                depth=row.target_depth,
                parent_url=source_page.final_url,
                priority=row.priority_score or 0.0,
                discovery_order=row.discovery_order,
                relevance_score=row.relevance_score,
                priority_score=row.priority_score,
                anchor_text=row.anchor_text,
                link_context=row.link_context,
                source_link_id=row.id,
            )
            self.frontier.push(item)
            if row.priority_score is not None:
                logger.info(
                    "URL priority assigned: %s score=%.4f", row.normalized_target_url,
                    row.priority_score,
                    extra={"job_id": self.job.id},
                )
        await session.commit()

    async def reject_selected(
        self, session: AsyncSession, item: FrontierItem, reason: RejectionReason
    ) -> None:
        if item.source_link_id is None:
            return
        row = await session.get(DiscoveredLink, item.source_link_id)
        if row is not None:
            row.selected_for_crawl = False
            row.rejection_reason = reason
