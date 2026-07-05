# .dev/lessons/ — learned-the-hard-way operational knowledge

One `.md` per lesson, named for the SYMPTOM (kebab-case), e.g.
`ci-green-but-deploy-ships-broken-code.md`. Shape:
`When this bites` · `Symptoms` · `Root cause` · `Fix / workaround` ·
`How to detect early` · `Related`.

**Before debugging a non-obvious bug**, grep here for the symptom — follow the
fix and skip the rediscovery loop. **After fixing a non-obvious bug**, write the
lesson (~10 min) and link it from the commit. Index notable lessons in CLAUDE.md.
