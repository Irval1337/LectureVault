import { describe, it, expect } from 'vitest'
import { visit } from 'unist-util-visit'
import { compileMarkdown } from '../web/src/quartz/reader'
import type { Element } from 'hast'

describe('ready-made Markdown pipeline with original Quartz components', () => {
  it('renders formulas, code, tables and duplicate heading anchors', () => {
    const { tree, toc } = compileMarkdown('# Заголовок\n\n## Формула\n\n$$\nx^2 + y^2 = z^2\n$$\n\n## Формула\n\n```js\nconst x = 1\n```\n\n| A | B |\n| - | - |\n| 1 | 2 |')
    expect(toc.map(t => t.slug)).toEqual(['section-формула', 'section-формула-1'])
    const tags: string[] = []
    visit(tree, 'element', (node: Element) => { tags.push(node.tagName) })
    expect(tags).toContain('math')
    expect(tags).toContain('table')
    expect(tags).toContain('pre')
  })
  it('drops executable HTML and external images and blocks unsafe link schemes', () => {
    const { tree } = compileMarkdown('<script>alert(1)</script>\n\n[click](javascript:alert%281%29)\n\n![tracking](https://attacker.example/pixel)')
    const serialized = JSON.stringify(tree)
    expect(serialized).not.toContain('"tagName":"script"')
    expect(serialized).not.toContain('"src":"https:')
    expect(serialized).not.toContain('"href":"javascript:')
  })
})

it('keeps illustration alt text and captions separate from the validated size', () => {
  const { tree } = compileMarkdown([
    '![Узел](/demo-replication.svg "Подпись [width=75%]")',
    'Текст ![0.50](/demo-replication.svg) внутри строки.',
    '![Оригинал](/demo-replication.svg "Текст [width=javascript:bad]")',
  ].join('\n\n'))
  const images: Element[] = []
  visit(tree, 'element', (node: Element) => { if (node.tagName === 'img') images.push(node) })
  expect(images.map(image => [image.properties.alt, image.properties.title, image.properties.dataImageScale])).toEqual([
    ['Узел', 'Подпись', 0.75], ['', undefined, 0.5], ['Оригинал', 'Текст [width=javascript:bad]', undefined],
  ])
})
