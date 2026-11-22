import { Plugin, TextSelection } from '@milkdown/kit/prose/state'
import { addColumn, addRow, isInTable, selectedRect, TableMap } from '@milkdown/kit/prose/tables'
import { Decoration, DecorationSet, type EditorView } from '@milkdown/kit/prose/view'

const insertions = [
  { dimension: 'row', after: false, label: 'Строку выше', icon: '↑' },
  { dimension: 'row', after: true, label: 'Строку ниже', icon: '↓' },
  { dimension: 'column', after: false, label: 'Столбец слева', icon: '←' },
  { dimension: 'column', after: true, label: 'Столбец справа', icon: '→' },
] as const

function focusCell(view: EditorView, tr: ReturnType<typeof addRow>, tableStart: number, row: number, col: number) {
  const table = tr.doc.nodeAt(tableStart - 1)!
  const cell = TableMap.get(table).positionAt(row, col, table)
  tr.setSelection(TextSelection.near(tr.doc.resolve(tableStart + cell + 1)))
  view.dispatch(tr.scrollIntoView())
  view.focus()
}

function appendButton(view: EditorView, getPos: () => number | undefined) {
  const button = document.createElement('button')
  button.type = 'button'
  button.className = 'table-append-row'
  button.textContent = '+'
  button.title = 'Добавить строку в конец таблицы'
  button.setAttribute('aria-label', button.title)
  button.onmousedown = event => event.preventDefault()
  button.onclick = () => {
    const end = getPos()
    if (end === undefined) return
    const table = view.state.doc.resolve(end).nodeBefore
    if (table?.type.spec.tableRole !== 'table') return
    const map = TableMap.get(table)
    const tableStart = end - table.nodeSize + 1
    const rect = { table, map, tableStart, left: 0, right: map.width, top: 0, bottom: map.height }
    focusCell(view, addRow(view.state.tr, rect, map.height), tableStart, map.height, 0)
  }
  return button
}

export const tableInsertionTools = new Plugin({
  props: {
    decorations(state) {
      const widgets: Decoration[] = []
      state.doc.descendants((node, pos) => {
        if (node.type.spec.tableRole === 'table')
          widgets.push(Decoration.widget(pos + node.nodeSize, appendButton, { side: -2, key: 'append-row', stopEvent: () => true, ignoreSelection: true }))
      })
      if (!isInTable(state)) return DecorationSet.create(state.doc, widgets)
      const rect = selectedRect(state)
      const end = rect.tableStart - 1 + rect.table.nodeSize
      const controls = Decoration.widget(end, view => {
        const toolbar = document.createElement('div')
        toolbar.className = 'table-insert-tools'
        toolbar.setAttribute('role', 'group')
        toolbar.setAttribute('aria-label', 'Вставка в таблицу')
        for (const { dimension, after, label, icon } of insertions) {
          const button = document.createElement('button')
          button.type = 'button'
          button.setAttribute('aria-label', 'Вставить ' + label.toLowerCase())
          button.title = button.getAttribute('aria-label')!
          const glyph = document.createElement('span')
          glyph.setAttribute('aria-hidden', 'true')
          glyph.textContent = icon
          button.append(glyph, label)
          // GFM has exactly one header row, in first position.
          button.disabled = dimension === 'row' && !after && rect.top === 0
          button.onmousedown = event => event.preventDefault()
          button.onclick = () => {
            if (!isInTable(view.state)) return
            const current = selectedRect(view.state)
            const row = dimension === 'row' && after ? current.bottom : current.top
            const col = dimension === 'column' && after ? current.right : current.left
            const tr = dimension === 'row' ? addRow(view.state.tr, current, row) : addColumn(view.state.tr, current, col)
            focusCell(view, tr, current.tableStart, row, col)
          }
          toolbar.append(button)
        }
        return toolbar
      }, { side: -1, key: 'insert-table-' + (rect.top === 0 ? 'header' : 'body'), ignoreSelection: true, stopEvent: () => true })
      return DecorationSet.create(state.doc, [...widgets, controls])
    },
    handleKeyDown(view, event) {
      if (event.altKey && event.key === 'F10' && isInTable(view.state)) {
        const button = view.dom.querySelector<HTMLButtonElement>('.table-insert-tools button:enabled')
        button?.focus()
        return !!button
      }
      return false
    },
  },
})
