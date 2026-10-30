import type { GraphSummaryDto } from '../../types/graph'

export type ResearchStage = 'CREATING_RESEARCH' | 'RESEARCHING' | 'INDEXING' | 'RUNNING_AGENT' | 'VERIFYING_EVIDENCE' | 'BUILDING_GRAPH' | 'COMPLETED' | 'PARTIAL' | 'FAILED' | 'CANCELLED'

export interface ClaimSummary {
  id: string; claim: string; status: string; confidence_tier: string
  support_count: number; contradiction_count: number; independent_support_groups: number
}
export interface ResearchSource { url: string; title: string | null; selected: boolean; crawl_status: string | null; pages: { url: string; status: string }[] }

export interface SpiderMindRun {
  question: string; stage: ResearchStage; startedAt: string; updatedAt: string
  researchJobId?: string; indexJobId?: string; agentRunId?: string
  evidenceJobId?: string; graphSnapshotId?: string
  researchStatus?: string; researchStep?: string; agentStatus?: string; agentStopReason?: string
  warning?: string; error?: string; retryStage?: ResearchStage
  partialStages?: ResearchStage[]; initialPagesCrawled?: number; agentPagesCrawled?: number
  searchResults?: number; sourcesSelected?: number; pagesCrawled?: number
  documentsIndexed?: number; chunksCreated?: number; claimsExtracted?: number; citationsValidated?: number
  summary?: GraphSummaryDto; claims?: ClaimSummary[]; graphValid?: boolean
  sources?: ResearchSource[]; evidenceStep?: string
  resultDetailsError?: boolean
}
