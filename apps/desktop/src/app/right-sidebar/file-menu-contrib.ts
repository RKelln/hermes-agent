/**
 * File context-menu contribution surface — `fileActions` data contributions
 * become items in the right-click menu of the Files pane tree. Each action
 * declares a label, a predicate over the right-clicked file, and a
 * `run(path)` handler. Contributions register once at boot; the predicate is
 * evaluated per render, so it can match on the live path.
 */

import { useContributions } from '@/contrib/react/use-contributions'

export const FILE_ACTIONS_AREA = 'fileActions'

/** Payload of a `fileActions` data contribution. */
export interface FileActionContribution {
  id: string
  label: string
  /** Called per right-click to decide whether the action applies to this file. */
  matches: (path: string, isDirectory: boolean) => boolean
  /** Invoked with the file's absolute path when the item is selected. */
  run: (path: string) => void
}

/** Contributed file actions, with stable render keys. */
export function useFileActionContributions(): Array<FileActionContribution & { key: string }> {
  return useContributions(FILE_ACTIONS_AREA)
    .map(c => ({ key: `${c.source ?? 'core'}:${c.id}`, ...(c.data as FileActionContribution) }))
    .filter(item => Boolean(item.label && item.run && item.matches))
}
