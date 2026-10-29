import type { ClaimViewDto, ConsistencyDto, GraphNodeDto, GraphStatusDto, GraphSummaryDto, SubgraphDto } from '../types/graph'

export const researchId = '11111111-1111-4111-8111-111111111111'
export const snapshotId = '22222222-2222-4222-8222-222222222222'
export const evidenceId = '33333333-3333-4333-8333-333333333333'
const common = { research_job_id: researchId }

export const research: GraphNodeDto = { id: `research:${researchId}`, type: 'RESEARCH', label: 'How does system A perform?', properties: { ...common, question: 'How does system A perform?', status: 'COMPLETED', created_at: '2026-10-29' } }
export const subquestion: GraphNodeDto = { id: 'subquestion:1', type: 'SUBQUESTION', label: 'Does it improve recall?', properties: { ...common, subquestion_id: 1, plan_id: 'recall', question: 'Does it improve recall?', priority: 1, status: 'COMPLETED', order: 0 } }
export const claim: GraphNodeDto = { id: 'claim:claim-1', type: 'CLAIM', label: 'System A improves recall.', properties: { ...common, claim_id: 'claim-1', subquestion_id: 1, claim_text: 'System A improves recall.', normalized_claim: 'system a improves recall', claim_type: 'FACTUAL', status: 'CONTESTED', confidence_tier: 'UNRESOLVED', support_count: 1, contradiction_count: 1, partial_count: 0, independent_support_groups: 1, independent_contradiction_groups: 1, needs_more_verification: true } }
export const support: GraphNodeDto = { id: 'evidence_chunk:1', type: 'EVIDENCE_CHUNK', label: 'Study A', properties: { ...common, chunk_id: 1, chunk_index: 0, heading: 'Results', text_hash: 'hash-1', source_title: 'Study A', source_url: 'https://example.com/a', source_domain: 'example.com', token_count: 54 } }
export const contradiction: GraphNodeDto = { id: 'evidence_chunk:2', type: 'EVIDENCE_CHUNK', label: 'Study B', properties: { ...common, chunk_id: 2, chunk_index: 0, heading: 'Results', text_hash: 'hash-2', source_title: 'Study B', source_url: 'https://example.org/b', source_domain: 'example.org', token_count: 50 } }
export const citation: GraphNodeDto = { id: 'citation:1', type: 'CITATION', label: 'Recall increased on Dataset X.', properties: { ...common, citation_id: 1, relation_id: 1, claim_id: 'claim-1', chunk_id: 1, document_id: 1, relation: 'SUPPORTING', start_offset: 0, end_offset: 30, exact_text: 'Recall increased on Dataset X.', source_url: 'https://example.com/a', valid: true } }
export const contraCitation: GraphNodeDto = { id: 'citation:2', type: 'CITATION', label: 'Recall decreased on Dataset X.', properties: { ...common, citation_id: 2, relation_id: 2, claim_id: 'claim-1', chunk_id: 2, document_id: 2, relation: 'CONTRADICTING', start_offset: 0, end_offset: 30, exact_text: 'Recall decreased on Dataset X.', source_url: 'https://example.org/b', valid: true } }
export const document: GraphNodeDto = { id: 'document:1', type: 'DOCUMENT', label: 'Study A', properties: { ...common, document_id: 1, title: 'Study A', canonical_url: 'https://example.com/a', source_url: 'https://example.com/a', source_domain: 'example.com', content_hash: 'hash-a' } }
export const contraDocument: GraphNodeDto = { id: 'document:2', type: 'DOCUMENT', label: 'Study B', properties: { ...common, document_id: 2, title: 'Study B', canonical_url: 'https://example.org/b', source_url: 'https://example.org/b', source_domain: 'example.org', content_hash: 'hash-b' } }
export const group: GraphNodeDto = { id: 'source_group:group-1', type: 'SOURCE_GROUP', label: 'Source group 1', properties: { ...common, source_group_id: 'group-1', group_key: 'key', grouping_method: null, member_count: 1, source_type: 'WEB' } }
export const contraGroup: GraphNodeDto = { id: 'source_group:group-2', type: 'SOURCE_GROUP', label: 'Source group 2', properties: { ...common, source_group_id: 'group-2', group_key: 'key2', grouping_method: null, member_count: 1, source_type: 'WEB' } }

