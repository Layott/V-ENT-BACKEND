# Bundled fonts

## Inter-Bold.ttf
- **Font:** Inter (variable font, used at Bold weight for generated default avatars)
- **License:** SIL Open Font License 1.1 — see `OFL.txt`
- **Source:** https://github.com/google/fonts/tree/main/ofl/inter
- **Used by:** `vent_auth/views_helpers.py::_load_avatar_font()` to render user initials
  on the auto-generated profile picture. Bundling it keeps avatar generation
  working on Linux (prod EC2) where the old hardcoded Windows font path failed.
