export const GRAPH_NODE_TYPES = [
  'RESEARCH', 'SUBQUESTION', 'CLAIM', 'CITATION',
  'EVIDENCE_CHUNK', 'DOCUMENT', 'SOURCE_GROUP',
] as const
export type GraphNodeType = (typeof GRAPH_NODE_TYPES)[number]

export const GRAPH_EDGE_TYPES = [
  'HAS_SUBQUESTION', 'HAS_CLAIM', 'ORIGINATED_FROM', 'SUPPORTED_BY',
  'PARTIALLY_SUPPORTED_BY', 'CONTRADICTED_BY', 'HAS_CITATION',
  'CITES_CHUNK', 'PART_OF_DOCUMENT', 'MEMBER_OF_SOURCE_GROUP',
] as const
export type GraphEdgeType = (typeof GRAPH_EDGE_TYPES)[number]

export const CLAIM_STATUSES = [
  'CORROBORATED', 'SUPPORTED', 'PARTIALLY_SUPPORTED',
  'CONTESTED', 'CONTRADICTED', 'INSUFFICIENT_EVIDENCE',
] as const
export type ClaimStatus = (typeof CLAIM_STATUSES)[number]
export type ConfidenceTier = 'HIGH' | 'MEDIUM' | 'LOW' | 'UNRESOLVED'

interface CommonProperties { research_job_id: string }
export interface ResearchProperties extends CommonProperties { question: string; status: string; created_at: string }
export interface SubquestionProperties extends CommonProperties {
  subquestion_id: number; plan_id: string; question: string; priority: number; status: string; order: number
}
export interface ClaimProperties extends CommonProperties {
  claim_id: string; subquestion_id: number; claim_text: string; normalized_claim: string
  claim_type: string; status: ClaimStatus; confidence_tier: ConfidenceTier
  support_count: number; contradiction_count: number; partial_count: number
  independent_support_groups: number; independent_contradiction_groups: number
  needs_more_verification: boolean
}
export interface CitationProperties extends CommonProperties {
  citation_id: number; relation_id: number; claim_id: string; chunk_id: number; document_id: number
  relation: string; start_offset: number; end_offset: number; exact_text: string
  source_url: string; valid: boolean
}
export interface ChunkProperties extends CommonProperties {
  chunk_id: number; chunk_index: number; heading: string | null; text_hash: string
  source_title: string | null; source_url: string; source_domain: string; token_count: number
  text_excerpt?: string | null
}
export interface DocumentProperties extends CommonProperties {
  document_id: number; title: string | null; canonical_url: string | null
  source_url: string; source_domain: string; content_hash: string
}
export interface SourceGroupProperties extends CommonProperties {
  source_group_id: string; group_key: string; grouping_method: string | null
  member_count: number; source_type: string
}

type TypedNode<T extends GraphNodeType, P> = { id: string; type: T; label: string; properties: P }
export type GraphNodeDto =
  | TypedNode<'RESEARCH', ResearchProperties>
  | TypedNode<'SUBQUESTION', SubquestionProperties>
  | TypedNode<'CLAIM', ClaimProperties>
  | TypedNode<'CITATION', CitationProperties>
  | TypedNode<'EVIDENCE_CHUNK', ChunkProperties>
  | TypedNode<'DOCUMENT', DocumentProperties>
  | TypedNode<'SOURCE_GROUP', SourceGroupProperties>

export interface GraphEdgeDto {
  id: string; type: GraphEdgeType; source: string; target: string
  properties: {
    source_entity_id: string; claim_evidence_relation_id?: number; document_id?: number
    source_group_id?: string; citation_valid?: boolean; nli_relation?: string
    adjudicated_relation?: string; retrieval_score?: number | null
    [key: string]: string | number | boolean | null | undefined
  }
}
export interface SubgraphDto { nodes: GraphNodeDto[]; edges: GraphEdgeDto[]; truncated: boolean }
export interface GraphStatusDto {
  graph_snapshot_id: string; graph_job_id: string | null; research_job_id: string
  evidence_job_id: string; status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'PARTIAL' | 'FAILED'
  graph_schema_version: number; input_fingerprint: string; graph_content_hash: string | null
  node_count: number; edge_count: number; validation_status: string
  validation_errors: { code: string; [key: string]: unknown }[]
  nodes_by_type: Record<string, number>; edges_by_type: Record<string, number>
}
export interface GraphSummaryDto {
  claims_total: number; claims_corroborated: number; claims_supported: number
  claims_partial: number; claims_contested: number; claims_contradicted: number
  claims_insufficient: number; citations_total: number; documents_total: number
  source_groups_total: number; support_edges: number; partial_support_edges: number
  contradiction_edges: number; source_group_contribution: Record<string, {
    claims_supported: number; claims_contradicted: number; citations_provided: number; unique_documents: number
  }>
}
export interface ConsistencyDto {
  status: 'VALID' | 'INVALID'; issues: { code: string; [key: string]: unknown }[]
  node_coverage: number; edge_coverage: number; cross_job_leakage_count: number
  input_fingerprint_matches: boolean
}
export interface ClaimViewDto {
  claim: Extract<GraphNodeDto, { type: 'CLAIM' }>; status: ClaimStatus; confidence: ConfidenceTier
  subquestions: GraphNodeDto[]; origin_chunks: GraphNodeDto[]; supporting_chunks: GraphNodeDto[]
  partial_support_chunks: GraphNodeDto[]; contradicting_chunks: GraphNodeDto[]
  citations: Extract<GraphNodeDto, { type: 'CITATION' }>[]
  documents: GraphNodeDto[]; source_groups: GraphNodeDto[]
  nodes: GraphNodeDto[]; edges: GraphEdgeDto[]
  connectivity: Record<string, number>
}
export interface NodeDetailDto {
  node: GraphNodeDto & { text?: string }; neighbors: GraphNodeDto[]
  incoming_edge_counts: Record<string, number>; outgoing_edge_counts: Record<string, number>
}
export interface PathDto { nodes: GraphNodeDto[]; edges: GraphEdgeDto[] }
export interface PathResponseDto { paths: PathDto[]; count: number }
export interface GraphBuildDto { graph_job_id: string; graph_snapshot_id: string; status: string }
