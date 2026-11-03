# Console design

Reference research was performed in Mobbin's signed-in Pro web library.

- [Linear Web collection](https://mobbin.com/apps/linear-web-9591f534-b2b8-40aa-a91a-fdbe183d0a5f/efdb5b1e-be6d-4321-ae35-b4df6801a3eb/screens)
- [Linear AI workspace welcome](https://mobbin.com/screens/a0193431-e1b3-44c4-b1a2-1d1f4a017ccc)
- [Linear AI conversation](https://mobbin.com/screens/afa62cbe-083b-4234-ad90-8a689d8e28cd)

Applied principles: persistent left navigation, clear selected context, quiet borders,
a spacious composer, progressive disclosure for technical details, and readable output.
The gateway uses its own olive and charcoal palette, wordmark, SVG icons, and layouts.
No Mobbin screenshot or third-party application asset is bundled in the product.

The thread list is the primary navigation. A selected thread's title, UUID and folder
remain visible beside the draft. Sending is an explicit action; a navigation click
does not accidentally submit a draft. New conversations and thread continuations
have visibly different destinations.

The console is served by FastAPI on the existing `/codex` mount. It needs no frontend
build, CDN, analytics service, browser storage or separate hosting account. Responsive
layouts stack the workspace panels on smaller screens and collapse navigation into
a drawer on narrow screens. Motion respects the reduced-motion preference.