const edge = (id: string, type: SubgraphDto['edges'][number]['type'], source: string, target: string, extra: Record<string, string | number | boolean> = {}): SubgraphDto['edges'][number] => ({ id, type, source, target, properties: { source_entity_id: id, ...extra } })
export const graph: SubgraphDto = { nodes: [research, subquestion, claim, support, contradiction, citation, contraCitation, document, contraDocument, group, contraGroup], edges: [
  edge('e1', 'HAS_SUBQUESTION', research.id, subquestion.id),
  edge('e2', 'HAS_CLAIM', subquestion.id, claim.id),
  edge('e3', 'ORIGINATED_FROM', claim.id, support.id),
  edge('e4', 'SUPPORTED_BY', claim.id, support.id, { source_group_id: 'group-1', claim_evidence_relation_id: 1, citation_valid: true }),
  edge('e5', 'PARTIALLY_SUPPORTED_BY', claim.id, support.id),
  edge('e6', 'CONTRADICTED_BY', claim.id, contradiction.id, { source_group_id: 'group-2', claim_evidence_relation_id: 2, citation_valid: true }),
  edge('e7', 'HAS_CITATION', claim.id, citation.id),
  edge('e8', 'CITES_CHUNK', citation.id, support.id),
  edge('e9', 'PART_OF_DOCUMENT', support.id, document.id),
  edge('e10', 'MEMBER_OF_SOURCE_GROUP', document.id, group.id),
  edge('e11', 'HAS_CITATION', claim.id, contraCitation.id),
  edge('e12', 'CITES_CHUNK', contraCitation.id, contradiction.id),
  edge('e13', 'PART_OF_DOCUMENT', contradiction.id, contraDocument.id),
  edge('e14', 'MEMBER_OF_SOURCE_GROUP', contraDocument.id, contraGroup.id),
], truncated: false }

export const initialGraph: SubgraphDto = { nodes: [research, subquestion, claim], edges: graph.edges.slice(0, 2), truncated: false }
export const claimView: ClaimViewDto = { claim: claim as Extract<GraphNodeDto, { type: 'CLAIM' }>, status: 'CONTESTED', confidence: 'UNRESOLVED', subquestions: [subquestion], origin_chunks: [support], supporting_chunks: [support], partial_support_chunks: [], contradicting_chunks: [contradiction], citations: [citation, contraCitation] as Extract<GraphNodeDto, { type: 'CITATION' }>[], documents: [document, contraDocument], source_groups: [group, contraGroup], nodes: graph.nodes, edges: graph.edges, connectivity: { origin_count: 1, document_count: 2 } }
export const status: GraphStatusDto = { graph_snapshot_id: snapshotId, graph_job_id: 'job', research_job_id: researchId, evidence_job_id: evidenceId, status: 'COMPLETED', graph_schema_version: 1, input_fingerprint: 'fingerprint', graph_content_hash: 'hash', node_count: 11, edge_count: 14, validation_status: 'VALID', validation_errors: [], nodes_by_type: {}, edges_by_type: {} }
export const summary: GraphSummaryDto = { claims_total: 1, claims_corroborated: 0, claims_supported: 0, claims_partial: 0, claims_contested: 1, claims_contradicted: 0, claims_insufficient: 0, citations_total: 2, documents_total: 2, source_groups_total: 2, support_edges: 1, partial_support_edges: 0, contradiction_edges: 1, source_group_contribution: {} }
export const consistency: ConsistencyDto = { status: 'VALID', issues: [], node_coverage: 1, edge_coverage: 1, cross_job_leakage_count: 0, input_fingerprint_matches: true }
