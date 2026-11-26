# Unity project seed

`studio.unity.bootstrap` copies `Packages/`, `ProjectSettings/`, and
`Assets/Settings/` into a new job project. Keep these files, their Unity `.meta`
files, and `Packages/packages-lock.json` together. The seed targets Unity
6000.3.24f1; its 2D URP assets are filtered when bootstrapping 3D or mixed games.
Runtime scripts, Editor builders, MCP startup code, and tests are installed from
`studio/templates/` rather than from this seed.

The build scene list starts empty. The scene builder replaces it with the scenes
assembled for each approved game. Legacy demo scripts, scenes, caches, and local
editor state in this directory are excluded from Git and are not prerequisites
for generation. They may still exist in a developer's local checkout.
