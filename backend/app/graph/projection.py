"""Bounded directed multigraph projection over one persisted snapshot."""

from collections import defaultdict, deque


class GraphProjection:
    def __init__(self, nodes: list[dict], edges: list[dict]) -> None:
        self.nodes = {node["id"]: node for node in nodes}
        self.edges = {edge["id"]: edge for edge in edges}
        self.outgoing: dict[str, list[dict]] = defaultdict(list)
        self.incoming: dict[str, list[dict]] = defaultdict(list)
        for edge in edges:
            self.outgoing[edge["source"]].append(edge)
            self.incoming[edge["target"]].append(edge)
        for mapping in (self.outgoing, self.incoming):
            for values in mapping.values():
                values.sort(key=lambda row: row["id"])

    def incident(self, node_id: str, direction: str = "both") -> list[dict]:
        if direction == "outgoing":
            return self.outgoing[node_id]
        if direction == "incoming":
            return self.incoming[node_id]
        return sorted(
            {edge["id"]: edge for edge in self.outgoing[node_id] + self.incoming[node_id]}.values(),
            key=lambda row: row["id"],
        )

    def neighborhood(
        self,
        root: str,
        *,
        depth: int,
        direction: str = "both",
        node_types: set[str] | None = None,
        edge_types: set[str] | None = None,
        max_nodes: int = 200,
        max_edges: int = 500,
    ) -> dict:
        if root not in self.nodes:
            raise KeyError(root)
        found = {root}
        edge_ids: set[str] = set()
        queue = deque([(root, 0)])
        truncated = False
        while queue:
            current, distance = queue.popleft()
            if distance >= depth:
                continue
            for edge in self.incident(current, direction):
                if edge_types and edge["type"] not in edge_types:
                    continue
                neighbor = edge["target"] if edge["source"] == current else edge["source"]
                if neighbor not in self.nodes:
                    continue
                if node_types and self.nodes[neighbor]["type"] not in node_types:
                    continue
                if neighbor not in found and len(found) >= max_nodes:
                    truncated = True
                    continue
                if edge["id"] not in edge_ids and len(edge_ids) >= max_edges:
                    truncated = True
                    continue
                edge_ids.add(edge["id"])
                if neighbor not in found:
                    found.add(neighbor)
                    queue.append((neighbor, distance + 1))
        return {
            "nodes": [self.nodes[key] for key in sorted(found)],
            "edges": [self.edges[key] for key in sorted(edge_ids)],
            "truncated": truncated,
        }

    def shortest_paths(
        self, source: str, target: str, *, max_hops: int, max_paths: int
    ) -> list[dict]:
        if source not in self.nodes or target not in self.nodes:
            raise KeyError("Path endpoint not found")
        if source == target:
            return [{"nodes": [self.nodes[source]], "edges": []}]
        queue = deque([(source, [source], [])])
        best_depth = None
        results = []
        seen_depth = {source: 0}
        while queue and len(results) < max_paths:
            current, node_path, edge_path = queue.popleft()
            if len(edge_path) >= max_hops or (
                best_depth is not None and len(edge_path) >= best_depth
            ):
                continue
            for edge in self.outgoing[current]:
                neighbor = edge["target"]
                if neighbor not in self.nodes or neighbor in node_path:
                    continue
                new_depth = len(edge_path) + 1
                if new_depth > seen_depth.get(neighbor, new_depth):
                    continue
                seen_depth[neighbor] = new_depth
                new_nodes, new_edges = node_path + [neighbor], edge_path + [edge]
                if neighbor == target:
                    best_depth = new_depth
                    results.append(
                        {
                            "nodes": [self.nodes[key] for key in new_nodes],
                            "edges": new_edges,
                        }
                    )
                    if len(results) >= max_paths:
                        break
                else:
                    queue.append((neighbor, new_nodes, new_edges))
        return results

    def connected_components(self) -> list[list[str]]:
        remaining = set(self.nodes)
        components = []
        while remaining:
            start = min(remaining)
            component = {start}
            queue = deque([start])
            remaining.remove(start)
            while queue:
                current = queue.popleft()
                for edge in self.incident(current):
                    neighbor = edge["target"] if edge["source"] == current else edge["source"]
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        component.add(neighbor)
                        queue.append(neighbor)
            components.append(sorted(component))
        return components
