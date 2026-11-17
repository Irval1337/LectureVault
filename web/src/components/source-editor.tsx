import { useEffect, useRef } from 'react'
import { EditorState } from '@codemirror/state'
import { EditorView, keymap, lineNumbers } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { markdown } from '@codemirror/lang-markdown'
import { defaultHighlightStyle, syntaxHighlighting } from '@codemirror/language'

export function SourceEditor({ initial, onChange, onReady }: { initial: string; onChange: (markdown: string) => void; onReady: () => void }) {
  const host = useRef<HTMLDivElement>(null)
  const callbacks = useRef({ onChange, onReady })
  callbacks.current = { onChange, onReady }
  useEffect(() => {
    const view = new EditorView({ parent: host.current!, state: EditorState.create({ doc: initial, extensions: [
      markdown(), lineNumbers(), history(), keymap.of([...defaultKeymap, ...historyKeymap]), syntaxHighlighting(defaultHighlightStyle), EditorView.lineWrapping,
      EditorView.contentAttributes.of({ 'aria-label': 'Исходный Markdown', role: 'textbox', 'aria-multiline': 'true' }),
      EditorView.updateListener.of(update => { if (update.docChanged) callbacks.current.onChange(update.state.doc.toString()) }),
      EditorView.theme({ '&': { background: 'var(--surface-page)', color: 'var(--text-body)', minHeight: '400px' }, '&.cm-focused': { outline: '1px solid var(--focus-ring)' }, '.cm-content': { fontFamily: 'var(--font-code)', fontSize: '13px', lineHeight: '1.8', padding: '20px 0' }, '.cm-gutters': { background: 'var(--surface-navigation)', color: 'var(--text-muted)', border: 'none' }, '.cm-cursor': { borderLeftColor: 'var(--accent)' }, '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': { background: 'var(--surface-selected)' } }, { dark: true }),
    ] }) })
    callbacks.current.onReady()
    return () => view.destroy()
  }, [initial])
  return <div className="source-editor-host" ref={host} />
}
