import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Compose Tailwind class names with conflict resolution.
 *
 * `clsx` handles conditional/argument flattening; `twMerge` resolves
 * conflicting Tailwind utilities so later classes win (e.g.
 * `cn('p-4', 'p-2')` → `p-2`). Existing `clsx` behaviour is preserved
 * for non-Tailwind consumers by importing `clsx` directly.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}