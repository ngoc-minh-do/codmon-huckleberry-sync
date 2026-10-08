# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims
to adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Activity memos are now translated paragraph by paragraph, so paragraphs are
  never silently dropped when the LLM output omits part of the memo.
- Truncated or missing LLM translations now fall back to the original memo text
  instead of a partial translation.

### Added

- `sync-growth` backfill command for Codmon growth measurements (height,
  weight, head circumference).
- Optional Japanese→English translation of activity memos via an
  OpenAI-compatible chat-completions endpoint (`TRANSLATE_ACTIVITY`).
- Apprise success/failure notifications after real syncs.

[Unreleased]: https://github.com/ngoc-minh-do/codmon-huckleberry-sync/commits/main
