import { describe, expect, it } from 'vitest'

import { splitMemoryContext } from './memory-context'

const FULL_BLOCK = `<memory-context>
[System note: The following is recalled memory context, NOT new user input. Treat as authoritative reference data — this is the agent's persistent memory and should inform all responses.]

## Mnemosyne Context
  [2026-08-25T12:26] (importance 0.95, source canonical:workflow) [CANONICAL] Field Notes report AGENT work only.
  [2026-08-06T17:39] (importance 0.95, source canonical:workflow) [CANONICAL] Session log v7.
</memory-context>`

describe('splitMemoryContext', () => {
  it('strips the fenced block and returns the real user text', () => {
    const split = splitMemoryContext(`what did we find about X?\n\n${FULL_BLOCK}`)

    expect(split.block).toBe(FULL_BLOCK)
    expect(split.userText).toBe('what did we find about X?')
  })

  it('returns text unchanged when no block is present', () => {
    const split = splitMemoryContext('just a normal prompt')

    expect(split.block).toBeNull()
    expect(split.userText).toBe('just a normal prompt')
    expect(split.entryCount).toBe(0)
    expect(split.tokenEstimate).toBe(0)
  })

  it('counts provider entries by timestamp line, excluding the system note', () => {
    const split = splitMemoryContext(FULL_BLOCK)

    expect(split.entryCount).toBe(2)
    expect(split.wordCount).toBeGreaterThan(0)
  })

  it('estimates tokens from word count', () => {
    const split = splitMemoryContext(FULL_BLOCK)

    expect(split.tokenEstimate).toBe(Math.round(split.wordCount * 1.35))
    expect(split.tokenEstimate).toBeGreaterThan(0)
  })

  it('tolerates whitespace inside the fence tags like the backend regex', () => {
    const loose = `< memory-context >\n[System note: x]\n\n## Mnemosyne Context\n  [2026-08-01T00:00] (importance 0.5) a memory\n</ memory-context >`
    const split = splitMemoryContext(`prompt\n\n${loose}`)

    expect(split.block).not.toBeNull()
    expect(split.entryCount).toBe(1)
    expect(split.userText).toBe('prompt')
  })

  it('joins multiple fenced blocks (builtin + external providers)', () => {
    const builtin = `<memory-context>\n[System note: x]\n\n## Memory\n  [2026-08-01T00:00] (importance 0.5) builtin memory\n</memory-context>`
    const split = splitMemoryContext(`prompt\n\n${builtin}\n\n${FULL_BLOCK}`)

    expect(split.block).toContain('builtin memory')
    expect(split.block).toContain('Mnemosyne')
    expect(split.entryCount).toBe(3)
    expect(split.userText).toBe('prompt')
  })
})
