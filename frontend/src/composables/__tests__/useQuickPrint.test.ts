import { describe, it, expect } from 'vitest'
import {
  buildQuickPrintText,
  collectTextBlocks,
  quickPrintFilename,
  resolveMentions,
  PROPERTY_INDENT,
} from '../useQuickPrint'
import type { Block } from '@/stores/blocks'

// ── buildQuickPrintText ───────────────────────────────────────────────────────

describe('buildQuickPrintText', () => {
  it('prints title, properties and text blocks separated by blank lines', () => {
    const text = buildQuickPrintText(
      'Task A',
      [
        { label: 'Date', value: '17.07.2045' },
        { label: 'State', value: 'Open' },
        { label: 'Tags', value: 'Tag X' },
        { label: 'Owner', value: 'Person A' },
      ],
      ['Block 1', 'Block 2', 'Block 3'],
    )
    expect(text).toBe([
      '# Task A',
      '',
      '...... Date',
      '17.07.2045',
      '',
      '...... State',
      'Open',
      '',
      '...... Tags',
      'Tag X',
      '',
      '...... Owner',
      'Person A',
      '',
      'Block 1',
      '',
      'Block 2',
      '',
      'Block 3',
    ].join('\n'))
  })

  it('leaves out properties without a value', () => {
    const text = buildQuickPrintText(
      'Task A',
      [{ label: 'Date', value: '' }, { label: 'Note', value: '   ' }, { label: 'State', value: 'Open' }],
      [],
    )
    expect(text).toBe(`# Task A\n\n${PROPERTY_INDENT}State\nOpen`)
  })

  it('prints only the title for a page without properties or text', () => {
    expect(buildQuickPrintText('Page A', [], [])).toBe('# Page A')
  })

  it('keeps the title on one line', () => {
    expect(buildQuickPrintText('Line A\n  Line B', [], [])).toBe('# Line A Line B')
  })

  it('keeps line breaks inside values and text blocks', () => {
    const text = buildQuickPrintText('Page A', [{ label: 'Note', value: 'Line A\nLine B' }], ['Line C\nLine D'])
    expect(text).toBe('# Page A\n\n...... Note\nLine A\nLine B\n\nLine C\nLine D')
  })

  it('skips empty text blocks', () => {
    expect(buildQuickPrintText('Page A', [], ['', '  ', 'Block 1'])).toBe('# Page A\n\nBlock 1')
  })
})

// ── resolveMentions ───────────────────────────────────────────────────────────

describe('resolveMentions', () => {
  const ID = '00000000-0000-4000-8000-000000000001'

  it('replaces mention tokens with the title', () => {
    expect(resolveMentions(`See @${ID}| now`, () => 'Page B')).toBe('See Page B now')
  })

  it('leaves text without mentions alone', () => {
    expect(resolveMentions('Plain @ text|', () => 'Page B')).toBe('Plain @ text|')
  })
})

// ── collectTextBlocks ─────────────────────────────────────────────────────────

function block(id: string, type: string, extra: Partial<Block> = {}): Block {
  return {
    id,
    parent_id: null,
    reference_id: null,
    type,
    position: 0,
    state: 'active',
    content: null,
    icon: null,
    cover: null,
    ...extra,
  }
}

function text(id: string, value: string, type = 'paragraph', extra: Partial<Block> = {}): Block {
  return block(id, type, { content: { text: value }, ...extra })
}

function tree(children: Record<string, Block[]>) {
  return async (parentId: string): Promise<Block[]> => children[parentId] ?? []
}

describe('collectTextBlocks', () => {
  it('collects text-carrying blocks in document order', async () => {
    const childrenOf = tree({
      root: [
        text('a', 'Heading A', 'heading_1'),
        text('b', 'Item A', 'bulleted_list_item'),
        text('c', 'Todo A', 'to_do'),
        text('d', 'Code A', 'code'),
      ],
    })
    expect(await collectTextBlocks('root', childrenOf)).toEqual(['Heading A', 'Item A', 'Todo A', 'Code A'])
  })

  it('descends into toggles, layouts and columns', async () => {
    const childrenOf = tree({
      root: [text('t', 'Toggle A', 'toggle'), block('l', 'layout'), text('z', 'After')],
      t: [text('t1', 'Inside toggle')],
      l: [block('c1', 'column'), block('c2', 'column')],
      c1: [text('x', 'Left')],
      c2: [text('y', 'Right')],
    })
    expect(await collectTextBlocks('root', childrenOf)).toEqual(
      ['Toggle A', 'Inside toggle', 'Left', 'Right', 'After'],
    )
  })

  it('reads a synced mirror through its origin', async () => {
    const childrenOf = tree({
      root: [block('m', 'synched_mirror', { reference_id: 'o' })],
      o: [text('o1', 'Synced A')],
    })
    expect(await collectTextBlocks('root', childrenOf)).toEqual(['Synced A'])
  })

  it('skips sub-pages, databases, media and trashed blocks', async () => {
    const childrenOf = tree({
      root: [
        block('p', 'page'),
        block('d', 'database'),
        block('i', 'image'),
        text('gone', 'Trashed', 'paragraph', { state: 'trash' }),
        text('kept', 'Kept'),
      ],
      p: [text('p1', 'Sub-page text')],
      d: [text('d1', 'Entry text')],
    })
    expect(await collectTextBlocks('root', childrenOf)).toEqual(['Kept'])
  })

  it('skips empty text blocks', async () => {
    const childrenOf = tree({ root: [text('a', ''), text('b', '  '), text('c', 'Text A')] })
    expect(await collectTextBlocks('root', childrenOf)).toEqual(['Text A'])
  })

  it('does not loop on a mirror pointing back up the tree', async () => {
    const childrenOf = tree({
      root: [block('o', 'synched_origin')],
      o: [text('a', 'Text A'), block('m', 'synched_mirror', { reference_id: 'o' })],
    })
    expect(await collectTextBlocks('root', childrenOf)).toEqual(['Text A'])
  })
})

// ── quickPrintFilename ────────────────────────────────────────────────────────

describe('quickPrintFilename', () => {
  it('turns the title into a safe file name', () => {
    expect(quickPrintFilename('Task A / Part 2')).toBe('Task_A_Part_2.txt')
  })

  it('keeps letters beyond ASCII', () => {
    expect(quickPrintFilename('Café Ñandú')).toBe('Café_Ñandú.txt')
  })

  it('falls back for an empty title', () => {
    expect(quickPrintFilename('  ')).toBe('print.txt')
  })
})
