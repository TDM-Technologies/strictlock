---
id: 01HZY-wire-fences
status: done
summary: splice only between the fence markers
updated: 2026-06-25T09:05:00Z
---

Splicing preserves every byte outside the fenced region, so the generated table can
live inside a hand-maintained README without ever clobbering the surrounding prose.
A missing marker is a hard refusal, never a silent append.
