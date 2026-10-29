Visual assets for Phase 8A:

- `spider-reference.png` is the supplied 800 × 800 source image, retained unchanged for identity comparison.
- `spider-cutout.png` is a transparent background extraction of that spider. `SpiderHub` imports it for the central visual and header mark because the source has a white background and overlaid stock text.
- `spidermind-ui-reference.png` is the supplied 1672 × 941 design reference. The app never renders it as a background or graph data.

The cutout preserves the black spider and red X, but its surface details are reconstructed, so it is not pixel-identical to the source. The UI's labels, counts, claims, citations, and links come from the Phase 7 API.
