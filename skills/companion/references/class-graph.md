# Class graph contract

The canonical implementation is the `section#class-graph` block in `../assets/page.html`,
including its scoped CSS, embedded data and script. It was captured from
http://35.174.243.82:3003/#class-graph on 2026-09-10. That server is a reference, not a
runtime dependency. Preserve the widget's design and interaction; adapt the content.

## Required behavior

- Question/story selector starts with a small meaningful scenario, not a dense graph.
- Class selector opens the selected class's direct neighborhood: incoming on the left,
  outgoing on the right. Selecting a neighbor continues exploration; Back to story returns.
- Calls / data / all-relationships filters distinguish calls, data and inheritance.
  Preserve the legend, arrow styles and optional dense all-connections checkbox.
- Cards retain domain color, new/existing/context identity, concise responsibility,
  preview methods/fields and the inline source arrow ↗ beside the class name.
  Do not restore a separate bulky source button or dump all members onto every card.
- Details retain the complete methods/fields and labelled, navigable connections.
- Preserve drag/scroll panning, −/+, Fit, 100%, Full screen, keyboard card selection,
  and localStorage restoration of selected view/class/story/filter/dense mode.

## Adapt only the session-specific model

The shipped export-reader graph is example data, not evidence for the current task.
Replace all of these together:

- `data`: nodes, edges and reviewed head; retain the node/member/edge object shapes.
- `url`: repository and commit-pinned source mapping. Context nodes without source
  must not receive fabricated links. Only emit safe source URLs.
- `colors`, `domains`, `roles`: meaningful owners and one-line responsibilities.
- `stories` and selector options: real questions, verified relationships and layouts.
  `story()` rejects a relationship absent from the underlying edge data.
- Default `lastStory`, default focus class and all-view description; do not leave
  `ConversationExportPageService`, export-specific counts or scenarios in a new report.
- Headings, counts, explanatory text and call-stack anchor (create its destination or
  remove that cross-link when there is no call-stack section).
- `preferenceKey`: scope it to the repository/session/page so different reports reusing
  the same port do not inherit one another's preferences. Validate restored selections.

Include relevant existing collaborators, not only new classes. For code without
classes, represent actual functions/modules/components as honestly labelled nodes in
the same explorer; never invent classes or relationships just to fill the template.
For a one-node change, show that real node rather than omit the graph. A graph can
explain structure without claiming independent review or runtime verification.

When serializing source data into an inline script, escape `<` as `\u003c` so source
strings cannot close the script element. Keep HTML escaping in the renderer. Do not
load executable content or diagram services from the reference server.

## Check the adapted graph

In a browser, exercise every story, selecting a card and a neighbor, Back to story,
all three relation filters, all-classes/dense mode, zoom/Fit/100%, panning and fullscreen.
Reload to confirm valid preferences restore. Verify source/member links against the
reviewed revision, no missing-node errors, reachable anchors and no page-wide overflow
at desktop and narrow widths. Graph-local scrolling is intentional. Do not claim the
graph is checked merely because its section heading exists.
