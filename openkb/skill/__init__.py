"""All skill-related code (generator, marketplace, agent runtime, validator,
workspace, evaluator) lives in this subpackage.

Today's only artifact type is ``skill``; the generator + marketplace
abstractions are nominally generic, but in v0.x they only serve skill
artifacts. If/when ppt / podcast / report targets land, factor the
generic primitives back out to ``openkb/<shared>/`` at that time.
"""
