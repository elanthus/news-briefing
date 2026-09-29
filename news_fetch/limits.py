"""Corpus caps and model-visible field and context budgets."""

from __future__ import annotations

import corpus_schema

DEFAULT_WINDOW_HOURS = 24
DEFAULT_SOURCE_CAP = 25
DEFAULT_CATEGORY_CAP = 60
# dev_community's five subreddits would otherwise each contribute up to
# DEFAULT_SOURCE_CAP items, and Reddit's higher post volume then crowds out
# the category's vendor release feeds and Hacker News coverage. A lower cap
# bounds Reddit's share without removing it as a source; min() with the
# configured --source-cap in curate_categories() so an explicitly stricter cap still wins.
REDDIT_SOURCE_CAP = 8

SUMMARY_CHARS = corpus_schema.ITEM_SUMMARY_MAX_CHARS
TITLE_BYTES = corpus_schema.ITEM_TITLE_MAX_BYTES
TITLE_TOKENS = corpus_schema.ITEM_TITLE_MAX_TOKENS
SUMMARY_BYTES = corpus_schema.ITEM_SUMMARY_MAX_BYTES
SUMMARY_TOKENS = corpus_schema.ITEM_SUMMARY_MAX_TOKENS
SOURCE_ID_BYTES = corpus_schema.ITEM_SOURCE_MAX_BYTES
SOURCE_ID_TOKENS = corpus_schema.ITEM_SOURCE_MAX_TOKENS
QUERY_BYTES = corpus_schema.ITEM_QUERY_MAX_BYTES
QUERY_TOKENS = corpus_schema.ITEM_QUERY_MAX_TOKENS
SOURCE_CONTEXT_BYTES = corpus_schema.SOURCE_CONTEXT_MAX_BYTES
SOURCE_CONTEXT_TOKENS = corpus_schema.SOURCE_CONTEXT_MAX_TOKENS
GLOBAL_CONTEXT_BYTES = corpus_schema.GLOBAL_CONTEXT_MAX_BYTES
GLOBAL_CONTEXT_TOKENS = corpus_schema.GLOBAL_CONTEXT_MAX_TOKENS
