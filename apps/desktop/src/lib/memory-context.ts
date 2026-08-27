// Memory-context block detection/parsing for the thread renderer.
//
// The backend memory service (agent/memory_manager.py build_memory_context_block)
// wraps provider-prefetched memory in a provider-agnostic fence:
//
//   <memory-context>
//   [System note: The following is recalled memory context, NOT new user
//   input. Treat as authoritative reference data — ...]
//
//   ## Mnemosyne Context
//     [2026-08-27T10:06] (importance 0.80, source X) ...
//   </memory-context>
//
// Keying off the service's fence — NOT the "## Mnemosyne Context" header —
// keeps this backend-agnostic: the builtin provider is always first and the
// external provider is swappable (memory.provider config). The regex mirrors
// the backend's _MEMORY_CONTEXT_RE (tolerant whitespace inside the tags).

export const MEMORY_CONTEXT_FENCE_RE = /<\s*memory-context\s*>[\s\S]*?<\s*\/\s*memory-context\s*>/g

export interface MemoryContextSplit {
  /** The full fenced block(s), joined — null when the message has none. */
  block: string | null
  /** The message with fenced blocks removed (what the human actually typed). */
  userText: string
  /** Provider entry lines (timestamp-led), excluding the "[System note:" header. */
  entryCount: number
  wordCount: number
  /** Rough token estimate for the block (words × 1.35), for the chip label. */
  tokenEstimate: number
}

export function splitMemoryContext(text: string): MemoryContextSplit {
  const matches = text.match(MEMORY_CONTEXT_FENCE_RE) ?? []
  const block = matches.length > 0 ? matches.join('\n') : null
  const userText = block ? text.replace(MEMORY_CONTEXT_FENCE_RE, '').trim() : text
  const wordCount = block ? block.split(/\s+/).filter(Boolean).length : 0
  // Provider entries look like "  [2026-08-27T10:06] (importance 0.80, ...) ...".
  // Requiring a date start excludes the "[System note: ...]" header line.
  const entryCount = block ? (block.match(/^\s*\[\d{4}-\d{2}-\d{2}/gm) || []).length : 0

  return { block, userText, entryCount, wordCount, tokenEstimate: Math.round(wordCount * 1.35) }
}
